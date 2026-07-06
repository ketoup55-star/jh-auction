# -*- coding: utf-8 -*-
"""#1 수정 반영: '호' 라벨(전유부 1호) 캐시된 집합건물 brief를 재계산 → 표제부/API 세대수로 교체."""
import os, time, concurrent.futures as cf
for line in open('.env', encoding='utf-8'):
    line=line.strip()
    if '=' in line and not line.startswith('#'):
        k,v=line.split('=',1); os.environ[k.strip()]=v.strip()
from api import main as M
db=M.auction_db
def page(p):
    rows,off=[],0
    while True:
        rr=db._get('items',{**p,'select':'item_key','limit':'1000','offset':str(off)})
        pg=rr.json() if rr.status_code in (200,206) else []
        rows+=[x['item_key'] for x in pg]
        if len(pg)<1000: break
        off+=1000
    return rows
keys=set()
for pat in ('*다세대*','*연립*','*빌라*','*도시형*','*아파트*','*오피스텔*'):
    keys|=set(page({'usage_name':f'like.{pat}'}))
keys=list(keys)
aff=[]
for i in range(0,len(keys),100):
    ch=keys[i:i+100]; rows=db.cache_get_many(['brief:'+k for k in ch])
    for k in ch:
        d=rows.get('brief:'+k)
        if isinstance(d,dict) and d.get('unit_label')=='호' and d.get('households'):
            aff.append(k)
print(f"재계산 대상(호 라벨): {len(aff)}건", flush=True)
t=time.time(); done=fixed=0
def one(k):
    try:
        b=M._compute_brief(k)
        if isinstance(b,dict):
            db.cache_save('brief:'+k, b)
            return b.get('unit_label')!='호'
    except Exception: pass
    return False
with cf.ThreadPoolExecutor(max_workers=6) as ex:
    for ok in ex.map(one, aff):
        done+=1; fixed+=1 if ok else 0
        if done%30==0 or done==len(aff):
            print(f"  {done}/{len(aff)} | '호'제거 {fixed} | {(time.time()-t)/60:.1f}분", flush=True)
print(f"완료: {done}건, '호'제거 {fixed}건, {(time.time()-t)/60:.1f}분", flush=True)
