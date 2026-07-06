# -*- coding: utf-8 -*-
"""다가구·상가주택 경매 분석 — 다가구 투자반 강의 기준 적용(2026-06-28).

제공:
  ① 다가구 3요건 체크리스트(연면적 660㎡·19가구·주택 3개층) — 부합/탈락 + 현황값 + 사유
  ② 우량 판별(감정가 ≤ 임차보증금합 + 근저당) — 수요 반증
  ③ 임대수요·명도 신호(배당요구 수 / 가구수)
  ④ 위반건축물 플래그

순수 로직만 둔다. 데이터(item·brief·보증금합·배당요구수)는 호출부(api/main.py)가 모아 전달.
수익률·대출·방공제·매도가 역산은 프론트 계산기(입력 기반)에서 처리한다.
"""
import re

_AREA_MAX = 660.0      # ① 주택 연면적 660㎡ 이하
_UNITS_MAX = 19        # ② 19가구 이하
_FLOORS_MAX = 3        # ③ 주택 3개 층 이하
_PY = 3.305785         # 1평 = 3.3058㎡


def _to_int(v):
    if v is None:
        return None
    try:
        s = re.sub(r"[^0-9.\-]", "", str(v))      # '45,700,000원' → '45700000'
        return int(float(s)) if s not in ("", "-", ".", "-.") else None
    except (TypeError, ValueError):
        return None


def _won(v):
    v = int(v or 0)
    if not v:
        return "0원"
    eok, man = v // 100_000_000, (v % 100_000_000) // 10_000
    s = ""
    if eok:
        s += f"{eok}억 "
    if man:
        s += f"{man:,}만"
    return (s or f"{v:,}").strip() + "원"


def bldg_area_m2(area_text):
    """area_text '토지 157m² / 건물 354.10m² ...' → 건물(연면적) ㎡. 없으면 None."""
    if not area_text:
        return None
    m = re.search(r"건물\s*([0-9,]+(?:\.[0-9]+)?)", area_text) \
        or re.search(r"연면적\s*([0-9,]+(?:\.[0-9]+)?)", area_text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _is_mixed(purpose):
    """상가주택(근린생활시설 혼합) 여부 — 주용도에 근린/제1·2종."""
    p = purpose or ""
    return ("근린" in p) or ("제1종" in p) or ("제2종" in p) or ("1종" in p) or ("2종" in p)


def three_requirements(area_text=None, units=None, floors=None, purpose=None):
    """다가구 3요건 체크리스트. 각 항목 {key,label,std,actual,ok,reason}.
    ok = True(부합) / False(탈락) / None(확인 필요)."""
    items = []
    mixed = _is_mixed(purpose)

    # ① 주택 연면적 660㎡ 이하
    ar = bldg_area_m2(area_text)
    if ar is None:
        items.append({"key": "area", "label": "주택 연면적 660㎡ 이하", "std": "≤ 660㎡",
                      "actual": "미상", "ok": None,
                      "reason": "건물 연면적 정보가 없어 확인 불가(감정평가서 면적 미수집)."})
    else:
        ok = ar <= _AREA_MAX
        tail = "" if not mixed else " ※상가주택이라 근린 면적을 뺀 '주택 면적'은 더 작을 수 있음(정밀은 층별 면적 확인)"
        items.append({"key": "area", "label": "주택 연면적 660㎡ 이하", "std": "≤ 660㎡",
                      "actual": f"{ar:.1f}㎡ ({ar/_PY:.1f}평)", "ok": ok,
                      "reason": (f"건물 연면적 {ar:.1f}㎡ → 660㎡ 이하라 부합" if ok
                                 else f"건물 연면적 {ar:.1f}㎡ → 660㎡ 초과라 탈락") + tail})

    # ② 19가구 이하
    if not units:
        items.append({"key": "units", "label": "19가구 이하", "std": "≤ 19가구",
                      "actual": "미상", "ok": None,
                      "reason": "건축물대장 가구수 정보가 없어 확인 불가."})
    else:
        ok = units <= _UNITS_MAX
        items.append({"key": "units", "label": "19가구 이하", "std": "≤ 19가구",
                      "actual": f"{units}가구", "ok": ok,
                      "reason": f"건축물대장상 {units}가구 → 19가구 {'이하라 부합' if ok else '초과라 탈락(불법 쪼개기 의심)'}"})

    # ③ 주택 3개 층 이하 (지상 전체 층수 기준 — 상가·계단실·지하 제외분은 별도 확인)
    if not floors:
        items.append({"key": "floors", "label": "주택 3개 층 이하", "std": "≤ 3개 층",
                      "actual": "미상", "ok": None,
                      "reason": "건축물대장 층수 정보가 없어 확인 불가."})
    elif floors <= _FLOORS_MAX:
        items.append({"key": "floors", "label": "주택 3개 층 이하", "std": "≤ 3개 층",
                      "actual": f"지상 {floors}층", "ok": True,
                      "reason": f"지상 {floors}층 → 3개 층 이하라 부합"})
    else:
        # 지상 총층이 4 이상이어도 1층 상가·계단실·지하는 주택 층수에서 빠지므로 '확인 필요'
        items.append({"key": "floors", "label": "주택 3개 층 이하", "std": "≤ 3개 층",
                      "actual": f"지상 {floors}층", "ok": None,
                      "reason": f"지상 {floors}층 → 1층 상가·계단실·지하는 주택 층수에서 제외되므로, "
                                f"층별 용도로 '주택으로 쓰는 층'이 3개 층 이하인지 확인 필요"
                                + (" (상가주택)" if mixed else "")})

    fails = [i for i in items if i["ok"] is False]
    unknowns = [i for i in items if i["ok"] is None]
    verdict = "탈락 가능" if fails else ("확인 필요" if unknowns else "3요건 충족")
    return {"verdict": verdict, "items": items,
            "note": "3요건을 모두 충족하면 19가구를 둬도 1가구 1주택으로 인정(양도 비과세 등). "
                    "하나라도 위반이면 다주택으로 과세될 수 있고, 위반건축물은 비과세도 깨질 수 있음."}


def woolyang(appraisal, deposit_sum, claim_amount):
    """우량 판별(강의 3주차): 감정가 ≤ 임차보증금합 + 근저당(청구) → 후순위 임차·대출이
    감정가에 육박 = 임대수요가 많다는 반증."""
    appraisal = _to_int(appraisal)
    if not appraisal:
        return None
    dep = _to_int(deposit_sum) or 0
    cl = _to_int(claim_amount) or 0
    if not dep and not cl:
        return {"ok": None, "reason": "임차보증금·채권(근저당) 정보가 없어 판단 불가."}
    total = dep + cl
    ratio = round(total / appraisal * 100)
    ok = total >= appraisal
    return {"ok": ok, "deposit_sum": dep, "claim": cl, "total": total,
            "appraisal": appraisal, "ratio": ratio,
            "reason": (f"임차보증금 {_won(dep)} + 채권(근저당) {_won(cl)} = {_won(total)} 로 "
                       f"감정가 {_won(appraisal)}의 {ratio}% — "
                       + ("후순위 임차·대출이 감정가에 육박/초과 → 임대수요가 많다는 반증(우량 가능성)"
                          if ok else "감정가 대비 부채가 낮은 편"))}


def demand_signal(baedang_count, units, tenant_count):
    """임대수요·명도 신호(강의 2·3주차):
    - 배당요구/임차 가구가 적으면 임대수요가 약하다는 신호
    - 배당요구(배당받는) 가구가 많으면 명도가 수월(명도확인서 필요해 협조적)."""
    units = _to_int(units)
    bc = _to_int(baedang_count) or 0
    tc = _to_int(tenant_count) or 0
    occ = max(bc, tc)                      # 임차 가구 근사(배당요구 또는 명세서 임차인)
    if not units:
        return {"level": None, "baedang": bc, "tenants": tc,
                "reason": "가구수 정보가 없어 수요 비율 판단 불가."}
    ratio = min(100, round(occ / units * 100)) if units else 0
    if ratio >= 60:
        level, msg = "good", f"임차 {occ}/{units}가구({ratio}%)로 채워짐 → 임대수요 양호. 배당받는 가구는 명도확인서가 필요해 명도도 수월한 편."
    elif ratio >= 30:
        level, msg = "mid", f"임차 {occ}/{units}가구({ratio}%) → 보통. 공실/수요는 현장(계량기·전화임장)으로 교차확인 권장."
    else:
        level, msg = "low", f"임차/배당요구 {occ}/{units}가구({ratio}%)로 적음 → 임대수요가 약할 수 있음(원룸 시세·공실 점검 필요)."
    return {"level": level, "baedang": bc, "tenants": tc, "units": units, "ratio": ratio, "reason": msg}


def violation_flag(detail_text, purpose=None, tags=None, doc_violation=False):
    """위반건축물(강의 1·2주차): 대출 난항·이행강제금·1주택 비과세 깨짐.
    doc_violation=건축물대장 문서 스탬프 기반(brief.violation) — 명세서/tags에 안 적혀도 대장상 위반이면 잡음."""
    txt = f"{detail_text or ''} {tags or ''}"
    viol = ("위반건축물" in txt) or ("위반 건축물" in txt) or bool(doc_violation)
    return {"violation": viol,
            "reason": ("명세서/건축물대장상 위반건축물 표시 → 대출 난항·이행강제금·1주택 비과세 깨질 수 있음"
                       "(원상복구 가능 여부·이행강제금 확인). 단 위반은 미등재 경우도 있어 현장·대장 교차확인 필요."
                       if viol else "")}


def analyze(item, brief=None, deposit_sum=0, baedang_count=0, tenant_count=0):
    """종합 — 호출부가 모은 데이터로 다가구 분석 묶음 반환."""
    item = item or {}
    brief = brief or {}
    units = brief.get("units") or brief.get("households")
    units = _to_int(units)
    floors = _to_int(brief.get("floors"))
    purpose = brief.get("purpose")
    return {
        "three_req": three_requirements(item.get("area_text"), units, floors, purpose),
        "woolyang": woolyang(item.get("appraisal_price"), deposit_sum, item.get("claim_amount")),
        "demand": demand_signal(baedang_count, units, tenant_count),
        "violation": violation_flag(item.get("detail_text"), purpose, item.get("tags"),
                                    doc_violation=brief.get("violation")),
        "purpose": purpose, "floors": floors, "units": units,
    }
