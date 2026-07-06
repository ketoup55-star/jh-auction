"""
국토부 연립다세대 매매 실거래가 OpenAPI 연동 (data.go.kr).

  Endpoint: https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade
  params  : serviceKey, LAWD_CD(시군구 5자리), DEAL_YMD(YYYYMM), pageNo, numOfRows
  검증된 응답 필드(2026-06): buildYear, dealAmount(만원,콤마), dealYear/Month/Day,
    excluUseAr(전용면적㎡), floor, houseType(연립/다세대), jibun, landAr,
    mhouseNm(건물명), sggCd(시군구코드), umdNm(법정동)

용도가 다세대(빌라)·도시형생활주택인 경매물건의 주변 유사 실거래 집계에 사용.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import date
import concurrent.futures as _cf

import httpx

_OP = "https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade"
_OP_APT = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"  # 운영계정(상세자료, 필드 동일)
_OP_SHRENT = "https://apis.data.go.kr/1613000/RTMSDataSvcSHRent/getRTMSDataSvcSHRent"        # 단독·다가구 전월세
_UA = {"User-Agent": "Mozilla/5.0"}

# 공유 httpx 클라이언트 — 매 요청 새 SSL 컨텍스트(CA 번들 load_verify_locations ~0.7s/회) 폭증 방지.
# 아파트 한 건 예열에 실거래 호출이 최대 12개월×4페이지=48회라, 새 연결마다 SSL 로드면 ~16초/건이었음.
# 공유 클라이언트는 SSL 컨텍스트 1회 생성 + keep-alive 커넥션 재사용(httpx.Client는 스레드 안전).
_CLIENT = httpx.Client(headers=_UA, timeout=30,
                       transport=httpx.HTTPTransport(retries=2,
                           limits=httpx.Limits(max_keepalive_connections=20, max_connections=40)))
_QUOTA_HIT = False   # 일일 할당량(QUOTA) 초과 감지 시 True → 이후 _recent는 즉시 중단(불필요 호출 방지)


def _t(el, tag: str) -> str:
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""


class MolitSource:
    def __init__(self, key: str | None = None):
        self.key = key or os.environ.get("ONBID_SERVICE_KEY", "")  # data.go.kr 공통 키

    def _month(self, lawd_cd: str, ymd: str) -> tuple[list[dict], str | None]:
        try:
            r = _CLIENT.get(_OP, params={"serviceKey": self.key, "LAWD_CD": lawd_cd,
                                         "DEAL_YMD": ymd, "numOfRows": "1000", "pageNo": "1"})
            if r.status_code == 429 or "quota exceeded" in r.text[:120].lower():
                return [], "QUOTA"                    # 일일 할당량 초과 → 즉시 중단(재시도 무의미)
            root = ET.fromstring(r.text)
        except Exception as e:
            return [], f"국토부 실거래 호출 실패: {type(e).__name__}"
        code = root.findtext(".//resultCode")
        if code not in ("000", "00"):
            return [], f"국토부 실거래 오류(코드 {code}): {root.findtext('.//resultMsg')}"
        out = []
        for it in root.findall(".//item"):
            amt = re.sub(r"[^0-9]", "", _t(it, "dealAmount"))
            out.append({
                "amount": int(amt) * 10000 if amt else 0,     # 만원 → 원
                "area": float(_t(it, "excluUseAr") or 0),       # 전용면적 ㎡
                "land_area": float(_t(it, "landAr") or 0),
                "house_type": _t(it, "houseType"),              # 연립/다세대
                "build_year": _t(it, "buildYear"),
                "floor": _t(it, "floor"),
                "umd": _t(it, "umdNm"),                          # 법정동
                "jibun": _t(it, "jibun"),
                "name": _t(it, "mhouseNm"),                      # 건물명
                "deal_date": f"{_t(it,'dealYear')}-{int(_t(it,'dealMonth') or 0):02d}-{int(_t(it,'dealDay') or 0):02d}",
                "sgg_cd": _t(it, "sggCd"),
            })
        return out, None

    def recent_trades(self, lawd_cd: str, months: int = 12,
                      base: date | None = None) -> dict:
        """시군구(lawd_cd) 최근 months개월 연립다세대 매매 실거래."""
        return self._recent(self._month, lawd_cd, months, base)

    # ---------- 아파트 매매 실거래 ----------
    def _apt_month(self, lawd_cd: str, ymd: str) -> tuple[list[dict], str | None]:
        """한 달치 아파트 실거래. 거래 많은 시군구(>1000건)는 페이지네이션(최대 4페이지)."""
        out: list[dict] = []
        for page in range(1, 5):
            try:
                r = _CLIENT.get(_OP_APT, params={"serviceKey": self.key, "LAWD_CD": lawd_cd,
                                                 "DEAL_YMD": ymd, "numOfRows": "1000",
                                                 "pageNo": str(page)})
                if r.status_code == 429 or "quota exceeded" in r.text[:120].lower():
                    return out, "QUOTA"
                root = ET.fromstring(r.text)
            except Exception as e:
                return out, f"국토부 아파트 실거래 호출 실패: {type(e).__name__}"
            code = root.findtext(".//resultCode")
            if code not in ("000", "00"):
                return out, f"국토부 아파트 실거래 오류(코드 {code}): {root.findtext('.//resultMsg')}"
            items = root.findall(".//item")
            for it in items:
                amt = re.sub(r"[^0-9]", "", _t(it, "dealAmount"))
                out.append({
                    "amount": int(amt) * 10000 if amt else 0,     # 만원 → 원
                    "area": float(_t(it, "excluUseAr") or 0),       # 전용면적 ㎡
                    "build_year": _t(it, "buildYear"),
                    "floor": _t(it, "floor"),
                    "umd": _t(it, "umdNm"),                          # 법정동
                    "jibun": _t(it, "jibun"),
                    "name": _t(it, "aptNm"),                         # 단지명
                    "dong": _t(it, "aptDong"),
                    "deal_date": f"{_t(it,'dealYear')}-{int(_t(it,'dealMonth') or 0):02d}-{int(_t(it,'dealDay') or 0):02d}",
                    "sgg_cd": _t(it, "sggCd"),
                })
            total = int(root.findtext(".//totalCount") or 0)
            if page * 1000 >= total or len(items) < 1000:
                break
        return out, None

    def apt_recent_trades(self, lawd_cd: str, months: int = 12,
                          base: date | None = None) -> dict:
        """시군구(lawd_cd) 최근 months개월 아파트 매매 실거래."""
        return self._recent(self._apt_month, lawd_cd, months, base)

    # ---------- 단독·다가구 전월세 실거래(다가구·근린주택 주변 임대시세) ----------
    def _sh_rent_month(self, lawd_cd: str, ymd: str) -> tuple[list[dict], str | None]:
        try:
            r = _CLIENT.get(_OP_SHRENT, params={"serviceKey": self.key, "LAWD_CD": lawd_cd,
                                                "DEAL_YMD": ymd, "numOfRows": "1000", "pageNo": "1"})
            if r.status_code == 429 or "quota exceeded" in r.text[:120].lower():
                return [], "QUOTA"
            root = ET.fromstring(r.text)
        except Exception as e:
            return [], f"국토부 단독다가구 전월세 호출 실패: {type(e).__name__}"
        code = root.findtext(".//resultCode")
        if code not in ("000", "00"):
            return [], f"국토부 전월세 오류(코드 {code}): {root.findtext('.//resultMsg')}"
        out = []
        for it in root.findall(".//item"):
            dep = re.sub(r"[^0-9]", "", _t(it, "deposit"))
            rent = re.sub(r"[^0-9]", "", _t(it, "monthlyRent"))
            out.append({
                "deposit": int(dep) * 10000 if dep else 0,       # 만원 → 원
                "rent": int(rent) * 10000 if rent else 0,        # 월세(0=전세)
                "area": float(_t(it, "totalFloorAr") or 0),       # 전용/연면적 ㎡
                "house_type": _t(it, "houseType"),                # 단독/다가구
                "build_year": _t(it, "buildYear"),
                "umd": _t(it, "umdNm"),                            # 법정동
                "deal_date": f"{_t(it,'dealYear')}-{int(_t(it,'dealMonth') or 0):02d}-{int(_t(it,'dealDay') or 0):02d}",
            })
        return out, None

    def sh_rent_recent(self, lawd_cd: str, months: int = 12,
                       base: date | None = None) -> dict:
        """시군구(lawd_cd) 최근 months개월 단독·다가구 전월세 실거래."""
        return self._recent(self._sh_rent_month, lawd_cd, months, base)

    def _recent(self, fn, lawd_cd: str, months: int, base) -> dict:
        global _QUOTA_HIT
        if not self.key:
            return {"error": "국토부 서비스키 미설정", "trades": []}
        if _QUOTA_HIT:                                  # 이미 할당량 초과 감지됨 → 즉시 중단(불필요 호출 방지)
            return {"error": "QUOTA", "trades": []}
        base = base or date.today()
        ymds, y, m = [], base.year, base.month         # 조회할 월(YYYYMM) 목록
        for _ in range(months):
            ymds.append(f"{y}{m:02d}")
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        # 월별 병렬 조회 — 시군구당 12개월 직렬(API 12번 줄세우기)이 예열의 핵심 병목이었음.
        # 공유 keep-alive 클라이언트로 동시 호출 → 시군구당 fetch 지연 ~월수배 단축. QUOTA는 호출별 감지 유지.
        trades, errs = [], []
        with _cf.ThreadPoolExecutor(max_workers=min(months, 12)) as ex:
            for rows, err in ex.map(lambda ymd: fn(lawd_cd, ymd), ymds):
                if err:
                    errs.append(err)
                    if err == "QUOTA":
                        _QUOTA_HIT = True
                else:
                    trades.extend(rows)
        if not trades and errs:
            return {"error": errs[0], "trades": []}
        return {"trades": trades, "count": len(trades), "months": months}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def match_apt(trades: list[dict], address: str, *, area: float | None = None,
              area_pct: float = 0.10) -> dict:
    """경매 아파트 주소와 같은 '단지'의 실거래를 추린다.
    매칭(강화): ①법정동(umd)+지번(jibun) 정확일치 우선 — 같은 지번 거래가 있으면 그것만(다른 단지 혼입 차단).
               ②지번 매칭이 없을 때만 '단지명' 부분일치(짧은 일반토큰 오매칭 방지: 단지명부와 비교, 3자 이상).
    같은 단지가 잡히면 전용면적 ±area_pct(같은 평형) 필터도 적용.
    반환: {complex: 매칭 단지명, build_year, trades(정렬), same_area(면적필터), addr_jibun}."""
    # 주소의 지번(법정동 뒤 숫자[-숫자]) 추출
    mj = re.search(r"([가-힣]+(?:동|리|가))\s*(\d+(?:-\d+)?)", address)
    addr_umd = mj.group(1) if mj else None
    addr_jibun = mj.group(2) if mj else None
    # ① 법정동+지번 정확일치 우선 → 있으면 그것만(다른 지번/단지 혼입 차단). 핵심 수정.
    loc = [t for t in trades if addr_umd and addr_jibun
           and t.get("umd") == addr_umd and t.get("jibun") == addr_jibun]
    if loc:
        same: list[dict] = loc
    else:                                  # ② 지번 매칭이 아예 없을 때만 단지명 부분일치(폴백, 지번 없어 혼입위험 낮음)
        addr_n = _norm(address)
        same = [t for t in trades if _norm(t.get("name")) and _norm(t.get("name")) in addr_n]

    complex_name = ""
    build_year = ""
    if same:
        # 가장 많이 등장한 단지명/건축년도 채택
        from collections import Counter
        complex_name = Counter(_n["name"] for _n in same if _n.get("name")).most_common(1)
        complex_name = complex_name[0][0] if complex_name else ""
        bys = Counter(_n["build_year"] for _n in same if _n.get("build_year"))
        build_year = bys.most_common(1)[0][0] if bys else ""

    # 같은 평형(전용면적 ±pct) 필터 — 매칭되면 area_matched=True
    same_area = same
    area_matched = False
    if area and same:
        lo, hi = area * (1 - area_pct), area * (1 + area_pct)
        f = [t for t in same if lo <= t.get("area", 0) <= hi]
        if f:
            same_area = f
            area_matched = True

    same.sort(key=lambda t: t.get("deal_date", ""), reverse=True)
    same_area = sorted(same_area, key=lambda t: t.get("deal_date", ""), reverse=True)
    return {"complex": complex_name, "build_year": build_year,
            "trades": same, "same_area": same_area, "area_matched": area_matched,
            "addr_umd": addr_umd, "addr_jibun": addr_jibun}


def filter_similar(trades: list[dict], *, umd: str | None = None,
                   area: float | None = None, area_pct: float = 0.20) -> list[dict]:
    """유사 조건 필터: (옵션) 같은 법정동 + 전용면적 ±area_pct."""
    out = []
    lo = area * (1 - area_pct) if area else None
    hi = area * (1 + area_pct) if area else None
    for t in trades:
        if umd and t.get("umd") != umd:
            continue
        if area and not (lo <= t.get("area", 0) <= hi):
            continue
        out.append(t)
    return out
