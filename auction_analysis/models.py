"""
권리분석 입출력 데이터 모델.

원천 데이터(등기부등본, 매각물건명세서, 현황조사서)를 이 모델로 정규화한 뒤
engine.analyze() 에 넣으면 분석 결과(AnalysisResult)가 나온다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class RightType(str, Enum):
    """등기부에 등장하는 권리 종류."""

    # --- 말소기준권리 후보 (금전채권성 권리 + 경매개시) ---
    MORTGAGE = "근저당권"          # (근)저당권
    SEIZURE = "압류"               # 압류
    PROV_SEIZURE = "가압류"        # 가압류
    SECURITY_PROV_REG = "담보가등기"  # 담보가등기
    AUCTION_START = "경매개시결정"  # 경매개시결정등기

    # --- 선순위면 인수, 후순위면 소멸하는 용익/보전 권리 ---
    JEONSE = "전세권"              # 전세권 (조건부 말소기준 후보)
    SURFACE = "지상권"            # 지상권
    EASEMENT = "지역권"           # 지역권
    REGISTERED_LEASE = "임차권"   # 등기된 임차권
    OWNERSHIP_PROV_REG = "소유권이전청구권가등기"  # 보전가등기 (선순위면 매우 위험)
    INJUNCTION = "가처분"         # 가처분
    REPURCHASE = "환매등기"       # 환매등기

    # --- 소유권 변동(정보 표시용, 인수/소멸·말소기준 대상 아님) ---
    OWNERSHIP_TRANSFER = "소유권이전"  # 소유권이전/보존 — 현 소유관계 표시


# 말소기준권리가 될 수 있는 권리 종류
BASELINE_CANDIDATES = {
    RightType.MORTGAGE,
    RightType.SEIZURE,
    RightType.PROV_SEIZURE,
    RightType.SECURITY_PROV_REG,
    RightType.AUCTION_START,
}

# 말소기준권리보다 '선순위'일 때 낙찰자가 인수하는 권리 종류
SURVIVOR_IF_SENIOR = {
    RightType.JEONSE,
    RightType.SURFACE,
    RightType.EASEMENT,
    RightType.REGISTERED_LEASE,
    RightType.OWNERSHIP_PROV_REG,
    RightType.INJUNCTION,
    RightType.REPURCHASE,
}

# 자동분석이 위험한, 사람 검토가 필요한 권리 (특수물건 신호)
HIGH_RISK_TYPES = {
    RightType.OWNERSHIP_PROV_REG,
    RightType.INJUNCTION,
}


@dataclass
class Right:
    """등기부상의 권리 1건 (갑구/을구 항목)."""

    type: RightType
    reg_date: date                 # 등기 접수일 (순위 판정 기준)
    holder: str = ""               # 권리자
    amount: int = 0                # 채권최고액/청구액 (원). 배당 계산용
    note: str = ""                 # 비고 (예: '건물철거 목적 가처분')

    # 전세권 전용: 배당요구 또는 경매신청을 했는지 (말소기준 후보 판정용)
    jeonse_demanded_distribution: bool = False
    jeonse_covers_whole: bool = True  # 건물 전부에 설정됐는지


@dataclass
class Tenant:
    """임차인 1명 (현황조사서 / 전입세대열람 / 매각물건명세서 기반)."""

    name: str = ""
    move_in_date: Optional[date] = None      # 전입신고일 (대항력 기준)
    fixed_date: Optional[date] = None         # 확정일자 (우선변제 순위)
    deposit: int = 0                          # 보증금 (원)
    rent: int = 0                             # 차임(월세, 원) — 매각물건명세서 임차인 표 '차임' 컬럼
    demanded_distribution: bool = False        # 배당요구 종기 내 '유효' 배당요구 여부
    demand_date: Optional[date] = None         # 배당요구 신청일(있으면). 종기 후면 무효지만 신청사실 보존
    occupying: bool = True                     # 실제 점유 여부

    @property
    def opposing_power_date(self) -> Optional[date]:
        """대항력 발생일 = 전입신고 다음날 0시 (익일 기준)."""
        if self.move_in_date is None:
            return None
        from datetime import timedelta
        return self.move_in_date + timedelta(days=1)


@dataclass
class AuctionProperty:
    """경매 물건 1건 + 분석 입력 전체."""

    case_no: str                              # 사건번호 (예: 2024타경12345)
    court: str = ""                           # 관할법원
    property_type: str = ""                   # 아파트/빌라/도시형생활주택/상가주택/다가구주택
    address: str = ""
    appraisal_value: int = 0                  # 감정가 (원)
    min_bid_price: int = 0                    # 최저매각가격 (원)

    rights: list[Right] = field(default_factory=list)
    tenants: list[Tenant] = field(default_factory=list)


# ---------------- 출력 모델 ----------------

@dataclass
class RightVerdict:
    """권리 1건에 대한 인수/소멸 판정 결과."""

    right: Right
    status: str          # "소멸" | "인수" | "말소기준권리"
    reason: str


@dataclass
class TenantVerdict:
    """임차인 1명에 대한 분석 결과."""

    tenant: Tenant
    has_opposing_power: bool          # 대항력 유무
    buyer_assumes_deposit: int        # 낙찰자가 인수할 보증금 (원)
    reason: str


@dataclass
class AnalysisResult:
    """권리분석 최종 결과."""

    case_no: str
    baseline_right: Optional[Right] = None     # 말소기준권리
    right_verdicts: list[RightVerdict] = field(default_factory=list)
    tenant_verdicts: list[TenantVerdict] = field(default_factory=list)

    assumed_amount_total: int = 0              # 낙찰자 총 추가 인수 예상액 (원)
    risk_level: str = "안전"                   # 안전 | 주의 | 위험
    needs_expert_review: bool = False           # 특수물건 → 전문가 검토 필요
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """사람이 읽는 요약 텍스트."""
        lines = [
            f"[{self.case_no}] 권리분석 결과",
            f"  위험도: {self.risk_level}"
            + ("  ⚠️ 전문가 검토 필요" if self.needs_expert_review else ""),
        ]
        if self.baseline_right:
            b = self.baseline_right
            lines.append(
                f"  말소기준권리: {b.type.value} ({b.reg_date}) / {b.holder}"
            )
        lines.append("  [등기 권리 판정]")
        for v in self.right_verdicts:
            r = v.right
            lines.append(
                f"    - {r.reg_date} {r.type.value:<14} {v.status:<8} : {v.reason}"
            )
        if self.tenant_verdicts:
            lines.append("  [임차인 판정]")
            for tv in self.tenant_verdicts:
                t = tv.tenant
                tag = "대항력O" if tv.has_opposing_power else "대항력X"
                lines.append(
                    f"    - {t.name or '임차인'} {tag} / "
                    f"인수보증금 {tv.buyer_assumes_deposit:,}원 : {tv.reason}"
                )
        lines.append(f"  낙찰자 총 추가 인수 예상액: {self.assumed_amount_total:,}원")
        if self.warnings:
            lines.append("  [경고]")
            for w in self.warnings:
                lines.append(f"    ⚠️ {w}")
        return "\n".join(lines)
