# -*- coding: utf-8 -*-
"""강제 풀가동 예열(1회) — 미캐시 빌라 nearby(유사거래/지도/공시가격) 전수 계산 → 공유 DB 캐시.
동시성↑(카카오 지오코딩, 실패 시 V-World 폴백). 진행률·ETA를 stdout에 기록."""
import os, time, sys, concurrent.futures as cf

for line in open('.env', encoding='utf-8'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip()

from api import main as M  # noqa: E402
db = M.auction_db


def _page(params):
    rows, off = [], 0
    while True:
        r = db._get('items', {**params, 'select': 'item_key', 'limit': '1000', 'offset': str(off)})
        page = r.json() if r.status_code in (200, 206) else []
        rows += [x['item_key'] for x in page]
        if len(page) < 1000:
            break
        off += 1000
    return rows


# 빌라류(다세대/연립/빌라/도시형) item_key 수집(중복 제거)
keys = set()
for pat in ('*다세대*', '*연립*', '*빌라*', '*도시형*'):
    keys |= set(_page({'usage_name': f'like.{pat}'}))
keys = list(keys)
print(f'빌라류 대상: {len(keys)}건', flush=True)

# 이미 nearby 캐시(geo_ok)된 건 스킵 — DB 일괄 조회
cached = set()
for i in range(0, len(keys), 100):
    chunk = keys[i:i + 100]
    rows = db.cache_get_many(['nearby:' + k for k in chunk])
    for k in chunk:
        d = rows.get('nearby:' + k)
        if isinstance(d, dict) and d.get('available') and d.get('v', 0) >= 2:
            cached.add(k)
todo = [k for k in keys if k not in cached]
print(f'캐시됨 {len(cached)} / 미캐시(예열대상) {len(todo)}', flush=True)

t0 = time.time()
done = 0


def warm(k):
    try:
        M.auction_nearby_trades(k)   # 계산+캐시(nearby:/geo:/gongsi:)
    except Exception:
        pass
    return k


with cf.ThreadPoolExecutor(max_workers=10) as ex:
    for _ in ex.map(warm, todo):
        done += 1
        if done % 20 == 0 or done == len(todo):
            el = time.time() - t0
            rate = done / el if el else 0
            eta = (len(todo) - done) / rate if rate else 0
            print(f'  진행 {done}/{len(todo)} | 경과 {el/60:.1f}분 | 속도 {rate*60:.0f}건/분 | ETA {eta/60:.0f}분', flush=True)

print(f'완료: {done}건, 총 {(time.time()-t0)/60:.1f}분', flush=True)
