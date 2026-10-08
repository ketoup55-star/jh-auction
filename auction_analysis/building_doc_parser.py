"""저장된 문서(건축물대장 PDF·감정평가서)에서 준공년도·세대수·승강기 추출.

data.go.kr 건축물대장 API는 일일 호출한도가 있어, DB에 보유한 문서를 1차 소스로 사용.
 - 건축물대장(일반/표제부): N호/N가구/N세대, 승용·비상용 승강기, 사용승인일 모두 포함
 - 건축물대장(집합 전유부): 신축 변동일(준공), 용도 (세대수·승강기 없음)
 - 감정평가서: 사용승인일자, 승강기설비 언급, 지상N층, 주용도
API(building_source)는 문서로 못 채운 항목만 폴백.
"""

from __future__ import annotations

import re

_DATE = r"(\d{4})[.\-]\s*\d{1,2}[.\-]\s*\d{1,2}"

# 숙박시설 세부용도(건축물대장 주용도/기타용도/전유부 용도) → 목록·상세 표시 라벨. 우선순위 순(먼저 매칭이 이김).
#  ⚠️'생활숙박'을 '숙박'보다 먼저 둬야 생숙이 '숙박시설'로 뭉개지지 않음. '여관' 등은 건축물대장 전유부 용도에 (여관)식으로 기재됨.
_SUKBAK_SUB = [
    (re.compile(r"생활\s*(형\s*)?숙박"), "생활형숙박시설"),
    (re.compile(r"여인숙"), "여인숙"),
    (re.compile(r"여관"), "여관"),
    (re.compile(r"관광\s*호텔|호텔"), "호텔"),
    (re.compile(r"모텔"), "모텔"),
    (re.compile(r"콘도\s*(미니엄)?"), "콘도미니엄"),
    (re.compile(r"펜션"), "펜션"),
    (re.compile(r"민박"), "민박"),
]


def sukbak_subtype(*texts) -> "str | None":
    """건축물대장 용도 텍스트(주용도·기타용도·전유부 용도)에서 숙박 세부유형 라벨 추출. 없으면 None(=일반 '숙박시설')."""
    joined = " ".join(str(t) for t in texts if t)
    if not joined:
        return None
    for pat, label in _SUKBAK_SUB:
        if pat.search(joined):
            return label
    return None


def _year(s: str):
    m = re.search(r"(19|20)\d{2}", s or "")
    return m.group(0) if m else None


def parse_bldg_doc(text: str) -> dict:
    """건축물대장 PDF 텍스트 → {build_year, units, unit_label, elevator}. 빈 값은 None."""
    out: dict = {"build_year": None, "units": None, "unit_label": None, "elevator": None,
                 "violation": False, "sukbak_sub": None}
    if not text:
        return out
    t = re.sub(r"\s+", "", text)
    out["violation"] = "위반건축물" in t          # 건축물대장 갑 상단 '위반건축물' 스탬프(표제부 API엔 없는 정보)
    out["sukbak_sub"] = sukbak_subtype(t)         # 전유부/표제부 용도의 숙박 세부유형(여관·생활숙박 등) — 무쿼터

    # 호수/가구수/세대수 (예: "1호/0가구/0세대")
    m = re.search(r"(\d+)호/(\d+)가구/(\d+)세대", t)
    if m:
        ho, ga, se = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if se > 0:
            out["units"], out["unit_label"] = se, "세대"
        elif ga > 0:
            out["units"], out["unit_label"] = ga, "가구"
        elif ho > 0:
            out["units"], out["unit_label"] = ho, "호"

    # 사용승인일(준공): ①'사용승인일' 인근 연도(표제부/일반대장) ②변동사항 '신규작성/신축'의 날짜(전유부).
    #   변동사항은 날짜와 키워드 사이에 다른 숫자(문서번호 등)가 끼므로 .{0,N}? 로 허용.
    yr = None
    m = re.search(r"사용승인일[^0-9]{0,30}((?:19|20)\d{2})", t)
    if m:
        yr = m.group(1)
    if not yr:
        for m in re.finditer(_DATE + r".{0,55}?(?:사용승인|신규작성|신축)", text):
            yr = m.group(1)        # 마지막(가장 가까운 맥락) 우선
        if not yr:
            m = re.search(r"(?:사용승인|신규작성|신축).{0,30}?" + _DATE, text)
            if m:
                yr = m.group(1)
    out["build_year"] = yr

    # 승강기: '승용N대' 정확 매칭만(평탄화 표에서 다른 숫자 오인 방지). 불명확하면 None→감정평가서/API.
    elev = None
    m = re.search(r"승용(\d+)대", t)              # 예: 승용1대
    if m:
        elev = int(m.group(1)) > 0
    out["elevator"] = elev
    return out


#  🔴2026-10-08: 감정평가서의 「사용승인일」은 **대상 물건 개요**에만 있는 게 아니다.
#   인근 거래사례·평가사례 표에도 같은 열 제목이 있고, PDF 가 표를 세로로 흘리면서
#   **거래시점/기준시점 날짜가 '사용승인일' 글자 바로 뒤**에 온다.
#   첫 매치를 쓰면 그 최근 날짜를 준공으로 읽는다(실측: 1995년 아파트 → 2026).
#   그래서 사례 표가 시작되기 전 구간에서만 찾는다.
#  '사용승인' 바로 앞 문맥에 이런 말이 있으면 **사례 표**다(그 물건의 준공일이 아니다).
#   ⚠️'기준시점'만으로 문서를 앞뒤로 자르면 안 된다 — 감정평가서 맨 앞 요약에도 나와서
#     물건 개요까지 잘려 나간다(1차 수정에서 실제로 그렇게 깨졌다).
#  ⚠️'출처'·'기호'는 **물건 개요에도** 쓰인다(실측: "5. 대상 물건의 개요 (출처 : 집합건축물대장 등)",
#    "기호 전유면적(㎡) 공용면적(㎡)") → 필터에 넣으면 진짜 개요까지 건너뛴다. 뺀다.
_APPR_CASE_RE = re.compile(r"거래사례|평가사례|비교사례|평가전례|거래시점|기준시점|"
                           r"거래단가|평가단가|거래금액|평가금액|거래가액")
#  'YYYY년 M월 D일' 한글 표기도 받는다(실측: "사용승인일자 1997년 10월 1일").
_DATE_KO = r"(\d{4})\s*년\s*\d{1,2}\s*월"


def _appr_year(text: str):
    """감정평가서에서 **대상 물건의** 사용승인 연도만 뽑는다.

    '사용승인' 이 나오는 자리를 모두 훑되, **앞 문맥이 사례 표면 건너뛴다.**
    마지막 안전장치로, 문서에 적힌 가장 늦은 해(=감정 시점)와 같은 해는 버린다
    — 경매 물건의 준공일이 감정 시점과 같은 해일 수는 거의 없고, 그게 이 오독의 모양이다.
    """
    if not text:
        return None
    years_all = [int(x) for x in re.findall(r"(?:19|20)\d{2}", text)]
    newest = max(years_all) if years_all else None
    fallback = None
    for m in re.finditer(r"사용승인일?자?", text):
        #  '사용승인일'은 표의 **열 제목**이라 값이 몇 줄 뒤에 온다(다른 열 제목이 끼어 ≈25자).
        #   그래서 바로 뒤가 아니라 90자 안에서 첫 날짜를 찾는다.
        tail = text[m.end():m.end() + 90]
        mm = re.search(_DATE, tail) or re.search(_DATE_KO, tail)
        if not mm:
            continue
        y = int(mm.group(1))
        ctx = text[max(0, m.start() - 120):m.start()]
        if _APPR_CASE_RE.search(ctx):
            continue                      # 거래·평가 사례 표의 시점 → 버린다
        if newest and y >= newest:
            fallback = fallback or str(y)  # 감정 시점과 같은 해 → 일단 보류
            continue
        return str(y)
    return fallback


def parse_appraisal_bldg(text: str) -> dict:
    """감정평가서 텍스트 → {build_year, units, unit_label, elevator}.
    물건개요에 '사용승인 YYYY.MM.DD', 'N개호/N세대', 설비란에 '승강기설비'가 있음."""
    out: dict = {"build_year": None, "units": None, "unit_label": None, "elevator": None}
    if not text:
        return out
    out["build_year"] = _appr_year(text)
    t = re.sub(r"\s+", "", text)
    # 세대수: 'N개호'(물건개요 총호수) 우선, 없으면 '총N세대'.
    #   ※ 바로 '1세대'/'구분건물N세대'는 물건 자체(전유)이므로 제외.
    #   ⚠️ 공백제거 후라 관리번호·면적 등 큰 숫자가 'N개호'에 붙어 오추출됨 → 자릿수 1~3(≤999)로 제한.
    #      (단독/다가구/연립/다세대 호수는 현실적으로 수백 이내. 그 이상은 파싱오류로 간주)
    m = re.search(r"(?<!\d)(\d{1,3})\s*개\s*호", t)
    if m and 1 <= int(m.group(1)) <= 500:
        out["units"], out["unit_label"] = int(m.group(1)), "호"
    else:
        m = re.search(r"총\s*(\d{1,4})\s*세대", t)
        if m and 2 <= int(m.group(1)) <= 500:
            out["units"], out["unit_label"] = int(m.group(1)), "세대"
    if re.search(r"승강기설비|엘리베이터|승용승강기|승강기가?\s*되어", t):
        out["elevator"] = True
    return out


def merge_doc_brief(bldg: dict | None, appr: dict | None) -> dict:
    """건축물대장 우선, 감정평가서 보완 → {build_year, units, unit_label, elevator}."""
    bldg = bldg or {}
    appr = appr or {}
    units = bldg.get("units") or appr.get("units")
    if isinstance(units, int) and (units < 0 or units > 500):   # 비현실값(파싱오류) 방어
        units = None
    unit_label = bldg.get("unit_label") if bldg.get("units") else appr.get("unit_label")
    return {
        "build_year": bldg.get("build_year") or appr.get("build_year"),
        "units": units,
        "unit_label": unit_label or "세대",
        "elevator": bldg.get("elevator") if bldg.get("elevator") is not None
        else appr.get("elevator"),
        "violation": bool(bldg.get("violation")) or bool(appr.get("violation")),
        "sukbak_sub": bldg.get("sukbak_sub") or appr.get("sukbak_sub"),   # 숙박 세부용도(여관·생활숙박 등)
    }
