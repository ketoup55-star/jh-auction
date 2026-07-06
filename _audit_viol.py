# -*- coding: utf-8 -*-
"""위반건축물 누락 감사: 건축물대장 문서엔 '위반건축물'인데 앱 목록(tags)엔 위반 없는 건 집계.
선례 _parse_bldg_usage.py 본뜸(media 일괄 → R2 PDF 병렬 스캔). 표본/전수 겸용(SAMPLE 환경변수)."""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, time, random, json
import concurrent.futures as cf
for ln in open('.env', encoding='utf-8'):
    if '=' in ln and not ln.strip().startswith('#'):
        k, v = ln.strip().split('=', 1); os.environ.setdefault(k, v)
import api.main as M
db = M.auction_db

SAMPLE = int(os.environ.get('AUDIT_SAMPLE', '500'))   # 0=전수

# 대상: 주거용 비아파트(위반 흔한 유형)
keys = []; off = 0
while True:
    r = db._get('items', {'select': 'item_key,usage_name,tags', 'search_group': 'eq.주거용',
        'or': '(usage_name.like.*다가구*,usage_name.like.*근린주택*,usage_name.like.*단독*,usage_name.like.*다세대*,usage_name.like.*연립*,usage_name.like.*도시형*)',
        'limit': '1000', 'offset': str(off)})
    rows = r.json() if r.status_code in (200, 206) else []
    keys += rows
    if len(rows) < 1000:
        break
    off += 1000
print('대상(주거 비아파트) %d건' % len(keys), flush=True)
tag = {x['item_key']: (x.get('tags') or '') for x in keys}
iks = [x['item_key'] for x in keys]

# media(건축물대장 r2_key) 일괄
r2 = db.r2; urlmap = {}
for i in range(0, len(iks), 150):
    ch = iks[i:i + 150]; inlist = ','.join('"' + k + '"' for k in ch)
    try:
        rows = db._get('media', {'select': 'item_key,r2_key', 'kind': 'eq.건축물대장',
                                 'item_key': 'in.(' + inlist + ')', 'limit': '300'}).json()
    except Exception:
        rows = []
    for row in rows:
        if row.get('r2_key') and row['item_key'] not in urlmap:
            urlmap[row['item_key']] = r2 + '/' + row['r2_key']
    time.sleep(0.12)
havedoc = [k for k in iks if k in urlmap]
print('건축물대장 문서 보유 %d건 (대상의 %.0f%%)' % (len(havedoc), 100 * len(havedoc) / max(1, len(iks))), flush=True)

random.seed(42)
target = havedoc if (SAMPLE == 0 or len(havedoc) <= SAMPLE) else random.sample(havedoc, SAMPLE)
print('스캔 대상 %d건 (%s) 시작 %s' % (len(target), '전수' if SAMPLE == 0 else '표본', time.strftime('%H:%M:%S')), flush=True)


def scan(ik):
    try:
        txt = M._pdf_text_pages(urlmap[ik], 2)
    except Exception:
        return ik, 'err'
    if not txt or len(txt) < 50:
        return ik, 'notext'
    return ik, ('VIOL' if '위반건축물' in txt.replace(' ', '') else 'ok')


viol = []; err = 0; done = 0; t0 = time.time()
_W = int(os.environ.get('AUDIT_WORKERS', '5'))
with cf.ThreadPoolExecutor(max_workers=_W) as ex:
    for ik, res in ex.map(scan, target):
        done += 1
        if res == 'VIOL':
            viol.append(ik)
        elif res in ('err', 'notext'):
            err += 1
        if done % 200 == 0:
            print('  %d/%d (%.0f초) 위반=%d' % (done, len(target), time.time() - t0, len(viol)), flush=True)
n = len(target)
miss = [k for k in viol if '위반건축물' not in tag.get(k, '').replace(' ', '')]
json.dump({'viol': viol, 'miss': miss, 'havedoc': len(havedoc), 'scanned': n},
          open('_audit_viol_result.json', 'w', encoding='utf-8'), ensure_ascii=False)
print('── 결과 ──', flush=True)
print('스캔 %d건 %.0f초 | err/notext %d' % (n, time.time() - t0, err))
print('위반건축물(문서) %d건 = 스캔의 %.1f%%' % (len(viol), 100 * len(viol) / max(1, n)))
print('그중 앱 목록 미표시(tags에 위반없음) %d건 = 위반의 %.0f%%' % (len(miss), 100 * len(miss) / max(1, len(viol) or 1)))
if SAMPLE and n:
    print('전체(문서보유 %d건) 추정: 위반 ~%.0f건, 미표시 ~%.0f건'
          % (len(havedoc), len(viol) / n * len(havedoc), len(miss) / n * len(havedoc)))
print('미표시 예시:', miss[:8])
