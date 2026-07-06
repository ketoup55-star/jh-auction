# -*- coding: utf-8 -*-
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, re, time
for ln in open('.env', encoding='utf-8'):
    if '=' in ln and not ln.strip().startswith('#'):
        k, v = ln.strip().split('=', 1); os.environ.setdefault(k, v)
import api.main as M
db = M.auction_db

# 비주거 공부상 용도 키워드
NONRES = re.compile(r'근린생활시설|사무소|사무실|업무시설|판매시설|제1종근린|제2종근린')
# "공부상/등재/건축물대장상/공부상의 용도" + 비주거 (현황 주거 무관하게 공부상이 비주거면 플래그)
#  검출 신호 패턴들
PATS = [
    re.compile(r'공부상[^\n]{0,40}?(근린생활시설|사무소|사무실|업무시설|판매시설)'),
    re.compile(r'(근린생활시설|사무소|사무실|업무시설|판매시설)[^\n]{0,15}?(?:으로|로)?\s*(?:등재|기재|등록)'),
    re.compile(r'(?:공부상의|건축물대장상|대장상)[^\n]{0,40}?(근린생활시설|사무소|사무실|업무시설)'),
    re.compile(r'용도[는은]?[^\n]{0,20}?(제[12]종\s*근린생활시설|근린생활시설|사무소|업무시설)'),
]

rows = []; off = 0
t0 = time.time()
while True:
    r = db._get('items', {'select': 'item_key,address,usage_name,detail_text',
        'search_group': 'eq.주거용',
        'or': '(usage_name.like.*다세대*,usage_name.like.*도시형*)',
        'limit': '500', 'offset': str(off)})
    page = r.json() if r.status_code in (200, 206) else []
    rows += page
    if len(page) < 500: break
    off += 500

flagged = {}
for x in rows:
    dt = x.get('detail_text') or ''
    if not dt or not NONRES.search(dt):
        continue
    # 패턴 중 하나라도 매칭 + 주변환경 묘사가 아닌 '본건 용도'인지(현황/공부상 맥락)
    hit = None
    for p in PATS:
        m = p.search(dt)
        if m:
            hit = m.group(0)[:40]; break
    if hit:
        flagged[x['item_key']] = (x['address'][:36], x.get('usage_name'), hit)

print('다세대/도시형 %d건 스캔(%.0f초)' % (len(rows), time.time()-t0))
print('★ 완전검출(넓힌 패턴): 공부상 비주거 다세대/도생 = %d건' % len(flagged))
print('--- 예시 12건 ---')
for ik, (a, u, h) in list(flagged.items())[:12]:
    print('  [%s] %s | 신호: %s' % (u, a, h))
# 저장(다음 단계용)
import json
with open('_nonres_keys.json', 'w', encoding='utf-8') as f:
    json.dump(list(flagged.keys()), f)
print('검출 키 %d개 → _nonres_keys.json 저장' % len(flagged))
