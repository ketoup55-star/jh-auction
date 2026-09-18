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
        # ⚠️일부 월이 쿼터(QUOTA)/오류로 실패하면 trades가 부분수집이다 → incomplete 표시(호출측이 캐시 저장을 막아 부분결과 고착 방지)
        return {"trades": trades, "count": len(trades), "months": months,
                "errors": errs, "incomplete": bool(errs)}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


_SIDO_TOK = re.compile(r"(특별자치도|특별자치시|특별시|광역시|도)$")
_UNIT_TOK = re.compile(r"(\d+층|\d+호$|^제?\d+동$|^제?[가-힣A-Za-z]{1,3}동$|^[A-Za-z]-?\d+호?$)")
_NAME_TAIL = re.compile(r"(아파트|맨션|apt|APT|오피스텔)$")


def _strip_tail(s: str) -> str:
    return _NAME_TAIL.sub("", _norm(s))


def addr_location(address: str) -> dict:
    """경매 주소 → {emd: 법정동(동·가) 또는 읍면, ri: 리, jibun: 지번, name: 단지명부(정규화), cname: 단지명(아파트 꼬리 뗌)}.
    지번주소 '… 기장읍 동부리 151-5 한신아파트 107동 …' / 도로명주소 '… 중계로 233, 104동 … (중계동,청구아파트)'
    (도로명은 끝 괄호의 법정동이 정확 — 읍면 도로명은 괄호에 단지명만 온다)."""
    a = address or ""
    par = re.search(r"\(([^()]*)\)\s*$", a)
    par_dong, par_name = "", ""
    if par:
        parts = [p.strip() for p in par.group(1).split(",") if p.strip()]
        if parts and re.fullmatch(r"[가-힣][가-힣0-9]*(동|가|리)", parts[0]):
            par_dong, parts = parts[0], parts[1:]
        par_name = " ".join(parts)
        a = a[:par.start()]
    toks = a.replace(",", " ").split()
    i = 1 if toks and _SIDO_TOK.search(toks[0]) else 0
    while i < len(toks) and re.fullmatch(r"[가-힣]+(시|군|구)", toks[i]):
        i += 1
    emd = ri = jibun = ""
    if i < len(toks) and re.fullmatch(r"[가-힣][가-힣0-9]*(읍|면)", toks[i]):
        emd, i = toks[i], i + 1
        if i < len(toks) and re.fullmatch(r"[가-힣][가-힣0-9]*리", toks[i]):
            ri, i = toks[i], i + 1
    elif i < len(toks) and re.fullmatch(r"[가-힣][가-힣0-9]*(동|가)", toks[i]):
        emd, i = toks[i], i + 1
    if i < len(toks) and re.fullmatch(r"[가-힣0-9.]+(로|길)", toks[i]):
        i += 1
        if i < len(toks) and re.match(r"^\d", toks[i]):
            i += 1
    elif i < len(toks) and re.fullmatch(r"산?\d+(-\d+)?(외|번지)?", toks[i]):
        jibun, i = re.sub(r"(외|번지)$", "", toks[i]), i + 1
    if not emd and par_dong:
        emd = par_dong
    elif emd.endswith(("읍", "면")) and par_dong.endswith("리") and not ri:
        ri = par_dong
    elif emd and par_dong and not emd.endswith(("읍", "면")):
        emd = par_dong
    head = []
    for t in toks[i:]:
        if _UNIT_TOK.search(t) or re.match(r"^\d", t):
            break
        head.append(t)
    return {"emd": emd, "ri": ri, "jibun": jibun,
            "name": _norm(" ".join(toks[i:]) + " " + par_name),
            "cname": _strip_tail(par_name) or _strip_tail(" ".join(head))}


def _same_place(t_umd: str, loc: dict) -> bool:
    """실거래 법정동(umd: '중계동' / '기장읍 동부리' / '인후동1가')이 물건과 같은 곳인가."""
    tu = (t_umd or "").split()
    e = loc["emd"]
    if not tu or not e:
        return False
    t_emd, t_ri = tu[0], (tu[1] if len(tu) > 1 else "")
    if e.endswith(("읍", "면")):
        if not (t_emd == e or (t_emd[:-1] == e[:-1] and t_emd[-1] in "읍면동")):   # 면→읍 승격·면→동 전환
            return False
        return not loc["ri"] or not t_ri or t_ri == loc["ri"]
    if t_emd == e:
        return True
    if re.search(r"\d+가$", t_emd) and re.search(r"\d+가$", e):       # 신흥동1가 ≠ 신흥동2가
        return False
    return re.sub(r"\d+가$", "", t_emd) == re.sub(r"\d+가$", "", e)   # 인후동 ↔ 인후동1가(한쪽만 번호)


def _main_lot(j: str):
    m = re.match(r"^산?(\d+)", j or "")
    return int(m.group(1)) if m else None


def _name_ok(t: dict, loc: dict) -> bool:
    """단지명 폴백 조건: 거래 단지명이 주소의 단지명부(시도·시군구·동 이름 제외)에 있어야.
    지번 주소인데 거래 지번이 다르면 근거를 더 요구 — 이름이 같고(아파트 꼬리 뗀 채) 본번이 가깝거나(±5) 4자 이상,
    또는 4자 이상 이름이 단지명 안에 있고 본번이 가까움(큰 단지는 여러 필지)."""
    tn = _norm(t.get("name"))
    if len(tn) < 2 or tn not in loc["name"]:
        return False
    if loc["jibun"] and t.get("jibun") != loc["jibun"]:
        ts, cn = _strip_tail(tn), loc["cname"]
        a, b = _main_lot(t.get("jibun")), _main_lot(loc["jibun"])
        near = a is not None and b is not None and abs(a - b) <= 5
        if cn and ts == cn and (near or len(ts) >= 4):
            return True
        return len(ts) >= 4 and bool(cn) and ts in cn and near
    return True


def match_apt(trades: list[dict], address: str, *, area: float | None = None,
              area_pct: float = 0.10) -> dict:
    """경매 아파트 주소와 같은 '단지'의 실거래를 추린다.
    ①같은 곳(법정동, 읍면이면 읍면+리) + 같은 지번 → 그것만.
    ②지번이 안 맞으면 '같은 법정동 안에서' 단지명 일치(주소의 단지명부와 비교, 가장 긴 이름 우선).
    🔴2026-09-18 전수감사(목록 아파트·오피스텔 9,027건 중 약 1,450건 오매칭): 예전 ②는 거래 단지명이 주소 문자열
    '어디든' 들어 있으면 붙였다 → 시도·시군구 이름('서울'·'강원'·'송파'·'평택'·'청주' 단지)과 흔한 이름('현대'·'한신'·
    '동원')이 다른 동 단지에 붙어 그 단지 거래로 시세를 냈다(예: 거제 연초면 한내시온숲속의아침뷰 → 아주동 '숲속의아침').
    또 ①은 읍면 지역 실거래 법정동이 '기장읍 동부리'라 주소의 '동부리'와 한 번도 같지 않았다(읍면 물건 지번매칭 0).
    같은 단지가 잡히면 전용면적 ±area_pct(같은 평형) 필터도 적용.
    반환: {complex, build_year, trades(최근순), same_area, area_matched, addr_umd, addr_jibun, match_by}."""
    loc = addr_location(address)
    same: list[dict] = []
    match_by = ""
    if loc["jibun"]:
        same = [t for t in trades if t.get("jibun") == loc["jibun"] and _same_place(t.get("umd"), loc)]
        match_by = "지번" if same else ""
    if not same:
        cand = [t for t in trades if _name_ok(t, loc)
                and (_same_place(t.get("umd"), loc) if loc["emd"] else len(_norm(t.get("name"))) >= 3)]
        if cand:
            best = max(len(_norm(t.get("name"))) for t in cand)          # '청구3차'가 있으면 '청구'는 버림
            spots = {(t.get("umd"), t.get("jibun")) for t in cand if len(_norm(t.get("name"))) == best}
            same = [t for t in cand if len(_norm(t.get("name"))) == best or (t.get("umd"), t.get("jibun")) in spots]
            match_by = "단지명"

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
            "addr_umd": loc["emd"] or None, "addr_jibun": loc["jibun"] or None, "match_by": match_by}


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
