"""
등기사항전부증명서(PDF 텍스트) → 권리 목록(list[Right]) 파서.

등기 구조:
  【 갑 구 】 소유권 관련 (소유권이전/가압류/압류/가처분/가등기/경매개시)
  【 을 구 】 소유권 이외 (근저당/전세권/지상권/지역권/임차권)
  각 항목: "순위번호 등기목적 접수(YYYY년MM월DD일 제N호) 등기원인 권리자및기타사항"

★ 순위번호는 갑구·을구에서 각각 독립적으로 매겨진다. 따라서 말소 처리도
  반드시 같은 구(區) 안에서만 적용해야 한다. (예: 갑구의 '5번압류등기말소'가
  을구 근저당 5번을 말소시키면 안 됨 — 실제로 발생한 버그)

말소 처리: "3 1번근저당권설정등기말소 …" 처럼 등기목적에 '말소'가 있으면
  같은 구의 참조 순위(1번)를 취소(cancelled)로 보고 해당 권리를 제외.
변경/이전/경정(1-1, 2-1 등) 부기등기는 새 권리가 아니므로 건너뜀.

전제: 텍스트 추출 가능한 등기 PDF(스캔 아님). codef_adapter의 분류·파싱 재사용.
"""

from __future__ import annotations

import re
from datetime import date

from .models import Right, RightType
from .codef_adapter import classify_right_type, parse_date_kr, parse_amount_kr

# 항목 시작 줄: "순위 등기목적 … YYYY년MM월DD일"
_ENTRY = re.compile(r"^(\d+(?:-\d+)?)\s+(.+?)\s+(\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)")
# 말소 항목이 참조하는 순위: "1번근저당권설정등기말소" → 1
_CANCEL_REF = re.compile(r"(\d+)\s*번")

# 구(區) 구분 줄. 상세부 "【 갑 구 】" 와 요약부 "( 갑구 )" 양식 모두 인식.
_GU_GAP = re.compile(r"【\s*갑\s*구\s*】|\(\s*갑\s*구\s*\)")
_GU_EUL = re.compile(r"【\s*을\s*구\s*】|\(\s*을\s*구\s*\)")

_HOLDER_LABELS = ["근저당권자", "전세권자", "지상권자", "지역권자", "임차권자",
                  "가등기권자", "가처분권자", "공유자", "소유자", "등기명의인",
                  "채권자", "권리자"]
# 페이지/열 머리글 등 본문에 섞여 들어오는 잡음 줄(권리자 추출 오인 방지)
_NOISE = ("권리자 및 기타사항", "순위번호", "등 기 목 적", "열람일시", "[집합건물]")
# 권리자명으로 인정하지 않는 토큰(머리글 잔재 등)
_HOLDER_STOP = {"및", "기타사항", "기타"}


# 한 글자라도 유효한 권리자(국가 압류 등): '국'=대한민국
_VALID_SHORT = {"국"}


def _valid_holder(name: str) -> bool:
    """권리자명 유효성: 잡음 토큰·접수번호(제N호)·숫자 포함 토큰 제외."""
    if name in _HOLDER_STOP:
        return False
    if any(ch.isdigit() for ch in name):  # 접수번호/지번 등은 사람·법인명이 아님
        return False
    if len(name) < 2 and name not in _VALID_SHORT:
        return False
    return True


def _extract_holder(body: str) -> str:
    # 머리글 잡음 줄 제거 후 라벨 매칭(라벨별로 첫 '유효' 토큰 채택)
    clean = "\n".join(ln for ln in body.split("\n")
                      if not any(n in ln for n in _NOISE))
    for label in _HOLDER_LABELS:
        for m in re.finditer(label + r"\s+([가-힣A-Za-z()㈜][가-힣A-Za-z()0-9]*)", clean):
            name = m.group(1).strip()
            if _valid_holder(name):
                # 국가 압류(권리자 '국')는 실제 집행기관인 '처분청'을 함께 표기
                if name == "국":
                    mp = re.search(r"처분청\s*([가-힣A-Za-z()]+)", clean)
                    if mp:
                        return f"국 ({mp.group(1).strip()})"
                return name
    return ""


# 소유자/공유자 이름: '홍길동 800101-*******' 패턴(주민번호 뒤따름)으로 추출
_OWNER_NAME = re.compile(r"([가-힣]{2,4})\s+\d{6}\s*-\s*\*{5,7}")


def _owner_names(body: str) -> list[str]:
    out: list[str] = []
    for n in _OWNER_NAME.findall(body):
        if n not in out:
            out.append(n)
    return out


def _fmt_owners(names: list[str]) -> str:
    """소유자 표시: 1명=이름, 2명='A, B', 3명↑='A 外 N인'."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]}, {names[1]}"
    return f"{names[0]} 外 {len(names) - 1}인"


def parse_building(text: str) -> dict:
    """등기 표제부 → 면적/구조/층 정보(있는 것만). 집합건물 기준."""
    out: dict = {}
    # 대지권 비율: '소유권대지권 322900분의 6860' → 토지 3229㎡ 중 68.60㎡
    m = re.search(r"대지권\s*([\d,]+)\s*분의\s*([\d,]+)", text)
    if m:
        tot = int(m.group(1).replace(",", "")) / 100
        share = int(m.group(2).replace(",", "")) / 100
        out["land_total"] = f"{tot:,.0f}㎡" if tot == int(tot) else f"{tot:,.2f}㎡"
        out["land_share"] = f"{share:,.2f}㎡"
    # 전유부분: '제1층 제102호'
    m = re.search(r"제\s*(\d+)\s*층\s*제\s*(\d+(?:-\d+)?)\s*호", text)
    if m:
        out["floor"] = int(m.group(1))
        out["ho"] = m.group(2)
    # 1동 구조 + 총층 + 용도
    seg = text[text.find("1동의 건물의 표시"):text.find("대지권의 목적")] or text[:1500]
    # 구조: '철근콘크리트조 … 지붕' (줄바꿈만 제거해 단어 보존)
    ms = re.search(r"(철근콘크리트[가-힣A-Za-z·및\s]*?지붕|벽돌조[가-힣·및\s]*?지붕|연와조[가-힣·및\s]*?지붕)", seg)
    if ms:
        out["structure"] = re.sub(r"\s{2,}", " ", ms.group(1).replace("\n", "")).strip()
    seg1 = re.sub(r"\s+", "", seg)
    mt = re.search(r"(\d+)층(공동주택|아파트|연립주택|다세대주택|주상복합|도시형생활주택)", seg1)
    if mt:
        out["total_floors"] = int(mt.group(1))
        out["bldg_usage"] = mt.group(2)
    return out


def parse_registry(text: str) -> list[Right]:
    """등기 텍스트 → 현행 유효 권리 목록(말소분 제외)."""
    lines = text.split("\n")

    # 1) 항목 단위로 그룹핑(시작 줄 + 이어지는 본문 줄) + 현재 구(區) 추적
    entries: list[dict] = []
    cur: dict | None = None
    gu = ""  # "갑" | "을" | ""(표제부)
    for ln in lines:
        s = ln.strip()
        # 구 구분 줄 감지(항목 줄보다 먼저 검사)
        if _GU_GAP.search(s):
            gu = "갑"
        elif _GU_EUL.search(s):
            gu = "을"

        m = _ENTRY.match(s)
        if m:
            if cur:
                entries.append(cur)
            cur = {"rank": m.group(1), "purpose": m.group(2),
                   "date": m.group(3), "body": [ln], "gu": gu}
        elif cur:
            cur["body"].append(ln)
    if cur:
        entries.append(cur)

    # 2) 항목별 권리 추출 + 말소 순위 수집 (구별로 분리)
    rights: list[tuple[str, str, Right]] = []   # (gu, base_rank, Right)
    cancelled: set[tuple[str, str]] = set()      # (gu, rank)
    owner_fix: dict[tuple[str, str], list[str]] = {}  # 소유권경정 → (gu,순위):보정소유자
    for e in entries:
        rank = e["rank"]
        base_rank = rank.split("-")[0]
        e_gu = e["gu"]
        purpose = e["purpose"].replace(" ", "")
        body = "\n".join(e["body"])
        # 등기목적이 다음 줄로 넘어가는 경우 대비(예: '…설정등'+'기말소')
        head2 = (purpose + " " + " ".join(e["body"][1:2])).replace(" ", "")

        # 말소 항목 → 같은 구의 참조 순위 취소(등기목적이 줄바꿈돼도 head2로 감지)
        if "말소" in head2:
            ref = _CANCEL_REF.search(head2)
            if ref:
                cancelled.add((e_gu, ref.group(1)))
            continue
        # 소유권경정(상속포기 등) → 참조 순위의 소유자를 보정값으로 교체
        if "소유권경정" in head2:
            ref = _CANCEL_REF.search(head2)
            names = _owner_names(body)
            if ref and names:
                owner_fix[(e_gu, ref.group(1))] = names
            continue
        # 부기등기(변경/이전/경정/대위/회복)는 새 권리 아님.
        #   판정: 순위가 'N-M'(1-1 등)이거나, 다른 순위를 참조하는 'N번…변경/이전/…'.
        #   주의: 'N번' 참조가 없는 본등기 '소유권이전'은 부기가 아니므로 제외하면 안 됨.
        if "-" in rank or re.match(r"\d+번.*(변경|이전|경정|대위|회복)", head2):
            continue

        rt = classify_right_type(purpose)
        if rt is None:
            continue
        d = parse_date_kr(e["date"])
        if d is None:
            continue
        # 소유권이전은 공유자/소유자 이름(주민번호 패턴)으로 추출 → 다인 표기
        if rt == RightType.OWNERSHIP_TRANSFER:
            holder = _fmt_owners(_owner_names(body)) or _extract_holder(body)
        else:
            holder = _extract_holder(body)
        rights.append((e_gu, base_rank, Right(
            type=rt, reg_date=d, holder=holder,
            amount=parse_amount_kr(body),
        )))

    # 3) 말소된 순위 제외(같은 구) + 소유권경정 반영 + 중복 제거
    live: list[Right] = []
    seen: set = set()
    for gu_, rk, r in rights:
        if (gu_, rk) in cancelled:
            continue
        if r.type == RightType.OWNERSHIP_TRANSFER and (gu_, rk) in owner_fix:
            r.holder = _fmt_owners(owner_fix[(gu_, rk)])  # 경정된 현 소유자로 교체
        k = (r.type, r.reg_date, r.holder, r.amount)
        if k in seen:
            continue
        seen.add(k)
        live.append(r)

    # 4) ★ '주요 등기사항 요약'(말소되지 않은 사항만)이 있으면, 담보/제한권리는
    #    요약의 유효 권리로 교체(취소선=말소는 PDF텍스트로 못 잡으므로 요약이 정확).
    #    소유권이전 이력(경정 반영)은 본문 갑구 그대로 유지.
    summary = _parse_summary_liens(text)
    if summary is not None:
        # 요약에서 권리자/금액이 비면 갑구 본문(live)의 같은 권리(종류+날짜)에서 보충
        by_td: dict = {}
        for r in live:
            if r.holder:
                by_td.setdefault((r.type, r.reg_date), r.holder)
        amt_td: dict = {(r.type, r.reg_date): r.amount for r in live if r.amount}
        for r in summary:
            full = by_td.get((r.type, r.reg_date), "")
            # 요약 권리자가 비거나 '국'뿐이면 갑구 본문(처분청 포함)으로 보충
            if full and (not r.holder or r.holder == "국"):
                r.holder = full
            if not r.amount and amt_td.get((r.type, r.reg_date)):
                r.amount = amt_td[(r.type, r.reg_date)]
        owners = [r for r in live if r.type == RightType.OWNERSHIP_TRANSFER]
        merged: list[Right] = []
        seen2: set = set()
        for r in owners + summary:
            kk = (r.type, r.reg_date, r.holder, r.amount)
            if kk in seen2:
                continue
            seen2.add(kk)
            merged.append(r)
        live = merged

    live.sort(key=lambda r: r.reg_date)
    return live


def _parse_summary_liens(text: str) -> list[Right] | None:
    """'주요 등기사항 요약'의 2·3절(갑구 제한권리 + 을구 담보/용익) → 유효 권리 목록.
    요약은 말소분이 제외돼 있어 가장 정확. 요약이 없으면 None."""
    i = text.find("주요 등기사항 요약")
    if i < 0:
        return None
    seg = text[i:]
    j = seg.find("[ 참 고 사 항 ]")
    if j > 0:
        seg = seg[:j]
    s2 = seg.find("소유지분을 제외한")            # 2. 갑구 제한권리
    s3re = re.search(r"\(?근\)?저당권\s*및\s*전세권", seg)  # 3. 을구
    s3 = s3re.start() if s3re else -1
    regions = []
    if s2 >= 0:
        regions.append(seg[s2:(s3 if s3 > s2 else len(seg))])
    if s3 >= 0:
        regions.append(seg[s3:])
    if not regions:
        return None

    rights: list[Right] = []
    seen: set = set()
    for region in regions:
        entries: list[dict] = []
        cur: dict | None = None
        for ln in region.split("\n"):
            m = _ENTRY.match(ln.strip())
            if m:
                if cur:
                    entries.append(cur)
                cur = {"purpose": m.group(2), "date": m.group(3), "body": [ln]}
            elif cur:
                cur["body"].append(ln)
        if cur:
            entries.append(cur)
        for e in entries:
            purpose = e["purpose"].replace(" ", "")
            rt = classify_right_type(purpose)
            if rt is None:
                continue
            d = parse_date_kr(e["date"])
            if d is None:
                continue
            body = "\n".join(e["body"])
            holder = _extract_holder(body)
            k = (rt, d, holder)
            if k in seen:
                continue
            seen.add(k)
            rights.append(Right(type=rt, reg_date=d, holder=holder,
                                amount=parse_amount_kr(body)))
    return rights
