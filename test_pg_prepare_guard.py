# -*- coding: utf-8 -*-
"""Supabase 트랜잭션 풀러(:6543) 연결은 반드시 prepare_threshold=None — 2026-09-18 실측 사고 고정.
실행: python -m unittest test_pg_prepare_guard -v   (네트워크·DB 불필요, 소스만 검사)

psycopg는 같은 쿼리 5회째부터 서버측 prepared statement('_pg3_N')를 만든다. 트랜잭션 풀러에서는 그 이름이 다른 세션의
다른 쿼리와 겹쳐 **남의 결과를 조용히 돌려준다**(재현: SELECT item_key 60회 중 25회가 SELECT * 결과).
query_pg가 이 설정 없이 연결해 물건 상세 데이터(get_auction)가 빈 껍데기로 캐시되고 지도 59건이 '불가'가 됐다.
이 테스트가 깨지면 prepare_threshold=None 없는 psycopg.connect(...)가 새로 생긴 것이다."""
import ast
import os
import unittest

_ROOT = os.path.dirname(os.path.abspath(__file__))
_ALLOW = {os.path.join("auction_analysis", "encar_match.py")}   # 다른 DB(엔카 Neon, ENCAR_DATABASE_URL)


def _py_files():
    yield os.path.join(_ROOT, "api", "main.py")
    d = os.path.join(_ROOT, "auction_analysis")
    for f in sorted(os.listdir(d)):
        if f.endswith(".py"):
            yield os.path.join(d, f)
    for f in sorted(os.listdir(_ROOT)):
        if f.endswith(".py") and not f.startswith("test_"):
            yield os.path.join(_ROOT, f)


class TestPrepareGuard(unittest.TestCase):
    def test_every_psycopg_connect_disables_prepare(self):
        bad = []
        for p in _py_files():
            rel = os.path.relpath(p, _ROOT)
            if rel in _ALLOW:
                continue
            try:
                tree = ast.parse(open(p, encoding="utf-8").read())
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if not (isinstance(f, ast.Attribute) and f.attr == "connect"
                        and isinstance(f.value, ast.Name) and f.value.id == "psycopg"):
                    continue
                ok = any(k.arg == "prepare_threshold" and isinstance(k.value, ast.Constant) and k.value.value is None
                         for k in node.keywords)
                if not ok:
                    bad.append(f"{rel}:{node.lineno}")
        self.assertEqual(bad, [], "prepare_threshold=None 없는 psycopg.connect: " + ", ".join(bad))


if __name__ == "__main__":
    unittest.main()
