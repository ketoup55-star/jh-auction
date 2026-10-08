# -*- coding: utf-8 -*-
"""매각물건(sale_price>0) 좌표 백필 → sold_coords. 인근 매각물건(nearby) 반경조회용.
개선: ①병렬12(네트워크 지오코딩 대기 병렬, 소량검증 5.0배) ②geo:DB캐시 프리로드(이미
지오코딩된 좌표 31% 재활용→네트워크·카카오쿼터 절약). resume: sold_coords 있는 것 스킵.
DB커밋은 메인스레드만(단일 psycopg 커넥션 안전), 지오코딩(네트워크)만 스레드 병렬."""
import sys, os, time, json
sys.path.insert(0, r'C:\Users\red85\부동산경매')
os.chdir(r'C:\Users\red85\부동산경매')
try:
    sys.stdout.reconfigure(encoding='utf-8')   # pythonw는 stdout=None → 가드
except Exception:
    pass
os.environ['KB_LOG_FILE'] = r'C:\Users\red85\부동산경매\kb_sold_coords_crawler.log'
from dotenv import load_dotenv; load_dotenv('.env', override=True)
import api.main as M
from auction_analysis import expected_bid as eb
from kb_crawler import _db_connect
from concurrent.futures import ThreadPoolExecutor

PROG = r'C:\Users\red85\부동산경매\kb_sold_coords_progress.json'
def dump(d):
    try:
        with open(PROG, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass

con = _db_connect(); con.autocommit = False; cur = con.cursor()
cur.execute("""select item_key, address from items
  where sale_price > 0 and item_key not in (select item_key from sold_coords)
  order by item_key""")
# (item_key, geo_addr) — geo_addr None(주소파싱 실패=지오코딩 불가)은 대상 제외
pairs = [(ik, eb.geo_addr(a)) for ik, a in cur.fetchall() if a]
pairs = [(ik, g) for ik, g in pairs if g]
total = len(pairs); start = time.time(); geo = 0; fail = 0
dump({'target': total, 'processed': 0, 'geo': 0, 'fail': 0, 'status': 'preloading'})

# ── ① geo:DB캐시 프리로드: 이미 지오코딩된 좌표를 메모리캐시에 병합(네트워크·쿼터 절약) ──
uniq = list({g for _, g in pairs})
pre = 0
for i in range(0, len(uniq), 2000):
    try:
        hit = M.auction_db.cache_get_many(["geo:" + g for g in uniq[i:i + 2000]])
        for k, v in hit.items():
            try:
                ll = v.get("ll") if isinstance(v, dict) else None
                if ll:
                    M._geo_cache[k[4:]] = ll; pre += 1   # k[4:] = "geo:" 제거한 주소
            except Exception:
                pass
    except Exception:
        pass
dump({'target': total, 'processed': 0, 'geo': 0, 'fail': 0, 'status': 'running', 'preloaded': pre})

# ── ② 병렬12 지오코딩(네트워크 대기 병렬) + 메인스레드 순차 DB커밋 ──
def geo_one(pr):
    ik, g = pr
    return ik, M._geocode(g)      # _geo_cache 히트면 즉시, 미스면 네트워크

with ThreadPoolExecutor(max_workers=12) as ex:
    for i, (ik, ll) in enumerate(ex.map(geo_one, pairs)):
        if ll:
            cur.execute("""insert into sold_coords(item_key,lng,lat) values(%s,%s,%s)
              on conflict(item_key) do update set lng=excluded.lng,lat=excluded.lat,updated_at=now()""",
              (ik, ll[0], ll[1]))
            geo += 1
        else:
            fail += 1
        if (i + 1) % 200 == 0:
            con.commit()
            dump({'target': total, 'processed': i + 1, 'geo': geo, 'fail': fail,
                  'status': 'running', 'preloaded': pre,
                  'elapsed_min': round((time.time() - start) / 60, 1)})
con.commit()
dump({'target': total, 'processed': total, 'geo': geo, 'fail': fail,
      'status': 'done', 'preloaded': pre,
      'elapsed_min': round((time.time() - start) / 60, 1)})
cur.close(); con.close()
