# -*- coding: utf-8 -*-
"""KB 매물 전량 백필 — is_active 기준 미처리 진행 아파트 전량 수집.
진행상황을 kb_backfill_progress.json 에 20초마다 기록(모니터용). background 실행."""
import sys, os, time, json, threading
sys.path.insert(0, r'C:\Users\red85\부동산경매')
os.chdir(r'C:\Users\red85\부동산경매')
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv('.env', override=True)
from kb_crawler import collect_apartments

PROG_FILE = r'C:\Users\red85\부동산경매\kb_backfill_progress.json'
prog = {}
_start = time.time()

def _dump(extra=None):
    d = {k: prog.get(k) for k in ('target', 'processed', 'matched', 'unmatched',
                                   'listings', 'zero_listing', 'errors', 'status')}
    d['elapsed_min'] = round((time.time() - _start) / 60, 1)
    if extra:
        d.update(extra)
    try:
        with open(PROG_FILE, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass

def _dump_loop():
    while True:
        time.sleep(20)
        _dump()

threading.Thread(target=_dump_loop, daemon=True).start()

print('[백필] 시작', flush=True)
stat = collect_apartments(limit=None, resume=True, progress=prog)
dt = time.time() - _start
print('[백필] 완료:', {k: stat.get(k) for k in
      ('target', 'processed', 'matched', 'unmatched', 'listings', 'zero_listing', 'errors')}, flush=True)
print(f'[백필] 소요 {dt/3600:.2f}시간', flush=True)
_dump({'status': 'done', 'elapsed_h': round(dt/3600, 2)})
