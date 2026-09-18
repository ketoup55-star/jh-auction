# -*- coding: utf-8 -*-
"""같은 단지 실거래 매칭(match_apt) 재발방지 '못' — 2026-09-18 전수감사에서 실제로 틀렸던 주소를 그대로 고정한다.
실행: python -m unittest test_match_apt -v   (네트워크·DB 불필요)

이 테스트가 깨지면 아래 중 하나가 되살아난 것이다:
  ① 단지명 폴백이 주소 문자열 '어디든'을 봄 → 시도·시군구 이름('강원'·'서울'·'평택' 단지)이 붙음
  ② 단지명 폴백이 다른 법정동 단지까지 붙임(거제 한내리 숲속의아침뷰 → 아주동 '숲속의아침')
  ③ 읍면 지역 지번매칭 불능(실거래 법정동 '정관읍 모전리' ≠ 주소 '모전리')
  ④ 지번 주소인데 멀리 떨어진 다른 번지의 흔한 이름('현대')을 붙임
"""
import unittest

from auction_analysis.molit_source import addr_location, match_apt


def _t(name, umd, jibun, area=84.9, date="2026-06-01", amount=300_000_000):
    return {"name": name, "umd": umd, "jibun": jibun, "area": area, "deal_date": date, "amount": amount, "build_year": "2010"}


def _spots(r):
    return sorted({(t["umd"], t["jibun"]) for t in r["trades"]})


class TestAddrLocation(unittest.TestCase):
    def test_jibun_eupmyeon(self):
        loc = addr_location("부산광역시 기장군 정관읍 모전리 755 정관신도시현진에버빌 105동 9층901호")
        self.assertEqual((loc["emd"], loc["ri"], loc["jibun"]), ("정관읍", "모전리", "755"))
        self.assertEqual(loc["cname"], "정관신도시현진에버빌")

    def test_road_with_paren_dong(self):
        loc = addr_location("서울특별시 노원구 중계로 233, 104동 3층304호 (중계동,청구아파트)")
        self.assertEqual((loc["emd"], loc["jibun"], loc["cname"]), ("중계동", "", "청구"))
        self.assertNotIn("서울", loc["name"])        # 시도·시군구 이름은 단지명부가 아님
        self.assertNotIn("노원", loc["name"])

    def test_jibun_with_oe(self):
        loc = addr_location("서울특별시 도봉구 쌍문동 734외 4필지 청구아파트 제109동 제1층 제106호")
        self.assertEqual((loc["emd"], loc["jibun"]), ("쌍문동", "734"))


class TestMatchApt(unittest.TestCase):
    def test_city_name_complex_not_matched(self):          # ① '강원'·'서울' 단지
        r = match_apt([_t("강원", "교동", "797-1")],
                      "강원특별자치도 강릉시 입암동 713 강릉입암동천년가밸로채아파트 104동 5층 502호", area=84.9)
        self.assertEqual(r["trades"], [])
        r = match_apt([_t("서울", "신월동", "517-6")], "서울특별시 양천구 신월동 131-2 그라비스 102동 6층 601호")
        self.assertEqual(r["trades"], [])

    def test_other_dong_same_name_not_matched(self):        # ② 거제 한내리 ↔ 아주동
        trades = [_t("숲속의아침", "아주동", "890"), _t("한내시온숲속의아침뷰", "연초면 한내리", "935")]
        r = match_apt(trades, "경상남도 거제시 연초면 한내리 935 한내시온숲속의아침뷰 104동 19층1902호")
        self.assertEqual(_spots(r), [("연초면 한내리", "935")])
        r = match_apt([_t("청구", "하계동", "284")], "서울특별시 노원구 중계로 233, 104동 3층304호 (중계동,청구아파트)")
        self.assertEqual(r["trades"], [])
        r = match_apt([_t("청구", "하계동", "284"), _t("청구", "중계동", "500")],
                      "서울특별시 노원구 중계로 233, 104동 3층304호 (중계동,청구아파트)")
        self.assertEqual(_spots(r), [("중계동", "500")])

    def test_eupmyeon_jibun_match(self):                     # ③ 읍면 지번매칭 + 면→읍 승격
        r = match_apt([_t("정관현진에버빌", "정관읍 모전리", "755"), _t("현진", "정관읍 달산리", "10")],
                      "부산광역시 기장군 정관읍 모전리 755 정관신도시현진에버빌 105동 9층901호")
        self.assertEqual((r["match_by"], _spots(r)), ("지번", [("정관읍 모전리", "755")]))
        r = match_apt([_t("동보호수마을", "직산읍 모시리", "253-38")],
                      "충청남도 천안시 직산면 모시리 253-38 동보영구임대아파트 101동 10층1030호")
        self.assertEqual(r["complex"], "동보호수마을")

    def test_far_lot_generic_name_not_matched(self):         # ④ 숭의동 184-21 현대맨션 ↔ 숭의동 129-96 '현대'
        r = match_apt([_t("현대", "숭의동", "129-96"), _t("현대", "주안동", "80")],
                      "인천광역시 미추홀구 숭의동 184-21 현대맨션  4층401호")
        self.assertEqual(r["trades"], [])

    def test_big_complex_neighbor_lot_matched(self):         # 큰 단지 옆 필지(725 ↔ 726)는 같은 단지, 다른 이름('엄궁')은 제외
        r = match_apt([_t("롯데캐슬", "엄궁동", "726"), _t("엄궁", "엄궁동", "35-1")],
                      "부산광역시 사상구 엄궁동 725 엄궁롯데캐슬리버 203동 20층2003호")
        self.assertEqual(_spots(r), [("엄궁동", "726")])

    def test_longest_name_wins(self):
        r = match_apt([_t("청구", "중계동", "10"), _t("청구3차", "중계동", "20")],
                      "서울특별시 노원구 중계로 1, 1동 1층101호 (중계동,청구3차)")
        self.assertEqual(_spots(r), [("중계동", "20")])

    def test_exact_jibun_kept_and_area_filter(self):
        trades = [_t("시티프라디움", "쌍문동", "734", area=84.9), _t("시티프라디움", "쌍문동", "734", area=59.9)]
        r = match_apt(trades, "서울특별시 도봉구 쌍문동 734외 4필지 청구아파트 제109동 제1층 제106호", area=84.9, area_pct=0.05)
        self.assertEqual((r["match_by"], len(r["trades"]), len(r["same_area"]), r["area_matched"]), ("지번", 2, 1, True))


if __name__ == "__main__":
    unittest.main()
