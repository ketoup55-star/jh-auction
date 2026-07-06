"""
경매 물건 데이터 소스.

AuctionSource: 외부에서 물건을 가져오는 추상 인터페이스.
MockSource:    개발/테스트용 샘플 데이터. 실제 운영 시 CODEF/법원경매 소스로 교체.

※ 합법성 원칙: 실제 소스는 법원경매정보(원천) 또는 CODEF API로 구현한다.
  타 유료사이트 크롤링은 사용하지 않는다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from .models import Right, RightType, Tenant
from .listing import Listing, ResidentialType, classify_residential
from .distribution import Region


class AuctionSource(ABC):
    """물건 데이터 공급 인터페이스."""

    @abstractmethod
    def fetch(self) -> list[Listing]:
        """물건 목록을 가져온다(원본 용도/권리/임차인 포함)."""
        raise NotImplementedError


class MockSource(AuctionSource):
    """샘플 5종 + 제외대상(오피스텔/선순위가등기)을 섞어 제공."""

    def fetch(self) -> list[Listing]:
        return [
            # 1) 아파트 - 깨끗한 일반물건
            Listing(
                case_no="2026타경1001", court="서울중앙지방법원",
                raw_type="아파트", address="서울 강남구 역삼동 100", region=Region.SEOUL,
                appraisal_value=1_200_000_000, min_bid_price=960_000_000,
                failed_count=1, sale_date=date(2026, 7, 15), status="유찰",
                view_count=312,
                auction_type="임의경매", building_area=84.97, land_area=29.41,
                lat=37.5006, lng=127.0366,
                rights=[
                    Right(RightType.MORTGAGE, date(2023, 5, 1), "국민은행", 700_000_000),
                    Right(RightType.AUCTION_START, date(2026, 1, 10), "국민은행"),
                ],
                tenants=[],
            ),
            # 2) 빌라(다세대) - 선순위 대항력 임차인
            Listing(
                case_no="2026타경1002", court="수원지방법원",
                raw_type="다세대주택", address="경기 수원시 권선구 200", region=Region.OVERCONCENTRATION,
                appraisal_value=300_000_000, min_bid_price=210_000_000,
                failed_count=2, sale_date=date(2026, 7, 20), status="유찰",
                view_count=250,
                auction_type="임의경매", building_area=59.82, land_area=33.15,
                lat=37.2636, lng=127.0286,
                rights=[
                    Right(RightType.MORTGAGE, date(2022, 6, 20), "신한은행", 150_000_000),
                    Right(RightType.AUCTION_START, date(2026, 2, 1), "신한은행"),
                ],
                tenants=[Tenant("이대항", move_in_date=date(2021, 11, 1),
                                fixed_date=date(2021, 11, 1), deposit=180_000_000,
                                demanded_distribution=True)],
            ),
            # 3) 도시형생활주택 - 소액임차인
            Listing(
                case_no="2026타경1003", court="인천지방법원",
                raw_type="도시형생활주택", address="인천 미추홀구 300", region=Region.METRO,
                appraisal_value=180_000_000, min_bid_price=126_000_000,
                failed_count=2, sale_date=date(2026, 8, 5), status="유찰",
                view_count=180,
                auction_type="강제경매", building_area=29.76, land_area=15.22,
                lat=37.4639, lng=126.6508,
                rights=[
                    Right(RightType.MORTGAGE, date(2024, 3, 10), "우리은행", 100_000_000),
                    Right(RightType.AUCTION_START, date(2026, 1, 20), "우리은행"),
                ],
                tenants=[Tenant("박소액", move_in_date=date(2024, 5, 1),
                                fixed_date=date(2024, 5, 1), deposit=80_000_000,
                                demanded_distribution=True)],
            ),
            # 4) 상가주택 - 일반
            Listing(
                case_no="2026타경1004", court="대전지방법원",
                raw_type="상가주택", address="대전 서구 400", region=Region.METRO,
                appraisal_value=550_000_000, min_bid_price=385_000_000,
                failed_count=2, sale_date=date(2026, 8, 12), status="재진행",
                view_count=95,
                auction_type="임의경매", building_area=219.5, land_area=180.2,
                lat=36.3515, lng=127.3845,
                rights=[
                    Right(RightType.MORTGAGE, date(2023, 9, 1), "하나은행", 300_000_000),
                    Right(RightType.AUCTION_START, date(2026, 2, 15), "하나은행"),
                ],
                tenants=[],
            ),
            # 5) 다가구주택 - 선순위 가등기(수집정책으로 제외돼야 함)
            Listing(
                case_no="2026타경1005", court="부산지방법원",
                raw_type="다가구주택", address="부산 해운대구 500", region=Region.METRO,
                appraisal_value=600_000_000, min_bid_price=294_000_000,
                failed_count=4, sale_date=date(2026, 8, 20),
                lat=35.1631, lng=129.1635,
                rights=[
                    Right(RightType.OWNERSHIP_PROV_REG, date(2020, 4, 1), "박가등", 0,
                          note="소유권이전청구권 보전"),
                    Right(RightType.MORTGAGE, date(2021, 9, 15), "농협", 250_000_000),
                    Right(RightType.AUCTION_START, date(2026, 3, 1), "농협"),
                ],
                tenants=[],
            ),
            # 6) 오피스텔 - 5종 아님(분류 None → 제외돼야 함)
            Listing(
                case_no="2026타경1006", court="서울남부지방법원",
                raw_type="오피스텔", address="서울 영등포구 600", region=Region.SEOUL,
                appraisal_value=250_000_000, min_bid_price=200_000_000,
                failed_count=1, sale_date=date(2026, 8, 25),
                rights=[Right(RightType.MORTGAGE, date(2023, 1, 1), "케이뱅크", 180_000_000)],
                tenants=[],
            ),
        ]
