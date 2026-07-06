"""주소 → (경도, 위도) 지오코더.

  우선순위: ① 카카오 로컬(KAKAO_REST_KEY 있으면, 일 10만건 무료·쿼터 여유) → ② V-World getcoord(폴백).
  카카오 키만 넣으면 V-World getcoord 일일쿼터(OVER_REQUEST_LIMIT)를 우회한다.

환경변수
  KAKAO_REST_KEY : 카카오 REST API 키(로컬 API 활성화 필요). 없으면 V-World만 사용.
  VWORLD_KEY     : V-World 인증키(폴백).
"""

from __future__ import annotations

import math
import os

import httpx

_VWORLD_URL = "https://api.vworld.kr/req/address"
_KAKAO_URL = "https://dapi.kakao.com/v2/local/search/address.json"


def haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """두 좌표(경도,위도) 간 거리(m)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class VGeocoder:
    def __init__(self, key: str | None = None):
        self.key = key or os.environ.get("VWORLD_KEY", "")
        self.kakao = os.environ.get("KAKAO_REST_KEY", "")

    def coord(self, address: str):
        """주소 → (lng, lat). 카카오 우선(키 있으면), 실패 시 V-World. 둘 다 실패 None."""
        if not address:
            return None
        if self.kakao:
            ll = self._kakao(address)
            if ll:
                return ll
        return self._vworld(address)

    def _kakao(self, address: str):
        """카카오 로컬 주소검색 → (lng, lat). 지번/도로명 모두 처리. 실패 None."""
        try:
            r = httpx.get(_KAKAO_URL, params={"query": address, "size": 1},
                          headers={"Authorization": "KakaoAK " + self.kakao}, timeout=8)
            docs = (r.json() or {}).get("documents") or []
            if docs:
                d = docs[0]
                return (float(d["x"]), float(d["y"]))   # x=경도, y=위도
        except Exception:
            pass
        return None

    def _vworld(self, address: str):
        """V-World getcoord → (lng, lat). 지번 우선, 실패 시 도로명. 실패 None."""
        if not (self.key and address):
            return None
        for typ in ("parcel", "road"):
            try:
                r = httpx.get(_VWORLD_URL, params={"service": "address", "request": "getcoord",
                                                   "version": "2.0", "crs": "epsg:4326", "type": typ,
                                                   "address": address, "format": "json",
                                                   "key": self.key}, timeout=8)
                resp = (r.json() or {}).get("response", {})
                if resp.get("status") == "OK":
                    p = resp.get("result", {}).get("point", {})
                    return (float(p["x"]), float(p["y"]))
            except Exception:
                continue
        return None
