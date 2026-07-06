"""건축물대장 표제부(건축HUB) → 준공년도·세대(가구/호)·승강기 유무.

  Endpoint: https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo
  params  : serviceKey, sigunguCd, bjdongCd, bun, ji, numOfRows
  주요필드 : useAprDay(사용승인일), hhldCnt(세대수), fmlyCnt(가구수), hoCnt(호수),
            rideUseElvtCnt(승용승강기), emgenUseElvtCnt(비상용승강기), mainPurpsCdNm(주용도)

모든 주거 건물유형(단독·다가구·다세대·연립 등)에 적용 가능(아파트는 K-apt가 더 풍부).
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

import httpx

from .bjd_codes import resolve_bjd

_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
_UA = {"User-Agent": "Mozilla/5.0"}


def _num(s: str) -> int:
    try:
        return int(re.sub(r"[^0-9]", "", s or "") or 0)
    except Exception:
        return 0


class BuildingSource:
    def __init__(self, key: str | None = None):
        self.key = key or os.environ.get("ONBID_SERVICE_KEY", "")
        self._cache: dict[str, dict | None] = {}
        self._quota_block_until = 0.0   # 쿼터 소진 감지 시 이 시각까지 API 호출 스킵

    def quota_blocked(self) -> bool:
        import time
        return time.time() < self._quota_block_until

    def info(self, address: str) -> dict | None:
        """주소 → 건축물대장 표제부 요약. 실패 None(성공만 캐시).
        일일 호출한도 초과 감지 시 30분간 호출 스킵(헛대기 방지)."""
        import time
        if not (self.key and address):
            return None
        if address in self._cache:
            return self._cache[address]
        if time.time() < self._quota_block_until:   # 쿼터 소진 중 → 즉시 None(API 헛호출 방지)
            return None
        r = resolve_bjd(address)
        if not r:
            return None
        sgg, bjd, bun, ji = r
        try:
            resp = httpx.get(_URL, params={"serviceKey": self.key, "sigunguCd": sgg,
                                           "bjdongCd": bjd, "bun": bun, "ji": ji,
                                           "numOfRows": "30", "_type": "xml"},
                             headers=_UA, timeout=20)
            if "quota exceeded" in resp.text or "LIMITED_NUMBER" in resp.text:
                self._quota_block_until = time.time() + 1800   # 30분 차단
                return None
            root = ET.fromstring(resp.text)
        except Exception:
            return None
        items = root.findall(".//item")
        if not items:
            return None

        def g(it, t):
            v = it.findtext(t)
            return v.strip() if v else ""

        # 같은 지번에 여러 동이면 '주거·세대 많은' 동 우선
        best, best_score = None, -1
        for it in items:
            hh, fm, ho = _num(g(it, "hhldCnt")), _num(g(it, "fmlyCnt")), _num(g(it, "hoCnt"))
            purp = g(it, "mainPurpsCdNm")
            score = hh * 3 + fm * 3 + ho + (100 if ("주택" in purp or "공동주택" in purp) else 0)
            if score > best_score:
                best, best_score = it, score
        it = best
        used = g(it, "useAprDay")
        hh, fm, ho = _num(g(it, "hhldCnt")), _num(g(it, "fmlyCnt")), _num(g(it, "hoCnt"))
        if hh > 0:
            units, label = hh, "세대"
        elif fm > 0:
            units, label = fm, "가구"
        elif ho > 0:
            units, label = ho, "호"
        else:
            units, label = 0, "세대"
        out = {
            "build_year": used[:4] if len(used) >= 4 else "",
            "units": units,
            "unit_label": label,
            "elevator": _num(g(it, "rideUseElvtCnt")) + _num(g(it, "emgenUseElvtCnt")),
            "purpose": g(it, "mainPurpsCdNm"),
            "floors": _num(g(it, "grndFlrCnt")),
        }
        self._cache[address] = out
        return out
