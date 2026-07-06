"""
권리분석 엔진 (핵심 로직).

분석 순서
  1) 말소기준권리 판정      : 후보 권리 중 가장 빠른 등기일
  2) 인수/소멸 판정         : 말소기준 대비 선/후순위 + 권리 종류
  3) 임차인 대항력/배당 판정 : 대항력 발생일 vs 말소기준일, 배당요구 여부

주의: 일반물건은 자동 판정, 특수물건(가등기·가처분·선순위전세권 등)은
needs_expert_review 플래그로 표시하고 경고를 남긴다. 자동 결과를 맹신하지 말 것.
"""

from __future__ import annotations

from .models import (
    AuctionProperty,
    Right,
    RightType,
    AnalysisResult,
    RightVerdict,
    TenantVerdict,
    BASELINE_CANDIDATES,
    SURVIVOR_IF_SENIOR,
    HIGH_RISK_TYPES,
)


def find_baseline(rights: list[Right]) -> Right | None:
    """말소기준권리 = 후보권리 중 가장 빠른 등기일(동일 시 접수순서 가정).

    전세권은 '건물 전부 + 배당요구/경매신청' 조건을 만족할 때만 후보가 된다.
    (collection_policy 등 외부에서도 재사용하므로 공개 함수로 둔다.)
    """
    candidates: list[Right] = []
    for r in rights:
        if r.type in BASELINE_CANDIDATES:
            candidates.append(r)
        elif r.type == RightType.JEONSE:
            if r.jeonse_covers_whole and r.jeonse_demanded_distribution:
                candidates.append(r)

    if not candidates:
        return None
    return min(candidates, key=lambda r: r.reg_date)


def _judge_right(r: Right, baseline: Right | None) -> RightVerdict:
    """권리 1건의 인수/소멸 판정."""
    # 소유권이전/보존은 현 소유관계 표시용 — 인수/소멸·말소기준 대상이 아님
    if r.type == RightType.OWNERSHIP_TRANSFER:
        return RightVerdict(r, "소유권", "소유권 등기(현 소유관계 표시, 인수/소멸 대상 아님)")

    if baseline is None:
        # 말소기준이 없으면 모든 권리가 인수 대상 (드문 케이스 → 전문가 검토)
        return RightVerdict(r, "인수", "말소기준권리 없음 → 인수 가능성, 검토 필요")

    if r is baseline:
        return RightVerdict(r, "말소기준권리", "이 권리부터 이하 후순위 권리가 소멸")

    senior = r.reg_date < baseline.reg_date

    # 후순위 권리는 원칙적으로 소멸
    if not senior:
        # 예외: 건물철거·토지인도 목적 가처분은 후순위라도 인수될 수 있음
        if r.type == RightType.INJUNCTION and "철거" in r.note:
            return RightVerdict(
                r, "인수", "건물철거 목적 가처분 → 후순위라도 인수(예외), 검토 필요"
            )
        return RightVerdict(r, "소멸", "말소기준보다 후순위 → 낙찰로 소멸")

    # 선순위 권리: 종류에 따라 인수
    if r.type in SURVIVOR_IF_SENIOR:
        return RightVerdict(
            r, "인수", "말소기준보다 선순위 → 낙찰자 인수(부담)"
        )

    # 선순위인데 금전채권성 권리면 사실상 그게 말소기준이어야 함(데이터 모순)
    return RightVerdict(
        r, "소멸", "말소기준보다 선순위 금전권리(데이터 확인 필요)"
    )


def _judge_tenant(t, baseline: Right | None, result: AnalysisResult) -> TenantVerdict:
    """임차인 대항력 + 인수 보증금 판정."""
    op_date = t.opposing_power_date

    # 대항력 판단: 대항력 발생일이 말소기준일보다 빠르거나 같으면 대항력 있음
    if baseline is None:
        has_power = op_date is not None
    elif op_date is None:
        has_power = False
    else:
        has_power = op_date <= baseline.reg_date

    if not has_power:
        return TenantVerdict(
            t, False, 0, "전입일이 말소기준보다 늦음(또는 미상) → 대항력 없음, 보증금 소멸"
        )

    # 대항력 있는 선순위 임차인
    if not t.demanded_distribution:
        # 배당요구 안 함 → 보증금 전액 낙찰자 인수
        return TenantVerdict(
            t, True, t.deposit,
            "선순위 + 배당요구 안 함 → 보증금 전액 낙찰자 인수",
        )

    # 배당요구는 했으나, 실제 배당 부족분은 낙찰자 인수 가능
    # (정확한 배당표 계산 전이므로 보수적으로 경고만)
    result.warnings.append(
        f"{t.name or '임차인'}: 선순위 + 배당요구 → 배당부족 시 잔여보증금 인수 가능. "
        f"확정일자({t.fixed_date})·배당순위 정밀 계산 필요"
    )
    return TenantVerdict(
        t, True, 0,
        "선순위 + 배당요구 → 원칙상 배당으로 회수(부족분은 인수 가능, 검토)",
    )


def analyze(prop: AuctionProperty) -> AnalysisResult:
    """경매 물건 1건 권리분석 메인 진입점."""
    result = AnalysisResult(case_no=prop.case_no)

    # 1) 말소기준권리
    baseline = find_baseline(prop.rights)
    result.baseline_right = baseline
    if baseline is None:
        result.warnings.append(
            "말소기준권리를 찾지 못함 → 등기부 데이터 확인 및 전문가 검토 필요"
        )
        result.needs_expert_review = True

    # 2) 등기 권리 인수/소멸 (등기일 오름차순 정렬해서 출력)
    for r in sorted(prop.rights, key=lambda x: x.reg_date):
        v = _judge_right(r, baseline)
        result.right_verdicts.append(v)
        if v.status == "인수":
            result.assumed_amount_total += r.amount
        if r.type in HIGH_RISK_TYPES:
            result.needs_expert_review = True
            result.warnings.append(
                f"{r.type.value}({r.reg_date}) 존재 → 특수물건, 전문가 검토 필요"
            )

    # 3) 임차인 분석
    for t in prop.tenants:
        tv = _judge_tenant(t, baseline, result)
        result.tenant_verdicts.append(tv)
        result.assumed_amount_total += tv.buyer_assumes_deposit
        if tv.has_opposing_power:
            result.needs_expert_review = True  # 대항력 임차인은 항상 검토 권장

    # 4) 위험도 종합
    result.risk_level = _risk_level(result)
    return result


def _risk_level(result: AnalysisResult) -> str:
    """인수액·인수권리 유무로 위험도 산정."""
    has_assumed_right = any(v.status == "인수" for v in result.right_verdicts)
    has_assumed_deposit = any(tv.buyer_assumes_deposit > 0 for tv in result.tenant_verdicts)

    if result.assumed_amount_total > 0 or has_assumed_right or has_assumed_deposit:
        return "위험"
    if result.needs_expert_review:
        return "주의"
    return "안전"
