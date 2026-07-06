"""좌표 → 용도지역(국토계획) 라벨. V-World LT_C_UQ111(용도지역) point 조회.

경매 원천데이터(detail_text)의 토지이용계획란에 용도지역 값이 없는 물건(주로 농촌형
단독·다가구·주택, 전체의 ~21%)을 정부 공식데이터(V-World)로 보완하기 위한 소스.

환경변수 VWORLD_KEY 사용(지오코더와 동일 키). domain=localhost 로 호출(키 등록 도메인 무관 확인됨).
"""

from __future__ import annotations

import os

import httpx

_VWORLD_DATA = "https://api.vworld.kr/req/data"


class LandUseSource:
    def __init__(self, key: str | None = None):
        self.key = key or os.environ.get("VWORLD_KEY", "")

    def zone_by_coord(self, lng: float, lat: float):
        """좌표(경도,위도) → (라벨, status).
          - ('제1종전용주거지역', 'OK')  : 용도지역 찾음
          - (None, 'NOT_FOUND')          : 해당 좌표에 용도지역 없음(섬/해상 등) — NF 캐시 대상
          - (None, 'ERROR')              : 통신/응답 오류 — 캐시하지 말고 재시도

        LT_C_UQ111(용도지역) 폴리곤을 point로 조회. 한 좌표에 여러 피처가 겹쳐 오면
        '…지역' 텍스트를 가진 첫 피처를 사용. 일시오류를 NF로 굳히지 않도록 status 구분."""
        if not self.key:
            return (None, "ERROR")
        try:
            r = httpx.get(_VWORLD_DATA, params={
                "service": "data", "request": "GetFeature", "data": "LT_C_UQ111",
                "key": self.key, "geomFilter": f"POINT({lng} {lat})", "format": "json",
                "crs": "EPSG:4326", "size": "10", "domain": "localhost"}, timeout=12)
            resp = ((r.json() or {}).get("response", {}) or {})
        except Exception:
            return (None, "ERROR")
        status = resp.get("status")
        feats = (((resp.get("result", {}) or {}).get("featureCollection", {}) or {})
                 .get("features", []) or [])
        for f in feats:
            p = f.get("properties") or {}
            v = (p.get("uname") or p.get("dgm_nm") or "").strip()
            if v and "지역" in v:
                return (v, "OK")
        if status == "NOT_FOUND":          # 명시적 '없음'만 NF(캐시), 그 외(에러/쿼터)는 재시도
            return (None, "NOT_FOUND")
        return (None, "ERROR")
