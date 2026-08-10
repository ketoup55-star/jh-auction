# -*- coding: utf-8 -*-
"""매각물건(sale_price>0) 좌표 백필 → sold_coords. 인근 매각물건(nearby) 반경조회용.
resume: 이미 sold_coords 있는 것 스킵. 지오코딩 건당 ~0.12초(coords.db 캐시)."""
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
rows = cur.fetchall()
total = len(rows); start = time.time(); geo = 0; fail = 0
dump({'target': total, 'processed': 0, 'geo': 0, 'fail': 0, 'status': 'running'})
for i, (ik, addr) in enumerate(rows):
    ll = M._geocode(eb.geo_addr(addr))
    if ll:
        cur.execute("""insert into sold_coords(item_key,lng,lat) values(%s,%s,%s)
          on conflict(item_key) do update set lng=excluded.lng,lat=excluded.lat,updated_at=now()""",
          (ik, ll[0], ll[1]))
        geo += 1
    else:
        fail += 1
    if (i + 1) % 100 == 0:
        con.commit()
        dump({'target': total, 'processed': i + 1, 'geo': geo, 'fail': fail,
              'status': 'running', 'elapsed_min': round((time.time() - start) / 60, 1)})
con.commit()
dump({'target': total, 'processed': total, 'geo': geo, 'fail': fail,
      'status': 'done', 'elapsed_min': round((time.time() - start) / 60, 1)})
cur.close(); con.close()
