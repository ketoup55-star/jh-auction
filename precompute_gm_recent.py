# -*- coding: utf-8 -*-
"""공매 아파트/오피스텔 '최근 실거래가' 대량 사전계산 + 추정시세 없을 때 fallback 매수판정.

배경(주인님 지정 2026-07-08)
----------------------------
추정시세(같은평형 3/6개월 창)가 없는 공매 아파트/오피스텔은 종전에 무조건 '매수금지'였다.
대신 **최근 실거래가**(같은 단지·평형 최신 실거래, 창 무관)를 가져와:
  · 차익 = 최근실거래가 − 낙찰가(base=max(예상낙찰가, 현재 최저입찰가))
  · 차익 ≥ 3천만 → **매수검토**(추정시세 없어 '매수양호'는 안 줌) / 그 외 → 매수금지
  · 최근 실거래가 자체가 없으면 → 매수금지
물건상세엔 그 실거래 체결일도 표시(라이브 buy_grade v5가 summary.recent+recent_date 반환).

방식(대량 · 물건별 API 0회 · precompute_apt_chips.py 복제)
---------------------------------------------------------
  ① 국토부 실거래는 시군구 pool(api_cache 'aptpool:')만 로드/갱신 → 물건별 메모리 match_apt.
  ② 최근 실거래가 = match_apt(pool, 주소, 전용±5%).same_area[0]  (= 라이브 apt_info.summary.recent 동일).
     ※ 이 스크립트가 aptpool 을 예열하므로 이후 라이브 apt_info 도 같은 풀을 읽어 리스트=상세 일치.
  ③ base = max(예상낙찰가(gm_expbid: 캐시), min_price 컬럼) — rewarm_profit_batch.py 와 동일.
  ④ 시세없음(sise IS NULL) 물건만 grade/profit 재계산. 시세있는 물건은 grade 불변(recent만 저장).

저장(Supabase만)
----------------
  · gongmae_items.recent_trade_price BIGINT  (신규, 원 단위 · 최근 실거래 금액)
  · gongmae_items.recent_trade_date  TEXT    (신규, YYYY-MM-DD · 체결일)
  · 시세없음 물건: buy_grade/profit 갱신(fallback). 시세있는 물건: 위 2컬럼만.
psycopg(SUPABASE_DB_URL) 직접.
"""
from __future__ import annotations
import os
import re
import sys
import time
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8")
_R = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _R)
for _l in open(os.path.join(_R, ".env"), encoding="utf-8"):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        k, v = _l.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

import psycopg
from auction_analysis.lawd_codes import resolve_lawd
from auction_analysis.molit_source import MolitSource, match_apt

POOL_FRESH_S = 7 * 86400
THRESH = 30000000    # 매수검토 임계(차익 3천만)


def _area_num(*vals):
    """main._area_num 복제: '전용 Y㎡' 우선, 없으면 첫 숫자."""
    for v in vals:
        if v is None:
            continue
        s = str(v)
        m = re.search(r"전용\s*(\d+(?:\.\d+)?)", s)
        if m:
            return float(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        if m:
            return float(m.group(1))
    return None


def _to_int(v):
    try:
        return int(round(float(v)))
    except Exception:
        return None


def main():
    t0 = time.time()
    c = psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None,
                        connect_timeout=30, autocommit=True)

    # 0) 컬럼 보장
    c.execute("ALTER TABLE gongmae_items ADD COLUMN IF NOT EXISTS recent_trade_price BIGINT")
    c.execute("ALTER TABLE gongmae_items ADD COLUMN IF NOT EXISTS recent_trade_date TEXT")
    print(f"[0] recent_trade_price/date 컬럼 보장 ({time.time()-t0:.1f}s)", flush=True)

    # 1) 대상 = 아파트/오피스텔 전량 (+ sise·min_price 컬럼)
    rows = c.execute("""SELECT id, manage_no, usage, data, sise, min_price
                        FROM gongmae_items
                        WHERE usage ~ '(아파트|오피스텔)'""").fetchall()
    print(f"[1] 대상 {len(rows)}건 로드 ({time.time()-t0:.1f}s)", flush=True)

    # 2) enrich(gm_enrich:) 대량 로드 — addr_jibun·bld_area
    key_of = {}
    for _id, mng, usage, data, sise, minp in rows:
        cdtn = (data or {}).get("pbct_cdtn_no") or ""
        key_of[_id] = "gm_enrich:" + mng + ((":" + cdtn) if cdtn else "")
    enrich = {}
    uk = list(set(key_of.values()))
    for i in range(0, len(uk), 4000):
        for k, d in c.execute("SELECT cache_key, data FROM api_cache WHERE cache_key = ANY(%s)",
                              (uk[i:i + 4000],)).fetchall():
            if isinstance(d, dict):
                enrich[k] = d
    print(f"[2] enrich {len(enrich)}건 로드 ({time.time()-t0:.1f}s)", flush=True)

    # 3) 주소·lawd·area 산출 + 필요한 시군구 pool 목록
    prep = {}
    lawds_needed = set()
    for _id, mng, usage, data, sise, minp in rows:
        e = enrich.get(key_of[_id])
        if not e:
            continue
        aj = (e.get("addr_jibun") or "").strip()
        if not aj:
            continue
        floor = (data or {}).get("floor")
        addr = aj + (f" {floor}층" if floor else "")
        lawd = resolve_lawd(addr)
        area = _area_num(f"{e['bld_area']}㎡" if e.get("bld_area") else None)
        prep[_id] = {"addr": addr, "lawd": lawd, "area": area}
        if lawd:
            lawds_needed.add(lawd)
    print(f"[3] 주소/lawd/area {len(prep)}건, 시군구 {len(lawds_needed)}개 ({time.time()-t0:.1f}s)", flush=True)

    # 4) 시군구 pool 로드(aptpool 7일 신선도, stale/miss는 molit 재계산+저장)
    pools = {}
    db_pool = {}
    lw = list(lawds_needed)
    for i in range(0, len(lw), 500):
        for k, d in c.execute("SELECT cache_key, data FROM api_cache WHERE cache_key = ANY(%s)",
                              (["aptpool:" + x for x in lw[i:i + 500]],)).fetchall():
            db_pool[k.split(":", 1)[1]] = d
    stale_or_missing = []
    for lawd in lawds_needed:
        d = db_pool.get(lawd)
        if isinstance(d, dict) and d.get("trades") is not None and (time.time() - d.get("ts", 0) < POOL_FRESH_S):
            pools[lawd] = d["trades"]
        else:
            stale_or_missing.append(lawd)
    print(f"[4] pool: fresh {len(pools)} / 재계산 {len(stale_or_missing)} ({time.time()-t0:.1f}s)", flush=True)
    if stale_or_missing:
        import json as _json
        ms = MolitSource()
        for n, lawd in enumerate(stale_or_missing, 1):
            try:
                tr = (ms.apt_recent_trades(lawd, months=12) or {}).get("trades") or []
            except Exception:
                tr = []
            pools[lawd] = tr
            if tr:
                try:
                    c.execute("""INSERT INTO api_cache (cache_key, data) VALUES (%s, %s)
                                 ON CONFLICT (cache_key) DO UPDATE SET data=excluded.data""",
                              ("aptpool:" + lawd, _json.dumps({"ts": time.time(), "trades": tr})))
                except Exception:
                    pass
            if n % 20 == 0 or n == len(stale_or_missing):
                print(f"    molit 재계산 {n}/{len(stale_or_missing)} ({time.time()-t0:.1f}s)", flush=True)

    # 5) 예상낙찰가 캐시(gm_expbid:) 대량 로드 — base 계산용
    expbid = {}
    ek = ["gm_expbid:" + mng for _id, mng, usage, data, sise, minp in rows]
    for i in range(0, len(ek), 4000):
        for k, d in c.execute("SELECT cache_key, data FROM api_cache WHERE cache_key = ANY(%s)",
                              (list(set(ek[i:i + 4000])),)).fetchall():
            if isinstance(d, dict) and d.get("available") and d.get("expected_bid"):
                expbid[k.split(":", 1)[1]] = _to_int(d["expected_bid"])
    print(f"[5] 예상낙찰가 캐시 {len(expbid)}건 ({time.time()-t0:.1f}s)", flush=True)

    # 6) 물건별 계산(메모리만)
    upd_recent = []   # (recent_price, recent_date, id) — 전체 아파트
    upd_grade = []    # (grade, profit, id) — 시세없음만
    n_recent = n_review = n_ban_norec = 0
    for _id, mng, usage, data, sise, minp in rows:
        p = prep.get(_id)
        recent_price = recent_date = None
        if p and p["lawd"]:
            pool = pools.get(p["lawd"])
            if pool:
                mt = match_apt(pool, p["addr"], area=p["area"], area_pct=0.05)
                if mt["area_matched"] and mt["same_area"]:
                    t = mt["same_area"][0]   # 최신(same_area는 deal_date desc) = apt_info.summary.recent 동일
                    recent_price = _to_int(t.get("amount"))
                    recent_date = t.get("deal_date")
        upd_recent.append((recent_price, recent_date, _id))
        if recent_price:
            n_recent += 1
        # 시세없음(sise NULL)만 fallback 재계산
        if sise is None:
            exp = expbid.get(mng)
            mp = _to_int(minp)
            cands = [x for x in (exp, mp) if x]
            base = max(cands) if cands else None
            if recent_price and base:
                pf = recent_price - base
                grade = "매수검토" if pf >= THRESH else "매수금지"
                if grade == "매수검토":
                    n_review += 1
                upd_grade.append((grade, pf, _id))
            else:
                n_ban_norec += 1
                upd_grade.append(("매수금지", None, _id))
    print(f"[6] 계산: 최근실거래 있음 {n_recent} · 시세없음→매수검토 {n_review} · 시세없음→매수금지(무자료) {n_ban_norec} ({time.time()-t0:.1f}s)", flush=True)

    # 7) 대량 UPDATE
    B = 1000
    for i in range(0, len(upd_recent), B):
        c.cursor().executemany(
            "UPDATE gongmae_items SET recent_trade_price=%s, recent_trade_date=%s WHERE id=%s",
            upd_recent[i:i + B])
    print(f"[7a] recent_trade UPDATE {len(upd_recent)}건 ({time.time()-t0:.1f}s)", flush=True)
    for i in range(0, len(upd_grade), B):
        c.cursor().executemany(
            "UPDATE gongmae_items SET buy_grade=%s, profit=%s WHERE id=%s",
            upd_grade[i:i + B])
    print(f"[7b] 시세없음 grade UPDATE {len(upd_grade)}건 ({time.time()-t0:.1f}s)", flush=True)
    c.close()
    print(f"[완료] recent {len(upd_recent)}·grade {len(upd_grade)}, 총 {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
