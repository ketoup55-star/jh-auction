# -*- coding: utf-8 -*-
"""
================================================================================
 kb_crawler.py — KB부동산(kbland.kr) 수집 통합 모듈  ★자체완결 단일 파일★
================================================================================
이 파일 "하나"만 다른 프로젝트에 넣으면 KB부동산 매물 수집 기능을 그대로 쓸 수 있다.
코드·문서·DB스키마·자가진단·CLI 가 전부 이 파일 안에 들어있다. (외부 모듈 의존 없음)

--------------------------------------------------------------------------------
0. 30초 요약 — 처음 받았다면 이것부터
--------------------------------------------------------------------------------
  # (1) 의존성 설치
  pip install requests "psycopg[binary]" cryptography           # 필수(수집/DB)
  pip install fastapi pydantic                                  # 웹 통합 시
  pip install playwright && playwright install chromium         # 자동로그인 시

  # (2) 환경변수 설정 (아래 '환경변수' 참고)
  export SUPABASE_DB_URL=postgresql://...:6543/postgres
  export KB_SITE_TOKEN=...        # 또는  KB_EMAIL / KB_PW (자동로그인)

  # (3) 자가진단 — 뭐가 준비됐고 뭐가 없는지 콕 집어준다
  python kb_crawler.py --selfcheck

  # (4) DB 테이블 생성(kb_* 4개, 최초 1회)
  python kb_crawler.py --init-db

  # (5a) 지역 매물 수집 테스트(무DB, JSON 출력)
  python kb_crawler.py --region "대전 서구 도마동" 3017010300
  # (5b) FastAPI 통합:  api/main.py 에 딱 두 줄
  from kb_crawler import router as kb_router
  app.include_router(kb_router)

--------------------------------------------------------------------------------
1. 이 모듈이 하는 일 (2가지 모드)
--------------------------------------------------------------------------------
 [모드A] 아파트 매매 수집  collect_apartments()  /  POST /kb/collect/apartments
   흐름: DB의 진행중 경매 아파트(items) → 주소로 KB단지 매칭 → 그 단지의 '매매' 매물
         전량 수집(서명 API) → kb_listing/kb_complex/items 적재.
   ※ 이 모드는 auction 성 'items' 테이블을 소스로 읽는다(아래 6. 의존성 주의).

 [모드B] 지역 매물/중개사 수집  collect_region()  /  POST /kb/collect/region
   흐름: 주소→좌표 → 법정동코드 지역의 매물 전량 수집(캡처헤더) → JSON 반환.
   ※ DB 불필요. 어떤 프로젝트든 그대로 사용 가능(가장 이식성 높음).

--------------------------------------------------------------------------------
2. FastAPI 엔드포인트 (router 를 include 하면 자동 노출)
--------------------------------------------------------------------------------
  POST /kb/collect/apartments  body: {limit?:int, resume?:bool=true, dry?:bool=false}
       → {job_id}  (백그라운드 실행. 오래 걸림)
  GET  /kb/collect/jobs/{id}   → {status,target,processed,matched,unmatched,listings,
                                   zero_listing,errors,errors_detail,last_error}
  POST /kb/collect/region      body: {address?:str, lawd_code:str, property_types:str="01",
                                   transaction_types:str="1", lat?:float, lng?:float}
       → {lat,lng,lawd_code,count,broker_count,properties:[...131필드...],brokers:[...]}
  GET  /kb/logs?level=INFO&limit=200   → 최근 로그  (level=ERROR 면 경고/오류만)
  GET  /kb/health              → 의존성·인증·DB·최근에러 종합 진단
  POST /kb/init-db             → kb_* 테이블 생성(idempotent)

--------------------------------------------------------------------------------
3. 코드로 직접 호출(같은 파이썬 프로젝트)
--------------------------------------------------------------------------------
  import kb_crawler as kb
  data = kb.collect_region(address="대전 서구 도마동", lawd_code="3017010300")   # JSON
  stat = kb.collect_apartments(limit=50)                                         # DB 적재
  m    = kb.match_address("서울 강남구 대치동 316 은마아파트 11동 502호")          # 매칭만
  ok, report = kb.selfcheck()                                                    # 자가진단

--------------------------------------------------------------------------------
4. 인증 (둘 다 대비: 배포/로컬)
--------------------------------------------------------------------------------
  우선순위 1) KB_SITE_TOKEN 환경변수 주입    → 브라우저 불필요(배포 권장). 모드A 완전동작.
  우선순위 2) KB_EMAIL/KB_PW 로 카카오 자동로그인 → siteToken+인증헤더 캡처(로컬). 모드A/B 완전동작.
  토큰은 메모리 캐시(기본 1h) + 401(만료) 시 자동 재발급.
  ※ siteToken 얻는 법(수동): kbland.kr 로그인 후 콘솔에서
    JSON.parse(localStorage.vuex).member.siteToken

--------------------------------------------------------------------------------
5. 환경변수
--------------------------------------------------------------------------------
  SUPABASE_DB_URL   (모드A/DB필수)  postgresql://user:pw@host:6543/postgres
  KB_SITE_TOKEN     (인증)  siteToken 직접 주입 — 배포 환경 권장
  KB_EMAIL, KB_PW   (인증)  카카오 자동로그인용(playwright 필요)
  KB_REQUEST_DELAY  (선택)  API 요청 간격 초, 기본 0.4 (KB 부하방지/승인서 준수 — 낮추면 IP차단 위험)
  KB_LOG_LEVEL      (선택)  콘솔 로그레벨, 기본 INFO (DEBUG면 요청단위까지)
  KB_LOG_FILE       (선택)  로그파일 경로, 기본 kb_crawler.log (회전 5MB×5, 파일엔 DEBUG 전량)

--------------------------------------------------------------------------------
6. 의존성 & 주의 (오류 없이 쓰려면)
--------------------------------------------------------------------------------
  * 파이썬 3.10+ (union 타입표기 사용).
  * 모드A(아파트)는 소스로 'items' 테이블(경매 물건: item_key, address, usage_name,
    data_class, status_reason, +kb_* 요약컬럼)이 있어야 한다. 이 테이블이 없는
    프로젝트면 모드A는 못 쓰고 모드B(지역)만 쓴다. --selfcheck 가 유무를 알려준다.
  * kb_* 4개 테이블은 --init-db 로 자동생성(SCHEMA_DDL 내장).
  * 배포(컨테이너)에서 자동로그인 쓰려면 chromium 설치 필요 + 카카오 캡차 위험 →
    KB_SITE_TOKEN 주입 방식 권장.
  * 지역 모드(B)는 캡처 인증헤더가 이상적이라 자동로그인 환경에서 완전동작.
    토큰만 주입된 환경에선 best-effort(실패 가능) — 로그에 경고로 표시됨.

--------------------------------------------------------------------------------
7. 오류/누락 진단
--------------------------------------------------------------------------------
  모든 단계가 로깅된다. 오류·누락 시 원인이 로그에 남는다:
    - 매칭실패 사유(단지명없음/검색0건/지역게이트/임계미달), API 상태·본문,
      401 토큰만료 자동재시도, 페이지상한 누락경고, 매매0건, 전체 트레이스백.
  확인:  GET /kb/logs?level=ERROR  |  파일 kb_crawler.log  |  job 결과 errors_detail

--------------------------------------------------------------------------------
8. 데이터 모델 (kb_listing 주요 컬럼 = KB 매물 131필드 중 핵심)
--------------------------------------------------------------------------------
  listing_id(매물일련번호) complex_no(단지) trade_type(매매) price(매매가,만원)
  area_excl(전용) area_supply(공급) floor(해당층) floor_total(총층) dong/ho
  direction(방향) room_cnt bath_cnt unit_price(평당) agent_name/addr/phone(중개업소)
  reg_date confirm_date feature(특징) photo_cnt dup_cnt  raw(원본 131필드 전체 jsonb)
================================================================================
"""
from __future__ import annotations

import base64
import datetime
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import unicodedata
import urllib.parse
import uuid
from logging.handlers import RotatingFileHandler
from typing import Any

import requests

# ──────────────────────────────────────────────────────────────────────────
# 0. 설정
# ──────────────────────────────────────────────────────────────────────────
DB_URL = os.environ.get("SUPABASE_DB_URL", "")
KB_EMAIL = os.environ.get("KB_EMAIL", "")
KB_PW = os.environ.get("KB_PW", "")
KB_SITE_TOKEN_ENV = os.environ.get("KB_SITE_TOKEN", "")
REQUEST_DELAY = float(os.environ.get("KB_REQUEST_DELAY", "0.4"))

BASE = "https://api.kbland.kr"
SEARCH_URL = f"{BASE}/land-complex/serch/intgraSerch"
PROP_MAIN_URL = f"{BASE}/land-property/propList/main"               # 단지 매물(서명)
PROP_REGION_URL = f"{BASE}/land-property/propList/stutCdFilter"     # 지역 매물(캡처헤더)
COUNT_URL = f"{BASE}/land-complex/complexResteBrhs/propCountByTradeKind"
PHOTO_URL = f"{BASE}/land-property/property/phtoList"
KBLAND_HOME = "https://kbland.kr/"
KBLAND_TRIGGER = "https://kbland.kr/al?xy=37.5665,126.9780,16"

_PUB_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": "https://kbland.kr",
    "referer": "https://kbland.kr/",
    "webservice": "1",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
}

# 진행중 아파트 조회 SQL (items)
ACTIVE_APT_SQL = """
    select item_key, address from items
    where usage_name like '%아파트%'
      and data_class = '현황'
      and (status_reason like '진행%' or status_reason like '재%')
"""

# ──────────────────────────────────────────────────────────────────────────
# 로깅 — 모든 단계 기록. 콘솔=KB_LOG_LEVEL(기본 INFO), 파일=DEBUG 전량(회전).
#   KB_LOG_LEVEL=DEBUG  로 하면 콘솔에도 요청단위까지 전부 표시.
#   KB_LOG_FILE 로 로그파일 경로 지정(기본 kb_crawler.log).
# 오류/누락은 원인(HTTP상태·응답본문·트레이스백·사유)이 로그에 남는다.
# ──────────────────────────────────────────────────────────────────────────
def _setup_logger() -> logging.Logger:
    lg = logging.getLogger("kb_crawler")
    if lg.handlers:                      # 중복 핸들러 방지(재import/재로드)
        return lg
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    con_level = os.environ.get("KB_LOG_LEVEL", "INFO").upper()
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(con_level)
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    lg.addHandler(sh)
    logfile = os.environ.get("KB_LOG_FILE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "kb_crawler.log")
    try:
        fh = RotatingFileHandler(logfile, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setLevel(logging.DEBUG)       # 파일엔 DEBUG까지 전부 → 사후 원인분석용
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(funcName)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        lg.addHandler(fh)
        lg.debug("파일 로깅 시작: %s", logfile)
    except Exception as e:               # 파일 못 열어도 콘솔 로깅은 유지
        lg.warning("로그파일 열기 실패(%s) — 콘솔만 사용: %s", logfile, e)
    return lg


log = _setup_logger()
# 최근 로그/에러를 메모리에도 보관(API /kb/logs 로 조회) — 파일 접근 없이 원인확인
_RECENT_LOGS: list[str] = []
_RECENT_ERRORS: list[dict] = []


class _MemoryHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
        except Exception:
            return
        _RECENT_LOGS.append(msg)
        del _RECENT_LOGS[:-500]                    # 최근 500줄 유지
        if record.levelno >= logging.WARNING:
            _RECENT_ERRORS.append({"t": record.asctime if hasattr(record, "asctime") else None,
                                   "level": record.levelname, "msg": record.getMessage()})
            del _RECENT_ERRORS[:-200]


_mh = _MemoryHandler()
_mh.setLevel(logging.INFO)
_mh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
log.addHandler(_mh)

# ──────────────────────────────────────────────────────────────────────────
# 1. RSA 서명 (kb_sign 인라인) — 단지 매물 API(propList/main)용
# ──────────────────────────────────────────────────────────────────────────
_KB_PUBLIC_KEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA1b14efejHNAhrqD5jnhX0Xtl0Is"
    "nYrNKNOCCqVAKADwmb3jszFOMJQrOhJpsqvp/l5gdxBRlwYyXU/MZ1G7jT+yQZGqEkzi3r4"
    "azaiudsoWl7uG5lkwNQrbzcMacMPT11ahjfzNr4JRo0nBiTt1DJVcfyoxr07mjxIOuqOf/AC"
    "NtrtdFQeJxHMGUsC5abzGVtVVUmQZcKUa/WbVP6/NPWqHxBbqOEU5nHutQbDwJ7M/GvHwTxS"
    "yiqct2UKPj/W4PdHAN8aBy8hLT5Twm/krSBCBZu4ehVHNO4V1OHBjPovY+NLpfLI/CQbBjo/"
    "JMxLXgarrV8kEPyVXdw1hDNQproQIDAQAB"
)
_pub_key = None


def _get_pubkey():
    global _pub_key
    if _pub_key is None:
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        _pub_key = load_der_public_key(base64.b64decode(_KB_PUBLIC_KEY_B64))
    return _pub_key


def _kb_timestamp(now: datetime.datetime | None = None) -> str:
    n = now or datetime.datetime.now()
    h12 = n.hour % 12 or 12
    return n.strftime("%Y%m%d") + str(h12) + n.strftime("%M%S") + f"{n.microsecond // 1000:03d}"


def _make_bearer(access_token: str, ts: str) -> str:
    from cryptography.hazmat.primitives.asymmetric import padding
    msg = base64.b64encode(f"{access_token}:{ts}".encode()).decode()
    ct = _get_pubkey().encrypt(msg.encode(), padding.PKCS1v15())
    return base64.b64encode(ct).decode()


def _signed_headers(access_token: str) -> dict:
    ts = _kb_timestamp()
    return {
        "accept": "application/json, text/plain, */*",
        "authorization": "bearer " + _make_bearer(access_token, ts),
        "content-type": "application/json",
        "origin": "https://kbland.kr",
        "referer": "https://kbland.kr/",
        "timestamp": ts,
        "webservice": "1",
        "user-agent": _PUB_HEADERS["user-agent"],
    }


# ──────────────────────────────────────────────────────────────────────────
# 2. 인증 (토큰/헤더 캐시) — 둘 다 대비: 토큰 주입 + 카카오 자동로그인
# ──────────────────────────────────────────────────────────────────────────
class _Auth:
    """siteToken(서명용) + 캡처 인증헤더(지역 API용)를 캐시."""
    def __init__(self):
        self.token: str | None = None
        self.captured_headers: dict | None = None
        self.obtained_at: float = 0.0
        self.ttl: float = 60 * 60  # 1시간 캐시(보수적). 실패 시 즉시 갱신.
        self._lock = threading.Lock()

    def _expired(self) -> bool:
        return not self.token or (time.monotonic() - self.obtained_at) > self.ttl

    def get_token(self, force: bool = False) -> str:
        with self._lock:
            if not force and not self._expired():
                return self.token
            # 1) 환경변수 토큰 주입 우선 (배포 친화)
            if KB_SITE_TOKEN_ENV:
                self.token = KB_SITE_TOKEN_ENV
                self.obtained_at = time.monotonic()
                return self.token
            # 2) 카카오 자동로그인
            tok, hdrs = _kakao_login_capture(KB_EMAIL, KB_PW)
            if not tok:
                raise RuntimeError(
                    "KB 인증 실패: KB_SITE_TOKEN 환경변수를 주입하거나 KB_EMAIL/KB_PW로 "
                    "자동로그인이 되도록 설정하세요(서버는 playwright+chromium 필요)."
                )
            self.token, self.captured_headers = tok, hdrs
            self.obtained_at = time.monotonic()
            return self.token

    def get_captured_headers(self) -> dict | None:
        """지역 API(stutCdFilter)용 캡처 헤더. 자동로그인 했을 때만 존재."""
        if self.captured_headers is None and not KB_SITE_TOKEN_ENV:
            self.get_token()
        return self.captured_headers


AUTH = _Auth()


def _kakao_login_capture(email: str, password: str):
    """카카오 자동로그인 → (siteToken, 캡처 인증헤더). playwright 필요."""
    if not email or not password:
        return None, None
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWT
    except ModuleNotFoundError:
        raise RuntimeError("playwright 미설치: `pip install playwright && playwright install chromium` "
                           "또는 KB_SITE_TOKEN 환경변수로 토큰을 주입하세요.")
    KB_API_PAT = re.compile(r"^https://api\.kbland\.kr/")
    captured: dict = {"request_headers": {}}
    timeout_ms = 30000

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            ctx = browser.new_context(locale="ko-KR", viewport={"width": 1440, "height": 1080})

            def handle_route(route, request):
                u = urllib.parse.urlparse(request.url)
                if u.netloc == "api.kbland.kr" and "pointInfo" in u.path:
                    captured["request_headers"] = {
                        k: v for k, v in request.headers.items()
                        if k.lower() not in ("content-length", "host")
                    }
                route.continue_()
            ctx.route(KB_API_PAT, handle_route)
            page = ctx.new_page()

            # 카카오 로그인
            page.goto(KBLAND_HOME, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.locator(".homePopupcon.open .btn.btn-close").first.click(timeout=2000)
            except PWT:
                pass
            try:
                page.get_by_role("button", name="로그인하기", exact=True).click(timeout=timeout_ms)
            except PWT:
                page.get_by_role("button", name="메뉴", exact=True).click(timeout=timeout_ms)
                page.get_by_role("button", name="로그인하기", exact=True).click(timeout=timeout_ms)
            page.locator(".btn.btn-login.kakao").wait_for(timeout=timeout_ms)
            with page.expect_popup(timeout=timeout_ms) as pop:
                page.locator(".btn.btn-login.kakao").click(timeout=timeout_ms)
            popup = pop.value
            popup.locator("input[name='loginId']").fill(email, timeout=timeout_ms)
            popup.locator("input[name='password']").fill(password, timeout=timeout_ms)
            popup.locator("button[type='submit']").click(timeout=timeout_ms)
            try:
                popup.wait_for_event("close", timeout=timeout_ms)
            except PWT:
                pass
            page.bring_to_front()

            # siteToken (vuex.member.siteToken) 폴링
            token = None
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                member = page.evaluate(
                    "() => { try { return JSON.parse(localStorage.getItem('vuex')||'{}').member || {}; }"
                    " catch(e){ return {}; } }") or {}
                if member.get("isLogin") and member.get("siteToken"):
                    token = member.get("siteToken")
                    break
                page.wait_for_timeout(1500)

            # 지역 API용 인증헤더 캡처 트리거
            try:
                page.goto(KBLAND_TRIGGER, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PWT:
                pass
            hdrs = dict(captured.get("request_headers") or {})
            return token, (hdrs or None)
        finally:
            browser.close()


# ──────────────────────────────────────────────────────────────────────────
# 3. KB API 클라이언트
# ──────────────────────────────────────────────────────────────────────────
_session = requests.Session()


def _sleep():
    if REQUEST_DELAY > 0:
        time.sleep(REQUEST_DELAY)


def _api(method: str, url: str, ctx: str, **kw) -> requests.Response:
    """모든 KB API 호출의 단일 통로. 요청/지연/오류/비정상응답을 로깅.
    통신오류·HTTP오류는 여기서 원인(상태·본문)을 남기고 예외를 올린다."""
    _sleep()
    t0 = time.monotonic()
    try:
        r = _session.request(method, url, timeout=kw.pop("timeout", 20), **kw)
    except requests.RequestException as e:
        log.error("API 통신실패 [%s] %s :: %s", ctx, url, e)      # 연결리셋/타임아웃 등
        raise
    dt = (time.monotonic() - t0) * 1000
    if r.status_code != 200:
        # 401=토큰만료, 403/429/연결차단=레이트리밋/WAF 등 → 원인 즉시 파악용
        log.warning("API 비정상응답 %s [%s] %s :: %s", r.status_code, ctx, url, r.text[:300])
    else:
        log.debug("API %s [%s] %sms %dB", r.status_code, ctx, int(dt), len(r.content))
    return r


def _body(r: requests.Response, ctx: str) -> dict:
    """응답 JSON 파싱 + KB측 오류(dataHeader) 로깅."""
    try:
        d = r.json()
    except Exception as e:
        log.error("응답 JSON 파싱실패 [%s] :: %s | 본문=%s", ctx, e, r.text[:200])
        raise
    dh = d.get("dataHeader") or {}
    ok = str(dh.get("resultCode", dh.get("successFlag", ""))) in ("", "0", "10000", "200", "S", "true", "True")
    if dh and not ok:
        log.warning("KB 응답이상 [%s] dataHeader=%s", ctx, dh)   # KB측 거절/오류 메시지
    return d


def kb_search(keyword: str, n: int = 15) -> list[dict]:
    """통합검색(intgraSerch) — 아파트 단지 후보. 토큰 불필요."""
    r = _api("GET", SEARCH_URL + "?" + urllib.parse.urlencode(
        {"검색설정명": "SRC_NTOTAL", "검색키워드": keyword, "출력갯수": n, "페이지설정값": 1}),
        ctx=f"search:{keyword}", headers=_PUB_HEADERS)
    data = ((_body(r, "search").get("dataBody") or {}).get("data") or {}).get("data") or {}
    cands = (data.get("HSCM") or {}).get("data") or []
    log.debug("검색 '%s' → 후보 %d건", keyword, len(cands))
    return cands


def kb_geocode(address: str) -> tuple[float, float] | None:
    """주소 → (lat, lng). SRC_JUSO 검색. 토큰 불필요."""
    r = _api("GET", SEARCH_URL, ctx=f"geocode:{address}", headers=_PUB_HEADERS,
             params={"검색설정명": "SRC_JUSO", "검색키워드": address, "출력갯수": "2", "페이지설정값": "1"}, timeout=15)
    juso = (((_body(r, "geocode").get("dataBody") or {}).get("data") or {}).get("data") or {}).get("JUSO") or {}
    items = juso.get("data") or []
    if not items:
        log.warning("지오코딩 결과없음: '%s' (좌표 미확인 → 지역수집 불가)", address)
        return None
    lat, lng = items[0].get("WGS84_LAT"), items[0].get("WGS84_LNG")
    if not (lat and lng):
        log.warning("지오코딩 좌표누락: '%s' item=%s", address, items[0])
        return None
    return float(lat), float(lng)


def kb_count_by_trade(complex_no) -> dict:
    r = _api("GET", COUNT_URL, ctx=f"count:{complex_no}", headers=_PUB_HEADERS,
             params={"단지기본일련번호": complex_no})
    return (_body(r, "count").get("dataBody") or {}).get("data") or {}


def kb_list_complex(complex_no, trade_code: str = "1", page: int = 1, size: int = 30) -> dict:
    """단지 매물(propList/main, 서명). trade_code 1=매매,2=전세,3=월세."""
    body = {
        "단지기본일련번호": complex_no, "매물종별구분": "01", "페이지번호": page,
        "페이지목록수": size, "중복타입": "02", "정렬타입": "date",
        "매물거래구분": trade_code, "면적일련번호": "", "전자계약여부": "0",
    }
    r = _api("POST", PROP_MAIN_URL, ctx=f"list_complex:{complex_no}:p{page}",
             data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
             headers=_signed_headers(AUTH.get_token()), timeout=30)
    if r.status_code == 401:
        # 토큰 만료 추정 → 1회 강제 재발급 후 재시도
        log.warning("단지매물 401(토큰만료 추정) — 토큰 재발급 후 재시도 %s", complex_no)
        r = _api("POST", PROP_MAIN_URL, ctx=f"list_complex:{complex_no}:p{page}:retry",
                 data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                 headers=_signed_headers(AUTH.get_token(force=True)), timeout=30)
    return (_body(r, "list_complex").get("dataBody") or {}).get("data") or {}


def kb_list_complex_all(complex_no, trade_code: str = "1", size: int = 30, max_pages: int = 40) -> list[dict]:
    out, page = [], 1
    while page <= max_pages:
        data = kb_list_complex(complex_no, trade_code, page, size)
        pl = data.get("propertyList") or []
        out.extend(pl)
        total_pages = data.get("페이지개수") or 1
        if page >= total_pages or not pl:
            break
        page += 1
    else:
        # while가 break 없이 끝남 = max_pages 도달 → 누락 가능성 경고
        log.warning("단지 %s 매물 페이지 상한(%d) 도달 — 일부 누락 가능(수집 %d건)",
                    complex_no, max_pages, len(out))
    log.debug("단지 %s 매매 수집 %d건", complex_no, len(out))
    return out


def kb_list_region(lat: float, lng: float, lawd_code: str, property_types: str,
                   transaction_types: str, max_pages: int = 200) -> list[dict]:
    """지역(법정동) 매물(stutCdFilter). 캡처 인증헤더 사용(자동로그인 필요)."""
    headers = AUTH.get_captured_headers()
    if not headers:
        log.warning("지역수집: 캡처헤더 없음(토큰주입 환경) → 서명헤더 best-effort (실패 가능)")
        headers = _signed_headers(AUTH.get_token())
    out, page = [], 1
    while page <= max_pages:
        payload = _region_payload(lat, lng, lawd_code, property_types, transaction_types, page, 30)
        r = _api("POST", PROP_REGION_URL, ctx=f"list_region:{lawd_code}:p{page}", json=payload, timeout=20,
                 headers=headers)
        data = (_body(r, "list_region").get("dataBody") or {}).get("data") or {}
        pl = data.get("propertyList") or []
        out.extend(pl)
        total = _to_int(data.get("총매물건수")) or 0
        if page * 30 >= total or not pl:
            break
        page += 1
    else:
        log.warning("지역 %s 매물 페이지 상한(%d) 도달 — 일부 누락 가능(수집 %d건)",
                    lawd_code, max_pages, len(out))
    log.info("지역 %s 수집 %d건", lawd_code, len(out))
    return out


def _region_payload(lat, lng, lawd_code, prop_types, trade_types, page, size):
    p = {"selectCode": "2", "zoomLevel": 18, "startLat": lat, "startLng": lng,
         "endLat": lat, "endLng": lng, "물건종류": prop_types, "거래유형": trade_types}
    for k in ("매매시작값", "매매종료값", "보증금시작값", "보증금종료값", "월세시작값", "월세종료값",
              "면적시작값", "면적종료값", "준공년도시작값", "준공년도종료값", "방수", "욕실수",
              "세대수시작값", "세대수종료값", "관리비시작값", "관리비종료값", "용적률시작값", "용적률종료값",
              "건폐율시작값", "건폐율종료값", "전세가율시작값", "전세가율종료값", "매매전세차시작값",
              "매매전세차종료값", "월세수익률시작값", "월세수익률종료값", "구조", "주차", "엘리베이터",
              "보안옵션", "매물", "융자금", "분양단지구분코드", "일반분양여부", "분양진행단계코드", "옵션",
              "점포수시작값", "점포수종료값", "지상층", "지하층", "지목", "용도지역", "추진현황"):
        p[k] = ""
    p.update({"webCheck": "Y", "페이지번호": page, "페이지목록수": size, "중복타입": "01",
              "정렬타입": "date", "사진있는매물순": False, "전자계약여부": "0", "비대면대출여부": "0",
              "클린주택여부": "0", "honeyYn": "0", "법정동코드": lawd_code})
    return p


# ──────────────────────────────────────────────────────────────────────────
# 4. 주소 → KB단지 매칭 (kb_match 인라인)
# ──────────────────────────────────────────────────────────────────────────
_TAIL = re.compile(r"\s*(제?\s*[0-9가-힣]+동)?\s*(제?\s*[0-9]+층)?\s*(제?\s*[0-9A-Za-z가-힣]*-?[0-9]+호).*$")
_NOISE_SUFFIX = re.compile(r"(아파트|아파트단지|단지)$")
# 감정평가/등기 표기 잔재(예: '제주건축물', '제2동', '외3필지')를 이름 꼬리에서 제거
_JUNK_SUFFIX = re.compile(r"\s*(제?\s*[가-힣0-9]*건축물|제\s*\d+동|외\s*\d+필지).*$")
_BRAND_ALIASES = [("이편한세상", "e편한세상"), ("에스케이뷰", "SK뷰"), ("에스케이", "SK"),
                  ("케이비", "KB"), ("엘에이치", "LH"), ("아이파크", "아이파크")]
# 스코어링 정규화용 별칭 통일(경매표기↔KB표기). 검색뿐 아니라 이름점수에도 반영.
_CANON_MAP = [("이편한세상", "e편한세상"), ("에스케이", "sk"), ("케이비", "kb"), ("엘에이치", "lh")]
ACCEPT_THRESHOLD = 0.6


def _strip_jibun(rest: str) -> str:
    # 지번(숫자[-숫자] [외N필지]) + 공백 을 1회 제거. 이전엔 뒤 문자가 '지/구/단/차'면
    # 통째로 스킵해 '지에스금정'·'지아이오' 같은 이름을 지번으로 오인·미제거했음 → 가드 제거.
    # ('3차현대'처럼 숫자+차가 이름이면 공백이 없어 애초에 매칭되지 않으므로 안전.)
    return re.sub(r"^\s*\d+(-\d+)?(외\s*\d+필지)?\s+", "", rest).strip()


def extract_complex_name(address: str) -> str | None:
    if not address:
        return None
    a = re.sub(r"\([^)]*\)", " ", address)
    a = re.sub(r",", " ", a)
    a = re.sub(r"\s+", " ", a).strip()
    m = re.search(
        r"((?:[가-힣]+(?:특별자치도|특별자치시|특별시|광역시|도))\s+)?"
        r"([가-힣]+시\s+)?([가-힣]+군\s+)?([가-힣]+구\s+)?"
        r"([가-힣]+(?:읍|면)\s+)?([가-힣]+동\d+가\s+|[가-힣]+(?:동|리|가)\s+)?"
        r"(?:[가-힣0-9]+(?:로|길)\s+)?(.*)$", a)
    rest = m.group(7) if m else a
    rest = _strip_jibun(rest)
    rest = _TAIL.sub("", rest).strip()
    rest = _JUNK_SUFFIX.sub("", rest).strip()      # 감정평가 잔재 제거
    rest = _NOISE_SUFFIX.sub("", rest).strip()
    return rest or None


def _alias_variants(keyword: str) -> list[str]:
    out = [keyword]
    m = re.match(r"^제?(\d+)차(.+)$", keyword)
    if m:
        num, rest = m.group(1), m.group(2)
        out += [f"{rest}{num}차", rest]
    for a, b in _BRAND_ALIASES:
        if a in keyword:
            out.append(keyword.replace(a, b))
        if b in keyword:
            out.append(keyword.replace(b, a))
    return list(dict.fromkeys(out))


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_()·,.]", "", unicodedata.normalize("NFKC", s or "")).lower()


def _canon(s: str) -> str:
    """정규화 + 브랜드 별칭 통일(에스케이→sk, 이편한세상→e편한세상 …). 이름점수 비교용."""
    n = _norm(s)
    for a, b in _CANON_MAP:
        n = n.replace(a, b)
    return n


def _sigungu_tokens(addr: str) -> list[str]:
    return [t for t in re.findall(r"[가-힣]+(?:시|군|구)", addr or "") if len(t) >= 2]


def _emd_tokens(addr: str) -> list[str]:
    return [t for t in re.findall(r"[가-힣]+(?:읍|면|동|리|가)", addr or "") if len(t) >= 2]


def _region_match(our_addr: str, kb_bub: str) -> tuple[bool, bool]:
    kb = re.sub(r"\s+", "", kb_bub or "")
    sgg = _sigungu_tokens(our_addr)
    emd = _emd_tokens(our_addr)
    return (any(t in kb for t in sgg) if sgg else False,
            any(t in kb for t in emd) if emd else False)


def _score(cand: dict, our_addr: str, name_kw: str) -> tuple[float, bool]:
    bub = cand.get("BUBADDR", "") + " " + cand.get("HSCM_NM_EXT", "")
    sgg_ok, emd_ok = _region_match(our_addr, bub)
    region_ok = sgg_ok and emd_ok
    nk, nm, tag = _canon(name_kw), _canon(cand.get("HSCM_NM", "")), _canon(cand.get("HSCM_TAG", ""))
    if nk and (nk in nm or nm in nk):
        name_score = 0.5
    elif nk and nk in tag:
        name_score = 0.4
    elif nk and nm and nk[:4] == nm[:4]:
        name_score = 0.2
    else:
        name_score = 0.0
    if not region_ok:
        return name_score * 0.2, False
    return round((0.5 if (sgg_ok and emd_ok) else 0.25) + name_score, 3), True


def match_address(address: str, n: int = 15) -> dict:
    name = extract_complex_name(address)
    res = {"our_address": address, "extracted_name": name, "search_kw": None, "complex_no": None,
           "kb_name": None, "kb_bubaddr": None, "confidence": 0.0, "n_candidates": 0, "region_ok": False}
    if not name:
        # 미매칭 사유①: 주소에서 단지명 추출 실패
        log.info("매칭실패[단지명없음] 주소='%s'", (address or "")[:60])
        res["fail_reason"] = "단지명추출실패"
        return res
    emd = _emd_tokens(address)
    keywords = _alias_variants(name)
    if emd:
        keywords.append(f"{emd[-1]} {name}")
    best, best_score, best_kw, best_region, total = None, -1.0, None, False, 0
    for kw in keywords:
        cands = kb_search(kw, n)
        total += len(cands)
        for c in cands:
            s, region_ok = _score(c, address, name)
            if s > best_score:
                best, best_score, best_kw, best_region = c, s, kw, region_ok
        if best_region and best_score >= ACCEPT_THRESHOLD:
            break
    res.update(n_candidates=total, search_kw=best_kw, region_ok=best_region)
    if best and best_score >= ACCEPT_THRESHOLD and best_region:
        res.update(complex_no=best.get("COMPLEX_NO"), obj_idnfr=best.get("OBJ_IDNFR"),
                   kb_name=best.get("HSCM_NM"), kb_bubaddr=best.get("BUBADDR"),
                   kb_households=best.get("THS_NUM"), confidence=round(best_score, 3), best_raw=best)
        log.debug("매칭성공 '%s'→'%s' conf=%.2f (검색어='%s')", name, best.get("HSCM_NM"),
                  best_score, best_kw)
    else:
        # 미매칭 사유 상세 로깅(원인분석용)
        if best is None:
            reason = f"검색결과0건(추출명='{name}')"
        elif not best_region:
            reason = f"지역게이트탈락(best='{best.get('HSCM_NM')}'@{(best.get('BUBADDR') or '')[:16]} score={best_score})"
        else:
            reason = f"임계미달(best='{best.get('HSCM_NM')}' score={best_score}<{ACCEPT_THRESHOLD})"
        log.info("매칭실패[%s] 주소='%s'", reason, (address or "")[:50])
        res["fail_reason"] = reason
        if best:
            res["best_reject"] = {"kb_name": best.get("HSCM_NM"), "bubaddr": best.get("BUBADDR"),
                                  "score": round(best_score, 3)}
    return res


# ──────────────────────────────────────────────────────────────────────────
# 5. DB 적재 (psycopg v3) — kb_crawl upsert 인라인
# ──────────────────────────────────────────────────────────────────────────
def _db_connect():
    if not DB_URL:
        raise RuntimeError("SUPABASE_DB_URL 환경변수가 필요합니다.")
    import psycopg
    return psycopg.connect(DB_URL, connect_timeout=20)


# ──────────────────────────────────────────────────────────────────────────
# 스키마 자동생성 — kb_* 4개 테이블(내장 DDL). items 요약컬럼은 items 존재 시에만.
# ──────────────────────────────────────────────────────────────────────────
SCHEMA_DDL = """
create table if not exists kb_complex (
  complex_no text primary key, obj_idnfr text, name text, bubaddr text, bubcode text,
  households int, build_ym text, area_scope text, lat double precision, lng double precision,
  deal_cnt int, lease_cnt int, rent_cnt int,
  first_seen timestamptz default now(), last_seen timestamptz default now(),
  updated_at timestamptz default now());
create table if not exists kb_item_match (
  item_key text primary key, complex_no text, match_name text, search_kw text,
  confidence numeric, region_ok boolean, status text, reject_info jsonb,
  matched_at timestamptz default now());
create table if not exists kb_listing (
  listing_id bigint primary key, complex_no text, item_key text, trade_type text, trade_code text,
  price bigint, price_min bigint, price_max bigint, deposit bigint, monthly bigint,
  area_excl numeric, area_supply numeric, area_no bigint, floor text, floor_total int,
  dong text, ho text, direction text, room_cnt int, bath_cnt int, unit_price int,
  agent_name text, agent_addr text, agent_phone text, source text, reg_date text,
  confirm_date text, feature text, photo_cnt int, dup_cnt int, raw jsonb,
  first_seen timestamptz default now(), last_seen timestamptz default now(),
  is_active boolean default true);
create index if not exists kb_listing_complex_idx on kb_listing(complex_no);
create index if not exists kb_listing_item_idx on kb_listing(item_key);
create table if not exists kb_listing_photo (
  listing_id bigint, seq int, url text, title text, primary key (listing_id, seq));
"""
# items 테이블이 있을 때만 적용(모드A 요약 역적재용)
ITEMS_ALTER_DDL = [
    "alter table items add column if not exists kb_complex_no text",
    "alter table items add column if not exists kb_match_conf numeric",
    "alter table items add column if not exists kb_deal_cnt int",
    "alter table items add column if not exists kb_lease_cnt int",
    "alter table items add column if not exists kb_rent_cnt int",
    "alter table items add column if not exists kb_deal_min bigint",
    "alter table items add column if not exists kb_deal_max bigint",
    "alter table items add column if not exists kb_synced_at timestamptz",
]


def _table_exists(cur, name: str) -> bool:
    cur.execute("select to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def init_db(create_items_columns: bool = True) -> dict:
    """kb_* 테이블 생성(idempotent). items 있으면 요약컬럼도 추가. 결과 리포트 반환."""
    log.info("init_db 시작")
    con = _db_connect(); con.autocommit = True; cur = con.cursor()
    cur.execute(SCHEMA_DDL)
    report = {"created_or_ok": ["kb_complex", "kb_item_match", "kb_listing", "kb_listing_photo"]}
    has_items = _table_exists(cur, "items")
    report["items_table"] = has_items
    if has_items and create_items_columns:
        for ddl in ITEMS_ALTER_DDL:
            cur.execute(ddl)
        report["items_columns"] = "added_or_ok"
    elif not has_items:
        report["items_columns"] = "SKIPPED(items 테이블 없음 → 모드A 불가, 모드B만 사용)"
        log.warning("items 테이블이 없어 모드A(아파트수집) 불가. 모드B(지역)만 사용 가능.")
    cur.close(); con.close()
    log.info("init_db 완료: %s", report)
    return report


def selfcheck() -> tuple[bool, dict]:
    """받은 프로젝트에서 뭐가 준비됐고 뭐가 없는지 콕 집어주는 자가진단.
    반환: (전체 OK 여부, 상세 리포트). CLI: python kb_crawler.py --selfcheck"""
    rpt: dict = {"python": sys.version.split()[0], "checks": {}}

    def chk(key, ok, detail=""):
        rpt["checks"][key] = {"ok": bool(ok), "detail": detail}

    # 파이썬 버전
    chk("python>=3.10", sys.version_info >= (3, 10), rpt["python"])
    # 의존성
    for mod, need in [("requests", "필수"), ("psycopg", "DB필수"), ("cryptography", "서명필수"),
                      ("fastapi", "웹통합"), ("pydantic", "웹통합"), ("playwright", "자동로그인")]:
        try:
            __import__(mod); chk(f"dep:{mod}", True, need)
        except Exception:
            chk(f"dep:{mod}", False, f"{need} — pip install {mod}")
    # 인증 설정
    chk("auth", bool(KB_SITE_TOKEN_ENV or (KB_EMAIL and KB_PW)),
        "KB_SITE_TOKEN 또는 KB_EMAIL/KB_PW 필요")
    # DB 연결 + 테이블
    if DB_URL:
        try:
            con = _db_connect(); cur = con.cursor()
            chk("db_connect", True, "연결 OK")
            for t in ("kb_complex", "kb_item_match", "kb_listing", "kb_listing_photo"):
                chk(f"table:{t}", _table_exists(cur, t), "" if _table_exists(cur, t) else "--init-db 필요")
            has_items = _table_exists(cur, "items")
            chk("table:items(모드A소스)", has_items, "" if has_items else "없으면 모드B만 가능")
            cur.close(); con.close()
        except Exception as e:
            chk("db_connect", False, str(e)[:120])
    else:
        chk("db_connect", False, "SUPABASE_DB_URL 미설정(모드B만 쓰면 무관)")

    # 필수(모드B 최소 동작) 판정: python + requests + auth
    essential = all(rpt["checks"][k]["ok"] for k in ("python>=3.10", "dep:requests", "auth"))
    rpt["ready_mode_B(region)"] = essential
    rpt["ready_mode_A(apartments)"] = essential and rpt["checks"].get("dep:psycopg", {}).get("ok") \
        and rpt["checks"].get("db_connect", {}).get("ok") \
        and rpt["checks"].get("table:kb_listing", {}).get("ok") \
        and rpt["checks"].get("table:items(모드A소스)", {}).get("ok")
    return essential, rpt


def _num(v):
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _to_int(v):
    f = _num(v)
    return int(f) if f is not None else None


def _upsert_complex(cur, complex_no, raw, counts):
    cur.execute("""
        insert into kb_complex (complex_no,obj_idnfr,name,bubaddr,bubcode,households,build_ym,
          area_scope,lat,lng,deal_cnt,lease_cnt,rent_cnt,last_seen,updated_at)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
        on conflict (complex_no) do update set obj_idnfr=excluded.obj_idnfr,name=excluded.name,
          bubaddr=excluded.bubaddr,bubcode=excluded.bubcode,households=excluded.households,
          build_ym=excluded.build_ym,area_scope=excluded.area_scope,lat=excluded.lat,lng=excluded.lng,
          deal_cnt=excluded.deal_cnt,lease_cnt=excluded.lease_cnt,rent_cnt=excluded.rent_cnt,
          last_seen=now(),updated_at=now()
    """, (str(complex_no), raw.get("OBJ_IDNFR"), raw.get("HSCM_NM"), raw.get("BUBADDR"),
          raw.get("BUBCODE"), _to_int(raw.get("THS_NUM")), raw.get("MVIHS_DATE"),
          raw.get("SQRMSR_SCOP"), _num(raw.get("WGS84_LAT")), _num(raw.get("WGS84_LNG")),
          _to_int(counts.get("매매건수")), _to_int(counts.get("전세건수")), _to_int(counts.get("월세건수"))))


def _upsert_match(cur, item_key, m):
    matched = bool(m.get("complex_no"))
    cur.execute("""
        insert into kb_item_match (item_key,complex_no,match_name,search_kw,confidence,region_ok,status,reject_info,matched_at)
        values (%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict (item_key) do update set complex_no=excluded.complex_no,match_name=excluded.match_name,
          search_kw=excluded.search_kw,confidence=excluded.confidence,region_ok=excluded.region_ok,
          status=excluded.status,reject_info=excluded.reject_info,matched_at=now()
    """, (item_key, m.get("complex_no"), m.get("extracted_name"), m.get("search_kw"), m.get("confidence"),
          m.get("region_ok"), "matched" if matched else "unmatched",
          json.dumps(m.get("best_reject"), ensure_ascii=False) if m.get("best_reject") else None))


def _big(v):
    return _to_int(v)


def _upsert_listing(cur, p, complex_no, item_key):
    lid = _big(p.get("매물일련번호"))
    if lid is None:
        return None
    cur.execute("""
        insert into kb_listing (listing_id,complex_no,item_key,trade_type,trade_code,price,price_min,price_max,
          deposit,monthly,area_excl,area_supply,area_no,floor,floor_total,dong,ho,direction,room_cnt,bath_cnt,
          unit_price,agent_name,agent_addr,agent_phone,source,reg_date,confirm_date,feature,photo_cnt,dup_cnt,
          raw,last_seen,is_active)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),true)
        on conflict (listing_id) do update set price=excluded.price,price_min=excluded.price_min,
          price_max=excluded.price_max,deposit=excluded.deposit,monthly=excluded.monthly,floor=excluded.floor,
          agent_name=excluded.agent_name,agent_phone=excluded.agent_phone,source=excluded.source,
          reg_date=excluded.reg_date,confirm_date=excluded.confirm_date,feature=excluded.feature,
          photo_cnt=excluded.photo_cnt,dup_cnt=excluded.dup_cnt,raw=excluded.raw,last_seen=now(),is_active=true
    """, (lid, str(complex_no), item_key, p.get("매물거래구분명"), p.get("매물거래구분"),
          _big(p.get("매매가")), _big(p.get("최소매매가")), _big(p.get("최대매매가")), _big(p.get("전세가")),
          _big(p.get("월세가")), _num(p.get("순전용면적") or p.get("전용면적")),
          _num(p.get("순공급면적") or p.get("공급면적")), _big(p.get("면적일련번호")), p.get("해당층수"),
          _to_int(p.get("총층수")), p.get("건물동명"), p.get("건물호명"), p.get("방향구분명"),
          _to_int(p.get("방수")), _to_int(p.get("욕실수")), _to_int(p.get("평당단가")), p.get("중개업소명"),
          p.get("중개업소주소"), p.get("중개업소전화번호"), p.get("매물유입구분명"), p.get("등록년월일"),
          p.get("매물확인년월일"), p.get("특징광고내용"), _to_int(p.get("매물이미지개수")),
          _to_int(p.get("중복개수")), json.dumps(p, ensure_ascii=False)))
    return lid


def _deactivate_missing(cur, complex_no, item_key, current_ids):
    cur.execute("""update kb_listing set is_active=false where complex_no=%s and item_key=%s
                   and is_active=true and not (listing_id = any(%s))""",
                (str(complex_no), item_key, current_ids or [-1]))


def _update_item_summary(cur, item_key, complex_no, conf, counts, listings):
    prices = [x for x in (_big(p.get("매매가")) for p in listings) if x]
    cur.execute("""update items set kb_complex_no=%s,kb_match_conf=%s,kb_deal_cnt=%s,kb_lease_cnt=%s,
                   kb_rent_cnt=%s,kb_deal_min=%s,kb_deal_max=%s,kb_synced_at=now() where item_key=%s""",
                (str(complex_no) if complex_no else None, conf, _to_int(counts.get("매매건수")),
                 _to_int(counts.get("전세건수")), _to_int(counts.get("월세건수")),
                 min(prices) if prices else None, max(prices) if prices else None, item_key))


# ──────────────────────────────────────────────────────────────────────────
# 6. 수집 오케스트레이션
# ──────────────────────────────────────────────────────────────────────────
def collect_apartments(limit: int | None = None, resume: bool = True, dry: bool = False,
                       progress: dict | None = None) -> dict:
    """진행중 아파트 → 매칭 → 매매매물 수집 → DB. progress dict 에 진행상황 기록."""
    log.info("아파트 수집 시작 (limit=%s, resume=%s, dry=%s)", limit, resume, dry)
    AUTH.get_token()  # 사전 인증(실패 시 즉시 예외)
    con = _db_connect()
    con.autocommit = False
    cur = con.cursor()
    sql = ACTIVE_APT_SQL
    if resume:
        sql += " and item_key not in (select item_key from kb_item_match)"
    sql += " order by item_key"
    if limit:
        sql += f" limit {int(limit)}"
    cur.execute(sql)
    rows = cur.fetchall()
    stat = {"target": len(rows), "processed": 0, "matched": 0, "unmatched": 0,
            "listings": 0, "zero_listing": 0, "errors": 0, "status": "running", "errors_detail": []}
    log.info("대상 %d건", len(rows))
    if progress is not None:
        progress.update(stat)
    for item_key, address in rows:
        try:
            m = match_address(address)
            if dry:
                stat["processed"] += 1
                if progress is not None:
                    progress.update(stat)
                continue
            _upsert_match(cur, item_key, m)
            cno = m.get("complex_no")
            if not cno:
                stat["unmatched"] += 1          # 사유는 match_address 가 이미 로깅
                con.commit()
            else:
                stat["matched"] += 1
                counts = kb_count_by_trade(cno)
                listings = kb_list_complex_all(cno, trade_code="1")
                counts["매매건수"] = len(listings)
                _upsert_complex(cur, cno, m["best_raw"], counts)
                ids = []
                for p in listings:
                    lid = _upsert_listing(cur, p, cno, item_key)
                    if lid is not None:
                        ids.append(lid)
                        stat["listings"] += 1
                    else:
                        # 누락: 매물일련번호 없어 적재 스킵
                        log.warning("적재스킵(매물일련번호 없음) item=%s 단지=%s", item_key, cno)
                _deactivate_missing(cur, cno, item_key, ids)
                _update_item_summary(cur, item_key, cno, m.get("confidence"), counts, listings)
                con.commit()
                if not listings:
                    stat["zero_listing"] += 1
                    log.info("수집완료 item=%s → %s 매매 0건(현재 매물없음)", item_key, m.get("kb_name"))
                else:
                    log.info("수집완료 item=%s → %s 매매 %d건 (conf %.2f)",
                             item_key, m.get("kb_name"), len(listings), m.get("confidence") or 0)
        except Exception as e:  # noqa: BLE001
            con.rollback()
            stat["errors"] += 1
            # 전체 트레이스백을 파일로그에 남김 → 사후 원인분석
            log.exception("수집오류 item=%s 주소='%s' :: %s", item_key, (address or "")[:40], e)
            stat["errors_detail"].append({"item_key": item_key, "error": str(e)[:200],
                                          "trace": traceback.format_exc()[-800:]})
            del stat["errors_detail"][:-50]
            stat["last_error"] = str(e)[:200]
        stat["processed"] += 1
        if progress is not None:
            progress.update(stat)
        if stat["processed"] % 100 == 0:
            log.info("진행 %d/%d (매칭%d 미매칭%d 매물%d 에러%d)", stat["processed"], stat["target"],
                     stat["matched"], stat["unmatched"], stat["listings"], stat["errors"])
    cur.close()
    con.close()
    stat["status"] = "done"
    log.info("아파트 수집 완료: %s", {k: stat[k] for k in
             ("target", "processed", "matched", "unmatched", "listings", "zero_listing", "errors")})
    if progress is not None:
        progress.update(stat)
    return stat


def collect_region(address: str | None, lawd_code: str, property_types: str = "01",
                   transaction_types: str = "1", lat: float | None = None,
                   lng: float | None = None) -> dict:
    """지역(법정동) 단위 매물 수집 → JSON 반환(중개사 포함 매물 리스트)."""
    log.info("지역 수집 시작 lawd=%s 물건=%s 거래=%s addr=%s", lawd_code, property_types,
             transaction_types, address)
    if lat is None or lng is None:
        if not address:
            raise ValueError("address 또는 lat/lng 가 필요합니다.")
        geo = kb_geocode(address)
        if not geo:
            raise ValueError(f"좌표를 찾지 못했습니다: {address}")
        lat, lng = geo
    props = kb_list_region(lat, lng, lawd_code, property_types, transaction_types)
    # 중개사 요약(중복 제거)
    seen, brokers = set(), []
    for p in props:
        key = (p.get("중개업소명"), p.get("중개업소전화번호"))
        if key not in seen:
            seen.add(key)
            brokers.append({k: p.get(k) for k in
                            ("중개업소명", "중개업소대표자명", "중개업소주소", "중개업소전화번호",
                             "중개업소대표자휴대폰번호")})
    return {"lat": lat, "lng": lng, "lawd_code": lawd_code, "count": len(props),
            "broker_count": len(brokers), "properties": props, "brokers": brokers}


# ──────────────────────────────────────────────────────────────────────────
# 7. FastAPI 라우터
# ──────────────────────────────────────────────────────────────────────────
try:
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel

    router = APIRouter(prefix="/kb", tags=["kb-crawler"])
    _JOBS: dict[str, dict] = {}

    class AptReq(BaseModel):
        limit: int | None = None
        resume: bool = True
        dry: bool = False

    class RegionReq(BaseModel):
        address: str | None = None
        lawd_code: str
        property_types: str = "01"     # 아파트
        transaction_types: str = "1"   # 매매
        lat: float | None = None
        lng: float | None = None

    def _run_apt_job(job_id: str, req: AptReq):
        prog = _JOBS[job_id]
        try:
            collect_apartments(limit=req.limit, resume=req.resume, dry=req.dry, progress=prog)
        except Exception as e:  # noqa: BLE001
            prog.update(status="error", error=str(e)[:300])

    @router.post("/collect/apartments")
    def start_apartments(req: AptReq):
        """아파트 매매 수집 시작(백그라운드). job_id 반환."""
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {"status": "queued", "processed": 0, "target": None}
        threading.Thread(target=_run_apt_job, args=(job_id, req), daemon=True).start()
        return {"job_id": job_id}

    @router.get("/collect/jobs/{job_id}")
    def job_status(job_id: str):
        if job_id not in _JOBS:
            raise HTTPException(404, "job not found")
        return _JOBS[job_id]

    @router.post("/collect/region")
    def collect_region_ep(req: RegionReq):
        """지역(법정동) 매물 수집 → JSON 반환."""
        try:
            return collect_region(req.address, req.lawd_code, req.property_types,
                                  req.transaction_types, req.lat, req.lng)
        except Exception as e:  # noqa: BLE001
            log.exception("지역수집 실패 lawd=%s :: %s", req.lawd_code, e)   # 전체 트레이스백
            raise HTTPException(500, str(e))

    @router.get("/logs")
    def get_logs(level: str = "INFO", limit: int = 200):
        """최근 로그/에러 조회 — 오류·누락 원인을 API로 바로 확인.
        level=ERROR 로 주면 경고/오류만. limit 로 줄 수 조절."""
        if level.upper() in ("WARNING", "ERROR"):
            return {"errors": _RECENT_ERRORS[-limit:], "count": len(_RECENT_ERRORS)}
        return {"logs": _RECENT_LOGS[-limit:], "count": len(_RECENT_LOGS)}

    @router.post("/init-db")
    def init_db_ep():
        """kb_* 테이블 생성(idempotent). 최초 1회 또는 배포 후 호출."""
        try:
            return init_db()
        except Exception as e:  # noqa: BLE001
            log.exception("init-db 실패 :: %s", e)
            raise HTTPException(500, str(e))

    @router.get("/health")
    def health():
        ok, rpt = selfcheck()
        return {
            "ready": ok,
            "auth_mode": "token_injected" if KB_SITE_TOKEN_ENV else ("auto_login" if KB_EMAIL else "none"),
            "token_cached": bool(AUTH.token),
            "request_delay": REQUEST_DELAY,
            "log_level": os.environ.get("KB_LOG_LEVEL", "INFO"),
            "recent_error_count": len(_RECENT_ERRORS),
            "last_errors": _RECENT_ERRORS[-3:],
            "selfcheck": rpt,
        }
except ModuleNotFoundError:
    # fastapi 미설치 환경에서도 import 가능하도록(함수만 사용)
    router = None


# ──────────────────────────────────────────────────────────────────────────
# 8. Standalone CLI — 웹 없이 이 파일만으로 진단/초기화/수집 실행
#    python kb_crawler.py --selfcheck
#    python kb_crawler.py --init-db
#    python kb_crawler.py --region "대전 서구 도마동" 3017010300 [물건종류 거래유형]
#    python kb_crawler.py --collect [--limit N] [--dry]
# ──────────────────────────────────────────────────────────────────────────
def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="KB부동산 수집 (자체완결 단일 파일)")
    ap.add_argument("--selfcheck", action="store_true", help="환경 자가진단(뭐가 준비/부족한지)")
    ap.add_argument("--init-db", action="store_true", help="kb_* 테이블 생성(idempotent)")
    ap.add_argument("--region", nargs="+", metavar=("ADDR", "LAWD"),
                    help="지역 수집: 주소 법정동코드 [물건종류=01] [거래유형=1] → JSON")
    ap.add_argument("--collect", action="store_true", help="아파트 매매 수집(모드A, DB 적재)")
    ap.add_argument("--limit", type=int, help="--collect 처리 건수 제한")
    ap.add_argument("--dry", action="store_true", help="--collect 매칭만(적재 안함)")
    a = ap.parse_args(argv)

    if a.selfcheck:
        ok, rpt = selfcheck()
        print(json.dumps(rpt, ensure_ascii=False, indent=2))
        print(f"\n모드B(지역) 준비: {rpt['ready_mode_B(region)']} | "
              f"모드A(아파트) 준비: {rpt['ready_mode_A(apartments)']}")
        return 0 if ok else 1
    if getattr(a, "init_db"):
        print(json.dumps(init_db(), ensure_ascii=False, indent=2))
        return 0
    if a.region:
        addr = a.region[0]
        lawd = a.region[1] if len(a.region) > 1 else ""
        ptypes = a.region[2] if len(a.region) > 2 else "01"
        ttypes = a.region[3] if len(a.region) > 3 else "1"
        data = collect_region(address=addr, lawd_code=lawd,
                              property_types=ptypes, transaction_types=ttypes)
        # 매물 본체는 크니 요약만 출력
        print(json.dumps({k: data[k] for k in ("lat", "lng", "lawd_code", "count", "broker_count")},
                         ensure_ascii=False, indent=2))
        print(f"중개업소 {data['broker_count']}곳, 매물 {data['count']}건 (properties/brokers 전체는 함수 반환값 참조)")
        return 0
    if a.collect:
        stat = collect_apartments(limit=a.limit, dry=a.dry)
        print(json.dumps({k: stat[k] for k in
              ("target", "processed", "matched", "unmatched", "listings", "zero_listing", "errors")},
              ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
