# -*- coding: utf-8 -*-
"""진행물건 우선 · 멀티프로세스 샤딩 예열.

고코어 VM에서 N개 프로세스를 동시에 띄워 진행물건(신건·유찰·재진행·재매각)의
 brief(여관/생숙·준공/세대) · 시세(실거래) · 예상낙찰가 · docs(권리분석/명세서/감정평가서)를
 계산해 Supabase(api_cache)에 저장한다. GIL(파이썬 PDF 파싱 1코어 제약)은 프로세스 분리로 회피.

사용:
    python prewarm_shard.py <shard_i> <total_N> [types]
      shard_i : 이 프로세스가 맡을 샤드 번호 (0 .. N-1)
      total_N : 전체 샤드 수 (= 동시에 띄우는 프로세스 수)
      types   : 쉼표구분, 생략시 all. 예) brief,expbid  /  sise,docs
                (all | brief | sise | expbid | docs)

권장 env (VM):
    DISABLE_LOCAL_CACHE=1   # 로컬 SQLite 버퍼 끔 → Supabase 직접 쓰기(프로세스 경합 방지)
    DISABLE_PREWARM=1       # import 시 백그라운드 예열 스레드가 뜨지 않게(우린 수동 실행)
    PYTHONIOENCODING=utf-8

동작은 멱등(이미 캐시된 건 건너뜀) → 2회 돌리면 1회차 실패분까지 채워짐.
"""
import os
import re
import sys
import time
import hashlib

# ── .env 로드(로컬/VM 공통) ──
_ROOT = os.path.dirname(os.path.abspath(__file__))
_envp = os.path.join(_ROOT, ".env")
if os.path.exists(_envp):
    for _line in open(_envp, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
# 예열 VM 기본값: import 시 백그라운드 스레드/로컬버퍼 억제(사용자가 안 줬으면)
os.environ.setdefault("DISABLE_PREWARM", "1")
sys.path.insert(0, _ROOT)


def _usage():
    print(__doc__)
    sys.exit(1)


if len(sys.argv) < 3:
    _usage()
try:
    SHARD = int(sys.argv[1])
    TOTAL = int(sys.argv[2])
except ValueError:
    _usage()
TYPES = set((sys.argv[3] if len(sys.argv) > 3 else "all").split(","))
DO_ALL = "all" in TYPES
WANT = lambda t: DO_ALL or (t in TYPES)   # noqa: E731

t0 = time.time()
print(f"[shard {SHARD}/{TOTAL}] import api.main …", flush=True)
from api import main as M   # noqa: E402
from auction_analysis.doc_analysis import (  # noqa: E402
    analyze_registry, analyze_appraisal, analyze_doc_summary, analyze_vehicle)


def _active_items():
    """진행물건(신건·유찰·재진행·재매각) 전체 페이징 → [(item_key, usage, group)]."""
    out, off = [], 0
    while True:
        try:
            items = M.auction_db.list_auctions(limit=200, offset=off, result_prefix="진행물건")
        except Exception as e:
            print(f"[shard {SHARD}/{TOTAL}] list_auctions 오류 off={off}: {e}", flush=True)
            time.sleep(2)
            continue
        if not items:
            break
        out += [(it["item_key"], it.get("usage") or "", it.get("group") or "")
                for it in items if it.get("item_key")]
        if len(items) < 200:
            break
        off += 200
    return out


def _mine(items):
    """안정 해시(md5)로 이 샤드 몫만. _docs_shard와 동일 방식."""
    return [(k, u, g) for (k, u, g) in items
            if int(hashlib.md5(k.encode()).hexdigest(), 16) % TOTAL == SHARD]


def _safe(fn):
    try:
        fn()
    except Exception:
        pass


_EXP_DONE = set()   # 이미 예상낙찰가 캐시된 item_key(재실행 스킵용 — compute_bg는 자체 캐시체크가 없음)


def _load_exp_done(keys):
    for pref in ("expbid", "vexpbid", "carexpbid"):
        for i in range(0, len(keys), 100):
            try:
                rows = M.auction_db.cache_get_many([pref + ":" + x for x in keys[i:i + 100]])
                for ck, v in rows.items():
                    if isinstance(v, dict):
                        _EXP_DONE.add(ck.split(":", 1)[1])
            except Exception:
                pass


def warm_one(k, u, g):
    is_car = ("차량" in g) or bool(re.search(r"승용|SUV|자동차", u))
    is_apt = bool(re.search(r"아파트|오피스텔", u))
    is_villa = bool(re.search(r"다세대|연립|빌라|도시형", u))
    is_house = bool(re.search(r"주택|농가|다가구|근린주택", u))   # 빌라식 예상낙찰가 대상
    # 1) brief — 여관/생숙·준공/세대/승강기 (전 주거/숙박/상가)
    if WANT("brief") and not is_car:
        _safe(lambda: M._get_brief(k))
    # 2) 시세(추정시세·주변 실거래)
    if WANT("sise"):
        if is_apt:
            _safe(lambda: M.auction_apt_ests(k, compute=True))
        elif is_villa:
            _safe(lambda: M.auction_villa_ests(k, compute=True))
            _safe(lambda: M.auction_nearby_trades(k))
    # 3) 예상낙찰가 — ⚠️엔드포인트(auction_*_expected_bid)는 비동기(pending, daemon 스레드)라 프로세스 종료 시 유실.
    #    반드시 동기 compute(_*_compute_bg)를 직접 호출해야 cache_save(Supabase)까지 완료됨.
    if WANT("expbid") and k not in _EXP_DONE:
        if is_apt:
            _safe(lambda: M._expbid_compute_bg(k))
        elif is_villa or is_house:
            _safe(lambda: M._villa_expbid_compute_bg(k))
        elif is_car:
            _safe(lambda: M._car_expbid_compute_bg(k))
    # 4) docs — 권리분석(등기)/감정평가서/명세서 or 차량
    if WANT("docs"):
        if is_car:
            _safe(lambda: M._cached_doc("vehicle", k, lambda: analyze_vehicle(M.auction_db, k)))
        else:
            _safe(lambda: M._cached_doc("analysis", k, lambda: analyze_registry(M.auction_db, k)))
            _safe(lambda: M._cached_doc("appraisal", k, lambda: analyze_appraisal(M.auction_db, k)))
            _safe(lambda: M._cached_doc("docsummary", k, lambda: analyze_doc_summary(M.auction_db, k)))


def main():
    items = _active_items()
    mine = _mine(items)
    print(f"[shard {SHARD}/{TOTAL}] 진행물건 {len(items)}건 · 이 샤드 {len(mine)}건 · types={sorted(TYPES)}",
          flush=True)
    # brief는 Supabase를 안 읽고 바로 계산하므로, 이미 계산된 건 스킵되게 먼저 로딩(멱등·재실행 고속)
    if (DO_ALL or WANT("brief")) and mine:
        try:
            M._load_briefs_from_db([k for k, _, _ in mine])
        except Exception:
            pass
    if (DO_ALL or WANT("expbid")) and mine:
        _load_exp_done([k for k, _, _ in mine])
        print(f"[shard {SHARD}/{TOTAL}] 예상낙찰가 기캐시 {len(_EXP_DONE)}건 스킵", flush=True)
    done = 0
    for k, u, g in mine:
        warm_one(k, u, g)
        done += 1
        if done % 50 == 0:
            el = time.time() - t0
            rate = done / el if el else 0
            eta = (len(mine) - done) / rate if rate else 0
            print(f"[shard {SHARD}/{TOTAL}] {done}/{len(mine)}  {rate:.1f}건/s  ETA {eta/60:.1f}분",
                  flush=True)
    print(f"[shard {SHARD}/{TOTAL}] DONE {done}건 · {(time.time()-t0)/60:.1f}분", flush=True)


if __name__ == "__main__":
    main()
