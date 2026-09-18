# -*- coding: utf-8 -*-
"""기일현황 정규화 규칙 고정 검사(오프라인). 주인님 확정 규칙(2026-09-18):
  회차는 최저매각금액이 떨어질 때만 증가 / 시간순 한 표 / 절차기일은 회차 없음·기일종류 표시 / 결과 없는 매각결정기일 제외."""
import unittest
from datetime import date

from auction_analysis import schedule_norm as N

TODAY = date(2026, 9, 18)


def _ex(round_, sd, mp, res, **kw):
    r = {"id": kw.pop("id", 0), "round": round_, "sell_date": sd, "min_price": mp, "result": res, "sale_price": None,
         "sale_rate": None, "bid_count": None, "sale_2nd_price": None, "winner_name": None}
    r.update(kw)
    return r


class ResultLabel(unittest.TestCase):
    def test_court_labels(self):
        self.assertEqual(N.norm_result("최고가매각허가결정"), "허가")
        self.assertEqual(N.norm_result("매각불허가"), "불허가")
        self.assertEqual(N.norm_result("최고가매각허가취소결정"), "허가취소")
        self.assertEqual(N.norm_result("허가취소"), "허가취소")
        self.assertEqual(N.norm_result("대금납부"), "납부")
        self.assertEqual(N.norm_result("배당종결"), "완료")
        self.assertEqual(N.norm_result("기한변경"), "기한변경")

    def test_speed_labels_kept(self):
        self.assertEqual(N.norm_result("재매각 2회"), "재매각 2회")   # '매각'으로 오인 금지
        self.assertEqual(N.norm_result("재진행 4회"), "재진행 4회")   # 띄어쓰기 보존
        self.assertEqual(N.norm_result("진행"), "진행")
        self.assertEqual(N.norm_result("예정"), "예정")
        self.assertEqual(N.norm_result("추후지정"), "추후지정")


class KindFromRow(unittest.TestCase):
    def test_kind_text_first(self):
        self.assertEqual(N._kind_from_row(_ex("", "2023-11-21", "대금지급기한 납부 (2023.11.21)", "")), "대금지급기한")
        self.assertEqual(N._kind_from_row(_ex("", "2023-11-21", "대금지급및 배당기일", "")), "대금지급및배당기일")
        self.assertEqual(N._kind_from_row(_ex("", "2023-11-21", "일부배당", "")), "일부배당")

    def test_price_rows(self):
        self.assertEqual(N._kind_from_row(_ex("2차", "2026-08-31", "135,100,000원", "유찰")), N.SALE_KIND)
        self.assertEqual(N._kind_from_row(_ex("4차", "2026-10-26", "94570000", None)), N.SALE_KIND)

    def test_result_only_rows(self):
        self.assertEqual(N._kind_from_row(_ex("", "2026-07-17", "", "기한변경")), "대금지급기한")
        self.assertEqual(N._kind_from_row(_ex("", "2026-07-20", "", "미납")), "대금지급기한")
        self.assertEqual(N._kind_from_row(_ex("", "2026-06-08", "", "허가")), "매각결정기일")
        self.assertEqual(N._kind_from_row(_ex("", "2026-06-08", "", "")), "기타")
        self.assertEqual(N._kind_from_row(_ex("3차", "2026-06-08", "", "")), N.SALE_KIND)
        self.assertEqual(N._kind_from_row(_ex("", "2026-06-08", "", "진행")), N.SALE_KIND)


class Canonical(unittest.TestCase):
    """주인님 확인 표(L02|2025|51731|1): 신건 04-20 유찰 / 2차 06-01 매각 / ·06-08 매각결정기일 허가 /
    ·07-17 대금지급기한 기한변경 / ·07-20 미납 / 2차 08-31 유찰(같은 금액) / 3차 10-26 94,570,000원."""
    DOC = [
        {"date": "2026-04-20", "time": "10:00", "kind": "매각기일", "place": "", "min_price": 193000000, "result": "유찰"},
        {"date": "2026-06-01", "time": "10:00", "kind": "매각기일", "place": "", "min_price": 135100000, "result": "매각"},
        {"date": "2026-06-08", "time": "14:00", "kind": "매각결정기일", "place": "", "min_price": None, "result": "최고가매각허가결정"},
        {"date": "2026-07-17", "time": "", "kind": "대금지급기한", "place": "", "min_price": None, "result": "기한변경"},
        {"date": "2026-07-20", "time": "", "kind": "대금지급기한", "place": "", "min_price": None, "result": "미납"},
        {"date": "2026-08-31", "time": "10:00", "kind": "매각기일", "place": "", "min_price": 135100000, "result": "유찰"},
        {"date": "2026-10-26", "time": "10:00", "kind": "매각기일", "place": "", "min_price": 94570000, "result": ""},
        {"date": "2026-11-02", "time": "14:00", "kind": "매각결정기일", "place": "", "min_price": None, "result": ""},
    ]
    EXISTING = [
        _ex("신건", "2026-04-20", "193,000,000원", "유찰", id=1),
        _ex("2차", "2026-06-01", "135,100,000원", "매각", id=2, sale_price=151060000, sale_rate="78%", bid_count=1, winner_name="김진O"),
        _ex("", "2026-07-17", "", "기한변경", id=3),
        _ex("", "2026-07-20", "", "미납", id=4),
        _ex("3차", "2026-08-31", "135,100,000원", "유찰", id=5),
        _ex("4차", "2026-10-26", "94570000", None, id=6),
    ]

    def test_with_doc(self):
        rows = N.canonical_rows(self.DOC, self.EXISTING, TODAY)
        got = [(r["round"], r["sell_date"], r["min_price"], r["result"]) for r in rows]
        self.assertEqual(got, [
            ("신건", "2026-04-20", "193,000,000원", "유찰"),
            ("2차", "2026-06-01", "135,100,000원", "매각"),
            ("", "2026-06-08", "매각결정기일", "허가"),
            ("", "2026-07-17", "대금지급기한", "기한변경"),
            ("", "2026-07-20", "대금지급기한", "미납"),
            ("2차", "2026-08-31", "135,100,000원", "유찰"),
            ("3차", "2026-10-26", "94,570,000원", ""),
        ])
        self.assertEqual(rows[1]["sale_price"], 151060000)     # 낙찰 상세 유지
        self.assertEqual(rows[1]["winner_name"], "김진O")

    def test_without_doc(self):
        rows = N.canonical_rows(None, self.EXISTING, TODAY)
        got = [(r["round"], r["sell_date"], r["min_price"], r["result"]) for r in rows]
        self.assertEqual(got, [
            ("신건", "2026-04-20", "193,000,000원", "유찰"),
            ("2차", "2026-06-01", "135,100,000원", "매각"),
            ("", "2026-07-17", "대금지급기한", "기한변경"),
            ("", "2026-07-20", "대금지급기한", "미납"),
            ("2차", "2026-08-31", "135,100,000원", "유찰"),
            ("3차", "2026-10-26", "94,570,000원", ""),
        ])

    def test_no_dedupe_of_distinct_rows_without_doc(self):
        ex = [_ex("", "2026-07-17", "대금지급기한 납부 (2026.07.17)", "납부", id=1),
              _ex("", "2026-07-17", "배당기일", "완료", id=2)]
        rows = N.canonical_rows(None, ex, TODAY)
        self.assertEqual(len(rows), 1)                          # 같은 날짜·회차칸 → 유니크 제약 때문에 한 행으로 병합
        self.assertIn("대금지급기한 납부 (2026.07.17)", rows[0]["min_price"])
        self.assertIn("배당기일", rows[0]["min_price"])
        self.assertEqual(rows[0]["result"], "납부 / 완료")

    def test_round_only_on_price_drop_and_raise(self):
        ex = [_ex("신건", "2026-01-05", "100,000,000원", "유찰", id=1),
              _ex("2차", "2026-02-05", "100,000,000원", "변경", id=2),     # 같은 금액 → 같은 회차
              _ex("3차", "2026-03-05", "80,000,000원", "유찰", id=3),
              _ex("4차", "2026-04-05", "80,000,000원", "유찰", id=4),
              _ex("5차", "2026-05-05", "64,000,000원", "", id=5)]
        rows = N.canonical_rows(None, ex, TODAY)
        self.assertEqual([r["round"] for r in rows], ["신건", "신건", "2차", "2차", "3차"])

    def test_restart_at_seen_price_keeps_that_round(self):
        """변경 뒤 전에 나온 금액으로 재진행 → 그 금액의 회차 — 실측 A01|2025|102122|1(목록 기준 3차)."""
        ex = [_ex("", "2025-08-20", "239,000,000원", "유찰", id=1), _ex("", "2025-09-24", "191,200,000원", "유찰", id=2),
              _ex("", "2025-11-05", "152,960,000원", "유찰", id=3), _ex("", "2026-07-22", "32,078,000원", "변경", id=4),
              _ex("", "2026-08-26", "191,200,000원", "유찰", id=5), _ex("", "2026-09-30", "152,960,000원", "진행", id=6)]
        ex = [dict(r, round="x") for r in ex]
        rows = N.canonical_rows(None, ex, TODAY)
        self.assertEqual([r["round"] for r in rows], ["신건", "2차", "3차", "4차", "2차", "3차"])

    def test_level_rounds_rules(self):
        M = 1_000_000
        self.assertEqual(N.level_rounds([193 * M, 135 * M, 135 * M, 94 * M]), [1, 2, 2, 3])            # 확인표
        self.assertEqual(N.level_rounds([298 * M, 238 * M, 190 * M, 49 * M, 298 * M, 39 * M, 238 * M, 190 * M]),
                         [1, 2, 3, 4, 1, 5, 2, 3])
        self.assertEqual(N.level_rounds([2159 * M, 1511 * M, 1058 * M, 1795 * M, 1256 * M]), [1, 2, 3, 1, 2])  # 재감정
        self.assertEqual(N.level_rounds([100_000_000, None, 80_000_000]), [1, 1, 2])
        self.assertEqual(N.level_rounds([39_997_000, 39_996_800]), [1, 1])            # 반올림 차이는 같은 금액

    def test_stored_rounds_wrong(self):
        good = [_ex("신건", "2026-01-01", "100,000,000원", "유찰", id=1), _ex("2차", "2026-02-01", "80,000,000원", "", id=2),
                _ex("", "2026-02-08", "매각결정기일", "허가", id=3)]
        self.assertFalse(N.stored_rounds_wrong(good))
        bad = [dict(good[0]), dict(good[1], round="3차"), dict(good[2])]
        self.assertTrue(N.stored_rounds_wrong(bad))

    def test_price_reset_restarts_at_singeon(self):
        """재감정·새 경매 주기(금액 상승)는 신건부터 다시 — 실측 M01|2019|22106|1(2020 주기 → 2025 재감정)."""
        ex = [_ex("신건", "2020-04-06", "2,159,368,400원", "유찰", id=1),
              _ex("2차", "2020-05-11", "1,511,558,000원", "유찰", id=2),
              _ex("3차", "2020-06-15", "1,058,091,000원", "변경", id=3),
              _ex("신건", "2025-08-05", "1,795,658,100원", "유찰", id=4),
              _ex("2차", "2025-09-09", "1,256,961,000원", "유찰", id=5)]
        rows = N.canonical_rows(None, ex, TODAY)
        self.assertEqual([r["round"] for r in rows], ["신건", "2차", "3차", "신건", "2차"])

    def test_stale_doc_sale_row_dropped(self):
        doc = [{"date": "2026-03-01", "time": "", "kind": "매각기일", "place": "", "min_price": 100000000, "result": ""},
               {"date": "2026-05-01", "time": "", "kind": "매각기일", "place": "", "min_price": 100000000, "result": ""}]
        ex = [_ex("신건", "2026-05-01", "100,000,000원", "유찰", id=1)]
        rows = N.canonical_rows(doc, ex, TODAY)
        self.assertEqual([(r["round"], r["sell_date"], r["result"]) for r in rows], [("신건", "2026-05-01", "유찰")])

    def test_blank_legacy_rows_dropped(self):
        ex = [_ex("신건", "2026-05-01", "100,000,000원", "유찰", id=1), _ex("", "2026-05-08", "", "", id=2)]
        rows = N.canonical_rows(None, ex, TODAY)
        self.assertEqual(len(rows), 1)

    def test_merged_amount_row_is_idempotent(self):
        """같은 날짜·같은 회차칸 병합 표기('810,000,000원 / 890,000,000원')는 재정규화해도 매각기일·회차·표기 그대로."""
        ex = [_ex("신건", "2026-03-23", "810,000,000원 / 890,000,000원", "유찰 / 변경", id=1),
              _ex("2차", "2026-05-11", "567,000,000원", "유찰", id=2),
              _ex("3차", "2026-06-22", "396,900,000원", "유찰", id=3)]
        rows = N.canonical_rows(None, ex, TODAY)
        self.assertEqual([(r["round"], r["min_price"]) for r in rows],
                         [("신건", "810,000,000원 / 890,000,000원"), ("2차", "567,000,000원"), ("3차", "396,900,000원")])
        self.assertTrue(N.rows_equal(ex, rows))

    DOC_MULTI_OBJ1 = [
        {"date": "2026-05-06", "time": "10:00", "kind": "매각기일", "place": "", "min_price": 1422000000, "result": "유찰"},
        {"date": "2026-06-10", "time": "10:00", "kind": "매각기일", "place": "", "min_price": 1137600000, "result": "유찰"},
        {"date": "2026-07-15", "time": "10:00", "kind": "매각기일", "place": "", "min_price": 910080000, "result": ""},
        {"date": "2026-07-22", "time": "14:00", "kind": "매각결정기일", "place": "", "min_price": None, "result": ""},
    ]

    def test_doc_range_prunes_foreign_sale_rows(self):
        """문서 범위 안인데 문서에 없는 매각기일 행(다른 물건번호 혼입)은 제외 — 실측 A01|2025|939|1."""
        ex = [_ex("신건", "2026-05-06", "1,422,000,000원", "유찰", id=1),
              _ex("2차", "2026-06-10", "1,219,200,000원", "유찰", id=2),     # 물건 2의 2차(같은 날짜지만 문서 종류 병합으로 안 걸림 → 금액 다름)
              _ex("3차", "2026-06-11", "975,360,000원", "유찰", id=3),       # 문서에 없는 날짜 → 제외
              _ex("3차", "2026-07-15", "910,080,000원", "유찰", id=4)]       # 문서 행(결과 빈칸)의 결과 보강
        rows = N.canonical_rows(self.DOC_MULTI_OBJ1, ex, TODAY, doc_multi=True)
        self.assertEqual([(r["round"], r["sell_date"], r["min_price"]) for r in rows],
                         [("신건", "2026-05-06", "1,422,000,000원"), ("2차", "2026-06-10", "1,137,600,000원"),
                          ("3차", "2026-07-15", "910,080,000원")])

    def test_chain_prunes_foreign_rows_after_doc(self):
        """다물건 사건: 문서 뒤 날짜의 행은 저감률(20%) 사슬에 맞는 것만 — 728,064,000·582,451,200만 이 물건 것."""
        ex = [_ex("3차", "2026-07-15", "910,080,000원", "유찰", id=0),
              _ex("4차", "2026-08-19", "728,064,000원", "유찰", id=1), _ex("신건", "2026-08-19", "780,288,000원", "유찰", id=2),
              _ex("2차", "2026-08-19", "461,312,000원", "유찰", id=3), _ex("3차", "2026-09-22", "369,049,600원", "", id=4),
              _ex("", "2026-09-22", "582,451,200원 / 624,230,400원", "", id=5)]
        rows = N.canonical_rows(self.DOC_MULTI_OBJ1, ex, TODAY, doc_multi=True)
        tail = [(r["round"], r["sell_date"], r["min_price"]) for r in rows if r["sell_date"] >= "2026-08-01"]
        self.assertEqual(tail, [("4차", "2026-08-19", "728,064,000원"), ("5차", "2026-09-22", "582,451,200원")])

    def test_chain_with_standard_ratios_when_doc_has_no_drop(self):
        """문서가 전부 신건·변경(저감 이력 없음)이어도 문서 뒤 같은 날짜에 다른 금액이 있으면 표준 저감률(20%/30%)로 판정 —
        실측 A01|2024|124272|3: 473M 신건 변경×3 → 09-08 378,400,000(=×0.8, 유지)·190,873,600(다른 물건, 제외) → 10-13 302,720,000(=×0.8²)."""
        doc = [{"date": "2026-01-27", "time": "", "kind": "매각기일", "place": "", "min_price": 473000000, "result": "변경"},
               {"date": "2026-05-26", "time": "", "kind": "매각기일", "place": "", "min_price": 473000000, "result": "변경"},
               {"date": "2026-08-04", "time": "", "kind": "매각기일", "place": "", "min_price": 473000000, "result": "변경"}]
        ex = [_ex("신건", "2026-01-27", "473,000,000원", "변경", id=1), _ex("신건", "2026-05-26", "473,000,000원", "변경", id=2),
              _ex("신건", "2026-08-04", "473,000,000원", "변경", id=3), _ex("2차", "2026-09-08", "378,400,000원", "변경", id=4),
              _ex("3차", "2026-09-08", "190,873,600원", "변경", id=5), _ex("신건", "2026-10-13", "302,720,000원", "", id=6)]
        rows = N.canonical_rows(doc, ex, TODAY, doc_multi=True)
        self.assertEqual([(r["round"], r["sell_date"], r["min_price"]) for r in rows if r["sell_date"] >= "2026-09-01"],
                         [("2차", "2026-09-08", "378,400,000원"), ("3차", "2026-10-13", "302,720,000원")])

    def test_chain_not_applied_when_ratio_unknown_or_single_object(self):
        ex = [_ex("3차", "2026-07-15", "910,080,000원", "유찰", id=0),
              _ex("4차", "2026-08-19", "728,064,000원", "유찰", id=1), _ex("신건", "2026-08-19", "780,288,000원", "유찰", id=2)]
        rows = N.canonical_rows(self.DOC_MULTI_OBJ1, ex, TODAY, doc_multi=False)      # 단일 물건 문서 → 사슬 필터 없음
        self.assertEqual(len([r for r in rows if r["sell_date"] == "2026-08-19"]), 2)
        doc1 = self.DOC_MULTI_OBJ1[:1]                                                    # 금액 하나 → 저감률 미확정
        ex2 = [_ex("2차", "2026-08-19", "1,137,600,000원", "유찰", id=1), _ex("3차", "2026-09-22", "999,000,000원", "유찰", id=2)]
        rows = N.canonical_rows(doc1, ex2, TODAY, doc_multi=True)                         # 같은 날짜 다른 금액(혼입 증거) 없음 → 안 지움
        self.assertEqual(len([r for r in rows if r["sell_date"] >= "2026-08-01"]), 2)
        rows = N.canonical_rows(doc1, ex, TODAY, doc_multi=True)                          # 혼입 증거 있음 → 표준 저감률로 판정(780,288,000 제외)
        self.assertEqual([r["min_price"] for r in rows if r["sell_date"] == "2026-08-19"], ["728,064,000원"])

    def test_current_sale_date_added_and_protected(self):
        """목록의 현재(다음) 매각기일이 표에 없으면 추가하고, 문서 범위 가지치기로도 지우지 않는다 — 실측 1,124건."""
        ex = [_ex("신건", "2026-08-31", "100,000,000원", "유찰", id=1)]
        rows = N.canonical_rows(None, ex, TODAY, current=("2026-10-12", 80000000))
        self.assertEqual([(r["round"], r["sell_date"], r["min_price"], r["result"]) for r in rows],
                         [("신건", "2026-08-31", "100,000,000원", "유찰"), ("2차", "2026-10-12", "80,000,000원", "")])
        # 문서 범위 안(문서 마지막 매각기일 11-02 이하)인데 문서에 없는 날짜여도 현재 매각기일이면 유지
        doc = [{"date": "2026-08-31", "time": "", "kind": "매각기일", "place": "", "min_price": 100000000, "result": "유찰"},
               {"date": "2026-11-02", "time": "", "kind": "매각기일", "place": "", "min_price": 80000000, "result": ""}]
        rows = N.canonical_rows(doc, ex, TODAY, current=("2026-10-12", 80000000))
        self.assertIn("2026-10-12", [r["sell_date"] for r in rows])

    def test_current_not_added_when_past_or_present(self):
        ex = [_ex("신건", "2026-08-31", "100,000,000원", "유찰", id=1)]
        self.assertEqual(len(N.canonical_rows(None, ex, TODAY, current=("2026-07-07", 80000000))), 1)   # 지난 날짜(크롤러 미갱신 물건)
        self.assertEqual(len(N.canonical_rows(None, ex, TODAY, current=("2026-08-31", 100000000))), 1)  # 이미 있음
        self.assertEqual(len(N.canonical_rows(None, ex, TODAY, current=("2026-10-12", None))), 1)       # 최저가 없음
        self.assertEqual(len(N.canonical_rows(None, ex, TODAY, current=None)), 1)
        rows = N.canonical_rows(None, ex, TODAY, current=("2026-10-12", 80000000))
        self.assertTrue(N.rows_equal(rows, N.canonical_rows(None, rows, TODAY, current=("2026-10-12", 80000000))))  # 멱등

    def test_time_suffix_stripped(self):
        ex = [_ex("신건", "2026-05-01 (10:30)", "100,000,000원", "진행", id=1)]
        rows = N.canonical_rows(None, ex, TODAY)
        self.assertEqual(rows[0]["sell_date"], "2026-05-01")
        self.assertEqual(rows[0]["result"], "진행")


class CourtParse(unittest.TestCase):
    HTML = """<table><tr><th>물건번호</th><th>감정평가액</th><th>기일</th><th>기일종류</th><th>기일장소</th><th>최저매각가격</th><th>기일결과</th></tr>
    <tr><td rowspan="3">1</td><td rowspan="3">193,000,000원</td><td>2026.04.20(10:00)</td><td>매각기일</td><td>경매법정</td><td>193,000,000원</td><td>유찰</td></tr>
    <tr><td>2026.06.01(10:00)</td><td>매각기일</td><td>경매법정</td><td>135,100,000원</td><td>매각</td></tr>
    <tr><td>2026.06.08(14:00)</td><td>매각결정기일</td><td>법정</td><td></td><td>최고가매각허가결정</td></tr>
    <tr><td rowspan="1">2</td><td rowspan="1">50,000,000원</td><td>2026.04.20(10:00)</td><td>매각기일</td><td>경매법정</td><td>50,000,000원</td><td>유찰</td></tr>
    </table>"""

    def test_parse_filters_by_obj_no(self):
        rows = N.parse_court_schedule(self.HTML, "1")
        self.assertEqual([(r["date"], r["kind"], r["min_price"], r["result"]) for r in rows], [
            ("2026-04-20", "매각기일", 193000000, "유찰"), ("2026-06-01", "매각기일", 135100000, "매각"),
            ("2026-06-08", "매각결정기일", None, "최고가매각허가결정")])
        self.assertEqual(len(N.parse_court_schedule(self.HTML, "2")), 1)
        self.assertEqual(sorted(N.parse_court_schedule_all(self.HTML)), ["1", "2"])


if __name__ == "__main__":
    unittest.main()
