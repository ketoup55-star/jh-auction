# -*- coding: utf-8 -*-
"""공매 매수판정 사전계산 워머 — 경매 buy_grade 컬럼 사전계산 패턴을 공매에 이식.

공매 목록(gongmae.html)이 행별로 /gongmae/buy_grade·/gongmae/villa_est 를 라이브
동시호출(콜드 ~5.5초/건)해 느리던 것을, 경매처럼 **사전계산→컬럼저장→읽기**로 바꾼다.

동작:
  1) gongmae_items 에서 매수판정 대상(아파트/오피스텔/다세대/연립/빌라/도시형) 순회.
  2) 각 물건을 **로컬 엔드포인트** GET http://127.0.0.1:4011/gongmae/buy_grade?mng=&cdtn=
     로 계산(기존 로직 재사용 → gm_grade/gm_nearby/gm_apt/gm_enrich 캐시도 함께 채워짐).
  3) 결과(grade·sise·profit·reason)를 psycopg 로 gongmae_items 컬럼에 UPDATE.

특징:
  - resumable: buy_grade 이미 있으면 스킵( --force 로 재계산).
  - rate limit 배려: 동시 3(--workers), 요청 사이 간격(--sleep), 온비드/지오코딩 폭주 방지.
  - 진행 로그(처리수/속도/ETA/등급분포).
  - --sample N: 앞 N건만(검증용). 전량은 백그라운드로 돌릴 것(수시간).

로컬(4011 서버 가동 중)에서 실행 → 계산은 서버가, 저장은 Supabase(api_cache + gongmae_items).
경매 워머와 동일 패턴(로컬 계산 → 클라우드 저장, 클라우드 OOM 무관).

사용:
  python warm_gongmae_grade.py --sample 30            # 검증(앞 30건)
  python warm_gongmae_grade.py                        # 전량(미워밍만)
  python warm_gongmae_grade.py --force                # 전량 재계산
  python warm_gongmae_grade.py --workers 4 --sleep 0.5
"""
import argparse
import os
import sys
import threading
import time
import concurrent.futures as cf

import httpx
import psycopg

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_ROOT, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

DBURL = os.environ["SUPABASE_DB_URL"]
BASE = os.environ.get("SELF_BASE", "http://127.0.0.1:4011").rstrip("/")

# 매수판정 대상 용도(main.py _is_villa_usage + 아파트/오피스텔과 1:1)
_USAGE_RE = "(아파트|오피스텔|다세대|연립|빌라|도시형)"

_SELECT = (
    "SELECT id, manage_no, data->>'pbct_cdtn_no' AS cdtn "
    "FROM gongmae_items WHERE usage ~ %s {extra} "
    "ORDER BY bid_close ASC NULLS LAST, id"   # 목록 기본정렬(입찰마감 임박순)과 동일 → 첫 페이지부터 워밍
)

_UPDATE = (
    "UPDATE gongmae_items SET buy_grade=%s, sise=%s, profit=%s, grade_reason=%s "
    "WHERE id=%s"
)

# 진행 통계(스레드 공유)
_lock = threading.Lock()
_stat = {"done": 0, "ok": 0, "err": 0, "skip": 0,
         "매수양호": 0, "매수검토": 0, "매수금지": 0, "미적용": 0}


def _fetch_targets(conn, force: bool, limit):
    extra = "" if force else "AND buy_grade IS NULL"
    q = _SELECT.format(extra=extra)
    rows = conn.execute(q, (_USAGE_RE,)).fetchall()
    if limit:
        rows = rows[:limit]
    return rows


def _compute_one(client: httpx.Client, mng: str, cdtn):
    """로컬 buy_grade 엔드포인트 호출 → (grade, sise, profit, reason) 또는 None(에러)."""
    params = {"mng": mng}
    if cdtn:
        params["cdtn"] = cdtn
    try:
        r = client.get(BASE + "/gongmae/buy_grade", params=params, timeout=90)
        if r.status_code != 200:
            return {"_err": f"http {r.status_code}"}
        return r.json()
    except Exception as e:
        return {"_err": type(e).__name__}


def _persist(mng_conn, iid, res):
    """buy_grade 결과 dict → gongmae_items 컬럼 UPDATE. 반환 grade 라벨(통계용)."""
    if not isinstance(res, dict) or res.get("_err"):
        return None
    # 판정 대상 아님(토지·상가·단독 등) → 컬럼은 NULL 유지하되 '미적용'으로 표기 안 함(다음 실행서 재시도되게 둠)
    if not res.get("applicable"):
        return "미적용"
    grade = res.get("grade")
    sise = res.get("sise")
    profit = res.get("profit")
    reason = res.get("reason")
    with mng_conn["lock"]:
        with mng_conn["conn"].cursor() as cur:
            cur.execute(_UPDATE, (grade, sise, profit, reason, iid))
        mng_conn["conn"].commit()
    return grade or "미적용"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="앞 N건만(검증용). 0=전량")
    ap.add_argument("--force", action="store_true", help="buy_grade 이미 있어도 재계산")
    ap.add_argument("--workers", type=int, default=3, help="동시 요청 수(온비드 rate limit 배려·기본 3)")
    ap.add_argument("--sleep", type=float, default=0.3, help="각 요청 시작 전 간격(초)")
    args = ap.parse_args()

    conn = psycopg.connect(DBURL, prepare_threshold=None, connect_timeout=20, autocommit=False)
    targets = _fetch_targets(conn, args.force, args.sample or None)
    total = len(targets)
    print(f"[워머] 대상 {total}건 (force={args.force}, sample={args.sample or '전량'}, "
          f"workers={args.workers}, sleep={args.sleep}s)", flush=True)
    if not total:
        print("처리할 물건 없음(이미 전량 워밍됨). --force 로 재계산 가능.", flush=True)
        conn.close()
        return

    # 저장 커넥션은 별도 락으로 직렬화(psycopg connection은 thread-safe 아님)
    save = {"conn": psycopg.connect(DBURL, prepare_threshold=None, connect_timeout=20,
                                    autocommit=False),
            "lock": threading.Lock()}
    client = httpx.Client(timeout=90)
    t0 = time.time()

    def _work(row):
        iid, mng, cdtn = row[0], row[1], row[2]
        time.sleep(args.sleep)   # 요청 분산(온비드/지오코딩 rate limit)
        res = _compute_one(client, mng, cdtn)
        label = None
        try:
            label = _persist(save, iid, res)
        except Exception as e:
            res = {"_err": "persist:" + type(e).__name__}
        with _lock:
            _stat["done"] += 1
            if isinstance(res, dict) and res.get("_err"):
                _stat["err"] += 1
            else:
                _stat["ok"] += 1
                if label in _stat:
                    _stat[label] += 1
            d = _stat["done"]
            if d % 10 == 0 or d == total:
                el = time.time() - t0
                rate = d / el if el else 0
                eta = (total - d) / rate if rate else 0
                print(f"  진행 {d}/{total} | ok {_stat['ok']} err {_stat['err']} | "
                      f"양호 {_stat['매수양호']} 검토 {_stat['매수검토']} 금지 {_stat['매수금지']} "
                      f"미적용 {_stat['미적용']} | {rate*60:.0f}건/분 | ETA {eta/60:.0f}분",
                      flush=True)
        return iid

    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        list(ex.map(_work, targets))

    client.close()
    save["conn"].close()
    conn.close()
    el = time.time() - t0
    print(f"\n[완료] {_stat['done']}건 처리, {el/60:.1f}분", flush=True)
    print(f"  분포 → 매수양호 {_stat['매수양호']} · 매수검토 {_stat['매수검토']} · "
          f"매수금지 {_stat['매수금지']} · 미적용 {_stat['미적용']} · 오류 {_stat['err']}", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
