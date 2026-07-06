"""
크롤러(speedauction) 구조화 데이터 → 기존 권리분석(analyze_registry) 출력 구조로 변환하는 어댑터.

전제: 크롤러가 `items.analyzed_at`·`item_rights`·`item_tenants`에 판정 포함 데이터를 적재.
  - 인수/소멸·대항력·소멸기준은 speedauction이 이미 계산한 값 → 사이트는 표시만.
  - 위험도(안전/주의/위험)는 사이트 자체 지표라 여기서 계산.
  - 물건현황/토지이용계획·면적 상세·소유권 이전행·건물열람일은 items.detail_text 원문을 파싱.
이 모듈이 내는 dict는 doc_analysis.analyze_registry 출력과 같은 키 구조라 프런트(auction.html) 호환.
"""
from __future__ import annotations

import concurrent.futures as _cf
import re
from typing import Optional


def _rows(db, table: str, item_key: str) -> list[dict]:
    r = db._get(table, {"select": "*", "item_key": f"eq.{item_key}", "order": "seq.asc"})
    return r.json() if r.status_code in (200, 206) else []


def _parse_floor(addr: str) -> Optional[int]:
    """주소에서 층수 추출. 'N층' 우선, 없으면 호수 앞자리 보조추정(502호→5)."""
    if not addr:
        return None
    m = re.search(r"(-?\d+)\s*층", addr)              # 'N층' 우선
    if m:
        f = int(m.group(1))
        return f if f > 0 else None                    # 지하(음수) 제외
    m = re.search(r"(\d{3,4})\s*호", addr)            # 'N층' 없으면 호수 앞자리(502호→5, 1203호→12)
    if m:
        h = m.group(1)
        return int(h[:-2]) if len(h) >= 3 else None
    return None


def elevator_caution(usage_name: str, address: str, elevator) -> Optional[str]:
    """다세대·도시형생활주택 + 4층 이상 + 승강기 없음/미상 → 매수세 조사 필요 코멘트(아니면 None).
    elevator: brief의 elevator 값. '0'=없음, 양수=있음, None/''/그외=미상. (미상도 매수검토 — 사용자 정책)"""
    u = usage_name or ""
    if not (("다세대" in u) or ("도시형" in u)):
        return None
    floor = _parse_floor(address)
    if floor is None or floor < 4:
        return None
    try:
        if int(str(elevator).strip()) > 0:             # 승강기 확정 '있음' → 제외
            return None
    except Exception:
        pass                                            # 숫자 아님 → 미상 취급(트리거)
    known_none = (str(elevator).strip() == "0")        # '0'=확정 없음 / 그외=미상
    return f"승강기 {'없음' if known_none else '미상'}·{floor}층 — 고층 비선호로 매수세(환금성) 조사 필요"


# ──────────────────────────────────── detail_text(분석본문 원문) 파서 ────────────────────────────────────
_SECT = ["물건현황/토지이용계획", "감정평가현황", "면적(단위:㎡)", "면적(단위:m²)",
         "임차인/대항력여부", "등기사항/소멸여부", "명세서 요약사항", "부동산종합공부 요약"]


def _split_sections(text: str) -> dict:
    lines = [l.strip() for l in (text or "").splitlines()]
    idx = {}
    for i, l in enumerate(lines):
        if l in _SECT and l not in idx:
            idx[l] = i
    order = sorted(idx.items(), key=lambda kv: kv[1])
    secs = {}
    for j, (h, i) in enumerate(order):
        end = order[j + 1][1] if j + 1 < len(order) else len(lines)
        secs[h] = [l for l in lines[i + 1:end] if l]
    return secs


def _between(lines: list, start: str, ends: tuple) -> list:
    if start not in lines:
        return []
    sub = lines[lines.index(start) + 1:]
    cut = len(sub)
    for e in ends:
        if e in sub:
            cut = min(cut, sub.index(e))
    return sub[:cut]


def _merge_frags(lines: list) -> list:
    """파편 라인 병합: '13,449.5㎡','분의','41.95㎡' → '13,449.5㎡ 분의 41.95㎡', '(12.69평)'는 앞줄에 붙임."""
    out: list[str] = []
    for l in lines:
        if l == "분의" and out:
            out[-1] = out[-1] + " 분의"
        elif l.startswith("(") and out:
            out[-1] = out[-1] + " " + l
        elif out and out[-1].endswith("분의"):
            out[-1] = out[-1] + " " + l
        else:
            out.append(l)
    return out


def _parse_owner(reg: list) -> Optional[dict]:
    """등기 섹션에서 소유권(지분/보존) 이전행 1건 추출(item_rights엔 없는 현 소유권 정보)."""
    for i, l in enumerate(reg):
        if l.startswith("소유권"):
            o = {"type": l, "date": "", "holder": "", "reason": "", "status": "이전", "gubun": ""}
            for x in reg[i + 1:i + 7]:
                if not o["date"] and re.match(r"\d{4}-\d{2}-\d{2}", x):
                    o["date"] = x
                    continue
                m = re.match(r"^(.+?)\s*등\s*기?\s*원\s*인", x)   # '상속 등 기 원 인' → 상속
                if m and not o["reason"]:
                    o["reason"] = m.group(1).strip()
                    continue
                if x in ("이전", "보존", "설정", "변경", "소멸", "인수"):
                    o["status"] = x
                    continue
                if x in ("건물", "토지", "집합건물", "집합"):
                    o["gubun"] = x
                    continue
                if not o["holder"] and o["date"]:
                    o["holder"] = x
            return o
    return None


def parse_detail_text(text: str) -> dict:
    """items.detail_text(소스 원문 플랫텍스트) → 4컬럼 표시용 구조."""
    secs = _split_sections(text)
    out: dict = {"status_bullets": [], "zone": "", "price_date": "",
                 "area_daeji": [], "area_bldg": [], "reg_view_date": "", "owner_transfer": None}
    bullets = secs.get("물건현황/토지이용계획", [])
    out["status_bullets"] = bullets
    out["zone"] = next((b for b in bullets
                        if re.search(r"제?\s*\d+\s*종.*(주거|상업|공업|녹지)지역|준주거지역|준공업지역", b)), "")
    appr = secs.get("감정평가현황", [])
    for i, l in enumerate(appr):
        if l == "가격시점" and i + 1 < len(appr):
            out["price_date"] = appr[i + 1]
            break
    area = secs.get("면적(단위:㎡)") or secs.get("면적(단위:m²)") or []
    out["area_daeji"] = _merge_frags(_between(area, "대지권", ("건물",)))
    out["area_bldg"] = _merge_frags(_between(area, "건물", ("건축물대장", "도로명")))
    reg = secs.get("등기사항/소멸여부", [])
    for i, l in enumerate(reg):
        if l == "건물열람" and i + 1 < len(reg):
            out["reg_view_date"] = reg[i + 1]
            break
    out["owner_transfer"] = _parse_owner(reg)
    return out


_WAIVER_KW = ("말소동의", "말소 동의", "말소에 동의", "대항력", "포기", "임차권등기 말소")


def _waiver_seg(flat: str, anchor: str, default: str) -> str:
    """anchor 키워드가 포함된 문장을 잘라 반환."""
    i = flat.find(anchor)
    start = flat.rfind(".", 0, i)
    end = flat.find(".", i)
    seg = flat[(start + 1 if start >= 0 else 0):(end + 1 if end >= 0 else len(flat))].strip()
    return seg[:240] if seg else default


def _detect_waiver(text: str) -> Optional[str]:
    """미배당 보증금 인수 면제 조건 감지 → 두 형태 모두 인정.
    ① 확약서: '말소동의·대항력 포기 확약서 제출'.
    ② 특별매각조건: '(임대차)보증금 반환청구권을 포기 + 임차권등기 말소' (채권자/임차인이 잔액 포기).
    → 둘 다 미배당 보증금을 낙찰자가 인수하지 않음."""
    if not text:
        return None
    flat = re.sub(r"\s+", " ", text)
    if "확약" in flat and any(k in flat for k in _WAIVER_KW):     # ① 확약서 형태
        return _waiver_seg(flat, "확약",
                           "말소동의 또는 대항력 포기 확약서가 제출되어 미배당 보증금을 낙찰자가 인수하지 않음")
    if re.search(r"반환(?:청구권|채권)[을를의\s]{0,4}포기(?!하지)", flat):    # ② 특별매각조건(보증금 반환청구권/채권 포기). 부정문 제외
        return _waiver_seg(flat, "반환청구권",
                           "특별매각조건(임대차보증금 반환청구권 포기·임차권등기 말소)으로 미배당 보증금을 낙찰자가 인수하지 않음")
    return None


def analyze_from_crawler(db, item_key: str) -> Optional[dict]:
    """analyzed_at 있으면 크롤러DB로 권리분석 구조 생성, 없으면 None(호출측이 PDF 폴백)."""
    cols = ("rights_baseline_date,total_debt,analyzed_at,detail_text,dividend_deadline,"
            "appraisal_price,appraisal_land,appraisal_building,appraisal_land_pct,"
            "appraisal_building_pct,land_area,building_area,area_text,address,usage_name,tags")
    # Supabase 클라우드 쿼리 왕복지연(~0.7s)이 병목 → items·rights·tenants 3개를 병렬 조회(순차 ~2.2s→~0.9s).
    with _cf.ThreadPoolExecutor(max_workers=3) as ex:
        f_it = ex.submit(lambda: db._get("items", {"select": cols, "item_key": f"eq.{item_key}", "limit": "1"}))
        f_ri = ex.submit(_rows, db, "item_rights", item_key)
        f_te = ex.submit(_rows, db, "item_tenants", item_key)
        r = f_it.result()
        rights_raw = f_ri.result()
        tenants_raw = f_te.result()
    head = r.json() if r.status_code in (200, 206) else []
    if not head or not head[0].get("analyzed_at"):
        return None                                    # 크롤러 미분석 → PDF 폴백
    it = head[0]

    # 승강기 유무(brief 캐시) — 다세대·도시형 4층↑ 무승강기 매수세 경고용
    _elev = None
    try:
        _bc = db.cache_get_many(["brief:" + item_key]) or {}
        _bv = _bc.get("brief:" + item_key)
        if isinstance(_bv, dict):
            _elev = _bv.get("elevator")
    except Exception:
        _elev = None

    # ── 등기 권리 ──
    rights: list[dict] = []
    baseline = None
    for x in rights_raw:
        rtype = x.get("right_type") or ""
        # 소유권은 채권총액 합산에서 제외돼야 하므로 status='소유권'으로 표기(프런트가 status!=='소유권'로 합산)
        status = "소유권" if "소유권" in rtype else (x.get("status") or None)
        rights.append({"date": x.get("reg_date"), "type": rtype,
                       "amount": x.get("amount") or 0, "holder": x.get("holder") or "",
                       "reason": "", "status": status, "gubun": x.get("gubun"),
                       "is_baseline": bool(x.get("is_baseline"))})
        if x.get("is_baseline") and baseline is None:
            baseline = {"date": x.get("reg_date"), "type": rtype, "holder": x.get("holder") or ""}

    # ── 임차인 ──
    tenants: list[dict] = []
    assumed_total = 0
    for x in tenants_raw:
        st = x.get("status") or ""
        deposit = x.get("deposit") or 0
        assume = 0
        if "인수" in st:                                # speedauction이 '인수예상'으로 판정한 경우
            assume = x.get("assume_amount") if x.get("assume_amount") is not None else deposit
        # 배당판정 요약(label)과 설명(comment) 분리.
        #  상태문 형태: '<배당판정> <임차인명>: <설명>'  (예: '일부배당 미배당금 인수)예상 김영제: ...')
        #  → 임차인명(없으면 첫 ':') 앞을 한 줄 요약 라벨로, 그 뒤를 설명으로. 단어 중간 분리·가짜 괄호 금지.
        name = x.get("name") or ""
        cut = st.index(name) if (name and name in st) else (st.index(":") if ":" in st else -1)
        label = st[:cut].strip() if cut > 0 else st.strip()
        comment = st[cut:].strip() if cut > 0 else ""
        if label.count(")") > label.count("("):     # 짝 안 맞는 닫는 괄호 제거('인수)예상'→'인수예상')
            label = label.replace(")", "")
        label = re.sub(r"\s+", " ", label).strip()
        tenants.append({
            "name": x.get("name"), "fixed": x.get("fixed_date"), "assume": assume,
            "reason": st, "deposit": deposit, "move_in": x.get("move_in_date"),
            "demanded": bool(x.get("dividend_date")), "demand_date": x.get("dividend_date"),
            "has_opposing_power": bool(x.get("has_opposing_power")),
            "tenant_right": x.get("tenant_right"), "occupancy": x.get("occupancy"),
            "dividend_amount": x.get("dividend_amount"),
            "undistributed_amount": x.get("undistributed_amount"),
            "status_label": label, "comment": comment,
        })
        assumed_total += assume or 0

    occ = next((t.get("occupancy") for t in tenants_raw if t.get("occupancy")), "")
    deposit_total = sum((x.get("deposit") or 0) for x in tenants_raw)

    detail = parse_detail_text(it.get("detail_text") or "")
    # ── 말소동의·대항력 포기 확약서 제출 → 미배당 보증금 매수인 인수 면제(AI 반영) ──
    waiver = _detect_waiver(it.get("detail_text") or "")
    if not waiver and "인수조건변경" in (it.get("tags") or ""):   # 보증기관(HUG/SGI/HF) 인수조건변경 태그 = 임차보증금 인수 면제
        waiver = "보증기관(HUG·SGI·HF) 인수조건변경 — 임차보증금 미배당분을 낙찰자가 인수하지 않음"
    waived_total = 0
    if waiver:
        assumed_ts = [t for t in tenants if t["assume"]]
        # 확약서에 이름이 명시된 임차인 우선, 매칭 없고 인수예상 임차인이 1명뿐이면 그 임차인 면제
        matched = [t for t in assumed_ts if t.get("name") and t["name"] in waiver]
        # 이름 명시 시 그 임차인만, 아니면(일반 포기·특별매각조건) 인수예상 임차인 전원 면제
        targets = matched if matched else assumed_ts
        for t in targets:
            t["assume_waived"] = t["assume"]
            t["assume"] = 0
            t["waiver"] = True
            waived_total += t["assume_waived"]
        assumed_total = sum(t["assume"] for t in tenants)

    # ── 대항력 자체 판정(크롤러 DB has_opposing_power가 놓쳐도 전입일로 보정) ──
    #  주택임차인은 전입일(익일 0시) 대항요건이 말소기준일보다 빠르면 대항력 있음.
    #  크롤러가 대항력을 놓친 경우(주택임차권등기·보증금승계 등)에도 전입일로 안전하게 재판정.
    from datetime import date as _date
    def _pd(s):
        try:
            return _date.fromisoformat(str(s)[:10])
        except Exception:
            return None
    _bd = _pd(baseline["date"]) if baseline else None
    for t in tenants:
        mv = _pd(t.get("move_in"))
        if mv and _bd and mv < _bd and not t.get("has_opposing_power"):
            t["has_opposing_power"] = True     # 전입 < 말소기준 → 대항력(보정)
    # 대항력 임차인 인수보증금: 대항력이 있으면 낙찰가로 배당이 부족할 때 미배당분을 매수인이 인수한다.
    #  크롤러가 인수액(assume)을 안 잡은 경우, 보증금을 '최대 인수액'으로 보수적으로 반영(→ 위험 판정).
    #  배당요구·확정일자가 있으면 낙찰가에 따라 배당으로 상당분 회수 가능(부족분만 실제 인수) — 경고로 안내.
    for t in tenants:
        if t.get("has_opposing_power") and t.get("deposit") and not t.get("assume"):
            t["assume"] = t["deposit"]
            assumed_total += t["deposit"]

    # ── 위험도(사이트 자체 지표) ──
    #  확약서로 인수가 제거된 대항력 임차인은 실제 리스크가 없으므로 '주의' 판정에서 제외.
    has_assume = assumed_total > 0 or any(r["status"] == "인수" for r in rights)
    has_opp = any(t["has_opposing_power"] and not t.get("waiver") for t in tenants)
    risk = "위험" if has_assume else ("주의" if has_opp else "안전")

    # 다세대·도시형 4층↑ 승강기 없음/미상 → 매수세 조사 필요(매수검토 수준). 안전이면 주의로 상향.
    _ev_caution = elevator_caution(it.get("usage_name"), it.get("address"), _elev)
    if _ev_caution and risk == "안전":
        risk = "주의"

    warnings: list[str] = []
    if assumed_total > 0:
        _demanded_opp = any(t.get("has_opposing_power") and t.get("assume") and t.get("demanded") for t in tenants)
        _msg = f"대항력 임차인 보증금 최대 {assumed_total:,}원 매수인 인수 가능"
        if _demanded_opp:
            _msg += " (배당요구·확정일자가 있어 낙찰가에 따라 배당으로 일부 회수, 부족분만 실제 인수 — 정밀 배당 검토 필요)"
        warnings.append(_msg)
    if _ev_caution:
        warnings.append(_ev_caution)
    # 확약서(인수 면제)는 매수인에게 유리한 정보 → 경고(warnings)가 아닌 별도 waiver 필드로 노출(프런트가 ✓로 표시)

    return {
        "available": True,
        "source": "crawler",
        "rights": rights,
        "tenants": tenants,
        "baseline": baseline,
        "risk_level": risk,
        "assumed_amount_total": assumed_total,
        "warnings": warnings,
        "occupancy": occ,
        "occupancy_notes": [],
        "occupancy_available": bool(tenants_raw),
        "deposit_total": deposit_total,
        "building": {},
        "dividend_deadline": it.get("dividend_deadline"),
        "needs_expert_review": risk != "안전",
        "senior_setup": baseline["date"] if baseline else None,
        "waiver": waiver,                 # 말소동의·대항력포기 확약서 문장(있으면 인수 면제 근거)
        "waived_total": waived_total,
        # ── 4컬럼 표시용 추가 데이터 ──
        "detail": detail,
        "appraisal": {
            "total": it.get("appraisal_price"),
            "land": it.get("appraisal_land"), "building": it.get("appraisal_building"),
            "land_pct": it.get("appraisal_land_pct"), "building_pct": it.get("appraisal_building_pct"),
            "price_date": detail.get("price_date"),
        },
        "land_area": it.get("land_area"), "building_area": it.get("building_area"),
        "area_text": it.get("area_text"), "address": it.get("address"),
        "usage": it.get("usage_name"),
    }
