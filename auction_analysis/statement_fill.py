# -*- coding: utf-8 -*-
"""매각물건명세서(PDF) → 임차인(item_tenants)·최선순위/배당요구종기(items)·명세서상 인수 권리(item_rights) 채우기.

2026-09-18 주인님 지시: 법원에서 수집한 물건은 임차인 행이 없어 권리분석이 '안전'처럼 보였다(예: 대항력 있는 임차인이
배당요구를 안 해 보증금 전액을 인수해야 하는 물건). 명세서에 최선순위 설정·배당요구종기·점유자 표(성명·점유부분·
정보출처·권원·기간·보증금·차임·전입/사업자등록·확정일자·배당요구여부)·비고·'매각으로 소멸되지 아니하는 것'이 다 있으니
그것을 읽어 스피드옥션 양식(item_tenants)대로 채운다.
 - 명세서 판정이 등기 파싱과 충돌하면 명세서 우선: '소멸되지 아니하는 것'에 적힌 권리는 인수.
 - 사실만 표시한다(전입·확정·배당요구 여부/일자·보증금·차임·점유·권원·비고 문장). '경매신청채권자는 배당요구 없이도
   배당받는다' 같은 추론 규칙은 넣지 않는다(주인님 지시). 신청채권자 승계 등 비고 문장은 임차인 코멘트로 그대로 보인다.
 - 대항력 = 전입(익일 0시) < 최선순위 설정일. 전입 미상·미전입인 전세권자/임차권등기권자는 비고의 등기일로 판단하고,
   그것도 없으면 '대항력 미상'으로 표시(안전으로 단정하지 않음).
정규화·판정 로직은 여기(순수 함수), DB 읽기/쓰기·대량 실행·스윕·API는 api/main.py.
"""
from __future__ import annotations

import re
from datetime import date

from .sale_statement_parser import parse_sale_statement
from .doc_analysis import _merge_tenants, _occupant_key

STMT_VER = 1
SRC_TAG = "[명세서]"          # item_tenants.status 끝 꼬리표(우리가 채운 행 식별)
# 명세서 '소멸되지 아니하는 것'과 등기 권리종류를 잇는 키워드(둘 다에 들어 있으면 그 권리는 인수)
_SURVIVE_KW = ("임차권", "전세권", "가처분", "가등기", "지상권", "지역권", "환매")
_DATE_RE = r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?"


def _d(y, m, d) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def senior_entries(senior_setup: str) -> list[tuple[date, str]]:
    """최선순위 설정 칸의 (날짜, 권리) 목록. '2021.10.01.근저당권(토지) 2023.09.18.근저당권(건물)' → 2개."""
    s = senior_setup or ""
    ms = list(re.finditer(_DATE_RE, s))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(s)
        kind = re.sub(r"\s+", "", s[m.end():end]).strip(" .,;·")
        if kind.startswith("(") and kind.endswith(")") and kind.count("(") == 1:
            kind = kind[1:-1]                      # '(근저당권)' → '근저당권'
        d = _d(m.group(1), m.group(2), m.group(3))
        if d:
            out.append((d, kind))
    return out


def senior_info(senior_setup: str) -> tuple[date | None, str]:
    """'2022.11.25.근저당권' / '2025. 8. 12. 경매개시결정' → (2022-11-25, '근저당권').
    토지·건물이 따로 적힌 경우(예: 근저당권(토지) / 근저당권(건물)) 주택임차인의 대항력은 건물 기준 → '건물' 항목, 없으면 첫 항목."""
    ents = senior_entries(senior_setup)
    if not ents:
        return None, re.sub(r"\s+", " ", senior_setup or "").strip()
    pick = next((e for e in ents if "건물" in e[1]), ents[0])
    return pick


def norm_right(raw: str) -> str:
    """명세서 '점유의 권원'(예: '주거\\n주택임\\n차권자') → 스피드옥션 tenant_right 라벨."""
    t = re.sub(r"\s+", "", raw or "")
    if not t:
        return ""
    if "전세권" in t:
        return ("상가" if "상가" in t else "주거") + "전세권자"
    if "임차권" in t:
        return "상가임차권자" if "상가" in t else "주택임차권자"
    if "유치권" in t:
        return "유치권점유자"
    if "상가" in t or "점포" in t or "사무" in t:
        return "상가임차인"
    if "임차" in t:
        return "주거임차인"
    return t[:20]


def sentences_about(text: str, key: str, other_keys: list[str] | None = None) -> str:
    """비고에서 이 점유자 몫의 문장들: 이름이 나온 문장부터, 다른 점유자 이름이 나오기 전까지(같은 사람 단락으로 봄).
    문장 경계는 한글/괄호 뒤의 마침표(날짜 '2024. 8. 29.'의 마침표는 경계가 아님)."""
    if not text or not key:
        return ""
    flat = re.sub(r"\s+", " ", text)
    parts = [p.strip() for p in re.split(r"(?<=[가-힣\)\]])\.\s+", flat) if p.strip()]
    others = [o for o in (other_keys or []) if o and o != key]
    out, on = [], False
    for p in parts:
        pn = p.replace(" ", "")
        if key in pn:
            on = True
        elif on and any(o in pn for o in others):
            on = False
        if on:
            out.append(p if p.endswith(".") else p + ".")
    return " ".join(out)[:400]


def reg_date_from_note(note: str) -> date | None:
    """비고의 '전세권설정등기일은 2015.12.29.임' / '주택임차권등기일은 2024. 8. 29.임' → 등기일."""
    m = re.search(r"(?:전세권설정등기일|임차권등기일|등기일)[은는]?\s*" + _DATE_RE, note or "")
    return _d(m.group(1), m.group(2), m.group(3)) if m else None


def build_rows(parsed: dict) -> dict:
    """파싱 결과 → item_tenants 행 목록 + items 보완값 + 인수 권리 키워드.
    반환: {available, no_tenant, senior_date(iso|None), senior_kind, deadline, tenants:[row], surviving, caution, ground,
           unknown_power(전입 미상 수)}"""
    if not parsed or not parsed.get("available"):
        return {"available": False, "tenants": []}
    sd, skind = senior_info(parsed.get("senior_setup") or "")
    deadline = parsed.get("dividend_deadline")
    caution = parsed.get("caution") or ""
    out = {"available": True, "no_tenant": bool(parsed.get("no_tenant")), "senior_date": sd.isoformat() if sd else None,
           "senior_kind": skind, "deadline": deadline, "surviving": parsed.get("surviving_rights") or "",
           "caution": caution, "ground": parsed.get("ground_rights") or "", "tenants": [], "unknown_power": 0}
    merged = _merge_tenants(parsed.get("tenants") or [])
    raw_by_key: dict[str, list] = {}
    for r in parsed.get("rows") or []:
        k = _occupant_key(r.get("name") or "") or (r.get("name") or "")
        raw_by_key.setdefault(k, []).append(r)
    all_keys = [(_occupant_key(t.name) or t.name) for t in merged]
    for i, t in enumerate(merged):
        key = _occupant_key(t.name) or t.name
        raws = raw_by_key.get(key, [])
        right = next((norm_right(r.get("right")) for r in raws if norm_right(r.get("right"))), "")
        occ = next((re.sub(r"\s+", " ", r.get("part") or "").strip() for r in raws
                    if (r.get("part") or "").strip() and "미상" not in r.get("part")), "")
        note = sentences_about(caution, key, all_keys)
        mv, fx = t.move_in_date, t.fixed_date
        reg_dt = reg_date_from_note(note) if not mv else None
        basis = mv or reg_dt
        has_opp: bool | None
        if sd and basis:
            has_opp = basis < sd                 # 전입 익일 0시 ≤ 설정일 ⇔ 전입일 < 설정일 (등기일도 같은 날이면 선후 불명 → 미인정)
        else:
            has_opp = None
        demanded = bool(t.demanded_distribution)
        ddate = t.demand_date
        late = bool(ddate and deadline and ddate.isoformat() > deadline)
        facts = []
        if mv:
            facts.append(f"전입 {mv.isoformat()}")
        elif reg_dt:
            facts.append(f"등기 {reg_dt.isoformat()}")
        else:
            facts.append("전입 미상")
        if sd:
            facts.append(f"최선순위 {sd.isoformat()}" + (f"({skind})" if skind else ""))
        facts.append(f"확정일자 {fx.isoformat()}" if fx else "확정일자 없음")
        if ddate:
            facts.append(f"배당요구 {ddate.isoformat()}" + ("(종기 후·무효)" if late else ""))
        elif demanded:
            facts.append("배당요구 있음")
        else:
            facts.append("배당요구 없음")
        if has_opp is True and not demanded:
            label, verdict = "인수", "대항력 있음 · 배당요구 없어 배당을 받지 못함 → 보증금 전액 매수인 인수"
        elif has_opp is True and demanded:
            label, verdict = "미배당금 인수예상", "대항력 있음 · 배당요구로 배당받되 낙찰가에 따라 부족분은 매수인 인수"
        elif has_opp is False:
            label = "소멸"
            verdict = "대항력 없음(전입이 최선순위 설정보다 늦음) → 보증금 매각으로 소멸" + (" · 배당요구로 배당 참여" if demanded else "")
        else:
            label, verdict = "대항력 미상", "전입일 미상 → 대항력 판단 불가(현장·서류 확인 필요)"
            out["unknown_power"] += 1
        # 끝의 '[명세서]' 꼬리표 = 이 행의 출처(명세서 채움) 표식 — 재실행(force) 시 우리 행만 골라 지우는 데 쓴다
        comment = " · ".join(facts) + " → " + verdict + (f" | 비고: {note}" if note else "") + " " + SRC_TAG
        dep = t.deposit if (t.deposit and t.deposit < 10**14) else None     # bigint·상식 범위 밖(파싱 오류) 방어
        out["tenants"].append({
            "seq": i, "name": t.name, "has_opposing_power": bool(has_opp),
            "move_in_date": mv.isoformat() if mv else None, "fixed_date": fx.isoformat() if fx else None,
            "dividend_date": ddate.isoformat() if ddate else None, "deposit": dep,
            "rent": t.rent or 0, "tenant_right": right or None, "occupancy": occ or None,
            "status": f"{label} {t.name}: {comment}",
            "assume_amount": (dep if (label == "인수" and dep) else None),
            "label": label,
        })
    return out


def survive_types(surviving: str, tenants: list[dict]) -> list[str]:
    """'소멸되지 아니하는 것' 문장에 적힌 권리 키워드 + 대항력 있는 임차인의 임차권/전세권 → 인수로 볼 권리종류 키워드."""
    kws = [k for k in _SURVIVE_KW if k in (surviving or "").replace(" ", "")]
    for t in tenants:
        if t.get("has_opposing_power") and t.get("tenant_right"):
            if "전세권" in t["tenant_right"] and "전세권" not in kws:
                kws.append("전세권")
            if "임차권" in t["tenant_right"] and "임차권" not in kws:
                kws.append("임차권")
    return kws


def rights_to_assume(rights: list[dict], surviving: str, tenants: list[dict]) -> list[dict]:
    """item_rights 행 중 명세서 기준으로 '인수'가 돼야 하는 행(현재 status != 인수). rights: {id,right_type,holder,status}"""
    kws = survive_types(surviving, tenants)
    opp_names = {re.sub(r"\s", "", t["name"]) for t in tenants if t.get("has_opposing_power") and t.get("name")}
    out = []
    for r in rights or []:
        rtype = (r.get("right_type") or "").replace(" ", "")
        if (r.get("status") or "") == "인수":
            continue
        hit = any(k in rtype for k in kws if k in ("가처분", "가등기", "지상권", "지역권", "환매"))
        if not hit and ("임차권" in rtype or "전세권" in rtype):
            k = "임차권" if "임차권" in rtype else "전세권"
            holder = (r.get("holder") or "").replace(" ", "")
            # 명세서에 그 권리가 '소멸되지 않는다'고 적혔거나, 그 권리자가 대항력 있는 임차인이면 인수
            hit = (k in kws and k in (surviving or "").replace(" ", "")) or any(n and (n in holder or holder in n) for n in opp_names)
        if hit:
            out.append(r)
    return out


def parse_pdf(pdf_bytes: bytes) -> dict:
    return parse_sale_statement(pdf_bytes)


def parse_json(data) -> dict:
    """법원 명세서 글자 JSON(media.kind='매각물건명세서_글자') — PDF가 없거나 그림 PDF인 물건용(2026-09-22)."""
    from .sale_statement_parser import parse_sale_statement_json
    return parse_sale_statement_json(data)
