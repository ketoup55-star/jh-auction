"""
수집 정책 (collection policy).

위험한 특수물건을 데이터 수집 단계에서 아예 제외하기 위한 규칙 모음.
물건을 DB에 적재하기 전에 screen()/is_collectible()로 거른다.

현재 제외 대상:
  - 선순위 소유권이전청구권가등기  (낙찰자가 소유권을 잃을 수 있는 최고위험 물건)

규칙은 (사유, 판정함수) 튜플의 리스트라, 유치권·법정지상권·지분경매 등
다른 제외 대상을 같은 틀에 계속 추가할 수 있다.
"""

from __future__ import annotations

from typing import Callable

from .models import AuctionProperty, RightType
from .engine import find_baseline


def has_senior_ownership_provisional(prop: AuctionProperty) -> bool:
    """선순위 소유권이전청구권가등기가 있으면 True.

    '선순위'란 말소기준권리보다 등기일이 빠른 것.
    담보가등기(SECURITY_PROV_REG)는 말소 대상이므로 여기서 제외하지 않는다.
    말소기준이 없으면(가등기만 존재 등) 보수적으로 선순위로 본다.
    """
    baseline = find_baseline(prop.rights)
    for r in prop.rights:
        if r.type != RightType.OWNERSHIP_PROV_REG:
            continue
        if baseline is None or r.reg_date < baseline.reg_date:
            return True
    return False


# (사유, 판정함수) — 판정함수가 True면 '제외'
EXCLUSION_RULES: list[tuple[str, Callable[[AuctionProperty], bool]]] = [
    ("선순위 가등기(소유권이전청구권) → 수집 제외", has_senior_ownership_provisional),
    # TODO: 추가 예정
    #   ("유치권 신고 → 수집 제외", has_lien),
    #   ("법정지상권 성립여지 → 수집 제외", has_statutory_surface_right),
    #   ("지분경매 → 수집 제외", is_share_auction),
]


def screen(prop: AuctionProperty) -> list[str]:
    """물건에 걸리는 제외 사유 목록을 반환. 비어있으면 수집 가능."""
    return [reason for reason, pred in EXCLUSION_RULES if pred(prop)]


def is_collectible(prop: AuctionProperty) -> bool:
    """수집 가능 여부. 제외 사유가 하나도 없어야 True."""
    return not screen(prop)


def filter_collectible(props: list[AuctionProperty]) -> tuple[list[AuctionProperty], list[tuple[AuctionProperty, list[str]]]]:
    """물건 목록을 (수집대상, 제외목록[(물건, 사유들)])으로 분리."""
    keep: list[AuctionProperty] = []
    dropped: list[tuple[AuctionProperty, list[str]]] = []
    for p in props:
        reasons = screen(p)
        if reasons:
            dropped.append((p, reasons))
        else:
            keep.append(p)
    return keep, dropped
