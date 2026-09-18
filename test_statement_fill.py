# -*- coding: utf-8 -*-
"""매각물건명세서 → 임차인 채우기 규칙 고정 검사(오프라인, PDF 없이 파싱 결과 dict로).
주인님 지시(2026-09-18): 대항력 있는 임차인이 배당요구를 안 하면 배당을 못 받고 보증금 전액 인수(임차권등기 '소멸' 아니라 '인수'),
사실만 표시(신청채권자 승계 등 비고 문장 그대로), '경매신청채권자는 배당요구 없이 배당' 규칙은 넣지 않는다."""
import unittest
from datetime import date

from auction_analysis import statement_fill as SF
from auction_analysis.models import Tenant


def _parsed(tenants, rows, senior, deadline, caution="", surviving="", no_tenant=False):
    return {"available": True, "senior_setup": senior, "dividend_deadline": deadline, "tenants": tenants, "rows": rows,
            "caution": caution, "surviving_rights": surviving, "ground_rights": "", "no_tenant": no_tenant}


class SeniorInfo(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(SF.senior_info("2022.11.25.근저당권"), (date(2022, 11, 25), "근저당권"))
        self.assertEqual(SF.senior_info("2025. 8. 12. 경매개시결정"), (date(2025, 8, 12), "경매개시결정"))
        self.assertEqual(SF.senior_info("2025.8.13.경매개시결정")[0], date(2025, 8, 13))
        self.assertEqual(SF.senior_info("")[0], None)


class RightLabel(unittest.TestCase):
    def test_norm_right(self):
        self.assertEqual(SF.norm_right("주거\n주택임\n차권자"), "주택임차권자")
        self.assertEqual(SF.norm_right("주거 전세권자"), "주거전세권자")
        self.assertEqual(SF.norm_right("주거 임차인"), "주거임차인")
        self.assertEqual(SF.norm_right("점포 임차인"), "상가임차인")


class Sentences(unittest.TestCase):
    CAUTION = ("윤아름: 주택임차권등기권자(대항력 있는 임차인)로서, 주택임차권등기일은 2024. 8. 29.임. "
               "신청채권자 주택도시보증공사가 승계함. 김철수: 점유 관계 미상.")

    def test_paragraph_until_other_name(self):
        s = SF.sentences_about(self.CAUTION, "윤아름", ["윤아름", "김철수"])
        self.assertIn("주택임차권등기일은 2024. 8. 29.임", s)
        self.assertIn("주택도시보증공사가 승계함", s)       # 이름 없는 다음 문장도 같은 사람 단락
        self.assertNotIn("김철수", s)
        self.assertEqual(SF.sentences_about(self.CAUTION, "김철수", ["윤아름", "김철수"]), "김철수: 점유 관계 미상.")

    def test_reg_date(self):
        self.assertEqual(SF.reg_date_from_note("박소연 : 전세권자로서 전세권설정등기일은 2015.12.29.임"), date(2015, 12, 29))
        self.assertEqual(SF.reg_date_from_note("주택임차권등기일은 2024. 8. 29.임"), date(2024, 8, 29))
        self.assertIsNone(SF.reg_date_from_note("점유 관계 미상"))


class BuildRows(unittest.TestCase):
    def test_opposing_no_demand_is_assumed(self):
        """대항력 O + 배당요구 X → 인수(보증금 전액), 임차권등기 소멸→인수, 비고(신청채권자 승계) 그대로 표시."""
        p = _parsed([Tenant(name="윤아름", move_in_date=date(2020, 8, 31), fixed_date=date(2020, 8, 5), deposit=125000000)],
                    [{"name": "윤아름", "part": "2층 201호 전부", "source": "등기사항 전부증명서", "right": "주거 주택임 차권자",
                      "period": "2020.08.28.", "deposit": "금 125,000,000원", "rent": "", "movein": "2020.08.31.",
                      "fixed": "2020.08.05", "demand": ""}],
                    "2025. 8. 12. 경매개시결정", "2025-11-03",
                    caution="윤아름: 주택임차권등기권자(대항력 있는 임차인)로서, 주택임차권등기일은 2024. 8. 29.임. 신청채권자 주택도시보증공사가 승계함.",
                    surviving="을구 순위 1번 주택임차권등기(2024.08.29. 접수 제28539호): 매수인에게 대항할 수 있는 임차인이 있음")
        b = SF.build_rows(p)
        self.assertEqual(b["senior_date"], "2025-08-12")
        self.assertEqual(b["senior_kind"], "경매개시결정")
        t = b["tenants"][0]
        self.assertTrue(t["has_opposing_power"])
        self.assertEqual(t["label"], "인수")
        self.assertEqual(t["assume_amount"], 125000000)
        self.assertEqual(t["tenant_right"], "주택임차권자")
        self.assertEqual(t["occupancy"], "2층 201호 전부")
        self.assertTrue(t["status"].startswith("인수 윤아름: "))
        self.assertIn("배당요구 없음", t["status"])
        self.assertIn("주택도시보증공사가 승계함", t["status"])
        self.assertTrue(t["status"].endswith(SF.SRC_TAG))
        self.assertNotIn("배당요구 없이", t["status"])       # 경매신청채권자 배당 규칙은 넣지 않는다
        upd = SF.rights_to_assume([{"id": 1, "right_type": "임차권설정", "holder": "윤아름", "status": "소멸"},
                                   {"id": 2, "right_type": "근저당권", "holder": "은행", "status": "소멸"}],
                                  b["surviving"], b["tenants"])
        self.assertEqual([r["id"] for r in upd], [1])

    def test_opposing_with_demand(self):
        p = _parsed([Tenant(name="전주윤", move_in_date=date(2021, 12, 28), fixed_date=date(2021, 12, 28), deposit=140000000,
                            demanded_distribution=True, demand_date=date(2025, 8, 21))],
                    [{"name": "전주윤", "part": "1406호 전부", "source": "권리신고", "right": "주거 임차인", "period": "", "deposit": "140,000,000",
                      "rent": "", "movein": "2021.12.28.", "fixed": "2021.12.28.", "demand": "2025.8.21."}],
                    "2025.8.13.경매개시결정", "2025-11-03")
        t = SF.build_rows(p)["tenants"][0]
        self.assertTrue(t["has_opposing_power"])
        self.assertEqual(t["label"], "미배당금 인수예상")
        self.assertIsNone(t["assume_amount"])
        self.assertEqual(t["dividend_date"], "2025-08-21")

    def test_late_demand_is_invalid(self):
        p = _parsed([Tenant(name="홍길동", move_in_date=date(2020, 1, 3), fixed_date=date(2021, 5, 27), deposit=100000000,
                            demanded_distribution=False, demand_date=date(2026, 1, 10))],
                    [], "2025. 8. 12. 경매개시결정", "2025-11-03")
        t = SF.build_rows(p)["tenants"][0]
        self.assertEqual(t["label"], "인수")
        self.assertIn("종기 후·무효", t["status"])

    def test_no_opposing(self):
        p = _parsed([Tenant(name="이몽룡", move_in_date=date(2023, 5, 1), deposit=50000000, demanded_distribution=True,
                            demand_date=date(2025, 9, 1))], [], "2022.11.25.근저당권", "2025-10-01")
        t = SF.build_rows(p)["tenants"][0]
        self.assertFalse(t["has_opposing_power"])
        self.assertEqual(t["label"], "소멸")
        self.assertIsNone(t["assume_amount"])

    def test_same_day_not_opposing(self):
        p = _parsed([Tenant(name="성춘향", move_in_date=date(2022, 11, 25), deposit=50000000)], [], "2022.11.25.근저당권", "2025-10-01")
        self.assertEqual(SF.build_rows(p)["tenants"][0]["label"], "소멸")

    def test_jeonse_by_registration_date(self):
        p = _parsed([Tenant(name="박소연", deposit=250000000)],
                    [{"name": "박소연", "part": "전부", "source": "등기사항 전부증명서", "right": "주거 전세권자", "period": "", "deposit": "250,000,000",
                      "rent": "", "movein": "", "fixed": "", "demand": ""}],
                    "2022.11.25.근저당권", "2025-07-07", caution="박소연 : 전세권자로서 전세권설정등기일은 2015.12.29.임",
                    surviving="을구 순위 2번 전세권설정등기(2015.12.29.제311738호)는 말소되지 않고 매수인에게 인수됨")
        b = SF.build_rows(p)
        t = b["tenants"][0]
        self.assertTrue(t["has_opposing_power"])
        self.assertEqual(t["label"], "인수")
        self.assertIn("등기 2015-12-29", t["status"])
        upd = SF.rights_to_assume([{"id": 7, "right_type": "전세권", "holder": "박소연", "status": "소멸"}], b["surviving"], b["tenants"])
        self.assertEqual([r["id"] for r in upd], [7])

    def test_unknown_move_in(self):
        p = _parsed([Tenant(name="미상인", deposit=30000000)], [], "2022.11.25.근저당권", "2025-07-07")
        b = SF.build_rows(p)
        t = b["tenants"][0]
        self.assertFalse(t["has_opposing_power"])
        self.assertEqual(t["label"], "대항력 미상")
        self.assertEqual(b["unknown_power"], 1)

    def test_no_tenant(self):
        b = SF.build_rows(_parsed([], [], "2020. 3. 20. 근저당권", "2026-02-19", no_tenant=True))
        self.assertTrue(b["no_tenant"])
        self.assertEqual(b["tenants"], [])
        self.assertEqual(b["senior_date"], "2020-03-20")

    def test_surviving_keywords_assume_rights(self):
        upd = SF.rights_to_assume([{"id": 1, "right_type": "가처분", "holder": "갑", "status": "소멸"},
                                   {"id": 2, "right_type": "가등기", "holder": "을", "status": "인수"},
                                   {"id": 3, "right_type": "지상권", "holder": "병", "status": "소멸"}],
                                  "갑구 순위 5번 가처분등기는 말소되지 않음", [])
        self.assertEqual([r["id"] for r in upd], [1])


class WonAmounts(unittest.TestCase):
    """명세서 금액 칸의 한글 단위 — 2026-09-18 실측: '8,000만원'·'3억1천5백만원'이 통째로 버려져 보증금 미상으로 들어갔다
    (G01|2025|51805|1 김윤경, J01|2025|742|1 장도석). 숫자만 있던 칸의 기존 결과는 그대로여야 한다."""

    def test_korean_units(self):
        from auction_analysis.sale_statement_parser import won_amounts as W
        self.assertEqual(W("8,000만원"), [80_000_000])
        self.assertEqual(W("3억1천5백만원"), [315_000_000])
        self.assertEqual(W("1억 5,000만원"), [150_000_000])
        self.assertEqual(W("2억 3천만원"), [230_000_000])
        self.assertEqual(W("1.5억"), [150_000_000])
        self.assertEqual(W("일금 삼천만원"), [30_000_000])
        self.assertEqual(W("월 50만원"), [500_000])

    def test_digits_unchanged(self):
        from auction_analysis.sale_statement_parser import won_amounts as W
        self.assertEqual(W("금 125,000,000원"), [125_000_000])
        self.assertEqual(W("155,000,000 157,000,000"), [155_000_000, 157_000_000])
        self.assertEqual(W("미상"), [])
        self.assertEqual(W(""), [])

    def test_amount_boundaries(self):
        from auction_analysis.sale_statement_parser import won_amounts as W
        self.assertEqual(W("3,500만원(월세 30만원)"), [35_000_000, 300_000])
        self.assertEqual(max(x for x in W("8,000만원 (2023.4.3. 임차권등기)") if x >= 100_000), 80_000_000)   # 날짜가 붙지 않게
        self.assertEqual(max(W("1억원(2억원으로 증액)")), 200_000_000)


if __name__ == "__main__":
    unittest.main()
