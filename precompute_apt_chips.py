# -*- coding: utf-8 -*-
"""공매 아파트·오피스텔 목록 칩 2개(실거래 3개월 · 호가) 대량 사전계산 — 물건별 API 호출 없이.

배경
----
공매 목록(static/gongmae.html)의 아파트/오피스텔 행에 경매처럼 칩 2개를 즉시 렌더한다:
  · 실거래 N건 = 같은 단지·같은 평형(±5%) 최근 3개월(92일) 국토부 아파트 실거래 건수
  · 호가   N건 = KB부동산에 올라온 같은 단지·같은 평형(전용±3㎡) 매매 매물 수

물건별로 /gongmae/apt_info·/gongmae/competing_listings 를 호출하면 6,917건에 대해
재워밍처럼 무거워져 사이트가 느려진다(금지). 대신:
  ① 국토부 실거래는 **시군구 단위 pool**(api_cache 'aptpool:', molit_source.apt_recent_trades)만
     로드/갱신 → 물건별은 메모리에서 match_apt(지번/단지+전용±5%)로 카운트.
  ② KB는 **kb_complex(단지)·kb_listing(매매)를 대량 로드** → 메모리에서 각 물건 지번주소를
     단지 매칭(kb_crawler.match_address 로직을 local kb_complex 후보로 replicate)→ 같은평형 카운트.
  ③ 결과를 **대량 UPDATE**(psycopg executemany) — rewarm_profit_batch.py 방식.

정확성
------
라이브 엔드포인트(main.gongmae_apt_info / gongmae_competing_listings)의 매칭을 그대로 복제:
  · 주소       = enrich.addr_jibun + (' N층' if data.floor) — _gm_cur 와 동일
  · lawd       = resolve_lawd(주소)
  · area       = _area_num('{bld_area}㎡')   (전용면적 우선 정규식)
  · 실거래풀   = _apt_trades(lawd, 12): aptpool 캐시(7일 신선도) 우선, stale/miss는 molit 재계산+저장
  · 같은평형   = match_apt(pool, 주소, area, area_pct=0.05).same_area (area_matched 일 때만)
  · 실거래3mo  = 위 same_area(=apt_info.trades) 중 deal_date >= today-92d  (auctions.html trades_3m 규약)
  · 호가       = kb_listing(매매, complex_no, 전용±3㎡) 건수  (competing_listings 쿼리 복제)
샘플 검증: 라이브 KB매칭(gm_kbmatch complex_no 보유)·fresh aptpool 물건에서 batch==live 확인.

저장(Supabase만 — 레포에 없음)
------------------------------
  · gongmae_items.apt_hoga INT     (신규 컬럼, ADD COLUMN IF NOT EXISTS)
  · gongmae_items.nb_count 재활용   (아파트/오피스텔=실거래3mo; 빌라류 nb_count=유사거래는 불변)
psycopg(SUPABASE_DB_URL) 직접 — PostgREST(SUPABASE_URL) 안 씀.
"""
from __future__ import annotations
import os
import re
import sys
import time
from collections import defaultdict
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
import kb_crawler as kb   # extract_complex_name, _score, _sigungu_tokens, ACCEPT_THRESHOLD (no KB API unless we call match_address — we don't)

APT_RE = re.compile(r"아파트|오피스텔")
POOL_FRESH_S = 7 * 86400          # aptpool 신선도(_pool_from_db 와 동일)
CUT92 = (date.today() - timedelta(days=92)).isoformat()   # 실거래 3개월(92일) 컷오프
HOGA_BAND = 3.0                   # 전용 ±3㎡ (competing_listings 와 동일)


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


def main():
    t0 = time.time()
    c = psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None,
                        connect_timeout=30, autocommit=True)

    # 0) 컬럼 보장(Supabase만) — apt_hoga 신규. nb_count 는 기존.
    c.execute("ALTER TABLE gongmae_items ADD COLUMN IF NOT EXISTS apt_hoga INT")
    print(f"[0] apt_hoga 컬럼 보장 ({time.time()-t0:.1f}s)", flush=True)

    # 1) 대상 = 아파트/오피스텔 전량
    rows = c.execute("""SELECT id, manage_no, usage, data
                        FROM gongmae_items
                        WHERE usage ~ '(아파트|오피스텔)'""").fetchall()
    print(f"[1] 대상 {len(rows)}건 로드 ({time.time()-t0:.1f}s)", flush=True)

    # 2) enrich(gm_enrich:) 대량 로드 — addr_jibun·bld_area (라이브 _gm_cur 입력)
    key_of = {}
    for _id, mng, usage, data in rows:
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

    # 3) 물건별 주소·lawd·area 산출 + 필요한 시군구 pool 목록
    #    (라이브 _gm_cur: addr = addr_jibun + ' N층'; area = _area_num('{bld_area}㎡'))
    prep = {}   # id -> {addr, lawd, area}
    lawds_needed = set()
    for _id, mng, usage, data in rows:
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
    print(f"[3] 주소/lawd/area 산출: {len(prep)}건, 시군구 {len(lawds_needed)}개 ({time.time()-t0:.1f}s)", flush=True)

    # 4) 시군구 아파트 실거래 pool 로드(_apt_trades 복제: aptpool 7일 신선도, stale/miss는 molit 재계산+저장)
    pools = {}
    # 4a) DB에서 기존 pool 일괄 로드
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
    print(f"[4] pool: fresh {len(pools)}개 / 재계산필요 {len(stale_or_missing)}개 ({time.time()-t0:.1f}s)", flush=True)
    # 4b) stale/missing 은 molit 로 재계산(시군구 단위·12개월 병렬) + aptpool 저장(라이브와 동일 정책)
    if stale_or_missing:
        import json as _json
        ms = MolitSource()
        for n, lawd in enumerate(stale_or_missing, 1):
            try:
                tr = (ms.apt_recent_trades(lawd, months=12) or {}).get("trades") or []
            except Exception:
                tr = []
            pools[lawd] = tr
            if tr:   # 빈 결과(할당량 등)는 저장 안 함(_pool_to_db 와 동일)
                try:
                    c.execute("""INSERT INTO api_cache (cache_key, data) VALUES (%s, %s)
                                 ON CONFLICT (cache_key) DO UPDATE SET data=excluded.data""",
                              ("aptpool:" + lawd, _json.dumps({"ts": time.time(), "trades": tr})))
                except Exception:
                    pass
            if n % 20 == 0 or n == len(stale_or_missing):
                print(f"    molit 재계산 {n}/{len(stale_or_missing)} ({time.time()-t0:.1f}s)", flush=True)

    # 5) KB 대량 로드: kb_complex(후보) + kb_listing(매매 전용면적)
    kbc_rows = c.execute("SELECT complex_no,name,bubaddr,households FROM kb_complex").fetchall()
    sgg_idx = defaultdict(list)   # 시군구토큰 -> [cand,...]
    for cno, name, bub, hh in kbc_rows:
        cand = {"COMPLEX_NO": str(cno), "HSCM_NM": name or "", "HSCM_NM_EXT": "",
                "HSCM_TAG": "", "BUBADDR": bub or "", "THS_NUM": hh}
        for t in set(kb._sigungu_tokens(bub or "")):
            sgg_idx[t].append(cand)
    lst_rows = c.execute("SELECT complex_no, area_excl FROM kb_listing WHERE trade_type='매매'").fetchall()
    listings = defaultdict(list)
    for cno, ax in lst_rows:
        listings[str(cno)].append(float(ax) if ax is not None else None)
    print(f"[5] KB 로드: 단지 {len(kbc_rows)}, 매매매물 {len(lst_rows)} ({time.time()-t0:.1f}s)", flush=True)

    # 5b) 이미 매칭된 KB 단지(gm_kbmatch:)는 라이브 결과 우선 사용(정확도)
    kbmatch = {}   # mng -> {complex_no, region_ok}
    for k, d in c.execute("SELECT cache_key, data FROM api_cache WHERE cache_key LIKE 'gm_kbmatch:%'").fetchall():
        if isinstance(d, dict):
            kbmatch[k.split(":", 1)[1]] = d

    def local_match(address):
        """match_address 를 local kb_complex 후보로 replicate. 반환 (complex_no|None, region_ok)."""
        name = kb.extract_complex_name(address)
        if not name:
            return None, False
        pool = []
        seen = set()
        for t in kb._sigungu_tokens(address):
            for cd in sgg_idx.get(t, []):
                if cd["COMPLEX_NO"] not in seen:
                    seen.add(cd["COMPLEX_NO"])
                    pool.append(cd)
        best, best_score, best_region = None, -1.0, False
        for cd in pool:
            s, region_ok = kb._score(cd, address, name)
            if s > best_score:
                best, best_score, best_region = cd, s, region_ok
        if best and best_score >= kb.ACCEPT_THRESHOLD and best_region:
            return best["COMPLEX_NO"], True
        return None, best_region

    def hoga_of(cno, area):
        axs = listings.get(str(cno), [])
        if not axs:
            return 0
        if area is None:
            return len(axs)
        lo, hi = round(area - HOGA_BAND, 2), round(area + HOGA_BAND, 2)
        return sum(1 for a in axs if a is not None and lo <= a <= hi)

    # 6) 물건별 계산(메모리만)
    updates = []   # (nb_count, apt_hoga, id)
    n_real = n_hoga = 0
    for _id, mng, usage, data in rows:
        p = prep.get(_id)
        if not p:
            continue
        addr, lawd, area = p["addr"], p["lawd"], p["area"]
        # 실거래 3개월
        nb = 0
        pool = pools.get(lawd) if lawd else None
        if pool:
            mt = match_apt(pool, addr, area=area, area_pct=0.05)
            if mt["area_matched"]:
                trades = mt["same_area"][:100]
                nb = sum(1 for t in trades if (t.get("deal_date") or "") >= CUT92)
        # 호가: gm_kbmatch 우선, 없으면 local_match
        km = kbmatch.get(mng)
        if isinstance(km, dict) and (km.get("complex_no") or km.get("region_ok") is not None):
            cno = km.get("complex_no")
            region_ok = km.get("region_ok")
        else:
            cno, region_ok = local_match(addr)
        hg = hoga_of(cno, area) if (cno and region_ok is not False) else 0
        if nb:
            n_real += 1
        if hg:
            n_hoga += 1
        updates.append((nb, hg, _id))
    print(f"[6] 계산 완료: {len(updates)}건 (실거래>0 {n_real} · 호가>0 {n_hoga}) ({time.time()-t0:.1f}s)", flush=True)

    # 7) 대량 UPDATE (executemany · rewarm_profit_batch 방식)
    B = 1000
    for i in range(0, len(updates), B):
        c.cursor().executemany(
            "UPDATE gongmae_items SET nb_count=%s, apt_hoga=%s WHERE id=%s", updates[i:i + B])
        print(f"[7] UPDATE {min(i+B, len(updates))}/{len(updates)} ({time.time()-t0:.1f}s)", flush=True)
    c.close()
    print(f"[완료] {len(updates)}건 갱신, 총 {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
