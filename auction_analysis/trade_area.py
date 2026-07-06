# -*- coding: utf-8 -*-
"""상권 영역(필지) — 경매물건이 속한 '연속 상가필지' 클러스터 + 근린 코어(300m).
강의 정의: 상권은 도로 아닌 경쟁상가 연속으로 형성 → 상가 POI가 있는 필지의 연속체로 근사."""
import math
import re
from shapely.geometry import shape, Point, mapping
from shapely.ops import unary_union

# 상가성 카테고리(상권 형성 업종): 음식·카페·편의·병원·학원·중개·은행·약국·마트·문화·숙박
_BIZ_CODES = ["FD6", "CE7", "CS2", "HP8", "AC5", "AG2", "BK9", "PM9", "MT1", "CT1", "AD5"]
_CORE_R = 300.0          # 근린 코어 반경(m)
_POI_BUF = 0.00008       # POI ~8m 버퍼(지오코딩 오차 흡수)
_GAP = 0.00003           # 필지 연속 판정 ~3m
_BRIDGE = 0.00010        # 클러스터 연결 ~11m: 좁은 도로 건너 상권 이어줌(大路는 분리). 강의='상권은 도로로 안 나뉨'
# 상권에서 제외할 지목(도로·하천·구거·제방·철도·유지·임야·전·답·과수원·목장·묘지·양어장·수도용지)
_EXCLUDE_JIMOK = set("도천구제철유임전답과목묘양수")


def _jimok(jibun):
    """지번 끝 한글 = 지목 (예 '3541도'→'도'[도로], '3721대'→'대'[대지]). 없으면 ''."""
    ch = re.findall(r"[가-힣]", jibun or "")
    return ch[-1] if ch else ""


def _hav(la1, lo1, la2, lo2):
    R = 6371000.0
    p = math.radians
    return 2 * R * math.asin(math.sqrt(
        math.sin((p(la2) - p(la1)) / 2) ** 2
        + math.cos(p(la1)) * math.cos(p(la2)) * math.sin((p(lo2) - p(lo1)) / 2) ** 2))


def compute_trade_area(lat, lng, radius=560, area_radius=None, apt_list=None, exclude_geo=None, resi_regions_meta=None):
    """좌표 → 물건 1.5km 배후: 상권(상가 POI 필지, radius 내, exclude_geo 밖) + 아파트(apt_list 근처 대지=진파랑, 단지별) + 주택단지(주거영역 대지).
    exclude_geo=usage_zones 아파트영역 → 그 안 필지는 상권/주택단지서 제외(아파트단지 내 상가가 상권으로 안 빨려듦, v10 유지).
    apt_list=apt_households 단지[{center,name,hh}](1.5km 전체) → 대지를 단지에 근접배정(진파랑, 배지 클릭 격리). area_radius=필지 반경(1.5km)."""
    from auction_analysis import usage_zones as uz
    from auction_analysis.market_flow import _kakao_cat
    from shapely.prepared import prep
    if area_radius is None:
        area_radius = radius
    excl = None
    if exclude_geo:                                          # 아파트 영역(근접) → 상권 제외용(v10)
        try:
            ag = shape(exclude_geo).buffer(0)
            if ag and not ag.is_empty:
                excl = prep(ag)
        except Exception:
            excl = None
    apt_pts = []                                             # [(Point(lng,lat), name, hh)] — 아파트 단지 중심
    if apt_list:
        for a in apt_list:
            c = a.get("center")
            if c and len(c) == 2:
                try:
                    apt_pts.append((Point(float(c[0]), float(c[1])), a.get("name"), a.get("hh")))
                except Exception:
                    pass
    resi_prep = None
    region_shapes = []                                       # [(region_shape, hh, center)] — 블록별 묶기용
    if resi_regions_meta:
        geos = []
        for reg in resi_regions_meta:
            try:
                rg = shape(reg.get("geo")).buffer(0)
                if rg and not rg.is_empty:
                    region_shapes.append((rg, reg.get("households"), reg.get("center")))
                    geos.append(rg)
            except Exception:
                pass
        if geos:
            try:
                resi_prep = prep(unary_union(geos))           # 주거영역 합집합 → 대지 한정 필터
            except Exception:
                resi_prep = None
    dlat = area_radius / 111320.0 * 1.05
    dlng = area_radius / (111320.0 * math.cos(math.radians(lat))) * 1.05
    try:
        parcels = uz._parcels(lat, lng, dlat, dlng) or []
    except Exception as e:
        return {"available": False, "reason": "필지 조회 오류:" + type(e).__name__}
    polys = []
    for p in parcels:
        pr = p.get("properties") or {}
        jm = _jimok(pr.get("jibun"))
        if jm in _EXCLUDE_JIMOK:                          # 도로·하천 등 제외
            continue
        try:
            g = shape(p["geometry"]).buffer(0)
            if g and not g.is_empty:
                polys.append((g, jm))                     # (필지, 지목)
        except Exception:
            pass
    if not polys:
        return {"available": False, "reason": "필지 없음"}
    pts = []
    for code in _BIZ_CODES:
        for t in _kakao_cat(code, lng, lat, int(radius)):    # (x=lng, y=lat, name) 튜플
            try:
                pts.append(Point(float(t[0]), float(t[1])))
            except Exception:
                pass
    if not pts:
        return {"available": False, "reason": "상가 POI 없음"}
    buf = unary_union([pt.buffer(_POI_BUF) for pt in pts])
    m2f = (111320.0 ** 2) * math.cos(math.radians(lat))     # 필지 면적 deg²→㎡ 대략(아파트 대지=큰 필지 판별)
    comm, resi = [], []
    apt_groups = {}                                          # 단지 인덱스 → 대지 필지들(진파랑)
    for g, jm in polys:
        if jm == "대" and apt_pts:                           # 아파트 대지? = 단지중심 포함 OR (근접+큰필지+상가POI없음)
            c = g.representative_point()
            bi, bd = -1, 1e18
            for i, (p, _nm, _hh) in enumerate(apt_pts):
                d = _hav(c.y, c.x, p.y, p.x)
                if d < bd:
                    bd, bi = d, i
            if bi >= 0 and (g.contains(apt_pts[bi][0])
                            or (bd <= 140.0 and g.area * m2f >= 2000.0 and not g.intersects(buf))):
                apt_groups.setdefault(bi, []).append(g)       # 아파트 대지(상가필지·소형 주택필지는 제외 → 상권/주택단지로)
                continue
        if excl is not None and excl.contains(g.representative_point()):
            continue                                          # 근접 아파트영역 내 필지 → 상권·주택단지서 제외(아파트단지 상가 흡수 방지, v10)
        if g.intersects(buf):
            comm.append(g)                                    # 상가 POI 있음 → 상권 후보
        elif jm == "대":                                     # 대지 + 상가아님
            if resi_prep is None or resi_prep.contains(g.representative_point()):
                resi.append(g)                                # 주거영역(주용도 주거) 안만 → 주택단지. 공원·나대지 제외
    if not comm:
        return {"available": False, "reason": "상가 필지 없음"}
    subj = Point(lng, lat)
    merged = unary_union([g.buffer(_BRIDGE) for g in comm])     # 연속 클러스터(좁은 도로 건너 연결)
    geoms = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
    mine = None
    for gg in geoms:                                            # 물건 포함(또는 60m내) 클러스터
        if gg.contains(subj) or gg.distance(subj) < 0.0006:
            mine = gg
            break
    if mine is None:
        mine = max(geoms, key=lambda x: x.area)
    out, core_polys, outer_polys = [], [], []
    for g in comm:
        if not g.intersects(mine):
            continue
        try:
            c = g.representative_point()
            core = _hav(lat, lng, c.y, c.x) <= _CORE_R
        except Exception:
            core = False
        out.append({"geo": mapping(g), "core": core})
        (core_polys if core else outer_polys).append(g)
    if not out:
        return {"available": False, "reason": "상권 클러스터 없음"}
    # 인접 필지끼리 깨끗이 병합(unary_union, 버퍼X → 외곽선은 실제 필지 경계 그대로, 내부선만 제거)
    core_u = unary_union(core_polys) if core_polys else None
    outer_u = unary_union(outer_polys) if outer_polys else None
    core_uu = core_u
    # 아파트: 단지별 대지 병합 + apt_list 전부(대지 없으면 geo=None, 배지만) → 배지 클릭=그 단지 영역만
    apt_regions = []
    for i, (p, nm, hh) in enumerate(apt_pts):
        if not hh:
            continue
        geo, ctr = None, [p.x, p.y]
        gs = apt_groups.get(i)
        if gs:
            try:
                mu = unary_union(gs)
                if mu and not mu.is_empty:
                    geo = mapping(mu)
                    cpt = mu.representative_point()
                    ctr = [cpt.x, cpt.y]
            except Exception:
                pass
        apt_regions.append({"geo": geo, "name": nm, "hh": hh, "center": ctr})
    # 주거를 blue_region(블록)별로 묶기 — 세대 배지 클릭 시 그 블록만 표시용
    resi_regions = []
    for rg, hh, center in region_shapes:
        try:
            rp = prep(rg)
            members = [g for g in resi if rp.contains(g.representative_point())]
            if not members:
                continue
            mu = unary_union(members)
            if mu and not mu.is_empty:
                try:
                    cpt = mu.representative_point()           # 배지=실제 주거 필지 위(블록 기하중심이 묘지·산에 떨어지는 것 방지)
                    rc = [cpt.x, cpt.y]
                except Exception:
                    rc = center
                resi_regions.append({"geo": mapping(mu), "hh": hh, "center": rc})
        except Exception:
            pass
    return {"available": True, "center": [lng, lat], "parcels": out,
            "core_geo": mapping(core_uu) if (core_uu and not core_uu.is_empty) else None,
            "outer_geo": mapping(outer_u) if (outer_u and not outer_u.is_empty) else None,
            "resi_regions": resi_regions,
            "apt_regions": apt_regions,
            "count": len(out), "core_count": sum(1 for x in out if x["core"]),
            "resi_count": len(resi), "poi_count": len(pts), "core_radius_m": int(_CORE_R)}
