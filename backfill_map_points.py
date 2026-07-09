"""
경공매 지도용 좌표 백필 — map_points 테이블 적재 (병렬).

- 대상: '현재 진행중' 경매(items) + 공매(gongmae_items) 중 지도 6개 용도그룹만.
- 지오코딩: VWorld(카카오와 별개 쿼터). ThreadPool 병렬(--workers)로 대폭 단축.
- 재실행 안전(resumable): 이미 좌표 있는 (source,item_key)는 스킵.
- usage_group(6그룹)은 값만 저장 → 나중에 재조정 시 재지오코딩 불필요.

사용:
  PYTHONIOENCODING=utf-8 python backfill_map_points.py --test 8   # 병렬 검증(적재 안 함)
  PYTHONIOENCODING=utf-8 python backfill_map_points.py --workers 16
"""
from __future__ import annotations
import os, re, time, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import httpx
import psycopg

def _load_env():
    env = {}
    for line in open(os.path.join(os.path.dirname(__file__), ".env"), encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1); env[k] = v.strip()
    return env

ENV = _load_env()
DBURL = ENV["SUPABASE_DB_URL"]
VW_KEY = ENV.get("VWORLD_KEY", "")
# 연결 재사용(keep-alive) — TLS 핸드셰이크 반복 제거로 대폭 가속. httpx.Client는 스레드 공유 안전.
_HTTP = httpx.Client(timeout=12, limits=httpx.Limits(max_keepalive_connections=48, max_connections=64))
KAKAO_KEY = ENV.get("KAKAO_REST_KEY", "")
_HDR = {"Authorization": f"KakaoAK {KAKAO_KEY}"}

_VEHICLE = re.compile(r"자동차|현대|기아|르노|쉐보레|벤츠|BMW|아우디|랜드로버|G80|G90|K3|K5|K7|K8|SM6|QM6|승용|스파크|투싼|쏘나타|쏘렌토|싼타페|셀토스|스포티지|토레스|디스커버리|에쿠스|그랜저|제네시스")
def usage_group(src: str, u: str):
    u = (u or "").strip()
    if not u:
        return None
    if src == "auction" and _VEHICLE.search(u):
        return None
    if "오피스텔" in u:
        return None
    if re.search(r"토지|임야|(^|\s)전($|\s)|(^|\s)답($|\s)|대지|잡종지|과수원|목장용지|공장용지|창고용지|묘지|체육용지|주차장|도로", u):
        return None
    if "아파트" in u:
        return "아파트"
    if "도시형생활" in u or "도시형 생활" in u:
        return "도생"
    if re.search(r"다세대|연립|빌라", u):
        return "다세대연립"
    if re.search(r"숙박|여관|생활형숙박|생활숙박|콘도", u):
        return "숙박"
    if re.search(r"단독|다가구|농가|기타주거|상가주택", u):
        return "단독다가구"
    if re.search(r"근린|상가|판매시설|업무시설|사무실|상업용|문화|교육연구|근린생활", u):
        return "상가"
    if "주택" in u:
        return "단독다가구"
    return None

def _norm(a: str) -> str:
    a = re.sub(r"\([^)]*\)", " ", a or "")
    return re.sub(r"\s+", " ", a).strip()

def _variants(addr: str):
    """지오코딩 시도 순서: ①지번까지만 ②원본 ③동 단위(중심좌표). 건물명·층·호 때문에 NOT_FOUND 나는 것 구제."""
    a = _norm(addr)
    out = []
    m = re.search(r"(.+?[가-힣]{2,}(?:동|리)\s+산?\d[\d-]*)", a)   # …동/리 + 번지 까지
    if m:
        out.append(m.group(1).strip())
    out.append(a)                                                # 원본
    toks = a.split()                                             # 동/리/읍/면 단위(중심)
    for i, t in enumerate(toks):
        if i >= 2 and re.search(r"(동|리|읍|면)$", t):
            out.append(" ".join(toks[:i + 1])); break
    seen, res = set(), []
    for x in out:
        if x and x not in seen:
            seen.add(x); res.append(x)
    return res

def kakao_geocode(addr: str):
    """카카오 지오코더 단일주소 → (lat,lng). 주소검색 우선, 실패 시 키워드(건물명) 검색."""
    if not addr:
        return None
    for url, extra in (("https://dapi.kakao.com/v2/local/search/address.json", {}),
                       ("https://dapi.kakao.com/v2/local/search/keyword.json", {"size": 1})):
        try:
            r = _HTTP.get(url, params={"query": addr, **extra}, headers=_HDR)
            if r.status_code == 429:                       # 레이트리밋 — 잠깐 쉬고 스킵(재개시 재시도)
                time.sleep(0.5); return None
            if r.status_code == 200:
                d = r.json().get("documents") or []
                if d:
                    return float(d[0]["y"]), float(d[0]["x"])
        except Exception:
            pass
    return None

def geocode_addr(addr: str):
    """축약 변형들을 순서대로 지오코딩 → 첫 성공 (lat,lng)."""
    for cand in _variants(addr):
        ll = kakao_geocode(cand)
        if ll:
            return ll
    return None

def _sido_sgg(addr: str):
    toks = (addr or "").split()
    return (toks[0] if toks else ""), (toks[1] if len(toks) > 1 else "")

DDL = """
CREATE TABLE IF NOT EXISTS map_points (
  source text NOT NULL, item_key text NOT NULL,
  lat double precision, lng double precision,
  usage_group text, prop_type text, usage_name text,
  title text, address text, min_price bigint, appraisal_price bigint,
  sido text, sigungu text, bid_close text, buy_grade text,
  updated_at timestamptz DEFAULT now(),
  PRIMARY KEY (source, item_key)
);
CREATE INDEX IF NOT EXISTS idx_map_points_latlng ON map_points(lat, lng);
CREATE INDEX IF NOT EXISTS idx_map_points_src_grp ON map_points(source, usage_group);
"""
UPSERT = """
INSERT INTO map_points
 (source,item_key,lat,lng,usage_group,prop_type,usage_name,title,address,min_price,appraisal_price,sido,sigungu,bid_close,buy_grade,updated_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
ON CONFLICT (source,item_key) DO UPDATE SET
 lat=EXCLUDED.lat, lng=EXCLUDED.lng, usage_group=EXCLUDED.usage_group,
 min_price=EXCLUDED.min_price, bid_close=EXCLUDED.bid_close, updated_at=now();
"""

def rows_auction(conn):
    sql = """SELECT item_key, usage_name, coalesce(title,''), address, min_price, appraisal_price, sell_date, buy_grade
             FROM items WHERE sell_date_d >= CURRENT_DATE AND (sale_price IS NULL OR sale_price=0)
               AND address IS NOT NULL AND address<>''"""
    for r in conn.execute(sql).fetchall():
        # (src,key,usage,prop,title,addr,min,appr,bid_close,grade)
        yield ("auction", r[0], r[1], None, r[2], r[3], r[4], r[5], r[6], r[7])

def rows_gongmae(conn):
    sql = """SELECT id, usage, prop_type, coalesce(name,''), address, min_price, appraisal_price, bid_close, buy_grade
             FROM gongmae_items WHERE bid_close >= to_char(CURRENT_DATE,'YYYY-MM-DD')
               AND address IS NOT NULL AND address<>''"""
    for r in conn.execute(sql).fetchall():
        # (src,key,usage,prop,title,addr,min,appr,bid_close,grade) — auction과 동일 순서
        yield ("gongmae", r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", type=int, default=0)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    conn = psycopg.connect(DBURL, prepare_threshold=None, connect_timeout=30, autocommit=True)
    for stmt in DDL.strip().split(";"):
        if stmt.strip():
            conn.execute(stmt)
    done = set((s, k) for s, k in conn.execute(
        "SELECT source,item_key FROM map_points WHERE lat IS NOT NULL").fetchall())
    print(f"[init] 기존 좌표 {len(done)}건 스킵", flush=True)

    work = []
    for _name, gen in (("auction", rows_auction), ("gongmae", rows_gongmae)):
        for src, key, usage, prop, title, addr, mn, appr, bidc, grade in gen(conn):
            grp = usage_group(src, usage)
            if grp is None or (src, key) in done:
                continue
            work.append((src, key, grp, prop, usage, title, addr, mn, appr, bidc, grade))
    if args.test:
        work = work[:args.test]
    print(f"[plan] 지오코딩 대상 {len(work)}건 · workers={args.workers}", flush=True)

    def geo(w):
        return w, geocode_addr(w[6])

    ok = fail = 0
    batch = []
    def flush():
        # Supabase 트랜잭션 풀러(pgbouncer)는 psycopg executemany의 파이프라인 모드를 거부(pipeline aborted).
        # → 배치를 단일 다중행 INSERT 한 방으로(파이프라인 안 씀).
        nonlocal batch
        if batch and not args.test:
            row_ph = "(" + ",".join(["%s"] * 15) + ",now())"
            q = ("INSERT INTO map_points "
                 "(source,item_key,lat,lng,usage_group,prop_type,usage_name,title,address,"
                 "min_price,appraisal_price,sido,sigungu,bid_close,buy_grade,updated_at) VALUES "
                 + ",".join([row_ph] * len(batch)) +
                 " ON CONFLICT (source,item_key) DO UPDATE SET "
                 "lat=EXCLUDED.lat, lng=EXCLUDED.lng, usage_group=EXCLUDED.usage_group, "
                 "min_price=EXCLUDED.min_price, bid_close=EXCLUDED.bid_close, updated_at=now()")
            flat = [x for row in batch for x in row]
            with conn.cursor() as cur:
                cur.execute(q, flat)
        batch = []

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(geo, w) for w in work]
        for i, fu in enumerate(as_completed(futs), 1):
            w, ll = fu.result()
            if not ll:
                fail += 1
                continue
            src, key, grp, prop, usage, title, addr, mn, appr, bidc, grade = w
            lat, lng = ll
            sido, sgg = _sido_sgg(addr)
            if args.test:
                print(f"  OK [{src}/{grp}] {addr[:38]} -> {lat:.5f},{lng:.5f}", flush=True)
            else:
                batch.append((src, key, lat, lng, grp, prop, usage, title, addr, mn, appr, sido, sgg, bidc, grade))
                if len(batch) >= 200:
                    flush()
            ok += 1
            if i % 1000 == 0:
                el = time.time() - t0
                rate = i / el * 60
                rem = (len(work) - i) / (i / el) if i else 0
                print(f"[prog] {i}/{len(work)} ok={ok} fail={fail} · 분당 {rate:.0f} · 남은 ~{rem/60:.0f}분", flush=True)
    flush()
    print(f"[DONE] 대상={len(work)} ok={ok} fail={fail} · {(time.time()-t0)/60:.1f}분", flush=True)
    conn.close()

if __name__ == "__main__":
    main()
