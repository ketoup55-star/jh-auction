# -*- coding: utf-8 -*-
"""상가 계량분석 — 강의 6편 기반 '이 상가 얼마짜리' 계산기(지도 아닌 숫자).
배후세대(질보정)·가능업종·평당임대료→월세→시세→감정가대비·허가권·경고. 모든 수치는 추정(강의 공식)."""
import re
import math


def _hav(la1, lo1, la2, lo2):
    R = 6371000.0
    p = math.radians
    return 2 * R * math.asin(math.sqrt(
        math.sin((p(la2) - p(la1)) / 2) ** 2
        + math.cos(p(la1)) * math.cos(p(la2)) * math.sin((p(lo2) - p(lo1)) / 2) ** 2))


_RENTAL_RE = re.compile(r"(국민임대|공공임대|행복주택|영구임대|장기전세|LH|SH|매입임대|전세임대)")


def parse_floor(address):
    """주소에서 층 추출. 지하=음수, 못 찾으면 None."""
    if not address:
        return None
    m = re.search(r"지하\s*(\d+)\s*층", address) or re.search(r"[Bb](\d+)", address)
    if m:
        return -int(m.group(1))
    m = re.search(r"(\d+)\s*층", address)
    if m:
        return int(m.group(1))
    return None


def parse_pyeong(text):
    """'129.20㎡ (39.08평)' 또는 '...39.08평...' 에서 평 추출."""
    if not text:
        return None
    m = re.search(r"([\d,.]+)\s*평", text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except Exception:
            pass
    m = re.search(r"([\d,.]+)\s*[㎡m²]", text)        # ㎡만 있으면 환산
    if m:
        try:
            return round(float(m.group(1).replace(",", "")) / 3.3058, 2)
        except Exception:
            pass
    return None


def _num(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"[\d,]+", str(s))
    return float(m.group(0).replace(",", "")) if m else None


def _rent_per_pyeong(households, floor):
    """1층 기준 평당임대료(만원) 힌트 — 강의 앵커(수도권 프라임 1만세대=20만)의 전국평균치(약 ½).
    수도권↔지방 2배 차이 + 입지 편차 커서 반드시 사용자가 현장값으로 수정. 층 보정. ※거친 추정."""
    h = households or 0
    if h >= 10000:
        base = 10.0
    elif h >= 5000:
        base = 8.0
    elif h >= 2500:
        base = 6.0
    elif h >= 1500:
        base = 5.0
    elif h >= 700:
        base = 4.0
    else:
        base = 3.0
    if floor is None:
        m = 0.5
    elif floor == 1:
        m = 1.0
    elif floor == 2:
        m = 0.5
    elif floor >= 3:
        m = 0.4
    else:
        m = 0.4          # 지하
    return round(base * m, 1)


# 업종별 필요 배후세대(강의)
_BIZ = [
    (700, ["편의점", "미용실", "세탁소", "부동산", "교습소"]),
    (2500, ["빵집", "학원", "의원", "일반음식점", "근린상권 형성"]),
    (10000, ["다이소", "올리브영"]),
]


def compute_analysis(item, lat, lng, backers, schools, cvs_near, in_commercial, resi_dominant):
    """item=물건dict · backers=[{lng,lat,hh,name}] · schools/cvs_near=[(lng,lat,name,dist_m)].
    배후세대=아파트 kapt 실세대수 기준(신뢰). 평당임대료는 프런트에서 사용자 입력 → 시세 계산."""
    out = {"available": True}

    # ── ① 배후세대(1km) — 아파트=실세대수(신뢰) / 택지=면적환산(참고·과대가능) 분리 ──
    R = 1000.0
    apt = [b for b in backers if b.get("hh") and (b.get("name") or "") != "택지"]
    takji = [b for b in backers if b.get("hh") and (b.get("name") or "") == "택지"]
    near = lambda lst: [b for b in lst if _hav(lat, lng, b["lat"], b["lng"]) <= R]
    apt_hh = sum(int(b["hh"]) for b in near(apt))
    takji_hh = sum(int(b["hh"]) for b in near(takji))
    rentals = [b for b in near(apt) if _RENTAL_RE.search(b.get("name", "") or "")]
    rental_hh = sum(int(b["hh"]) for b in rentals)
    total_hh = apt_hh + takji_hh                            # 배후 합계(아파트+주택단지 모두 수요)
    base_hh = max(0, total_hh - int(rental_hh * 0.5))       # 질보정(임대 절반 차감)
    out["backers"] = {
        "apt_hh": apt_hh, "takji_hh": takji_hh, "total_hh": total_hh,
        "rental_hh": rental_hh, "rental_names": [b.get("name") for b in rentals][:6],
        "rental_pct": round(rental_hh / total_hh * 100) if total_hh else 0,
        "base_hh": base_hh,
    }
    school_near = any(d <= 500 for (_x, _y, _n, d) in schools)

    # ── ② 가능 업종(아파트 기준 보수) ──
    ok, edge, no = [], [], []
    for thr, biz in _BIZ:
        for b in biz:
            need_school = b in ("교습소", "학원", "의원")
            if base_hh >= thr and (not need_school or school_near):
                ok.append(b)
            elif base_hh >= thr * 0.8 and (not need_school or school_near):
                edge.append(b)
            else:
                no.append(b)
    out["biz"] = {"ok": ok, "edge": edge, "no": no, "school_near": school_near, "base_hh": base_hh}

    # ── ③ 시세 계산 입력값(평당임대료는 프런트 사용자 입력, 강의공식은 힌트) ──
    floor = parse_floor(item.get("address"))
    pyeong = parse_pyeong(item.get("building_area")) or parse_pyeong(item.get("area_text"))
    appraisal = _num(item.get("appraisal_price")) or _num(item.get("min_price"))
    out["price"] = {
        "floor": floor, "pyeong": pyeong,
        "appraisal_man": round(appraisal / 10000) if appraisal else None,
        "rpp_hint": _rent_per_pyeong(base_hh, floor),       # 강의공식 추정(입력칸 기본값)
        "yield_default": 6,                                 # 지방 상가 기본 수익률(%) — 수정 가능
    }

    # ── ④ 허가권 ──
    sch_d = min([d for (_x, _y, _n, d) in schools], default=9999)
    cvs_d = min([d for (_x, _y, _n, d) in cvs_near], default=9999)
    out["license"] = {
        "edu_zone": ("절대보호구역(50m내)" if sch_d <= 50 else "상대보호구역(200m내)" if sch_d <= 200 else "해당없음"),
        "edu_restrict": (sch_d <= 200),       # 유흥·숙박·PC방 제한
        "school_dist_m": int(sch_d) if sch_d < 9999 else None,
        "cigarette": ("신규 어려움(기존 편의점 %dm)" % int(cvs_d)) if cvs_d <= 100 else "신규 가능성",
        "cvs_dist_m": int(cvs_d) if cvs_d < 9999 else None,
        "zone": ("상업지역" if in_commercial else "비상업(주거 등)" if in_commercial is False else "미상"),
        "lodging_new": ("가능성" if in_commercial else "어려움"),   # 숙박·유흥 신규
    }

    # ── ⑤ 경고 ──
    warn = []
    usage = item.get("usage") or ""
    if re.search(r"위락|유흥|숙박|단란", usage) and resi_dominant:
        warn.append("주거지 위락 — 배후가 주거인데 위락·숙박 업종이라 수요 불일치(강의 위험)")
    if floor == 1 and base_hh < 700:
        warn.append("1층 맹신 주의 — 1층이어도 배후수요가 약해 공실 위험(강의: 3등 입지 1층 위험)")
    if out["backers"]["rental_pct"] >= 25:
        warn.append("배후 質 — 임대단지 비중 %d%%로 소비력·학원수요 약할 수 있음" % out["backers"]["rental_pct"])
    if base_hh < 2500:
        warn.append("근린상권 형성선(2,500세대) 미달 — 빵집·학원·병원 등 업종 제한적")
    # 흐른입지·후면은 동선·현장 판단 필요 → 체크리스트로
    out["warnings"] = warn
    out["manual_check"] = ["흐른 입지 여부(대로변인데 안쪽 독립상권?)", "전면/후면(주동선에 걸쳤나)",
                           "관통도로·코너", "현장 답사(지도와 실제 동선 차이)"]
    return out
