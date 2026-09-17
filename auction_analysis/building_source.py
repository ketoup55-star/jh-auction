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
_URL_RECAP = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrRecapTitleInfo"  # 총괄표제부 = 집합건물 단지 총세대수(표제부는 동별/0이라 소규모 아파트 세대수 누락)
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

    def info(self, address: str, collective: bool = False) -> dict | None:
        """주소 → 건축물대장 표제부 요약. 실패 None(성공만 캐시).
        일일 호출한도 초과 감지 시 30분간 호출 스킵(헛대기 방지).
        collective=True(아파트·오피스텔·다세대 등 집합건물): 같은 지번의 단독주택 표제부를 후보에서 빼고
        총괄표제부(단지 총세대)를 우선한다."""
        import time
        if not (self.key and address):
            return None
        ck = f"{address}|{int(bool(collective))}"
        if ck in self._cache:
            return self._cache[ck]
        if time.time() < self._quota_block_until:   # 쿼터 소진 중 → 즉시 None(API 헛호출 방지)
            return None
        r = resolve_bjd(address)
        if not r:
            return None
        sgg, bjd, bun, ji = r
        # 🔴도로명주소는 resolve_bjd가 지번을 '0000-0000'(미상)으로 돌려준다. 이걸 그대로 조회하면 API가 그 법정동의
        #   0-0 레코드("단독주택·1층·1가구·승강기0")를 돌려주고, 그게 물건 정보로 박혔다(실측: 오피스텔 1세대 209건 중 205건,
        #   집합건물 용도=단독주택 289건·승강기없음 290건). 지번 미상이면 호출하지 않는다 — 호출측이 detail_text 지번으로 재구성.
        if bun == "0000" and ji == "0000":
            return None
        root = None
        from .api_throttle import throttle
        for _try in range(3):                     # ★재시도(타임아웃·일시오류) — 부하 구간에서 조용히 None 되던 것 방지
            try:
                throttle()
                resp = httpx.get(_URL, params={"serviceKey": self.key, "sigunguCd": sgg,
                                               "bjdongCd": bjd, "bun": bun, "ji": ji,
                                               "numOfRows": "30", "_type": "xml"},
                                 headers=_UA, timeout=25)
                if "PER_SECOND" in resp.text or "resultCode>22<" in resp.text:   # 초당 제한 → 잠깐 쉬고 재시도
                    time.sleep(1.0 + _try)
                    continue
                if "quota exceeded" in resp.text or "LIMITED_NUMBER" in resp.text:
                    self._quota_block_until = time.time() + 1800   # 30분 차단
                    print("[building] 건축물대장 API 일일 쿼터 소진 → 30분 호출 중단(세대수·승강기 보완 지연)", flush=True)
                    return None
                root = ET.fromstring(resp.text)
                break
            except Exception:
                time.sleep(1.0 + _try)
        if root is None:
            return None
        items = root.findall(".//item")
        if not items:
            return None

        def g(it, t):
            v = it.findtext(t)
            return v.strip() if v else ""

        # 같은 지번에 여러 동이면 '주거·세대 많은' 동 우선.
        # ★집합건물이면 단독주택 표제부는 후보에서 제외 — 같은 지번의 경비동·이웃 단독주택(1가구)이 '주택'+100 점수로
        #   아파트 동(표제부 세대 0)을 이기던 것(실측: api경로 저세대 아파트 76건 중 purpose=단독주택 43건).
        if collective:
            _cand = [it for it in items if "단독주택" not in g(it, "mainPurpsCdNm")]
            items = _cand or items
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
        units_src = "title"                       # title=표제부(동 단위) / recap=총괄표제부(단지 총세대)
        if hh > 0:
            units, label = hh, "세대"
        elif fm > 0:
            units, label = fm, "가구"
        elif ho > 0:
            units, label = ho, "호"
        else:
            units, label = 0, "세대"
        # 🔴집합건물 단지 총세대수는 총괄표제부(getBrRecapTitleInfo)에 있다 — 표제부는 동별/0이라 소규모·나홀로
        #   아파트 세대수가 누락됐다(광우무지개맨션 표제부0 vs 총괄312, 명지한신휴 표제부10 vs 총괄841). kapt 미등록도 커버.
        #   표제부 세대수가 총괄보다 작으면 총괄표제부 총세대수로 교체(단독·다가구는 총괄에 없어 그대로 유지).
        #   ★재시도(2026-09-17): 병렬 재계산 중 recap 호출이 타임아웃/일시오류로 조용히 실패하면 상가동·경비실 표제부만 남아
        #     세대수가 통째로 비었다(실측: 그랑힐스 5,050·가능역하우스토리 121이 서버에선 None). 2회 재시도 + 실패 표식.
        recap_failed = False
        for _try in range(3):
            try:
                throttle()
                _rc = httpx.get(_URL_RECAP, params={"serviceKey": self.key, "sigunguCd": sgg,
                                                    "bjdongCd": bjd, "bun": bun, "ji": ji,
                                                    "numOfRows": "30", "_type": "xml"},
                                headers=_UA, timeout=25)
                if "quota exceeded" in _rc.text or "LIMITED_NUMBER" in _rc.text:
                    self._quota_block_until = time.time() + 1800
                    recap_failed = True
                    break
                _rec = 0
                for _it in ET.fromstring(_rc.text).findall(".//item"):
                    _rec = max(_rec, _num(g(_it, "hhldCnt")), _num(g(_it, "fmlyCnt")))
                if _rec > units:
                    units, label, units_src = _rec, "세대", "recap"
                recap_failed = False
                break
            except Exception:
                recap_failed = True
                time.sleep(1.5 * (_try + 1))
        if recap_failed and collective:
            return None                 # 집합건물인데 총괄을 못 받았으면 부분정보로 오염시키지 말고 이번엔 없음(캐시 안 함 → 재시도)
        # 숙박 세부용도(여관·생활숙박 등): 생숙은 주용도(mainPurpsCdNm)가 아니라 기타용도(etcPurps)에만 기재되는
        #  경우가 많아, 전체 동(item)의 주용도+기타용도를 합쳐 스캔(가장 우선순위 높은 라벨).
        from .building_doc_parser import sukbak_subtype
        _alltext = " ".join(((i.findtext("mainPurpsCdNm") or "") + " " + (i.findtext("etcPurps") or ""))
                            for i in items)
        out = {
            "build_year": used[:4] if len(used) >= 4 else "",
            "units": units,
            "unit_label": label,
            "units_src": units_src,                # recap=단지 총세대(신뢰) / title=선택된 한 동의 표제부
            "elevator": _num(g(it, "rideUseElvtCnt")) + _num(g(it, "emgenUseElvtCnt")),
            "purpose": g(it, "mainPurpsCdNm"),
            "floors": _num(g(it, "grndFlrCnt")),
            "sukbak_sub": sukbak_subtype(_alltext),
        }
        self._cache[ck] = out
        return out
