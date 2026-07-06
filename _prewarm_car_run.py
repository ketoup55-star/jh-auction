# -*- coding: utf-8 -*-
"""강제 예열(1회) — 차량외(자동차/중기) 엔카 동일중고차 평균시세 → 공유 DB 캐시(encar2:).
지오코딩 없음(엔카 Neon DB). auction_encar_avgs(compute=True)가 미캐시만 계산+저장."""
import os, time

for line in open('.env', encoding='utf-8'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip()

from api import main as M  # noqa: E402
db = M.auction_db

rows, off = [], 0
while True:
    r = db._get('items', {'select': 'item_key', 'search_group': 'eq.차량외', 'limit': '1000', 'offset': str(off)})
    page = r.json() if r.status_code in (200, 206) else []
    rows += [x['item_key'] for x in page]
    if len(page) < 1000:
        break
    off += 1000
print(f'차량외 대상: {len(rows)}건', flush=True)

t0 = time.time()
done = 0
for i in range(0, len(rows), 40):
    chunk = rows[i:i + 40]
    try:
        M.auction_encar_avgs(','.join(chunk), compute=True)
    except Exception:
        pass
    done += len(chunk)
    el = time.time() - t0
    rate = done / el if el else 0
    eta = (len(rows) - done) / rate if rate else 0
    print(f'  진행 {done}/{len(rows)} | 경과 {el/60:.1f}분 | ETA {eta/60:.0f}분', flush=True)

print(f'완료: {done}건, 총 {(time.time()-t0)/60:.1f}분', flush=True)
