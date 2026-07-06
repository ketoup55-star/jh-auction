# -*- coding: utf-8 -*-
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os
for ln in open('.env', encoding='utf-8'):
    if '=' in ln and not ln.strip().startswith('#'):
        k, v = ln.strip().split('=', 1); os.environ.setdefault(k, v)
import api.main as M
db = M.auction_db

# 대상: 상가 - 오피스텔 (전 키)
keys = []; off = 0
while True:
    r = db._get('items', {'select': 'item_key', 'search_group': 'eq.상가',
                          'usage_name': 'not.ilike.*오피스텔*', 'limit': '1000', 'offset': str(off)})
    rows = r.json() if r.status_code in (200, 206) else []
    keys += [x['item_key'] for x in rows]
    if len(rows) < 1000:
        break
    off += 1000

# usagezone: 캐시된(계산완료) 수
done = empty = 0
for i in range(0, len(keys), 100):
    ch = keys[i:i + 100]
    try:
        got = db.cache_get_many(['usagezone:' + k for k in ch]) or {}
    except Exception:
        got = {}
    for ck, vv in got.items():
        if isinstance(vv, dict) and vv.get('v') == 1:
            done += 1

print('상가(오피스텔 제외) 대상: %d건' % len(keys))
print('usagezone 캐시 완료: %d건 (%.1f%%)' % (done, 100.0 * done / max(1, len(keys))))
print('남은 계산대상: %d건' % (len(keys) - done))
