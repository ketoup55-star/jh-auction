# -*- coding: utf-8 -*-
"""차량 예상낙찰가 — 백데이터(과거 낙찰) 사례 중 **같은 제조사+모델·연식±1·주행거리±20%·최근3년**
낙찰가의 **중앙값**. 아파트/빌라 expected_bid와 같은 철학, 매칭 기준만 차량 스펙.

target/cases 항목(dict)에 필요한 키:
  manufacturer, model, model_year, mileage_km   (vehicle_specs)
  sale_price, sell_date, bid_count, sale_2nd_price, case_no, item_key   (items, cases에만)
"""
import re
import datetime
import statistics

YEAR_TOL = 1          # 연식 ±1년
MILEAGE_TOL = 0.20    # 주행거리 ±20%
RECENT_YEARS = 3      # 최근 3년 낙찰만
MIN_CASES = 2         # 최소 사례 수


def norm_model(model, manufacturer):
    """model 필드 뒤에 잘못 붙은 제조사 꼬리 제거. 예: 'K9기아자동차'→'K9', 'BMW M5 …PackagBMW'→'BMW M5 …Packag'."""
    m = (model or "").strip()
    mf = (manufacturer or "").strip()
    if mf and m.endswith(mf):
        m = m[: len(m) - len(mf)].strip()
    return m


def _date(s):
    m = re.search(r"\d{4}-\d{2}-\d{2}", s or "")
    return m.group(0) if m else None


def compute(target: dict, cases: list, today: str = None) -> dict:
    """target=현황 차량 스펙, cases=백데이터 낙찰사례 리스트.
    반환: {available, expected_bid, count, cases_used, reason}. cases_used는 관리자만 노출(호출부 gating)."""
    mf = target.get("manufacturer")
    yr = target.get("model_year")
    km = target.get("mileage_km")
    tmodel = norm_model(target.get("model"), mf)
    if not (mf and yr and km and tmodel):
        return {"available": False, "reason": "차량 스펙 부족(제조사·모델·연식·주행)"}

    lo_km, hi_km = km * (1 - MILEAGE_TOL), km * (1 + MILEAGE_TOL)
    cutoff = None
    if today:
        try:
            cutoff = (datetime.date.fromisoformat(today)
                      - datetime.timedelta(days=RECENT_YEARS * 365)).isoformat()
        except Exception:
            cutoff = None

    matched = []
    for c in cases:
        if c.get("manufacturer") != mf:
            continue
        if norm_model(c.get("model"), c.get("manufacturer")) != tmodel:
            continue
        cy = c.get("model_year")
        if not cy or abs(cy - yr) > YEAR_TOL:
            continue
        ck = c.get("mileage_km")
        if not ck or not (lo_km <= ck <= hi_km):
            continue
        if not c.get("sale_price"):
            continue
        if cutoff:
            d = _date(c.get("sell_date"))
            if d and d < cutoff:
                continue
        matched.append(c)

    if len(matched) < MIN_CASES:
        return {"available": False, "count": len(matched),
                "reason": f"유사 낙찰사례 부족({len(matched)}건 · 최소 {MIN_CASES})"}

    prices = [c["sale_price"] for c in matched]
    exp = int(statistics.median(prices))                       # ★중앙값(이상치 완화)
    matched.sort(key=lambda c: c.get("sale_price") or 0)
    cases_used = [{
        "case_no": c.get("case_no"), "item_key": c.get("item_key"),
        "manufacturer": c.get("manufacturer"),
        "model": norm_model(c.get("model"), c.get("manufacturer")),
        "model_year": c.get("model_year"), "mileage_km": c.get("mileage_km"),
        "sale_price": c.get("sale_price"), "sell_date": _date(c.get("sell_date")),
        "bid_count": c.get("bid_count"), "sale_2nd_price": c.get("sale_2nd_price"),
    } for c in matched]
    return {"available": True, "expected_bid": exp, "count": len(matched),
            "cases_used": cases_used, "median": True}
