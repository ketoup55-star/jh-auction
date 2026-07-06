# -*- coding: utf-8 -*-
"""DiskDict JSON 캐시 → SqliteDict(.db) 1회 이관. docs 예열이 끝나(cache_summary.json 최신) 정지된 상태에서 실행.
JSON 원본은 삭제하지 않고 백업으로 남김(.db만 새로 생성). 실행 후 doc_analysis가 .db를 쓰도록 교체 + 서버 재시작."""
import os, sys, time
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'C:\Users\red85\부동산경매')
from auction_analysis.disk_cache import migrate_json_to_sqlite

CDIR = r'C:\Users\red85\부동산경매'
for name in ('cache_analysis', 'cache_appraisal', 'cache_summary', 'cache_vehicle'):
    jp = os.path.join(CDIR, name + '.json')
    db = os.path.join(CDIR, name + '.db')
    if not os.path.exists(jp):
        print(f'{name}: JSON 없음 — 스킵'); continue
    mb = os.path.getsize(jp) / 1024 / 1024
    t0 = time.time()
    n = migrate_json_to_sqlite(jp, db)
    print(f'{name}: {n:,}건 이관 ({mb:.0f}MB JSON → {os.path.getsize(db)/1024/1024:.0f}MB db, {time.time()-t0:.1f}s)')
print('완료 — JSON 원본은 백업으로 보존. 다음: doc_analysis를 SqliteDict(.db)로 교체 + 서버 재시작.')
