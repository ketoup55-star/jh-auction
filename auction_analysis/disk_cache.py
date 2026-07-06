"""디스크 영구 캐시(dict 호환). 서버 재시작에도 분석 결과 유지 → 콜드 재계산 방지.

사용:
    _cache = DiskDict(path)
    if k in _cache: return _cache[k]
    ...
    _cache.remember(k, out)     # 메모리+디스크(시간 디바운스 저장)
"""

from __future__ import annotations

import atexit
import json
import os
import sqlite3
import threading
import time


class DiskDict(dict):
    def __init__(self, path: str, save_every: int = 5, min_interval: float = 60.0):
        super().__init__()
        self._path = path
        self._save_every = save_every
        self._min_interval = min_interval     # 디바운스: 직전 flush 후 이 초가 지나야 재기록 — 대용량 JSON(예: 152MB) 전체-재기록 O(n²) 폭주 방지
        self._pending = 0
        self._lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.update(data)
        except Exception:
            pass
        self._last_flush = time.monotonic()   # 시작 직후 즉시 대량 flush 방지(첫 flush도 min_interval 뒤)
        atexit.register(self.flush)           # 정상 종료 시 잔여 보장(디바운스로 미flush된 마지막 항목 저장)

    def remember(self, key, value) -> None:
        """메모리에 저장하고, **save_every건 AND 직전 flush 후 min_interval초 경과** 시에만 디스크 flush(시간 디바운스).
        ⚠️예전엔 5건마다 무조건 flush → 캐시가 커지면(예: cache_summary 152MB) 매 flush가 전체 파일 재기록이라
        물건마다 호출되는 대량 예열에서 O(n²)로 폭주(처리 ~9건/분, 사실상 정지)했음. 디바운스로 재기록 빈도를 분당 1회 수준으로 제한."""
        self[key] = value
        with self._lock:
            self._pending += 1
            due = self._pending >= self._save_every and (time.monotonic() - self._last_flush) >= self._min_interval
        if due:
            self.flush()

    def flush(self) -> None:
        """잔여(pending)가 있으면 전체 스냅샷을 디스크에 기록. 외부/종료(atexit) 호출은 시간과 무관하게 즉시 기록."""
        with self._lock:
            if self._pending == 0:
                return
            self._pending = 0
            self._last_flush = time.monotonic()
            snapshot = dict(self)
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception:
            pass


class SqliteDict:
    """SQLite 백엔드 캐시 — DiskDict 드롭인 대체(인터페이스 in/[]/get/remember/flush/pop 호환).
    DiskDict는 전체 JSON을 메모리 dict로 상주(예: cache_summary 198MB) + flush가 전체-파일 재기록(O(n²))이었음.
    SqliteDict는 항목별 인덱스 upsert(증분 O(1)) + 온디맨드 조회(메모리 상주 없음)로 둘 다 해소."""

    def __init__(self, path: str, save_every: int = 5, min_interval: float = 60.0):
        self._path = path                              # save_every/min_interval은 DiskDict 호환용(미사용)
        self._con = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.execute("PRAGMA busy_timeout=30000")    # 다중 프로세스(샤드) 동시 쓰기 시 잠금 대기(에러 대신) — WAL+busy_timeout
        self._con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        self._con.commit()
        self._lock = threading.Lock()

    def __contains__(self, k) -> bool:
        with self._lock:
            return self._con.execute("SELECT 1 FROM kv WHERE k=?", (k,)).fetchone() is not None

    def __getitem__(self, k):
        with self._lock:
            r = self._con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        if r is None:
            raise KeyError(k)
        return json.loads(r[0])

    def get(self, k, default=None):
        with self._lock:
            r = self._con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def remember(self, key, value) -> None:
        with self._lock:
            self._con.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?, ?)",
                              (key, json.dumps(value, ensure_ascii=False)))
            self._con.commit()

    def pop(self, k, default=None):
        with self._lock:
            r = self._con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            if r is not None:
                self._con.execute("DELETE FROM kv WHERE k=?", (k,))
                self._con.commit()
        return json.loads(r[0]) if r is not None else default

    def flush(self) -> None:
        pass                                           # WAL: 쓰기마다 commit → 별도 flush 불필요

    def __len__(self) -> int:
        with self._lock:
            return self._con.execute("SELECT COUNT(*) FROM kv").fetchone()[0]


def migrate_json_to_sqlite(json_path: str, db_path: str) -> int:
    """기존 DiskDict JSON 캐시를 SqliteDict(.db)로 1회 이관. 반환=이관 항목 수(파일 없으면 0)."""
    if not os.path.exists(json_path):
        return 0
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    con.executemany("INSERT OR REPLACE INTO kv(k, v) VALUES(?, ?)",
                    ((k, json.dumps(v, ensure_ascii=False)) for k, v in data.items()))
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM kv").fetchone()[0]
    con.close()
    return n
