"""
배당표 계산 검증 테스트.

    python test_distribution.py

손으로 검산 가능한 시나리오로 단계별 배당을 확인한다.
  D-1) 소액임차인 최우선변제 + 1순위 근저당 (확정일자 임차인)
  D-2) 소액 최우선이 매각가 1/2 한도를 넘어 안분되는 경우
  D-3) 대항력 임차인 미배당 잔여 → 낙찰자 인수액 정밀 산출
  D-4) 가압류 안분배당
"""

import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")

from auction_analysis import (
    AuctionProperty, Right, RightType, Tenant,
    Region, Claim, ClaimKind, calculate_distribution, small_tenant_bracket,
)


def check(label, got, expected):
    ok = got == expected
    print(("  ✅ " if ok else "  ❌ ") + f"{label}: {got:,}"
          + ("" if ok else f"  (기대값 {expected:,})"))
    return ok


def test_bracket():
    print("[D-0] 소액임차인 표 조회 (기준일·지역)")
    ok = True
    # 2024년 설정 → 현행(2023.02.21) 표
    md, mp = small_tenant_bracket(date(2024, 1, 1), Region.OTHER)
    ok &= check("그밖지역 소액보증금 상한", md, 75_000_000)
    ok &= check("그밖지역 최우선변제 상한", mp, 25_000_000)
    # 2020년 설정 → 2018.09.18 표
    md2, mp2 = small_tenant_bracket(date(2020, 1, 1), Region.SEOUL)
    ok &= check("서울 2018표 최우선변제 상한", mp2, 37_000_000)
    return ok


def test_small_tenant_priority():
    print("[D-1] 소액 최우선 + 근저당 (확정일자 임차인)")
    # 매각가 1억, 비용 0, 그밖지역, 근저당 2024 설정
    # 소액임차인(보증금 6천만, 확정일자 O, 배당요구 O):
    #   1단계 소액 최우선 2,500만 (<= 5천만 한도)
    #   2단계 우선변제: 근저당(2024-03-10) vs 임차확정일자(2024-05-01) → 근저당 먼저
    #     근저당 8천만 청구 → 잔여 7,500만 전액 근저당
    #     임차 잔여(6천만-2,500만=3,500만) → 잔여 0 → 0원
    prop = AuctionProperty(
        case_no="2024타경40001", property_type="빌라",
        rights=[Right(RightType.MORTGAGE, date(2024, 3, 10), "○○은행", 80_000_000)],
        tenants=[Tenant("김소액", move_in_date=date(2024, 4, 1),
                        fixed_date=date(2024, 5, 1), deposit=60_000_000,
                        demanded_distribution=True)],
    )
    r = calculate_distribution(prop, sale_price=100_000_000, region=Region.OTHER)
    print(r.summary())
    ok = True
    tr = r.tenant_recoveries[0]
    ok &= check("임차인 회수(소액 2,500만만)", tr.received, 25_000_000)
    # 임차인이 근저당보다 후순위(대항력X: 전입 2024-04-01 > 근저당 2024-03-10) → 인수 0
    ok &= check("낙찰자 인수액", r.buyer_assumed_total, 0)
    ok &= check("근저당 배당", next(p.paid for p in r.payouts if "근저당" in p.claim.label), 75_000_000)
    return ok


def test_senior_tenant_shortfall():
    print("[D-3] 대항력 임차인 미배당 → 낙찰자 인수")
    # 매각가 1.5억, 비용 0, 그밖지역
    # 임차인: 전입 2021-11-01(근저당 2022-06-20보다 빠름 → 대항력 O),
    #         확정일자 2021-11-01, 보증금 1.8억, 배당요구 O. 소액 아님(1.8억>7,500만).
    # 우선변제 순위: 임차확정(2021-11-02 대항력익일/확정 2021-11-01 → max=2021-11-02)
    #               < 근저당(2022-06-20) → 임차인 먼저
    #   임차인 1.8억 청구 → 잔여 1.5억 전액 임차인. 미배당 3천만 → 대항력이므로 낙찰자 인수.
    prop = AuctionProperty(
        case_no="2024타경40003", property_type="빌라",
        rights=[Right(RightType.MORTGAGE, date(2022, 6, 20), "□□은행", 150_000_000)],
        tenants=[Tenant("이대항", move_in_date=date(2021, 11, 1),
                        fixed_date=date(2021, 11, 1), deposit=180_000_000,
                        demanded_distribution=True)],
    )
    r = calculate_distribution(prop, sale_price=150_000_000, region=Region.OTHER)
    print(r.summary())
    tr = r.tenant_recoveries[0]
    ok = check("임차인 회수", tr.received, 150_000_000)
    ok &= check("낙찰자 인수(미배당 3천만)", r.buyer_assumed_total, 30_000_000)
    return ok


def test_general_proration():
    print("[D-4] 가압류 안분배당")
    # 매각가 1억, 근저당 없음. 가압류 A 6천만 / 가압류 B 4천만 (합 1억)
    # 우선변제 없음 → 안분: A 6천만, B 4천만 (정확히 청구액만큼)
    prop = AuctionProperty(
        case_no="2024타경40004", property_type="아파트",
        rights=[
            Right(RightType.PROV_SEIZURE, date(2023, 1, 1), "가압류A", 60_000_000),
            Right(RightType.PROV_SEIZURE, date(2023, 2, 1), "가압류B", 40_000_000),
        ],
    )
    r = calculate_distribution(prop, sale_price=100_000_000, region=Region.OTHER)
    print(r.summary())
    payA = next(p.paid for p in r.payouts if p.claim.holder == "가압류A")
    payB = next(p.paid for p in r.payouts if p.claim.holder == "가압류B")
    ok = check("가압류A 안분", payA, 60_000_000)
    ok &= check("가압류B 안분", payB, 40_000_000)
    return ok


def main():
    results = [test_bracket(), test_small_tenant_priority(),
               test_senior_tenant_shortfall(), test_general_proration()]
    for _ in results:
        pass
    print("-" * 70)
    print(("🎉 전체 통과" if all(results) else "⚠️ 일부 실패")
          + f"  ({sum(results)}/{len(results)})")


if __name__ == "__main__":
    main()
