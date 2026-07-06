"""
적재 파이프라인.

소스 → 5종 주거 분류 → (5종 아니면 제외) → 수집정책(선순위 가등기 등 제외)
     → SQLite 저장.

결과로 적재/제외 통계를 돌려준다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .sources import AuctionSource
from .listing import Listing, classify_residential
from .collection_policy import screen
from .store import ListingStore


@dataclass
class IngestReport:
    fetched: int = 0
    stored: int = 0
    dropped_not_residential: list[str] = field(default_factory=list)  # 5종 아님
    dropped_by_policy: list[tuple[str, list[str]]] = field(default_factory=list)  # 수집정책 제외

    def summary(self) -> str:
        lines = [
            f"수집 시도: {self.fetched}건",
            f"적재 완료: {self.stored}건",
            f"5종 외 제외: {len(self.dropped_not_residential)}건 {self.dropped_not_residential}",
            f"정책 제외: {len(self.dropped_by_policy)}건",
        ]
        for case_no, reasons in self.dropped_by_policy:
            lines.append(f"  - {case_no}: {reasons}")
        return "\n".join(lines)


def ingest(source: AuctionSource, store: ListingStore) -> IngestReport:
    report = IngestReport()
    for listing in source.fetch():
        report.fetched += 1

        # 1) 5종 주거 분류 (이미 채워져 있어도 원본 용도로 재분류해 일관성 보장)
        rtype = classify_residential(listing.raw_type)
        if rtype is None:
            report.dropped_not_residential.append(listing.case_no)
            continue
        listing.residential_type = rtype

        # 2) 수집 정책 (선순위 가등기 등 위험 특수물건 제외)
        reasons = screen(listing.to_property())
        if reasons:
            report.dropped_by_policy.append((listing.case_no, reasons))
            continue

        # 3) 적재
        store.upsert(listing)
        report.stored += 1

    return report
