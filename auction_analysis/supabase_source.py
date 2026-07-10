"""
Supabase(스피드옥션 제휴 데이터) 연동 어댑터.

DATA_ACCESS.md 스키마(items / auction_schedule / media)를 읽어
목록·상세를 반환한다. PostgREST(httpx) 기반이라 psycopg 불필요.

전제: 스피드옥션 '업무 제휴 자료 수집·배포 승인'(공개자료 한정)에 근거.
  - 승인서 제외항목 준수: 개인정보(채무자·소유자 등 실명)는 표시 시 마스킹.
  - 미디어(사진·서류)는 제3자 권리 유의.

환경변수
  SUPABASE_URL   = https://xxx.supabase.co
  SUPABASE_KEY   = anon 키(공개읽기 RLS 필요) 또는 service_role 키(RLS 우회)
  R2_PUBLIC_URL  = https://pub-xxx.r2.dev
  (대안) SUPABASE_DB_URL 로 psycopg 직접연결도 가능하나 본 모듈은 REST 사용.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import httpx

# 경매결과 그룹옵션 → 포함 상태(여러 result 값 OR 매칭). 스피드옥션 드롭다운의 묶음 항목과 동일.
_STATUS_GROUPS: dict[str, list[str]] = {
    "진행물건": ["신건", "유찰", "재진행", "재매각"],          # 아직 매각 안 된 진행 중
    "종결물건": ["매각", "배당종결", "잔금납부", "취하", "기각", "정지", "미진행", "대금미납", "변경", "불허"],  # 변경·불허=재경매 예정이나, 과거날짜 '그날 있었던 일' 조회 위해 포함
    "매각/잔금납부/배당종결": ["매각", "잔금납부", "배당종결"],   # 낙찰·완결
}

# 매각·종결(과거) 상태로 조회할 땐 백데이터(과거 매각분)도 포함해야 함 — 현황만 보면 매각물건이 통째 누락.
#   진행물건 계열(신건·유찰·재진행·재매각)만 현황 전용. (舊: '종결물건'만 특례라 '매각/잔금납부/배당종결'이 0건이던 버그)
_PAST_PREFIXES = {"종결물건", "매각/잔금납부/배당종결"} | set(_STATUS_GROUPS["종결물건"])

# 전국 시/도→시/군/구 전체 목록(cosmosfarm). 동/읍은 우리 데이터에서 채운다.
try:
    with open(os.path.join(os.path.dirname(__file__), "sigungu_kr.json"), encoding="utf-8") as _f:
        SIGUNGU_KR: dict = json.load(_f)
except OSError:
    SIGUNGU_KR = {}

# 시/도 표시용 짧은 이름 + 표시 순서(스피드옥션식)
_SIDO_SHORT = {
    "서울특별시": "서울", "경기도": "경기", "인천광역시": "인천", "부산광역시": "부산",
    "대구광역시": "대구", "대전광역시": "대전", "광주광역시": "광주", "울산광역시": "울산",
    "경상북도": "경북", "경상남도": "경남", "충청북도": "충북", "충청남도": "충남",
    "전북특별자치도": "전북", "전라남도": "전남", "강원특별자치도": "강원",
    "제주특별자치도": "제주", "세종특별자치시": "세종",
}
_SIDO_ORDER = ["서울특별시", "경기도", "인천광역시", "부산광역시", "대구광역시", "대전광역시",
               "광주광역시", "울산광역시", "경상북도", "경상남도", "충청북도", "충청남도",
               "전북특별자치도", "전라남도", "강원특별자치도", "제주특별자치도", "세종특별자치시"]


# 스피드옥션 법원코드 → (지방법원, 본원/지원). 데이터 주소 분포로 도출.
COURT_CODE_MAP = {
    "A01": ("서울중앙지법", "본원"), "A02": ("서울동부지법", "본원"),
    "A03": ("서울서부지법", "본원"), "A04": ("서울남부지법", "본원"),
    "A05": ("서울북부지법", "본원"),
    "B01": ("부산지법", "본원"), "B02": ("부산지법", "동부지원"), "B03": ("부산지법", "서부지원"),
    "C01": ("인천지법", "본원"), "C02": ("인천지법", "부천지원"),
    "D01": ("의정부지법", "본원"), "D02": ("의정부지법", "고양지원"), "D03": ("의정부지법", "남양주지원"),
    "E01": ("수원지법", "본원"), "E02": ("수원지법", "성남지원"), "E03": ("수원지법", "여주지원"),
    "E04": ("수원지법", "평택지원"), "E05": ("수원지법", "안산지원"), "E06": ("수원지법", "안양지원"),
    "F01": ("춘천지법", "본원"), "F02": ("춘천지법", "강릉지원"), "F04": ("춘천지법", "원주지원"),
    "F05": ("춘천지법", "영월지원"),
    "G01": ("청주지법", "본원"), "G02": ("청주지법", "충주지원"), "G03": ("청주지법", "제천지원"),
    "G04": ("청주지법", "영동지원"),
    "H01": ("대전지법", "본원"), "H02": ("대전지법", "천안지원"), "H03": ("대전지법", "공주지원"),
    "H04": ("대전지법", "서산지원"), "H05": ("대전지법", "홍성지원"), "H06": ("대전지법", "논산지원"),
    "I01": ("대구지법", "본원"), "I02": ("대구지법", "경주지원"), "I03": ("대구지법", "김천지원"),
    "I06": ("대구지법", "영덕지원"), "I07": ("대구지법", "의성지원"), "I08": ("대구지법", "포항지원"),
    "I09": ("대구지법", "서부지원"),
    "J01": ("창원지법", "본원"), "J03": ("창원지법", "밀양지원"), "J04": ("창원지법", "진주지원"),
    "J05": ("창원지법", "통영지원"), "J06": ("창원지법", "마산지원"),
    "K01": ("전주지법", "본원"), "K02": ("전주지법", "군산지원"), "K03": ("전주지법", "남원지원"),
    "K04": ("전주지법", "정읍지원"),
    "L01": ("광주지법", "본원"), "L02": ("광주지법", "순천지원"), "L03": ("광주지법", "목포지원"),
    "L05": ("광주지법", "해남지원"),
    "M01": ("제주지법", "본원"), "N01": ("울산지법", "본원"),
}

# 법원 드롭다운 표시 순서(서울→수도권→지방)
_JIBEOP_ORDER = [
    "서울중앙지법", "서울동부지법", "서울서부지법", "서울남부지법", "서울북부지법",
    "인천지법", "수원지법", "의정부지법", "춘천지법",
    "대전지법", "청주지법", "대구지법", "부산지법", "울산지법", "창원지법",
    "광주지법", "전주지법", "제주지법",
]


def court_display_name(court_code: str | None) -> str:
    """법원코드 → 표시용 법원명. 예: B03→'부산서부지원', A01→'서울중앙지방법원'."""
    t = COURT_CODE_MAP.get(court_code or "")
    if not t:
        return ""
    jibeop, branch = t
    if branch in ("", "본원"):
        return jibeop.replace("지법", "지방법원")
    return jibeop.replace("지법", "") + branch  # '부산지법'+'서부지원'→'부산서부지원'


_CASE_RE = re.compile(r"(\d{4})\s*타경\s*(\d+)")


def case_label(row: dict) -> str:
    """사건번호 표시용 'YYYY타경NNNN'. title 우선, 없으면 case_no로 구성."""
    m = _CASE_RE.search(str(row.get("title") or ""))
    if m:
        return f"{m.group(1)}타경{m.group(2)}"
    cn = str(row.get("case_no") or "")
    m = re.match(r"(\d{4})-(\d+)", cn)
    return f"{m.group(1)}타경{m.group(2)}" if m else cn


# 주소 첫 토큰 → 표준 시/도. 약어·생략 표기를 통일한다.
_SIDO_CANON = {
    "서울": "서울특별시", "서울시": "서울특별시", "서율특별시": "서울특별시", "서울특별시": "서울특별시",
    "부산": "부산광역시", "부산시": "부산광역시", "부산광역시": "부산광역시",
    "대구": "대구광역시", "대구시": "대구광역시", "대구광역시": "대구광역시",
    "인천": "인천광역시", "인천시": "인천광역시", "인천광역시": "인천광역시",
    "광주": "광주광역시", "광주광역시": "광주광역시",
    "대전": "대전광역시", "대전광역시": "대전광역시",
    "울산": "울산광역시", "울산시": "울산광역시", "울산광역시": "울산광역시",
    "세종": "세종특별자치시", "세종특별자치시": "세종특별자치시",
    "경기": "경기도", "경기도": "경기도",
    "강원": "강원특별자치도", "강원도": "강원특별자치도", "강원특별자치도": "강원특별자치도",
    "충북": "충청북도", "충청북도": "충청북도", "충남": "충청남도", "충청남도": "충청남도",
    "전북": "전북특별자치도", "전북특별자치도": "전북특별자치도", "전라북도": "전북특별자치도",
    "전남": "전라남도", "전라남도": "전라남도",
    "경북": "경상북도", "경상북도": "경상북도", "경남": "경상남도", "경상남도": "경상남도",
    "제주": "제주특별자치도", "제주시": "제주특별자치도", "제주특별자치도": "제주특별자치도",
}
# 도 prefix 생략하고 시부터 시작하는 주소 → (시/도, 그 시를 구·군 레벨로)
_SI_TO_DO = {
    "화성시": "경기도", "양주시": "경기도", "군포시": "경기도", "파주시": "경기도", "광주시": "경기도",
    "전주시": "전북특별자치도", "천안시": "충청남도", "창원시": "경상남도", "진주시": "경상남도",
    "청주시": "충청북도", "경주시": "경상북도", "순천시": "전라남도", "강릉시": "강원특별자치도",
    "제주시": "제주특별자치도",
}


# 표준 시/도 → 주소에 나타나는 모든 표기 변형(누락 없는 시/도 필터용)
_SIDO_VARIANTS: dict = {}
for _k, _v in _SIDO_CANON.items():
    _SIDO_VARIANTS.setdefault(_v, set()).update({_k, _v})
_SIDO_VARIANTS = {k: sorted(v) for k, v in _SIDO_VARIANTS.items()}


def _is_beopjeong_dong(tok: str) -> bool:
    """법정동/읍/면/리(가 포함)인지 — 도로명(로/길)은 제외. '검암동'·'금남로4가'=O, '강서로'·'화곡로18길'=X."""
    if not tok:
        return False
    if re.search(r"(로|길)$", tok):            # 도로명(…로/…길)
        return False
    return bool(re.search(r"(동|읍|면|리|가)$", tok))


def _dong_from_paren(address: str) -> str:
    """도로명주소 괄호의 법정동 추출: '(화곡동)'·'(덕포동,건물명)' → 화곡동. 법정동 없으면(건물명만) ''."""
    m = re.search(r"\(([^)]*)\)", address or "")
    if not m:
        return ""
    for part in m.group(1).split(","):
        p = part.strip()
        if _is_beopjeong_dong(p):
            return p
    return ""


def normalize_address(address: str):
    """주소 → (시도_표준, 구군, 동읍). 표기 불일치를 통일.
    동 슬롯이 도로명(로/길)이면 괄호의 법정동으로 보정, 못 구하면 동='' (드롭다운에서 제외)."""
    toks = (address or "").split()
    if not toks:
        return None, "", ""
    t0 = toks[0]
    if t0 in _SI_TO_DO:                       # '화성시 ...' → 도 보강, 시를 구군으로
        sido, gu, dong_c = _SI_TO_DO[t0], t0, (toks[1] if len(toks) > 1 else "")
    else:
        sido = _SIDO_CANON.get(t0, t0)
        gu = toks[1] if len(toks) > 1 else ""
        dong_c = toks[2] if len(toks) > 2 else ""
    dong = dong_c if _is_beopjeong_dong(dong_c) else _dong_from_paren(address)
    return sido, gu, dong


def mask_name(value: Optional[str]) -> str:
    """개인 실명 마스킹: '지희수'→'지○수', '김민'→'김○'. 기관/빈값은 그대로.

    공백·괄호가 있으면 기관/법인으로 보고 마스킹하지 않는다(보수적).
    """
    if not value:
        return value or ""
    v = value.strip()
    # 기관/법인 추정(금고·은행·공사·회사·캐피탈·새마을 등 또는 공백 포함) → 비마스킹
    if re.search(r"(은행|금고|공사|회사|캐피탈|새마을|보증|조합|대부|자산관리|세무서|구청|시청|국|법인|\s)", v):
        return v
    if len(v) <= 1:
        return "○"
    if len(v) == 2:
        return v[0] + "○"
    return v[0] + "○" * (len(v) - 2) + v[-1]


def _reconciled_result(row: dict) -> Optional[str]:
    """낡은 소스목록 result 교정.
    매물이 소스 활성목록에서 이탈하면 `result`('신건 (100%)' 등)가 갱신을 멈춰 굳는데,
    크롤러는 `data_class`='백데이터'와 `status_reason`('매각 2회 …')은 파생·갱신한다.
    → result가 '신건/유찰'(미매각)인데 백데이터 & status_reason이 매각확정이면 '매각'으로 교정.
    (신건 오표시 중 백데이터분 즉시 정상화 — 크롤러 재수집 의존 X.
     status_reason이 취하/취소/기각 등이면 트리거 안 돼 오교정 방지. 현황 2건 재경매는 크롤러 _relist 과제)"""
    raw = row.get("result")
    r = (raw or "").strip()
    if not (r.startswith("신건") or r.startswith("유찰")):
        return raw
    if row.get("data_class") != "백데이터":
        return raw
    sr = (row.get("status_reason") or "").strip()
    if any(sr.startswith(k) for k in ("매각", "잔금납부", "배당", "재매각")):
        return "매각"
    return raw


class SupabaseSource:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None,
                 r2_url: Optional[str] = None, mask_personal: Optional[bool] = None):
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.environ.get("SUPABASE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")
        self.r2 = (r2_url or os.environ.get("R2_PUBLIC_URL", "")).rstrip("/")
        # 채무자·소유자 실명 마스킹 여부.
        #   기본 = 표시(끔). 경매는 공개공고라 실명 노출이 제도의 본질.
        #   발표 등 공개 자리에서 가리고 싶으면 env MASK_PERSONAL_INFO=1.
        if mask_personal is None:
            mask_personal = os.environ.get("MASK_PERSONAL_INFO", "0") in ("1", "true", "True")
        self.mask_personal = mask_personal
        self._h = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        # keep-alive 연결풀 공유 클라이언트 — 매 쿼리 새 연결(TLS 핸드셰이크 ~0.5초) 제거 → 쿼리 ~0.15초.
        #  httpx.Client는 스레드세이프(병렬 list/count 공유 가능).
        self._client = httpx.Client(
            headers=self._h, timeout=20,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=40,
                                keepalive_expiry=60))
        # 로컬 디스크 캐시(write-behind): Supabase 과부하(크롤러 등) 시 앱이 로컬에서 즉시 응답.
        #   계산결과(cache_save)는 로컬에 즉시 저장(synced=0) → 나중에 flush_localcache.py가 api_cache로 동기화.
        # DISABLE_LOCAL_CACHE=1 → 로컬 버퍼 끔(cache_save가 Supabase 직접 쓰기). 예열 VM에서 N개 프로세스가
        #  같은 _localcache.db에 쓰는 경합을 피하려고 사용(단일 프로세스 앱에선 미설정=버퍼 유지).
        if os.environ.get("DISABLE_LOCAL_CACHE", "0") in ("1", "true", "True"):
            self.local = None
        else:
            try:
                from auction_analysis.local_cache import LocalCache
                self.local = LocalCache()
            except Exception:
                self.local = None

    def _name(self, value: Optional[str]) -> str:
        """마스킹 토글 적용. 끄면 원문 그대로."""
        return mask_name(value) if self.mask_personal else (value or "")

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)

    def _get(self, table: str, params: dict[str, Any], *, count: bool = False) -> httpx.Response:
        headers = {"Prefer": "count=exact"} if count else None   # client에 apikey/Authorization 상주
        return self._client.get(f"{self.url}/rest/v1/{table}", params=params, headers=headers)

    def vehicle_specs(self, item_key: str) -> Optional[dict]:
        """차량외 물건의 vehicle_specs 1행(크롤러 구조화 차량/중기현황). 없으면 None."""
        try:
            r = self._get("vehicle_specs",
                          {"select": "*", "item_key": f"eq.{item_key}", "limit": "1"})
            if r.status_code in (200, 206):
                rows = r.json()
                return rows[0] if rows else None
        except Exception:
            pass
        return None

    # ---------- api_cache: API/문서 계산 결과 영구 저장(1회 계산→DB 재사용). 범용 JSONB ----------
    def cache_get_many(self, cache_keys: list[str]) -> dict[str, dict]:
        """cache_key 목록 → {cache_key: data(dict)}. **로컬 캐시 우선**(Supabase 과부하 무관 즉답),
        없는 키만 Supabase 조회 후 로컬에 캐싱(synced=1). 오류면 가진 만큼 반환(디스크 폴백)."""
        out: dict[str, dict] = {}
        if not cache_keys:
            return out
        if self.local is not None:                       # 1) 로컬 먼저
            try:
                out = self.local.get_many(cache_keys)
            except Exception:
                out = {}
        missing = [k for k in cache_keys if k not in out]
        if not missing:
            return out
        for i in range(0, len(missing), 100):            # 2) 로컬에 없는 것만 Supabase
            chunk = missing[i:i + 100]
            inlist = "(" + ",".join('"' + k + '"' for k in chunk) + ")"
            try:
                r = self._get("api_cache",
                              {"select": "cache_key,data", "cache_key": f"in.{inlist}"})
                if r.status_code != 200:
                    return out
                fetched = []
                for row in r.json():
                    out[row["cache_key"]] = row.get("data")
                    fetched.append((row["cache_key"], row.get("data")))
                if fetched and self.local is not None:
                    try:
                        self.local.put_many(fetched, synced=1)   # Supabase서 온 값 = 읽기캐시
                    except Exception:
                        pass
            except Exception:
                return out
        return out

    def cache_count(self, like_prefix: str) -> int:
        """api_cache에서 'prefix:*' 행 수(예열 진행 표시용). 실패 시 0."""
        try:
            r = self._get("api_cache",
                          {"select": "cache_key", "cache_key": f"like.{like_prefix}:*",
                           "limit": "1"}, count=True)
            return int(r.headers.get("content-range", "*/0").split("/")[-1])
        except Exception:
            return 0

    def cache_save(self, cache_key: str, data: dict) -> bool:
        """**로컬에 즉시 저장(synced=0)** 후 Supabase 베스트에포트 업서트.
        Supabase 빠르면 즉시 동기화(synced=1), 느리면(크롤러 등) 로컬에만 두고 나중에 flush로 동기화."""
        if self.local is not None:                       # 1) 로컬 즉시(절대 안 막힘)
            try:
                self.local.put(cache_key, data, synced=0)
            except Exception:
                pass
        headers = dict(self._h)
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        try:                                             # 2) Supabase 베스트에포트(짧게 — 과부하 시 즉시 포기)
            r = httpx.post(f"{self.url}/rest/v1/api_cache?on_conflict=cache_key",
                           headers=headers, json={"cache_key": cache_key, "data": data},
                           timeout=4)
            if r.status_code in (200, 201, 204):
                if self.local is not None:
                    try:
                        self.local.mark_synced([cache_key])
                    except Exception:
                        pass
                return True
        except Exception:
            pass
        return self.local is not None                    # 로컬엔 저장됨(나중에 flush)

    def cache_delete_many(self, cache_keys: list[str]) -> int:
        """api_cache에서 cache_key 목록 삭제(갱신 무효화). 삭제 시도 수 반환."""
        if not cache_keys:
            return 0
        headers = dict(self._h)
        headers["Prefer"] = "return=minimal"
        n = 0
        for i in range(0, len(cache_keys), 80):
            chunk = cache_keys[i:i + 80]
            inlist = "(" + ",".join('"' + k.replace('"', '') + '"' for k in chunk) + ")"
            try:
                r = httpx.delete(f"{self.url}/rest/v1/api_cache",
                                 params={"cache_key": f"in.{inlist}"}, headers=headers, timeout=20)
                if r.status_code in (200, 204):
                    n += len(chunk)
            except Exception:
                pass
        return n

    def items_updated_since(self, ts: str, limit: int = 3000):
        """updated_at > ts 인 물건의 item_key 목록 + 최신 updated_at. 크롤러 갱신 감지용."""
        keys, newest, off = [], ts, 0
        while off < limit:
            try:
                r = self._get("items", {"select": "item_key,updated_at",
                                        "updated_at": f"gt.{ts}", "order": "updated_at.asc",
                                        "limit": "1000", "offset": str(off)})
                rows = r.json() if r.status_code in (200, 206) else []
            except Exception:
                rows = []
            if not rows:
                break
            keys += [x["item_key"] for x in rows]
            newest = rows[-1].get("updated_at") or newest
            if len(rows) < 1000:
                break
            off += 1000
        return keys, newest

    def max_updated_at(self) -> str:
        """현재 items의 최신 updated_at(없으면 빈 문자열)."""
        try:
            r = self._get("items", {"select": "updated_at", "order": "updated_at.desc", "limit": "1"})
            rows = r.json() if r.status_code in (200, 206) else []
            return rows[0]["updated_at"] if rows else ""
        except Exception:
            return ""

    def count(self, data_class: str = "현황") -> int:
        """연결·권한 점검용 건수 조회."""
        r = self._get("items", {"select": "item_key", "data_class": f"eq.{data_class}",
                                 "limit": "1"}, count=True)
        r.raise_for_status()
        cr = r.headers.get("content-range", "*/0")
        try:
            return int(cr.split("/")[-1])
        except (ValueError, IndexError):
            return 0

    _SORTS = {
        "사건번호": "case_sort.asc.nullslast,item_key.asc",   # 연도×10^9+번호(트리거 유지) 숫자순 + item_key 유니크 tiebreak(안정 페이지네이션)
        "매각기일": "sell_date_d.asc.nullslast",   # sell_date는 '날짜+낙찰자/입찰인원' 오염 텍스트 → 파싱 date컬럼(트리거 유지)으로 정렬해야 날짜 그룹핑 정확
        "감정가높은": "appraisal_price.desc.nullslast",
        "감정가낮은": "appraisal_price.asc.nullslast",
        "최저가높은": "min_price.desc.nullslast",
        "최저가낮은": "min_price.asc.nullslast",
        "유찰많은": "fail_count.desc.nullslast",
    }

    def _filters(self, *, group=None, usages=None, keyword=None, data_class="현황",
                 region=None, regions=None, sido=None, year=None, caseno=None, court=None, court_code=None,
                 result_prefix=None, special=None, item_keys=None,
                 appraisal_min=None, appraisal_max=None, price_min=None, price_max=None,
                 fail_min=None, fail_max=None, barea_min=None, barea_max=None,
                 sell_from=None, sell_to=None, buy_grade=None) -> list[tuple]:
        """PostgREST 필터를 (key,value) 튜플 리스트로. 같은 컬럼 범위(gte+lte) 지원."""
        if caseno:                     # 특정 사건번호 검색 = 상태·매각기일 무관하게 그 물건을 찾는다
            result_prefix = None       #  프론트가 기본 status=진행물건 + 매각기일범위(오늘~+3개월)를 항상 붙이는데,
            sell_from = sell_to = None #   과거 매각물건은 그 둘에 다 걸려 안 나왔음 → 사건번호는 특정 물건이니 필터 무시
        if (result_prefix in _PAST_PREFIXES or sell_from or sell_to or caseno) and data_class == "현황":
            # 매각·종결(과거) 상태 OR 매각기일 날짜범위 OR **사건번호 검색** 시 백데이터(과거 매각분)도 함께 노출
            #  ('전체'로 특정 날짜 조회 시 그날 매각된 물건이 백데이터라 빠지던 버그 + 사건번호로 검색해도
            #   매각완료 물건(백데이터)이 안 나오던 버그 픽스 — 특정 사건번호는 상태 무관하게 찾아야 함)
            f: list[tuple] = []   # 전 행이 현황∪백데이터(그외 0건)라 in.(현황,백데이터)는 100%매치=무의미인데 12만행 IN평가로 3배 느림 → 필터 생략(결과 동일·caseno/과거검색 속도개선)
        else:
            f: list[tuple] = [("data_class", f"eq.{data_class}")]
        if buy_grade:                          # 매수판정 컬럼 직접필터(빠름; buy_grade 컬럼 존재 시 main이 전달)
            if isinstance(buy_grade, (list, tuple, set)):
                q = ",".join('"' + str(g).replace('"', '') + '"' for g in buy_grade)
                f.append(("buy_grade", f"in.({q})"))
            else:
                f.append(("buy_grade", f"eq.{buy_grade}"))
        if special:                            # 특수물건: tags 부분일치(여러개=AND), '제외' 라벨은 NOT
            for s in special:
                s = (s or "").strip()
                if not s:
                    continue
                if "제외" in s:
                    base = re.sub(r"\s*제외.*$", "", s).strip()
                    if base:
                        f.append(("tags", f"not.ilike.*{base}*"))
                else:
                    f.append(("tags", f"ilike.*{s}*"))
        if item_keys is not None:               # 유형별 필터: 사전 산출된 item_key 집합으로 제한(빈 집합=0건)
            ks = list(item_keys) or ["__none__"]
            q = ",".join('"' + str(k).replace('"', '') + '"' for k in ks)
            f.append(("item_key", f"in.({q})"))
        if group:
            if isinstance(group, (list, tuple, set)):
                q = ",".join('"' + str(g).replace('"', '') + '"' for g in group)
                f.append(("search_group", f"in.({q})"))
            else:
                f.append(("search_group", f"eq.{group}"))
        if usages:
            quoted = ",".join('"' + u.replace('"', '') + '"' for u in usages)
            f.append(("usage_name", f"in.({quoted})"))
        if keyword:
            f.append(("address", f"ilike.*{keyword}*"))
        if region:
            # "구군 동" 토큰을 각각 AND(비연속) → 도로명주소("강서구 강서로 … (화곡동)")도 동으로 조회됨.
            #  기존 연속매칭("강서구 화곡동")은 지번주소만 잡고 도로명주소는 누락했음.
            for _rp in region.split():
                f.append(("address", f"ilike.*{_rp}*"))
        if regions:
            # 소재지 여러 개(+버튼) → 지역끼리 OR, 각 지역 안 토큰은 AND.
            #  예: (대구 AND 달서구) OR (부산 AND 해운대구)  →  or=(and(...),and(...))
            _rgroups = []
            for _reg in (regions if isinstance(regions, (list, tuple)) else [regions]):
                _toks = [t for t in str(_reg).split() if t]
                if not _toks:
                    continue
                if len(_toks) == 1:
                    _rgroups.append(f"address.ilike.*{_toks[0]}*")
                else:
                    _inner = ",".join(f"address.ilike.*{t}*" for t in _toks)
                    _rgroups.append(f"and({_inner})")
            if _rgroups:
                f.append(("or", f"({','.join(_rgroups)})"))
        if sido:
            # 시/도만 선택 시 표기 변형(충남|충청남도 등)을 OR로 모두 매칭 → 누락 방지.
            #  시/도는 주소 맨 앞에 오므로 '전방일치'(대구*)로 매칭 — '%대구%'면 부산 '해운대구'에 오매칭됨.
            variants = _SIDO_VARIANTS.get(sido, [sido])
            ors = ",".join(f"address.ilike.{v}*" for v in variants)
            f.append(("or", f"({ors})"))
        if caseno:
            # 타경번호 검색: case_no 는 'YYYY-NNNNNN' 형식. 연도 있으면 정확 일치, 없으면 끝자리 매칭.
            cs = str(caseno).strip()
            _m = re.match(r"^\s*(\d{4})\s*(?:타경|-)\s*(\d+)\s*$", cs)   # '2024-3113' 또는 '2024타경3113'(사용자가 통째로 입력) → 연도+번호 분리
            if _m:                                  #  (프론트/사용자가 '2025타경100006' 통째로 보내도 0건 안 되게 — 숫자만 추출하면 20252100006로 깨짐)
                f.append(("case_no", f"eq.{_m.group(1)}-{_m.group(2)}"))
            else:
                cno = re.sub(r"[^0-9]", "", cs)
                if cno:
                    if year:
                        f.append(("case_no", f"eq.{year}-{cno}"))
                    else:
                        f.append(("case_no", f"like.*-{cno}"))
        elif year:
            f.append(("case_no", f"like.{year}-*"))
        if court:
            f.append(("court_name", f"eq.{court}"))
        if court_code:
            if isinstance(court_code, (list, tuple, set)):
                q = ",".join('"' + str(c) + '"' for c in court_code)
                f.append(("court_code", f"in.({q})"))
            else:
                f.append(("court_code", f"eq.{court_code}"))
        if result_prefix:
            grp = _STATUS_GROUPS.get(result_prefix)
            if grp:                            # 그룹옵션(진행물건/종결물건 등) → 여러 상태 OR 매칭
                ors = ",".join(f"result.like.{s}*" for s in grp)
                f.append(("or", f"({ors})"))
            else:
                f.append(("result", f"like.{result_prefix}*"))
        if appraisal_min is not None:
            f.append(("appraisal_price", f"gte.{appraisal_min}"))
        if appraisal_max is not None:
            f.append(("appraisal_price", f"lte.{appraisal_max}"))
        if price_min is not None:
            f.append(("min_price", f"gte.{price_min}"))
        if price_max is not None:
            f.append(("min_price", f"lte.{price_max}"))
        if fail_min is not None:
            f.append(("fail_count", f"gte.{fail_min}"))
        if fail_max is not None:
            f.append(("fail_count", f"lte.{fail_max}"))
        if barea_min is not None:
            f.append(("building_area", f"gte.{barea_min}"))
        if barea_max is not None:
            f.append(("building_area", f"lte.{barea_max}"))
        # 매각기일 범위(sell_date는 'YYYY-MM-DD (…)' 텍스트 → 문자열 비교).
        #   상한은 해당 날짜 종일 포함 위해 '~'(공백보다 큰 문자) 부가.
        if sell_from:
            f.append(("sell_date", f"gte.{sell_from}"))
        if sell_to:
            f.append(("sell_date", f"lte.{sell_to}~"))
        # ⚠️ PostgREST는 root-level or= 파라미터를 '하나만' 허용. or 그룹이 2개↑(예: 복수소재지 regions +
        #  진행물건 status, 또는 시/도 sido + status)면 or=X&or=Y가 되어 422로 실패한다.
        #  → and=(or(X),or(Y)) 로 묶어 각 or 그룹을 AND로 결합(= 지역들 중 하나 그리고 진행물건).
        _ors = [v for (k, v) in f if k == "or"]
        if len(_ors) >= 2:
            f = [(k, v) for (k, v) in f if k != "or"]
            f.append(("and", "(" + ",".join("or" + v for v in _ors) + ")"))
        return f

    def _count_one(self, **kw) -> int:
        params = [("select", "item_key"), ("limit", "1")] + self._filters(**kw)
        last = None
        import time as _t
        for i in range(3):                       # count=exact 타임아웃·동시연결거부(WinError 10061) 재시도
            try:
                r = self._get("items", params, count=True)
                r.raise_for_status()
                return int(r.headers.get("content-range", "*/0").split("/")[-1])
            except Exception as e:
                last = e
                _t.sleep(0.4 * (i + 1))          # 지연 후 재시도(연결거부 회복 여유)
        raise last if last else RuntimeError("count failed")

    def count_filtered(self, **kw) -> int:
        # item_keys 집합이 크면(예: 매수판정 필터) IN 리스트가 URL 한계를 넘으므로 청크 분할 후 합산(키 디스조인트).
        iks = kw.get("item_keys")
        if iks is not None and len(iks) > 250:
            ks = list(iks)
            chunks = [ks[i:i + 250] for i in range(0, len(ks), 250)]   # 250: count=exact 안정(pipe키 URL·타임아웃 여유)
            #  순차 처리(uvicorn 스레드풀 + endpoint 병렬 + 청크 병렬의 3중 중첩 회피 → 서버에서 0 반환 버그 방지)
            return sum(self._count_one(**{**kw, "item_keys": c}) for c in chunks)
        return self._count_one(**kw)

    @staticmethod
    def _chunk_sort(rows: list, order: str) -> list:
        """청크 병합 결과를 PostgREST order(첫 필드 기준)로 재정렬(nulls last)."""
        p = order.split(",")[0].split(".")
        field = p[0]
        desc = len(p) > 1 and p[1] == "desc"
        nonnull = [r for r in rows if r.get(field) is not None]
        nulls = [r for r in rows if r.get(field) is None]
        nonnull.sort(key=lambda r: r.get(field), reverse=desc)
        return nonnull + nulls

    def group_counts(self, data_class: str = "현황") -> dict[str, int]:
        """그룹별 건수(필터 UI용)."""
        return {g: self.count_filtered(group=g, data_class=data_class)
                for g in ("주거용", "상가", "차량외")}

    # 물건통계 상태 표시 순서
    STATUS_ORDER = ["신건", "유찰", "매각", "재진행", "재매각", "변경",
                    "취하", "정지", "미진행", "배당종결", "기각", "잔금납부", "각하"]

    def status_stats(self, **kw) -> dict[str, int]:
        """현재 필터 기준 상태별 건수(전체 + 존재하는 상태만). 카운트 쿼리 병렬 실행."""
        from concurrent.futures import ThreadPoolExecutor
        kw.pop("result_prefix", None)   # 상태필터는 제외하고 전 상태 집계
        # 결과가 크지 않으면(≤1000건) 상태별 15개 카운트쿼리(~1s) 대신 1회 조회 후 파이썬 집계(~0.3s).
        #  count=exact로 총건수를 함께 받아 '전량 확보'됐을 때만 집계(캡 초과 시 아래 카운트쿼리 폴백). 전상태 (1) 뜨던 것도 해소.
        try:
            params = [("select", "result,status_reason,data_class"), ("limit", "1000")] + self._filters(**kw)
            r = self._get("items", params, count=True)
            if r.status_code in (200, 206):
                rows = r.json()
                total = int(r.headers.get("content-range", "*/0").split("/")[-1])
                if total <= len(rows):        # 전량 확보(캡 안 넘음) → 정확 집계
                    out: dict[str, int] = {"전체": total}
                    for x in rows:
                        res = (_reconciled_result(x) or "").strip()   # 목록과 동일 교정(백데이터 매각을 신건으로 오집계 방지)
                        for st in self.STATUS_ORDER:
                            if res.startswith(st):
                                out[st] = out.get(st, 0) + 1
                                break
                    return out
        except Exception:
            pass

        def one(st):
            try:
                if st == "전체":
                    return st, self.count_filtered(**kw)
                return st, self.count_filtered(result_prefix=st, **kw)
            except Exception:
                return st, 0     # 카운트 1개 일시 실패(Supabase 500 등)해도 전체 통계 반환(500 방지·캐시/예열 안정화)

        tasks = ["전체"] + self.STATUS_ORDER
        with ThreadPoolExecutor(max_workers=3) as ex:   # 동시 Supabase 연결 과다→거부(WinError 10061) 방지
            results = list(ex.map(one, tasks))
        return {st: c for st, c in results if st == "전체" or c}

    def region_facets(self, data_class: str = "현황") -> dict:
        """전체 물건의 주소를 파싱해 시/도→구·군→동·읍 트리 + 연도 + 법원(계) 목록.

        주소 토큰(시도/구군/동읍)을 그대로 사용한다. 전체를 페이지네이션해 1회 집계.
        """
        tree: dict = {}
        years: set = set()
        codes: set = set()
        offset = 0
        while True:
            params = [("select", "address,case_no,court_code"),
                      ("data_class", f"eq.{data_class}"),
                      ("limit", "1000"), ("offset", str(offset))]
            rows = self._get("items", params).json()
            if not rows:
                break
            for row in rows:
                cn = row.get("case_no") or ""
                if len(cn) >= 4 and cn[:4].isdigit():
                    years.add(cn[:4])
                if row.get("court_code"):
                    codes.add(row["court_code"])
                sido, gu, dong = normalize_address(row.get("address") or "")
                if not sido:
                    continue
                gd = tree.setdefault(sido, {})
                if gu:
                    dl = gd.setdefault(gu, set())
                    if dong:
                        dl.add(dong)
            if len(rows) < 1000:
                break
            offset += 1000
        dong_tree = {s: {g: sorted(dl) for g, dl in gd.items()} for s, gd in tree.items()}
        # 법원코드 → 지법 → {지원명: code}. 미매핑(비정상) 코드는 제외(기타 숨김).
        raw: dict = {}
        for code in codes:
            if code not in COURT_CODE_MAP:
                continue
            jibeop, branch = COURT_CODE_MAP[code]
            raw.setdefault(jibeop, {})[branch] = code
        # 정해진 순서로 정렬(목록에 없는 지법은 뒤에 가나다)
        court_tree = {j: raw[j] for j in _JIBEOP_ORDER if j in raw}
        for j in sorted(raw):
            if j not in court_tree:
                court_tree[j] = raw[j]
        return {
            # 시/도: 짧은 이름 + 표준키(순서 유지). 데이터 없는 시도도 포함(전국 목록)
            "sido": [{"short": _SIDO_SHORT.get(s, s), "canon": s}
                     for s in _SIDO_ORDER if s in SIGUNGU_KR],
            "sigungu": SIGUNGU_KR,        # 전국 시/군/구 전체
            "dong": dong_tree,            # 우리 데이터의 동/읍 (시도→구군→[동])
            "years": sorted(years, reverse=True),
            "courts": court_tree,
        }

    def usages_in_group(self, group: str, data_class: str = "현황") -> list[str]:
        """그룹 내 실제 존재하는 용도 목록(체크박스용). 샘플 기반 distinct."""
        params = [("select", "usage_name"), ("search_group", f"eq.{group}"),
                  ("data_class", f"eq.{data_class}"), ("limit", "1000")]
        r = self._get("items", params)
        r.raise_for_status()
        seen = []
        for row in r.json():
            u = row.get("usage_name")
            if u and u not in seen:
                seen.append(u)
        return sorted(seen)

    # ---- 목록/검색 ----
    def list_auctions(self, *, limit: int = 20, offset: int = 0,
                      sort: str = "매각기일", sort2: Optional[str] = None, **kw) -> list[dict]:
        order = self._SORTS.get(sort, self._SORTS["매각기일"])
        if sort2 and sort2 in self._SORTS and sort2 != sort:
            order += "," + self._SORTS[sort2]
        cols = ("item_key,case_no,obj_no,court_name,usage_name,search_group,address,"
                "area_text,land_area,building_area,tags,appraisal_price,min_price,"
                "sale_price,sale_rate,fail_count,sell_date,result,status_reason,"
                "bid_count,sale_2nd_price,hit_count,thumb_url,buy_grade,data_class")
        iks = kw.get("item_keys")
        if iks is not None and len(iks) > 600:
            # 큰 item_keys 집합 → 청크별 상위(offset+limit) 조회 후 병합·정렬·슬라이스(분산 top-k).
            ks = list(iks)
            need = offset + limit
            chunks = [ks[i:i + 600] for i in range(0, len(ks), 600)]

            def _fetch(c):
                base = [("select", cols), ("order", order), ("limit", str(need)), ("offset", "0")]
                r = self._get("items", base + self._filters(**{**kw, "item_keys": c}))
                r.raise_for_status()
                return r.json()
            from concurrent.futures import ThreadPoolExecutor
            merged: list[dict] = []
            with ThreadPoolExecutor(max_workers=8) as ex:   # 청크 조회 병렬(순차 ~5초→~0.6초)
                for rows in ex.map(_fetch, chunks):
                    merged += rows
            page = self._chunk_sort(merged, order)[offset:offset + limit]
            return self._attach_winners([self._summary(row) for row in page])
        base = [
            ("select", cols),
            ("order", order),
            ("limit", str(limit)), ("offset", str(offset)),
        ]
        r = self._get("items", base + self._filters(**kw))
        r.raise_for_status()
        return self._attach_winners([self._summary(row) for row in r.json()])

    def _attach_winners(self, rows: list) -> list:
        """매각된 행에 낙찰가·낙찰자·입찰수·2등가를 auction_schedule(매각 회차)에서 직접 붙인다.
        items 컬럼(sale_price 등) 백필이 늦은 최근 매각건도 **즉시 노출** — 크롤러 백필 의존 X.
        (auction_schedule은 크롤러가 수집하며 바로 채우므로, items 복사 지연/멈춤과 무관하게 항상 뜸)
        재매각(미납)은 **최종 낙찰가와 일치하는 회차**(=지금 낙찰받은 사람), 없으면 가장 최근 매각회차.
        (목록=최종 낙찰자만, 전 회차 이력은 상세 '기일현황'에서 회차별로 봄)"""
        def _sold(r):
            if r.get("sale_price"):
                return True
            res = r.get("result") or ""
            return ("재매각" not in res) and any(k in res for k in ("매각", "잔금납부", "배당종결"))
        keys = [r["item_key"] for r in rows if _sold(r)]   # 매각된 행(백필 안 된 것 포함)
        if not keys:
            return rows
        smap: dict = {}   # item_key → [매각 회차row, ...] (오래된→최신)
        for i in range(0, len(keys), 200):
            q = ",".join('"' + k + '"' for k in keys[i:i + 200])
            try:
                rr = self._get("auction_schedule",
                               [("select", "item_key,sale_price,bid_count,sale_2nd_price,winner_name"),
                                ("item_key", f"in.({q})"),
                                ("result", "like.매각*"),
                                ("order", "id.asc")])          # 회차 순(오래된→최신)
                for s in (rr.json() if rr.status_code in (200, 206) else []):
                    smap.setdefault(s["item_key"], []).append(s)
            except Exception:
                pass
        for r in rows:
            cand = smap.get(r["item_key"])
            if not cand:
                continue
            sp = r.get("sale_price")
            match = [c for c in cand if c.get("sale_price") == sp] if sp else []
            c = match[-1] if match else cand[-1]               # 최종 낙찰가 일치 회차, 없으면 최근 매각회차
            if not r.get("sale_price"):
                r["sale_price"] = c.get("sale_price")          # items 백필 안 됐으면 schedule 값으로
            if r.get("bid_count") is None:
                r["bid_count"] = c.get("bid_count")
            if r.get("sale_2nd_price") is None:
                r["sale_2nd_price"] = c.get("sale_2nd_price")
            if c.get("winner_name"):
                r["winner_name"] = self._name(c["winner_name"])   # 개인정보 마스킹 토글(기관명 자동 유지)
        # auction_schedule가 비어(크롤러 미수집) 낙찰자·입찰수를 못 채운 매각건:
        #  sell_date 텍스트('YYYY-MM-DD N 명 낙찰자')에서 복구. (낙찰가 금액은 텍스트에 없음 → 정확값은 크롤러 재수집 필요)
        for r in rows:
            if not _sold(r) or (r.get("winner_name") and r.get("bid_count") is not None):
                continue
            m = re.match(r"^\s*\d{4}-\d{2}-\d{2}\s+(\d+)\s*명\s+(.+?)\s*$", r.get("sell_date") or "")
            if not m:
                continue
            if r.get("bid_count") is None:
                try:
                    r["bid_count"] = int(m.group(1))
                except ValueError:
                    pass
            if not r.get("winner_name"):
                nm = m.group(2).strip()
                if nm:
                    r["winner_name"] = self._name(nm)
        return rows

    def filtered_item_keys(self, **kw) -> list:
        """필터에 맞는 전체 item_key 목록(정렬 없이). 유사거래 전역 정렬(서버)용 — 메모리 캐시로 sort 후 페이지."""
        keys, off = [], 0
        while True:
            r = self._get("items", [("select", "item_key"), ("limit", "1000"),
                                    ("offset", str(off))] + self._filters(**kw))
            page = r.json() if r.status_code in (200, 206) else []
            keys += [x.get("item_key") for x in page if x.get("item_key")]
            if len(page) < 1000:
                break
            off += 1000
        return keys

    def media_url(self, item_key: str, kind: str) -> Optional[str]:
        """특정 물건의 특정 종류 문서(등기 등) R2 공개 URL(첫 건)."""
        r = self._get("media", [("select", "r2_key"), ("item_key", f"eq.{item_key}"),
                                ("kind", f"eq.{kind}"), ("limit", "1")])
        rows = r.json()
        if rows and self.r2 and rows[0].get("r2_key"):
            return f"{self.r2}/{rows[0]['r2_key']}"
        return None

    def summaries_by_keys(self, keys: list[str]) -> list[dict]:
        """item_key 목록 → 요약 목록(관심물건용). 저장 순서 보존."""
        if not keys:
            return []
        inlist = ",".join('"' + k.replace('"', "") + '"' for k in keys)
        r = self._get("items", {"select": "*", "item_key": f"in.({inlist})",
                                 "limit": str(len(keys))})
        rows = r.json() if r.status_code == 200 else []
        by = {row.get("item_key"): row for row in rows}
        out: list[dict] = []
        for k in keys:                       # 저장(관심추가) 순서 유지
            row = by.get(k)
            if not row:
                continue
            d = self._summary(row)
            d["court_name"] = court_display_name(row.get("court_code"))
            d["case_label"] = case_label(row)
            d["appraisal_raw"] = row.get("appraisal_raw")
            d["min_price_raw"] = row.get("min_price_raw")
            out.append(d)
        return out

    def get_auction(self, item_key: str) -> Optional[dict]:
        """**로컬 우선(TTL 5분)** → 신선하면 즉답, 아니면 Supabase. Supabase 타임아웃 시 stale 로컬 폴백.
        (물건 데이터는 'item:'키로 로컬 캐싱 synced=1 = flush 대상 아님)"""
        lk = "item:" + item_key
        val, age = (None, None)
        if self.local is not None:
            try:
                val, age = self.local.get_entry(lk)
            except Exception:
                val, age = (None, None)
        if val is not None and age is not None and age < 300:    # 5분 신선 → 로컬 즉답
            return val
        try:
            item = self._get_auction_remote(item_key)
        except Exception:
            if val is not None:
                return val                                        # Supabase 지연 → stale 로컬 폴백
            raise                                                 # 로컬도 없음 → 호출부가 pending 처리하도록 재던짐
        if item and self.local is not None:
            try:
                self.local.put(lk, item, synced=1)
            except Exception:
                pass
        return item if item is not None else val

    def _get_auction_remote(self, item_key: str) -> Optional[dict]:
        import concurrent.futures as _cf
        # items·schedule·media 모두 item_key만 필요 → 3쿼리 병렬(순차 ~1.3초 → ~0.45초)
        with _cf.ThreadPoolExecutor(max_workers=3) as _ex:
            f_item = _ex.submit(self._get, "items",
                                {"select": "*", "item_key": f"eq.{item_key}", "limit": "1"})
            f_sched = _ex.submit(self._get, "auction_schedule",
                                 {"select": ("round,sell_date,min_price,result,"
                                             "sale_price,sale_rate,bid_count,sale_2nd_price,winner_name"),
                                  "item_key": f"eq.{item_key}", "order": "round.asc"})
            f_media = _ex.submit(self._get, "media",
                                 {"select": "kind,seq,r2_key,content_type",
                                  "item_key": f"eq.{item_key}", "order": "kind.asc,seq.asc"})
            r = f_item.result()
            r.raise_for_status()
            rows = r.json()
            if not rows:
                return None
            item = self._detail(rows[0])

            sched = f_sched.result()
            sched_rows = sched.json() if sched.status_code == 200 else []
            for s in sched_rows:                       # 낙찰자명도 개인정보 → 마스킹 토글 적용
                if s.get("winner_name"):
                    s["winner_name"] = self._name(s["winner_name"])
            item["schedule"] = sched_rows

            media = f_media.result()
            items_media = media.json() if media.status_code == 200 else []
            for m in items_media:
                m["url"] = f"{self.r2}/{m['r2_key']}" if self.r2 and m.get("r2_key") else None
            item["media"] = items_media
        return item

    # ---- 매핑(+개인정보 마스킹) ----
    def _summary(self, row: dict) -> dict:
        return {
            "item_key": row.get("item_key"),
            "case_no": row.get("case_no"),
            "obj_no": row.get("obj_no"),
            "court": row.get("court_name"),
            "usage": row.get("usage_name"),
            "group": row.get("search_group"),
            "address": row.get("address"),
            "area_text": row.get("area_text"),
            "land_area": row.get("land_area"),
            "building_area": row.get("building_area"),
            "tags": row.get("tags"),
            "appraisal_price": row.get("appraisal_price"),
            "min_price": row.get("min_price"),
            "sale_price": row.get("sale_price"),
            "sale_rate": row.get("sale_rate"),
            "fail_count": row.get("fail_count"),
            "sell_date": row.get("sell_date"),
            "result": _reconciled_result(row),   # 낡은 소스 result('신건') vs 파생 status_reason('매각') 모순 교정
            "status": row.get("status_reason"),
            "bid_count": row.get("bid_count"),
            "sale_2nd_price": row.get("sale_2nd_price"),   # 2등가(목록 표시; items 컬럼)
            "winner_name": None,                            # 낙찰자(_attach_winners가 매각건만 채움)
            "hit_count": row.get("hit_count"),
            "thumb_url": row.get("thumb_url"),
            "buy_grade": row.get("buy_grade"),   # 매수판정 컬럼 — 클라우드는 이 값으로 배지(로컬은 in-메모리 버킷 우선)
        }

    def _detail(self, row: dict) -> dict:
        d = self._summary(row)
        # 헤더 표시용: 법원명(코드 매핑) / 경매계(DB court_name) / 사건번호 라벨
        d.update({
            "court_name": court_display_name(row.get("court_code")),  # 예: 부산서부지원
            "court_division": row.get("court_name"),                   # 예: 경매1계
            "court_code": row.get("court_code"),
            "case_label": case_label(row),                             # 예: 2025타경1086
        })
        # 승인서 제외항목: 개인정보(실명) 마스킹
        d.update({
            "creditor": self._name(row.get("creditor")),     # 토글 적용(기관명은 헬퍼가 자동 유지)
            "debtor": self._name(row.get("debtor")),
            "owner": self._name(row.get("owner")),
            "deposit": row.get("deposit"),
            "claim_amount": row.get("claim_amount"),
            "case_received": row.get("case_received"),
            "dividend_deadline": row.get("dividend_deadline"),
            "decision_date": row.get("decision_date"),
            "sale_target": row.get("sale_target"),
            "area_text": row.get("area_text"),
            "appraisal_raw": row.get("appraisal_raw"),
            "min_price_raw": row.get("min_price_raw"),
            "bid_count": row.get("bid_count"),
            "sale_2nd_price": row.get("sale_2nd_price"),
            "data_class": row.get("data_class"),
        })
        return d
