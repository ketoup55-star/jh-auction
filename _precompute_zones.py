# -*- coding: utf-8 -*-
"""상가 용도영역 사전계산 — 샤드 지원. 사용: python _precompute_zones.py <shard> <nshard>
표제부는 Supabase 공유캐시(jepyo:)라 샤드끼리 건축물대장 재호출 안 함. 필지 계산만 N배 병렬."""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, time, zlib
import concurrent.futures as cf
SHARD = int(sys.argv[1]) if len(sys.argv) > 1 else 0
NSHARD = int(sys.argv[2]) if len(sys.argv) > 2 else 1
for ln in open('.env', encoding='utf-8'):
    if '=' in ln and not ln.strip().startswith('#'):
        k, v = ln.strip().split('=', 1); os.environ.setdefault(k, v)
import api.main as M
from auction_analysis.usage_zones import compute_zones
db = M.auction_db
TAG = '[shard%d/%d]' % (SHARD, NSHARD)

keys = []; off = 0
while True:
    r = db._get('items', {'select': 'item_key,address', 'search_group': 'eq.상가',
                          'usage_name': 'not.ilike.*오피스텔*',     # 오피스텔 제외(이 지도 대상 아님)
                          'limit': '1000', 'offset': str(off)})
    rows = r.json() if r.status_code in (200, 206) else []
    keys += [(x['item_key'], x.get('address') or '') for x in rows]
    if len(rows) < 1000: break
    off += 1000
# 내 샤드 분량만(crc32 결정적)
mine = [(k, a) for k, a in keys if zlib.crc32(k.encode()) % NSHARD == SHARD]
# 캐시된 것 제외
cached = set()
mk = [k for k, _ in mine]
for i in range(0, len(mk), 100):
    try:
        got = db.cache_get_many(['usagezone:' + k for k in mk[i:i+100]]) or {}
    except Exception:
        got = {}
    for ck, v in got.items():
        if isinstance(v, dict) and v.get("v") == 11:
            cached.add(ck.split('usagezone:', 1)[1])
todo = [(k, a) for k, a in mine if k not in cached]
print('%s 상가전체 %d · 내샤드 %d · 캐시됨 %d · 계산대상 %d 시작 %s'
      % (TAG, len(keys), len(mine), len(cached), len(todo), time.strftime('%H:%M:%S')), flush=True)

done = saved = 0; t0 = time.time()
def one(ka):
    k, addr = ka
    try:
        ll = M._uz_geocode(addr)               # 블럭/획지 주소 폴백 포함
        if not ll:
            return 'nogeo'
        res = compute_zones(ll[1], ll[0], db=db)   # 표제부 공유캐시
        res["v"] = 11
        if res.get('available') and (res.get('red_count', 0) + res.get('blue_count', 0) + res.get('red_zone_count', 0)) > 0:
            db.cache_save('usagezone:' + k, res)
            return 'ok'
        return 'empty'
    except Exception as e:
        return 'err:' + type(e).__name__

with cf.ThreadPoolExecutor(max_workers=3) as ex:   # 단일프로세스 3병렬(V-World throttle 회피 균형점)
    for st in ex.map(one, todo):
        done += 1
        if st == 'ok': saved += 1
        if done % 50 == 0:
            print('%s %d/%d (%.0f분) 저장=%d' % (TAG, done, len(todo), (time.time()-t0)/60, saved), flush=True)
        time.sleep(0.05)
print('%s 완료 %.0f분 | 저장 %d' % (TAG, (time.time()-t0)/60, saved), flush=True)
