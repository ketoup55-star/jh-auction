# -*- coding: utf-8 -*-
"""gongmae_items에 매수판정 사전계산 컬럼 추가(경매 case_sort 인덱스 추가와 동일 방식).

경매 목록이 buy_grade 컬럼 사전계산으로 즉답하듯, 공매도 목록 행별 라이브
계산(/gongmae/buy_grade·/gongmae/villa_est)을 없애기 위해 결과를 컬럼에 저장한다.

추가 컬럼:
  buy_grade   TEXT    매수양호/매수검토/매수금지 (판정 대상 아니면 NULL)
  sise        BIGINT  추정시세(원)
  profit      BIGINT  차익(시세-마지막회차최저가, 원)
  grade_reason TEXT   판정 근거 문구
  nb_count    INTEGER 유사(주변) 실거래 건수(빌라=nearby_trades, 아파트=같은평형 매칭수)

psycopg(SUPABASE_DB_URL·pooler:6543·prepare_threshold=None) + SET LOCAL 타임아웃.
ADD COLUMN IF NOT EXISTS라 반복 실행 무해.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_ROOT, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

import psycopg  # noqa: E402

DBURL = os.environ["SUPABASE_DB_URL"]

_DDL = [
    "ALTER TABLE gongmae_items ADD COLUMN IF NOT EXISTS buy_grade TEXT",
    "ALTER TABLE gongmae_items ADD COLUMN IF NOT EXISTS sise BIGINT",
    "ALTER TABLE gongmae_items ADD COLUMN IF NOT EXISTS profit BIGINT",
    "ALTER TABLE gongmae_items ADD COLUMN IF NOT EXISTS grade_reason TEXT",
    "ALTER TABLE gongmae_items ADD COLUMN IF NOT EXISTS nb_count INTEGER",
]


def main():
    conn = psycopg.connect(DBURL, prepare_threshold=None, connect_timeout=20, autocommit=False)
    with conn.cursor() as cur:
        # pooler에서 DDL이 오래 걸리면 즉시 실패하도록(다른 세션 블로킹 방지)
        cur.execute("SET LOCAL lock_timeout = '30s'")   # 워머 UPDATE와 경합 시 대기(ADD COLUMN은 즉시·rewrite 없음)
        cur.execute("SET LOCAL statement_timeout = '60s'")
        for ddl in _DDL:
            cur.execute(ddl)
            print("OK:", ddl, flush=True)
    conn.commit()
    # 확인
    cur = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='gongmae_items' AND column_name IN "
        "('buy_grade','sise','profit','grade_reason','nb_count') ORDER BY column_name")
    print("--- 추가된 컬럼 확인 ---", flush=True)
    for r in cur.fetchall():
        print(f"  {r[0]} : {r[1]}", flush=True)
    conn.close()
    print("완료.", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
