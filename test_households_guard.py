# -*- coding: utf-8 -*-
"""세대수(brief) 재발방지 '못' — 2026-09-17 전수감사에서 실제로 틀렸던 케이스를 그대로 고정한다.
실행: python -m unittest test_households_guard -v   (네트워크·DB 불필요)

이 테스트가 깨지면 아래 중 하나가 되살아난 것이다:
  ① 도로명주소(지번 0000)로 건축물대장 API를 부름 → 0-0 레코드(단독주택 1가구) 오염
  ② 단지명 추출 실패(괄호형 주소) → K-apt 미시도
  ③ K-apt 퍼지 오매칭(대우↔우성·자이↔현대·노르웨이아침↔숲·율정13단지↔7단지)
  ④ 타당성 규칙 무력화(집합건물 1세대·0세대·동번호 아파트 5세대)
  ⑤ K-apt kaptdaCnt 0.0 통과
"""
import ast
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_main_helpers():
    """api.main은 import 시 서버 스레드가 뜨므로, 순수 함수만 소스에서 떼어 실행한다."""
    with open(os.path.join(_ROOT, "api", "main.py"), encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    ns = {"re": re}
    want = {"_has_dong_no", "_danji_hint", "_hh_plausible", "_apt_name_from_addr"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_COLLECTIVE_RE" for t in node.targets):
            exec(ast.get_source_segment(src, node), ns)
        if isinstance(node, ast.FunctionDef) and node.name in want:
            exec(ast.get_source_segment(src, node), ns)
    missing = want - set(ns)
    assert not missing, f"main.py에서 함수를 못 찾음: {missing}"
    return ns


class TestAddressHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = _load_main_helpers()

    def test_name_from_paren_address(self):          # ② 괄호형 주소 — 감사 당시 추출 실패 781건(27%)의 주범
        n = self.ns["_apt_name_from_addr"]
        self.assertEqual(n("경기도 고양시 덕양구 화신로 106, 2405동 15층1502호 (행신동,햇빛마을아파트)"), "햇빛마을아파트")
        self.assertEqual(n("대구광역시 달성군 논공읍 금포로 75-20, 104동 26층2603호 (우신미가뷰아파트)"), "우신미가뷰아파트")
        self.assertEqual(n("경기도 화성시 봉담읍 유리마을길 79, 104동 7층707호 (봉담베스트빌아파트)"), "봉담베스트빌아파트")
        self.assertEqual(n("인천광역시 서구 가정동 루원시티공동2블록 포레나루원시티 207동 15층1503호"), "포레나루원시티")
        self.assertEqual(n("광주광역시 북구 두암동 329 반석힐라 제101동 제1층 제119호"), "반석힐라")
        self.assertEqual(n("인천광역시 연수구 동춘동 925-7 연수2차대우아파트 102동 1층103호"), "연수2차대우아파트")
        self.assertEqual(n("경기도 안산시 단원구 광덕오름길 6, 5층501호 (와동,퀸즈트윈빌)"), "퀸즈트윈빌")

    def test_dong_and_danji(self):
        d, h = self.ns["_danji_hint"], self.ns["_has_dong_no"]
        self.assertEqual(d("경기도 고양시 덕양구 화신로 106, 2405동 15층1502호 (행신동,햇빛마을아파트)"), 24)
        self.assertIsNone(d("인천광역시 연수구 동춘동 925-7 연수2차대우아파트 102동 1층103호"))
        self.assertTrue(h("경상남도 진주시 이현동 111-4 신익무지개아파트 다동 4층408호"))
        self.assertFalse(h("인천광역시 미추홀구 용현동 630-65 지오아파트  2층202호"))

    def test_plausibility(self):                       # ④ 감사에서 화면에 나갔던 값들
        ok = self.ns["_hh_plausible"]
        apt = "경기도 고양시 덕양구 화신로 106, 2405동 15층1502호 (행신동,햇빛마을아파트)"
        self.assertFalse(ok(1, "세대", "아파트", apt))          # 전유부 1세대
        self.assertFalse(ok("0", "세대", "아파트", apt))        # K-apt 0.0
        self.assertFalse(ok(5, "세대", "아파트", apt))          # 동번호 있는 단지 5세대
        self.assertTrue(ok(916, "세대", "아파트", apt))
        self.assertTrue(ok(12, "세대", "아파트", apt))          # 소단지 허용(≥10)
        self.assertTrue(ok(8, "세대", "아파트", "인천광역시 미추홀구 용현동 630-65 지오아파트  2층202호"))   # 동번호 없는 소형
        self.assertFalse(ok(1, "호", "오피스텔", "부산 부전로 108, 6층비-606호 (부전동,대동레미안센트럴시티)"))
        self.assertTrue(ok(144, "호", "오피스텔", "부산 부전로 108, 6층비-606호 (부전동,대동레미안센트럴시티)"))
        self.assertTrue(ok("단독", "", "주택", "x"))
        self.assertFalse(ok("단독", "", "아파트", "x"))         # 아파트에 '단독' 금지


class TestKaptMatching(unittest.TestCase):           # ③ 실제 오매칭 사례 고정(네트워크 불필요·순수 함수)
    def test_core_compatible(self):
        from auction_analysis.kapt_source import _core, _core_compatible
        bad = [("연수2차대우아파트", "연수2차우성아파트"), ("양주자이", "양주현대"), ("양산유림노르웨이아침", "양산유림노르웨이숲아파트"),
               ("현우파크맨션", "현대파크맨션"), ("대원아파트", "성원아파트"), ("안성부영", "안성동신")]
        good = [("연수2차대우아파트", "연수대우삼환아파트"), ("탑실마을대주피오레2단지", "공세대주피오레2단지"), ("라인동산", "화정라인동산"),
                ("햇빛마을아파트", "행신햇빛마을24단지"), ("이편한세상부평그랑힐스", "e편한세상부평그랑힐스"), ("영남탑스빌", "마전영남탑스빌"),
                ("예술인아파트", "성포예술인"), ("연수대우.삼환", "연수대우삼환아파트")]
        for a, b in bad:
            self.assertFalse(_core_compatible(_core(a), _core(b)), f"오매칭 허용됨: {a} ~ {b}")
        for a, b in good:
            self.assertTrue(_core_compatible(_core(a), _core(b)), f"정답 거부됨: {a} ~ {b}")

    def test_danji_and_ident(self):
        from auction_analysis.kapt_source import _danji_set, _ident_set, _cha_no
        self.assertEqual(_danji_set("햇빛마을24단지"), {"24"})
        self.assertEqual(_danji_set("분평계룡리슈빌1,2단지"), {"1", "2"})
        self.assertTrue(_danji_set("율정마을13단지").isdisjoint(_danji_set("율정마을7단지")))
        self.assertEqual(_ident_set("현대2"), {"2"})
        self.assertEqual(_ident_set("래미안3차"), {"3"})
        self.assertEqual(_cha_no("동화6차"), "6")
        self.assertNotEqual(_cha_no("동화6차"), _cha_no("동화3차"))

    def test_num_zero(self):                           # ⑤ V5 float 0.0
        from auction_analysis.kapt_source import _num
        self.assertEqual(_num("0.0"), "0")
        self.assertEqual(_num("916.0"), "916")


class TestBuildingApiGuard(unittest.TestCase):       # ① 도로명(지번 0000)이면 API를 부르지 않는다(네트워크 없이 None)
    def test_road_address_returns_none_without_http(self):
        from auction_analysis.bjd_codes import resolve_bjd
        from auction_analysis.building_source import BuildingSource
        addr = "경기도 고양시 덕양구 화신로 106, 2405동 15층1502호 (행신동,햇빛마을아파트)"
        r = resolve_bjd(addr)
        self.assertIsNotNone(r)
        self.assertEqual((r[2], r[3]), ("0000", "0000"))          # 도로명 → 지번 미상 계약
        b = BuildingSource(key="dummy-key")
        b._quota_block_until = 0.0
        self.assertIsNone(b.info(addr, collective=True))            # HTTP 전에 차단(키가 가짜라도 예외 없이 None)

    def test_sql_guard_covers_zero_one_and_quota(self):
        with open(os.path.join(_ROOT, "api", "main.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("_HH_BAD_SQL", src)
        self.assertIn("(c.data->>'households')::int <= 1", src)
        self.assertIn("(c.data->>'quota')='true'", src)
        self.assertIn("_brief_sweep()", src)                        # 20분 루프에 스윕이 연결돼 있는지


if __name__ == "__main__":
    unittest.main(verbosity=2)
