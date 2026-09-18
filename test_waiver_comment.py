# -*- coding: utf-8 -*-
"""확약서(말소동의·대항력 포기)로 인수 면제된 임차인의 판정 문장 — 2026-09-18 실제 카드 문장 고정.
실행: python -m unittest test_waiver_comment -v   (네트워크·DB 불필요)
깨지면: 같은 임차인 카드에 '✓ 확약서 제출 → 낙찰자 미인수'와 '→ 보증금 전액 매수인 인수'가 함께 뜨는 모순이 되살아난 것."""
import unittest

from auction_analysis.crawler_analysis import waiver_comment

REAL = ("김윤경: 전입 2019-02-25 · 최선순위 2025-03-10(압류) · 확정일자 2019-01-29 · 배당요구 없음 → 대항력 있음 · "
        "배당요구 없어 배당을 받지 못함 → 보증금 전액 매수인 인수 | 비고: 김윤경:주택임차권등기권자로서 주택임차권등기일은 "
        "2023. 4. 3. 이고 임차보증금 반환채권을 주택도시보증공사에게 양도함. [명세서]")


class TestWaiverComment(unittest.TestCase):
    def test_real_card(self):
        out = waiver_comment(REAL)
        self.assertIn("보증금 전액 매수인 인수 대상이나, 말소동의·대항력 포기 확약서 제출로 낙찰자 미인수", out)
        self.assertTrue(out.endswith("[명세서]"))                 # 화면이 떼는 꼬리표 위치 유지
        self.assertEqual(out.count("확약서"), 1)

    def test_idempotent_and_empty(self):
        once = waiver_comment(REAL)
        self.assertEqual(waiver_comment(once), once)               # 두 번 적용해도 그대로
        self.assertEqual(waiver_comment(""), "")
        self.assertIsNone(waiver_comment(None))
        self.assertEqual(waiver_comment("배당요구 → 전액 배당 소멸"), "배당요구 → 전액 배당 소멸")


if __name__ == "__main__":
    unittest.main()
