# -*- coding: utf-8 -*-
"""공매 상세 features 예열 — 미방문 물건도 상세 진입 1초 이내(캐시화).
아파트류: apt_info·competing·expected_bid / 빌라류: building_brief·villa_est·nearby·villa_expected_bid.
토지 등은 상세 features 없음(loadGmFeatures가 스킵). 4011 서버 통해 호출→각 gm_* 캐시 저장. 물건 병렬.

★증분(2026-09-15 주인님): 이미 예열된(캐시 존재) feature는 재호출하지 않는다.
  기존엔 매일 전량(수천건×4~7 feature)을 HTTP 재호출해 새벽 CPU를 포화시키고(검색 경합) PC가 버벅였다.
  시작 시 gm_* 캐시 키를 한 번에 로드→미캐시 feature만 호출. 신규 물건(매일 crawl)만 소량 예열.
  시세 갱신 등으로 전량 재계산이 필요하면 `--force`.
⚠️쿼터: apt_info(국토부)·building_brief(건축물대장)·nearby(국토부) 일일쿼터 → 초과 시 다음날 이어서(멱등).
"""
import os, sys, re, time, httpx
from dotenv import load_dotenv; load_dotenv(r"C:\Users\red85\부동산경매\.env")
import psycopg
from concurrent.futures import ThreadPoolExecutor
L = "http://127.0.0.1:4011"

# feature → 캐시 키 접두(증분 판정). villa_est는 자체 캐시 없이 gm_nearby 기반이라 gm_nearby로 판정.
FEAT_CK = {"apt_info": "gm_apt:", "competing_listings": "gm_competing:", "expected_bid": "gm_expbid:",
           "building_brief": "gm_brief:", "villa_est": "gm_nearby:", "nearby_trades": "gm_nearby:",
           "villa_expected_bid": "gm_vexpbid:"}


def feats(u):
    if re.search(r"아파트|오피스텔", u):
        return ["apt_info", "competing_listings", "expected_bid"]
    if re.search(r"다세대|연립|빌라|도시형", u):
        return ["building_brief", "villa_est", "nearby_trades", "villa_expected_bid"]
    return []


FORCE = "--force" in sys.argv
_nums = [a for a in sys.argv[1:] if a.isdigit()]
_W = int(_nums[0]) if _nums else 4

c = psycopg.connect(os.environ["SUPABASE_DB_URL"], connect_timeout=25, autocommit=True, prepare_threshold=None)
cur = c.cursor()
cur.execute("""SELECT manage_no, data->>'pbct_cdtn_no', usage FROM gongmae_items
  WHERE usage ~ '아파트|오피스텔|다세대|연립|빌라|도시형' ORDER BY id DESC""")
rows = cur.fetchall()

# ★증분: 이미 캐시된 feature 키를 한 번에 로드 → 미캐시만 호출
cached = set()
if not FORCE:
    prefs = sorted(set(FEAT_CK.values()))
    where = " OR ".join("cache_key LIKE '%s%%'" % p for p in prefs)
    cur.execute("SELECT cache_key FROM api_cache WHERE " + where)
    cached = set(r[0] for r in cur.fetchall())
    print(f"[feat] 캐시 로드 {len(cached):,}건 — 미캐시만 예열(증분). 전량 재예열은 --force", flush=True)
c.close()

print(f"[feat] 대상 {len(rows)}건 (워커 {_W}, force={FORCE})", flush=True)


def warm(row):
    m, d, u = row
    q = f"?mng={m}" + (f"&cdtn={d}" if d else "")
    ok = err = skip = 0
    for f in feats(u):
        ck = FEAT_CK.get(f, "") + m
        if ck and ck in cached:
            skip += 1
            continue                       # 이미 예열됨 → 스킵(증분)
        try:
            httpx.get(L + "/gongmae/" + f + q, timeout=45); ok += 1
        except Exception:
            err += 1
    return ok, err, skip


tot_ok = tot_err = tot_skip = 0
t0 = time.time()
with ThreadPoolExecutor(max_workers=_W) as ex:
    for i, (ok, err, sk) in enumerate(ex.map(warm, rows)):
        tot_ok += ok; tot_err += err; tot_skip += sk
        if (i + 1) % 500 == 0:
            el = time.time() - t0; rate = (i + 1) / el
            print(f"[feat] {i+1}/{len(rows)} ({round(rate,1)}물건/초) 호출{tot_ok}·스킵{tot_skip}·err{tot_err} "
                  f"ETA~{round((len(rows)-i-1)/max(rate,0.01)/60,1)}분", flush=True)
print(f"[feat] 완료 {len(rows)} ({round(time.time()-t0)}초): 호출{tot_ok}·스킵{tot_skip}(증분)·err{tot_err}", flush=True)
