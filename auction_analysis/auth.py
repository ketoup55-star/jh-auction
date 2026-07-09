"""
회원 인증 + 세션 + 관심물건 저장소 — PostgreSQL(Supabase) 백엔드(psycopg3).

- 비밀번호: pbkdf2_hmac(sha256) + 사용자별 salt. 외부 의존(bcrypt) 없이 안전.
- 세션: 랜덤 토큰을 DB에 저장, httponly 쿠키로 전달.
- 관심물건: (user_id, case_no) 매핑.

포팅 노트(SQLite → PostgreSQL):
- 연결: 환경변수 SUPABASE_DB_URL(트랜잭션 pooler, 포트 6543).
  psycopg.connect(url, prepare_threshold=None, ...) — pooler는 prepared statement
  미지원이라 prepare_threshold=None 필수.
- FastAPI 멀티스레드 + psycopg connection(thread-safe 아님) 대비:
  threading.RLock 으로 모든 DB 접근을 직렬화. pooler idle 끊김 대비 _ex()가
  OperationalError/InterfaceError 시 1회 재연결 후 재시도.
- public 메서드의 시그니처·반환 형태는 SQLite 판과 100% 동일(api/main.py 무수정).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
from datetime import datetime, timezone
from typing import Optional

import psycopg
import psycopg.rows


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """salt$hash 형태로 반환. salt 미지정 시 새로 생성."""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), 100_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), stored)


def _user_dict(r: dict) -> dict:
    keys = r.keys()
    return {
        "id": r["id"], "email": r["email"], "name": r["name"],
        "grade": r["grade"], "created_at": r["created_at"],
        "provider": r["provider"] if "provider" in keys else "local",
        "role": (r["role"] if "role" in keys else "user") or "user",
        "phone": (r["phone"] if "phone" in keys else "") or "",
        "mileage": (r["mileage"] if "mileage" in keys else 0) or 0,
        "paid_until": (r["paid_until"] if "paid_until" in keys else None) or None,
        "grade_until": (r["grade_until"] if "grade_until" in keys else None) or None,
        # 비밀번호 설정 여부(해시 자체는 노출 안 함) — 카카오 신규계정은 password="" 라 False.
        "has_password": bool((r["password"] if "password" in keys else "") or ""),
    }


class UserStore:
    def __init__(self, db_path: Optional[str] = None):
        # db_path 인자는 하위호환으로 받되 무시(SQLite 시절 호출부 호환).
        # 실제 연결은 항상 SUPABASE_DB_URL(PostgreSQL/Supabase 트랜잭션 pooler).
        self._db_url = os.environ["SUPABASE_DB_URL"]
        self._lock = threading.RLock()
        self.conn = self._connect()
        self._init_schema()

    # ---- 연결/실행 인프라 ----
    def _connect(self) -> "psycopg.Connection":
        # prepare_threshold=None: transaction pooler는 prepared statement 미지원.
        # autocommit=False: 쓰기마다 명시적 commit(결제·마일리지 트랜잭션 정확성).
        return psycopg.connect(
            self._db_url,
            prepare_threshold=None,
            autocommit=False,
            row_factory=psycopg.rows.dict_row,
        )

    def _reconnect(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
        self.conn = self._connect()

    def _ex(self, sql: str, params: tuple = (), fetch: Optional[str] = None):
        """모든 DB 접근의 단일 통로. lock으로 직렬화 + pooler 끊김 시 1회 재연결 재시도.

        fetch: None(반환없음, 쓰기 후 commit) / "one" / "all" / "val"(첫 열)
               / "rowcount"(영향 행수, commit) / "id"(RETURNING id 값, commit).
        읽기(one/all/val)는 commit 안 함. 쓰기(None/rowcount/id)는 성공 시 commit.
        """
        with self._lock:
            try:
                return self._run(sql, params, fetch)
            except (psycopg.OperationalError, psycopg.InterfaceError):
                # pooler idle 끊김 등 → 1회 재연결 후 재시도.
                self._reconnect()
                return self._run(sql, params, fetch)

    def _run(self, sql: str, params: tuple, fetch: Optional[str]):
        cur = self.conn.execute(sql, params)
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        if fetch == "val":
            row = cur.fetchone()
            if not row:
                return None
            return next(iter(row.values()))
        if fetch == "id":
            row = cur.fetchone()
            self.conn.commit()
            return row["id"] if row else None
        if fetch == "rowcount":
            n = cur.rowcount
            self.conn.commit()
            return n
        # fetch None → 쓰기, commit.
        self.conn.commit()
        return None

    def _tx(self, ops) -> object:
        """여러 execute를 한 트랜잭션으로 실행. 중간 실패 시 rollback, 성공 시 commit.

        ops(exec_fn) 콜백을 받아 호출한다. exec_fn(sql, params, fetch)은
        같은 커넥션에서 커밋 없이 실행하며 fetch("one"/"all"/"val"/"id"/"rowcount")를
        지원한다. 콜백 반환값을 그대로 돌려준다. pooler 끊김 시 1회 재연결 재시도.
        """
        with self._lock:
            try:
                return self._tx_once(ops)
            except (psycopg.OperationalError, psycopg.InterfaceError):
                self._reconnect()
                return self._tx_once(ops)

    def _tx_once(self, ops) -> object:
        def exec_fn(sql: str, params: tuple = (), fetch: Optional[str] = None):
            cur = self.conn.execute(sql, params)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            if fetch == "val":
                row = cur.fetchone()
                return None if not row else next(iter(row.values()))
            if fetch == "id":
                row = cur.fetchone()
                return row["id"] if row else None
            if fetch == "rowcount":
                return cur.rowcount
            return None
        try:
            out = ops(exec_fn)
            self.conn.commit()
            return out
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise

    def _init_schema(self) -> None:
        # 테이블은 이미 Supabase에 존재. CREATE TABLE IF NOT EXISTS는 무해(재실행 안전).
        # PostgreSQL 타입: BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY / TEXT / INTEGER.
        stmts = [
            """CREATE TABLE IF NOT EXISTS users(
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password TEXT DEFAULT '',
                name TEXT DEFAULT '',
                grade TEXT DEFAULT '무료',
                provider TEXT DEFAULT 'local',
                provider_id TEXT,
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS sessions(
                token TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS favorites(
                user_id BIGINT NOT NULL,
                case_no TEXT NOT NULL,
                created_at TEXT,
                PRIMARY KEY(user_id, case_no)
            )""",
            """CREATE TABLE IF NOT EXISTS gongmae_favorites(
                user_id BIGINT NOT NULL,
                manage_no TEXT NOT NULL,
                data TEXT,
                folder TEXT DEFAULT '기타',
                memo TEXT DEFAULT '',
                created_at TEXT,
                PRIMARY KEY(user_id, manage_no)
            )""",
            """CREATE TABLE IF NOT EXISTS mock_bids(
                user_id BIGINT NOT NULL,
                item_key TEXT NOT NULL,
                case_no TEXT,
                bid_amount INTEGER NOT NULL,
                data TEXT,
                created_at TEXT,
                PRIMARY KEY(user_id, item_key)
            )""",
            """CREATE TABLE IF NOT EXISTS mileage_log(
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                balance INTEGER NOT NULL,
                reason TEXT DEFAULT '',
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS coupons(
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                name TEXT DEFAULT '',
                amount INTEGER NOT NULL,
                user_id BIGINT,
                used INTEGER DEFAULT 0,
                used_by BIGINT,
                used_at TEXT,
                expires_at TEXT,
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS payments(
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                merchant_uid TEXT UNIQUE NOT NULL,
                user_id BIGINT NOT NULL,
                plan TEXT NOT NULL,
                months INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT DEFAULT 'ready',
                imp_uid TEXT,
                grade TEXT DEFAULT '전국',
                paid_at TEXT,
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS posts(
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                board TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT DEFAULT '',
                author_id BIGINT,
                author_name TEXT DEFAULT '',
                views INTEGER DEFAULT 0,
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS comments(
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                post_id BIGINT NOT NULL,
                author_id BIGINT,
                author_name TEXT DEFAULT '',
                content TEXT NOT NULL,
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS grades(
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                rank INTEGER DEFAULT 0,
                lecture INTEGER DEFAULT 0,
                color TEXT DEFAULT '',
                comment TEXT DEFAULT '',
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS board_settings(
                board TEXT PRIMARY KEY,
                title TEXT,
                read_perm TEXT DEFAULT 'all',
                write_perm TEXT DEFAULT 'admin',
                comment_perm TEXT DEFAULT 'user',
                updated_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS user_folders(
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT,
                PRIMARY KEY(user_id, name)
            )""",
            """CREATE TABLE IF NOT EXISTS gongmae_saved_searches(
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                user_id BIGINT NOT NULL,
                name TEXT DEFAULT '',
                conditions TEXT NOT NULL,
                created_at TEXT
            )""",
        ]
        for s in stmts:
            self._ex(s)
        # 기존 DB 마이그레이션(ADD COLUMN IF NOT EXISTS — 이미 있으면 무해).
        self._ensure_column("users", "provider", "TEXT DEFAULT 'local'")
        self._ensure_column("users", "provider_id", "TEXT")
        self._ensure_column("users", "role", "TEXT DEFAULT 'user'")   # user | admin
        self._ensure_column("users", "phone", "TEXT DEFAULT ''")      # 휴대폰(본인인증)
        self._ensure_column("users", "mileage", "INTEGER DEFAULT 0")  # 마일리지 잔액
        self._ensure_column("users", "paid_until", "TEXT")            # 이용권 만료 YYYY-MM-DD
        self._ensure_column("users", "grade_until", "TEXT")           # 등급 유지기한 ISO(넘으면 무료 대우·NULL=무제한)
        self._ensure_column("grades", "comment", "TEXT DEFAULT ''")   # 등급별 코멘트
        self._ensure_column("favorites", "folder", "TEXT DEFAULT '기타'")     # 관심물건 폴더
        self._ensure_column("favorites", "importance", "INTEGER DEFAULT 0")   # 중요도 0~5
        self._ensure_column("favorites", "memo", "TEXT DEFAULT ''")           # 메모
        self._ensure_column("favorites", "notify", "INTEGER DEFAULT 1")       # 알림/달력 표시
        self._ensure_column("user_folders", "sort_order", "INTEGER DEFAULT 0")  # 폴더 정렬순서
        self._ex(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_provider "
            "ON users(provider, provider_id)")
        self._seed_grades()

    def _seed_grades(self) -> None:
        """기본 등급 시드(없을 때만): 무료(강의X), 전국(결제 부여·강의O)."""
        have = self._ex("SELECT COUNT(*) AS c FROM grades", (), fetch="val")
        if have:
            return
        for name, rank, lec, color in [("무료", 0, 0, "#9a9a93"),
                                       ("전국", 10, 1, "#1f4fa3")]:
            self._ex(
                "INSERT INTO grades(name,rank,lecture,color,created_at) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (name, rank, lec, color, _now()))

    def _ensure_column(self, table: str, col: str, decl: str) -> None:
        # Supabase 테이블은 완성된 컬럼으로 이미 생성돼 있어 ALTER가 불필요하다.
        #  매 시작 시 ALTER TABLE이 pooler의 statement timeout을 유발하므로 no-op 처리.
        return

    # ---- 회원 ----
    def create_user(self, email: str, password: str, name: str = "", phone: str = "") -> dict:
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError("올바른 이메일을 입력하세요.")
        if len(password) < 6:
            raise ValueError("비밀번호는 6자 이상이어야 합니다.")
        if self._ex("SELECT 1 FROM users WHERE email=%s", (email,), fetch="one"):
            raise ValueError("이미 가입된 이메일입니다.")
        if phone and self._ex(
                "SELECT 1 FROM users WHERE phone=%s AND phone<>''", (phone,), fetch="one"):
            raise ValueError("이미 가입에 사용된 연락처입니다.")
        new_id = self._ex(
            "INSERT INTO users(email,password,name,phone,created_at) "
            "VALUES(%s,%s,%s,%s,%s) RETURNING id",
            (email, hash_password(password), name, phone, _now()), fetch="id")
        return self.get_user(new_id)

    def phone_exists(self, phone: str) -> bool:
        if not phone:
            return False
        return self._ex(
            "SELECT 1 FROM users WHERE phone=%s AND phone<>''",
            (phone,), fetch="one") is not None

    def get_user(self, uid: int) -> Optional[dict]:
        r = self._ex("SELECT * FROM users WHERE id=%s", (uid,), fetch="one")
        return _user_dict(r) if r else None

    def authenticate(self, email: str, password: str) -> Optional[dict]:
        email = email.strip().lower()
        r = self._ex("SELECT * FROM users WHERE email=%s", (email,), fetch="one")
        # 소셜 회원(password 빈값)은 이메일/비번 로그인 불가
        if r and r["password"] and verify_password(password, r["password"]):
            return _user_dict(r)
        return None

    def get_or_create_social_user(self, provider: str, provider_id: str,
                                  email: Optional[str] = None, name: str = "") -> dict:
        """소셜 로그인(카카오 등) 회원 조회 또는 신규 생성. provider_id 기준."""
        provider_id = str(provider_id)
        r = self._ex(
            "SELECT * FROM users WHERE provider=%s AND provider_id=%s",
            (provider, provider_id), fetch="one")
        if r:
            u = _user_dict(r)
            # 재로그인 소급 갱신: 카카오가 이번에 실제 닉네임/이메일을 주면 기존 '카카오회원'·합성이메일을 채운다.
            new_name = (name or "").strip()
            real_email = (email or "").strip().lower()
            sets, vals = [], []
            if new_name and new_name != f"{provider}회원" and (u.get("name") or "") != new_name:
                sets.append("name=%s"); vals.append(new_name)
            if real_email and "@" in real_email and (u.get("email") or "").endswith(f"@{provider}.local"):
                sets.append("email=%s"); vals.append(real_email)
            if sets:
                try:
                    self._ex(f"UPDATE users SET {', '.join(sets)} WHERE id=%s",
                             tuple(vals) + (u["id"],), fetch="rowcount")
                    u = self.get_user(u["id"])
                except psycopg.errors.UniqueViolation:        # 실제이메일이 이미 다른 계정에 있음 → 이름만 갱신
                    try:
                        if new_name and new_name != f"{provider}회원" and (u.get("name") or "") != new_name:
                            self._ex("UPDATE users SET name=%s WHERE id=%s", (new_name, u["id"]), fetch="rowcount")
                            u = self.get_user(u["id"])
                    except Exception:
                        pass
                except Exception:
                    pass
            return u

        # email 미동의 시 합성 이메일로 UNIQUE 제약 충족. 충돌 시에도 합성으로 폴백.
        synthetic = f"{provider}_{provider_id}@{provider}.local"
        use_email = (email or "").strip().lower() or synthetic
        params = (use_email, "", name or f"{provider}회원", "무료",
                  provider, provider_id, _now())
        sql = ("INSERT INTO users(email,password,name,grade,provider,provider_id,created_at) "
               "VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id")
        try:
            new_id = self._ex(sql, params, fetch="id")
        except psycopg.errors.UniqueViolation:
            new_id = self._ex(sql, (synthetic,) + params[1:], fetch="id")
        return self.get_user(new_id)

    # ---- 권한(관리자) ----
    def set_admin(self, *, name: Optional[str] = None, email: Optional[str] = None) -> int:
        """이름 또는 이메일로 관리자 지정. 적용된 행 수 반환."""
        n = 0
        if name:
            n += self._ex("UPDATE users SET role='admin' WHERE name=%s",
                          (name,), fetch="rowcount")
        if email:
            n += self._ex("UPDATE users SET role='admin' WHERE email=%s",
                          (email.strip().lower(),), fetch="rowcount")
        return n

    def list_admins(self) -> list[dict]:
        rows = self._ex("SELECT * FROM users WHERE role='admin'", (), fetch="all")
        return [_user_dict(r) for r in rows]

    # ---- 세션 ----
    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        self._ex(
            "INSERT INTO sessions(token,user_id,created_at) VALUES(%s,%s,%s)",
            (token, user_id, _now()))
        return token

    def get_user_by_session(self, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None
        r = self._ex(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=%s",
            (token,), fetch="one")
        return _user_dict(r) if r else None

    def delete_session(self, token: Optional[str]) -> None:
        if token:
            self._ex("DELETE FROM sessions WHERE token=%s", (token,))

    # ---- 관심물건 ----
    def add_favorite(self, user_id: int, case_no: str, folder: str = "기타",
                     importance: int = 0, memo: str = "", notify: int = 1) -> None:
        """관심물건 등록/수정(UPSERT) — 폴더·중요도·메모·알림 포함. created_at은 최초만 기록."""
        self._ex(
            "INSERT INTO favorites(user_id,case_no,created_at,folder,importance,memo,notify) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(user_id,case_no) DO UPDATE SET "
            "folder=excluded.folder, importance=excluded.importance, "
            "memo=excluded.memo, notify=excluded.notify",
            (user_id, case_no, _now(), folder or "기타",
             max(0, min(5, int(importance or 0))), memo or "", 1 if notify else 0))

    def remove_favorite(self, user_id: int, case_no: str) -> None:
        self._ex(
            "DELETE FROM favorites WHERE user_id=%s AND case_no=%s", (user_id, case_no))

    def list_favorites(self, user_id: int) -> list[str]:
        rows = self._ex(
            "SELECT case_no FROM favorites WHERE user_id=%s ORDER BY created_at DESC",
            (user_id,), fetch="all")
        return [r["case_no"] for r in rows]

    def list_favorites_full(self, user_id: int) -> list:
        """관심물건 전체(메타 포함, 저장 역순)."""
        rows = self._ex(
            "SELECT case_no, folder, importance, memo, notify, created_at FROM favorites "
            "WHERE user_id=%s ORDER BY created_at DESC", (user_id,), fetch="all")
        return [dict(r) for r in rows]

    def get_favorite(self, user_id: int, case_no: str):
        """관심물건 1건 메타(folder/importance/memo/notify) 또는 None."""
        r = self._ex(
            "SELECT case_no, folder, importance, memo, notify FROM favorites "
            "WHERE user_id=%s AND case_no=%s", (user_id, case_no), fetch="one")
        return dict(r) if r else None

    def is_favorite(self, user_id: int, case_no: str) -> bool:
        return self._ex(
            "SELECT 1 FROM favorites WHERE user_id=%s AND case_no=%s",
            (user_id, case_no), fetch="one") is not None

    # ---- 관심공매물건(온비드, 실시간 API라 스냅샷 보관) ----
    def add_gongmae_fav(self, user_id: int, manage_no: str, data: str = "",
                        folder: str = "기타", memo: str = "") -> None:
        """관심공매물건 등록/수정(UPSERT). data=공매물건 스냅샷 JSON."""
        self._ex(
            "INSERT INTO gongmae_favorites(user_id,manage_no,data,folder,memo,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,manage_no) DO UPDATE SET "
            "data=excluded.data, folder=excluded.folder, memo=excluded.memo",
            (user_id, manage_no, data or "", folder or "기타", memo or "", _now()))

    def remove_gongmae_fav(self, user_id: int, manage_no: str) -> None:
        self._ex(
            "DELETE FROM gongmae_favorites WHERE user_id=%s AND manage_no=%s",
            (user_id, manage_no))

    def list_gongmae_favs(self, user_id: int) -> list:
        rows = self._ex(
            "SELECT manage_no, data, folder, memo, created_at FROM gongmae_favorites "
            "WHERE user_id=%s ORDER BY created_at DESC", (user_id,), fetch="all")
        return [dict(r) for r in rows]

    def is_gongmae_fav(self, user_id: int, manage_no: str) -> bool:
        return self._ex(
            "SELECT 1 FROM gongmae_favorites WHERE user_id=%s AND manage_no=%s",
            (user_id, manage_no), fetch="one") is not None

    # ---- 공매 즐겨쓰는검색(검색조건 저장) ----
    def add_gongmae_search(self, user_id: int, name: str, conditions: str) -> int:
        """공매 검색조건 저장. conditions=필터조건 JSON 문자열. 반환=새 id."""
        return self._ex(
            "INSERT INTO gongmae_saved_searches(user_id,name,conditions,created_at) "
            "VALUES(%s,%s,%s,%s) RETURNING id",
            (user_id, name or "", conditions or "{}", _now()), fetch="id")

    def list_gongmae_searches(self, user_id: int) -> list:
        rows = self._ex(
            "SELECT id, name, conditions, created_at FROM gongmae_saved_searches "
            "WHERE user_id=%s ORDER BY created_at DESC", (user_id,), fetch="all")
        return [dict(r) for r in rows]

    def remove_gongmae_search(self, user_id: int, search_id: int) -> None:
        self._ex(
            "DELETE FROM gongmae_saved_searches WHERE user_id=%s AND id=%s",
            (user_id, search_id))

    # ---- 모의입찰(연습) ----
    def add_mock_bid(self, user_id: int, item_key: str, bid_amount: int,
                     case_no: str = "", data: str = "") -> None:
        """모의입찰 등록/수정(UPSERT). data=물건 스냅샷 JSON, bid_amount=원 단위."""
        self._ex(
            "INSERT INTO mock_bids(user_id,item_key,case_no,bid_amount,data,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,item_key) DO UPDATE SET "
            "bid_amount=excluded.bid_amount, data=excluded.data, "
            "case_no=excluded.case_no, created_at=excluded.created_at",
            (user_id, item_key, case_no or "", int(bid_amount or 0), data or "", _now()))

    def remove_mock_bid(self, user_id: int, item_key: str) -> None:
        self._ex("DELETE FROM mock_bids WHERE user_id=%s AND item_key=%s",
                 (user_id, item_key))

    def list_mock_bids(self, user_id: int) -> list:
        rows = self._ex(
            "SELECT item_key, case_no, bid_amount, data, created_at FROM mock_bids "
            "WHERE user_id=%s ORDER BY created_at DESC", (user_id,), fetch="all")
        return [dict(r) for r in rows]

    def get_mock_bid(self, user_id: int, item_key: str):
        r = self._ex(
            "SELECT bid_amount, created_at FROM mock_bids WHERE user_id=%s AND item_key=%s",
            (user_id, item_key), fetch="one")
        return dict(r) if r else None

    # ---- 개인폴더 ----
    def list_folders(self, user_id: int) -> list:
        rows = self._ex(
            "SELECT name FROM user_folders WHERE user_id=%s ORDER BY sort_order, created_at",
            (user_id,), fetch="all")
        return [r["name"] for r in rows]

    def add_folder(self, user_id: int, name: str) -> None:
        name = (name or "").strip()
        if name:
            mx = self._ex(
                "SELECT COALESCE(MAX(sort_order),-1) AS m FROM user_folders WHERE user_id=%s",
                (user_id,), fetch="val")
            self._ex(
                "INSERT INTO user_folders(user_id,name,created_at,sort_order) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (user_id, name, _now(), mx + 1))

    def rename_folder(self, user_id: int, old: str, new: str) -> bool:
        """개인폴더 이름변경 — 그 폴더의 관심물건도 새 이름으로 이동. 중복명/없음이면 False."""
        old = (old or "").strip(); new = (new or "").strip()
        if not old or not new or old == new:
            return False
        if self._ex("SELECT 1 FROM user_folders WHERE user_id=%s AND name=%s",
                    (user_id, new), fetch="one"):
            return False                                          # 같은 이름 이미 있음

        def _do(ex):
            n = ex("UPDATE user_folders SET name=%s WHERE user_id=%s AND name=%s",
                   (new, user_id, old), fetch="rowcount")
            if n:
                ex("UPDATE favorites SET folder=%s WHERE user_id=%s AND folder=%s",
                   (new, user_id, old))
            return n
        return self._tx(_do) > 0

    def reorder_folders(self, user_id: int, names: list) -> None:
        """개인폴더 정렬순서 일괄 지정(▲▼) — names 순서대로 sort_order=index."""
        def _do(ex):
            for i, name in enumerate(names or []):
                ex("UPDATE user_folders SET sort_order=%s WHERE user_id=%s AND name=%s",
                   (i, user_id, name))
        self._tx(_do)

    def remove_folder(self, user_id: int, name: str) -> None:
        """개인폴더 삭제 — 그 폴더의 관심물건은 '기타'로 이동(고아 방지)."""
        def _do(ex):
            ex("DELETE FROM user_folders WHERE user_id=%s AND name=%s", (user_id, name))
            ex("UPDATE favorites SET folder='기타' WHERE user_id=%s AND folder=%s",
               (user_id, name))
        self._tx(_do)

    # ---- 회원관리(관리자) ----
    def list_users(self, q: str = "", limit: int = 500) -> list[dict]:
        """회원 목록(최근 가입순). q로 이름/이메일/연락처 부분검색."""
        sql = "SELECT * FROM users"
        params: tuple = ()
        if q:
            like = f"%{q.strip()}%"
            sql += " WHERE name LIKE %s OR email LIKE %s OR phone LIKE %s"
            params = (like, like, like)
        sql += " ORDER BY id DESC LIMIT %s"
        params = params + (int(limit),)
        return [_user_dict(r) for r in self._ex(sql, params, fetch="all")]

    def set_grade(self, uid: int, grade: str, grade_until: Optional[str] = None) -> Optional[dict]:
        """등급 설정 + 유지기한(grade_until ISO, None=무제한) 저장.
        grade_until 컬럼이 없는 DB(마이그레이션 전)에서도 500 안 나게 등급만 폴백."""
        try:
            self._ex("UPDATE users SET grade=%s, grade_until=%s WHERE id=%s", (grade, grade_until, uid))
        except Exception:
            self._reconnect()                                    # 실패 트랜잭션 복구(grade_until 컬럼 없는 경우 등)
            self._ex("UPDATE users SET grade=%s WHERE id=%s", (grade, uid))
        return self.get_user(uid)

    def set_role(self, uid: int, role: str) -> Optional[dict]:
        if role not in ("user", "admin"):
            raise ValueError("role은 user 또는 admin")
        self._ex("UPDATE users SET role=%s WHERE id=%s", (role, uid))
        return self.get_user(uid)

    def update_profile(self, uid: int, name: Optional[str] = None, email: Optional[str] = None) -> Optional[dict]:
        """회원 본인 정보수정: 닉네임(name)·이메일. 이메일 UNIQUE 체크(자기 제외).
        카카오 로그인은 provider_id로 매칭하므로 이메일을 바꿔도 로그인은 안 깨짐."""
        sets, vals = [], []
        if name is not None and name.strip():
            sets.append("name=%s"); vals.append(name.strip())
        if email is not None and email.strip():
            e = email.strip()
            if self._ex("SELECT 1 FROM users WHERE email=%s AND id<>%s", (e, uid), fetch="one"):
                raise ValueError("이미 사용 중인 이메일입니다.")
            sets.append("email=%s"); vals.append(e)
        if not sets:
            return self.get_user(uid)
        try:
            self._ex(f"UPDATE users SET {', '.join(sets)} WHERE id=%s", tuple(vals) + (uid,))
        except psycopg.errors.UniqueViolation:
            self._reconnect(); raise ValueError("이미 사용 중인 이메일입니다.")
        return self.get_user(uid)

    def admin_set_password(self, uid: int, new_password: str) -> Optional[dict]:
        """관리자가 회원 비밀번호 재설정(이메일 로그인 복구용). 소셜계정에도 걸면 이메일 로그인도 가능해짐."""
        if len(new_password or "") < 6:
            raise ValueError("비밀번호는 6자 이상이어야 합니다.")
        self._ex("UPDATE users SET password=%s WHERE id=%s", (hash_password(new_password), uid))
        return self.get_user(uid)

    def change_password(self, uid: int, current: str, new_password: str) -> Optional[dict]:
        """회원 본인 비밀번호 변경. 기존 비번이 있으면 current 검증, 없으면(카카오 등) 신규 설정."""
        if len(new_password or "") < 6:
            raise ValueError("새 비밀번호는 6자 이상이어야 합니다.")
        u = self._ex("SELECT * FROM users WHERE id=%s", (uid,), fetch="one")
        if not u:
            raise ValueError("회원을 찾을 수 없습니다.")
        stored = (u["password"] or "")
        if stored:                                        # 기존 비번 있으면 현재 비번 확인
            if not verify_password(current or "", stored):
                raise ValueError("현재 비밀번호가 일치하지 않습니다.")
        self._ex("UPDATE users SET password=%s WHERE id=%s", (hash_password(new_password), uid))
        return self.get_user(uid)

    # ---- 마일리지 ----
    def adjust_mileage(self, uid: int, amount: int, reason: str = "") -> dict:
        """마일리지 증감(+적립/-차감). 잔액 음수 불가. 거래내역 기록 후 회원정보 반환."""
        u = self.get_user(uid)
        if not u:
            raise ValueError("회원을 찾을 수 없습니다.")
        amount = int(amount)
        new_bal = (u.get("mileage") or 0) + amount
        if new_bal < 0:
            raise ValueError("마일리지 잔액이 부족합니다.")

        def _do(ex):
            ex("UPDATE users SET mileage=%s WHERE id=%s", (new_bal, uid))
            ex("INSERT INTO mileage_log(user_id,amount,balance,reason,created_at) "
               "VALUES(%s,%s,%s,%s,%s)",
               (uid, amount, new_bal, reason or ("적립" if amount >= 0 else "차감"), _now()))
        self._tx(_do)
        return self.get_user(uid)

    def mileage_log(self, uid: int, limit: int = 100) -> list[dict]:
        rows = self._ex(
            "SELECT amount,balance,reason,created_at FROM mileage_log "
            "WHERE user_id=%s ORDER BY id DESC LIMIT %s", (uid, int(limit)), fetch="all")
        return [dict(r) for r in rows]

    # ---- 쿠폰 ----
    def create_coupons(self, name: str, amount: int, user_id: Optional[int] = None,
                        count: int = 1, expires_at: Optional[str] = None) -> list[dict]:
        """쿠폰 발급. user_id 지정 시 그 회원 전용, 미지정 시 아무나 사용 가능한 코드.
        count>1이면 같은 조건의 코드를 여러 장 생성(범용 코드일 때 유용)."""
        amount = int(amount)
        if amount <= 0:
            raise ValueError("쿠폰 금액(마일리지)은 1 이상이어야 합니다.")
        count = max(1, min(int(count), 1000))
        out: list[dict] = []
        for _ in range(count):
            code = "JH" + secrets.token_hex(4).upper()   # 예: JH1A2B3C4D
            self._ex(
                "INSERT INTO coupons(code,name,amount,user_id,expires_at,created_at) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (code, name or "쿠폰", amount, user_id, expires_at or None, _now()))
            out.append(self._coupon_by_code(code))
        return out

    def _coupon_by_code(self, code: str) -> Optional[dict]:
        r = self._ex("SELECT * FROM coupons WHERE code=%s", (code,), fetch="one")
        return dict(r) if r else None

    def list_coupons(self, limit: int = 500) -> list[dict]:
        rows = self._ex(
            "SELECT c.*, u.name AS user_name, u.email AS user_email "
            "FROM coupons c LEFT JOIN users u ON u.id=c.user_id "
            "ORDER BY c.id DESC LIMIT %s", (int(limit),), fetch="all")
        return [dict(r) for r in rows]

    def delete_coupon(self, coupon_id: int) -> bool:
        """미사용 쿠폰만 회수(삭제). 사용된 쿠폰은 보존."""
        return self._ex("DELETE FROM coupons WHERE id=%s AND used=0",
                        (int(coupon_id),), fetch="rowcount") > 0

    def redeem_coupon(self, code: str, user_id: int) -> dict:
        """회원이 쿠폰코드 사용 → 마일리지 적립. {mileage, amount, name} 반환."""
        code = (code or "").strip().upper()
        r = self._ex("SELECT * FROM coupons WHERE code=%s", (code,), fetch="one")
        if not r:
            raise ValueError("존재하지 않는 쿠폰 코드입니다.")
        if r["used"]:
            raise ValueError("이미 사용된 쿠폰입니다.")
        if r["user_id"] is not None and r["user_id"] != user_id:
            raise ValueError("이 쿠폰은 다른 회원 전용입니다.")
        if r["expires_at"]:
            today = _now()[:10]
            if r["expires_at"] < today:
                raise ValueError("유효기간이 지난 쿠폰입니다.")
        # 쿠폰 사용표시 + 마일리지 적립을 한 트랜잭션으로(중간 실패 시 rollback).
        new_bal_holder = {}

        def _do(ex):
            ex("UPDATE coupons SET used=1, used_by=%s, used_at=%s WHERE id=%s",
               (user_id, _now(), r["id"]))
            urow = ex("SELECT mileage FROM users WHERE id=%s", (user_id,), fetch="one")
            if not urow:
                raise ValueError("회원을 찾을 수 없습니다.")
            new_bal = (urow["mileage"] or 0) + int(r["amount"])
            if new_bal < 0:
                raise ValueError("마일리지 잔액이 부족합니다.")
            ex("UPDATE users SET mileage=%s WHERE id=%s", (new_bal, user_id))
            ex("INSERT INTO mileage_log(user_id,amount,balance,reason,created_at) "
               "VALUES(%s,%s,%s,%s,%s)",
               (user_id, int(r["amount"]), new_bal,
                f"쿠폰 사용: {r['name']} ({code})", _now()))
            new_bal_holder["v"] = new_bal
        self._tx(_do)
        return {"mileage": new_bal_holder["v"], "amount": r["amount"], "name": r["name"]}

    # ---- 결제(이용권) ----
    def create_payment(self, user_id: int, merchant_uid: str, plan: str,
                       months: int, amount: int, grade: str = "전국") -> dict:
        """주문 생성(결제 전 'ready'). merchant_uid·금액을 서버가 확정해 변조 방지."""
        self._ex(
            "INSERT INTO payments(merchant_uid,user_id,plan,months,amount,status,grade,created_at) "
            "VALUES(%s,%s,%s,%s,%s,'ready',%s,%s)",
            (merchant_uid, user_id, plan, int(months), int(amount), grade, _now()))
        return self.get_payment(merchant_uid)

    def get_payment(self, merchant_uid: str) -> Optional[dict]:
        r = self._ex(
            "SELECT * FROM payments WHERE merchant_uid=%s", (merchant_uid,), fetch="one")
        return dict(r) if r else None

    def complete_payment(self, merchant_uid: str, imp_uid: str, paid_amount: int) -> dict:
        """결제 검증 통과 후 호출: 주문 금액과 실제 결제금액 대조 → 'paid' 처리 + 이용권 부여.
        멱등: 이미 paid면 회원정보만 반환(중복 부여 방지)."""
        p = self.get_payment(merchant_uid)
        if not p:
            raise ValueError("존재하지 않는 주문입니다.")
        if p["status"] == "paid":
            return {"already": True, "user": self.get_user(p["user_id"]), "payment": p}
        if int(paid_amount) != int(p["amount"]):
            self._ex(
                "UPDATE payments SET status='failed', imp_uid=%s WHERE merchant_uid=%s",
                (imp_uid, merchant_uid))
            raise ValueError(f"결제금액 불일치(주문 {p['amount']}원 / 결제 {paid_amount}원)")
        # 이용권 부여 + 결제 완료표시를 한 트랜잭션으로(중간 실패 시 rollback → 중복/부분부여 방지).
        self._grant_membership_tx(p["user_id"], int(p["months"]), p["grade"],
                                  extra=lambda ex: ex(
            "UPDATE payments SET status='paid', imp_uid=%s, paid_at=%s WHERE merchant_uid=%s",
            (imp_uid, _now(), merchant_uid)))
        return {"already": False, "user": self.get_user(p["user_id"]),
                "payment": self.get_payment(merchant_uid)}

    def _grant_membership(self, uid: int, months: int, grade: str) -> dict:
        """이용권 부여: 만료일을 max(오늘, 기존만료일)에서 months개월 연장 + 등급 설정."""
        self._grant_membership_tx(uid, months, grade)
        return self.get_user(uid)

    def _grant_membership_tx(self, uid: int, months: int, grade: str, extra=None) -> None:
        """이용권 부여 코어(트랜잭션 실행). extra(ex) 콜백이 있으면 같은 트랜잭션에 포함."""
        from datetime import date, timedelta
        u = self.get_user(uid)
        if not u:
            raise ValueError("회원을 찾을 수 없습니다.")
        today = date.today()
        cur = u.get("paid_until")
        base = today
        if cur:
            try:
                cd = date.fromisoformat(cur)
                if cd > today:
                    base = cd
            except ValueError:
                pass
        # months개월 연장(달력월 근사: 30일×개월). 기존 로직 그대로 보존.
        new_until = base + timedelta(days=30 * int(months))

        def _do(ex):
            ex("UPDATE users SET paid_until=%s, grade=%s WHERE id=%s",
               (new_until.isoformat(), grade, uid))
            if extra is not None:
                extra(ex)
        self._tx(_do)

    def grant_membership_admin(self, uid: int, months: int, grade: str = "전국") -> dict:
        """관리자 수동 부여(무통장입금 확인 등)."""
        return self._grant_membership(uid, months, grade)

    def list_payments(self, limit: int = 300) -> list[dict]:
        rows = self._ex(
            "SELECT p.*, u.name AS user_name, u.email AS user_email "
            "FROM payments p LEFT JOIN users u ON u.id=p.user_id "
            "ORDER BY p.id DESC LIMIT %s", (int(limit),), fetch="all")
        return [dict(r) for r in rows]

    # ---- 게시판 ----
    def list_posts(self, board: str, page: int = 1, size: int = 15) -> dict:
        page = max(1, int(page)); size = max(1, min(int(size), 100))
        tot = self._ex(
            "SELECT COUNT(*) AS c FROM posts WHERE board=%s", (board,), fetch="val")
        rows = self._ex(
            "SELECT id,board,title,author_name,views,created_at FROM posts "
            "WHERE board=%s ORDER BY id DESC LIMIT %s OFFSET %s",
            (board, size, (page - 1) * size), fetch="all")
        return {"total": tot, "page": page, "size": size,
                "items": [dict(r) for r in rows]}

    def get_post(self, post_id: int, bump: bool = True) -> Optional[dict]:
        if bump:                                   # 먼저 증가 → 반환 row가 현재 조회수 반영
            self._ex("UPDATE posts SET views=views+1 WHERE id=%s", (int(post_id),))
        r = self._ex("SELECT * FROM posts WHERE id=%s", (int(post_id),), fetch="one")
        return dict(r) if r else None

    def create_post(self, board: str, title: str, content: str,
                    author_id: int, author_name: str = "") -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("제목을 입력하세요.")
        new_id = self._ex(
            "INSERT INTO posts(board,title,content,author_id,author_name,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
            (board, title[:200], content or "", author_id, author_name, _now()), fetch="id")
        return self.get_post(new_id, bump=False)

    def delete_post(self, post_id: int) -> bool:
        def _do(ex):
            n = ex("DELETE FROM posts WHERE id=%s", (int(post_id),), fetch="rowcount")
            ex("DELETE FROM comments WHERE post_id=%s", (int(post_id),))  # 댓글도 함께
            return n
        return self._tx(_do) > 0

    # ---- 댓글 ----
    def list_comments(self, post_id: int) -> list[dict]:
        rows = self._ex(
            "SELECT id,post_id,author_id,author_name,content,created_at FROM comments "
            "WHERE post_id=%s ORDER BY id ASC", (int(post_id),), fetch="all")
        return [dict(r) for r in rows]

    def create_comment(self, post_id: int, author_id: int,
                       author_name: str, content: str) -> dict:
        content = (content or "").strip()
        if not content:
            raise ValueError("댓글 내용을 입력하세요.")
        new_id = self._ex(
            "INSERT INTO comments(post_id,author_id,author_name,content,created_at) "
            "VALUES(%s,%s,%s,%s,%s) RETURNING id",
            (int(post_id), author_id, author_name, content[:1000], _now()), fetch="id")
        r = self._ex("SELECT * FROM comments WHERE id=%s", (new_id,), fetch="one")
        return dict(r)

    def get_comment(self, cid: int) -> Optional[dict]:
        r = self._ex("SELECT * FROM comments WHERE id=%s", (int(cid),), fetch="one")
        return dict(r) if r else None

    def delete_comment(self, cid: int) -> bool:
        return self._ex("DELETE FROM comments WHERE id=%s",
                        (int(cid),), fetch="rowcount") > 0

    # ---- 등급(관리자 생성·관리) ----
    def list_grades(self) -> list[dict]:
        rows = self._ex(
            "SELECT * FROM grades ORDER BY rank DESC, id ASC", (), fetch="all")
        return [dict(r) for r in rows]

    def create_grade(self, name: str, rank: int = 0, lecture: bool = False,
                     color: str = "", comment: str = "") -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("등급명을 입력하세요.")
        if self._ex("SELECT 1 FROM grades WHERE name=%s", (name,), fetch="one"):
            raise ValueError("이미 존재하는 등급명입니다.")
        self._ex(
            "INSERT INTO grades(name,rank,lecture,color,comment,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (name, int(rank), 1 if lecture else 0, color or "", comment or "", _now()))
        return self._grade_by_name(name)

    def update_grade(self, gid: int, rank: Optional[int] = None,
                     lecture: Optional[bool] = None, color: Optional[str] = None,
                     name: Optional[str] = None, comment: Optional[str] = None) -> dict:
        g = self._ex("SELECT * FROM grades WHERE id=%s", (int(gid),), fetch="one")
        if not g:
            raise ValueError("등급을 찾을 수 없습니다.")
        old = dict(g)
        new_name = old["name"]
        rename_from = None
        if name and name.strip() and name.strip() != old["name"]:
            new_name = name.strip()
            if self._ex("SELECT 1 FROM grades WHERE name=%s AND id<>%s",
                        (new_name, int(gid)), fetch="one"):
                raise ValueError("이미 존재하는 등급명입니다.")
            rename_from = old["name"]   # 이름 변경 시 해당 등급 회원들도 일괄 변경

        def _do(ex):
            if rename_from is not None:
                ex("UPDATE users SET grade=%s WHERE grade=%s", (new_name, rename_from))
            ex("UPDATE grades SET name=%s, rank=%s, lecture=%s, color=%s, comment=%s WHERE id=%s",
               (new_name,
                int(rank) if rank is not None else old["rank"],
                (1 if lecture else 0) if lecture is not None else old["lecture"],
                color if color is not None else old["color"],
                comment if comment is not None else (old["comment"] if "comment" in old else ""),
                int(gid)))
        self._tx(_do)
        return self._grade_by_name(new_name)

    def delete_grade(self, gid: int) -> dict:
        g = self._ex("SELECT * FROM grades WHERE id=%s", (int(gid),), fetch="one")
        if not g:
            raise ValueError("등급을 찾을 수 없습니다.")
        if g["name"] == "무료":
            raise ValueError("'무료' 등급은 삭제할 수 없습니다.")
        used = self._ex("SELECT COUNT(*) AS c FROM users WHERE grade=%s",
                        (g["name"],), fetch="val")

        def _do(ex):
            if used:
                # 사용 중이면 그 회원들을 '무료'로 강등 후 삭제
                ex("UPDATE users SET grade='무료' WHERE grade=%s", (g["name"],))
            ex("DELETE FROM grades WHERE id=%s", (int(gid),))
        self._tx(_do)
        return {"deleted": g["name"], "downgraded": used}

    def _grade_by_name(self, name: str) -> Optional[dict]:
        r = self._ex("SELECT * FROM grades WHERE name=%s", (name,), fetch="one")
        return dict(r) if r else None

    def grade_counts(self) -> dict:
        """등급명 → 회원 수."""
        rows = self._ex(
            "SELECT grade, COUNT(*) AS c FROM users GROUP BY grade", (), fetch="all")
        return {r["grade"]: r["c"] for r in rows}

    def grade_can_lecture(self, grade_name: Optional[str]) -> bool:
        """해당 등급명이 등급전용 게시판 이용 가능한지(grades.lecture=1)."""
        if not grade_name:
            return False
        r = self._ex("SELECT lecture FROM grades WHERE name=%s", (grade_name,), fetch="one")
        return bool(r and r["lecture"])

    # ---- 게시판 권한 설정 ----
    def ensure_board(self, board: str, title: str, read_perm: str,
                     write_perm: str, comment_perm: str) -> None:
        """없으면 기본값으로 게시판 설정 행 생성(있으면 유지)."""
        self._ex(
            "INSERT INTO board_settings"
            "(board,title,read_perm,write_perm,comment_perm,updated_at) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (board, title, read_perm, write_perm, comment_perm, _now()))

    def get_board_setting(self, board: str) -> Optional[dict]:
        r = self._ex(
            "SELECT * FROM board_settings WHERE board=%s", (board,), fetch="one")
        return dict(r) if r else None

    def list_board_settings(self) -> list[dict]:
        rows = self._ex("SELECT * FROM board_settings", (), fetch="all")
        return [dict(r) for r in rows]

    def update_board_setting(self, board: str, title: Optional[str] = None,
                             read_perm: Optional[str] = None,
                             write_perm: Optional[str] = None,
                             comment_perm: Optional[str] = None) -> dict:
        s = self.get_board_setting(board)
        if not s:
            raise ValueError("존재하지 않는 게시판입니다.")
        READ = {"all", "user", "grade"}
        WRITE = {"admin", "user", "grade"}
        COMMENT = {"off", "user", "grade"}
        if read_perm is not None and read_perm not in READ:
            raise ValueError("읽기 권한 값이 올바르지 않습니다.")
        if write_perm is not None and write_perm not in WRITE:
            raise ValueError("쓰기 권한 값이 올바르지 않습니다.")
        if comment_perm is not None and comment_perm not in COMMENT:
            raise ValueError("댓글 권한 값이 올바르지 않습니다.")
        self._ex(
            "UPDATE board_settings SET title=%s, read_perm=%s, write_perm=%s, "
            "comment_perm=%s, updated_at=%s WHERE board=%s",
            (title if title is not None else s["title"],
             read_perm if read_perm is not None else s["read_perm"],
             write_perm if write_perm is not None else s["write_perm"],
             comment_perm if comment_perm is not None else s["comment_perm"],
             _now(), board))
        return self.get_board_setting(board)

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.close()
            except Exception:
                pass
