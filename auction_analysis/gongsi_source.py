"""V-World 공동주택가격 속성조회 → 호별 공시가격(아파트·연립·다세대).

  Endpoint: https://api.vworld.kr/ned/data/getApartHousingPriceAttr
  필수파라: key, pnu(19자리 필지고유번호), format=json, domain(키 등록 도메인)
  주요필드: stdrYear(기준년도), prvuseAr(전용면적), floorNm(층), hoNm(호), dongNm(동),
            pblntfPc(공시가격, 원), aphusNm(단지명), pnu

매칭: 거래/물건의 전용면적·층으로 같은 면적(±tol)·같은 층 호들의 공시가격 median.
연도: 최신 기준년도 우선(현재연도부터 역순 탐색).
"""

from __future__ import annotations

import os
import statistics
import xml.etree.ElementTree as ET  # noqa: F401  (json 사용, 보존용)
from datetime import datetime, timezone

import httpx

_URL = "https://api.vworld.kr/ned/data/getApartHousingPriceAttr"


def addr_to_pnu(sgg: str, bjd: str, bun: str, ji: str, mountain: bool = False) -> str:
    """resolve_bjd 결과(시군구5·법정동5·본번4·부번4) → PNU 19자리."""
    return f"{sgg}{bjd}{'2' if mountain else '1'}{bun}{ji}"


class GongsiPrice:
    def __init__(self, key: str | None = None, domain: str | None = None):
        self.key = key or os.environ.get("VWORLD_KEY", "")
        self.domain = domain or os.environ.get("VWORLD_DOMAIN", "http://localhost:4011")
        self._cache: dict[str, list[dict] | None] = {}   # pnu -> 최신연도 호별 리스트

    def _fetch_year(self, pnu: str, year: int) -> list[dict]:
        try:
            r = httpx.get(_URL, params={"key": self.key, "pnu": pnu, "format": "json",
                                        "domain": self.domain, "numOfRows": "800",
                                        "pageNo": "1", "stdrYear": str(year)}, timeout=20)
            d = r.json()
        except Exception:
            return []
        body = (d or {}).get("apartHousingPrices") or {}
        if body.get("resultCode") and body.get("resultCode") != "NORMAL_CODE":
            return []
        fs = body.get("field") or []
        out = []
        for f in fs:
            try:
                out.append({
                    "area": float(f.get("prvuseAr") or 0),
                    "floor": int(str(f.get("floorNm") or "0").strip() or 0),
                    "ho": (f.get("hoNm") or "").strip(),
                    "dong": (f.get("dongNm") or "").strip(),
                    "price": int(f.get("pblntfPc") or 0),
                    "year": int(f.get("stdrYear") or 0),
                    "name": (f.get("aphusNm") or "").strip(),
                })
            except Exception:
                continue
        return [x for x in out if x["price"] > 0]

    def units(self, pnu: str) -> list[dict] | None:
        """PNU의 최신 기준년도 호별 공시가격 리스트(캐시). 없으면 None."""
        if not (self.key and pnu and len(pnu) == 19):
            return None
        if pnu in self._cache:
            return self._cache[pnu]
        cur = datetime.now(timezone.utc).year
        rows: list[dict] = []
        for y in (cur, cur - 1, cur - 2):
            rows = self._fetch_year(pnu, y)
            if rows:
                break
        self._cache[pnu] = rows or None
        return rows or None

    def price(self, pnu: str, area: float | None = None,
              floor: int | None = None, tol: float = 0.7) -> dict | None:
        """면적(±tol)·층 매칭 호들의 공시가격 median. {price, year, name, n}."""
        rows = self.units(pnu)
        if not rows:
            return None
        cand = rows
        if area:
            cand = [r for r in cand if abs(r["area"] - area) <= tol] or rows
        if floor is not None:
            byfl = [r for r in cand if r["floor"] == floor]
            if byfl:
                cand = byfl
        if not cand:
            return None
        prices = [r["price"] for r in cand]
        return {"price": int(statistics.median(prices)), "year": cand[0]["year"],
                "name": cand[0]["name"], "n": len(cand)}

    def indvd_land_price(self, pnu: str) -> dict | None:
        """개별공시지가(원/㎡, 최신 기준년도). V-World getIndvdLandPriceAttr → {price, year} 또는 None."""
        if not (self.key and pnu and len(pnu) == 19):
            return None
        cur = datetime.now(timezone.utc).year
        for y in (cur, cur - 1, cur - 2):
            try:
                r = httpx.get("https://api.vworld.kr/ned/data/getIndvdLandPriceAttr",
                              params={"key": self.key, "pnu": pnu, "format": "json",
                                      "domain": self.domain, "numOfRows": "10",
                                      "pageNo": "1", "stdrYear": str(y)}, timeout=20)
                fs = ((r.json() or {}).get("indvdLandPrices") or {}).get("field") or []
                vals = [int(f["pblntfPclnd"]) for f in fs if f.get("pblntfPclnd")]
                if vals:
                    return {"price": max(vals), "year": y}
            except Exception:
                continue
        return None
