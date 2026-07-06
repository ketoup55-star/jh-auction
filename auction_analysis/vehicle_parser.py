"""차량/중기현황 파싱.

- 매각물건명세서 '자동차의 표시'(텍스트): 차명·등록번호·연식·차종·원동기형식·차대번호·등록일자·제원관리번호·사용본거지·보관장소·보관방법 + 비고(사고)
- 감정평가서 '대상자동차 개요'(텍스트, 법인별 형식 상이): 주행거리·배기량·색상·사용연료·변속기·검사기간
  (차량등록원부는 스캔이미지라 텍스트추출 불가 → 감정평가서로 보완)
두 소스를 합쳐 표시한다.
"""

from __future__ import annotations

import datetime
import re

_KM_PER_YEAR = 13000        # 1년당 양호 주행거리(5년 초과 차량)
_KM_PER_YEAR_RECENT = 14000  # 연식 5년 이내(나이≤5) 차량은 14,000으로 완화
_RECENT_YEARS = 5
_KM_TOLERANCE = 9999        # 추가 허용
_ACC_MAX_COUNT = 5          # 사고회수(내차+상대차) 이 값 이하면 OK(=5회 OK)
_ACC_MAX_SELF_AMT = 6_000_000   # 내차피해금액 이 값 미만이면 OK(=600만원 정확히면 금지)
_DAMAGE_KEYWORDS = ("파손", "누유")   # 차량현황(기타)에 이 단어 있으면 매수 금지('없음' 표현 제외). 추가 시 여기에.


def buy_grade(spec: dict) -> dict | None:
    """매수 판정: ①주행거리 ②사고 조건을 모두 만족해야 '매수 검토', 하나라도 위반이면 '매수 금지'.
      - 주행거리: (현재연도-연식)*13,000 + 9,999km 이하
      - 사고(수집된 경우만): 총 사고회수(내차+상대차) ≤ 5 그리고 내차피해금액 < 600만원
      - 연식/주행거리 없으면 None(→ '정보없음'). 사고 미수집이면 주행거리 조건만 적용."""
    # 차량/중기현황(기타)에 파손·누유 등 키워드 있으면 무조건 매수 금지('… 없음/흔적 없음'은 제외)
    note = str(spec.get("etc_note") or "")
    damage_kw = [kw for kw in _DAMAGE_KEYWORDS
                 if kw in note and not re.search(kw + r"\s*(?:흔적\s*)?없", note)]
    damaged = bool(damage_kw)
    try:
        y = int(spec.get("model_year")); km = int(spec.get("mileage_km"))
        has_yk = y > 0 and km >= 0
    except (TypeError, ValueError):
        has_yk = False
    if not has_yk and not damaged:
        return None                                     # 연식/주행 없고 파손도 아니면 정보없음
    reasons = []
    if damaged:
        reasons.append("차량 " + "·".join(damage_kw) + " 기재")
    allowed = None
    acc_eval = False
    if has_yk:
        age = max(0, datetime.date.today().year - y)
        per_year = _KM_PER_YEAR_RECENT if age <= _RECENT_YEARS else _KM_PER_YEAR
        allowed = age * per_year + _KM_TOLERANCE
        if km > allowed:
            reasons.append(f"주행거리 {km:,}km(허용 {allowed:,}km 초과)")
        sc, oc = spec.get("accident_self_count"), spec.get("accident_other_count")
        acc_eval = not (sc is None and oc is None)      # 사고 수집 여부
        if acc_eval:
            total = int(sc or 0) + int(oc or 0)
            self_amt = int(spec.get("accident_self_amount") or 0)
            if total > _ACC_MAX_COUNT:
                reasons.append(f"사고 {total}회(5회 초과)")
            if self_amt >= _ACC_MAX_SELF_AMT:
                reasons.append(f"내차피해 {self_amt:,}원(600만원 이상)")
    ok = not reasons
    return {"ok": ok, "label": "매수 양호" if ok else "매수 금지",
            "allowed": allowed, "year": y if has_yk else None, "mileage": km if has_yk else None,
            "reasons": reasons, "accident_evaluated": acc_eval}

# 표시 순서(있는 것만 노출)
DISPLAY_ORDER = [
    "차명", "제조사", "등록번호", "차종", "연식", "색상", "주행거리", "배기량",
    "사용연료", "변속기", "원동기형식", "검사기간", "차대번호", "등록일자",
    "제원관리번호", "사용본거지", "보관장소", "보관방법",
]

# 매각물건명세서 원시 라벨(정규화) → 표시 라벨
_MS_LABELS = {
    "차명": "차명", "제조사": "제조사", "등록번호": "등록번호", "차종": "차종",
    "연식": "연식", "원동기형식": "원동기형식", "차대번호": "차대번호",
    "등록연월일": "등록일자", "등록일자": "등록일자",
    "제원관리번호": "제원관리번호", "제작자관리번호": "제원관리번호",
    "사용본거지": "사용본거지", "보관장소": "보관장소", "보관방법": "보관방법",
}
_END = ("감정평가액", "회차 기", "------", "최저매각", "비 고")
_COLORS = (r"흰색|백색|검정색?|회색|은색|쥐색|남색|청색|파랑색?|빨강색?|은회색|진회색|"
           r"연회색|곤색|네이비|샴페인|진주색?|초록색?|노란색?|주황색?|갈색")


def parse_vehicle_ms(text: str) -> dict:
    """매각물건명세서 '자동차의 표시' → {raw(label→value), notes, machine}."""
    machine = False
    i = text.find("자동차의 표시")
    if i < 0:
        i = text.find("기계기구의 표시")
        machine = i >= 0
    if i < 0:
        return {"raw": {}, "notes": [], "machine": False}
    block = text[i:i + 1200]
    ends = [block.find(e) for e in _END if 0 <= block.find(e)]
    fend = min(ends) if ends else len(block)
    head = block[:fend]

    rawkv: dict[str, str] = {}
    for line in head.split("\n"):
        m = re.match(r"\s*(?:\d+\.\s*)?([가-힣A-Za-z][가-힣A-Za-z()\s]*?)\s*[:：]\s*(.+)", line)
        if not m:
            continue
        lab = re.sub(r"\s+", "", m.group(1))
        val = re.sub(r"\s+", " ", m.group(2)).strip()
        if lab and val and lab not in rawkv:
            rawkv[lab] = val
    if "원동기형식및연식" in rawkv:
        v = rawkv.pop("원동기형식및연식")
        mm = re.match(r"([^/\s]+)\s*/\s*(\d{4})", v)
        if mm:
            rawkv.setdefault("원동기형식", mm.group(1))
            rawkv.setdefault("연식", mm.group(2))
        else:
            rawkv.setdefault("원동기형식", v)

    raw: dict[str, str] = {}
    for rk, disp in _MS_LABELS.items():
        if rk in rawkv and disp not in raw:
            raw[disp] = rawkv[rk]

    notes = []
    for line in block[fend:].split("\n"):
        s = line.strip()
        if not s or set(s) <= set("- ") or "회차" in s or "최저매각" in s:
            continue
        if re.match(r"^\d+회", s) or re.search(r"감정평가액|매수신청|보증금|기 일", s):
            continue
        s = s.lstrip("-※ ").strip()
        if s and re.search(r"[가-힣]", s) and not re.match(r"^\d[\d,. ]*$", s):
            notes.append(s)
    return {"raw": raw, "notes": notes, "machine": machine}


def parse_vehicle_appraisal(text: str) -> dict:
    """감정평가서 → {주행거리, 배기량, 색상, 사용연료, 변속기, 검사기간}(있는 것만)."""
    if not text or len(text.strip()) < 50:
        return {}
    out: dict[str, str] = {}
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d{4,6})\s*(?:km|㎞)", text)
    if m:
        out["주행거리"] = f"{int(m.group(1).replace(',', '')):,}km"   # 21,673km
    m = re.search(r"(\d{1,3}(?:,\d{3})*|\d{3,5})\s*(?:cc|㏄)", text)
    if m:
        out["배기량"] = f"{int(m.group(1).replace(',', '')):,}cc"     # 1,591cc
    m = re.search(r"(디젤|경유|휘발유|가솔린|LPG|엘피지|하이브리드|전기|수소)", text)
    if m:
        out["사용연료"] = m.group(1)
    m = re.search(r"(오토|수동|CVT|DCT|자동변속)", text)
    if m:
        out["변속기"] = m.group(1)
    m = re.search(r"(%s)" % _COLORS, text)
    if m:
        out["색상"] = m.group(1)
    m = re.search(r"(?:검사유효기간|유효검사기간|검사기간)[^0-9]{0,10}"
                  r"(\d{4}[.\-]\s?\d{1,2}[.\-]\s?\d{1,2}\s*[~∼-]\s*\d{4}[.\-]\s?\d{1,2}[.\-]\s?\d{1,2})", text)
    if m:
        out["검사기간"] = re.sub(r"\s", "", m.group(1)).replace("-", ".")
    return out


def parse_accident(text: str) -> dict | None:
    """감정평가서 사고이력(보험개발원/CarHistory) → {available, count, amount, total_loss, flood, items, summary}.
    형식이 평가법인마다 달라 best-effort: 수리(견적)비용 금액(10만원↑)을 사고건으로 집계."""
    if not text:
        return None
    m = re.search(r"사고이력|Car\s*History|보험개발원", text, re.I)
    if not m:
        return None
    # 사고 섹션: 마커 ~ 다음 섹션('감정평가액'/'결정의견'/'매물사례'/'명세표') 전까지(감정가·매물가 오인 방지)
    rest = text[m.start():]
    end = re.search(r"감정평가액|결정의견|매물사례|평가사례|감정평가명세표", rest)
    seg = rest[:end.start()] if end else rest[:1000]
    t = re.sub(r"\s+", "", seg)
    flood = bool(re.search(r"침수(보험)?사고[^.]{0,8}(있|발생)", t))
    total_loss = bool(re.search(r"전손(보험)?사고[^.]{0,8}(있|발생)", t))
    none_flag = ("내차피해없음" in t and "상대차피해없음" in t) or "사고이력없음" in t

    def _won(s):
        return int(re.sub(r"[^\d]", "", s))

    # ① 명시적 '내차/상대차 피해 N회(금액원)'  (가장 신뢰도 높음 → 섹션경계 무관하게 넓게)
    count = amount = 0
    for cnt, amt in re.findall(
            r"(?:내차|상대차)\s*피해\s*(\d+)\s*회\s*\(?\s*([\d,\.]+)\s*원", rest[:2500]):
        count += int(cnt)
        amount += _won(amt)
    # ② 명시 패턴 없으면 '날짜 + 금액' 행으로 집계(시세/입찰가 등 날짜 없는 금액은 자동 제외)
    if count == 0 and not none_flag:
        for _dt, amt in re.findall(
                r"(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})[^\n]{0,40}?(\d{1,3}(?:[,\.]\d{3})+)", seg):
            v = _won(amt)
            if v >= 100000:
                count += 1
                amount += v
    if count == 0:
        if none_flag:
            return {"available": True, "count": 0, "amount": 0,
                    "total_loss": False, "flood": False, "summary": "사고이력 없음(보험개발원)"}
        return None                       # 단서 없으면 표시 안 함
    return {"available": True, "count": count, "amount": amount,
            "total_loss": total_loss, "flood": flood}


# vehicle_specs 컬럼 → 표시 라벨(노출 순서)
_SPEC_ORDER = [
    ("model", "차명"), ("model_grade", "세부등급"), ("manufacturer", "제조사"),
    ("plate_no", "등록번호"), ("model_year", "연식"), ("color", "색상"),
    ("mileage_km", "주행거리"), ("displacement_cc", "배기량"), ("fuel", "사용연료"),
    ("transmission", "변속기"), ("engine_type", "원동기형식"), ("inspection_period", "검사기간"),
    ("vin", "차대번호"), ("reg_date", "등록일자"),
    ("storage_location", "보관장소"), ("approval_no", "승인번호"),
]


def _accident_from_spec(spec: dict) -> dict:
    """vehicle_specs 사고 컬럼 → 사고이력 dict.
    수집 안 됨(둘 다 null) → needs_check, 수집됨 → 내차/상대차 횟수·금액 + 소유자변경."""
    sc, oc = spec.get("accident_self_count"), spec.get("accident_other_count")
    own = spec.get("owner_changes")
    if sc is None and oc is None:
        return {"available": True, "needs_check": True}     # 미수집
    sc, oc = int(sc or 0), int(oc or 0)
    sa, oa = int(spec.get("accident_self_amount") or 0), int(spec.get("accident_other_amount") or 0)
    return {"available": True, "needs_check": False,
            "self_count": sc, "self_amount": sa, "other_count": oc, "other_amount": oa,
            "total_count": sc + oc, "total_amount": sa + oa,
            "owner_changes": (int(own) if own is not None else None)}


def build_vehicle_from_specs(spec: dict) -> dict | None:
    """vehicle_specs 1행(크롤러 구조화) → {available, machine, fields, notes, accident}.
    PDF 파싱 없이 DB 값만 사용. 빈 칸(원본 미입력)은 노출 제외."""
    if not spec:
        return None
    maker = str(spec.get("manufacturer") or "").strip()
    fields = []
    for col, lab in _SPEC_ORDER:
        v = spec.get(col)
        if v in (None, "", 0) and col in ("mileage_km", "displacement_cc"):
            continue                       # 미입력/0(전기차 cc 등)은 표시 안 함
        if v in (None, ""):
            continue
        if col == "mileage_km":
            val = f"{int(v):,}km"
        elif col == "displacement_cc":
            val = f"{int(v):,}cc"
        elif col == "model_year":
            val = str(v)
        elif col == "model":
            val = str(v).strip()           # 차명 끝 꼬리 제조사명 제거(예: 'E220 d Cabriolet벤츠'→'E220 d Cabriolet')
            if maker and val.endswith(maker) and len(val) > len(maker):
                val = val[:-len(maker)].rstrip(" /")
        else:
            val = str(v).strip()
        if val:
            fields.append({"label": lab, "value": val})
    if not fields:
        return None
    return {"available": True, "machine": False, "fields": fields, "notes": [],
            "accident": _accident_from_spec(spec),     # 크롤러가 수집한 사고이력
            "owner_changes": spec.get("owner_changes"),
            "etc_note": spec.get("etc_note"),          # 기타 셀 원문(관리상태·옵션·사용본거지 등)
            "model_grade": spec.get("model_grade"),    # 세부등급(매칭 보강용: 예 BMW 528i)
            "grade": buy_grade(spec)}


def build_vehicle(ms_text: str, appraisal_text: str | None) -> dict | None:
    """매각물건명세서 + 감정평가서 병합 → {available, machine, fields, notes, accident}."""
    ms = parse_vehicle_ms(ms_text or "")
    raw = dict(ms["raw"])
    ap = parse_vehicle_appraisal(appraisal_text or "")
    for k, v in ap.items():               # 감정평가서 보완(명세서에 없으면 추가)
        if v and not raw.get(k):
            raw[k] = v
    fields = [{"label": lab, "value": raw[lab]} for lab in DISPLAY_ORDER if raw.get(lab)]
    accident = parse_accident(appraisal_text or "")
    if not fields and not ms["notes"]:
        return None
    return {"available": True, "machine": ms["machine"], "fields": fields,
            "notes": ms["notes"], "accident": accident}
