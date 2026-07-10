# -*- coding: utf-8 -*-
"""로컬 캐시(_localcache.db, synced=0) → Supabase api_cache 동기화(write-behind flush).
크롤러 종료 후(Supabase 한가할 때) 작업 예약으로 1회 실행. 'item:'키(물건 읽기캐시)는 제외.
결과는 flush_localcache.log에 기록. 실패분은 synced=0으로 남아 다음 실행 때 재시도(데이터 무손실)."""
import os
import re
import sys
import time
import json

sys.stdout.reconfigure(encoding="utf-8")
_HERE = os.path.dirname(os.path.abspath(__file__))
_LOG = os.path.join(_HERE, "flush_localcache.log")


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
    print(line)
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main():
    # .env 로드
    env = {}
    try:
        for ln in open(os.path.join(_HERE, ".env"), encoding="utf-8"):
            m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)", ln)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
                os.environ.setdefault(m.group(1), env[m.group(1)])
    except Exception as e:
        log("‼ .env 로드 실패: %s" % e)

    import httpx
    from auction_analysis.local_cache import LocalCache

    url = os.environ.get("SUPABASE_URL", "https://jakwbngokvlzehpjiozh.supabase.co").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
    if not key:
        log("‼ Supabase 키 없음 — 중단")
        return

    lc = LocalCache()
    tot, uns = lc.counts()
    rows = [(k, v) for (k, v) in lc.unsynced(500000) if not k.startswith("item:")]
    log("flush 시작 — 로컬 총 %d건, 미동기화 %d건, 대상(item: 제외) %d건" % (tot, uns, len(rows)))
    if not rows:
        log("동기화할 항목 없음 — 종료")
        return

    headers = {"apikey": key, "Authorization": "Bearer " + key,
               "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    _EP = url + "/rest/v1/api_cache?on_conflict=cache_key"

    def _post(cl, payload):
        # PostgreSQL은 텍스트에 (널바이트) 저장 불가 → 직렬화 후 이스케이프 시퀀스 제거.
        body = json.dumps(payload, ensure_ascii=False)
        if "\\u0000" in body:
            body = body.replace("\\u0000", "")
        return cl.post(_EP, headers=headers, content=body.encode("utf-8"))

    done = []
    with httpx.Client(timeout=90) as cl:
        for i in range(0, len(rows), 100):              # 100건 배치 업서트
            chunk = rows[i:i + 100]
            payload = [{"cache_key": k, "data": v} for k, v in chunk]
            for attempt in range(5):                    # 혹시 아직 부하면 백오프 재시도
                try:
                    r = _post(cl, payload)
                    if r.status_code in (200, 201, 204):
                        done += [k for k, _ in chunk]
                        break
                    log("  배치 %d HTTP %d | 응답: %s" % (i // 100, r.status_code, r.text[:300]))
                    if r.status_code == 400:
                        # 불량 1건이 배치 99건 버리지 않게 — 한 건씩 재시도
                        ok = []
                        for k, v in chunk:
                            try:
                                rr = _post(cl, [{"cache_key": k, "data": v}])
                                if rr.status_code in (200, 201, 204):
                                    ok.append(k)
                            except Exception:
                                pass
                        done += ok
                        log("  배치 %d 400 → 개별재시도 %d/%d 성공" % (i // 100, len(ok), len(chunk)))
                        break   # 개별처리 완료 → 다음 배치로
                except Exception as e:
                    log("  배치 %d 재시도(%d): %s" % (i // 100, attempt + 1, str(e)[:50]))
                time.sleep(6 * (attempt + 1))
            if (i // 100) % 10 == 0:
                log("  진행 %d/%d" % (len(done), len(rows)))

    lc.mark_synced(done)
    log("동기화 완료: %d/%d (실패분은 synced=0 유지 → 다음 실행 재시도)" % (len(done), len(rows)))


if __name__ == "__main__":
    main()
