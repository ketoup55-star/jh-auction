# -*- coding: utf-8 -*-
"""관리자 카카오 자동발송 — 뉴스/매각예정/매각완료 콘텐츠 생성 + 설정·발송이력(중복방지).
- 매각예정: 다음 매각기일(내일 이후 가장 가까운, 주말·연휴 자동 스킵) 물건. 예상낙찰가(없으면 최저가)로 단기매도 수익.
- 매각완료: 직전 매각일 물건. 실제 낙찰가로 단기매도 수익.
- 둘 다: 매수양호 + 시세있음 + 차익순, 아파트/다세대·빌라/도시형 각 2건.
- 뉴스: home_news.json 중 아직 안 보낸 것(링크 이력).
스케줄 실행·발송은 main.py(로컬 4011 서버)에서. 이 모듈은 콘텐츠·설정만."""
import os
import re
import json
import html as _html
import datetime

import httpx

from auction_analysis import shortsale as ss

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "kakao_broadcast.json")   # 설정 + 발송이력
NEWS_JSON = os.path.join(HERE, "static", "data", "home_news.json")

# 유형: (표시명, PostgREST 필터, 시세종류) — apt=아파트 계열, villa=빌라 계열
_TYPES = [
    ("아파트", {"usage_name": "ilike.*아파트*"}, "apt"),
    ("다세대·빌라", {"or": "(usage_name.ilike.*다세대*,usage_name.ilike.*연립*,usage_name.ilike.*빌라*)"}, "villa"),
    ("도시형생활주택", {"usage_name": "ilike.*도시형*"}, "villa"),
]

DEFAULTS = {
    "news":     {"room": "", "time": "13:00", "on": False, "sent_links": [], "last": "", "openai_key": "", "openai_model": "gpt-4o-mini"},
    "upcoming": {"room": "", "time": "10:00", "on": False, "sent_date": "", "last": ""},
    "sold":     {"room": "", "time": "06:00", "on": False, "sent_date": "", "last": ""},
}


# ───────── 설정/이력 저장 ─────────
def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        s = {}
    for k, v in DEFAULTS.items():
        s.setdefault(k, dict(v))
        for kk, vv in v.items():
            s[k].setdefault(kk, vv)
    return s


def save_state(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


# ───────── 유틸 ─────────
def _d10(sd):
    m = re.match(r"(\d{4}-\d{2}-\d{2})", sd or "")
    return m.group(1) if m else None


def _dow(date_str):
    """'2026-07-06' → '2026-07-06(월)'."""
    try:
        d = datetime.date.fromisoformat(date_str)
        return f"{date_str}({'월화수목금토일'[d.weekday()]})"
    except Exception:
        return date_str


def _dep(s):
    if not s:
        return 0
    m = re.search(r"[\d,]+", re.sub(r"\([^)]*\)", "", str(s)))   # 괄호(비율) 제거 후 숫자만('원'·텍스트 제외)
    return int(m.group(0).replace(",", "")) if m else 0


def _area(s):
    m = re.search(r"[\d.]+", str(s or ""))
    return float(m.group(0)) if m else 0


def _won(n):
    return ss._won(n)


# ───────── 날짜(매각기일 데이터로 영업일 판단) ─────────
def _items(params, tries=4):
    """items 조회 + Supabase 타임아웃(크롤러 부하) 재시도 → 항상 list 반환(실패 시 []).
    지금발송·자동발송이 크롤러 포화 순간에 '매각일 못 찾음'·빈 발송으로 실패하던 것 방지."""
    from api import main as M
    import time as _t
    for i in range(tries):
        try:
            r = M.auction_db._get("items", params)
            if r.status_code in (200, 206):
                j = r.json()
                if isinstance(j, list):
                    return j
        except Exception:
            pass
        if i < tries - 1:
            _t.sleep(1.2 * (i + 1))   # 1.2s→2.4s→3.6s 백오프(타임아웃 창 회피)
    return []


_RESI = ("아파트", "다세대", "연립", "빌라", "도시형")   # 주거 용도(=api.main._HERO_OR) — 앱측 필터용


def next_sale_date():
    """내일 이후 가장 가까운 매각기일(현황 주거). 인덱스 범위(sell_date_d)만으로 좁혀 받고 data_class·용도는 앱에서 필터.
    (무거운 or(용도)+data_class 를 DB에 넣으면 전체스캔 → 크롤러 부하 시 3초 statement timeout. 주인님 2026-07-08.)"""
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    rows = _items({"select": "sell_date_d,data_class,usage_name",
                   "sell_date_d": f"gte.{tomorrow}", "order": "sell_date_d.asc", "limit": "3000"})
    ds = sorted({r["sell_date_d"] for r in rows
                 if r.get("sell_date_d") and r["sell_date_d"] >= tomorrow
                 and (r.get("data_class") or "") == "현황"
                 and any(k in (r.get("usage_name") or "") for k in _RESI)})
    return ds[0] if ds else None


def prev_sale_date():
    """어제 이전 가장 가까운 매각일(매각 완료 주거)."""
    today = datetime.date.today().isoformat()
    rows = _items({"select": "sell_date_d,result,usage_name",
                   "sell_date_d": f"lt.{today}", "order": "sell_date_d.desc", "limit": "3000"})
    ds = sorted({r["sell_date_d"] for r in rows
                 if r.get("sell_date_d") and r["sell_date_d"] < today
                 and str(r.get("result") or "").startswith("매각")
                 and any(k in (r.get("usage_name") or "") for k in _RESI)}, reverse=True)
    return ds[0] if ds else None


# ───────── 물건 선정 ─────────
def _pick_type(kind, date, tparam, est_kind, n=2):
    """한 유형에서 매수양호+시세있음+차익순 상위 n. kind=upcoming(예상낙찰/최저)·sold(낙찰가)."""
    from api import main as M
    sel = "item_key,address,buy_grade,min_price,building_area,case_no,court_name,deposit,appraisal_price,thumb_url"
    params = {"select": sel + (",sale_price,result,bid_count" if kind == "sold" else ""),
              "sell_date_d": f"eq.{date}", "limit": "300"}   # 텍스트 like(풀스캔·타임아웃) → 인덱스 날짜컬럼(33배 빠름)
    params.update(tparam)
    if kind == "upcoming":
        params["data_class"] = "eq.현황"
    else:
        params["result"] = "like.매각*"
    rows = _items(params)   # 타임아웃 재시도 + 항상 list 반환(크래시·간헐실패 방지)
    good = [x for x in rows if isinstance(x, dict) and "양호" in (x.get("buy_grade") or "")]
    keys = [x["item_key"] for x in good][:150]
    if not keys:
        return []
    kj = ",".join(keys)
    if est_kind == "apt":
        ests = M.auction_apt_ests(kj, compute=False)
        exps = M.auction_expbid_batch(kj)
    else:
        ests = M.auction_villa_ests(kj, compute=False)
        exps = M.auction_vexpbid_batch(kj)
    if not isinstance(ests, dict): ests = {}   # 에러응답(dict/str)·None 방어
    if not isinstance(exps, dict): exps = {}
    out = []
    THRESH = 30000000   # 순수익(대출·이자·취득세·종소세 반영) 3천만원 이상만 발송 — 차익만 크고 실익 없는 물건 제외(주인님 지시)
    for x in good:
        k = x["item_key"]
        _e = ests.get(k); price = _e.get("price") if isinstance(_e, dict) else None
        if not price:
            continue                                 # 시세 없으면 제외(주인님 지시)
        if kind == "upcoming":
            _x = exps.get(k); eb = _x.get("expected_bid") if isinstance(_x, dict) else None
            if not eb:
                continue                              # 예상낙찰가 있는 물건만(주인님 지시)
            # 기준가 = 예상낙찰가·현재 최저입찰가 중 큰 값. 최저가 미만 낙찰 불가 →
            #   예상낙찰가가 최저보다 낮으면 그 값은 실현 불가(허수 차익) → 최저가가 실질 하한.
            mn = M._to_int(x.get("min_price")) or 0
            bid = max(eb, mn)
            bid_lbl = "예상낙찰가" if eb >= mn else "최저입찰가"
        else:
            bid = M._to_int(x.get("sale_price"))
            bid_lbl = "낙찰가"
        if not bid:
            continue
        diff = price - bid
        # 순수익 기준 필터 — 차익(price-bid)은 3천만↑이어도 대출이자·취득세·종소세 빼면 실익 거의 없는
        #   물건(순수익 241만 등)이 발송되던 것 방지(주인님 지시). ss.calc=물건상세 단타계산기와 동일 산식.
        _appr = M._to_int(x.get("appraisal_price"))
        _dp = _dep(x.get("deposit"))
        _ar = _area(x.get("building_area"))
        try:
            _fin = ss.calc(bid=bid, sell=price, appraisal=_appr, deposit=_dp, exclusive_area=_ar)
            net = _fin.get("net")                    # ss.calc net=round()=부호 있는 정수(손해면 음수). ⚠️_to_int 쓰면 음수부호 탈락→손해가 이익으로 뒤집힘
            if not isinstance(net, (int, float)):
                net = None
        except Exception:
            net = None
        if net is None or net < THRESH:
            continue                                 # 순수익 3천만원 미만(손해=음수 포함) 제외 — 매수양호+실익 있는 물건만
        out.append({"item_key": k, "addr": (x.get("address") or "").split("(")[0].strip(),
                    "thumb": x.get("thumb_url") or "",
                    "price": price, "bid": bid, "bid_lbl": bid_lbl, "diff": diff, "net": net,
                    "appraisal": _appr, "deposit": _dp,
                    "area": _ar, "bid_count": M._to_int(x.get("bid_count"))})
    out.sort(key=lambda r: r["net"], reverse=True)   # 순수익 큰 순
    return out[:n]


def _city(addr):
    """주소를 시/도 + 시·군·구 까지만(동·번지·단지명 숨김). 예: '경기도 성남시 분당구 …' → '경기도 성남시'."""
    m = re.match(r"(\S+(?:특별자치시|특별자치도|특별시|광역시|도))\s+(\S+?(?:시|군|구))", addr or "")
    if m:
        return m.group(1) + " " + m.group(2)
    toks = (addr or "").split()
    return " ".join(toks[:2]) if toks else (addr or "")


def _vwidth(s):
    """문자열 시각 폭(한글·전각 = 2, 그 외 = 1)."""
    return sum(2 if ord(c) > 0x2000 else 1 for c in s)


def _amt(n, width):
    """금액을 시각폭 기준 우측정렬(끝자리 '원' 위치 맞춤)."""
    s = _won(n)
    return " " * max(0, width - _vwidth(s)) + s


def _prop_block(p, usage="", sold=False):
    """물건 1개 → 주소(시·군·구, 용도) + 정렬된 단기매도 정보. 사건번호·부가세 문구 없음. sold=True면 '(매각완료)' 표기."""
    r = ss.calc(bid=p["bid"], sell=p["price"], appraisal=p["appraisal"],
                deposit=p["deposit"], exclusive_area=p["area"])
    loan_src = "감정60%" if r["use_appr"] else "낙찰80%"
    yld = f" (자본대비 {r['yld']}%)" if r["yld"] is not None else ""
    rows = [
        ("낙찰가", r["bid"], (f" ({p.get('bid_count') or 1}명 입찰)" if sold else "")),
        ("매도가", r["sell"], ""),
        ("차익", r["diff"], ""),
        None,
        ("대출", r["loan"], f" ({loan_src})"),
        ("이자", r["pre_int"] + r["hold_int"], f" (중도상환+{r['months']}개월)"),
        ("취득세", r["acq"], ""),
        ("종소세", r["tax_net"], f" ({r['tax_rate']}%)"),
        None,
        ("순수익", r["net"], yld),
        None,
        ("투자금", r["total"], ""),
    ]
    width = max(_vwidth(_won(x[1])) for x in rows if x)      # 금액 최대 시각폭
    _tag = "(매각완료)" if sold else "(매각예정)"
    out = [f"{_tag}📍 {_city(p['addr'])}" + (f" ({usage})" if usage else "")]
    for x in rows:
        if x is None:
            out.append("")
            continue
        lb, v, sfx = x
        label = lb + ("　" * (3 - len(lb)))                  # 2글자 라벨→전각공백 채워 3글자폭
        out.append(f"· {label} : {_amt(v, width)}{sfx}")
    return "\n".join(out)


def build_upcoming():
    """→ (date, items) or (date, None) or (None, None). items=시퀀스[{type:text|image}] — 사진→정보 번갈아."""
    date = next_sale_date()
    if not date:
        return None, None
    header = f"🏠 [{_dow(date)} 매각예정 물건]"
    items = []
    picked = False
    for name, tparam, est_kind in _TYPES:
        picks = _pick_type("upcoming", date, tparam, est_kind, 2)
        if not picks:
            continue
        if not picked:
            items.append({"type": "text", "text": header})     # 헤더 단독(맨 앞 한 번)
            picked = True
        for p in picks:
            if p.get("thumb"):
                items.append({"type": "image", "url": p["thumb"]})
            items.append({"type": "text", "text": _prop_block(p, name)})
    return (date, items) if picked else (date, None)


def build_sold():
    """→ (date, items) 시퀀스. 매각완료는 실제 낙찰가 기준(예상낙찰 필터 없음)."""
    date = prev_sale_date()
    if not date:
        return None, None
    header = f"✅ [{_dow(date)} 매각완료 물건]"
    items = []
    picked = False
    for name, tparam, est_kind in _TYPES:
        picks = _pick_type("sold", date, tparam, est_kind, 2)
        if not picks:
            continue
        if not picked:
            items.append({"type": "text", "text": header})     # 헤더 단독(맨 앞 한 번)
            picked = True
        for p in picks:
            if p.get("thumb"):
                items.append({"type": "image", "url": p["thumb"]})
            items.append({"type": "text", "text": _prop_block(p, name, sold=True)})
    return (date, items) if picked else (date, None)


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def _strip_tags(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return _html.unescape(re.sub(r"\s+", " ", s)).strip()


def _meta(htmltext, prop):
    """<meta property/name=prop content=...> 값."""
    pats = [r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\'](.*?)["\']' % re.escape(prop),
            r'<meta[^>]+content=["\'](.*?)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(prop)]
    for p in pats:
        m = re.search(p, htmltext, re.I | re.S)
        if m:
            return _html.unescape(m.group(1)).strip()
    return ""


def _fetch_article_text(url, timeout=12):
    """기사 URL → 요약용 텍스트(og:description + 본문 문단, 최대 ~2500자). 실패시 ''."""
    try:
        r = httpx.get(url, headers={"User-Agent": _UA}, timeout=timeout, follow_redirects=True)
        t = r.text
    except Exception:
        return ""
    desc = _meta(t, "og:description") or _meta(t, "description")
    ps = [_strip_tags(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", t, re.S | re.I)]
    body = " ".join(p for p in ps if len(p) > 20)
    return (desc + "\n" + body).strip()[:2500]


def _summarize_gpt(title, article_text, api_key, model="gpt-4o-mini"):
    """OpenAI Chat으로 2~3문장 한국어 요약. 키 없음·본문 없음·실패시 None."""
    if not api_key or not (article_text or "").strip():
        return None
    prompt = ("다음 부동산 뉴스 기사를 핵심만 2~3문장으로 간결하게 한국어로 요약해줘. "
              "인사말·머리말 없이 요약 본문만.\n\n제목: %s\n기사: %s" % (title, article_text))
    try:
        r = httpx.post("https://api.openai.com/v1/chat/completions",
                       headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                       json={"model": model or "gpt-4o-mini",
                             "messages": [{"role": "user", "content": prompt}],
                             "temperature": 0.3, "max_tokens": 300}, timeout=45)
        if r.status_code != 200:
            return None
        return ((r.json().get("choices") or [{}])[0].get("message", {}).get("content") or "").strip() or None
    except Exception:
        return None


def build_news(sent_links, api_key="", model="gpt-4o-mini"):
    """home_news.json 중 안 보낸 것. api_key 있으면 각 기사 GPT 요약 첨부. → (text, new_links) or (None, [])."""
    try:
        with open(NEWS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, []
    seen = set(sent_links or [])
    fresh = [x for x in data.get("items", []) if x.get("link") and x["link"] not in seen]
    if not fresh:
        return None, []
    today = datetime.date.today().isoformat()
    lines = [f"📰 [{_dow(today)} 부동산 뉴스]"]
    new_links = []
    for x in fresh[:10]:
        title = x.get("title", "")
        summary = _summarize_gpt(title, _fetch_article_text(x["link"]), api_key, model) if api_key else None
        if summary:
            lines.append("\n▪ %s\n%s\n%s" % (title, summary, x["link"]))
        else:
            lines.append("\n▪ %s\n%s" % (title, x["link"]))       # 요약 실패·키 없음시 제목+링크
        new_links.append(x["link"])
    return "\n".join(lines), new_links


def seq_to_text(items):
    """시퀀스(매각예정/완료)를 미리보기용 단일 텍스트로 — 이미지 항목은 '[물건 사진]'으로 표시."""
    out = []
    for it in items or []:
        if it.get("type") == "image":
            out.append("🖼 [물건 사진]")
        else:
            out.append(it.get("text", ""))
    return "\n\n".join(out)
