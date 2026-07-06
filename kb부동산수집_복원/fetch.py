"""KB Land(kbland.kr) 매물 수집기 — 디컴파일(바이트코드 역분석)로 복원한 소스.

원본: kb+부동산+수집.exe (PyInstaller, Python 3.13) 내부 fetch.pyc
주의: 변수/함수명은 원본 그대로 복원됨. 주석/공백/일부 표현은 추정 재구성.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KBLAND_HOME_URL = "https://kbland.kr/"
KBLAND_AUTH_TRIGGER_URL = "https://kbland.kr/al?xy=37.5665,126.9780,16"
KB_API_HOST = "api.kbland.kr"
KB_API_URL_PATTERN = re.compile(r"^https://api\.kbland\.kr/")
KB_SEARCH_URL = "https://api.kbland.kr/land-complex/serch/intgraSerch"
KB_PROPERTY_LIST_URL = "https://api.kbland.kr/land-property/propList/stutCdFilter"
PLAYWRIGHT_VIEWPORT = {"width": 1440, "height": 1080}
LOGIN_TIMEOUT_MS = 30000
PROPERTY_PAGE_SIZE = 30
PROPERTY_PAGE_FETCH_RETRY_COUNT = 3
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATTERNS = (
    "chromium-*/chrome-win64/chrome.exe",
    "chromium-*/chrome-win/chrome.exe",
    "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",
)


def _make_chromium_launch_options(headless: bool) -> dict[str, Any]:
    launch_options = {"headless": headless}
    executable_path = _find_playwright_chromium_executable()
    if executable_path is not None:
        launch_options["executable_path"] = str(executable_path)
        return launch_options
    if getattr(sys, "frozen", False):
        raise RuntimeError(
            "Playwright Chromium 실행 파일을 찾지 못했습니다. "
            "`uv run playwright install chromium`을 실행한 뒤 다시 빌드하거나, "
            "PyInstaller 빌드 결과물에 ms-playwright 폴더를 포함해주세요."
        )
    return launch_options


def _find_playwright_chromium_executable() -> Path | None:
    for root in _iter_playwright_browser_roots():
        for pattern in PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATTERNS:
            for path in sorted(root.glob(pattern), reverse=True):
                if path.is_file():
                    return path
    return None


def _iter_playwright_browser_roots() -> list[Path]:
    roots: list[Path] = []
    seen = set()

    def add_root(path: Path) -> None:
        resolved = str(path.expanduser())
        if resolved not in seen:
            roots.append(path.expanduser())
            seen.add(resolved)

    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        exe_root = Path(sys.executable).resolve().parent
        add_root(bundle_root / "ms-playwright")
        add_root(bundle_root / "playwright" / "driver" / "package" / ".local-browsers")
        add_root(exe_root / "ms-playwright")

    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_path and browsers_path != "0":
        add_root(Path(browsers_path))

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        add_root(Path(local_app_data) / "ms-playwright")

    add_root(Path.home() / "AppData" / "Local" / "ms-playwright")
    return roots


def login(
    email: str,
    password: str,
    *,
    headless: bool = True,
    timeout_ms: int = LOGIN_TIMEOUT_MS,
    trigger_url: str = KBLAND_AUTH_TRIGGER_URL,
) -> dict[str, str]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "playwright가 설치되어 있지 않습니다. `uv add playwright` 후 "
            "`uv run playwright install chromium`을 실행해주세요."
        ) from error

    captured: dict[str, Any] = {"request_headers": {}}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**_make_chromium_launch_options(headless))
        try:
            context = browser.new_context(locale="ko-KR", viewport=PLAYWRIGHT_VIEWPORT)
            _hook_request_headers(context, captured)
            page = context.new_page()
            _perform_kakao_login(page, email, password, timeout_ms, PlaywrightTimeoutError)

            # 로그인 직후 캡처된 헤더가 있으면 바로 사용
            request_headers = _wait_for_request_headers(captured, page, 5000)
            if request_headers:
                return request_headers

            # 없으면 지도 API를 한 번 트리거해서 헤더를 캡처
            _trigger_kbland_api_request(page, trigger_url, timeout_ms, PlaywrightTimeoutError)
            request_headers = _wait_for_request_headers(captured, page, timeout_ms)
            if request_headers:
                return request_headers
        finally:
            browser.close()

    raise RuntimeError("KB Land pointInfo 요청에서 request headers를 찾지 못했습니다.")


def get_access_token(
    email: str,
    password: str,
    *,
    headless: bool = False,
    timeout_ms: int = LOGIN_TIMEOUT_MS,
) -> str | None:
    """카카오 자동로그인 후 kbland 세션의 accessToken을 추출한다.

    단지 단위 매물 API(propList/main)는 accessToken으로 RSA 서명한
    bearer 헤더가 필요하다. 이 토큰은 로그인된 kbland 세션의
    localStorage('accessToken') 또는 동명 쿠키에 들어있다.
    (확장 기능: 원본 EXE에는 없던, 복원본에 추가한 함수)
    """
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as error:
        raise RuntimeError("playwright가 설치되어 있지 않습니다.") from error

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**_make_chromium_launch_options(headless))
        try:
            context = browser.new_context(locale="ko-KR", viewport=PLAYWRIGHT_VIEWPORT)
            page = context.new_page()
            _perform_kakao_login(page, email, password, timeout_ms, PlaywrightTimeoutError)

            # 로그인 후 kbland 홈으로 이동해 세션 스토리지가 채워지도록 한다.
            page.goto(KBLAND_HOME_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2000)

            token = _extract_access_token(page, context)
            return token
        finally:
            browser.close()


def _read_vuex_member(page) -> dict:
    """localStorage['vuex'].member 를 dict 로 읽는다 (로그인 상태/토큰 보관처)."""
    try:
        member = page.evaluate(
            "() => { try { return JSON.parse(localStorage.getItem('vuex')||'{}').member || {}; }"
            " catch(e){ return {}; } }"
        )
        return member or {}
    except Exception:
        return {}


def wait_for_kbland_login(page, timeout_ms: int = 60000, poll_ms: int = 1500) -> dict:
    """vuex.member.isLogin 이 true 가 될 때까지 폴링 후 member 반환.

    카카오 OAuth 후 KB siteToken 발급이 비동기라 잠시 걸린다. 추가 동의 단계가
    필요하면 (브라우저가 보이는 상태에서) 사용자가 직접 처리할 시간을 준다.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    member = {}
    while time.monotonic() < deadline:
        member = _read_vuex_member(page)
        if member.get("isLogin") and (member.get("siteToken") or member.get("accessToken")):
            return member
        page.wait_for_timeout(poll_ms)
    return member


def _extract_access_token(page, context) -> str | None:
    """KB accessToken 추출. 우선순위: vuex.member.siteToken → accessToken →
    localStorage/sessionStorage['accessToken'] → 동명 쿠키."""
    member = _read_vuex_member(page)
    for field in ("siteToken", "accessToken", "token"):
        value = member.get(field)
        if value and isinstance(value, str) and value.strip():
            return value
    for expr in (
        "() => localStorage.getItem('accessToken')",
        "() => sessionStorage.getItem('accessToken')",
    ):
        try:
            value = page.evaluate(expr)
        except Exception:
            value = None
        if value and isinstance(value, str) and value not in ("null", ""):
            return value
    for cookie in context.cookies():
        if cookie.get("name") == "accessToken" and cookie.get("value"):
            return cookie["value"]
    return None


def _hook_request_headers(context, captured: dict[str, Any]):
    def handle_route(route, request):
        if _is_point_info_api_url(request.url):
            captured["request_headers"] = _normalize_request_headers(request.headers)
        route.continue_()

    context.route(KB_API_URL_PATTERN, handle_route)


def _is_point_info_api_url(url: str) -> bool:
    parsed_url = urlparse(url)
    return parsed_url.netloc == KB_API_HOST and "pointInfo" in parsed_url.path


def _normalize_request_headers(headers: dict[str, str]) -> dict[str, str]:
    excluded_headers = {"content-length", "host"}
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in excluded_headers
    }


def _perform_kakao_login(page, email: str, password: str, timeout_ms: int, playwright_timeout_error):
    page.goto(KBLAND_HOME_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    _close_home_popup(page, playwright_timeout_error)
    _open_login_dialog(page, timeout_ms, playwright_timeout_error)

    with page.expect_popup(timeout=timeout_ms) as popup_info:
        page.locator(".btn.btn-login.kakao").click(timeout=timeout_ms)

    popup = popup_info.value
    popup.locator("input[name='loginId']").fill(email, timeout=timeout_ms)
    popup.locator("input[name='password']").fill(password, timeout=timeout_ms)
    popup.locator("button[type='submit']").click(timeout=timeout_ms)

    try:
        popup.wait_for_event("close", timeout=timeout_ms)
    except playwright_timeout_error:
        pass

    page.bring_to_front()


def _close_home_popup(page, playwright_timeout_error):
    try:
        page.locator(".homePopupcon.open .btn.btn-close").first.click(timeout=2000)
    except playwright_timeout_error:
        return


def _open_login_dialog(page, timeout_ms: int, playwright_timeout_error):
    try:
        page.get_by_role("button", name="로그인하기", exact=True).click(timeout=timeout_ms)
    except playwright_timeout_error:
        # 메뉴를 먼저 열어야 로그인 버튼이 노출되는 경우
        page.get_by_role("button", name="메뉴", exact=True).click(timeout=timeout_ms)
        page.get_by_role("button", name="로그인하기", exact=True).click(timeout=timeout_ms)
    page.locator(".btn.btn-login.kakao").wait_for(timeout=timeout_ms)


def _trigger_kbland_api_request(page, trigger_url: str, timeout_ms: int, playwright_timeout_error):
    page.goto(trigger_url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except playwright_timeout_error:
        return


def _wait_for_request_headers(captured: dict[str, Any], page, timeout_ms: int) -> dict[str, str]:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        request_headers = captured.get("request_headers", {})
        if request_headers:
            return request_headers.copy()
        page.wait_for_timeout(200)
    return {}


def make_headers(request_headers: dict[str, str] | None = None) -> dict[str, str]:
    if request_headers:
        return request_headers.copy()
    return {}


def get_lat_lng(search_keyword: str, request_headers: dict[str, str] | None = None) -> tuple[float, float]:
    params = {
        "검색설정명": "SRC_JUSO",
        "검색키워드": search_keyword,
        "출력갯수": "2",
        "페이지설정값": "1",
    }
    headers = make_headers(request_headers)
    response = requests.get(KB_SEARCH_URL, params=params, headers=headers, timeout=10)
    response.raise_for_status()
    return parse_lat_lng(response.json(), search_keyword)


def get_all_properties(
    lat: float,
    lng: float,
    property_type: str,
    transaction_type: str,
    lawd_code: str,
    request_headers: dict[str, str] | None = None,
    total_count: int | None = None,
    progress_callback: Callable[[int, int, int, int], None] | None = None,
) -> list[dict[str, Any]]:
    if total_count is None:
        total_count = get_property_total_count(
            lat=lat, lng=lng, property_type=property_type,
            transaction_type=transaction_type, lawd_code=lawd_code,
            request_headers=request_headers,
        )
    if total_count == 0:
        return []

    page_count = (total_count + PROPERTY_PAGE_SIZE - 1) // PROPERTY_PAGE_SIZE
    properties: list[dict[str, Any]] = []
    for page_number in range(1, page_count + 1):
        page_data = fetch_property_page(
            lat=lat, lng=lng, property_type=property_type,
            transaction_type=transaction_type, lawd_code=lawd_code,
            page_number=page_number, page_size=PROPERTY_PAGE_SIZE,
            request_headers=request_headers,
        )
        properties.extend(parse_property_list(page_data))
        if progress_callback:
            progress_callback(page_number, page_count, len(properties), total_count)
    return properties


def get_property_total_count(
    lat: float, lng: float, property_type: str, transaction_type: str,
    lawd_code: str, *, request_headers: dict[str, str] | None = None,
) -> int:
    first_page_data = fetch_property_page(
        lat=lat, lng=lng, property_type=property_type,
        transaction_type=transaction_type, lawd_code=lawd_code,
        page_number=1, page_size=1, request_headers=request_headers,
    )
    return parse_total_property_count(first_page_data)


def fetch_property_page(
    lat: float, lng: float, property_type: str, transaction_type: str,
    lawd_code: str, page_number: int, page_size: int,
    *, request_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = make_property_list_payload(
        lat=lat, lng=lng, property_type=property_type,
        transaction_type=transaction_type, lawd_code=lawd_code,
        page_number=page_number, page_size=page_size,
    )
    headers = make_headers(request_headers)
    last_error = None
    for attempt in range(1, PROPERTY_PAGE_FETCH_RETRY_COUNT + 1):
        try:
            response = requests.post(KB_PROPERTY_LIST_URL, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as error:
            last_error = error
            if attempt == PROPERTY_PAGE_FETCH_RETRY_COUNT:
                pass
            else:
                time.sleep(attempt)  # 지수적이진 않지만 시도횟수만큼 대기
    if last_error:
        raise last_error
    raise RuntimeError("매물 페이지 조회에 실패했습니다.")


def make_property_list_payload(
    lat: float, lng: float, property_type: str, transaction_type: str,
    lawd_code: str, page_number: int, page_size: int,
) -> dict[str, Any]:
    return {
        "selectCode": "2",
        "zoomLevel": 18,
        "startLat": lat,
        "startLng": lng,
        "endLat": lat,
        "endLng": lng,
        "물건종류": property_type,
        "거래유형": transaction_type,
        "매매시작값": "",
        "매매종료값": "",
        "보증금시작값": "",
        "보증금종료값": "",
        "월세시작값": "",
        "월세종료값": "",
        "면적시작값": "",
        "면적종료값": "",
        "준공년도시작값": "",
        "준공년도종료값": "",
        "방수": "",
        "욕실수": "",
        "세대수시작값": "",
        "세대수종료값": "",
        "관리비시작값": "",
        "관리비종료값": "",
        "용적률시작값": "",
        "용적률종료값": "",
        "건폐율시작값": "",
        "건폐율종료값": "",
        "전세가율시작값": "",
        "전세가율종료값": "",
        "매매전세차시작값": "",
        "매매전세차종료값": "",
        "월세수익률시작값": "",
        "월세수익률종료값": "",
        "구조": "",
        "주차": "",
        "엘리베이터": "",
        "보안옵션": "",
        "매물": "",
        "융자금": "",
        "분양단지구분코드": "",
        "일반분양여부": "",
        "분양진행단계코드": "",
        "옵션": "",
        "점포수시작값": "",
        "점포수종료값": "",
        "지상층": "",
        "지하층": "",
        "지목": "",
        "용도지역": "",
        "추진현황": "",
        "webCheck": "Y",
        "페이지번호": page_number,
        "페이지목록수": page_size,
        "중복타입": "01",
        "정렬타입": "date",
        "사진있는매물순": False,
        "전자계약여부": "0",
        "비대면대출여부": "0",
        "클린주택여부": "0",
        "honeyYn": "0",
        "법정동코드": lawd_code,
    }


def parse_total_property_count(response_data: dict[str, Any]) -> int:
    data = get_property_response_data(response_data)
    return to_int(data.get("총매물건수", 0))


def parse_property_list(response_data: dict[str, Any]) -> list[dict[str, Any]]:
    property_list = get_property_response_data(response_data).get("propertyList", [])
    if not isinstance(property_list, list):
        raise ValueError("propertyList is missing or invalid")
    return property_list


def get_property_response_data(response_data: dict[str, Any]) -> dict[str, Any]:
    data = response_data.get("dataBody", {}).get("data", {})
    if not isinstance(data, dict):
        raise ValueError("property response data is missing or invalid")
    return data


def parse_lat_lng(response_data: dict[str, Any], search_keyword: str = "") -> tuple[float, float]:
    juso_data = (
        response_data
        .get("dataBody", {})
        .get("data", {})
        .get("data", {})
        .get("JUSO", {})
        .get("data", [])
    )
    if not juso_data:
        message = f"lat/lng not found: {search_keyword}" if search_keyword else "lat/lng not found"
        raise ValueError(message)

    first_item = juso_data[0]
    lat = first_item.get("WGS84_LAT")
    lng = first_item.get("WGS84_LNG")
    if lat is None or lng is None:
        raise ValueError("WGS84_LAT or WGS84_LNG is missing")
    return float(lat), float(lng)


def to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def save_properties_json(properties: list[dict[str, Any]], address: str) -> Path:
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_address = re.sub(r'[\\/:*?"<>|]+', "_", address).strip() or "properties"
    output_path = output_dir / f"kb_properties_{safe_address}_{timestamp}.json"
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(properties, file, ensure_ascii=False, indent=2)
    return output_path


class FetchService:
    def __init__(self):
        self.request_headers = {}

    def login(
        self, email: str, password: str, *,
        headless: bool = True, timeout_ms: int = LOGIN_TIMEOUT_MS,
        trigger_url: str = KBLAND_AUTH_TRIGGER_URL,
    ) -> dict[str, str]:
        self.request_headers = login(
            email, password, headless=headless,
            timeout_ms=timeout_ms, trigger_url=trigger_url,
        )
        return self.request_headers

    def get_lat_lng(self, search_keyword: str, request_headers: dict[str, str] | None = None) -> tuple[float, float]:
        return get_lat_lng(search_keyword, request_headers or self.request_headers)

    def get_all_properties(
        self, lat: float, lng: float, property_type: str, transaction_type: str,
        lawd_code: str, request_headers: dict[str, str] | None = None,
        total_count: int | None = None,
        progress_callback: Callable[[int, int, int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        return get_all_properties(
            lat, lng, property_type, transaction_type, lawd_code,
            request_headers=request_headers or self.request_headers,
            total_count=total_count, progress_callback=progress_callback,
        )

    def get_property_total_count(
        self, lat: float, lng: float, property_type: str, transaction_type: str,
        lawd_code: str, request_headers: dict[str, str] | None = None,
    ) -> int:
        return get_property_total_count(
            lat, lng, property_type, transaction_type, lawd_code,
            request_headers=request_headers or self.request_headers,
        )


def main():
    email = "dwbyun17@gmail.com"      # ← 원본에 하드코딩된 기본 카카오 계정
    password = "ekrxjqus12!!"         # ← 원본에 하드코딩된 비밀번호 (CLI 테스트용)
    address = "대전광역시 서구 도마동"
    property_type = "16,19,21,23,20,22,28,43"
    transaction_type = "1,2,3"
    lawd_code = "3017010300"

    if not email or not password:
        raise ValueError("이메일과 비밀번호를 모두 입력해주세요.")

    request_headers = login(email, password, headless=True)

    if not address:
        raise ValueError("주소를 입력해주세요.")

    lat, lng = get_lat_lng(address, request_headers)
    print(f"lat: {lat}")
    print(f"lng: {lng}")

    properties = get_all_properties(
        lat, lng, property_type, transaction_type, lawd_code,
        request_headers=request_headers,
    )
    print(f"전체 매물 수: {len(properties)}")

    output_path = save_properties_json(properties, address)
    print(f"json 저장 완료: {output_path}")


if __name__ == "__main__":
    main()
