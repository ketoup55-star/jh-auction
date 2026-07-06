# -*- coding: utf-8 -*-
"""강제 예열(1회) — 아파트/오피스텔 apt_info(실거래/단지정보/시세) 전수 → 공유 DB 캐시(apt:).
지오코딩 없음(molit 실거래 + kapt). auction_apt_ests(compute=True)가 미캐시만 계산+저장."""
import os, time

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


keys = set()
for pat in ('*아파트*', '*오피스텔*'):
    keys |= set(_page({'usage_name': f'like.{pat}'}))
keys = list(keys)
print(f'아파트/오피스텔 대상: {len(keys)}건', flush=True)

t0 = time.time()
done = 0
CH = 30
for i in range(0, len(keys), CH):
    chunk = keys[i:i + CH]
    try:
        M.auction_apt_ests(','.join(chunk), compute=True)   # 미캐시만 계산(6워커)+apt: 저장
    except Exception:
        pass
    done += len(chunk)
    el = time.time() - t0
    rate = done / el if el else 0
    eta = (len(keys) - done) / rate if rate else 0
    print(f'  진행 {done}/{len(keys)} | 경과 {el/60:.1f}분 | 속도 {rate*60:.0f}건/분 | ETA {eta/60:.0f}분', flush=True)

print(f'완료: {done}건, 총 {(time.time()-t0)/60:.1f}분', flush=True)
