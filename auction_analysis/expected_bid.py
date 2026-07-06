# -*- coding: utf-8 -*-
"""아파트 예상낙찰가 — 동일건물 과거 매각사례 평균(사용자 공식, 2026-06-27).
백데이터 = items 테이블의 매각완료 행(result=매각*, sale_price>0). 별도 테이블 아님.
필터: ①동일건물(주소 prefix 일치) 매각기일 직전 3년 ②전용 ±6㎡ ③감정가 ±2,500만 ④층 군(1~6/7층이상)
     ⑤낙찰가율 ≤60% 제외 → ⑥남은 사례 낙찰가(금액) 단순평균 = 예상낙찰가. 차익 = 추정시세 − 예상낙찰가."""
import re
import math
from datetime import date

_APR_TOL = 25_000_000      # ③ 감정가 ±2,500만원
_AREA_TOL = 6.0            # ② 전용 ±6㎡
_RATE_MIN = 60.0           # ⑤ 낙찰가율 60% 이하 제외(초과만 사용)
_YEARS = 3                 # ① 매각기일 직전 3년


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def _exclusive_m2(it):
    """전용면적 ㎡ — area_text '전용 NN' 우선, building_area 'NN㎡' 폴백."""
    m = re.search(r"전용\s*([0-9.]+)", it.get("area_text") or "")
    if not m:
        m = re.search(r"([0-9.]+)\s*(?:㎡|m²|m2)", it.get("building_area") or "")
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _floor(addr):
    """주소의 '...NN층...' → 층(int). 예 '105동 17층1704호' → 17."""
    m = re.search(r"(\d+)\s*층", addr or "")
    return int(m.group(1)) if m else None


def _floor_bracket(f):
    """④ 유사 층수 군: 1~6층=0 / 7층 이상=1."""
    if f is None:
        return None
    return 0 if f <= 6 else 1


def _sale_rate(it):
    m = re.search(r"([0-9.]+)", str(it.get("sale_rate") or ""))
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def _sell_date(it):
    """sell_date 'YYYY-MM-DD ...' → date. 실패 None."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", (it.get("sell_date") or "")[:10])
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def building_key(addr):
    """주소 → (건물주소 prefix, '법정동 지번'). 동/층/호 앞까지가 건물주소(동일건물 매칭용),
    '법정동 지번'은 조회용 ilike 키(예 '용전리 696')."""
    if not addr:
        return None, None
    pre = re.split(r"\s+(?:제?\d+동|지하\s*\d*층|제?\d+층|[Bb]\d+|\d+호)", addr)[0].strip()
    m = re.search(r"([가-힣]+(?:동|리|가))\s+(\d+(?:-\d+)?)", pre)
    bunji = (m.group(1) + " " + m.group(2)) if m else None
    return pre, bunji


def compute(cur, cases, est_price=None):
    """cur=현재 물건 dict(get_auction), cases=동일건물 후보 매각사례 list, est_price=추정시세(원).
    반환: {available, expected_bid, count, excluded_low, est?, profit?} 또는 {available:False, reason}."""
    cur_pre, _ = building_key(cur.get("address"))
    cur_pre = _norm(cur_pre)
    cur_apr = _to_int(cur.get("appraisal_price"))
    cur_area = _exclusive_m2(cur)
    cur_floor = _floor(cur.get("address"))
    cur_sell = _sell_date(cur) or date.today()
    if not (cur_pre and cur_apr and cur_area and cur_floor is not None):
        return {"available": False, "reason": "현재 물건 주소/감정가/전용/층 누락"}
    cur_br = _floor_bracket(cur_floor)
    try:
        yr3 = date(cur_sell.year - _YEARS, cur_sell.month, cur_sell.day)
    except ValueError:
        yr3 = date(cur_sell.year - _YEARS, cur_sell.month, 28)

    prices, excluded_low, used, seen = [], 0, [], set()
    for c in cases:
        ik = c.get("item_key")
        if ik and ik == cur.get("item_key"):
            continue
        if ik:
            if ik in seen:                                              # 같은 물건(item_key) 중복 사례는 1회만(페이지네이션 중복 방어)
                continue
            seen.add(ik)
        if _norm(building_key(c.get("address"))[0]) != cur_pre:          # 동일 건물만
            continue
        sp = _to_int(c.get("sale_price"))
        ca = _to_int(c.get("appraisal_price"))
        if not sp or sp <= 0 or not ca:
            continue
        sd = _sell_date(c)                                               # ① 직전 3년
        if not sd or sd < yr3 or sd > cur_sell:
            continue
        ar = _exclusive_m2(c)                                            # ② 전용 ±6㎡
        if ar is None or abs(ar - cur_area) > _AREA_TOL:
            continue
        if abs(ca - cur_apr) > _APR_TOL:                                 # ③ 감정가 ±2,500만
            continue
        cfloor = _floor(c.get("address"))
        if _floor_bracket(cfloor) != cur_br:                            # ④ 층 군
            continue
        rate = _sale_rate(c)                                             # ⑤ 낙찰가율 ≤60% 제외
        if rate is None:
            continue
        if rate <= _RATE_MIN:
            excluded_low += 1
            continue
        prices.append(sp)
        used.append({"item_key": c.get("item_key"), "address": c.get("address"),
                     "area": ar, "floor": cfloor, "appraisal": ca,
                     "sale_price": sp, "sale_rate": rate, "sell_date": str(sd),
                     "bid_count": c.get("bid_count"), "sale_2nd": _to_int(c.get("sale_2nd_price"))})

    if not prices:
        return {"available": False, "reason": "조건 만족 사례 없음", "excluded_low": excluded_low}
    exp = round(sum(prices) / len(prices))                               # ⑥ 단순 평균
    used.sort(key=lambda u: u["sell_date"], reverse=True)
    out = {"available": True, "expected_bid": exp, "count": len(prices),
           "excluded_low": excluded_low, "cases_used": used}
    e = _to_int(est_price)
    if e:
        out["est"] = e
        out["profit"] = e - exp                                          # 차익 = 시세 − 예상낙찰가
    return out


# ── 빌라/도시형생활주택 예상낙찰가(반경 1km, 동일건물 아님) ──
_VILLA_APR_TOL = 15_000_000    # 감정가 ±1,500만원
_VILLA_FLOOR_TOL = 1           # 층 ±1개층(층군 아님)
_VILLA_RADIUS_M = 1000.0       # 반경 1km
_VILLA_RATE_MAX = 100.0        # 낙찰가율 100% 이상 제외(미만만 사용)


def _haversine_m(lng1, lat1, lng2, lat2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def geo_addr(addr):
    """지오코딩용 지번 주소(시도…법정동 번지까지; 단지명·동·층·호 제거)."""
    if not addr:
        return None
    m = re.search(r"[가-힣]+(?:동|읍|면|리|가)\s+(?:산\s*)?\d+(?:-\d+)?", addr)
    return addr[:m.end()].strip() if m else None


def compute_villa(cur, cur_ll, cases, est_price=None):
    """빌라/도생 예상낙찰가 — 반경 1km + 면적±6㎡ + 층±1 + 감정가±1,500만 + 낙찰가율<100% 평균.
    cur_ll=(lng,lat) 대상 좌표. cases=각 dict에 'll':(lng,lat) 포함한 낙찰완료 사례."""
    if not cur_ll:
        return {"available": False, "reason": "대상 좌표 없음"}
    cur_apr = _to_int(cur.get("appraisal_price"))
    cur_area = _exclusive_m2(cur)
    cur_floor = _floor(cur.get("address"))
    cur_sell = _sell_date(cur) or date.today()
    if not (cur_apr and cur_area and cur_floor is not None):
        return {"available": False, "reason": "현재 물건 감정가/전용/층 누락"}
    try:
        yr3 = date(cur_sell.year - _YEARS, cur_sell.month, cur_sell.day)
    except ValueError:
        yr3 = date(cur_sell.year - _YEARS, cur_sell.month, 28)
    clng, clat = cur_ll[0], cur_ll[1]
    prices, excluded_high, used, seen = [], 0, [], set()
    for c in cases:
        ik = c.get("item_key")
        if ik and ik == cur.get("item_key"):
            continue
        if ik:
            if ik in seen:                                              # 같은 물건(item_key) 중복 사례 1회만
                continue
            seen.add(ik)
        ll = c.get("ll")
        if not ll:
            continue
        if _haversine_m(clng, clat, ll[0], ll[1]) > _VILLA_RADIUS_M:    # ① 반경 1km
            continue
        sp = _to_int(c.get("sale_price"))
        ca = _to_int(c.get("appraisal_price"))
        if not sp or sp <= 0 or not ca:
            continue
        sd = _sell_date(c)                                              # ② 직전 3년
        if not sd or sd < yr3 or sd > cur_sell:
            continue
        ar = _exclusive_m2(c)                                          # ③ 면적 ±6㎡
        if ar is None or abs(ar - cur_area) > _AREA_TOL:
            continue
        cf = _floor(c.get("address"))                                  # ④ 층 ±1
        if cf is None or abs(cf - cur_floor) > _VILLA_FLOOR_TOL:
            continue
        if abs(ca - cur_apr) > _VILLA_APR_TOL:                         # ⑤ 감정가 ±1,500만
            continue
        rate = _sale_rate(c)                                           # ⑥ 낙찰가율 100%↑ 제외
        if rate is None:
            continue
        if rate >= _VILLA_RATE_MAX:
            excluded_high += 1
            continue
        prices.append(sp)
        used.append({"item_key": ik, "address": c.get("address"),
                     "area": ar, "floor": cf, "appraisal": ca,
                     "sale_price": sp, "sale_rate": rate, "sell_date": str(sd),
                     "bid_count": c.get("bid_count"), "sale_2nd": _to_int(c.get("sale_2nd_price"))})
    if not prices:
        return {"available": False, "reason": "조건 만족 사례 없음", "excluded_high": excluded_high}
    exp = round(sum(prices) / len(prices))                             # ⑦ 단순 평균
    used.sort(key=lambda u: u["sell_date"], reverse=True)
    out = {"available": True, "expected_bid": exp, "count": len(prices),
           "excluded_high": excluded_high, "cases_used": used}
    e = _to_int(est_price)
    if e:
        out["est"] = e
        out["profit"] = e - exp
    return out
