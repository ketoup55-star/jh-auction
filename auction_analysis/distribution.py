"""
배당표 정밀 계산 (distribution).

매각대금을 법정 배당순위에 따라 채권자·임차인에게 분배하고,
대항력 임차인의 '미배당 잔여보증금 → 낙찰자 인수액'을 정확히 산출한다.

배당 순위(간소화된 실무 모델)
  0) 경매비용(집행비용)          : 매각대금에서 먼저 공제
  1) 소액임차인 최우선변제        : 매각대금(공제후)의 1/2 한도, 부족 시 안분
                                    ※ 소액 여부는 '최선순위 담보물권 설정일' 기준
                                       지역별 시행령 표로 판정 (배당시점 아님!)
  2) 우선변제 순위배당            : (근)저당·전세권·확정일자부 임차보증금·
                                    법정기일 조세 등을 기준일 순서로 배당
  3) 안분배당                    : 가압류·일반채권을 잔여재단에서 채권액 비례 분배

미구현(주입으로 처리): 당해세 우선, 임금채권 최우선, 4대보험, 안분후흡수.
  → 필요한 조세·임금채권은 Claim 으로 직접 넣으면 순위에 반영된다.

⚠️ 소액임차인 표(LAW_SMALL_TENANT)는 주택임대차보호법 시행령 기준이며
   개정으로 바뀐다. 반드시 법제처 현행 시행령과 대조해 유지보수할 것.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

from .models import AuctionProperty, Right, RightType, Tenant
from .engine import find_baseline


# ──────────────────────────────────────────────────────────────
# 지역 구분 + 소액임차인 시행령 표
# ──────────────────────────────────────────────────────────────

class Region(str, Enum):
    """주택임대차보호법 소액임차인 지역 구분(간소화 4분류)."""
    SEOUL = "서울특별시"
    OVERCONCENTRATION = "과밀억제권역등"   # 수도권 과밀억제권역 + 세종·용인·화성·김포
    METRO = "광역시등"                    # 광역시(군 제외) + 안산·광주·파주·이천·평택
    OTHER = "그밖의지역"


# 시행령 개정 이력: (시행일, {지역: (소액보증금_상한, 최우선변제_상한)})
#   소액 판정은 '최선순위 담보물권 설정일'이 속하는 구간의 표로 한다.
#   - 2023-02-21 구간: 법제처/생활법령 대조 검증 완료 (2026-06 확인).
#   - 2021-05-11, 2018-09-18 구간: 값 재확인 권장.  # TODO(verify-law-history)
LAW_SMALL_TENANT: list[tuple[date, dict[Region, tuple[int, int]]]] = [
    (date(2023, 2, 21), {   # ✓ 검증 완료
        Region.SEOUL:             (165_000_000, 55_000_000),
        Region.OVERCONCENTRATION: (145_000_000, 48_000_000),
        Region.METRO:             (85_000_000, 28_000_000),
        Region.OTHER:             (75_000_000, 25_000_000),
    }),
    (date(2021, 5, 11), {
        Region.SEOUL:             (150_000_000, 50_000_000),
        Region.OVERCONCENTRATION: (130_000_000, 43_000_000),
        Region.METRO:             (70_000_000, 23_000_000),
        Region.OTHER:             (60_000_000, 20_000_000),
    }),
    (date(2018, 9, 18), {
        Region.SEOUL:             (110_000_000, 37_000_000),
        Region.OVERCONCENTRATION: (100_000_000, 34_000_000),
        Region.METRO:             (60_000_000, 20_000_000),
        Region.OTHER:             (50_000_000, 17_000_000),
    }),
]


def small_tenant_bracket(baseline_date: Optional[date], region: Region) -> tuple[int, int]:
    """기준일(최선순위 담보물권 설정일)·지역 → (소액보증금 상한, 최우선변제 상한).

    기준일이 가장 오래된 구간보다 앞서거나 None이면 가장 오래된 표를 사용한다.
    """
    if baseline_date is None:
        eff, table = LAW_SMALL_TENANT[-1]
        return table[region]
    for eff, table in LAW_SMALL_TENANT:           # 최신 → 과거 순
        if baseline_date >= eff:
            return table[region]
    return LAW_SMALL_TENANT[-1][1][region]         # 모든 구간보다 과거


# ──────────────────────────────────────────────────────────────
# 배당 입력 모델
# ──────────────────────────────────────────────────────────────

class ClaimKind(str, Enum):
    PRIORITY = "우선변제"   # 저당·전세·확정일자임차·법정기일조세 → 기준일 순서
    GENERAL = "안분"        # 가압류·일반채권 → 채권액 비례


@dataclass
class Claim:
    """배당에 참가하는 채권 1건."""
    kind: ClaimKind
    amount: int                       # 청구(채권)액
    holder: str = ""
    priority_date: Optional[date] = None  # 우선변제 기준일(PRIORITY일 때 필수)
    tenant: Optional[Tenant] = None       # 임차인 채권이면 연결(인수액 산정용)
    label: str = ""                       # 표시용(예: '근저당', '임차보증금')


@dataclass
class ClaimPayout:
    claim: Claim
    paid: int
    stage: str            # "소액최우선" | "우선변제" | "안분"


@dataclass
class TenantRecovery:
    tenant: Tenant
    received: int             # 배당으로 회수한 금액
    buyer_assumes: int        # 낙찰자가 인수할 잔여보증금


@dataclass
class DistributionResult:
    fund: int                            # 배당재단(매각대금 - 경매비용)
    payouts: list[ClaimPayout] = field(default_factory=list)
    tenant_recoveries: list[TenantRecovery] = field(default_factory=list)
    leftover: int = 0                    # 배당 후 잔여(소유자/후순위에 귀속)
    buyer_assumed_total: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"배당재단: {self.fund:,}원"]
        for p in self.payouts:
            c = p.claim
            d = f" {c.priority_date}" if c.priority_date else ""
            lines.append(
                f"  [{p.stage}] {c.label or c.kind.value}{d} {c.holder} "
                f"→ {p.paid:,}원 / 청구 {c.amount:,}원"
            )
        if self.tenant_recoveries:
            lines.append("  [임차인 회수]")
            for tr in self.tenant_recoveries:
                lines.append(
                    f"    - {tr.tenant.name or '임차인'}: 회수 {tr.received:,}원, "
                    f"낙찰자 인수 {tr.buyer_assumes:,}원"
                )
        lines.append(f"  배당 후 잔여: {self.leftover:,}원")
        lines.append(f"  낙찰자 총 인수액: {self.buyer_assumed_total:,}원")
        for n in self.notes:
            lines.append(f"  ※ {n}")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# AuctionProperty → Claim 목록 변환
# ──────────────────────────────────────────────────────────────

# 우선변제(기준일 순) 대상이 되는 등기 권리
_PRIORITY_RIGHTS = {RightType.MORTGAGE, RightType.JEONSE}
# 안분배당 대상이 되는 등기 권리
_GENERAL_RIGHTS = {RightType.PROV_SEIZURE, RightType.SEIZURE}


def build_claims(prop: AuctionProperty) -> list[Claim]:
    """물건의 등기권리 + 임차인 → 배당 참가 채권 목록.

    임차인의 소액 최우선분은 별도 단계에서 계산하므로 여기서는
    임차인을 '우선변제(확정일자 있을 때)' 채권으로만 등록한다.
    """
    claims: list[Claim] = []

    for r in prop.rights:
        if r.type in _PRIORITY_RIGHTS:
            claims.append(Claim(
                ClaimKind.PRIORITY, r.amount, r.holder,
                priority_date=r.reg_date, label=r.type.value,
            ))
        elif r.type in _GENERAL_RIGHTS:
            claims.append(Claim(
                ClaimKind.GENERAL, r.amount, r.holder,
                label=r.type.value,
            ))

    for t in prop.tenants:
        if not t.demanded_distribution:
            continue  # 배당요구 안 한 임차인은 배당 미참가
        # 우선변제권 발생일 = max(대항요건 구비 익일, 확정일자). 확정일자 없으면 우선변제 없음.
        if t.fixed_date and t.opposing_power_date:
            pdate = max(t.fixed_date, t.opposing_power_date)
            claims.append(Claim(
                ClaimKind.PRIORITY, t.deposit, t.name,
                priority_date=pdate, tenant=t, label="임차보증금(확정일자)",
            ))
        else:
            # 확정일자 없으면 우선변제 채권은 없지만, 소액 최우선 대상일 수는 있음.
            # 소액 단계에서만 다루도록 tenant 참조만 남긴 0원 우선채권은 만들지 않는다.
            pass

    return claims


# ──────────────────────────────────────────────────────────────
# 배당 계산 메인
# ──────────────────────────────────────────────────────────────

def calculate_distribution(
    prop: AuctionProperty,
    *,
    sale_price: int,
    region: Region,
    execution_cost: int = 0,
    extra_claims: Optional[list[Claim]] = None,
) -> DistributionResult:
    """매각가 기준 배당표 계산.

    extra_claims: 당해세·임금채권·조세 등 등기부에 없는 채권을 직접 주입.
    """
    baseline = find_baseline(prop.rights)
    baseline_date = baseline.reg_date if baseline else None

    fund = max(0, sale_price - execution_cost)
    result = DistributionResult(fund=fund)
    remaining = fund

    claims = build_claims(prop)
    if extra_claims:
        claims.extend(extra_claims)

    # 임차인별 회수액 누적 추적
    received: dict[int, int] = {id(t): 0 for t in prop.tenants}

    # ── 1단계: 소액임차인 최우선변제 (매각가의 1/2 한도) ──
    max_deposit, max_priority = small_tenant_bracket(baseline_date, region)
    small_amounts: list[tuple[Tenant, int]] = []
    for t in prop.tenants:
        if not t.demanded_distribution:
            continue
        if t.opposing_power_date is None:
            continue  # 대항요건(전입+점유) 없으면 소액 최우선도 없음
        if t.deposit <= max_deposit:
            small_amounts.append((t, min(t.deposit, max_priority)))

    total_small = sum(a for _, a in small_amounts)
    cap_half = remaining // 2
    if small_amounts:
        if total_small <= cap_half:
            for t, amt in small_amounts:
                _pay_tenant(result, t, amt, "소액최우선")
                received[id(t)] += amt
                remaining -= amt
        else:
            # 1/2 한도 초과 → 최우선변제액 비율로 안분
            result.notes.append(
                f"소액 최우선 합계({total_small:,})가 매각대금 1/2({cap_half:,}) 초과 → 안분"
            )
            for t, amt in small_amounts:
                share = cap_half * amt // total_small if total_small else 0
                _pay_tenant(result, t, share, "소액최우선")
                received[id(t)] += share
                remaining -= share

    # ── 2단계: 우선변제 순위배당 (기준일 오름차순) ──
    priority_claims = [c for c in claims if c.kind == ClaimKind.PRIORITY]
    priority_claims.sort(key=lambda c: (c.priority_date or date.max))
    for c in priority_claims:
        if remaining <= 0:
            break
        # 임차인 우선채권은 이미 받은 소액분을 차감한 잔액만 청구
        claim_remaining = c.amount
        if c.tenant is not None:
            claim_remaining = max(0, c.amount - received.get(id(c.tenant), 0))
        pay = min(remaining, claim_remaining)
        if pay <= 0:
            continue
        result.payouts.append(ClaimPayout(c, pay, "우선변제"))
        if c.tenant is not None:
            received[id(c.tenant)] += pay
        remaining -= pay

    # ── 3단계: 안분배당 (잔여재단을 채권액 비례) ──
    general_claims = [c for c in claims if c.kind == ClaimKind.GENERAL]
    total_general = sum(c.amount for c in general_claims)
    if remaining > 0 and total_general > 0:
        for c in general_claims:
            share = min(c.amount, remaining * c.amount // total_general)
            result.payouts.append(ClaimPayout(c, share, "안분"))
            if c.tenant is not None:
                received[id(c.tenant)] += share
        # 안분은 동시 분배라 remaining은 한 번에 차감
        distributed = sum(p.paid for p in result.payouts if p.stage == "안분")
        remaining -= distributed

    result.leftover = max(0, remaining)

    # ── 임차인 회수/인수 정리 ──
    for t in prop.tenants:
        rec = received.get(id(t), 0)
        # 대항력 있는 임차인만 미배당 잔여보증금을 낙찰자가 인수
        senior = (
            t.opposing_power_date is not None
            and baseline is not None
            and t.opposing_power_date <= baseline.reg_date
        )
        assume = max(0, t.deposit - rec) if senior else 0
        result.tenant_recoveries.append(TenantRecovery(t, rec, assume))
        result.buyer_assumed_total += assume

    return result


def _pay_tenant(result: DistributionResult, t: Tenant, amt: int, stage: str) -> None:
    """임차인 소액 최우선 배당 기록(임차인 연결 Claim 생성)."""
    c = Claim(ClaimKind.PRIORITY, t.deposit, t.name, tenant=t, label="임차보증금(소액)")
    result.payouts.append(ClaimPayout(c, amt, stage))
