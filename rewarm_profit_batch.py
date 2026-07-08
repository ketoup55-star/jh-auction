# -*- coding: utf-8 -*-
"""공매 차익 base=max 일괄 재계산 — 파이프라인 재실행 없이 저장값으로 직접.

느린 재워밍(buy_grade 파이프라인: villa_est+expected_bid+nearby+bid_schedule per item,
Supabase 포화)의 대체. base=max 차익은 ①sise 컬럼 ②min_price 컬럼 ③예상낙찰가 캐시
(gm_vexpbid:/gm_expbid:) 3개만 있으면 계산되고, 이 값들은 이미 저장돼 있으므로
대량읽기 + 대량 UPDATE 한 번으로 수분 내 완료(저부하).

라이브 buy_grade와 결과 동일 검증됨(6/6). buy_grade/profit 컬럼만 갱신(sise·nb_count 불변).
"""
import os, sys, time, psycopg
sys.stdout.reconfigure(encoding="utf-8")
_R = os.path.dirname(os.path.abspath(__file__))
for _l in open(os.path.join(_R, ".env"), encoding="utf-8"):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        k, v = _l.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

VILLA = ("다세대", "연립", "빌라", "도시형")
THRESH = 30000000
c = psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None, connect_timeout=30, autocommit=False)

t0 = time.time()
rows = c.execute("""SELECT id, manage_no, usage, sise, min_price
                    FROM gongmae_items
                    WHERE usage ~ '(다세대|연립|빌라|도시형|아파트|오피스텔)'""").fetchall()
print(f"[1] 대상 {len(rows)}건 로드 ({time.time()-t0:.1f}s)", flush=True)

# 예상낙찰가 캐시 키 수집 → 청크로 일괄 조회
keys = set()
for _id, mng, usage, sise, minp in rows:
    isv = any(x in (usage or "") for x in VILLA)
    keys.add(("gm_vexpbid:" if isv else "gm_expbid:") + mng)
keys = list(keys)
cache = {}
for i in range(0, len(keys), 4000):
    ch = keys[i:i+4000]
    for k, d in c.execute("SELECT cache_key, data FROM api_cache WHERE cache_key = ANY(%s)", (ch,)).fetchall():
        if isinstance(d, dict) and d.get("available") and d.get("expected_bid"):
            cache[k] = int(d["expected_bid"])
print(f"[2] 예상낙찰가 캐시 {len(cache)}건 로드 ({time.time()-t0:.1f}s)", flush=True)

updates = []
dist = {"매수양호": 0, "매수검토": 0, "매수금지": 0, "skip": 0}
for _id, mng, usage, sise, minp in rows:
    isv = any(x in (usage or "") for x in VILLA)
    exp = cache.get(("gm_vexpbid:" if isv else "gm_expbid:") + mng)
    minp = int(minp) if minp is not None else None
    sise = int(sise) if sise is not None else None
    cands = [x for x in (exp, minp) if x]
    base = max(cands) if cands else None
    if not sise:
        grade, profit = "매수금지", None
    elif base is None:
        dist["skip"] += 1; continue
    elif base > sise:
        grade, profit = "매수금지", sise - base
    else:
        pf = sise - base
        grade, profit = ("매수양호" if pf >= THRESH else "매수검토"), pf
    dist[grade] += 1
    updates.append((grade, profit, _id))
print(f"[3] 계산 완료: 양호 {dist['매수양호']} 검토 {dist['매수검토']} 금지 {dist['매수금지']} skip {dist['skip']} ({time.time()-t0:.1f}s)", flush=True)

cur = c.cursor()
B = 1000
for i in range(0, len(updates), B):
    cur.executemany("UPDATE gongmae_items SET buy_grade=%s, profit=%s WHERE id=%s", updates[i:i+B])
    c.commit()
    print(f"[4] UPDATE {min(i+B, len(updates))}/{len(updates)} ({time.time()-t0:.1f}s)", flush=True)
c.close()
print(f"[완료] {len(updates)}건 갱신, 총 {time.time()-t0:.1f}s", flush=True)
