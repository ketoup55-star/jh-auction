"""
CODEF 등기부등본 응답 → AuctionProperty 정규화 어댑터.

설계 원칙(정직성):
  - 검증 가능한 핵심부(권리종류 분류 / 날짜·금액 파싱)는 순수함수로 격리 → 단위 테스트 가능.
  - CODEF 특정 JSON 키에 의존하는 '평탄화(flatten)'는 한 곳에 모으고, 실제 응답으로
    필드명을 보정해야 하는 부분을 # TODO(verify) 로 명시한다.

권장 호출 옵션:
  codef.py 의 register 호출 시 registerSummaryYN="1" 로 바꾸면
  PDF 텍스트 파싱 대신 구조화된 '등기사항요약' 데이터를 받을 수 있다.
  (현재 호스텔 app/codef.py 는 registerSummaryYN="0" → PDF만 받음)
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from .models import Right, RightType, AuctionProperty


# ──────────────────────────────────────────────────────────────
# 1) 등기목적 텍스트 → 권리종류 분류  (순수함수 · 완전 테스트 가능)
# ──────────────────────────────────────────────────────────────

# 검사 순서가 중요하다. 더 구체적인 키워드를 먼저 둔다.
#   예) '가압류'를 '압류'보다 먼저, '담보가등기'를 '가등기'보다 먼저,
#       '근저당'을 '저당'보다 먼저.
_RIGHT_KEYWORDS: list[tuple[str, RightType]] = [
    ("경매개시결정", RightType.AUCTION_START),
    ("강제경매개시", RightType.AUCTION_START),
    ("임의경매개시", RightType.AUCTION_START),
    ("근저당권", RightType.MORTGAGE),
    ("저당권", RightType.MORTGAGE),
    ("전세권", RightType.JEONSE),
    ("가압류", RightType.PROV_SEIZURE),
    ("압류", RightType.SEIZURE),
    ("담보가등기", RightType.SECURITY_PROV_REG),
    ("소유권이전청구권가등기", RightType.OWNERSHIP_PROV_REG),
    ("소유권이전담보가등기", RightType.SECURITY_PROV_REG),
    ("가등기", RightType.OWNERSHIP_PROV_REG),  # 종류 불명 가등기는 보전가등기로 보수적 처리
    ("가처분", RightType.INJUNCTION),
    ("지상권", RightType.SURFACE),
    ("지역권", RightType.EASEMENT),
    ("임차권", RightType.REGISTERED_LEASE),
    ("환매", RightType.REPURCHASE),
    # 소유권 변동(정보 표시용) — 가등기/담보가등기 키워드 뒤에 두어 오분류 방지
    ("소유권이전", RightType.OWNERSHIP_TRANSFER),
    ("소유권보존", RightType.OWNERSHIP_TRANSFER),
]


def classify_right_type(purpose_text: str) -> Optional[RightType]:
    """등기목적 텍스트 → RightType. 분류 불가면 None.

    소유권이전/보존은 인수/소멸 대상은 아니지만 현 소유관계 표시를 위해
    OWNERSHIP_TRANSFER 로 분류한다(엔진에서 '소유권'으로 별도 판정).
    """
    if not purpose_text:
        return None
    text = purpose_text.replace(" ", "")
    for kw, rtype in _RIGHT_KEYWORDS:
        if kw in text:
            return rtype
    return None


# ──────────────────────────────────────────────────────────────
# 2) 날짜 / 금액 파싱  (순수함수 · 완전 테스트 가능)
# ──────────────────────────────────────────────────────────────

def parse_date_kr(text: str) -> Optional[date]:
    """등기부 날짜 표기 → date.

    지원: '2021년 3월 10일', '2021.03.10', '2021-03-10', '20210310'
    접수란('2021년3월10일 제12345호')처럼 뒤에 접수번호가 붙어도 첫 날짜만 취한다.
    """
    if not text:
        return None
    t = text.strip()

    m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", t)
    if m:
        return _safe_date(m.group(1), m.group(2), m.group(3))

    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", t)
    if m:
        return _safe_date(m.group(1), m.group(2), m.group(3))

    m = re.search(r"\b(\d{4})(\d{2})(\d{2})\b", t)
    if m:
        return _safe_date(m.group(1), m.group(2), m.group(3))

    return None


def _safe_date(y, mo, d) -> Optional[date]:
    try:
        return date(int(y), int(mo), int(d))
    except (ValueError, TypeError):
        return None


def parse_amount_kr(text: str) -> int:
    """'금500,000,000원', '채권최고액 금 1억2,000만원' 등 → 정수(원).

    '억'·'만' 단위 한글 표기도 처리. 숫자가 없으면 0.

    주의: 화폐 단서('원'/'억'/'만')가 전혀 없으면 0을 반환한다.
    이렇게 하지 않으면 본문 폴백 시 날짜('2020년')·접수번호('제11111호')의
    숫자를 금액으로 오인한다(실제 발견된 버그).
    """
    if not text:
        return 0
    t = text.replace(",", "").replace(" ", "")

    # 화폐 단서가 없으면 금액 아님 → 0
    if not any(cue in t for cue in ("원", "억", "만")):
        return 0

    # 한글 단위(억/만) 표기 처리: 예) 1억2000만 → 120000000
    han = re.search(r"(?:(\d+)억)?(?:(\d+)만)?(?:(\d+)원)?", t)
    if han and (han.group(1) or han.group(2)):
        eok = int(han.group(1) or 0) * 100_000_000
        man = int(han.group(2) or 0) * 10_000
        won = int(han.group(3) or 0)
        total = eok + man + won
        if total:
            return total

    # 순수 숫자 + '원' 표기: '원' 바로 앞의 숫자만 취한다(접수번호 등 오인 방지)
    m = re.search(r"(\d{4,})\s*원", t) or re.search(r"금\s*(\d{4,})", t)
    return int(m.group(1)) if m else 0


# ──────────────────────────────────────────────────────────────
# 3) 정규화 엔트리 → Right  (구조 확정 · 테스트 가능)
#    정규화 엔트리는 CODEF든 PDF든 출처와 무관한 중립 포맷이다.
# ──────────────────────────────────────────────────────────────
# 정규화 엔트리 형식:
#   {
#     "section": "갑구" | "을구",
#     "purpose": "근저당권설정",       # 등기목적
#     "date_text": "2021년 3월 10일",  # 접수일자
#     "holder": "○○은행",
#     "amount_text": "금500,000,000원",
#     "cancelled": False,              # 말소(주말)된 권리인지
#   }

def entries_to_rights(entries: list[dict[str, Any]]) -> list[Right]:
    """정규화 엔트리 목록 → Right 목록. 말소된 권리·분류불가는 제외."""
    rights: list[Right] = []
    for e in entries:
        if e.get("cancelled"):
            continue  # 이미 말소된 권리는 현재 권리관계에서 제외
        rtype = classify_right_type(e.get("purpose", ""))
        if rtype is None:
            continue
        reg_date = parse_date_kr(e.get("date_text", ""))
        if reg_date is None:
            # 날짜 없으면 순위 판정 불가 → 건너뛰되 호출측에서 경고하도록 표시 가능
            continue
        rights.append(
            Right(
                type=rtype,
                reg_date=reg_date,
                holder=(e.get("holder") or "").strip(),
                amount=parse_amount_kr(e.get("amount_text", "")),
                note=(e.get("note") or "").strip(),
            )
        )
    return rights


# ──────────────────────────────────────────────────────────────
# 4) CODEF 응답 평탄화  (★ 실제 응답으로 키 보정 필요)
# ──────────────────────────────────────────────────────────────

def codef_summary_to_entries(codef_data: dict[str, Any]) -> list[dict[str, Any]]:
    """CODEF '등기사항요약' 응답(data) → 정규화 엔트리 목록.

    CODEF 요약 응답은 대략 다음 형태로 알려져 있다(등급/버전에 따라 키가 다를 수 있음):
        data["resRegisterEntriesList"][*]["resRegistrationHisList"][*]
            ["resType"]          == "갑구"/"을구"/"표제부"
            ["resContentsList"][*]
                ["resNumber"]    순위번호
                ["resPurpose"]   등기목적
                ["resContents"]  내용(접수일자·권리자·금액이 섞여 옴)
                ["resDetailList"][*] {"resType":..., "resContents":...}

    실제 응답을 한 번 받아 보고 아래 # TODO(verify) 키들을 확정해야 한다.
    구조가 달라도 견고하도록 .get() + 다중 키 후보로 방어한다.
    """
    entries: list[dict[str, Any]] = []

    blocks = (
        codef_data.get("resRegisterEntriesList")
        or codef_data.get("resRegisterList")
        or []
    )
    for block in blocks:
        his_list = (
            block.get("resRegistrationHisList")
            or block.get("resHisList")
            or []
        )
        for his in his_list:
            section = his.get("resType") or his.get("resSection") or ""
            if ("갑구" not in section) and ("을구" not in section):
                continue  # 표제부 등은 권리분석 대상 아님
            section_norm = "갑구" if "갑구" in section else "을구"

            for item in his.get("resContentsList") or his.get("resList") or []:
                purpose = item.get("resPurpose") or item.get("resType2") or ""
                # 접수일자·권리자·금액은 상세목록 또는 본문에 섞여 옴 → 한 덩어리로 모아 파싱
                blob = _collect_text(item)
                entries.append(
                    {
                        "section": section_norm,
                        "purpose": purpose,
                        "date_text": _pick(item, ("접수", "접수일자", "resReceipt"), blob),
                        "holder": _pick(item, ("권리자", "등기명의인", "resHolder"), blob),
                        "amount_text": _pick(
                            item, ("채권최고액", "금액", "resAmount"), blob
                        ),
                        "cancelled": _is_cancelled(item, blob),
                    }
                )
    return entries


def _collect_text(item: dict[str, Any]) -> str:
    """item 안의 모든 문자열을 한 덩어리로 합친다(파싱 폴백용)."""
    parts: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(item)
    return " ".join(parts)


def _pick(item: dict[str, Any], labels: tuple[str, ...], blob: str) -> str:
    """상세목록(resDetailList)에서 label 매칭 값을 찾고, 없으면 blob 전체를 돌려준다.

    blob 전체를 돌려주면 parse_date_kr / parse_amount_kr 가 알아서 첫 매칭만 취한다.
    """
    detail = item.get("resDetailList") or item.get("resDetails") or []
    for d in detail:
        key = str(d.get("resType") or d.get("resKey") or "")
        if any(lb in key for lb in labels):
            val = d.get("resContents") or d.get("resValue") or ""
            if val:
                return str(val)
    return blob


def _is_cancelled(item: dict[str, Any], blob: str) -> bool:
    """말소(주말) 여부. 등기부는 말소된 권리에 밑줄/말소 표시가 붙는다."""
    flag = item.get("resMScelChk") or item.get("resCancel") or ""
    if str(flag) in ("1", "Y", "true", "True"):
        return True
    return "말소" in blob and "말소기준" not in blob


# ──────────────────────────────────────────────────────────────
# 5) 최종 조립: CODEF 응답 → AuctionProperty
# ──────────────────────────────────────────────────────────────

def register_to_property(
    codef_data: dict[str, Any],
    *,
    case_no: str,
    court: str = "",
    property_type: str = "",
    address: str = "",
    appraisal_value: int = 0,
    min_bid_price: int = 0,
    tenants: Optional[list] = None,
) -> AuctionProperty:
    """CODEF 등기부 응답(data) + 경매 메타정보 → 분석 입력(AuctionProperty).

    임차인(tenants)은 등기부가 아니라 매각물건명세서/현황조사서에서 오므로
    별도로 받아 합친다.
    """
    entries = codef_summary_to_entries(codef_data)
    rights = entries_to_rights(entries)
    return AuctionProperty(
        case_no=case_no,
        court=court,
        property_type=property_type,
        address=address,
        appraisal_value=appraisal_value,
        min_bid_price=min_bid_price,
        rights=rights,
        tenants=tenants or [],
    )
