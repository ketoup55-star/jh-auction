# -*- coding: utf-8 -*-
"""경매 차량 엔카 시세(encar2:) 전량 재계산 — 가드(A) 반영 + stale/미계산 캐시 갱신.

근본원인: 현황 차량 655대 중 시세 캐시 16대뿐(324 stale count=0·315 미계산). 엔드포인트는 캐시
있으면 재계산 안 해(main.auction_encar_avgs 4720행) 크롤 새 데이터·가드가 반영 안 됨 → 강제 재계산+저장.
가드(A): 차명이 분류어(SUV·승용 등)만이면 매칭 금지 → 엉터리 광역시세(캐스퍼→벤츠 8305) 차단.
"""
import os
import sys
import time
import concurrent.futures as cf

sys.stdout.reconfigure(encoding="utf-8")
_R = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _R)
for _l in open(os.path.join(_R, ".env"), encoding="utf-8"):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        k, v = _l.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from api import main as M

t0 = time.time()
keys, off = [], 0
while True:
    r = M.auction_db._get("items", {"select": "item_key", "search_group": "eq.차량외",
                                    "data_class": "eq.현황", "limit": "1000", "offset": str(off)})
    rows = r.json() if r.status_code in (200, 206) else []
    keys += [x["item_key"] for x in rows if x.get("item_key")]
    if len(rows) < 1000:
        break
    off += 1000
print(f"[1] 현황 차량 {len(keys)}대 ({time.time()-t0:.1f}s)", flush=True)

done = [0]
npos = [0]
nneg = [0]


def one(k):
    try:
        d = M._compute_encar(k)                 # 가드 반영 재계산(캐시 무시)
        M.auction_db.cache_save("encar2:" + k, d)
        done[0] += 1
        if isinstance(d, dict) and d.get("count"):
            npos[0] += 1
        else:
            nneg[0] += 1
        if done[0] % 50 == 0:
            print(f"  {done[0]}/{len(keys)} · 시세有 {npos[0]} · 시세無 {nneg[0]} ({time.time()-t0:.1f}s)", flush=True)
    except Exception:
        pass


with cf.ThreadPoolExecutor(max_workers=6) as ex:
    list(ex.map(one, keys))
print(f"[완료] {done[0]}대 · 시세有 {npos[0]} · 시세無(분류어/무매칭) {nneg[0]} · 총 {time.time()-t0:.1f}s", flush=True)
