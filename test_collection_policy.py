"""
수집 정책 검증 테스트.

    python test_collection_policy.py

선순위 가등기 물건은 수집 제외, 후순위 가등기/일반물건은 수집됨을 확인한다.
"""

import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")

from auction_analysis import (
    AuctionProperty,
    Right,
    RightType,
    is_collectible,
    screen,
    filter_collectible,
)


def check(label, got, expected):
    ok = got == expected
    print(("  ✅ " if ok else "  ❌ ") + f"{label}: {got!r}"
          + ("" if ok else f"  (기대값 {expected!r})"))
    return ok


def prop_senior_gadeunggi():
    """선순위 가등기(2020) > 근저당(2021) → 제외 대상."""
    return AuctionProperty(
        case_no="2024타경30001", property_type="다가구주택",
        rights=[
            Right(RightType.OWNERSHIP_PROV_REG, date(2020, 4, 1), "박가등"),
            Right(RightType.MORTGAGE, date(2021, 9, 15), "◇◇은행", 250_000_000),
            Right(RightType.AUCTION_START, date(2024, 3, 1), "◇◇은행"),
        ],
    )


def prop_junior_gadeunggi():
    """후순위 가등기(2023) < 근저당(2021) → 수집 가능(낙찰로 소멸)."""
    return AuctionProperty(
        case_no="2024타경30002", property_type="빌라",
        rights=[
            Right(RightType.MORTGAGE, date(2021, 5, 1), "○○은행", 200_000_000),
            Right(RightType.OWNERSHIP_PROV_REG, date(2023, 7, 1), "김후순"),
            Right(RightType.AUCTION_START, date(2024, 1, 1), "○○은행"),
        ],
    )


def prop_clean():
    """가등기 없는 일반물건 → 수집 가능."""
    return AuctionProperty(
        case_no="2024타경30003", property_type="아파트",
        rights=[
            Right(RightType.MORTGAGE, date(2022, 3, 10), "△△은행", 400_000_000),
            Right(RightType.AUCTION_START, date(2024, 2, 1), "△△은행"),
        ],
    )


def main():
    ok = True
    senior, junior, clean = prop_senior_gadeunggi(), prop_junior_gadeunggi(), prop_clean()

    print("[개별 판정]")
    ok &= check("선순위 가등기 → 수집 제외", is_collectible(senior), False)
    ok &= check("후순위 가등기 → 수집 가능", is_collectible(junior), True)
    ok &= check("일반물건 → 수집 가능", is_collectible(clean), True)
    print(f"    제외 사유: {screen(senior)}")

    print("[일괄 필터링]")
    keep, dropped = filter_collectible([senior, junior, clean])
    ok &= check("수집대상 수", len(keep), 2)
    ok &= check("제외 수", len(dropped), 1)
    ok &= check("제외된 사건번호", dropped[0][0].case_no, "2024타경30001")

    print("-" * 70)
    print("🎉 전체 통과" if ok else "⚠️ 일부 실패")


if __name__ == "__main__":
    main()
