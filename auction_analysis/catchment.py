# -*- coding: utf-8 -*-
"""배후세대 동선(상권 흡인) 분석: 각 아파트 단지의 도로 접점(입구)에서 도로망을 따라
경매 상가 필지로 가는 최단 보행경로를 그리되, 도중에 '다른 상업(빨강) 영역'에 닿으면 거기서 끊는다.
끊기지 않고 상가까지 닿는 단지 = 그 상가의 진짜 배후세대.

도로망 = V-World 표준노드링크(LT_L_MOCTLINK). 끝점을 노드로 스냅(소수5자리≈1m)해 그래프 구성 →
Dijkstra 최단경로. 별도 API·키 불필요. 아파트·상업 영역은 usagezone 캐시(apt/red)에서 가져옴.
"""
from __future__ import annotations
import os, math, heapq
import httpx
from shapely.geometry import shape, Point, LineString, mapping
from shapely.ops import unary_union

_VKEY = os.environ.get("VWORLD_KEY", "")
_VDOM = os.environ.get("VWORLD_DOMAIN", "")
_TKEY = os.environ.get("TMAP_APP_KEY", "")
_DATA = "https://api.vworld.kr/req/data"
_TMAP = "https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1&format=json"
_SNAP = 5                 # 좌표 반올림(노드 스냅)
_ROUTE_R = 1600.0         # 도로망 수집 반경(m)
_ENTRY_NEAR = 18.0        # 단지 경계에서 이 거리(m) 안의 도로 노드 = 입구


def _hav(la1, lo1, la2, lo2):
    R = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _fetch_links(lat, lng, r=_ROUTE_R):
    """MOCTLINK 도로 링크(전체). 1000개 하드캡 → bbox 4분할 재귀로 누락 방지. link_id 중복제거."""
    out = {}
    dlat0 = r / 111320.0
    dlng0 = r / (111320.0 * math.cos(math.radians(lat)))

    def fetch(la, lo, dla, dlo, depth):
        box = "BOX(%f,%f,%f,%f)" % (lo - dlo, la - dla, lo + dlo, la + dla)
        try:
            d = httpx.get(_DATA, params={"service": "data", "request": "GetFeature", "data": "LT_L_MOCTLINK",
                          "key": _VKEY, "geomFilter": box, "format": "json", "crs": "EPSG:4326",
                          "size": "1000", "domain": _VDOM or "localhost", "geometry": "true"}, timeout=30).json()
            feats = (((d.get("response", {}) or {}).get("result", {}) or {})
                     .get("featureCollection", {}) or {}).get("features", []) or []
        except Exception:
            feats = []
        for f in feats:
            lid = (f.get("properties") or {}).get("link_id")
            if lid:
                out[lid] = f
        if len(feats) >= 1000 and depth < 2:
            h_la, h_lo = dla / 2.0, dlo / 2.0
            for sla in (la - h_la, la + h_la):
                for slo in (lo - h_lo, lo + h_lo):
                    fetch(sla, slo, h_la, h_lo, depth + 1)

    fetch(lat, lng, dlat0, dlng0, 0)
    return list(out.values())


def _build_graph(links):
    """링크 → 그래프 {node:[(nbr,weight_m)]}. node=(round lng,round lat). 멀티라인 처리."""
    G: dict = {}
    for f in links:
        g = f.get("geometry")
        if not g:
            continue
        try:
            sh = shape(g)
        except Exception:
            continue
        if sh.geom_type == "LineString":
            segs = [sh]
        elif sh.geom_type == "MultiLineString":
            segs = list(sh.geoms)
        else:
            continue
        for s in segs:
            cs = list(s.coords)
            for a, b in zip(cs, cs[1:]):
                na = (round(a[0], _SNAP), round(a[1], _SNAP))
                nb = (round(b[0], _SNAP), round(b[1], _SNAP))
                if na == nb:
                    continue
                w = _hav(a[1], a[0], b[1], b[0])
                G.setdefault(na, []).append((nb, w))
                G.setdefault(nb, []).append((na, w))
    return G


def _nearest_node(G, lng, lat):
    best, bd = None, 1e18
    for n in G:
        d = _hav(lat, lng, n[1], n[0])
        if d < bd:
            bd, best = d, n
    return best, bd


def _dijkstra(G, start, end):
    """start→end 최단경로 노드열. 없으면 None."""
    if start is None or end is None:
        return None
    dist = {start: 0.0}
    prev = {}
    pq = [(0.0, start)]
    while pq:
        du, u = heapq.heappop(pq)
        if u == end:
            break
        if du > dist.get(u, 1e18):
            continue
        for v, w in G.get(u, ()):  # type: ignore
            nd = du + w
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if end not in dist:
        return None
    path = [end]
    while path[-1] != start:
        path.append(prev[path[-1]])
    return path[::-1]


def _tmap_route(slng, slat, elng, elat):
    """Tmap 보행자 경로(횡단보도·보도 반영) → [[lng,lat],...] 좌표열. 실패 시 None."""
    if not _TKEY:
        return None
    body = {"startX": slng, "startY": slat, "endX": elng, "endY": elat,
            "startName": "출발", "endName": "도착", "reqCoordType": "WGS84GEO",
            "resCoordType": "WGS84GEO", "searchOption": "0"}
    try:
        r = httpx.post(_TMAP, headers={"appKey": _TKEY, "Content-Type": "application/json"},
                       json=body, timeout=15)
        if r.status_code != 200:
            return None
        feats = r.json().get("features", []) or []
    except Exception:
        return None
    coords = []
    for f in feats:
        g = f.get("geometry") or {}
        if g.get("type") == "LineString":
            for c in g.get("coordinates", []):
                try:
                    coords.append([float(c[0]), float(c[1])])
                except Exception:
                    pass
    return coords if len(coords) >= 2 else None


def _entrance_nodes(G, apt_poly):
    """단지 폴리곤 경계에서 _ENTRY_NEAR(m) 안의 도로 노드 = 입구. ~40m내 가까운 입구는 1개로 묶음."""
    bd = apt_poly.boundary
    near_deg = _ENTRY_NEAR / 111000.0
    cand = []
    minx, miny, maxx, maxy = apt_poly.bounds
    m = near_deg * 1.5
    for n in G:
        if not (minx - m <= n[0] <= maxx + m and miny - m <= n[1] <= maxy + m):
            continue
        try:
            if bd.distance(Point(n[0], n[1])) < near_deg:
                cand.append(n)
        except Exception:
            pass
    reps = []                                   # 가까운 입구 클러스터링(대표만)
    for n in cand:
        if all(_hav(n[1], n[0], r[1], r[0]) > 40 for r in reps):
            reps.append(n)
    return reps


def compute_catchment(lat, lng, red_geo):
    """상가(lat,lng) 기준 배후세대 동선. Kakao '아파트' **단지별로 단지 중심→상가 Tmap 보행경로**.
    Tmap이 단지 내부 길을 알아 **실제 진입로로 빠져나감**(지적 모서리 오검출 방지, 선이 단지 안에서 시작).
    단지당 1개 최적 경로. 도중 다른 상업(빨강)에 닿으면 그 가장자리서 절단."""
    from auction_analysis.usage_zones import _apartment_complexes
    center = Point(lng, lat)
    try:
        red = shape(red_geo) if red_geo else None
    except Exception:
        red = None
    dest_red = None
    if red is not None:
        comps = list(red.geoms) if red.geom_type == "MultiPolygon" else [red]
        near_comps = [c for c in comps if c.distance(center) < (60 / 111000.0)]   # 상가 인접 상업덩어리(닿아도 OK)
        if near_comps:
            dest_red = unary_union(near_comps)

    try:                                                 # 개별 아파트 단지(Kakao)
        complexes, _ent = _apartment_complexes(lat, lng)
    except Exception:
        complexes = []
    if not complexes:
        return {"available": True, "dest": [lng, lat], "routes": [], "reach_count": 0, "blocked_count": 0}

    def _truncate(path):
        """경로 따라가다 '어떤 상업(빨강)이든' 처음 닿는 지점에서 정지(빨강 안으로 안 들어감).
        멈춘 지점이 상가 본인 상업덩어리(dest_red)면 reached=True, 다른 상업이면 False(가로채임)."""
        if red is None:
            return True, [list(p) for p in path]
        out = []
        for i in range(len(path) - 1):
            out.append(list(path[i]))
            seg = LineString([path[i], path[i + 1]])
            if seg.intersects(red):                  # 어떤 상업이든 닿는 순간 정지
                inter = seg.intersection(red)
                pts = []
                try:
                    geoms = inter.geoms if hasattr(inter, "geoms") else [inter]
                    for gg in geoms:
                        pts.extend(list(gg.coords) if hasattr(gg, "coords") else [])
                except Exception:
                    pts = []
                a = path[i]
                if pts:
                    entry = min(pts, key=lambda q: _hav(a[1], a[0], q[1], q[0]))
                    out.append([entry[0], entry[1]])
                    ep = Point(entry[0], entry[1])
                else:
                    ep = Point(a[0], a[1])
                reached = dest_red is not None and dest_red.distance(ep) < (4 / 111000.0)
                return reached, out                  # 빨강 가장자리에서 정지
        out.append(list(path[-1]))
        return False, out                            # 상업 한번도 안 닿음(상가 상권 미도달)

    routes = []
    seen = set()
    for cx in complexes:
        try:
            slng, slat = float(cx["lng"]), float(cx["lat"])   # 단지 중심(Kakao POI 평균)
        except Exception:
            continue
        key = (round(slng, 4), round(slat, 4))
        if key in seen:
            continue
        seen.add(key)
        path = _tmap_route(slng, slat, lng, lat)     # 단지중심→상가: Tmap이 실제 진입로로 빼줌
        if not path or len(path) < 2:
            continue
        reaches, coords = _truncate(path)
        if len(coords) >= 2:
            routes.append({"path": coords, "reaches": bool(reaches),
                           "from": coords[0], "name": cx.get("name")})
    return {"available": True, "dest": [lng, lat],
            "routes": routes,
            "reach_count": sum(1 for r in routes if r["reaches"]),
            "blocked_count": sum(1 for r in routes if not r["reaches"])}
