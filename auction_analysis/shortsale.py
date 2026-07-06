# -*- coding: utf-8 -*-
"""단기매도 수익률 계산 — auction.html의 ssCalc/acqDetail/ssTax 로직을 서버로 포팅([8강] 엑셀 기준).
카카오 자동발송(매각 예정/완료)에서 낙찰 가정 시 지출·순수익을 텍스트로 뽑기 위함.
클라이언트와 동일한 기본값(대출 감정60%/낙찰80% 낮은쪽·금리5%·중도상환0.5%·3개월·법무20만).
※ 85㎡초과 건물분 부가세(vat)는 기준시가 조회가 필요해 서버 자동계산에선 제외(over85면 안내문만)."""


def ss_tax(income):
    """차익(원) → (종합소득세율%, 누진공제원). 2023~ 누진세율표."""
    T = [(14_000_000, 6, 0), (50_000_000, 15, 1_260_000), (88_000_000, 24, 5_760_000),
         (150_000_000, 35, 15_440_000), (300_000_000, 38, 19_940_000),
         (500_000_000, 40, 25_940_000), (1_000_000_000, 42, 35_940_000),
         (float("inf"), 45, 65_940_000)]
    for cap, rate, ded in T:
        if income <= cap:
            return rate, ded
    return 45, 65_940_000


def acq_detail(price, over85, malso_cnt=0, chae=0):
    """취등록세 상세(취득세+농특세+교육세+말소+인지+채권). price=낙찰가(원)."""
    eok = price / 1e8
    base = 1.0 if eok <= 6 else (eok * 2 / 3 - 3 if eok <= 9 else 3.0)   # 6~9억 금액 따라 1~3%
    base = round(base * 100) / 100
    acq = round(price * base / 100)                       # 취득세
    nong = round(price * 0.2 / 100) if over85 else 0      # 농특세(85㎡초과 0.2%)
    edu = round(acq * 0.1)                                # 지방교육세=취득세×10%
    malso = (malso_cnt or 0) * 11200                      # 말소등록세=건수×(7,200+인지4,000)
    inji = 0 if price <= 1e8 else (150000 if price <= 1e9 else 350000)   # 인지세
    chae = chae or 0
    return {"base": base, "acq": acq, "nong": nong, "edu": edu, "malso": malso,
            "inji": inji, "chae": chae, "total": acq + nong + edu + malso + inji + chae}


def calc(bid, sell, appraisal, deposit, exclusive_area=0, malso_cnt=0,
         lim_appr=60, lim_bid=80, int_r=5.0, pre_r=0.5, months=3,
         law=200000, mgmt=0, evict=0, repair=0, broker=0, etc=0, chae=0):
    """단기매도 순수익 계산. 모든 금액 원 단위. 반환 dict(원 단위)."""
    over85 = (exclusive_area or 0) > 85
    diff = sell - bid                                     # 차익(매도−낙찰)
    ad = acq_detail(bid, over85, malso_cnt, chae)
    acq = ad["total"]
    loan_appr = appraisal * lim_appr / 100
    loan_bid = bid * lim_bid / 100
    use_appr = appraisal > 0 and loan_appr < loan_bid     # 감정가 있으면 둘 중 낮은 금액
    loan = loan_appr if use_appr else loan_bid
    m_int = loan * int_r / 100 / 12                       # 월이자
    pre_int = loan * pre_r / 100                          # 중도상환이자
    hold_int = months * m_int                             # 보유기간이자
    inc = max(0, diff)
    rate, ded = ss_tax(inc)
    tax_gross = inc * rate / 100
    tax_net = max(0, tax_gross - ded)                     # 종소세(누진공제 반영)
    costs = pre_int + hold_int + acq + law + mgmt + evict + repair + broker + etc
    net = diff - costs - tax_net                          # 순수익
    capital = bid - loan                                  # 대출외 자본금
    transfer = capital - deposit                          # 소유권이전 필요금(법무제외)
    total = deposit + transfer + (costs + tax_gross)      # 총 필요금액(투자금)
    yld = (net / capital * 100) if capital > 0 else None  # 자본 대비 수익률
    return {"bid": bid, "sell": sell, "diff": diff, "loan": round(loan), "use_appr": use_appr,
            "m_int": round(m_int), "pre_int": round(pre_int), "hold_int": round(hold_int),
            "acq": acq, "tax_rate": rate, "tax_net": round(tax_net), "net": round(net),
            "capital": round(capital), "transfer": round(transfer), "total": round(total),
            "yld": (round(yld, 1) if yld is not None else None), "over85": over85, "months": months}


def _won(n):
    """원 → 억/만원 한글(예: 653,000,000 → '6억 5,300만원')."""
    n = int(round(n or 0))
    sign = "-" if n < 0 else ""
    n = abs(n)
    eok, man = n // 100_000_000, (n % 100_000_000) // 10000
    if eok and man:
        return f"{sign}{eok}억 {man:,}만원"
    if eok:
        return f"{sign}{eok}억원"
    if man:
        return f"{sign}{man:,}만원"
    return f"{sign}{n:,}원"


def format_lines(r):
    """calc() 결과 → 카카오 텍스트용 지출·순수익 여러 줄(list[str])."""
    lines = [
        f"· 낙찰가 {_won(r['bid'])}",
        f"· 매도가(시세) {_won(r['sell'])}",
        f"· 차익 {_won(r['diff'])}",
        f"· 대출 {_won(r['loan'])} ({'감정60%' if r['use_appr'] else '낙찰80%'} 낮은쪽)",
        f"· 이자(중도상환+{r['months']}개월) {_won(r['pre_int'] + r['hold_int'])}",
        f"· 취등록세 {_won(r['acq'])}",
        f"· 종소세({r['tax_rate']}%) {_won(r['tax_net'])}",
        f"· 순수익 {_won(r['net'])}" + (f" (자본대비 {r['yld']}%)" if r['yld'] is not None else ""),
        f"· 총 필요금액 {_won(r['total'])}",
    ]
    if r["over85"]:
        lines.append("· ※85㎡초과: 건물분 부가세 별도 확인")
    return lines
