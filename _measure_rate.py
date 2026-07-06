# -*- coding: utf-8 -*-
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, time
for ln in open('.env', encoding='utf-8'):
    if '=' in ln and not ln.strip().startswith('#'):
        k, v = ln.strip().split('=', 1); os.environ.setdefault(k, v)
import api.main as M
db = M.auction_db

keys = []; off = 0
while True:
    r = db._get('items', {'select': 'item_key', 'search_group': 'eq.상가',
                          'usage_name': 'not.ilike.*오피스텔*', 'limit': '1000', 'offset': str(off)})
    rows = r.json() if r.status_code in (200, 206) else []
    keys += [x['item_key'] for x in rows]
    if len(rows) < 1000: break
    off += 1000
TOT = len(keys)


def count_v2():
    done = 0
    for i in range(0, len(keys), 80):
        ch = keys[i:i + 80]
        got = None
        for _ in range(5):
            try:
                got = db.cache_get_many(['usagezone:' + k for k in ch]) or {}
            except Exception:
                got = {}
            if got: break
            time.sleep(0.5)
        for ck, vv in (got or {}).items():
            if isinstance(vv, dict) and vv.get('v') == 2:
                done += 1
    return done


WAIT = 600
a = count_v2(); t0 = time.time()
print('[A] %s · v2=%d / %d' % (time.strftime('%H:%M:%S'), a, TOT), flush=True)
time.sleep(WAIT)
b = count_v2(); dt = time.time() - t0
rate_min = (b - a) / (dt / 60.0)
remain = TOT - b
print('[B] %s · v2=%d / %d' % (time.strftime('%H:%M:%S'), b, TOT), flush=True)
print('증가 %d건 / %.1f분 → %.1f건/분 (%.0f건/시간)' % (b - a, dt / 60.0, rate_min, rate_min * 60), flush=True)
if rate_min > 0:
    eta_h = remain / rate_min / 60.0
    print('남은 %d건 → 연산 ETA ~%.1f시간 (쿼터 무시, 현재속도 유지 가정)' % (remain, eta_h), flush=True)
else:
    print('증가 0 — 멈춤/쿼터 의심', flush=True)
