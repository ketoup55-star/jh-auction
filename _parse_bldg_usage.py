# -*- coding: utf-8 -*-
"""다세대/도생 건축물대장 전유부 용도 전수 파싱(젠틀판). media는 미리 일괄조회(Supabase 부하 최소),
PDF 파싱은 3병렬 R2 전용(검색 쿼리와 경합 안 함) + 체크포인트."""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, re, json, time
import concurrent.futures as cf
for ln in open('.env', encoding='utf-8'):
    if '=' in ln and not ln.strip().startswith('#'):
        k, v = ln.strip().split('=', 1); os.environ.setdefault(k, v)
import api.main as M
db = M.auction_db

NONRES = re.compile(r'근린생활시설|사무소|사무실|업무시설|판매시설|점포|소매점|제조업소|제1종근린|제2종근린')
RES = re.compile(r'다세대주택|공동주택|연립주택|도시형생활주택|아파트|오피스텔|단독주택|다가구주택|주택')

# 대상 키
keys = []; off = 0
while True:
    r = db._get('items', {'select': 'item_key', 'search_group': 'eq.주거용',
        'or': '(usage_name.like.*다세대*,usage_name.like.*도시형*)', 'limit': '1000', 'offset': str(off)})
    rows = r.json() if r.status_code in (200, 206) else []
    keys += [x['item_key'] for x in rows]
    if len(rows) < 1000: break
    off += 1000
print('대상 %d건. media 일괄조회 시작 %s' % (len(keys), time.strftime('%H:%M:%S')), flush=True)

# ① media(건축물대장 r2_key) 미리 일괄 — Supabase 부하는 여기서 짧게만
r2 = db.r2
urlmap = {}
for i in range(0, len(keys), 150):
    ch = keys[i:i + 150]
    inlist = ','.join('"' + k + '"' for k in ch)
    try:
        rows = db._get('media', {'select': 'item_key,r2_key', 'kind': 'eq.건축물대장',
                                 'item_key': 'in.(' + inlist + ')', 'limit': '300'}).json()
    except Exception:
        rows = []
    for row in rows:
        if row.get('r2_key') and row['item_key'] not in urlmap:
            urlmap[row['item_key']] = r2 + '/' + row['r2_key']
    time.sleep(0.2)   # Supabase 숨통
print('media 확보 %d건. PDF 파싱(3병렬, R2전용) 시작 %s' % (len(urlmap), time.strftime('%H:%M:%S')), flush=True)

def classify(ik):
    url = urlmap.get(ik)
    if not url:
        return ik, 'nodoc', None
    try:
        txt = M._pdf_text_pages(url, 3)   # R2 다운로드만(Supabase 무관)
    except Exception:
        return ik, 'err', None
    if not txt or len(txt) < 50:
        return ik, 'noText', None
    s = txt.find('전유부'); e = txt.find('공용부', s) if s >= 0 else -1
    if e < 0 and s >= 0: e = txt.find('소유자', s)
    sec = txt[s:e] if (s >= 0 and e > s) else txt[:600]
    nr = NONRES.search(sec); rs = RES.search(sec)
    if nr and not rs:
        return ik, 'NONRES', nr.group(0)
    return ik, ('RES' if rs else 'UNCLEAR'), None

flagged = {}; stat = {}; done = 0; t0 = time.time()
with cf.ThreadPoolExecutor(max_workers=3) as ex:   # 3병렬(과부하 방지)
    for ik, cls, sig in ex.map(classify, keys):
        stat[cls] = stat.get(cls, 0) + 1
        if cls == 'NONRES':
            flagged[ik] = sig
        done += 1
        if done % 400 == 0:
            json.dump(flagged, open('_bldg_nonres.json', 'w', encoding='utf-8'), ensure_ascii=False)  # 체크포인트
            print('  %d/%d (%.0f초) NONRES=%d' % (done, len(keys), time.time()-t0, len(flagged)), flush=True)
        time.sleep(0.03)   # 살짝 throttle

json.dump(flagged, open('_bldg_nonres.json', 'w', encoding='utf-8'), ensure_ascii=False)
print('완료 %.0f초 | 분류:%s' % (time.time()-t0, stat), flush=True)
print('★ 건축물대장 전유부=비주거: %d건' % len(flagged), flush=True)
