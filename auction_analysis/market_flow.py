# -*- coding: utf-8 -*-
"""상가 상권분석 — 목적지 기준 배후세대 주동선 엔진. (물건 중심 아님)
배후세대(아파트 kapt + 택지 면적환산)가 학교·생활상권·버스정류장·지하철역으로 가는 보행경로를
'이용 빈도' 가중해 누적한 게 주동선. 경매물건은 그 위에 얹어 채점. 지하철역은 집객력도 평가.
"""
import os
import math
import httpx
from shapely.geometry import shape, Point

_TKEY = os.environ.get("TMAP_APP_KEY", "")
_TMAP = "https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1&format=json"
_KKEY = os.environ.get("KAKAO_REST_KEY", "")
_SK = os.environ.get("ONBID_SERVICE_KEY", "")     # data.go.kr 공용키(TAGO 버스 정류소)
_CELL = 0.00035          # 누적 격자 ≈ 35m
# 목적지 타입별 최대 도보거리(이보다 멀면 그 배후세대는 거기 안 감): 학교=등하교, 버스=최근접정류장…
_DEST_MAX = {"school": 1300.0, "mart": 1500.0, "bus": 700.0, "station": 1700.0}

# 목적지 이용빈도 가중(주동선 기여도): 출퇴근(역·버스)=매일·전세대, 등하교=매일이나 학생가구만, 장보기=주2~3회
TYPE_W = {"station": 1.0, "bus": 1.0, "school": 0.6, "mart": 0.45}
TYPE_KO = {"station": "지하철", "bus": "버스", "school": "학교", "mart": "상권"}


def _hav(la1, lo1, la2, lo2):
    R = 6371000.0
    p = math.radians
    return 2 * R * math.asin(math.sqrt(
        math.sin((p(la2) - p(la1)) / 2) ** 2
        + math.cos(p(la1)) * math.cos(p(la2)) * math.sin((p(lo2) - p(lo1)) / 2) ** 2))


def _tmap(slng, slat, elng, elat):
    if not _TKEY:
        return None
    body = {"startX": slng, "startY": slat, "endX": elng, "endY": elat,
            "startName": "s", "endName": "e",
            "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO", "searchOption": "0"}
    try:
        r = httpx.post(_TMAP, headers={"appKey": _TKEY, "Content-Type": "application/json"},
                       json=body, timeout=15)
        if r.status_code != 200:
            return None
        feats = r.json().get("features", []) or []
    except Exception:
        return None
    pts = []
    for f in feats:
        g = f.get("geometry") or {}
        if g.get("type") == "LineString":
            for c in g.get("coordinates", []):
                try:
                    pts.append([float(c[0]), float(c[1])])
                except Exception:
                    pass
    return pts if len(pts) >= 2 else None


def _kakao_route(slng, slat, elng, elat):
    """카카오모빌리티 자동차 길찾기 → 경로 polyline [[lng,lat],...]. 기존 KAKAO_REST_KEY 사용(무료·넉넉).
    도보 API는 카카오 미공개라 자동차로 대체(집계 주동선엔 도로따라가면 충분, Tmap 쿼터 회피)."""
    if not _KKEY:
        return None
    try:
        r = httpx.get("https://apis-navi.kakaomobility.com/v1/directions",
                      headers={"Authorization": "KakaoAK " + _KKEY},
                      params={"origin": "%s,%s" % (slng, slat), "destination": "%s,%s" % (elng, elat),
                              "priority": "RECOMMEND", "road_details": "false"}, timeout=12)
        if r.status_code != 200:
            return None
        rt = (r.json().get("routes") or [{}])[0]
        if rt.get("result_code") != 0:
            return None
    except Exception:
        return None
    pts = []
    for sec in rt.get("sections", []) or []:
        for road in sec.get("roads", []) or []:
            vx = road.get("vertexes", []) or []
            for i in range(0, len(vx) - 1, 2):
                try:
                    pts.append([float(vx[i]), float(vx[i + 1])])   # vertexes=[x,y,x,y,…]
                except Exception:
                    pass
    return pts if len(pts) >= 2 else None


def _route(slng, slat, elng, elat):
    """배후세대 라우팅 — 🔴보행자 전용(자동차 길찾기 절대 금지, 주인님). 엔진 교체 시 여기만 수정.
    현재=Tmap 보행자(쿼터 소진 중이면 None→0경로). 카카오/네이버 자동차는 쓰지 않음."""
    return _tmap(slng, slat, elng, elat)


_OSM_SNAP = 6
# 보행 가능한 길만(자동차전용=motorway/trunk 제외). 횡단보도·보도·계단·골목 포함
_OSM_HW = "footway|path|pedestrian|living_street|residential|service|unclassified|tertiary|secondary|primary|steps|track|cycleway|road|crossing"
_OSM_MIRRORS = ["https://maps.mail.ru/osm/tools/overpass/api/interpreter",
                "https://overpass.kumi.systems/api/interpreter",
                "https://overpass-api.de/api/interpreter"]


def _overpass(lat, lng, r):
    ql = ('[out:json][timeout:60];(way["highway"~"%s"](around:%d,%f,%f););(._;>;);out skel;'
          % (_OSM_HW, int(r), lat, lng))
    for url in _OSM_MIRRORS:
        try:
            resp = httpx.post(url, data={"data": ql}, timeout=60)
            if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                return resp.json().get("elements", []) or []
        except Exception:
            continue
    return []


def _osm_walk_graph(lat, lng, r=1700.0):
    """OSM 보행망(횡단보도·보도·골목·단지길 매핑분 포함) → 그래프 {node(lng,lat):[(nbr,weight_m)]} 양방향."""
    els = _overpass(lat, lng, r)
    nodes = {}
    ways = []
    for e in els:
        t = e.get("type")
        if t == "node":
            nodes[e["id"]] = (e["lon"], e["lat"])
        elif t == "way":
            ways.append(e.get("nodes", []) or [])
    G = {}
    for seq in ways:
        for aid, bid in zip(seq, seq[1:]):
            a = nodes.get(aid)
            b = nodes.get(bid)
            if not a or not b:
                continue
            na = (round(a[0], _OSM_SNAP), round(a[1], _OSM_SNAP))
            nb = (round(b[0], _OSM_SNAP), round(b[1], _OSM_SNAP))
            if na == nb:
                continue
            w = _hav(a[1], a[0], b[1], b[0])
            G.setdefault(na, []).append((nb, w))
            G.setdefault(nb, []).append((na, w))
    return G


def _kakao_cat(code, lng, lat, radius=2500, pages=3):
    if not _KKEY:
        return []
    out = []
    for pg in range(1, pages + 1):
        try:
            r = httpx.get("https://dapi.kakao.com/v2/local/search/category.json",
                          headers={"Authorization": "KakaoAK " + _KKEY},
                          params={"category_group_code": code, "x": lng, "y": lat,
                                  "radius": radius, "page": pg, "size": 15}, timeout=10)
            j = r.json()
            out += j.get("documents", []) or []
            if j.get("meta", {}).get("is_end", True):
                break
        except Exception:
            break
    res = []
    for d in out:
        try:
            res.append((float(d["x"]), float(d["y"]), d.get("place_name", "")))
        except Exception:
            pass
    return res


def _tago_bus_stops(lng, lat, rows=100):
    """data.go.kr TAGO 좌표기반 근접 버스정류소 → [{lng,lat,name,w}]."""
    if not _SK:
        return []
    url = ("http://apis.data.go.kr/1613000/BusSttnInfoInqireService/getCrdntPrxmtSttnList"
           "?serviceKey=%s&gpsLati=%s&gpsLong=%s&numOfRows=%d&_type=json" % (_SK, lat, lng, rows))
    try:
        r = httpx.get(url, timeout=12)
        items = (((r.json().get("response") or {}).get("body") or {}).get("items") or {}).get("item")
    except Exception:
        return []
    if isinstance(items, dict):
        items = [items]
    out, seen = [], set()
    for it in (items or []):
        try:
            x, y = float(it["gpslong"]), float(it["gpslati"])
            k = (round(x, 4), round(y, 4))
            if k in seen:
                continue
            seen.add(k)
            out.append({"lng": x, "lat": y, "name": it.get("nodenm", "정류장"), "w": 1.0})
        except Exception:
            pass
    return out


def _station_pull(lng, lat):
    """지하철역 집객력 근사 = 역 250m 내 음식점·카페 수(상권 밀도). 정밀=승하차 데이터(미보유)."""
    try:
        n = len(_kakao_cat("FD6", lng, lat, radius=250, pages=2)) + \
            len(_kakao_cat("CE7", lng, lat, radius=250, pages=1))
    except Exception:
        n = 0
    if n >= 30:
        return ("높음", 1.0)
    if n >= 12:
        return ("보통", 0.6)
    return ("낮음", 0.3)


def collect_destinations(lng, lat):
    """배후세대가 향하는 목적지 4종. station은 집객력(pull)·가중(w) 포함."""
    sc = [{"lng": x, "lat": y, "name": n, "w": 1.0}
          for (x, y, n) in _kakao_cat("SC4", lng, lat)
          if any(t in n for t in ("초등학교", "중학교", "고등학교"))]
    mt = [{"lng": x, "lat": y, "name": n, "w": 1.0} for (x, y, n) in _kakao_cat("MT1", lng, lat)]
    bus = _tago_bus_stops(lng, lat)
    st = []
    for (x, y, n) in _kakao_cat("SW8", lng, lat, radius=3000):
        lab, w = _station_pull(x, y)
        st.append({"lng": x, "lat": y, "name": n, "w": w, "pull": lab})
    return {"school": sc, "mart": mt, "bus": bus, "station": st}


def _nearest(olng, olat, dl):
    best = None
    for d in dl:
        dist = _hav(olat, olng, d["lat"], d["lng"])
        if best is None or dist < best[0]:
            best = (dist, d)
    return best


def _peaks(grid, commercial=None, top=5, sep_m=170.0):
    """🔴입지 등급 = '상권 안에서 배후세대 흐름이 가장 높은 지점'(상권 밖 교차로 제외).
    commercial(상권=실제 상업영역) 60m 버퍼 안의 셀만 후보로, 흐름 높은 순 NMS 5개."""
    if not grid:
        return []
    cells = sorted(((k[0] * _CELL, k[1] * _CELL, v) for k, v in grid.items()), key=lambda x: -x[2])
    pc = None
    if commercial is not None:
        try:
            from shapely.prepared import prep
            pc = prep(commercial.buffer(60.0 / 111000.0))   # 상권 ±60m(상가는 도로서 물러나 있음)
        except Exception:
            pc = None
    spots = []
    checked = 0
    for (lng, lat, v) in cells:
        checked += 1
        if checked > 4000:
            break
        if pc is not None and not pc.contains(Point(lng, lat)):
            continue                                  # 상권 밖이면 입지 등급 제외
        if all(_hav(lat, lng, s[1], s[0]) > sep_m for s in spots):
            spots.append((lng, lat, v))
            if len(spots) >= top:
                break
    return spots


def compute_market_flow(lat, lng, origins, dests, commercial_geo=None):
    """origins=[(lng,lat,households,kind)] · dests={school,mart,bus,station}(dict 리스트).
    각 배후세대 → 가장 가까운 학교/상권/버스/역 보행경로(세대수 × 빈도가중 × 역집객력) 누적 = 주동선.
    commercial_geo=상권(실제 상업영역) GeoJSON → 입지 등급은 그 안에서만(상권 밖 교차로는 등급 제외)."""
    commercial = None
    if commercial_geo:
        try:
            commercial = shape(commercial_geo)
        except Exception:
            commercial = None

    from auction_analysis.catchment import _nearest_node, _dijkstra
    try:                                  # 🚶OSM 보행망(횡단보도·보도·골목·단지길 매핑분 포함) 그래프 1회 구축. 무료·자동차 아님
        _G = _osm_walk_graph(lat, lng, 1700.0)
    except Exception:
        _G = {}
    _nncache = {}

    def _nn(plng, plat):
        kk = (round(plng, 5), round(plat, 5))
        if kk not in _nncache:
            _nncache[kk] = (_nearest_node(_G, plng, plat)[0] if _G else None)
        return _nncache[kk]

    routes = []
    grid = {}

    def add(path, val):
        for c in path:
            k = (round(c[0] / _CELL), round(c[1] / _CELL))
            grid[k] = grid.get(k, 0) + val

    types = [("school", dests.get("school", [])), ("mart", dests.get("mart", [])),
             ("bus", dests.get("bus", [])), ("station", dests.get("station", []))]

    for (olng, olat, hh, kind) in origins:
        if not hh or hh <= 0:
            continue
        for tname, dl in types:
            if not dl:
                continue
            nb = _nearest(olng, olat, dl)
            if not nb or nb[0] > _DEST_MAX.get(tname, 1500.0):
                continue
            d = nb[1]
            sn = _nn(olng, olat)
            dn = _nn(d["lng"], d["lat"])
            npath = _dijkstra(_G, sn, dn) if (sn and dn) else None   # 보행자 최단경로(도로망 양방향)
            if not npath or len(npath) < 2:
                continue
            path = [[olng, olat]] + [[float(n[0]), float(n[1])] for n in npath] + [[d["lng"], d["lat"]]]
            w = TYPE_W[tname] * float(d.get("w", 1.0))   # 이용빈도 × (역=집객력)
            routes.append({"path": path, "households": int(hh), "to": tname, "weight": round(w, 2)})
            add(path, hh * w)

    ck = (round(lng / _CELL), round(lat / _CELL))
    around = [grid.get((ck[0] + dx, ck[1] + dy), 0)
              for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    on_flow = max(around) if around else 0
    max_flow = max(grid.values()) if grid else 0

    peaks = _peaks(grid, commercial, top=5)        # 상권 안에서만 입지 등급
    spots = [{"lng": p[0], "lat": p[1], "flow": int(p[2]), "rank": i + 1}
             for i, p in enumerate(peaks)]
    top_flow = spots[0]["flow"] if spots else (max_flow or 0)
    score = round(on_flow / top_flow, 2) if top_flow else 0.0   # 상권 1등 입지 대비
    item_grade = sum(1 for s in spots if s["flow"] > on_flow * 1.05) + 1   # 물건이 상권 내 몇 등급

    return {
        "available": True,
        "center": [lng, lat],
        "routes": routes,
        "route_count": len(routes),
        "origin_count": len(origins),
        "on_flow": int(on_flow),
        "max_flow": int(max_flow),
        "score": score,
        "on_backbone": score >= 0.5,
        "spots": spots,
        "item_rank_ratio": score,
        "item_grade": item_grade,
        "spot_count": len(spots),
        "dest": dests,
    }
