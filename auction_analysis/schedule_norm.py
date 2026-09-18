# -*- coding: utf-8 -*-
"""기일현황(auction_schedule) 정규화 — 스피드옥션 양식 + 주인님 회차 규칙(2026-09-18).

배경: 물건이 스피드옥션·대법원 경매정보 두 출처에서 수집되며 기일현황 행이 뒤섞였다(법원 출처는 매각기일이 아닌
기일(대금지급기한 등)이 회차·최저가 빈칸으로 들어가 화면에서 '0원'·맨 아래로 밀림, 매각결정기일 누락).

규칙(주인님 확정):
  1) 모든 기일을 시간순으로 한 표에.
  2) 회차는 **최저매각금액이 떨어질 때만** 다음 회차(신건→2차→3차…). 같은 금액으로 다시 진행(미납 후 재매각)은 같은 회차.
  3) 매각기일이 아닌 행(매각결정기일·대금지급기한·배당기일 …)은 회차 없음. 최저매각금액 칸에는 기일 종류를 쓴다(스피드옥션 양식).
  4) 결과가 비어 있는 매각결정기일(유찰 뒤 형식적 기일)은 표에 넣지 않는다.
  5) 낙찰 상세(낙찰가·낙찰가율·입찰수·2등·낙찰자)는 기존 행의 값을 같은 날짜 매각 행에 붙인다.
원천: 법원 '기일내역문서'(media kind 기일내역문서, HTML) 우선 → 없으면 기존 auction_schedule 행만으로 정규화.
"""
from __future__ import annotations

import re
from datetime import date

SALE_KIND = "매각기일"
NONSALE_KINDS = ("매각결정기일", "대금지급기한", "대금지급및배당기일", "배당기일", "대금납부및배당기일")

# 법원 기일결과 → 스피드옥션 결과 라벨(순서 중요: '매각불허가'는 불허가, '최고가매각허가취소결정'은 허가취소, 그 다음 허가)
_RESULT_MAP = [
    (r"불허가", "불허가"),
    (r"허가취소", "허가취소"),
    (r"최고가매각허가결정|매각허가결정|허가", "허가"),
    (r"기한변경|기한연장|기일변경", "기한변경"),
    (r"미납", "미납"),
    (r"대금납부|납부", "납부"),
    (r"배당종결|종결|완료", "완료"),
    (r"유찰", "유찰"),
    (r"매각", "매각"),
    (r"변경", "변경"),
    (r"취하", "취하"),
    (r"취소", "취소"),
    (r"기각", "기각"),
    (r"각하", "각하"),
    (r"정지", "정지"),
]
# 스피드옥션 고유 라벨(재매각 N회·재진행 N회·진행·예정·미진행·추후지정 …)은 사전 매핑을 거치지 않고 그대로 둔다
_KEEP_RE = re.compile(r"^(재매각|재진행|진행|예정|미진행|추후지정|일부배당|차순위|기한후납부)")


def norm_result(s: str | None, kind: str = "") -> str:
    t0 = re.sub(r"\s+", " ", s or "").strip()   # 원문(띄어쓰기 한 칸으로만 정리)
    if " / " in t0:                              # 병합 행의 결과('유찰 / 변경')는 조각마다 정규화해 그대로 유지(멱등)
        return " / ".join(x for x in (norm_result(p, kind) for p in t0.split(" / ")) if x)
    t = t0.replace(" ", "")
    if not t:
        return ""
    if _KEEP_RE.match(t):
        return t0[:14]
    if kind == SALE_KIND and t == "매각":
        return "매각"
    for pat, lab in _RESULT_MAP:
        if re.search(pat, t):
            return lab
    return t0[:14]   # 사전에 없는 결과는 띄어쓰기 보존해 그대로


def _num(s) -> int | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    t = re.sub(r"[^0-9]", "", str(s))
    return int(t) if t else None


# 같은 날짜·같은 회차칸으로 병합된 매각기일 금액 표기('810,000,000원 / 890,000,000원') — 재정규화 때 절차기일로 오인해 회차가
#  빠지던 것(2026-09-18 실측 8행) 방지: 매각기일로 보고, 회차 계산 금액은 마지막 금액(정규화가 회차 계산에 쓴 last_price)
_MERGED_AMT_RE = re.compile(r"^\s*[0-9][0-9,]*\s*원?(\s*/\s*[0-9][0-9,]*\s*원?)+\s*$")


def _price_of(mp) -> int | None:
    s = str(mp or "")
    if _MERGED_AMT_RE.match(s):
        return int(re.findall(r"[0-9][0-9,]*", s)[-1].replace(",", ""))
    return _num(s)


def fmt_won(n: int | None) -> str:
    return f"{n:,}원" if n else ""


def _ymd(s: str) -> str:
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def _hm(s: str) -> str:
    m = re.search(r"\((\d{1,2}):(\d{2})\)", s or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def level_rounds(prices: list) -> list[int]:
    """매각기일 금액(시간순) → 회차 번호. 주인님 규칙(2026-09-18) '최저매각금액이 떨어질 때만 다음 회차' + 재진행·재감정 보완:
      ① 처음 보는 더 낮은 금액 → 다음 회차(그보다 높은 금액 단계 수 + 1)
      ② 같은 금액 → 같은 회차(미납 후 재매각 등)
      ③ 전에 나온 금액으로 다시 오름 → 그 금액의 회차(변경·불허가 뒤 80% 가격으로 재진행 = 2차).
         예전엔 '오르면 신건부터'라 A01|2025|102122|1(…10차 3,208만 변경 → 1억9,120만 재진행)이 신건·2차로 찍혀 목록(3차)과 어긋났음
      ④ 처음 보는 금액으로 오름 → 재감정·새 주기 → 신건부터(M01|2019|22106|1: 2020 주기 → 2025 재감정)
      ⑤ 떨어졌다가 그 절차의 첫 가격(감정가)으로 되돌아감 → 새 절차의 신건(옛 회차는 버림). 예전엔 ③으로 처리해 그 뒤 30% 저감
         가격을 옛 회차 사이에 끼워 한 칸 더 셈(실측 B03|2023|105977|17: 1억400만 재진행 → 7,280만이 3차로, 목록은 2차)
      금액 없는 행은 직전 회차. 원 단위 반올림 차이(±1,000원)는 같은 금액으로 본다."""
    st = _RoundState()
    return [st.step(p) for p in prices]


def _tol(p: int) -> int:
    return max(1000, p // 10000)


class _RoundState:
    """level_rounds 규칙의 상태 기계(금액 하나씩 넣으면 회차를 돌려줌).
    anchor = 목록의 현재 감정가: 그 금액의 행은 현재 절차의 신건(재감정으로 감정가가 내려간 경우 — 실측 E05|2025|52292|1:
    1억7,000만 재진행 뒤 재감정 1억4,500만이 '떨어진 2차'로 잡히던 것)."""

    def __init__(self, anchor=None):
        self.levels: dict[int, int] = {}
        self.top = None
        self.last = None
        self.anchor = anchor

    def is_top_return(self, p) -> bool:
        return (p is not None and self.top is not None and self.last is not None
                and abs(p - self.top) <= _tol(self.top) and self.last < self.top - _tol(self.top))

    def is_anchor_start(self, p) -> bool:
        return bool(p is not None and self.anchor and abs(p - self.anchor) <= _tol(p) and self.top is not None
                    and abs(self.top - p) > _tol(p) and self.last is not None and abs(self.last - p) > _tol(p))

    def step(self, p, allow_restart: bool = True) -> int:
        if p is None:
            return self.levels.get(self.last, 1) if self.last is not None else 1
        hit = next((q for q in self.levels if abs(q - p) <= _tol(p)), None)
        if not self.levels or (allow_restart and self.is_top_return(p)) or self.is_anchor_start(p):
            self.levels, self.top, n = {p: 1}, p, 1          # 첫 행 / ⑤ 첫 가격으로 되돌아감 / 현재 감정가 = 새 절차 신건
        elif hit is not None:
            n = self.levels[hit]
        elif self.last is not None and p < self.last:
            n = 1 + sum(1 for q in self.levels if q > p)
            self.levels[p] = n
        else:
            self.levels, self.top, n = {p: 1}, p, 1          # ④ 처음 보는 금액으로 오름 = 재감정
        self.last = p
        return n


def order_and_rounds(sale: list, anchor=None) -> tuple:
    """[(날짜, 금액)] 시간순 → (처리 순서 인덱스 목록, 인덱스별 회차). 같은 날짜에 '그 절차의 첫 가격으로 되돌아간 행'과 다른
    행이 함께 있으면 되돌아간 행을 그날 마지막에 둔다(옛 절차의 변경 행 먼저 → 새 절차 신건). 그 외에는 원래 순서 그대로
    (실측 L05|2024|55405|1: 같은 날 신건·2차가 연달아 적힌 경우 원래 순서가 맞음)."""
    st = _RoundState(anchor)
    order: list[int] = []
    rounds = [1] * len(sale)
    i = 0
    while i < len(sale):
        j = i
        while j < len(sale) and sale[j][0] == sale[i][0]:
            j += 1
        grp = list(range(i, j))
        if len(grp) > 1:
            # 새 절차 시작 행(첫 가격 복귀·현재 감정가 = 재감정)은 그날 마지막 — 먼저 두면 옛 절차 행까지 '신건'이 돼 한 행으로
            #  합쳐지고 탐지와 어긋나 20분마다 공회전(실측 A01|2025|102915|1: 04-08 2.45억 재감정 + 7.96억 옛 절차 변경)
            back = [g for g in grp if st.is_top_return(sale[g][1]) or st.is_anchor_start(sale[g][1])]
            if back:
                grp = [g for g in grp if g not in back] + back
        nxt = next((sale[x][1] for x in range(j, len(sale)) if sale[x][1] is not None), None)
        for g in grp:
            p = sale[g][1]
            allow = True
            if st.is_top_return(p) and nxt is not None:
                # 되돌아간 뒤 다음 가격이 새 절차의 저감 단계(그 가격의 80%·70% 1~3단계)가 아니고 옛 절차의 저감 단계로 이어지면
                #  재시작이 실제로 없었던 것(실측 A02|2024|58545|1: 08-24 옛 절차 28.8억 유찰 + 56.2억 재시작 '변경' → 다음 23.0억 = 28.8억×0.8)
                def _fits(base, val, ks):
                    return any(abs(val - base * r ** k) <= max(_tol(val), val * 0.002) for r in (0.8, 0.7) for k in ks)
                new_fit = _fits(p, nxt, (0, 1, 2, 3))
                old_fit = st.last is not None and _fits(st.last, nxt, (0, 1, 2))
                allow = new_fit or not old_fit
            rounds[g] = st.step(p, allow_restart=allow)
            order.append(g)
        i = j
    return order, rounds


def round_label(n: int) -> str:
    return "신건" if n <= 1 else f"{n}차"


def stored_rounds_wrong(rows: list[dict], anchor=None) -> bool:
    """저장된 기일현황 행(id 포함) → 매각기일 행의 회차 라벨이 level_rounds 규칙과 다르면 True.
    20분 스윕의 탐지를 정규화와 '같은 함수'로 해서 두 규칙이 어긋나 공회전하는 일을 막는다."""
    rs = sorted(rows, key=lambda r: (_ymd(str(r.get("sell_date") or "")), r.get("id") or 0))
    sale = [r for r in rs if str(r.get("round") or "").strip() and _kind_from_row(r) == SALE_KIND]
    if not sale:
        return False
    # 합쳐진 행('A원 / B원')은 금액별로 풀어서 계산(정규화가 두 건을 따로 처리한 것과 같게) — 풀린 조각들의 회차가 서로 다르면
    #  합쳐질 이유가 없던 것이므로 불일치로 본다
    seq, owner = [], []
    for idx, r in enumerate(sale):
        for p in _split_amounts(r.get("min_price")):
            seq.append((_ymd(str(r.get("sell_date") or "")), p))
            owner.append(idx)
    _, want = order_and_rounds(seq, anchor=_num(anchor) if anchor else None)
    per_row: dict[int, set] = {}
    for o, n in zip(owner, want):
        per_row.setdefault(o, set()).add(round_label(n))
    return any(per_row.get(i) != {str(r.get("round") or "").strip()} for i, r in enumerate(sale))


def _split_amounts(mp) -> list:
    """'245,000,000원 / 796,160,000원' → [245000000, 796160000]. 합쳐진 표기가 아니면 [금액 하나]."""
    s = str(mp or "")
    if _MERGED_AMT_RE.match(s):
        return [int(x.replace(",", "")) for x in re.findall(r"[0-9][0-9,]*", s)]
    return [_price_of(s)]


def _amount_hit(p, mp) -> bool:
    """금액 p가 행의 최저가 표기(합쳐진 표기면 그 조각 중 하나)와 같은가."""
    return p is not None and any(a is not None and abs(a - p) <= _tol(p) for a in _split_amounts(mp))


def _ex_result_for(e: dict, ex_rows: list, sole: bool) -> str:
    """기존 행에서 이벤트 e의 결과. 매각기일은 금액이 맞는 행(합쳐진 행이면 금액·결과를 짝지은 조각)의 결과.
    금액이 맞는 행이 없으면 그날 매각기일이 e 하나(sole)일 때만 금액 하나짜리 행의 결과를 쓴다(기존 동작)."""
    fallback = ""
    for r in ex_rows:
        if _kind_from_row(r) != e["kind"] or not r.get("result"):
            continue
        if e["kind"] != SALE_KIND:
            return norm_result(r.get("result"), e["kind"])
        amts = _split_amounts(r.get("min_price"))
        parts = [x.strip() for x in str(r.get("result")).split(" / ")]
        p = e.get("min_price")
        for i, a in enumerate(amts):
            if p is not None and a is not None and abs(a - p) <= _tol(p):
                if len(amts) == 1:
                    return norm_result(r.get("result"), e["kind"])
                if len(parts) == len(amts):
                    return norm_result(parts[i], e["kind"])
        if not fallback and len(amts) == 1:
            fallback = norm_result(r.get("result"), e["kind"])
    return fallback if sole else ""


def parse_court_schedule_all(html: str) -> dict[str, list[dict]]:
    """법원 기일내역 HTML → {물건번호: [{date, time, kind, place, min_price(int|None), result}]} (표의 모든 물건번호).
    표 구조(실측): 헤더 7칸(물건번호·감정평가액·기일·기일종류·기일장소·최저매각가격·기일결과),
    물건의 첫 행 7칸(물건번호·감정가 rowspan), 이후 행 5칸(기일·종류·장소·최저가·결과, 빈칸 유지)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    out: dict[str, list[dict]] = {}
    for tbl in soup.find_all("table"):
        if "기일종류" not in tbl.get_text():
            continue
        cur_obj = "1"
        for tr in tbl.find_all("tr"):
            cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip() for c in tr.find_all(["td", "th"])]
            if not cells or "기일종류" in cells:
                continue
            if len(cells) >= 7:
                cur_obj = cells[0].strip() or cur_obj
                cells = cells[2:7]
            elif len(cells) == 5:
                pass
            else:
                continue
            d, kind, place, price, res = (cells + ["", "", "", "", ""])[:5]
            ymd = _ymd(d)
            if not ymd:
                continue
            out.setdefault(cur_obj, []).append({"date": ymd, "time": _hm(d), "kind": kind.replace(" ", ""), "place": place,
                                                "min_price": _num(price), "result": res.strip()})
        break
    return out


def parse_court_schedule(html: str, obj_no: str | int = "1") -> list[dict]:
    """법원 기일내역 HTML → 해당 물건번호의 기일 행 목록."""
    return parse_court_schedule_all(html).get(str(obj_no or "1").strip(), [])


def _prune_offchain(ev: list[dict], doc_rows: list[dict], doc_last_sale: str, keep_price: int | None = None) -> list[dict]:
    """다물건 사건: 문서 뒤 날짜의 기존 매각기일 행 중 이 물건의 가격 사슬에 안 맞는 것(다른 물건번호 행 혼입) 제외.
    사슬 = 문서의 매각기일 금액들에서 확정된 저감률(20%/30%, 전 구간 동일할 때만)로 마지막 금액에서 이어지는 금액,
    또는 이미 이 물건 것으로 확정된 금액(재진행)·신건 금액(새 주기). 저감률을 확정 못 하면 아무것도 지우지 않는다.
    keep_price = 목록(items)의 현재 최저가 — 이 물건의 금액이 확실하므로 사슬 판정과 무관하게 남긴다
    (2026-09-18 실측 L01|2025|6453|1: 문서엔 30% 저감 한 번뿐이라 사슬을 30%로만 보고, 그다음 20% 저감된 목록의 현재
     최저가 15.6억 행을 다른 물건 행으로 오인해 지웠다).
    (2026-09-18 실측 A01|2025|939|1: 08-19에 728,064,000·780,288,000·461,312,000 세 행 — 뒤 둘은 물건 2·3의 행)"""
    prices = [r["min_price"] for r in sorted(doc_rows, key=lambda r: (r["date"], r.get("time") or ""))
              if r["kind"] == SALE_KIND and r.get("min_price")]
    if not prices:
        return ev
    ratios = {round(b / a, 4) for a, b in zip(prices, prices[1:]) if b < a}
    if len(ratios) == 1 and next(iter(ratios)) in (0.8, 0.7):
        rs = (next(iter(ratios)),)
    elif not ratios:
        # 문서에 저감 이력이 없음(전부 신건·변경). 문서 뒤에 같은 날짜 다른 금액 매각기일이 있으면(혼입 증거) 법원 표준 저감률
        #  20%·30% 둘 다로 사슬을 시험 — 둘 중 하나에라도 맞으면 유지 (실측 A01|2024|124272|3: 09-08에 378,400,000·190,873,600)
        after = [e for e in ev if e["kind"] == SALE_KIND and e.get("src") == "ex" and e["date"] > doc_last_sale and e.get("min_price")]
        by_date: dict[str, set] = {}
        for e in after:
            by_date.setdefault(e["date"], set()).add(e["min_price"])
        if not any(len(v) > 1 for v in by_date.values()):
            return ev
        rs = (0.8, 0.7)
    else:
        return ev                                   # 저감률이 섞이거나 표준이 아니면 손대지 않는다
    accepted = set(prices)
    mx, last = max(prices), prices[-1]

    def _fits(p: int) -> bool:
        if p in accepted or abs(p - mx) <= 1 or (keep_price and abs(p - keep_price) <= 1):
            return True
        for r in rs:
            exp = last
            for _ in range(8):
                exp = exp * r
                if abs(p - exp) <= 1000:
                    return True
        return False

    out = []
    for e in sorted(ev, key=lambda e: (e["date"], e.get("time") or "")):
        if not (e["kind"] == SALE_KIND and e.get("src") == "ex" and e["date"] > doc_last_sale and e.get("min_price")):
            out.append(e)
            continue
        # 병합 표기 행('582,451,200원 / 624,230,400원')은 금액마다 따로 판정해 이 물건 것만 남긴다
        amts = ([int(x.replace(",", "")) for x in re.findall(r"[0-9][0-9,]*", e["label"])]
                if e.get("label") and _MERGED_AMT_RE.match(e["label"]) else [e["min_price"]])
        kept = [a for a in amts if _fits(a)]
        if not kept:
            continue
        for a in kept:
            accepted.add(a)
        last = kept[-1]
        e["min_price"] = kept[-1]
        e["label"] = " / ".join(fmt_won(a) for a in kept) if len(kept) > 1 else None
        out.append(e)
    return out


def _kind_from_row(r: dict) -> str:
    """기존 auction_schedule 행에서 기일 종류 추정(법원 원본행은 round·min_price가 비어 있음)."""
    mp = str(r.get("min_price") or "").strip()
    # 종류 글자가 있으면 그것이 우선("대금지급기한 납부 (2023.11.21)"의 날짜 숫자를 금액으로 오인하지 않게)
    for k in NONSALE_KINDS:
        if k.replace(" ", "") in mp.replace(" ", ""):
            return k
    if re.fullmatch(r"[0-9,]+\s*원?", mp) or _MERGED_AMT_RE.match(mp):
        return SALE_KIND
    if mp:                                   # 금액이 아닌 텍스트(예: '일부배당') = 그 이름의 절차기일
        return mp
    res = str(r.get("result") or "").replace(" ", "")
    if re.search(r"허가|불허가", res):
        return "매각결정기일"
    if re.search(r"미납|기한변경|납부", res):
        return "대금지급기한"
    if re.search(r"완료|종결", res):
        return "배당기일"
    if not res:                              # 금액·결과·회차 전부 없음 = 정체불명(옛 법원행) → 제외. 회차가 있으면 매각기일(금액 미정)
        return SALE_KIND if str(r.get("round") or "").strip() else "기타"
    return SALE_KIND                         # 유찰·매각·변경·진행·예정·재진행… = 매각기일


def canonical_rows(doc_rows: list[dict] | None, existing: list[dict], today: date | None = None,
                   doc_multi: bool = False, current: tuple | None = None) -> list[dict]:
    """정규화된 auction_schedule 행 목록(순서=시간순). existing 행의 낙찰 상세를 같은 날짜 매각 행에 붙인다.
    doc_rows가 없으면 existing만으로(기존 행 재분류·재라벨). doc_multi = 문서에 물건번호가 여럿(다물건 사건).
    current = (items.sell_date 'YYYY-MM-DD', items.min_price) — 목록의 현재(다음) 매각기일. 오늘 이후인데 표에 그 날짜 매각기일이
    없으면 추가하고 어떤 가지치기 규칙으로도 지우지 않는다(2026-09-18 실측: 크롤러·명세서 회차표가 items만 갱신하고 기일현황 행은
    안 넣어 진행중 1,124건이 '다음 매각기일'이 표에 없었음 — 입찰자에게 가장 중요한 행)."""
    today = today or date.today()
    ev: list[dict] = []           # {date,time,kind,min_price(int|None),result,src,sale...}
    if doc_rows:
        for r in doc_rows:
            ev.append({"date": r["date"], "time": r.get("time") or "", "kind": r["kind"], "src": "doc",
                       "min_price": r.get("min_price"), "result": norm_result(r.get("result"), r["kind"])})
    # existing 행: 문서에 없는 날짜(명세서 회차표로 보완된 예정 매각기일 등)는 추가, 있는 날짜는 결과 보강용
    ex_by_date: dict[str, list[dict]] = {}
    n_doc = len(ev)
    seen_ex: set[tuple] = set()
    for r in existing or []:
        d = _ymd(str(r.get("sell_date") or ""))
        if not d:
            continue
        ex_by_date.setdefault(d, []).append(r)
        kind = _kind_from_row(r)
        mp = _price_of(r.get("min_price")) if kind == SALE_KIND else None
        mtxt = str(r.get("min_price") or "").strip()
        res = norm_result(r.get("result"), kind)
        # 문서 행과 같은 날짜·종류면 병합(추가 안 함). 문서가 없으면 완전히 같은 행(날짜·종류·금액·결과)만 중복 제거.
        if any(e["date"] == d and e["kind"] == kind for e in ev[:n_doc]):
            continue
        sig = (d, kind, mp, mtxt if kind != SALE_KIND else "", res)
        if sig in seen_ex:
            continue
        seen_ex.add(sig)
        if kind == SALE_KIND:
            label = mtxt if _MERGED_AMT_RE.match(mtxt) else None       # 병합 금액 표기는 그대로(멱등)
        else:
            label = mtxt if mtxt.startswith(kind) else kind             # 스피드옥션 부가 정보("대금지급기한 납부 (2023.11.21)") 보존
        ev.append({"date": d, "time": "", "kind": kind, "min_price": mp, "result": res, "src": "ex", "label": label})
    if doc_rows:
        # 문서 범위 안(≤ 문서의 마지막 매각기일)인데 문서에 없는 기존 매각기일 행 = 이 물건의 기일이 아님(옛 크롤러의 다른
        #  물건번호 행 혼입 등, 2026-09-18 실측 381건) → 제외. 절차기일 행(납부·배당 등 스피드옥션 정보)은 유지.
        doc_sale_dates = {r["date"] for r in doc_rows if r["kind"] == SALE_KIND}
        doc_last_sale = max(doc_sale_dates) if doc_sale_dates else ""
        ev = [e for e in ev if not (e.get("src") == "ex" and e["kind"] == SALE_KIND and doc_last_sale
                                    and e["date"] <= doc_last_sale and e["date"] not in doc_sale_dates)]
        if doc_multi and doc_last_sale:
            ev = _prune_offchain(ev, doc_rows, doc_last_sale,
                                 keep_price=_num(current[1]) if (current and len(current) > 1 and current[1]) else None)
    # 목록의 현재 매각기일(오늘 이후) 보장 — 가지치기 뒤에 넣어 어떤 규칙도 지우지 못하게
    if current and current[0] and current[1]:
        cd, cp = str(current[0])[:10], _num(current[1])
        if cp and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cd) and cd >= today.isoformat() \
                and not any(e["date"] == cd and e["kind"] == SALE_KIND for e in ev):
            ev.append({"date": cd, "time": "", "kind": SALE_KIND, "min_price": cp, "result": "", "src": "item", "label": None})
    # 문서 행의 빈 결과를 기존 행 결과로 보강(같은 날짜·같은 종류). 매각기일은 금액이 맞는 조각의 결과만 — 같은 날 매각기일이
    #  둘(옛 절차 변경 + 재감정 신건)일 때 합쳐진 결과('유찰 / 변경')를 통째로 붙이면 돌릴 때마다 결과가 불어났다
    #  (2026-09-18 실측 I02|2025|10682|1: '변경 / 유찰 / 변경')
    n_sale_on: dict[str, int] = {}
    for e in ev:
        if e["kind"] == SALE_KIND:
            n_sale_on[e["date"]] = n_sale_on.get(e["date"], 0) + 1
    for e in ev:
        if not e["result"]:
            e["result"] = _ex_result_for(e, ex_by_date.get(e["date"], []), sole=n_sale_on.get(e["date"], 0) <= 1)
    # 규칙 4: 결과 없는 매각결정기일은 제외. 기타 종류 제외.
    ev = [e for e in ev if not (e["kind"] == "매각결정기일" and not e["result"]) and e["kind"] != "기타"]
    # 문서 스냅샷에만 있는 '지난 매각기일인데 결과 없음'(그 뒤 기일변경으로 사라진 행)은 제외 — 기존 행에 그 날짜가 있으면 유지
    tstr = today.isoformat()
    ev = [e for e in ev if not (e["kind"] == SALE_KIND and not e["result"] and e["date"] < tstr and e["date"] not in ex_by_date)]
    ev.sort(key=lambda e: (e["date"], e["time"], 0 if e["kind"] == SALE_KIND else 1))
    # 규칙 2: 회차 = 최저매각금액이 떨어질 때만 증가(재진행·재감정 보완은 level_rounds 주석). 같은 날 '감정가로 되돌아간 행'은
    #  그날 마지막(order_and_rounds) — 실측 J04|2024|38904|1: 07-13에 8,000만 새 시작과 983만(옛 절차 변경)이 함께 적힘
    _sale = [e for e in ev if e["kind"] == SALE_KIND]
    _anchor = _num(current[2]) if (current and len(current) > 2 and current[2]) else None
    _order, _rnds = order_and_rounds([(e["date"], e["min_price"]) for e in _sale], anchor=_anchor)
    for _pos, _g in enumerate(_order):
        _sale[_g]["_rnd"], _sale[_g]["_pos"] = _rnds[_g], _pos
    ev.sort(key=lambda e: (e["date"], 0 if e["kind"] == SALE_KIND else 1, e.get("_pos", 0), e["time"]))
    rows: list[dict] = []
    for e in ev:
        if e["kind"] == SALE_KIND:
            p = e["min_price"]
            rnd = round_label(e["_rnd"])
            row = {"round": rnd, "sell_date": e["date"], "min_price": e.get("label") or (fmt_won(p) if p else ""),
                   "result": e["result"], "sale_price": None, "sale_rate": None, "bid_count": None,
                   "sale_2nd_price": None, "winner_name": None}
            # 규칙 5: 낙찰 상세 부착(같은 날짜의 기존 매각 행). 그날 매각기일이 둘 이상이면 금액이 맞는 행에만(변경된 옛 절차 행에
            #  낙찰가가 붙지 않게)
            sole = n_sale_on.get(e["date"], 0) <= 1
            for r in ex_by_date.get(e["date"], []):
                if r.get("sale_price") and (sole or _amount_hit(p, r.get("min_price"))):
                    for k in ("sale_price", "sale_rate", "bid_count", "sale_2nd_price", "winner_name"):
                        row[k] = r.get(k)
                    if not row["result"]:
                        row["result"] = "매각"
                    break
        else:
            row = {"round": "", "sell_date": e["date"], "min_price": e.get("label") or e["kind"], "result": e["result"],
                   "sale_price": None, "sale_rate": None, "bid_count": None, "sale_2nd_price": None,
                   "winner_name": None}
        rows.append(row)
    # DB 유니크 제약(item_key, round, sell_date): 같은 날짜·같은 회차칸(특히 회차 없는 절차기일 둘)은 한 행으로 합친다
    merged: list[dict] = []
    seen: dict[tuple, dict] = {}
    for row in rows:
        k = (row["round"], row["sell_date"])
        if k in seen:
            prev = seen[k]
            if row["min_price"] and row["min_price"] not in prev["min_price"]:
                prev["min_price"] = f"{prev['min_price']} / {row['min_price']}" if prev["min_price"] else row["min_price"]
            if row["result"] and row["result"] not in prev["result"]:
                prev["result"] = f"{prev['result']} / {row['result']}" if prev["result"] else row["result"]
            for kk in ("sale_price", "sale_rate", "bid_count", "sale_2nd_price", "winner_name"):
                if prev.get(kk) is None and row.get(kk) is not None:
                    prev[kk] = row[kk]
            continue
        seen[k] = row
        merged.append(row)
    return merged


def rows_equal(a: list[dict], b: list[dict]) -> bool:
    keys = ("round", "sell_date", "min_price", "result", "sale_price", "sale_rate", "bid_count", "sale_2nd_price", "winner_name")
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        for k in keys:
            if str(x.get(k) if x.get(k) is not None else "") != str(y.get(k) if y.get(k) is not None else ""):
                return False
    return True
