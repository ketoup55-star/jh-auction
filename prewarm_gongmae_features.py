# -*- coding: utf-8 -*-
"""공매 상세 features 예열 — 미방문 물건도 상세 진입 1초 이내(캐시화).
아파트류: apt_info·competing·expected_bid / 빌라류: building_brief·villa_est·nearby·villa_expected_bid.
토지 등은 상세 features 없음(loadGmFeatures가 스킵). 4011 서버 통해 호출→각 gm_* 캐시 저장. 물건 8병렬.
⚠️쿼터: apt_info(국토부)·building_brief(건축물대장)·nearby(국토부) 일일쿼터 → err율 모니터, 초과 시 다음날 이어서(멱등)."""
import os, sys, re, time, httpx
from dotenv import load_dotenv; load_dotenv(r"C:\Users\red85\부동산경매\.env")
import psycopg
from concurrent.futures import ThreadPoolExecutor
L = "http://127.0.0.1:4011"
def feats(u):
    if re.search(r"아파트|오피스텔", u): return ["apt_info", "competing_listings", "expected_bid"]
    if re.search(r"다세대|연립|빌라|도시형", u): return ["building_brief", "villa_est", "nearby_trades", "villa_expected_bid"]
    return []
c = psycopg.connect(os.environ["SUPABASE_DB_URL"], connect_timeout=25, autocommit=True, prepare_threshold=None)
cur = c.cursor()
cur.execute("""SELECT manage_no, data->>'pbct_cdtn_no', usage FROM gongmae_items
  WHERE usage ~ '아파트|오피스텔|다세대|연립|빌라|도시형' ORDER BY id DESC""")
rows = cur.fetchall()
c.close()
_W = int(sys.argv[1]) if len(sys.argv) > 1 else 8
print(f"[feat] 대상 {len(rows)}건 (아파트류+빌라류)", flush=True)
def warm(row):
    m, d, u = row; q = f"?mng={m}" + (f"&cdtn={d}" if d else "")
    ok = err = 0
    for f in feats(u):
        try:
            httpx.get(L + "/gongmae/" + f + q, timeout=45); ok += 1
        except Exception:
            err += 1
    return ok, err
tot_ok = tot_err = 0; t0 = time.time()
with ThreadPoolExecutor(max_workers=_W) as ex:
    for i, (ok, err) in enumerate(ex.map(warm, rows)):
        tot_ok += ok; tot_err += err
        if (i + 1) % 300 == 0:
            el = time.time() - t0; rate = (i + 1) / el
            print(f"[feat] {i+1}/{len(rows)} ({round(rate,1)}물건/초) 호출성공{tot_ok}·err{tot_err} ETA~{round((len(rows)-i-1)/rate/60,1)}분", flush=True)
print(f"[feat] 완료 {len(rows)} ({round(time.time()-t0)}초): 호출성공{tot_ok}·err{tot_err}", flush=True)
