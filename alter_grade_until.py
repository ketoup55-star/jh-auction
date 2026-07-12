# -*- coding: utf-8 -*-
"""users.grade_until 컬럼 1회 추가(등급 유지기간 기능). _ensure_column이 no-op(Supabase DDL 회피)라
코드로는 안 생겨 수동 추가. pooler statement timeout은 SET LOCAL=0으로, 락은 서버 중지 후 실행으로 회피."""
import os, sys
_R = os.path.dirname(os.path.abspath(__file__))
for _l in open(os.path.join(_R, ".env"), encoding="utf-8"):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        k, v = _l.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
import psycopg
url = os.environ["SUPABASE_DB_URL"]
c = psycopg.connect(url, autocommit=False, prepare_threshold=None, connect_timeout=25)
try:
    with c.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout=0")
        cur.execute("SET LOCAL lock_timeout='40s'")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS grade_until TEXT")
    c.commit(); print("ALTER_OK")
except Exception as e:
    c.rollback(); print("ALTER_FAIL:", type(e).__name__, str(e)[:150])
finally:
    c.close()
c2 = psycopg.connect(url, autocommit=True, prepare_threshold=None, connect_timeout=25)
col = c2.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='grade_until'").fetchall()
print("COLUMN:", "EXISTS" if col else "NONE")
c2.close()
