"""
경매 물건 카탈로그 모델 + 5종 주거 분류기.

대상 5종: 아파트 / 빌라(다세대·연립) / 도시형생활주택 / 상가주택 / 다가구주택.
그 외 용도(오피스텔·근린상가·토지 등)는 수집 대상에서 제외(None).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

from .models import Right, Tenant, AuctionProperty
from .distribution import Region


class ResidentialType(str, Enum):
    APARTMENT = "아파트"
    VILLA = "빌라"                  # 다세대·연립
    URBAN_LIVING = "도시형생활주택"
    COMMERCIAL_HOUSE = "상가주택"
    MULTIPLEX = "다가구주택"


# 원본 용도 텍스트 → 5종 분류. 더 구체적인 키워드를 먼저 검사한다.
_TYPE_KEYWORDS: list[tuple[tuple[str, ...], ResidentialType]] = [
    (("도시형생활주택", "도시형"), ResidentialType.URBAN_LIVING),
    (("상가주택", "점포주택", "근린주택"), ResidentialType.COMMERCIAL_HOUSE),
    (("다가구",), ResidentialType.MULTIPLEX),
    (("다세대", "빌라", "연립"), ResidentialType.VILLA),
    (("아파트",), ResidentialType.APARTMENT),
]


def classify_residential(raw_type: str) -> Optional[ResidentialType]:
    """원본 용도 문자열 → 5종 ResidentialType. 대상 외면 None(=수집 제외)."""
    if not raw_type:
        return None
    t = raw_type.replace(" ", "")
    for keywords, rtype in _TYPE_KEYWORDS:
        if any(k in t for k in keywords):
            return rtype
    return None


@dataclass
class Listing:
    """경매 물건 1건(카탈로그 + 분석 입력)."""

    case_no: str                      # 사건번호 (PK)
    court: str = ""
    raw_type: str = ""                # 원본 용도
    residential_type: Optional[ResidentialType] = None
    address: str = ""
    region: Region = Region.OTHER     # 배당 소액 판정용 지역
    appraisal_value: int = 0          # 감정가
    min_bid_price: int = 0            # 최저매각가격
    failed_count: int = 0             # 유찰 횟수
    sale_date: Optional[date] = None  # 매각기일
    status: str = "신건"             # 신건/유찰/재진행/재매각/변경/취하/기각/정지/잔금납부
    auction_type: str = "임의경매"   # 임의경매/강제경매
    building_area: float = 0.0        # 전용면적(㎡)
    land_area: float = 0.0            # 대지권/대지면적(㎡)
    view_count: int = 0               # 조회수
    lat: Optional[float] = None       # 지도 좌표
    lng: Optional[float] = None

    # 권리분석 입력(등기부/명세서에서 채움)
    rights: list[Right] = field(default_factory=list)
    tenants: list[Tenant] = field(default_factory=list)

    def to_property(self) -> AuctionProperty:
        return AuctionProperty(
            case_no=self.case_no,
            court=self.court,
            property_type=(self.residential_type.value if self.residential_type else self.raw_type),
            address=self.address,
            appraisal_value=self.appraisal_value,
            min_bid_price=self.min_bid_price,
            rights=self.rights,
            tenants=self.tenants,
        )
