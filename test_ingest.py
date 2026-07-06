"""
적재 파이프라인 검증 테스트.

    python test_ingest.py

MockSource(5종 4건 + 다가구 선순위가등기 1건 + 오피스텔 1건) 적재 시
  - 오피스텔 → 5종 외 제외
  - 다가구 선순위가등기 → 수집정책 제외
  - 나머지 4건만 적재
검색 필터도 함께 확인한다.
"""

import sys
import tempfile, os

sys.stdout.reconfigure(encoding="utf-8")

from auction_analysis import (
    MockSource, ListingStore, ingest,
    ResidentialType, Region, classify_residential,
)


def check(label, got, expected):
    ok = got == expected
    print(("  ✅ " if ok else "  ❌ ") + f"{label}: {got!r}"
          + ("" if ok else f"  (기대값 {expected!r})"))
    return ok


def test_classify():
    print("[I-0] 5종 주거 분류")
    ok = True
    ok &= check("아파트", classify_residential("아파트"), ResidentialType.APARTMENT)
    ok &= check("다세대주택→빌라", classify_residential("다세대주택"), ResidentialType.VILLA)
    ok &= check("연립주택→빌라", classify_residential("연립주택"), ResidentialType.VILLA)
    ok &= check("도시형생활주택", classify_residential("도시형생활주택"), ResidentialType.URBAN_LIVING)
    ok &= check("상가주택", classify_residential("상가주택"), ResidentialType.COMMERCIAL_HOUSE)
    ok &= check("다가구주택", classify_residential("다가구주택"), ResidentialType.MULTIPLEX)
    ok &= check("오피스텔→제외", classify_residential("오피스텔"), None)
    ok &= check("근린상가→제외", classify_residential("근린생활시설"), None)
    return ok


def test_pipeline():
    print("[I-1] 적재 파이프라인")
    db = os.path.join(tempfile.mkdtemp(), "test_auction.db")
    store = ListingStore(db)
    report = ingest(MockSource(), store)
    print(report.summary())

    ok = True
    ok &= check("수집 시도", report.fetched, 6)
    ok &= check("적재 완료(5종-위험제외)", report.stored, 4)
    ok &= check("오피스텔 5종외 제외", "2026타경1006" in report.dropped_not_residential, True)
    ok &= check("선순위가등기 정책제외",
                report.dropped_by_policy[0][0], "2026타경1005")
    ok &= check("DB 적재 수", store.count(), 4)

    print("[I-2] 검색 필터")
    seoul = store.search(region=Region.SEOUL)
    ok &= check("서울 검색", len(seoul), 1)
    villas = store.search(types=[ResidentialType.VILLA])
    ok &= check("빌라 검색", len(villas), 1)
    cheap = store.search(max_price=300_000_000)
    ok &= check("3억 이하 검색", len(cheap), 2)  # 빌라(2.1억)+도시형(1.26억)

    store.close()
    return ok


def main():
    results = [test_classify(), test_pipeline()]
    print("-" * 70)
    print(("🎉 전체 통과" if all(results) else "⚠️ 일부 실패")
          + f"  ({sum(results)}/{len(results)})")


if __name__ == "__main__":
    main()
