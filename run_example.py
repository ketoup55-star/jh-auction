"""
권리분석 엔진 동작 예제.

    python run_example.py

3가지 시나리오로 엔진을 검증한다.
  CASE 1) 깨끗한 물건 (후순위 임차인) → 안전
  CASE 2) 선순위 대항력 임차인 (배당요구 안 함) → 위험, 보증금 인수
  CASE 3) 선순위 소유권이전청구권 가등기 → 특수물건, 전문가 검토
"""

import sys
from datetime import date

# Windows 콘솔(cp949)에서도 한글/이모지 출력되도록 UTF-8 고정
sys.stdout.reconfigure(encoding="utf-8")

from auction_analysis import (
    AuctionProperty,
    Right,
    RightType,
    Tenant,
    analyze,
)


def case1_clean() -> AuctionProperty:
    """근저당 후에 전입한 임차인 → 대항력 없음, 깨끗."""
    return AuctionProperty(
        case_no="2024타경10001",
        court="서울중앙지방법원",
        property_type="아파트",
        address="서울 강남구 ...",
        appraisal_value=800_000_000,
        min_bid_price=640_000_000,
        rights=[
            Right(RightType.MORTGAGE, date(2021, 3, 10), "○○은행", 500_000_000),
            Right(RightType.PROV_SEIZURE, date(2023, 8, 1), "△△카드", 20_000_000),
            Right(RightType.AUCTION_START, date(2024, 1, 5), "○○은행"),
        ],
        tenants=[
            Tenant("김세입", move_in_date=date(2022, 5, 1),
                   fixed_date=date(2022, 5, 1), deposit=200_000_000,
                   demanded_distribution=True),
        ],
    )


def case2_senior_tenant() -> AuctionProperty:
    """근저당보다 먼저 전입 + 배당요구 안 함 → 보증금 전액 인수."""
    return AuctionProperty(
        case_no="2024타경10002",
        court="수원지방법원",
        property_type="빌라",
        address="경기 수원시 ...",
        appraisal_value=300_000_000,
        min_bid_price=210_000_000,
        rights=[
            Right(RightType.MORTGAGE, date(2022, 6, 20), "□□은행", 150_000_000),
            Right(RightType.AUCTION_START, date(2024, 2, 10), "□□은행"),
        ],
        tenants=[
            # 전입이 근저당(2022-06-20)보다 빠름 + 배당요구 안 함
            Tenant("이대항", move_in_date=date(2021, 11, 1),
                   fixed_date=None, deposit=180_000_000,
                   demanded_distribution=False),
        ],
    )


def case3_special() -> AuctionProperty:
    """선순위 소유권이전청구권 가등기 → 낙찰자 소유권 위협, 특수물건."""
    return AuctionProperty(
        case_no="2024타경10003",
        court="인천지방법원",
        property_type="다가구주택",
        address="인천 ...",
        appraisal_value=600_000_000,
        min_bid_price=294_000_000,  # 여러 번 유찰 → 특수물건 신호
        rights=[
            Right(RightType.OWNERSHIP_PROV_REG, date(2020, 4, 1), "박가등", 0,
                  note="소유권이전청구권 보전"),
            Right(RightType.MORTGAGE, date(2021, 9, 15), "◇◇은행", 250_000_000),
            Right(RightType.AUCTION_START, date(2024, 3, 1), "◇◇은행"),
        ],
        tenants=[],
    )


def main() -> None:
    for builder in (case1_clean, case2_senior_tenant, case3_special):
        prop = builder()
        result = analyze(prop)
        print(result.summary())
        print("-" * 70)


if __name__ == "__main__":
    main()
