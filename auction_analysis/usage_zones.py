# -*- coding: utf-8 -*-
"""상가 용도영역: 경매물건 중심 반경 1.5km 내를
   RED(상업)  = ⓐ용도지역 '상업지역' 폴리곤(LT_C_UQ111) ∪ ⓑ건축물대장 주용도=상업 건물 필지
   BLUE(주택) = 건축물대장 주용도=주거 건물 필지(반경 1.5km), 단 학교·아파트 제외
로 분류해 shapely로 병합한 폴리곤(GeoJSON)을 산출.

추가 처리:
 - 학교(교육연구시설): 빨강·파랑 모두 제외(부지 비움).
 - 아파트: Kakao '아파트' POI(지도 라벨)로 식별 → 세대수 별도이므로 밀집택지서 제외.
 - 비아파트 주거: 큰 도로(V-World 표준노드링크 제한속도≥50)로 잘라 super-block 단위 분할,
   영역별 면적(㎡) → 세대수 추정(5만㎡당 약 1,300세대).

데이터:
 - 필지: V-World 연속지적 lp_pa_cbnd_bubun. WFS 1,000개 하드캡+페이징 미작동 → 쿼드트리.
 - 용도지역: V-World data API LT_C_UQ111(상업지역).
 - 도로: V-World data API LT_L_MOCTLINK(표준노드링크, max_spd).
 - 아파트 POI: Kakao Local 키워드검색('아파트').
 - 주용도: 건축HUB 표제부(법정동), Supabase 공유캐시(jepyo:).
"""
from __future__ import annotations
import os, math, re
import xml.etree.ElementTree as ET
import httpx
from shapely.geometry import shape, mapping, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

_VKEY = os.environ.get("VWORLD_KEY", "")
_VDOM = os.environ.get("VWORLD_DOMAIN", "")
_BKEY = os.environ.get("ONBID_SERVICE_KEY", "")
_KKEY = os.environ.get("KAKAO_REST_KEY", "")
_WFS = "https://api.vworld.kr/req/wfs"
_DATA = "https://api.vworld.kr/req/data"
_BR = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
_KAKAO_KW = "https://dapi.kakao.com/v2/local/search/keyword.json"
_UA = {"User-Agent": "Mozilla/5.0"}

# 주택류 주용도(나머지 = 상업/비주거)
_RES = ("단독주택", "공동주택", "다세대주택", "연립주택", "다가구주택", "아파트", "도시형", "기숙사")
_EDU = ("학교", "교육연구", "초등학교", "중학교", "고등학교", "대학교", "대학", "유치원")  # 학교=빨강·파랑 모두 제외
# 실제 상업/상권 용도(빨강). 이 외 비주거(공장·창고·종교·발전·위험물·묘지관리·노유자 등)는 무색.
_COM = ("근린생활", "판매시설", "업무시설", "숙박시설", "위락시설", "의료시설",
        "문화및집회", "운동시설", "관광휴게", "운수시설", "공연장")

RED_R = 1500.0    # 상업 반경(m)
BLUE_R = 1500.0   # 주택 반경(m) — 사장님 지시: 1.5km, 절대 축소 금지
_MAXDEPTH = 3     # 쿼드트리 최대 재귀(잘린 타일만 4분할)
_CLOSE = 0.0007   # 모폴로지 클로징(≈65~78m): 주거 블록 사이 골목/구멍 메움
_HH_PER_50K = 1300.0   # 아파트 제외 주택 밀집택지 세대수 기준: 5만㎡당 약 1,300세대
_MAJOR_SPD = 50        # 큰 도로 기준①: 제한속도(㎞/h) 이상 = 간선도로(번길·골목 제외)
_ROAD_MIN_W = 10.0     # 큰 도로 기준②: 도로 필지(지목 道) 폭(m) 이상 = 넓은 도로(저속이어도 절단)
_ROAD_BUF = 0.00014    # 도로 중심선 버퍼(≈15m, 양쪽 ~30m 폭)으로 주거영역 절단
_APT_BUF = 0.00025     # 아파트 POI 매칭 반경(≈25m): POI가 경계/도로변에 찍혀도 단지 필지 편입
_MIN_REGION_M2 = 2000.0  # 이보다 작은 자투리 영역은 세대수 표시 제외
_ABSORB_MAX = 6000.0     # 주거 흡수 대상 상업 클러스터 최대면적(㎡). 이상=상권이라 빨강 유지(흡수X)

_jepyo_cache: dict[str, dict] = {}   # "sgg+bjd" -> {(본번,부번):[주용도]}


def _hav(la1, lo1, la2, lo2):
    R = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _area_m2(geom, lat):
    """EPSG:4326(경위도) 폴리곤 면적 → ㎡. 국지 등장방형 근사(deg²×mlat×mlng)."""
    try:
        return geom.area * 111320.0 * (111320.0 * math.cos(math.radians(lat)))
    except Exception:
        return 0.0


def _road_width_m(poly, lat):
    """도로 필지의 대략적 '폭'(m) = 최소회전사각형 짧은 변. MultiPolygon(흩어진 도로조각)이면
    최대 단일조각으로(전체를 감싸 폭이 뻥튀기되는 것 방지). 단일조각도 면적/길이로 교차검증."""
    try:
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        cs = list(poly.minimum_rotated_rectangle.exterior.coords)
        if len(cs) < 3:
            return 0.0
        e1 = _hav(cs[0][1], cs[0][0], cs[1][1], cs[1][0])
        e2 = _hav(cs[1][1], cs[1][0], cs[2][1], cs[2][0])
        lo, hi = min(e1, e2), max(e1, e2)
        # 길쭉할수록 신뢰(도로). 짧은변이 긴변의 0.6 초과면 도로형 아님(광장/네모) → 면적/긴변으로 보정
        if hi > 0 and lo > hi * 0.6:
            a = _area_m2(poly, lat)
            return min(lo, a / hi) if hi else lo
        return lo
    except Exception:
        return 0.0


def _parcels(lat, lng, dlat, dlng):
    """bbox 내 연속지적 필지(폴리곤+PNU). WFS 1,000개 하드캡+페이징 미작동 → 쿼드트리(4분할 재귀)."""
    out: dict[str, dict] = {}

    def fetch(la, lo, dla, dlo, depth):
        p = {"SERVICE": "WFS", "REQUEST": "GetFeature", "TYPENAME": "lp_pa_cbnd_bubun",
             "BBOX": "%f,%f,%f,%f" % (la - dla, lo - dlo, la + dla, lo + dlo),
             "KEY": _VKEY, "DOMAIN": _VDOM, "OUTPUT": "application/json",
             "SRSNAME": "EPSG:4326", "MAXFEATURES": "1000", "VERSION": "1.1.0"}
        try:
            fs = httpx.get(_WFS, params=p, timeout=40).json().get("features", [])
        except Exception:
            fs = []
        for f in fs:
            pnu = (f.get("properties") or {}).get("pnu")
            if pnu:
                out[pnu] = f
        if len(fs) >= 1000 and depth < _MAXDEPTH:   # 잘림 → 4분할
            h_la, h_lo = dla / 2.0, dlo / 2.0
            for sla in (la - h_la, la + h_la):
                for slo in (lo - h_lo, lo + h_lo):
                    fetch(sla, slo, h_la, h_lo, depth + 1)

    fetch(lat, lng, dlat, dlng, 0)
    return list(out.values())


def _commercial_zone_polys(lat, lng, r=RED_R):
    """반경 내 용도지역 '상업지역' 폴리곤(LT_C_UQ111) → shapely geom 리스트. red base."""
    dlat = r / 111320.0
    dlng = r / (111320.0 * math.cos(math.radians(lat)))
    box = "BOX(%f,%f,%f,%f)" % (lng - dlng, lat - dlat, lng + dlng, lat + dlat)
    polys = []
    try:
        rr = httpx.get(_DATA, params={
            "service": "data", "request": "GetFeature", "data": "LT_C_UQ111",
            "key": _VKEY, "geomFilter": box, "format": "json", "crs": "EPSG:4326",
            "size": "1000", "domain": _VDOM or "localhost", "geometry": "true"}, timeout=30)
        resp = (rr.json() or {}).get("response", {}) or {}
        feats = (((resp.get("result", {}) or {}).get("featureCollection", {}) or {})
                 .get("features", []) or [])
    except Exception:
        feats = []
    for f in feats:
        pr = f.get("properties") or {}
        nm = (pr.get("uname") or pr.get("dgm_nm") or "")
        if "상업" not in nm:
            continue
        g = f.get("geometry")
        if not g:
            continue
        try:
            polys.append(shape(g))
        except Exception:
            pass
    return polys


def _apartment_points(lat, lng, r=BLUE_R):
    """Kakao 키워드검색 '아파트' → 단지 POI 좌표(shapely Point) 리스트. 아파트 식별용(지도 라벨 그대로).
    카테고리에 '아파트' 포함만(상가·입출구·관리 제외)."""
    if not _KKEY:
        return []
    hdr = {"Authorization": "KakaoAK " + _KKEY}
    rad = str(int(min(r, 20000)))
    pts = []
    for page in range(1, 6):                       # 최대 75개
        try:
            d = httpx.get(_KAKAO_KW, params={"query": "아파트", "x": str(lng), "y": str(lat),
                          "radius": rad, "size": "15", "page": str(page), "sort": "distance"},
                          headers=hdr, timeout=15).json()
        except Exception:
            break
        docs = d.get("documents", []) or []
        for doc in docs:
            cat = doc.get("category_name", "") or ""
            if "아파트" not in cat:
                continue
            if any(x in cat for x in ("상가", "입출구", "관리")):
                continue
            try:
                pts.append(Point(float(doc["x"]), float(doc["y"])))
            except Exception:
                pass
        if (d.get("meta", {}) or {}).get("is_end"):
            break
    return pts


def _apartment_complexes(lat, lng, r=BLUE_R):
    """Kakao '아파트' POI → (단지[{name,lng,lat}], 입출구[{name,lng,lat,kind}]).
    ⚠️근본원인: 정관 등 아파트 밀집지에선 단일 중심 '거리순' 검색이 **가까운 몇 단지의 동·상가 POI만으로
    is_end 조기종료**(total 291인데 page1에서 끝) → 286m 너머 단지 전부 누락. 해결: **격자 검색**
    (물건+8방위 ±700m, 각 반경 700m)으로 1.5km 전역 회수. 단지=단지명 집계(중심=평균), 아파트상가 제외."""
    if not _KKEY:
        return [], []
    hdr = {"Authorization": "KakaoAK " + _KKEY}
    agg: dict[str, list] = {}
    ents: dict[str, dict] = {}                          # 이름키 중복제거
    _ENT = re.compile(r"(정문|후문|측문|동문|서문|남문|북문|출입구|입구|게이트)")

    def _scan(cy, cx, query, rad, maxpage):
        for page in range(1, maxpage + 1):
            try:
                d = httpx.get(_KAKAO_KW, params={"query": query, "x": str(cx), "y": str(cy),
                              "radius": str(int(rad)), "size": "15", "page": str(page), "sort": "distance"},
                              headers=hdr, timeout=15).json()
            except Exception:
                break
            for doc in d.get("documents", []) or []:
                cat = doc.get("category_name", "") or ""
                nm = (doc.get("place_name", "") or "").strip()
                try:
                    x, y = float(doc["x"]), float(doc["y"])
                except Exception:
                    continue
                if "아파트" in nm and _ENT.search(nm):     # 아파트 입구(정문/후문/게이트…)
                    _mk = re.search(r"(정문|후문|측문|동문|서문|남문|북문)", nm)
                    ents[nm] = {"name": nm, "lng": x, "lat": y, "kind": _mk.group(1) if _mk else "입구"}
                    continue
                if query != "아파트":                       # 단지/동 집계는 '아파트' 검색에서만
                    continue
                if "아파트" not in cat or "상가" in cat:
                    continue
                m = re.search(r"^(.*?아파트)", nm)
                if m:
                    agg.setdefault(m.group(1).strip(), []).append((x, y))
            if (d.get("meta", {}) or {}).get("is_end"):
                break

    dla = 700.0 / 110540.0                              # 격자: 물건 + 8방위 ±700m, 각 반경 700m
    dln = 700.0 / (111320.0 * math.cos(math.radians(lat)))
    grid = [(lat, lng)] + [(lat + a * dla, lng + b * dln)
                           for a in (-1, 0, 1) for b in (-1, 0, 1) if not (a == 0 and b == 0)]
    for cy, cx in grid:
        _scan(cy, cx, "아파트", 700, 3)
    for q in ("아파트 입구", "아파트 정문", "아파트 후문"):     # 입출구 보강(중심 1회)
        _scan(lat, lng, q, min(r, 20000), 3)

    comps = []
    for nm, pts in agg.items():                         # 격자가 1.5km를 약간 넘겨 잡을 수 있어 단지중심으로 클립
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        dd = 2 * 6371000 * math.asin(math.sqrt(math.sin(math.radians(cy - lat) / 2) ** 2
             + math.cos(math.radians(lat)) * math.cos(math.radians(cy)) * math.sin(math.radians(cx - lng) / 2) ** 2))
        if dd <= r + 100:
            comps.append({"name": nm, "lng": cx, "lat": cy})
    return comps, list(ents.values())


def _major_road_union(lat, lng, r=BLUE_R):
    """V-World 표준노드링크 中 제한속도≥_MAJOR_SPD(큰 도로) 라인 → 버퍼한 폴리곤 union.
    주거영역을 큰 도로로 잘라 super-block 분할하는 칼날."""
    dlat = r / 111320.0
    dlng = r / (111320.0 * math.cos(math.radians(lat)))
    box = "BOX(%f,%f,%f,%f)" % (lng - dlng, lat - dlat, lng + dlng, lat + dlat)
    lines = []
    try:
        d = httpx.get(_DATA, params={"service": "data", "request": "GetFeature", "data": "LT_L_MOCTLINK",
                      "key": _VKEY, "geomFilter": box, "format": "json", "crs": "EPSG:4326",
                      "size": "1000", "domain": _VDOM or "localhost", "geometry": "true"}, timeout=30).json()
        feats = (((d.get("response", {}) or {}).get("result", {}) or {})
                 .get("featureCollection", {}) or {}).get("features", []) or []
    except Exception:
        feats = []
    for f in feats:
        p = f.get("properties") or {}
        try:
            spd = int(p.get("max_spd") or 0)
        except Exception:
            spd = 0
        nm = (p.get("road_name") or "").strip()
        # 큰 도로 = 제한속도≥50 OR 도로명이 '~로/대로'(간선, 폭 대개 10m↑). '~길/번길'은 '로'로 안 끝나 자동 제외.
        if spd < _MAJOR_SPD and not nm.endswith("로"):
            continue
        g = f.get("geometry")
        if not g:
            continue
        try:
            lines.append(shape(g))
        except Exception:
            pass
    if not lines:
        return None
    try:
        return unary_union(lines).buffer(_ROAD_BUF, cap_style=2, join_style=2, mitre_limit=2.0)
    except Exception:
        return None


def _jepyo(sgg: str, bjd: str, db=None) -> dict:
    """(시군구,법정동) 표제부 주용도 → {(본번,부번):[주용도]}.
    ①프로세스 메모리 ②Supabase 공유캐시(jepyo:) ③건축물대장 호출 후 저장."""
    ck = sgg + bjd
    if ck in _jepyo_cache:
        return _jepyo_cache[ck]
    if db is not None:
        try:
            cv = db.cache_get_many(["jepyo:" + ck]).get("jepyo:" + ck)
            if isinstance(cv, dict) and cv.get("m") is not None:
                m = {tuple(k.split("|")): v for k, v in cv["m"].items()}
                _jepyo_cache[ck] = m
                return m
        except Exception:
            pass
    m: dict = {}
    for page in range(1, 30):
        try:
            r = httpx.get(_BR, params={"serviceKey": _BKEY, "sigunguCd": sgg, "bjdongCd": bjd,
                          "numOfRows": "100", "pageNo": str(page), "_type": "xml"}, headers=_UA, timeout=40)
            root = ET.fromstring(r.text)
            items = root.findall(".//item")
        except Exception:
            break
        for it in items:
            def g(t):
                e = it.find(t)
                return (e.text or "") if e is not None else ""
            key = (re.sub(r"\D", "", g("bun")).lstrip("0") or "0",
                   re.sub(r"\D", "", g("ji")).lstrip("0") or "0")
            m.setdefault(key, []).append(g("mainPurpsCdNm"))
        if len(items) < 100:
            break
    if db is not None and m:
        try:
            db.cache_save("jepyo:" + ck, {"m": {"|".join(k): v for k, v in m.items()}})
        except Exception:
            pass
    _jepyo_cache[ck] = m
    return m


def _maj(purps_list, kws) -> bool:
    if not purps_list:
        return False
    n = sum(1 for p in purps_list if any(k in p for k in kws))
    return n > (len(purps_list) - n)


def _is_res(purps_list) -> bool:
    return _maj(purps_list, _RES)


def _is_edu(purps_list) -> bool:
    return _maj(purps_list, _EDU)


def _is_com(purps_list) -> bool:
    """과반수가 실제 상업/상권 용도면 True(빨강). 공장·창고·종교 등은 False(무색)."""
    return _maj(purps_list, _COM)


def _clean(polys):
    out = []
    for p in polys:
        try:
            out.append(p if p.is_valid else p.buffer(0))
        except Exception:
            pass
    return out


def _close_union(polys):
    """클로징으로 골목 메워 병합. 🔴테두리는 직선만(mitre join)=곡선 금지."""
    if not polys:
        return None
    try:
        u = unary_union(polys)
        return (u.buffer(_CLOSE, join_style=2, mitre_limit=2.0)
                 .buffer(-_CLOSE, join_style=2, mitre_limit=2.0))
    except Exception:
        try:
            return unary_union([p.buffer(0) for p in polys])
        except Exception:
            return None


def _diff(geom, sub):
    if geom is None or sub is None:
        return geom
    try:
        return geom.difference(sub)
    except Exception:
        return geom


def _map(g):
    try:
        return mapping(g) if (g is not None and not g.is_empty) else None
    except Exception:
        return None


def compute_zones(lat: float, lng: float, db=None) -> dict:
    """좌표 중심 → RED(상업)·아파트·비아파트 주거 super-block(영역별 세대수) GeoJSON."""
    if not (lat and lng):
        return {"available": False, "reason": "좌표 없음"}
    dlat = RED_R / 111320.0 * 1.05
    dlng = RED_R / (111320.0 * math.cos(math.radians(lat))) * 1.05
    parcels = _parcels(lat, lng, dlat, dlng)
    items = []                            # 반경 내 전 필지: [(shape, purps_or_None)]
    for f in parcels:
        pr = f.get("properties") or {}
        pnu = pr.get("pnu") or ""
        if len(pnu) < 19:
            continue
        sgg, bjd = pnu[:5], pnu[5:10]
        bon = re.sub(r"\D", "", str(pr.get("bonbun") or "")).lstrip("0") or "0"
        bub = re.sub(r"\D", "", str(pr.get("bubun") or "")).lstrip("0") or "0"
        geom = f.get("geometry")
        if not geom:
            continue
        try:
            co = geom["coordinates"][0][0][0]
            plng, plat = co[0], co[1]
        except Exception:
            continue
        if _hav(lat, lng, plat, plng) > RED_R:
            continue
        try:
            g = shape(geom)
        except Exception:
            continue
        items.append((g, _jepyo(sgg, bjd, db).get((bon, bub))))

    # #2 아파트 식별: Kakao '아파트' POI 주변 필지 = 무조건 아파트(표제부 무관 → 미래아파트·대지권 단지도 포착)
    apt_pts = _apartment_points(lat, lng, BLUE_R)
    geoms = [g for g, _ in items]
    apt_idx = set()
    if apt_pts and geoms:
        try:
            tree = STRtree(geoms)
            for pt in apt_pts:
                bp = pt.buffer(_APT_BUF)
                for idx in tree.query(bp):
                    try:
                        if geoms[idx].intersects(bp):
                            apt_idx.add(int(idx))
                    except Exception:
                        pass
        except Exception:
            pass

    # 분류 우선순위: #3 경매물건 본인필지(빨강) > #2 아파트POI > 학교 > 주거 > #1 상업 > 무색
    center_pt = Point(lng, lat)
    res_all, red, edu, apt = [], [], [], []
    for i, (g, purps) in enumerate(items):
        try:
            is_center = g.contains(center_pt)
        except Exception:
            is_center = False
        if is_center and not (purps and _is_res(purps)):
            red.append(g)                     # #3 경매물건 본인 필지는 무조건 색칠(상가=빨강)
        elif i in apt_idx:
            apt.append(g)                     # #2 아파트 POI 필지
        elif purps and _is_edu(purps):
            edu.append(g)
        elif purps and _is_res(purps):
            res_all.append(g)
        elif purps and _is_com(purps):
            red.append(g)                     # #1 실제 상업/상권 용도만 빨강
        # else: 공장·창고·종교·발전·묘지관리 등 비상업 비주거 → 무색
    res_all = _clean(res_all); red = _clean(red); edu = _clean(edu); apt = _clean(apt)

    # 주택단지 안에 갇힌 '작은 고립' 상업 필지만 주거로 흡수. 큰 상권(상업 덩어리 큰 것)은 빨강 유지.
    res_region = _close_union(res_all)
    if red and res_region is not None:
        small_u = None
        try:
            rc = _close_union(red)            # 붙은 상업 병합 → 상권=큰 덩어리, 고립점포=작은 덩어리
            comps = list(rc.geoms) if rc.geom_type == "MultiPolygon" else [rc]
            smalls = [c for c in comps if _area_m2(c, lat) < _ABSORB_MAX]
            small_u = unary_union(smalls) if smalls else None
        except Exception:
            small_u = None
        keep = []
        for rp in red:
            try:
                pp = rp.representative_point()
                if (small_u is not None and not small_u.is_empty
                        and small_u.contains(pp) and res_region.contains(pp)):
                    res_all.append(rp)        # 작은 고립 상업 → 주거 흡수
                else:
                    keep.append(rp)           # 상권(큰 덩어리)·가로변은 빨강 유지
            except Exception:
                keep.append(rp)
        red = keep
    nonapt = res_all                          # 아파트는 이미 분리됨

    zones = _commercial_zone_polys(lat, lng, RED_R)
    zc = len(zones)
    edu_u = unary_union(edu) if edu else None
    apt_region = _diff(_close_union(apt), edu_u) if apt else None   # 닫힌 아파트영역(겹침 방지용)
    roads = _major_road_union(lat, lng, BLUE_R)      # 큰도로: 제한속도≥50 OR 도로명 '~로/대로'

    # 비아파트 주거: 병합 → 아파트(닫힌영역)·학교·큰도로 제외 → 큰도로로 갈린 super-block들(겹침X)
    base = _close_union(nonapt)
    base = _diff(_diff(_diff(base, apt_region), edu_u), roads)
    regions = []
    total_area = 0.0
    if base is not None and not base.is_empty:
        geoms = list(base.geoms) if base.geom_type == "MultiPolygon" else [base]
        for gp in geoms:
            try:
                a = _area_m2(gp, lat)
            except Exception:
                a = 0.0
            if a < _MIN_REGION_M2:
                continue
            hh = int(round(a / 50000.0 * _HH_PER_50K))
            try:
                c = gp.representative_point()
                ctr = [c.x, c.y]
            except Exception:
                ctr = [lng, lat]
            regions.append({"geo": _map(gp), "area_m2": int(round(a)), "households": hh, "center": ctr})
            total_area += a
    regions.sort(key=lambda x: x["area_m2"], reverse=True)
    total_hh = sum(r["households"] for r in regions)

    # apt_region(닫힌 아파트영역)은 위에서 이미 계산. 상업 영역 — 주거·학교와 겹침 제외
    red_u = None
    if red:                                              # B: 용도지역(zones) 제외 = 실제 상업 '건물'만 빨강
        try:
            red_u = _close_union(red)                    # 클로징=인접 상업필지 골목 메워 병합
            res_all_u = _close_union(nonapt + apt)
            red_u = _diff(_diff(red_u, res_all_u), edu_u)
        except Exception:
            red_u = None

    return {"available": True,
            "center": [lng, lat],
            "red": _map(red_u),
            "apt": _map(apt_region),               # 아파트 단지(파랑, 세대수 별도)
            "blue_regions": regions,               # 비아파트 주거 super-block + 영역별 세대수
            "red_count": len(red), "blue_count": len(nonapt),
            "apt_count": len(apt), "red_zone_count": zc,
            "region_count": len(regions),
            "nonapt_area_m2": int(round(total_area)),
            "est_households": total_hh,
            "hh_per_50k": _HH_PER_50K,
            "red_radius_m": RED_R, "blue_radius_m": BLUE_R}
