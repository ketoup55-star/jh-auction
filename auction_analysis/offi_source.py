# -*- coding: utf-8 -*-
"""국토부 오피스텔 매매·전월세 실거래 (data.go.kr RTMSDataSvcOffi*). 아파트와 유사 구조.
매매: dealAmount, 전월세: deposit/monthlyRent(0=전세). 단지=offiNm, 면적=excluUseAr."""
from __future__ import annotations
import os, datetime
import threading as _th
import concurrent.futures as _cf
import xml.etree.ElementTree as ET
import httpx

_KEY = os.environ.get("ONBID_SERVICE_KEY", "")
_TRADE = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade"
_RENT = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent"
_UA = {"User-Agent": "Mozilla/5.0"}


def _to_int(s):
    try:
        return int(str(s).replace(",", "").strip() or 0)
    except Exception:
        return 0


def _ym_list(months: int):
    d = datetime.date.today()
    out = []
    y, m = d.year, d.month
    for _ in range(months):
        out.append("%04d%02d" % (y, m))
        m -= 1
        if m == 0:
            m = 12; y -= 1
    return out


def _fetch(url: str, lawd: str, ym: str, rent: bool):
    try:
        r = httpx.get(url, params={"serviceKey": _KEY, "LAWD_CD": lawd, "DEAL_YMD": ym,
                                   "numOfRows": "1000", "pageNo": "1"}, headers=_UA, timeout=30)
        root = ET.fromstring(r.text)
        if (root.findtext(".//resultCode") or "") not in ("000", "00"):
            return []
    except Exception:
        return []
    out = []
    for it in root.findall(".//item"):
        def g(t):
            e = it.find(t); return (e.text or "").strip() if e is not None else ""
        rec = {
            "name": g("offiNm"), "umd": g("umdNm"), "jibun": g("jibun"),
            "area": float(g("excluUseAr") or 0) or None, "floor": _to_int(g("floor")) or None,
            "build_year": _to_int(g("buildYear")) or None,
            "deal_date": "%s-%02d-%02d" % (_to_int(g("dealYear")), _to_int(g("dealMonth")), _to_int(g("dealDay"))),
        }
        if rent:
            rec["deposit"] = _to_int(g("deposit")) * 10000      # 만원 → 원
            rec["monthly"] = _to_int(g("monthlyRent")) * 10000
        else:
            rec["amount"] = _to_int(g("dealAmount")) * 10000
        out.append(rec)
    return out


_DEALS_MEMO: dict = {}      # (lawd, months, rent) -> deals. 시군구 단위 재사용(같은 시군구 오피스텔 대량 예열 가속). 캡으로 증가 방지.
_DEALS_LOCK = _th.Lock()


def offi_deals(lawd: str, months: int = 12, rent: bool = False) -> list:
    """시군구(lawd) 최근 months개월 오피스텔 매매(rent=False)/전월세(rent=True) 실거래.
    월별 병렬 fetch + 시군구 단위 메모 — 직렬 12개월·시군구 재fetch가 예열 병목이라 가속(apt와 동일 원리)."""
    key = (lawd, months, rent)
    hit = _DEALS_MEMO.get(key)
    if hit is not None:
        return hit
    url = _RENT if rent else _TRADE
    out = []
    with _cf.ThreadPoolExecutor(max_workers=min(months, 12)) as ex:
        for rows in ex.map(lambda ym: _fetch(url, lawd, ym, rent), _ym_list(months)):
            out += rows
    with _DEALS_LOCK:
        if len(_DEALS_MEMO) > 1000:     # 라이브 장기실행 시 무한 증가 방지
            _DEALS_MEMO.clear()
        _DEALS_MEMO[key] = out
    return out
