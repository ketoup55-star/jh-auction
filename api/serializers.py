"""분석/배당/물건 객체 → JSON 직렬화 헬퍼."""

from __future__ import annotations

from auction_analysis import Listing, AnalysisResult, DistributionResult


def listing_summary(l: Listing) -> dict:
    """목록용 요약."""
    return {
        "case_no": l.case_no,
        "court": l.court,
        "type": l.residential_type.value if l.residential_type else l.raw_type,
        "address": l.address,
        "region": l.region.value,
        "appraisal_value": l.appraisal_value,
        "min_bid_price": l.min_bid_price,
        "discount_rate": (
            round(100 * (1 - l.min_bid_price / l.appraisal_value))
            if l.appraisal_value else 0
        ),
        "failed_count": l.failed_count,
        "bid_ratio": (
            round(100 * l.min_bid_price / l.appraisal_value)
            if l.appraisal_value else 0
        ),
        "sale_date": l.sale_date.isoformat() if l.sale_date else None,
        "status": l.status,
        "auction_type": l.auction_type,
        "building_area": l.building_area,
        "land_area": l.land_area,
        "view_count": l.view_count,
        "lat": l.lat, "lng": l.lng,
    }


# 물건통계 표시 순서(스피드옥션식)
_STATUS_ORDER = ["유찰", "재진행", "신건", "재매각", "변경",
                 "취하", "기각", "정지", "미진행", "잔금납부"]


def compute_stats(listings: list[Listing]) -> dict:
    """검색결과의 상태별·용도별 건수 집계."""
    from collections import Counter
    sc = Counter(l.status for l in listings)
    tc = Counter(l.residential_type.value for l in listings if l.residential_type)
    status_counts = {"전체": len(listings)}
    for s in _STATUS_ORDER:
        if sc.get(s):
            status_counts[s] = sc[s]
    return {
        "total": len(listings),
        "status_counts": status_counts,
        "type_counts": dict(tc),
    }


def analysis_to_dict(r: AnalysisResult) -> dict:
    return {
        "case_no": r.case_no,
        "risk_level": r.risk_level,
        "needs_expert_review": r.needs_expert_review,
        "baseline_right": (
            {"type": r.baseline_right.type.value,
             "reg_date": r.baseline_right.reg_date.isoformat(),
             "holder": r.baseline_right.holder}
            if r.baseline_right else None
        ),
        "rights": [
            {"type": v.right.type.value,
             "reg_date": v.right.reg_date.isoformat(),
             "holder": v.right.holder,
             "amount": v.right.amount,
             "status": v.status,
             "reason": v.reason}
            for v in r.right_verdicts
        ],
        "tenants": [
            {"name": tv.tenant.name,
             "has_opposing_power": tv.has_opposing_power,
             "buyer_assumes_deposit": tv.buyer_assumes_deposit,
             "reason": tv.reason}
            for tv in r.tenant_verdicts
        ],
        "assumed_amount_total": r.assumed_amount_total,
        "warnings": r.warnings,
    }


def distribution_to_dict(d: DistributionResult) -> dict:
    return {
        "fund": d.fund,
        "payouts": [
            {"stage": p.stage,
             "label": p.claim.label or p.claim.kind.value,
             "holder": p.claim.holder,
             "priority_date": p.claim.priority_date.isoformat() if p.claim.priority_date else None,
             "claim_amount": p.claim.amount,
             "paid": p.paid}
            for p in d.payouts
        ],
        "tenant_recoveries": [
            {"name": tr.tenant.name,
             "received": tr.received,
             "buyer_assumes": tr.buyer_assumes}
            for tr in d.tenant_recoveries
        ],
        "leftover": d.leftover,
        "buyer_assumed_total": d.buyer_assumed_total,
        "notes": d.notes,
    }
