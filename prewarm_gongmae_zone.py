# -*- coding: utf-8 -*-
"""공매 zone(토지이용계획=용도지역) 주기 백필 — 신규 물건/신규 좌표의 zone을 자동 유지.
map_points 좌표(source=gongmae) → landuse_source(V-World LT_C_UQ111) → 경매 7종 카테고리 → gongmae_items.zone.
zone IS NULL AND 좌표있음만 처리(멱등·소량). V-World 32동시연결 최적(100은 큐잉으로 느림).
「JH옥션_공매zone백필」 매일 스케줄. 좌표동기화(JH옥션_지도좌표동기화) 이후 시각 권장."""
import os, sys, time
from dotenv import load_dotenv; load_dotenv(r"C:\Users\red85\부동산경매\.env")
sys.path.insert(0, r"C:\Users\red85\부동산경매")
import psycopg
from concurrent.futures import ThreadPoolExecutor
from auction_analysis.landuse_source import LandUseSource

def categorize(z):
    if not z: return None
    if "준주거" in z: return "준주거지역"
    if "주거지역" in z: return "주거지역"
    if "상업지역" in z: return "상업지역"
    if "준공업" in z: return "준공업지역"
    if "공업지역" in z: return "공업지역"
    if "녹지지역" in z: return "녹지지역"
    if "관리지역" in z: return "관리지역"
    return None

lu = LandUseSource()
c = psycopg.connect(os.environ["SUPABASE_DB_URL"], connect_timeout=25, autocommit=True, prepare_threshold=None)
cur = c.cursor()
cur.execute("""SELECT g.id, m.lat, m.lng FROM gongmae_items g
  JOIN map_points m ON m.item_key=g.id AND m.source='gongmae' AND m.lat IS NOT NULL
  WHERE g.zone IS NULL""")
rows = cur.fetchall()
print(f"[gm_zone] 대상(신규 zone NULL) {len(rows)}건", flush=True)
if not rows:
    print("[gm_zone] 신규 없음 — 종료", flush=True); c.close(); sys.exit(0)

def probe(row):
    k, lat, lng = row
    try:
        label, status = lu.zone_by_coord(float(lng), float(lat))
    except Exception:
        return (k, None)
    if label:
        return (k, categorize(label) or "기타")
    if status == "NOT_FOUND":
        return (k, "NF")
    return (k, None)

cat_ok = nf = err = 0; t0 = time.time()
_WORKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 32
with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
    for idx, (k, z) in enumerate(ex.map(probe, rows)):
        if z is None:
            err += 1
        else:
            cur.execute("UPDATE gongmae_items SET zone=%s WHERE id=%s", (z, k))
            if z == "NF": nf += 1
            else: cat_ok += 1
        if (idx + 1) % 500 == 0:
            el = time.time() - t0
            print(f"[gm_zone] {idx+1}/{len(rows)} 용도지역{cat_ok}·NF{nf}·err{err}", flush=True)
print(f"[gm_zone] 완료 {len(rows)} ({round(time.time()-t0)}초): 용도지역{cat_ok}·NF{nf}·err{err}", flush=True)
# 특수물건(지분/공유매각) is_share 동기화 — name 파싱(불변·외부호출 없음). 신규 물건 자동 유지.
try:
    r = cur.execute("""UPDATE gongmae_items SET is_share = (name ILIKE '%지분%' OR name ILIKE '%공유%')
        WHERE is_share IS DISTINCT FROM (name ILIKE '%지분%' OR name ILIKE '%공유%')""")
    print(f"[gm_zone] is_share 동기화: {r.rowcount}행", flush=True)
except Exception as e:
    print(f"[gm_zone] is_share 동기화 실패: {str(e)[:60]}", flush=True)
c.close()
