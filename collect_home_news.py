# -*- coding: utf-8 -*-
"""홈 부동산 뉴스 일일 수집 — google_news_crawler(헤드리스 크롬)로 6키워드를
'최근 24h·최신순'(tbs=qdr:d,sbd:1)으로 검색하고, 각 기사에서 og:image·출처·발행일시를
추출해 **오늘(수집일) 발행분만** 골라 static/data/home_news.json 저장.

- 반드시 오늘 날짜: 발행일시 = JSON-LD datePublished 우선 → 메타 → htmldate(원본일자).
- 광고(네이버 유료·'(N원)')·중복(같은 사건, 고유명사 토큰 겹침) 제외.
- 최신순 정렬 + published(ISO KST)·has_time 저장 → 프론트가 'N시간 전' 표시.
- 오늘 기사가 적으면 그만큼만(카드 수 축소). 주인님 지정.
의존성: zendriver, trafilatura(htmldate 포함), httpx (+ Chrome). 매일 13시 Windows 작업."""
import os
import re
import sys
import json
import html as _html
from datetime import datetime, timezone, timedelta, date
from urllib.parse import quote_plus

sys.stdout.reconfigure(encoding="utf-8")
# 크롤러는 주인님 바탕화면의 단일 파일(google_news_crawler.py)을 참조(무수정)
#   ⚠️ 바탕화면이 OneDrive/로컬 어느 쪽이든 찾도록 둘 다 추가(경로 불일치 시 ModuleNotFound 방지)
for _dp in (r"C:\Users\red85\Desktop", r"C:\Users\red85\OneDrive\Desktop"):
    if os.path.isdir(_dp) and _dp not in sys.path:
        sys.path.insert(0, _dp)

import httpx
from google_news_crawler import Service as _BaseService
from htmldate import find_date

KEYWORDS = ["부동산 세금", "아파트 경매", "빌라 경매", "집값", "대출규제", "부동산 정책"]
CAND_PER_KW = 10   # 키워드당 후보 수(오늘 필터 전에 넉넉히 긁음)
KEEP_PER_KW = 3    # 키워드당 최종 유지(오늘분)
KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "static", "data", "home_news.json")
LOG = os.path.join(HERE, "static", "data", "home_news_run.log")       # 실행 이력(성공/실패·에러 append)
STATUS = os.path.join(HERE, "static", "data", "home_news_status.json")  # 마지막 실행 상태(앱/관리자 조회용)


def _log(line):
    """실행 로그를 파일에 append + stdout. 스케줄러로 돌아도 흔적이 남도록."""
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line}\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        pass


def _save_status(ok, count=0, error="", stats=""):
    """마지막 실행 상태를 JSON으로 저장 → 관리자 페이지에서 '언제/성공여부/에러' 즉시 확인."""
    try:
        os.makedirs(os.path.dirname(STATUS), exist_ok=True)
        with open(STATUS, "w", encoding="utf-8") as f:
            json.dump({"last_run": datetime.now(KST).isoformat(), "ok": bool(ok),
                       "count": count, "error": error, "stats": stats},
                      f, ensure_ascii=False, indent=1)
    except Exception:
        pass

# 무관 주제 블랙리스트 — 하나라도 있으면 즉시 제외(부동산 아닌 '경매'류: 광물·미술품·자동차·코인 등).
#   "아파트/빌라 경매" 검색이 '경매' 단어만 걸친 잡기사(광물 채굴권 경매·미술품 경매)를 반환하는 것 차단.
OFF_TERMS = [
    "광물", "채굴", "광산", "석탄", "철광", "원석", "희토류",
    "소설", "초고", "원고", "육필", "친필", "문학", "작가", "시집", "노트", "필사본",
    "미술품", "골동품", "문화재", "유물", "그림", "화가", "전시", "경매장", "옥션하우스",
    "음반", "영화", "드라마", "게임", "자동차", "중고차", "차량", "선박", "항공기",
    "코인", "가상자산", "암호화폐", "비트코인", "NFT", "가축", "농산물", "수산물", "한우",
]
_OFF_RE = re.compile("|".join(re.escape(t) for t in OFF_TERMS))

# 부동산 핵심어 — 1개 이상 반드시 있어야 통과('경매·공매·낙찰·세금·대출·시세'는 단독으론 부족).
CORE_TERMS = [
    "집값", "집 값", "아파트", "빌라", "오피스텔", "주택", "다세대", "연립", "도시형",
    "분양", "청약", "재건축", "재개발", "전세", "월세", "임대차", "전셋값", "월셋값",
    "갭투자", "역전세", "전세사기", "다주택", "무주택", "입주", "집주인", "세입자",
    "매매가", "매맷값", "주택시장", "부동산", "공시가", "종부세", "보유세", "취득세",
    "양도세", "부동산세", "규제지역", "토지거래", "토허", "주담대", "주택담보", "분양가",
    "재산세", "실수요", "청약통장", "임대주택", "부동산시장",
]
_CORE_RE = re.compile("|".join(re.escape(t) for t in CORE_TERMS))


def is_relevant(title, desc):
    """무관 주제(광물·문학·미술품·자동차·코인 등) 제외 + 부동산 핵심어 1개+ 필수.
    '경매/공매/낙찰/세금/대출' 단독으론 부족 — 광물 경매·미술품 경매 오염 방지."""
    t = (title or "") + " " + (desc or "")
    if _OFF_RE.search(t):
        return False
    return bool(_CORE_RE.search(t))


class RecentNewsService(_BaseService):
    """검색 URL에 tbs=qdr:d(최근 24h)+sbd:1(최신순) 추가 — 주인님 원본 파일 무수정."""

    def _build_search_url(self, keyword, limit):
        query = quote_plus(keyword)
        count = min(max(limit * 4, 20), 100)
        return (f"{self.SEARCH_URL}?q={query}&tbm=nws&hl=ko&gl=KR"
                f"&num={count}&tbs=qdr:d,sbd:1")


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _pub_raw(html):
    """발행일시 문자열: JSON-LD datePublished → 메타 → htmldate(원본일자)."""
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.I | re.S,
    ):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            mm = re.search(r'"datePublished"\s*:\s*"([^"]+)"', raw)
            if mm:
                return mm.group(1)
            continue
        for c in _walk(data):
            if isinstance(c, dict) and c.get("datePublished"):
                return str(c["datePublished"])
    for pat in (
        r'property=["\'](?:og:)?article:published_time["\'][^>]*content=["\']([^"\']+)',
        r'itemprop=["\']datePublished["\'][^>]*content=["\']([^"\']+)',
        r'name=["\'](?:pubdate|date|publishdate|publish_date|article:published_time)["\'][^>]*content=["\']([^"\']+)',
    ):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    try:
        hd = find_date(html, original_date=True, extensive_search=True)
        if hd:
            return hd
    except Exception:
        pass
    return None


def _jsonld_image(html):
    """JSON-LD image 추출 (AMP 기사는 og:image 없이 여기에만 있음). string/배열/ImageObject.url 대응."""
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.I | re.S,
    ):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        for c in _walk(data):
            if isinstance(c, dict) and c.get("image"):
                img = c["image"]
                if isinstance(img, str) and img.startswith("http"):
                    return img
                if isinstance(img, dict) and str(img.get("url", "")).startswith("http"):
                    return img["url"]
                if isinstance(img, list):
                    for it in img:
                        if isinstance(it, str) and it.startswith("http"):
                            return it
                        if isinstance(it, dict) and str(it.get("url", "")).startswith("http"):
                            return it["url"]
    return None


def _parse_dt(s):
    """(KST datetime, has_time) 반환. 실패 시 (None, False)."""
    if not s:
        return None, False
    s = s.strip()
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return datetime.fromisoformat(s).replace(tzinfo=KST), False
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST), True
    except Exception:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        if m:
            try:
                return datetime.fromisoformat(m.group(1)).replace(tzinfo=KST), False
            except Exception:
                pass
    return None, False


def article_meta(url, cl):
    """기사 페이지 1회 fetch → (og:image, 출처, 발행 raw, 본문요약)."""
    img = site = pub = desc = None
    try:
        r = cl.get(url, timeout=12, follow_redirects=True)
        h = r.text
        for pat in (
            r'property=["\']og:image["\'][^>]*content=["\']([^"\']+)',
            r'property=["\']og:image:url["\'][^>]*content=["\']([^"\']+)',
            r'(?:name|property)=["\']twitter:image(?::src)?["\'][^>]*content=["\']([^"\']+)',
            r'content=["\']([^"\']+)["\'][^>]*property=["\']og:image',
        ):
            m = re.search(pat, h, re.I)
            if m and m.group(1).startswith("http"):
                img = _html.unescape(m.group(1))
                break
        if not img:                          # AMP 기사 등 og:image 없으면 JSON-LD image 폴백
            img = _jsonld_image(h)
        m = re.search(r'property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)', h, re.I)
        if m:
            site = m.group(1).strip()
        pub = _pub_raw(h)
        for pat in (r'property=["\']og:description["\'][^>]*content=["\']([^"\']+)',
                    r'name=["\']description["\'][^>]*content=["\']([^"\']+)'):
            m = re.search(pat, h, re.I)
            if m:
                desc = _html.unescape(m.group(1))
                break
    except Exception:
        pass
    return img, site, pub, desc


def host_of(url):
    try:
        return re.sub(r"^www\.", "", httpx.URL(url).host or "")
    except Exception:
        return ""


def clean_source(s, link):
    """og:site_name 정리 — '중부일보 - 경기·인천...' → '중부일보'."""
    s = (s or "").strip()
    s = re.split(r"\s*[-|]\s*", s)[0].strip()
    s = _html.unescape(s)
    return s or host_of(link)


def is_ad(link, title):
    h = host_of(link)
    if "premium.naver" in h or "blog.naver" in h or "cafe.naver" in h:
        return True
    if re.search(r"\(\s*\d[\d,]*\s*원\s*\)", title):          # (2,900원)
        return True
    if re.search(r"(광고|스폰서|sponsored)", title, re.I):
        return True
    return False


def _tokens(t):
    t = re.sub(r"[·\-|,\[\]\"'“”…()]", " ", t)
    ws = [re.sub(r"[^가-힣0-9a-zA-Z]", "", w) for w in t.split()]
    return set(w for w in ws if len(w) >= 2)


def is_dupe(title, kept_titles):
    """같은 사건 중복 — 고유명사 토큰 2개+ 겹치고 overlap>=0.5."""
    a = _tokens(title)
    if not a:
        return False
    for kt in kept_titles:
        b = _tokens(kt)
        if not b:
            continue
        shared = a & b
        if len(shared) >= 2 and len(shared) / min(len(a), len(b)) >= 0.5:
            return True
    return False


def main():
    svc = RecentNewsService()
    cl = httpx.Client(
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=15, follow_redirects=True,
    )
    today = date.today()
    news, seen_links, kept_titles = [], set(), []
    n_cand = n_today = n_ad = n_off = n_dup = 0
    for kw in KEYWORDS:
        try:
            items = svc.search_news(kw, CAND_PER_KW)
        except Exception as e:
            print(f"{kw}: 검색 오류 {type(e).__name__} {e}")
            items = []
        kept = 0
        for it in items:
            if kept >= KEEP_PER_KW:
                break
            link = (it.get("link") or "").strip()
            title = (it.get("title") or "").strip()
            if not link or not title or link in seen_links:
                continue
            seen_links.add(link)
            n_cand += 1
            if is_ad(link, title):                 # 광고: fetch 전 컷
                n_ad += 1
                continue
            img, site, pub_raw, desc = article_meta(link, cl)
            dt, has_time = _parse_dt(pub_raw)
            if dt is None or dt.date() != today:   # ★ 반드시 오늘 날짜
                continue
            n_today += 1
            if not is_relevant(title, desc):       # ★ 부동산 투자 무관 기사 제외
                n_off += 1
                continue
            if is_dupe(title, kept_titles):        # 같은 사건 중복
                n_dup += 1
                continue
            kept_titles.append(title)
            news.append({
                "keyword": kw,
                "title": title,
                "link": link,
                "image": img,
                "source": clean_source(site, link),
                "published": dt.isoformat(),
                "has_time": has_time,
                "ts": dt.timestamp(),
            })
            kept += 1
        print(f"{kw}: {kept}건")
    news.sort(key=lambda x: x.get("ts", 0), reverse=True)   # 최신순
    for x in news:
        x.pop("ts", None)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"items": news, "count": len(news), "date": today.isoformat()},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    imgs = sum(1 for x in news if x.get("image"))
    _log(f"저장: {OUT}")
    _log(f"  후보 {n_cand} · 오늘 {n_today} · 광고제외 {n_ad} · 무관제외 {n_off} · 중복제외 {n_dup}")
    _log(f"  최종 {len(news)}건 · 썸네일 {imgs}개")
    _save_status(True, len(news),
                 stats=f"후보 {n_cand}·오늘 {n_today}·최종 {len(news)}·썸네일 {imgs}")
    if len(news) == 0:                                   # 0건이면 성공이어도 경고(오늘 기사 못 찾음)
        _log("⚠️ 최종 0건 — 오늘 발행 기사를 못 찾았거나 검색 실패 가능")


if __name__ == "__main__":
    import traceback
    _log("─────── 수집 시작 ───────")
    try:
        main()
        _log("─────── 수집 완료 ───────")
    except Exception as e:
        _log(f"❌❌ 실패: {type(e).__name__}: {e}")
        _log(traceback.format_exc().strip())
        _save_status(False, error=f"{type(e).__name__}: {e}")
        sys.exit(1)
