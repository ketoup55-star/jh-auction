# -*- coding: utf-8 -*-
"""로컬 디스크 캐시(SQLite) — Supabase 과부하 시 앱이 로컬에서 즉시 응답 + write-behind.
저장 구조가 Supabase api_cache와 동일(cache_key→JSON value)이라, synced=0 항목을 나중에
api_cache로 그대로 밀어넣을 수 있음(flush_localcache.py가 새벽에 동기화).
- synced=0: 앱이 계산해 저장한 값(cache_save) → Supabase로 flush 대상
- synced=1: Supabase에서 읽어와 캐싱한 값(읽기 전용) / 'item:'키(물건 데이터)도 여기 → flush 안 함
"""
import os
import json
import time
import sqlite3
import threading

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_localcache.db")
_init_lock = threading.Lock()
_inited: set = set()


def _conn(path):
    c = sqlite3.connect(path, timeout=10)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=8000")
        c.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return c


def _ensure(path):
    if path in _inited:
        return
    with _init_lock:
        if path in _inited:
            return
        try:
            c = _conn(path)
            c.execute("CREATE TABLE IF NOT EXISTS kv "
                      "(k TEXT PRIMARY KEY, v TEXT NOT NULL, updated REAL, synced INTEGER DEFAULT 0)")
            c.execute("CREATE INDEX IF NOT EXISTS ix_kv_synced ON kv(synced)")
            c.commit()
            c.close()
            _inited.add(path)
        except Exception:
            pass


class LocalCache:
    def __init__(self, path: str = None):
        self.path = path or _DEFAULT_PATH
        _ensure(self.path)

    def get_many(self, keys) -> dict:
        out = {}
        if not keys:
            return out
        try:
            c = _conn(self.path)
            qs = ",".join("?" * len(keys))
            for k, v in c.execute("SELECT k,v FROM kv WHERE k IN (%s)" % qs, list(keys)):
                try:
                    out[k] = json.loads(v)
                except Exception:
                    pass
            c.close()
        except Exception:
            pass
        return out

    def get(self, key):
        return self.get_many([key]).get(key)

    def get_entry(self, key):
        """(value, age_seconds) — 없으면 (None, None). TTL 판단용."""
        try:
            c = _conn(self.path)
            r = c.execute("SELECT v,updated FROM kv WHERE k=?", (key,)).fetchone()
            c.close()
            if r:
                return json.loads(r[0]), max(0.0, time.time() - (r[1] or 0))
        except Exception:
            pass
        return None, None

    def put(self, key, value, synced=0) -> bool:
        try:
            c = _conn(self.path)
            c.execute("INSERT INTO kv(k,v,updated,synced) VALUES(?,?,?,?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated=excluded.updated, synced=excluded.synced",
                      (key, json.dumps(value, ensure_ascii=False), time.time(), int(synced)))
            c.commit()
            c.close()
            return True
        except Exception:
            return False

    def put_many(self, items, synced=0) -> bool:
        """items: iterable of (key, value)."""
        rows = []
        now = time.time()
        for k, v in items:
            try:
                rows.append((k, json.dumps(v, ensure_ascii=False), now, int(synced)))
            except Exception:
                pass
        if not rows:
            return False
        try:
            c = _conn(self.path)
            c.executemany("INSERT INTO kv(k,v,updated,synced) VALUES(?,?,?,?) "
                          "ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated=excluded.updated, synced=excluded.synced",
                          rows)
            c.commit()
            c.close()
            return True
        except Exception:
            return False

    def unsynced(self, limit=200000):
        """flush 대상(synced=0). [(key, value), ...]."""
        out = []
        try:
            c = _conn(self.path)
            for k, v in c.execute("SELECT k,v FROM kv WHERE synced=0 LIMIT ?", (limit,)):
                try:
                    out.append((k, json.loads(v)))
                except Exception:
                    pass
            c.close()
        except Exception:
            pass
        return out

    def mark_synced(self, keys) -> None:
        keys = list(keys)
        if not keys:
            return
        try:
            c = _conn(self.path)
            for i in range(0, len(keys), 400):
                chunk = keys[i:i + 400]
                qs = ",".join("?" * len(chunk))
                c.execute("UPDATE kv SET synced=1 WHERE k IN (%s)" % qs, chunk)
            c.commit()
            c.close()
        except Exception:
            pass

    def counts(self):
        try:
            c = _conn(self.path)
            tot = c.execute("SELECT COUNT(*) FROM kv").fetchone()[0]
            uns = c.execute("SELECT COUNT(*) FROM kv WHERE synced=0").fetchone()[0]
            c.close()
            return tot, uns
        except Exception:
            return 0, 0
