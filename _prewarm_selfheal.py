# -*- coding: utf-8 -*-
"""자동 예열(loop-until-dry, 자가치유) — '목록·상세에 실제 표시되는 값' 기준으로 미캐시를
   더 안 채워질 때까지 회차 반복 계산 → 캐시. 정기 자동화·수동 동일 로직.

커버리지·warm 기준 = 서빙 함수(compute=False가 값을 돌려주면 '덮임'):
  · villa_est : auction_villa_ests   (목록 빌라 시세/차익)
  · apt       : auction_apt_ests     (목록 아파트 시세/차익)  ← _apt_cache(DiskDict) 포함
  · car       : auction_encar_avgs   (목록 차량 시세)
  · nearby    : auction_nearby_trades (상세 지도/유사거래/공시가격; nearby: 캐시 available&v>=2)

수렴 보장: 계산해도 값이 안 나오는 물건(원천 데이터 없음)은 pwneg2: 마커(NEGTTL)로 캐시 →
무한 재시도 차단. 일시에러는 마커 없이 다음 회차/실행 재시도. 회차 신규 0이면 종료.
웹서버(uvicorn)와 무관 — 캐시에 직접 적재(공유). 진행률·신규수 stdout 기록.
"""
import os, sys, time, argparse, concurrent.futures as cf

for _line in open('.env', encoding='utf-8'):
    _line = _line.strip()
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ[_k.strip()] = _v.strip()

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


class _Tee:
    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s); st.flush()
            except Exception:
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


try:
    _logf = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '_selfheal_cron.log'),
                 'a', encoding='utf-8')
    sys.stdout = _Tee(sys.__stdout__, _logf)
    sys.stderr = sys.stdout
    print(f"\n===== 자동예열 시작 {time.strftime('%Y-%m-%d %H:%M:%S')} =====", flush=True)
except Exception:
    pass

from api import main as M  # noqa: E402
db = M.auction_db

WARM_WORKERS = 6           # nearby(건별) 동시성
CHUNK = 40                 # est계열 배치 크기(함수가 내부 ≤200 슬라이스/병렬)
EST_OUTER = 3              # est 청크 동시 실행(외부 3 × 내부 4 = 12워커)
MAXROUNDS = 4
NEGTTL = 3 * 86400


def _page(params):
    rows, off = [], 0
    while True:
        r = db._get('items', {**params, 'select': 'item_key', 'limit': '1000', 'offset': str(off)})
        pg = r.json() if r.status_code in (200, 206) else []
        rows += [x['item_key'] for x in pg]
        if len(pg) < 1000:
            break
        off += 1000
    return rows


def collect():
    DC = {'data_class': 'eq.현황'}            # 진행중(라이브 목록)만 — 백데이터(종결 99k)는 라이브에 안 나와 예열 불필요(6배 낭비 방지)
    villa = set()
    for pat in ('*다세대*', '*연립*', '*빌라*', '*도시형*'):
        villa |= set(_page({'usage_name': f'like.{pat}', **DC}))
    apt = set(_page({'usage_name': 'like.*아파트*', **DC}))     # 오피스텔 분리: 오피스텔은 Offi API(auction_offi_info)라 apt 예열(auction_apt_ests)서 항상 no-data로 헛돌았음
    offi = set(_page({'usage_name': 'like.*오피스텔*', **DC}))   # 오피스텔은 'offi' 카테고리로 별도 예열
    car = set(_page({'search_group': 'eq.차량외', **DC}))
    villa, apt, car, offi = list(villa), list(apt), list(car), list(offi)
    return {'villa_est': villa, 'apt': apt, 'car': car, 'nearby': list(villa), 'offi': offi}


# ── 카테고리별 서빙 함수(est 계열) ──
_EST_FN = {'villa_est': M.auction_villa_ests, 'apt': M.auction_apt_ests, 'car': M.auction_encar_avgs}


IGNORE_NEG = False    # --fresh: no-data 마커 무시하고 전부 재시도(로직 바뀐 후 재적재용)


def _neg_fresh(d, now):
    if IGNORE_NEG:
        return False
    return isinstance(d, dict) and (now - d.get('ts', 0)) < NEGTTL


def _negmap(cat, keys):
    out = {}
    for i in range(0, len(keys), 100):
        ch = keys[i:i + 100]
        out.update(db.cache_get_many(['pwneg2:' + cat + ':' + k for k in ch]))
    return out


def covered_set(cat, keys):
    """서빙 경로로 '값이 나오는' 키 집합(=목록/상세에 표시됨)."""
    ok = set()
    if cat in _EST_FN:
        fn = _EST_FN[cat]
        for i in range(0, len(keys), 120):   # ★villa_ests 캡 120 — 150이면 초과 30개가 조용히 드롭돼 값 있는데도 '미처리'로 오판(허수). apt/car(캡200)도 120은 안전
            ch = keys[i:i + 120]
            try:
                r = fn(",".join(ch), compute=False)
            except Exception:
                r = {}
            ok |= {k for k in ch if r.get(k) is not None}
    elif cat == 'offi':   # 오피스텔: offi: 캐시(v2)에 est(시세) 있으면 덮임
        for i in range(0, len(keys), 100):
            ch = keys[i:i + 100]
            rows = db.cache_get_many(['offi:' + k for k in ch])
            for k in ch:
                d = rows.get('offi:' + k)
                if isinstance(d, dict) and d.get('v') == 2 and d.get('est') is not None:
                    ok.add(k)
    else:  # nearby: 캐시 직접 검사
        for i in range(0, len(keys), 100):
            ch = keys[i:i + 100]
            rows = db.cache_get_many(['nearby:' + k for k in ch])
            for k in ch:
                d = rows.get('nearby:' + k)
                if isinstance(d, dict) and d.get('available') and d.get('v', 0) >= 2:
                    ok.add(k)
    return ok


def warm(cat, todo):
    """todo를 계산(캐시 적재). 반환: 새로 덮인 수, no-data로 마킹한 수."""
    before = covered_set(cat, todo)
    if cat in _EST_FN:
        fn = _EST_FN[cat]
        chunks = [todo[i:i + CHUNK] for i in range(0, len(todo), CHUNK)]

        def _do(ch):
            try:
                fn(",".join(ch), compute=True)   # 함수 내부에도 4워커 병렬
            except Exception:
                pass
            return len(ch)
        done = 0
        with cf.ThreadPoolExecutor(max_workers=EST_OUTER) as ex:   # 외부 EST_OUTER × 내부 4
            for n in ex.map(_do, chunks):
                done += n
                _progress(cat, done, len(todo))
    else:  # 건별(nearby/offi)
        fn_item = _safe_offi if cat == 'offi' else _safe_nearby
        done = 0
        with cf.ThreadPoolExecutor(max_workers=WARM_WORKERS) as ex:
            for _ in ex.map(fn_item, todo):
                done += 1
                if done % 50 == 0 or done == len(todo):
                    _progress(cat, done, len(todo))
    after = covered_set(cat, todo)
    new_cov = after - before
    # 계산했는데도 안 덮인 것 = 원천 데이터 없음 → no-data 마커
    still = [k for k in todo if k not in after]
    for k in still:
        try:
            db.cache_save('pwneg2:' + cat + ':' + k, {'ts': time.time(), 'reason': 'no-data'})
        except Exception:
            pass
    return len(new_cov), len(still)


def _safe_nearby(k):
    try:
        M.auction_nearby_trades(k)
    except Exception:
        pass


def _safe_offi(k):
    try:
        M.auction_offi_info(k)   # 오피스텔 시세(Offi API) 계산 + offi: 캐시 적재
    except Exception:
        pass


_last = {}


def _progress(cat, done, total):
    import time as _t
    now = _t.time()
    if now - _last.get(cat, 0) < 8 and done < total:
        return
    _last[cat] = now
    print(f"  [{cat}] {done}/{total}", flush=True)


def run_category(cat, keys):
    for rnd in range(1, MAXROUNDS + 1):
        now = time.time()
        cov = covered_set(cat, keys)
        neg = _negmap(cat, [k for k in keys if k not in cov])
        todo = [k for k in keys if k not in cov
                and not _neg_fresh(neg.get('pwneg2:' + cat + ':' + k), now)]
        print(f"[{cat}] round{rnd}: 덮임 {len(cov):,}/{len(keys):,} · 처리대상 {len(todo):,}", flush=True)
        if not todo:
            print(f"[{cat}] 수렴 완료", flush=True)
            break
        t0 = time.time()
        new_cov, marked = warm(cat, todo)
        print(f"[{cat}] round{rnd} 완료: 신규덮임 {new_cov:,}, no-data마커 {marked:,}, "
              f"{(time.time()-t0)/60:.1f}분", flush=True)
        if new_cov == 0:
            print(f"[{cat}] 더 채워지지 않음 — 종료", flush=True)
            break


def run_documents():
    """문서 예열(준공·세대·승강기 / 아파트정보 / 주변유사 / 권리분석·감정평가·명세서) — 서버 내장 함수 재사용."""
    for label, fnname in (("준공·세대·승강기", "_prewarm_briefs"),
                          ("아파트정보·실거래", "_prewarm_apt_info"),
                          ("주변 유사 실거래", "_prewarm_nearby"),
                          ("물건현황·권리분석·명세서", "_prewarm_docs")):
        fn = getattr(M, fnname, None)
        if not fn:
            print(f"[docs] {label}: 함수 없음(스킵)", flush=True)
            continue
        t0 = time.time()
        try:
            fn()
            print(f"[docs] {label}: 완료 {(time.time()-t0)/60:.1f}분", flush=True)
        except Exception as e:
            print(f"[docs] {label}: 오류 {e}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='', help='docs,villa_est,apt,car,nearby 일부(쉼표). 비우면 전체')
    ap.add_argument('--fresh', action='store_true', help='no-data 마커 무시하고 전부 재시도(로직 변경 후)')
    args = ap.parse_args()
    if args.fresh:
        global IGNORE_NEG
        IGNORE_NEG = True
        print("[옵션] --fresh: no-data 마커 무시(전부 재시도)", flush=True)
    order = ['docs', 'car', 'car_expbid', 'apt', 'expbid', 'villa_est', 'vexpbid', 'nearby', 'offi']   # apt→expbid, villa_est→vexpbid(차익 위해 순서 중요). car→car_expbid(백데이터 낙찰사례). offi=오피스텔 시세 별도
    sel = [s for s in args.only.split(',') if s] or order

    tgt = collect()
    print(f"대상: 빌라 {len(tgt['villa_est']):,} / 아파트 {len(tgt['apt']):,} / 차량 {len(tgt['car']):,}", flush=True)
    T0 = time.time()
    for cat in sel:
        if cat == 'docs':
            run_documents()
            continue
        if cat == 'expbid':
            t0 = time.time()
            try:
                n = M._prewarm_expbid(tgt['apt'])
                print(f"[expbid] 예상낙찰가: {n}건 계산 {(time.time()-t0)/60:.1f}분", flush=True)
            except Exception as e:
                print(f"[expbid] 오류 {e}", flush=True)
            continue
        if cat == 'vexpbid':
            t0 = time.time()
            try:
                n = M._prewarm_villa_expbid()
                print(f"[vexpbid] 빌라/도생 예상낙찰가: {n}건 계산 {(time.time()-t0)/60:.1f}분", flush=True)
            except Exception as e:
                print(f"[vexpbid] 오류 {e}", flush=True)
            continue
        if cat == 'car_expbid':
            t0 = time.time()
            try:
                n = M._prewarm_car_expbid()
                print(f"[car_expbid] 차량 예상낙찰가: {n}건 계산 {(time.time()-t0)/60:.1f}분", flush=True)
            except Exception as e:
                print(f"[car_expbid] 오류 {e}", flush=True)
            continue
        run_category(cat, tgt[cat])

    print("── 최종 커버리지(서빙 기준) ──", flush=True)
    for cat in sel:
        if cat in ('docs', 'expbid', 'vexpbid', 'car_expbid'):
            continue
        keys = tgt[cat]
        c = len(covered_set(cat, keys))
        print(f"  {cat}: {c:,} / {len(keys):,} ({100*c/max(len(keys),1):.1f}%)", flush=True)
    print(f"총 소요 {(time.time()-T0)/60:.1f}분", flush=True)


if __name__ == '__main__':
    main()
