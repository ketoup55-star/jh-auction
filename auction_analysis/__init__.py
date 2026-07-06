"""
경매 권리분석 엔진 (auction_analysis)

법원경매 물건의 등기부 권리 + 임차인 정보를 입력받아
말소기준권리 판정 → 인수/소멸 판정 → 임차인 대항력/배당 분석을 수행한다.

- 입력 데이터는 모두 원천(법원경매정보 / CODEF 등기부)에서 합법적으로 확보한다.
- 일반물건은 자동 분석 100%, 특수물건은 '전문가 검토 필요' 경고를 띄운다.
"""

from .models import (
    RightType,
    Right,
    Tenant,
    AuctionProperty,
    AnalysisResult,
)
from .engine import analyze
from .codef_adapter import (
    classify_right_type,
    parse_date_kr,
    parse_amount_kr,
    entries_to_rights,
    codef_summary_to_entries,
    register_to_property,
)
from .collection_policy import (
    has_senior_ownership_provisional,
    screen,
    is_collectible,
    filter_collectible,
)
from .distribution import (
    Region,
    Claim,
    ClaimKind,
    DistributionResult,
    calculate_distribution,
    small_tenant_bracket,
    build_claims,
)
from .listing import ResidentialType, Listing, classify_residential
from .sources import AuctionSource, MockSource
from .store import ListingStore
from .ingest import ingest, IngestReport

__all__ = [
    "RightType",
    "Right",
    "Tenant",
    "AuctionProperty",
    "AnalysisResult",
    "analyze",
    "classify_right_type",
    "parse_date_kr",
    "parse_amount_kr",
    "entries_to_rights",
    "codef_summary_to_entries",
    "register_to_property",
    "has_senior_ownership_provisional",
    "screen",
    "is_collectible",
    "filter_collectible",
    "Region",
    "Claim",
    "ClaimKind",
    "DistributionResult",
    "calculate_distribution",
    "small_tenant_bracket",
    "build_claims",
    "ResidentialType",
    "Listing",
    "classify_residential",
    "AuctionSource",
    "MockSource",
    "ListingStore",
    "ingest",
    "IngestReport",
]
