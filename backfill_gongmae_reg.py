# -*- coding: utf-8 -*-
"""공매 gongmae_items 에 규제 구분(reg) 컬럼 사전계산 백필.
address ILIKE-OR 는 46k행 스캔→statement timeout이라, reg 를 컬럼에 저장해
목록 필터를 reg.eq.X (인덱스) 로 빠르게 처리한다.

 - ALTER ADD COLUMN IF NOT EXISTS reg + 인덱스(1회, 무해 재실행).
 - reg = _reg_by_addr(address): 서울/경기13구=regulated, 경기·인천=metro, 그 외=none.
 - 기본은 reg IS NULL 만(증분·재실행 안전). --all 로 전량 재계산.
 - VALUES-join 배치 UPDATE(pgbouncer pooler 안전, PK 매칭이라 빠름).

사용:  python backfill_gongmae_reg.py           # 미백필분만
       python backfill_gongmae_reg.py --all     # 전량 재계산
"""
import os
import sys

for _line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

import psycopg

# main.py _reg_by_addr 와 동일 규칙(2026.7.1 규제 기준).
_REG_ADDR_NAMES = ("과천시", "광명시", "의왕시", "하남시", "구리시", "성남시",
                   "수원시 영통구", "수원시 장안구", "수원시 팔달구", "안양시 동안구",
                   "용인시 수지구", "용인시 기흥구", "화성시 동탄구")


def reg_by_addr(a):
    a = a or ""
    if not a:
        return None
    if "서울" in a or any(n in a for n in _REG_ADDR_NAMES):
        return "regulated"
    return "metro" if ("경기" in a or "인천" in a) else "none"


def main():
    all_mode = "--all" in sys.argv
    conn = psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None, autocommit=False)
    cur = conn.cursor()
    # 1) 컬럼 + 인덱스(1회)
    cur.execute("ALTER TABLE gongmae_items ADD COLUMN IF NOT EXISTS reg TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gongmae_reg ON gongmae_items(reg)")
    conn.commit()
    print("컬럼·인덱스 준비 완료")
    # 2) 대상 조회
    where = "" if all_mode else " WHERE reg IS NULL"
    cur.execute(f"SELECT id, address FROM gongmae_items{where}")
    rows = cur.fetchall()
    print(f"대상 {len(rows)}건 (mode={'전량' if all_mode else '미백필만'})")
    upd = [(rid, reg_by_addr(addr)) for rid, addr in rows]
    upd = [(rid, rg) for rid, rg in upd if rg]
    print(f"reg 산출 {len(upd)}건 (주소없음 제외)")
    # 3) 배치 UPDATE (VALUES-join, PK 매칭)
    B, done = 500, 0
    for i in range(0, len(upd), B):
        batch = upd[i:i + B]
        ph = ",".join(["(%s,%s)"] * len(batch))
        params = [x for pair in batch for x in pair]
        cur.execute(
            f"UPDATE gongmae_items AS g SET reg = v.reg "
            f"FROM (VALUES {ph}) AS v(id, reg) WHERE g.id = v.id::text", params)
        conn.commit()
        done += len(batch)
        if done % 5000 < B:
            print(f"  진행 {done}/{len(upd)}")
    print(f"백필 완료 {done}건")
    # 4) 분포 검증
    cur.execute("SELECT reg, count(*) FROM gongmae_items GROUP BY reg ORDER BY count(*) DESC")
    print("분포:", {(r[0] or "NULL"): r[1] for r in cur.fetchall()})
    conn.close()


if __name__ == "__main__":
    main()
