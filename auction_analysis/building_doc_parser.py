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


def _year(s: str):
    m = re.search(r"(19|20)\d{2}", s or "")
    return m.group(0) if m else None


def parse_bldg_doc(text: str) -> dict:
    """건축물대장 PDF 텍스트 → {build_year, units, unit_label, elevator}. 빈 값은 None."""
    out: dict = {"build_year": None, "units": None, "unit_label": None, "elevator": None, "violation": False}
    if not text:
        return out
    t = re.sub(r"\s+", "", text)
    out["violation"] = "위반건축물" in t          # 건축물대장 갑 상단 '위반건축물' 스탬프(표제부 API엔 없는 정보)

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


def parse_appraisal_bldg(text: str) -> dict:
    """감정평가서 텍스트 → {build_year, units, unit_label, elevator}.
    물건개요에 '사용승인 YYYY.MM.DD', 'N개호/N세대', 설비란에 '승강기설비'가 있음."""
    out: dict = {"build_year": None, "units": None, "unit_label": None, "elevator": None}
    if not text:
        return out
    m = re.search(r"사용승인일?자?\s*[:：]?\s*" + _DATE, text)
    if m:
        out["build_year"] = m.group(1)
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
    }
