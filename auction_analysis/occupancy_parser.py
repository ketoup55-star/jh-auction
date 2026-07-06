"""
현황조사서(HTML) → 임차인(Tenant) 목록 + 점유 요약 파서.

스피드옥션 수집본 현황조사서는 PDF가 아니라 HTML 표다.
  · '부동산의 현황 및 점유관계 조사서' : 점유관계/기타(현장 메모)
  · '임대차관계조사서' : 점유인별 표 (점유인/당사자구분/전입일자/확정일자/보증금/차임…)

표는 <th>라벨</th><td>값</td> 쌍으로 되어 있고, 값이 없으면 빈 <td>가 온다.
따라서 라벨→직후 td 페어링 방식으로 파싱하고, '점유인' 라벨을 만나면 새 점유인
블록으로 분리한다. (라벨 순서·빈칸에 흔들리지 않도록 순차 페어링)
"""

from __future__ import annotations

import re
import html as _html
from datetime import date

from .models import Tenant
from .codef_adapter import parse_date_kr, parse_amount_kr

# 임대차관계조사서 점유인 블록의 라벨
_LABELS = {"점유인", "당사자구분", "점유부분", "용도", "점유기간",
           "보증(전세)금", "차임", "전입일자", "확정일자"}

# 셀 토큰: 여는 <th>/<td>부터 '다음 셀의 시작 또는 행/표 끝'까지를 한 셀로 본다.
# 수집본 HTML은 <th ...> 1 </td> 처럼 여닫음 태그가 불일치해 짝매칭(</\1>)이 깨지고,
# 반대로 메모 셀은 내부에 <br>이 있어 [^<]* 로는 잘린다. 둘 다 견디도록
# 닫는 태그가 아니라 '다음 셀 시작'을 경계로 삼는다(셀 내부 <br> 등은 _clean이 제거).
_CELL = re.compile(r"<(th|td)\b[^>]*>(.*?)(?=<(?:th|td)\b|</t(?:r|body|able))",
                   re.S | re.I)


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _section(text: str, start_kw: str, end_kw: str | None) -> str:
    i = text.find(start_kw)
    if i < 0:
        return ""
    j = text.find(end_kw, i) if end_kw else -1
    return text[i:j] if j > 0 else text[i:]


def parse_occupancy(html_text: str) -> dict:
    """현황조사서 HTML → {tenants:[Tenant], occupancy:str, notes:[str], raw_count:int}."""
    occ_block = _section(html_text, "부동산의 현황", "임대차관계조사서")
    lease_block = _section(html_text, "임대차관계조사서", "2. 기타") or \
        _section(html_text, "임대차관계조사서", None)

    # 1) 점유관계 / 기타 메모(현장 조사 코멘트)
    occupancy = ""
    notes: list[str] = []
    if occ_block:
        cells = [(_clean(t)) for _, t in _CELL.findall(occ_block)]
        for k in range(len(cells) - 1):
            if cells[k] == "점유관계" and cells[k + 1]:
                occupancy = cells[k + 1]
            if cells[k] == "기타" and cells[k + 1]:
                notes = [ln.strip() for ln in re.split(r"(?<=\.)\s+|①|②|③|④",
                                                       cells[k + 1]) if ln.strip()]

    # 2) 임대차관계조사서: 점유인별 블록 파싱(라벨→직후 td 순차 페어링)
    occupants: list[dict] = []
    cur: dict | None = None
    pending: str | None = None
    for tag, body in _CELL.findall(lease_block):
        text = _clean(body)
        if tag.lower() == "th":
            pending = text if text in _LABELS else None
        else:  # td
            if pending == "점유인":
                cur = {"점유인": text}
                occupants.append(cur)
            elif pending and cur is not None:
                cur[pending] = text
            pending = None

    # 3) Tenant 변환(점유인 이름이 있는 블록만)
    tenants: list[Tenant] = []
    for o in occupants:
        name = o.get("점유인", "").strip()
        if not name:
            continue
        tenants.append(Tenant(
            name=name,
            move_in_date=parse_date_kr(o.get("전입일자", "")),
            fixed_date=parse_date_kr(o.get("확정일자", "")),
            deposit=parse_amount_kr(o.get("보증(전세)금", "")),
            demanded_distribution=False,  # 현황조사서엔 배당요구 정보 없음(보수적 가정)
            occupying=True,
        ))

    raw_count = _lease_count(html_text)
    return {"tenants": tenants, "occupancy": occupancy,
            "notes": notes, "raw_count": raw_count}


def _lease_count(text: str) -> int:
    """'부동산 임대차정보'의 'N명' 표기 추출(참고용)."""
    seg = _section(text, "부동산 임대차정보", "부동산의 현황") or text
    m = re.search(r"(\d+)\s*명", seg)
    return int(m.group(1)) if m else 0
