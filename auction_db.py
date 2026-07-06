# -*- coding: utf-8 -*-
"""경매 낙찰물건 조회 헬퍼.

기억할 2가지(이것 때문에 단순 조회로는 안 잡힘):
  ① 낙찰자·입찰자수·2등가는 items 가 아니라 **auction_schedule** 테이블에 있다.
  ② 매각되면 물건이 **백데이터**(data_class='백데이터')로 이동한다.
fetch_sold() 가 이 둘을 모두 처리해 낙찰물건을 한 번에 돌려준다.

사용:
    from auction_db import fetch_sold
    for r in fetch_sold(limit=100):          # 전체(백데이터 포함)
        print(r["address"], r["낙찰가"], r["낙찰자"], r["입찰자수"])

    fetch_sold(limit=50, usage="아파트")      # 용도 필터
"""

import os
import httpx

__all__ = ["fetch_sold"]


def _env():
    """auction_db.py 옆의 .env 를 읽어 Supabase 접속정보 구성(한 번)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    # 앱(main.py:98-100)과 동일한 fallback — .env 에 URL/KEY 가 없어도 동작(공개 가능 값)
    url = (os.environ.get("SUPABASE_URL")
           or "https://jakwbngokvlzehpjiozh.supabase.co").rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY")
           or os.environ.get("SUPABASE_KEY")
           or os.environ.get("SUPABASE_ANON_KEY")
           or "sb_publishable_OAKI_mJcm8v9M4n1WLRotQ_wF0sl5p-")
    return url + "/rest/v1/", {"apikey": key, "Authorization": "Bearer " + key}


_BASE, _H = _env()


def fetch_sold(limit=100, usage=None):
    """낙찰(매각)된 물건 목록을 반환한다.

    각 항목 키: item_key, address, 사건번호, 용도, 매각기일,
                낙찰가, 낙찰자, 입찰자수, 2등가, data_class

    - 낙찰가     : items.sale_price
    - 낙찰자     : auction_schedule(매각 회차).winner_name
    - 입찰자수   : auction_schedule(매각 회차).bid_count
    - 2등가      : auction_schedule(매각 회차).sale_2nd_price
    - 매각물건은 대부분 data_class='백데이터'. 현황에 남은 최근 낙찰도 함께 포함(data_class 무필터).

    Parameters
    ----------
    limit : int   최대 건수(매각기일 최신순)
    usage : str   용도명 필터(예: '아파트'). None이면 전체.
    """
    p = {
        "select": "item_key,address,sale_price,sell_date,case_no,usage_name,data_class",
        "sale_price": "gt.0",                 # 낙찰가 있는 = 매각된 물건
        "order": "sell_date.desc",
        "limit": str(int(limit)),
    }
    if usage:
        p["usage_name"] = f"eq.{usage}"
    items = httpx.get(_BASE + "items", params=p, headers=_H, timeout=60).json()
    items = items if isinstance(items, list) else []

    # ① 낙찰자/입찰자수/2등가 — auction_schedule 의 '매각' 회차에서 조인
    sched = {}
    keys = [it["item_key"] for it in items]
    if keys:
        inq = "in.(" + ",".join('"' + k + '"' for k in keys) + ")"
        rows = httpx.get(
            _BASE + "auction_schedule",
            params={"select": "item_key,result,bid_count,winner_name,sale_2nd_price",
                    "item_key": inq, "result": "like.매각*"},
            headers=_H, timeout=60,
        ).json()
        for r in (rows if isinstance(rows, list) else []):
            sched.setdefault(r["item_key"], r)     # 물건당 매각 회차 1건

    out = []
    for it in items:
        s = sched.get(it["item_key"], {})
        out.append({
            "item_key": it["item_key"],
            "address": it.get("address"),
            "사건번호": it.get("case_no"),
            "용도": it.get("usage_name"),
            "매각기일": (it.get("sell_date") or "")[:10],
            "낙찰가": it.get("sale_price"),
            "낙찰자": s.get("winner_name"),
            "입찰자수": s.get("bid_count"),
            "2등가": s.get("sale_2nd_price"),
            "data_class": it.get("data_class"),
        })
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for r in fetch_sold(limit=10):
        print(r["address"], r["낙찰가"], r["낙찰자"], r["입찰자수"])
