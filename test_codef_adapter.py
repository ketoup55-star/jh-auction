"""
CODEF 어댑터 검증 테스트.

    python test_codef_adapter.py

3개 레이어를 검증한다.
  A) 순수함수: 권리종류 분류 / 날짜·금액 파싱   (확실히 동작 보장)
  B) 정규화 엔트리 → Right → 분석              (구조 확정)
  C) CODEF-형태 응답 평탄화 → 분석              (실제 키는 # TODO(verify))
"""

import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")

from auction_analysis import (
    classify_right_type,
    parse_date_kr,
    parse_amount_kr,
    entries_to_rights,
    register_to_property,
    analyze,
    RightType,
    Tenant,
)


def check(label, got, expected):
    ok = got == expected
    mark = "✅" if ok else "❌"
    print(f"  {mark} {label}: {got!r}" + ("" if ok else f"  (기대값 {expected!r})"))
    return ok


def test_classify():
    print("[A-1] 권리종류 분류")
    cases = [
        ("근저당권설정", RightType.MORTGAGE),
        ("소유권이전청구권가등기", RightType.OWNERSHIP_PROV_REG),
        ("담보가등기", RightType.SECURITY_PROV_REG),
        ("가압류", RightType.PROV_SEIZURE),
        ("압류", RightType.SEIZURE),
        ("가처분", RightType.INJUNCTION),
        ("강제경매개시결정", RightType.AUCTION_START),
        ("전세권설정", RightType.JEONSE),
        ("소유권이전", None),       # 단순 소유권 변동 → 대상 아님
        ("소유권보존", None),
    ]
    return all(check(t, classify_right_type(t), e) for t, e in cases)


def test_parse():
    print("[A-2] 날짜 파싱")
    ok = True
    ok &= check("2021년 3월 10일", parse_date_kr("2021년 3월 10일"), date(2021, 3, 10))
    ok &= check("2021.03.10", parse_date_kr("2021.03.10"), date(2021, 3, 10))
    ok &= check("접수란 혼합", parse_date_kr("2022년6월20일 제45678호"), date(2022, 6, 20))
    print("[A-3] 금액 파싱")
    ok &= check("금500,000,000원", parse_amount_kr("금500,000,000원"), 500_000_000)
    ok &= check("1억2,000만원", parse_amount_kr("1억2,000만원"), 120_000_000)
    # 회귀 방지: 화폐 단서 없는 본문(날짜·접수번호)을 금액으로 오인하면 안 됨
    ok &= check("날짜/접수번호 오인 방지", parse_amount_kr("2020년 4월 1일 제11111호"), 0)
    return ok


def test_entries():
    print("[B] 정규화 엔트리 → Right → 분석")
    entries = [
        {"section": "을구", "purpose": "근저당권설정",
         "date_text": "2022년 6월 20일", "holder": "□□은행",
         "amount_text": "금150,000,000원"},
        {"section": "갑구", "purpose": "강제경매개시결정",
         "date_text": "2024년 2월 10일", "holder": "□□은행"},
        {"section": "을구", "purpose": "근저당권설정",
         "date_text": "2019년 1월 1일", "holder": "옛은행",
         "amount_text": "금1억원", "cancelled": True},  # 말소 → 제외돼야 함
    ]
    rights = entries_to_rights(entries)
    ok = check("말소 제외 후 권리 수", len(rights), 2)
    prop = register_to_property(
        {}, case_no="2024타경20002", property_type="빌라",
        tenants=[Tenant("이대항", move_in_date=date(2021, 11, 1),
                        deposit=180_000_000, demanded_distribution=False)],
    )
    prop.rights = rights  # 위에서 만든 권리 주입
    result = analyze(prop)
    print(result.summary())
    ok &= check("말소기준 = 근저당(2022-06-20)",
                (result.baseline_right.type, result.baseline_right.reg_date),
                (RightType.MORTGAGE, date(2022, 6, 20)))
    ok &= check("선순위 임차인 보증금 인수", result.assumed_amount_total, 180_000_000)
    return ok


def test_codef_shape():
    print("[C] CODEF-형태 응답 평탄화 → 분석  (실제 키는 보정 필요)")
    # CODEF 등기사항요약 응답을 모사한 샘플 (구조는 # TODO(verify))
    codef_data = {
        "resRegisterEntriesList": [{
            "resType": "집합건물",
            "resRegistrationHisList": [
                {
                    "resType": "을구",
                    "resContentsList": [{
                        "resNumber": "1",
                        "resPurpose": "근저당권설정",
                        "resDetailList": [
                            {"resType": "접수", "resContents": "2021년 9월 15일 제33333호"},
                            {"resType": "채권최고액", "resContents": "금250,000,000원"},
                            {"resType": "권리자 및 기타사항", "resContents": "근저당권자 ◇◇은행"},
                        ],
                    }],
                },
                {
                    "resType": "갑구",
                    "resContentsList": [{
                        "resNumber": "2",
                        "resPurpose": "소유권이전청구권가등기",
                        "resDetailList": [
                            {"resType": "접수", "resContents": "2020년 4월 1일 제11111호"},
                            {"resType": "권리자 및 기타사항", "resContents": "가등기권자 박가등"},
                        ],
                    }],
                },
            ],
        }],
    }
    prop = register_to_property(
        codef_data, case_no="2024타경20003", property_type="다가구주택",
        appraisal_value=600_000_000, min_bid_price=294_000_000,
    )
    result = analyze(prop)
    print(result.summary())
    ok = check("권리 2건 파싱", len(result.right_verdicts), 2)
    ok &= check("선순위 가등기 → 특수물건", result.needs_expert_review, True)
    return ok


def main():
    results = []
    for fn in (test_classify, test_parse, test_entries, test_codef_shape):
        results.append(fn())
        print("-" * 70)
    print(("🎉 전체 통과" if all(results) else "⚠️ 일부 실패") + f"  ({sum(results)}/{len(results)})")


if __name__ == "__main__":
    main()
