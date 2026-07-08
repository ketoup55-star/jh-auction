"""경매/공매 차량외 매물 ↔ 엔카(중고차 Neon DB) 동일차량 매칭.

매칭 6조건(사용자 정의):
  ① 브랜드  ② 차종(모델)  ③ 연식  ④ 사용연료  ⑤ 배기량  ⑥ 주행거리 ±5,000km

데이터 현실 반영:
  - 경매 차량의 '제조사'는 거의 비어 있고 '차종'은 '승용 자동'(분류)임.
    → 모델은 '차명'(QM6·SM6·산타페 등)으로 매칭. 모델이 브랜드를 함의하므로 브랜드도 사실상 일치.
  - 엔카에 배기량(cc) 컬럼이 없어 등급(badge)의 배기량(L)으로 근사 매칭.
  - 배기량·주행거리·연료는 경매측 정보가 없으면 그 조건은 생략(있는 정보로만 매칭).
  - 연식은 연도(YYYY) 일치(엔카 year=YYYYMM).
"""

from __future__ import annotations

import concurrent.futures as _cf
import json
import os
import re

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

# 차명 첫 토큰이 '브랜드'면 진짜 모델은 그 뒤(수입차: 'BMW 528i'→528i). 한글·영문 모두.
_BRANDS = {
    "현대", "기아", "제네시스", "genesis", "르노", "르노삼성", "르노코리아", "삼성", "renault",
    "쉐보레", "chevrolet", "지엠대우", "대우", "쌍용", "kg모빌리티", "케이지모빌리티", "ssangyong",
    "bmw", "벤츠", "mercedes", "mercedes-benz", "benz", "mercedes-amg", "amg", "mini", "미니",
    "아우디", "audi", "폭스바겐", "volkswagen", "vw", "볼보", "volvo", "도요타", "토요타", "toyota",
    "렉서스", "lexus", "혼다", "honda", "닛산", "nissan", "인피니티", "infiniti", "아큐라", "acura",
    "포드", "ford", "캐딜락", "cadillac", "지프", "짚", "jeep", "크라이슬러", "chrysler", "gmc", "hummer",
    "포르쉐", "porsche", "재규어", "jaguar", "랜드로버", "land-rover", "landrover",
    "테슬라", "tesla", "푸조", "peugeot", "시트로엥", "citroen", "링컨", "lincoln", "마세라티",
    "maserati", "벤틀리", "bentley", "람보르기니", "페라리", "ferrari", "폴스타", "polestar",
    "gm", "홀덴", "holden", "스바루", "subaru", "마쯔다", "mazda", "사브", "saab", "피아트", "fiat",
}
# 모델명 끝에 붙는 트림·파워트레인 토큰(모델 핵심만 남기려고 제거)
_TRIM_RE = re.compile(
    r"(하이브리드|hybrid|phev|hev|디젤|diesel|가솔린|gasoline|터보|turbo|"
    r"4matic|콰트로|quattro|xdrive|awd|2wd|4wd|e-?tech).*$", re.I)
# 영문↔한글·철자 차이 별칭(키 소문자). 같은 차인데 엔카 표기가 다른 경우. 점진 확장.
_ALIASES = {
    "arkana": ["아르카나", "XM3"], "아르카나": ["아르카나", "XM3", "ARKANA"], "xm3": ["XM3", "아르카나"],
    "camry": ["캠리"], "캠리": ["캠리", "Camry"], "arteon": ["아테온"], "아테온": ["아테온", "Arteon"],
    "산타페": ["싼타페"], "싼타페": ["싼타페", "산타페"], "santafe": ["싼타페", "산타페"],
    "sienna": ["시에나"], "시에나": ["시에나", "Sienna"], "jetta": ["제타"], "제타": ["제타", "Jetta"],
    "koleos": ["콜레오스"], "tucson": ["투싼"], "kona": ["코나"], "sonata": ["쏘나타", "소나타"],
    "grandeur": ["그랜저"], "avante": ["아반떼"], "sorento": ["쏘렌토", "소렌토"], "tivoli": ["티볼리"],
    # 수입차 영문명 → 엔카 한글표기(전수감사로 엔카 실재 검증한 것만)
    "cooper": ["쿠퍼"], "countryman": ["컨트리맨"], "explorer": ["익스플로러"], "tiguan": ["티구안"],
    "aviator": ["에비에이터"], "bolt": ["볼트"], "golf": ["골프"], "altima": ["알티마"], "maxima": ["맥시마"],
    "impala": ["임팔라"], "passat": ["파사트"], "mustang": ["머스탱"], "bronco": ["브롱코"], "avalon": ["아발론"],
    "corsair": ["코세어"], "maybach": ["마이바흐"], "mercedes-maybach": ["마이바흐"], "e-tron": ["e-트론"],
    "코란도c": ["코란도"], "town-continental": ["컨티넨탈"],
    # 크롤 오타·철자차이 보정(엔카 표준표기)
    "티블리": ["티볼리"], "그랜져": ["그랜저"], "펠리세이드": ["팰리세이드"], "마칸s": ["마칸"],
    "짚그랜드체로키": ["체로키"], "그랜드체로키": ["체로키"],
}


def _fuel_cond(s) -> tuple[str, list] | None:
    """경매 연료표기 → 엔카 fuel_type 매칭 SQL(엔카 어휘: 가솔린/디젤/전기/'가솔린+전기'(하이브리드)/'LPG(일반인 구매)' 등)."""
    t = re.sub(r"\s+", "", str(s or "")).lower()
    if not t:
        return None
    if "하이브리드" in t or ("전기" in t and ("가솔린" in t or "휘발유" in t or "디젤" in t or "경유" in t)):
        return ("fuel_type LIKE %s", ["%+전기%"])          # 가솔린+전기/디젤+전기 등
    if t in ("전기", "ev"):
        return ("fuel_type = %s", ["전기"])
    if "lpg" in t or "엘피지" in t or "엘피아이" in t:
        return ("fuel_type LIKE %s", ["%LPG%"])
    if "디젤" in t or "경유" in t:
        return ("fuel_type = %s", ["디젤"])
    if "가솔린" in t or "휘발유" in t:
        return ("fuel_type = %s", ["가솔린"])
    return None


def _dsn() -> str:
    return os.environ.get("ENCAR_DATABASE_URL", "")


def _conn():
    return psycopg.connect(_dsn(), row_factory=dict_row)


def _num(s) -> int | None:
    if not s:
        return None
    m = re.search(r"\d[\d,]*", str(s))
    return int(m.group(0).replace(",", "")) if m else None


def _year4(s) -> int | None:
    m = re.search(r"(19|20)\d{2}", str(s or ""))
    return int(m.group(0)) if m else None


def _strip_brand_prefix(tok: str) -> str:
    """한글 브랜드가 모델에 글자로 붙은 경우 제거(예: '볼보S60D3'→'S60D3', '그랜저'는 모델이라 유지)."""
    for b in _BRANDS:
        if not re.match(r"^[가-힣]", b):
            continue
        if tok.startswith(b) and len(tok) > len(b) and re.match(r"[A-Za-z0-9]", tok[len(b):]):
            return tok[len(b):]
    return tok


def _strip_brand_suffix(tok: str) -> str:
    """모델 토큰 끝에 글자로 붙은 한글 브랜드 제거(예: '200벤츠'→'200', '그랜저'는 모델이라 유지).
    크롤/감정서에서 제조사가 모델 꼬리에 붙어 'E 200벤츠'처럼 들어오는 오염 보정."""
    for b in _BRANDS:
        if not re.match(r"^[가-힣]", b):
            continue
        if tok.endswith(b) and len(tok) > len(b) and re.search(r"[A-Za-z0-9]$", tok[:-len(b)]):
            return tok[:-len(b)]
    return tok


def _clean_model(t: str) -> str:
    """모델 토큰에서 트림·배기량 꼬리 제거: 'K7하이브리드'→'K7', '그랜저3.6'→'그랜저', 'S60D3'→'S60'."""
    t = _TRIM_RE.sub("", t).strip(" -")
    m = re.match(r"^([가-힣]+)\d", t)          # 한글모델+숫자(그랜저3.6)
    if m:
        return m.group(1)
    m = re.match(r"^([A-Za-z]+\d+)[A-Za-z]", t)  # 영문+숫자+꼬리(S60D3→S60)
    if m:
        return m.group(1)
    return t


def _model_terms(name: str) -> list[str]:
    """차명 → 엔카 검색 후보 모델 토큰들(괄호 영문병기·수입차 브랜드선행·트림붙음·별칭 처리).
    다른 5조건(연식·연료·주행·배기량)이 정밀도를 잡아주므로 후보는 넉넉히 생성(재현율 우선)."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        return []
    terms: list[str] = []

    def add(t: str):
        t = (t or "").strip(" -")
        if len(t) >= 2 and t.lower() not in _BRANDS and t not in terms:
            terms.append(t)

    for m in re.findall(r"\(([^)]+)\)", name):     # 괄호 속 영문병기(투싼(TUCSON)→TUCSON)
        if re.search(r"[A-Za-z0-9]", m):
            add(m.strip())
    base = re.sub(r"\([^)]*\)", " ", name)         # 괄호 제거 → 투싼
    base = re.sub(r"\s+", " ", base).strip()
    toks = [_strip_brand_suffix(_strip_brand_prefix(t)) for t in base.split(" ")]
    toks = [t for t in toks if t]
    merged, i = [], 0                               # 'E 200'→'E200'(짧은 영문+숫자 결합)
    while i < len(toks):
        if (i + 1 < len(toks) and re.fullmatch(r"[A-Za-z]{1,2}", toks[i])
                and re.fullmatch(r"\d{2,4}", toks[i + 1])):
            merged.append(toks[i] + toks[i + 1]); i += 2
        else:
            merged.append(toks[i]); i += 1
    model_toks = [t for t in merged if t.lower() not in _BRANDS]
    if model_toks:
        add(model_toks[0])                          # 첫 모델토큰(브랜드 제외)
        add(_clean_model(model_toks[0]))            # 트림 제거판
    if not terms and base:                          # 브랜드만 있는 차명(제네시스(GENESIS) 등)도 검색
        b0 = base.split(" ")[0]
        if len(b0) >= 2:
            terms.append(b0)
    for t in list(terms):                           # 별칭(영문↔한글·철자)
        for a in _ALIASES.get(t.lower(), []):
            add(a)
    return terms[:8]


def _liters(text: str) -> list[float]:
    """badge/badge_detail 문자열에서 배기량(L) 후보 추출(예: '디젤 2.2 2WD' → [2.2])."""
    out = []
    for m in re.finditer(r"(?<!\d)(\d\.\d)(?!\d)", text or ""):
        try:
            v = float(m.group(1))
            if 0.6 <= v <= 8.0:           # 배기량(L) 범위만(연식/기타 숫자 배제)
                out.append(v)
        except ValueError:
            pass
    return out


def _car(r: dict) -> dict:
    pu = r.get("photo_urls")
    if isinstance(pu, str):
        try:
            pu = json.loads(pu)
        except Exception:
            pu = []
    return {
        "id": r.get("id"),
        "manufacturer": r.get("manufacturer"), "model": r.get("model"),
        "badge": r.get("badge"), "badge_detail": r.get("badge_detail"),
        "fuel_type": r.get("fuel_type"), "year": r.get("year"),
        "mileage": r.get("mileage"), "price": r.get("price"),
        "photo": (pu or [None])[0], "detail_url": r.get("detail_url"),
    }


# ──────────────────────────────────────────────── 보험이력(카히스토리) 온디맨드
# 매물ID로 보험개발원 사고이력 조회(차량번호 불필요, 서버가 무시).
# 같은 엔카 Neon DB의 insurance_cache 에 캐싱(기본 30일) → 카드별 내차/상대차 피해·용도변경 텍스트.
_INSURANCE_URL = "https://api.encar.com/v1/readside/record/vehicle/{id}/open"
_INSURANCE_HEADERS = {
    "accept": "*/*", "origin": "https://fem.encar.com", "referer": "https://fem.encar.com/",
    "user-agent": ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36"),
}
_INS_MAX_AGE_DAYS = 30
_INS_WORKERS = 8


def _ensure_ins_cache(conn):
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS insurance_cache ("
            "car_id TEXT PRIMARY KEY, available BOOLEAN, raw JSONB, "
            "fetched_at TIMESTAMP DEFAULT now())")
    conn.commit()


def _fetch_ins_api(car_id: str):
    """엔카 보험이력 1건 호출 → (available, raw). DB 미접근."""
    try:
        r = httpx.get(_INSURANCE_URL.format(id=car_id), headers=_INSURANCE_HEADERS, timeout=20)
        if r.status_code == 200:
            return True, r.json()
    except Exception:
        pass
    return False, None


def _won_man(won) -> int:
    return round((won or 0) / 10000)


def _parse_ins(raw, available) -> dict:
    """보험이력 원본 → 카드 표시용. 이력 없으면(404) 모두 '없음'."""
    if not available or not raw:
        return {"my_damage": "없음", "other_damage": "없음", "use_change": "없음",
                "insurance_available": False}
    my_c, my_w = raw.get("myAccidentCnt") or 0, raw.get("myAccidentCost") or 0
    ot_c, ot_w = raw.get("otherAccidentCnt") or 0, raw.get("otherAccidentCost") or 0
    uses = []
    if (raw.get("business") or 0) > 0:
        uses.append("영업용")
    if (raw.get("government") or 0) > 0:
        uses.append("관용")
    if (raw.get("loan") or 0) > 0:
        uses.append("대여용")
    return {
        "my_damage": f"{my_c}회·{_won_man(my_w)}만원" if my_c else "없음",
        "other_damage": f"{ot_c}회·{_won_man(ot_w)}만원" if ot_c else "없음",
        "use_change": "/".join(uses) if uses else "없음",
        "insurance_available": True,
    }


def _attach_insurance(cars: list[dict]) -> None:
    """카드 목록에 보험이력(my_damage/other_damage/use_change)을 in-place로 부착.
    캐시는 단일 쿼리 일괄조회, 미캐시분만 병렬 호출 후 일괄 캐싱."""
    ids = [str(c["id"]) for c in cars if c.get("id")]
    if not ids:
        return
    cached: dict = {}
    try:
        with _conn() as conn:
            _ensure_ins_cache(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT car_id, available, raw FROM insurance_cache "
                    "WHERE car_id = ANY(%s) AND fetched_at > now() - (%s || ' days')::interval",
                    [ids, str(_INS_MAX_AGE_DAYS)])
                for row in cur.fetchall():
                    cached[row["car_id"]] = (row["available"], row["raw"])
            missing = [i for i in ids if i not in cached]
            fetched: dict = {}
            if missing:
                with _cf.ThreadPoolExecutor(max_workers=_INS_WORKERS) as ex:
                    for cid, res in zip(missing, ex.map(_fetch_ins_api, missing)):
                        fetched[cid] = res
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO insurance_cache (car_id, available, raw, fetched_at) "
                        "VALUES (%s,%s,%s, now()) ON CONFLICT(car_id) DO UPDATE SET "
                        "available=EXCLUDED.available, raw=EXCLUDED.raw, fetched_at=now()",
                        [(cid, av, Json(raw) if raw is not None else None)
                         for cid, (av, raw) in fetched.items()])
                conn.commit()
            cached.update(fetched)
    except Exception:
        return  # 보험이력 실패해도 매물 카드는 그대로 노출
    for c in cars:
        av, raw = cached.get(str(c.get("id")), (False, None))
        c.update(_parse_ins(raw, av))


def match_vehicle(fields: dict) -> dict:
    """차량 파싱 필드(dict label→value) → 엔카 동일차량 매칭 결과(가격순 + 평균)."""
    if not _dsn():
        return {"available": False, "reason": "엔카 DB 미설정"}
    terms = _model_terms(fields.get("차명"))
    if not terms:
        return {"available": False, "reason": "차명(모델) 정보 없음"}
    # 일반 분류어(SUV·승용·화물 등)만 있으면 실제 모델 불명(상세문서 미수집 등) → 매칭 금지.
    #   광역 오매칭 방지: 예) 캐스퍼가 차명 'SUV'로만 잡혀 벤츠 EQE에 매칭돼 8305만 엉터리시세. (주인님 2026-07-08)
    _GENERIC = {"SUV", "RV", "승용", "승용차", "승용자동차", "승합", "승합차", "승합자동차",
                "화물", "화물차", "화물자동차", "특수", "특수자동차", "자동차", "중기", "건설기계",
                "세단", "해치백", "쿠페", "왜건", "밴", "경형", "소형", "중형", "대형",
                "지게차", "굴삭기", "트럭", "버스", "덤프", "탱크로리"}
    if all(t.upper() in {g.upper() for g in _GENERIC} for t in terms):
        return {"available": False, "reason": "차명이 일반 분류어(모델 불명) — 매칭 불가"}
    core = terms[0]
    year = _year4(fields.get("연식"))
    fcond = _fuel_cond(fields.get("사용연료"))
    cc = _num(fields.get("배기량"))
    liter = round(cc / 1000, 1) if cc else None
    km = _num(fields.get("주행거리"))

    # 모델명: 후보 토큰 중 하나라도 model/badge/badge_detail에 '단어 경계'로 포함.
    #  ※ 부분일치 금지 — 앞뒤가 한글/영문/숫자면 제외(예: 'E200'이 'CLE200'에, '레이'가 '트레일블레이저'에 잡히면 안 됨).
    applied = ["브랜드·차종(모델)"]
    ors, mparams = [], []
    for t in terms:
        pat = r"(^|[^0-9A-Za-z가-힣])" + re.escape(t) + r"($|[^0-9A-Za-z가-힣])"
        ors.append("(model ~* %s OR badge ~* %s OR badge_detail ~* %s)")
        mparams += [pat, pat, pat]
    base_where = ["(" + " OR ".join(ors) + ")"]
    base_params = list(mparams)
    if year:
        # 경매 '연식'은 연형(예:2025년형), 엔카 year는 연식(최초등록 YYYYMM, 예:202405=24/05식)이라
        # 같은 차도 1년 어긋남 → ±1년 허용(25년형은 2024~2025 등록).
        base_where.append("year >= %s AND year < %s"); base_params += [(year - 1) * 100, (year + 2) * 100]
        applied.append("연식±1")
    if km:
        base_where.append("mileage BETWEEN %s AND %s"); base_params += [max(0, km - 5000), km + 5000]
        applied.append("주행거리±5천km")

    def _run(extra_where, extra_params):
        sql = ("SELECT id,manufacturer,model,badge,badge_detail,fuel_type,year,mileage,"
               "price,photo_urls,detail_url FROM cars WHERE " +
               " AND ".join(base_where + extra_where) +
               " ORDER BY price ASC NULLS LAST LIMIT 300")
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, base_params + extra_params)
            return cur.fetchall()

    # 연료는 '엄격 적용 → 0건이면 완화' (마일드 하이브리드를 엔카가 '가솔린'으로 표기하는 등
    #  표기 체계 차이로 동일 차종이 통째로 탈락하는 것을 방지. 완화 시 그 사실을 결과에 표시).
    fuel_relaxed = False
    try:
        if fcond:
            rows = _run([fcond[0]], list(fcond[1]))
            if rows:
                applied.append("연료")
            else:
                rows = _run([], [])           # 연료 0건 → 연료 빼고 재조회
                fuel_relaxed = bool(rows)
        else:
            rows = _run([], [])
    except Exception as e:
        return {"available": False, "reason": f"엔카 조회 실패: {type(e).__name__}"}

    # ⑤ 배기량(L) 근사 매칭: 경매 배기량 있으면 '등급에 표기된 L'이 다른 것만 제외.
    #   ※ 등급에 배기량 표기가 아예 없으면(레이·모닝 등 국산 경차) 판단불가 → 탈락시키지 말고 통과.
    if liter:
        applied.append("배기량(근사)")
        def _lit_ok(r):
            ls = _liters((r.get("badge") or "") + " " + (r.get("badge_detail") or ""))
            return (not ls) or any(abs(L - liter) <= 0.1 for L in ls)
        rows = [r for r in rows if _lit_ok(r)]
    rows = rows[:60]
    cars = [_car(r) for r in rows]
    # 보험이력은 doc 캐시 바깥(엔드포인트)에서 부착한다 → 캐시된 매칭결과에도 매번 최신 반영.
    prices = [c["price"] for c in cars if c["price"]]
    avg = round(sum(prices) / len(prices)) if prices else None
    return {
        "available": True, "count": len(cars), "avg_price": avg, "applied": applied,
        "fuel_relaxed": fuel_relaxed,        # 연료조건 완화 여부(엔카 표기 상이로 0건이라 제외)
        "criteria": {"model": core, "aliases": terms[1:], "year": year,
                     "fuel": (fcond[1][0] if fcond else None), "liter": liter, "mileage_km": km},
        "cars": cars,
    }
