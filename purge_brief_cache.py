# -*- coding: utf-8 -*-
"""교정한 물건의 **디스크 brief 캐시**(brief_cache.json)를 비운다 (2026-10-08).

왜 필요한가 — items.build_year 를 고쳐도 화면이 되돌아간다:
  · `_brief_cache` 는 DiskDict(brief_cache.json)라 서버를 재시작해도 파일에서 되살아난다.
  · 20분 스윕 r1 이 `brief:` → `items.build_year` 로 **덮어쓴다**
    (가드는 source='api' 일 때만 막고, 'doc'·'api+doc' 은 무조건 덮는다).
  · 그래서 옛 brief 가 남아 있으면 교정값이 20분 안에 되돌아간다.

🔴메모리 함정(project_auction_build_year): **실행 중인 서버·예열이 옛 메모리로 되쓰기** 한다.
  → 반드시 **서버를 멈춘 뒤** 이 스크립트를 돌리고, 그 다음 서버를 띄운다.

사용: python purge_brief_cache.py --keys-from <파일>   (한 줄에 item_key 하나)
      python purge_brief_cache.py --changed-since 2026-10-08   (그 날짜 이후 build_year 가 바뀐 것)
"""
from __future__ import annotations
import os, sys, json, io, argparse, shutil, datetime

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
_ROOT = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(_ROOT, "brief_cache.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys-from")
    ap.add_argument("--all-missing-in-db", action="store_true",
                    help="Supabase api_cache 에 brief: 가 없는 키를 지운다(교정 배치가 지운 것)")
    a = ap.parse_args()

    if not os.path.exists(P):
        print("brief_cache.json 없음"); return 1
    #  🔴서버가 떠 있으면 되쓰기로 되돌아간다 — 먼저 확인시킨다.
    try:
        import socket
        s = socket.socket(); s.settimeout(1)
        alive = s.connect_ex(("127.0.0.1", 4011)) == 0
        s.close()
    except Exception:
        alive = False
    if alive:
        print("🔴 포트 4011 서버가 떠 있다. 멈춘 뒤 다시 실행하라 "
              "(실행 중 예열이 옛 메모리로 파일을 되쓴다).")
        return 2

    d = json.load(io.open(P, encoding="utf-8"))
    print(f"키 {len(d):,}개")

    keys: set = set()
    if a.keys_from:
        for ln in io.open(a.keys_from, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                keys.add(ln)
    if a.all_missing_in_db:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_ROOT, ".env"))
        import psycopg
        con = psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None,
                              connect_timeout=60, autocommit=True)
        cur = con.cursor()
        cur.execute("set statement_timeout = '110s'")
        ks = list(d.keys())
        have: set = set()
        for i in range(0, len(ks), 2000):
            chunk = ["brief:" + k for k in ks[i:i + 2000]]
            cur.execute("select cache_key from api_cache where cache_key = any(%s)", (chunk,))
            have |= {r[0][6:] for r in cur.fetchall()}
        con.close()
        keys |= (set(ks) - have)
        print(f"  DB 에 brief 가 없는 키 {len(set(ks) - have):,}개")

    if not keys:
        print("지울 키 없음"); return 0
    hit = [k for k in keys if k in d]
    shutil.copy2(P, P + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    for k in hit:
        d.pop(k, None)
    json.dump(d, io.open(P, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"지움 {len(hit):,}개 · 남은 {len(d):,}개 (백업 .bak_* 생성)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
