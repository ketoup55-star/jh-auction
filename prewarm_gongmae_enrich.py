# -*- coding: utf-8 -*-
"""공매 물건상세 gm_enrich 예열 — 상세 진입 시 온비드 상세(2~4초) 대기 제거.
미캐시 물건만 /gongmae/enrich 호출(서버가 온비드 조회+캐시 저장). 8병렬(온비드 32는 큐잉). 로컬 전용."""
import os, sys, time, httpx
from dotenv import load_dotenv; load_dotenv(r"C:\Users\red85\부동산경매\.env")
import psycopg
from concurrent.futures import ThreadPoolExecutor
L = "http://127.0.0.1:4011"
c = psycopg.connect(os.environ["SUPABASE_DB_URL"], connect_timeout=25, autocommit=True, prepare_threshold=None)
cur = c.cursor()
# 미캐시 물건(gm_enrich: 없음) — 주거·토지 우선(상세 진입 많음)
cur.execute("""SELECT manage_no, data->>'pbct_cdtn_no' FROM gongmae_items
  WHERE prop_type IN ('압류재산','기타일반재산') ORDER BY id DESC""")
rows = cur.fetchall()
# 이미 캐시된 것 제외
cur.execute("SELECT substring(cache_key from 11) FROM api_cache WHERE cache_key LIKE 'gm_enrich:%'")
cached = set(r[0] for r in cur.fetchall())
c.close()
todo = [(m, d) for (m, d) in rows if (m + ":" + (d or "")) not in cached]
_W = int(sys.argv[1]) if len(sys.argv) > 1 else 8
print(f"[enrich] 대상 {len(todo)}건 / 전체 {len(rows)} (캐시 {len(cached)})", flush=True)
def warm(row):
    m, d = row; q = f"?mng={m}" + (f"&cdtn={d}" if d else "")
    try:
        httpx.get(L + "/gongmae/enrich" + q, timeout=40); return 1
    except Exception:
        return 0
ok = 0; t0 = time.time()
with ThreadPoolExecutor(max_workers=_W) as ex:
    for i, r in enumerate(ex.map(warm, todo)):
        ok += r
        if (i + 1) % 500 == 0:
            el = time.time() - t0; rate = (i + 1) / el
            print(f"[enrich] {i+1}/{len(todo)} ({round(rate,1)}건/초) 성공{ok} ETA~{round((len(todo)-i-1)/rate/3600,1)}시간", flush=True)
print(f"[enrich] 완료 {len(todo)} ({round(time.time()-t0)}초) 성공{ok}", flush=True)
