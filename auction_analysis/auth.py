"""
회원 인증 + 세션 + 관심물건 저장소 (표준 라이브러리만 사용).

- 비밀번호: pbkdf2_hmac(sha256) + 사용자별 salt. 외부 의존(bcrypt) 없이 안전.
- 세션: 랜덤 토큰을 SQLite에 저장, httponly 쿠키로 전달.
- 관심물건: (user_id, case_no) 매핑.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Optional


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


def _user_dict(r: sqlite3.Row) -> dict:
    keys = r.keys()
    return {
        "id": r["id"], "email": r["email"], "name": r["name"],
        "grade": r["grade"], "created_at": r["created_at"],
        "provider": r["provider"] if "provider" in keys else "local",
        "role": (r["role"] if "role" in keys else "user") or "user",
        "phone": (r["phone"] if "phone" in keys else "") or "",
        "mileage": (r["mileage"] if "mileage" in keys else 0) or 0,
        "paid_until": (r["paid_until"] if "paid_until" in keys else None) or None,
    }


class UserStore:
    def __init__(self, db_path: str = ":memory:"):
        # 영구볼륨 경로(예: /data/auth.db) 지정 시 부모 디렉터리 자동 생성(없으면 connect 실패 방지)
        if db_path != ":memory:":
            import os
            d = os.path.dirname(db_path)
            if d:
                os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT DEFAULT '',
                name TEXT DEFAULT '',
                grade TEXT DEFAULT '무료',
                provider TEXT DEFAULT 'local',
                provider_id TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions(
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS favorites(
                user_id INTEGER NOT NULL,
                case_no TEXT NOT NULL,
                created_at TEXT,
                PRIMARY KEY(user_id, case_no)
            );
            CREATE TABLE IF NOT EXISTS gongmae_favorites(
                user_id INTEGER NOT NULL,
                manage_no TEXT NOT NULL,
                data TEXT,
                folder TEXT DEFAULT '기타',
                memo TEXT DEFAULT '',
                created_at TEXT,
                PRIMARY KEY(user_id, manage_no)
            );
            CREATE TABLE IF NOT EXISTS mock_bids(
                user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                case_no TEXT,
                bid_amount INTEGER NOT NULL,   -- 입찰가(원)
                data TEXT,                     -- 물건 스냅샷 JSON
                created_at TEXT,
                PRIMARY KEY(user_id, item_key)
            );
            CREATE TABLE IF NOT EXISTS mileage_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,       -- +적립 / -차감
                balance INTEGER NOT NULL,      -- 거래 후 잔액
                reason TEXT DEFAULT '',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS coupons(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT DEFAULT '',
                amount INTEGER NOT NULL,        -- 사용 시 적립되는 마일리지
                user_id INTEGER,                -- 지정 회원(NULL=아무나 사용 가능 코드)
                used INTEGER DEFAULT 0,         -- 0 미사용 / 1 사용됨
                used_by INTEGER,
                used_at TEXT,
                expires_at TEXT,                -- YYYY-MM-DD (NULL=무기한)
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS payments(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                merchant_uid TEXT UNIQUE NOT NULL,  -- 주문번호(우리 발급)
                user_id INTEGER NOT NULL,
                plan TEXT NOT NULL,                 -- 요금제 코드(m1/m3/m6/m12)
                months INTEGER NOT NULL,            -- 이용 개월수
                amount INTEGER NOT NULL,            -- 결제금액(원)
                status TEXT DEFAULT 'ready',        -- ready | paid | failed
                imp_uid TEXT,                       -- 포트원 결제 고유번호
                grade TEXT DEFAULT '전국',          -- 결제 시 부여 등급
                paid_at TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS posts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                board TEXT NOT NULL,                -- 게시판 키(yejung/video/community/data)
                title TEXT NOT NULL,
                content TEXT DEFAULT '',
                author_id INTEGER,
                author_name TEXT DEFAULT '',
                views INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS comments(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                author_id INTEGER,
                author_name TEXT DEFAULT '',
                content TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS grades(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                rank INTEGER DEFAULT 0,             -- 등급 높이(정렬)
                lecture INTEGER DEFAULT 0,          -- 등급전용 게시판 이용 가능(1)
                color TEXT DEFAULT '',              -- 배지 색(선택)
                comment TEXT DEFAULT '',            -- 등급별 코멘트/메모
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS board_settings(
                board TEXT PRIMARY KEY,             -- 게시판 키(yejung/video/community/data)
                title TEXT,
                read_perm TEXT DEFAULT 'all',       -- 읽기: all|user|grade
                write_perm TEXT DEFAULT 'admin',    -- 쓰기: admin|user|grade
                comment_perm TEXT DEFAULT 'user',   -- 댓글: off|user|grade
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS user_folders(
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,                 -- 개인폴더명(회원별)
                created_at TEXT,
                PRIMARY KEY(user_id, name)
            );
        """)
        # 기존 DB 마이그레이션
        self._ensure_column("users", "provider", "TEXT DEFAULT 'local'")
        self._ensure_column("users", "provider_id", "TEXT")
        self._ensure_column("users", "role", "TEXT DEFAULT 'user'")  # user | admin
        self._ensure_column("users", "phone", "TEXT DEFAULT ''")     # 휴대폰(본인인증 완료번호)
        self._ensure_column("users", "mileage", "INTEGER DEFAULT 0") # 마일리지 잔액
        self._ensure_column("users", "paid_until", "TEXT")           # 유료 이용권 만료일 YYYY-MM-DD(NULL=없음)
        self._ensure_column("grades", "comment", "TEXT DEFAULT ''")  # 등급별 코멘트
        self._ensure_column("favorites", "folder", "TEXT DEFAULT '기타'")    # 관심물건 폴더
        self._ensure_column("favorites", "importance", "INTEGER DEFAULT 0")  # 중요도 0~5
        self._ensure_column("favorites", "memo", "TEXT DEFAULT ''")          # 메모
        self._ensure_column("favorites", "notify", "INTEGER DEFAULT 1")      # 알림/입찰달력 표시(1)/제외(0)
        self._ensure_column("user_folders", "sort_order", "INTEGER DEFAULT 0")  # 개인폴더 정렬순서(▲▼)
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_provider "
            "ON users(provider, provider_id)")
        self.conn.commit()
        self._seed_grades()

    def _seed_grades(self) -> None:
        """기본 등급 시드(없을 때만): 무료(강의X), 전국(결제 부여·강의O)."""
        have = self.conn.execute("SELECT COUNT(*) FROM grades").fetchone()[0]
        if have:
            return
        for name, rank, lec, color in [("무료", 0, 0, "#9a9a93"),
                                       ("전국", 10, 1, "#1f4fa3")]:
            self.conn.execute(
                "INSERT OR IGNORE INTO grades(name,rank,lecture,color,created_at) "
                "VALUES(?,?,?,?,?)", (name, rank, lec, color, _now()))
        self.conn.commit()

    def _ensure_column(self, table: str, col: str, decl: str) -> None:
        cols = [c[1] for c in self.conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if col not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    # ---- 회원 ----
    def create_user(self, email: str, password: str, name: str = "", phone: str = "") -> dict:
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError("올바른 이메일을 입력하세요.")
        if len(password) < 6:
            raise ValueError("비밀번호는 6자 이상이어야 합니다.")
        if self.conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise ValueError("이미 가입된 이메일입니다.")
        if phone and self.conn.execute(
                "SELECT 1 FROM users WHERE phone=? AND phone<>''", (phone,)).fetchone():
            raise ValueError("이미 가입에 사용된 연락처입니다.")
        cur = self.conn.execute(
            "INSERT INTO users(email,password,name,phone,created_at) VALUES(?,?,?,?,?)",
            (email, hash_password(password), name, phone, _now()))
        self.conn.commit()
        return self.get_user(cur.lastrowid)

    def phone_exists(self, phone: str) -> bool:
        if not phone:
            return False
        return self.conn.execute(
            "SELECT 1 FROM users WHERE phone=? AND phone<>''", (phone,)).fetchone() is not None

    def get_user(self, uid: int) -> Optional[dict]:
        r = self.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return _user_dict(r) if r else None

    def authenticate(self, email: str, password: str) -> Optional[dict]:
        email = email.strip().lower()
        r = self.conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        # 소셜 회원(password 빈값)은 이메일/비번 로그인 불가
        if r and r["password"] and verify_password(password, r["password"]):
            return _user_dict(r)
        return None

    def get_or_create_social_user(self, provider: str, provider_id: str,
                                  email: Optional[str] = None, name: str = "") -> dict:
        """소셜 로그인(카카오 등) 회원 조회 또는 신규 생성. provider_id 기준."""
        provider_id = str(provider_id)
        r = self.conn.execute(
            "SELECT * FROM users WHERE provider=? AND provider_id=?",
            (provider, provider_id)).fetchone()
        if r:
            return _user_dict(r)

        # email 미동의 시 합성 이메일로 UNIQUE 제약 충족. 충돌 시에도 합성으로 폴백.
        synthetic = f"{provider}_{provider_id}@{provider}.local"
        use_email = (email or "").strip().lower() or synthetic
        params = (use_email, "", name or f"{provider}회원", "무료",
                  provider, provider_id, _now())
        sql = ("INSERT INTO users(email,password,name,grade,provider,provider_id,created_at) "
               "VALUES(?,?,?,?,?,?,?)")
        try:
            cur = self.conn.execute(sql, params)
        except sqlite3.IntegrityError:
            cur = self.conn.execute(sql, (synthetic,) + params[1:])
        self.conn.commit()
        return self.get_user(cur.lastrowid)

    # ---- 권한(관리자) ----
    def set_admin(self, *, name: Optional[str] = None, email: Optional[str] = None) -> int:
        """이름 또는 이메일로 관리자 지정. 적용된 행 수 반환."""
        n = 0
        if name:
            cur = self.conn.execute("UPDATE users SET role='admin' WHERE name=?", (name,))
            n += cur.rowcount
        if email:
            cur = self.conn.execute("UPDATE users SET role='admin' WHERE email=?",
                                    (email.strip().lower(),))
            n += cur.rowcount
        self.conn.commit()
        return n

    def list_admins(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM users WHERE role='admin'").fetchall()
        return [_user_dict(r) for r in rows]

    # ---- 세션 ----
    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        self.conn.execute(
            "INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)",
            (token, user_id, _now()))
        self.conn.commit()
        return token

    def get_user_by_session(self, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None
        r = self.conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (token,)).fetchone()
        return _user_dict(r) if r else None

    def delete_session(self, token: Optional[str]) -> None:
        if token:
            self.conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            self.conn.commit()

    # ---- 관심물건 ----
    def add_favorite(self, user_id: int, case_no: str, folder: str = "기타",
                     importance: int = 0, memo: str = "", notify: int = 1) -> None:
        """관심물건 등록/수정(UPSERT) — 폴더·중요도·메모·알림 포함. created_at은 최초만 기록."""
        self.conn.execute(
            "INSERT INTO favorites(user_id,case_no,created_at,folder,importance,memo,notify) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id,case_no) DO UPDATE SET "
            "folder=excluded.folder, importance=excluded.importance, "
            "memo=excluded.memo, notify=excluded.notify",
            (user_id, case_no, _now(), folder or "기타",
             max(0, min(5, int(importance or 0))), memo or "", 1 if notify else 0))
        self.conn.commit()

    def remove_favorite(self, user_id: int, case_no: str) -> None:
        self.conn.execute(
            "DELETE FROM favorites WHERE user_id=? AND case_no=?", (user_id, case_no))
        self.conn.commit()

    def list_favorites(self, user_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT case_no FROM favorites WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)).fetchall()
        return [r["case_no"] for r in rows]

    def list_favorites_full(self, user_id: int) -> list:
        """관심물건 전체(메타 포함, 저장 역순)."""
        rows = self.conn.execute(
            "SELECT case_no, folder, importance, memo, notify, created_at FROM favorites "
            "WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_favorite(self, user_id: int, case_no: str):
        """관심물건 1건 메타(folder/importance/memo/notify) 또는 None."""
        r = self.conn.execute(
            "SELECT case_no, folder, importance, memo, notify FROM favorites "
            "WHERE user_id=? AND case_no=?", (user_id, case_no)).fetchone()
        return dict(r) if r else None

    def is_favorite(self, user_id: int, case_no: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM favorites WHERE user_id=? AND case_no=?",
            (user_id, case_no)).fetchone() is not None

    # ---- 관심공매물건(온비드, 실시간 API라 스냅샷 보관) ----
    def add_gongmae_fav(self, user_id: int, manage_no: str, data: str = "",
                        folder: str = "기타", memo: str = "") -> None:
        """관심공매물건 등록/수정(UPSERT). data=공매물건 스냅샷 JSON."""
        self.conn.execute(
            "INSERT INTO gongmae_favorites(user_id,manage_no,data,folder,memo,created_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,manage_no) DO UPDATE SET "
            "data=excluded.data, folder=excluded.folder, memo=excluded.memo",
            (user_id, manage_no, data or "", folder or "기타", memo or "", _now()))
        self.conn.commit()

    def remove_gongmae_fav(self, user_id: int, manage_no: str) -> None:
        self.conn.execute(
            "DELETE FROM gongmae_favorites WHERE user_id=? AND manage_no=?",
            (user_id, manage_no))
        self.conn.commit()

    def list_gongmae_favs(self, user_id: int) -> list:
        rows = self.conn.execute(
            "SELECT manage_no, data, folder, memo, created_at FROM gongmae_favorites "
            "WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def is_gongmae_fav(self, user_id: int, manage_no: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM gongmae_favorites WHERE user_id=? AND manage_no=?",
            (user_id, manage_no)).fetchone() is not None

    # ---- 모의입찰(연습) ----
    def add_mock_bid(self, user_id: int, item_key: str, bid_amount: int,
                     case_no: str = "", data: str = "") -> None:
        """모의입찰 등록/수정(UPSERT). data=물건 스냅샷 JSON, bid_amount=원 단위."""
        self.conn.execute(
            "INSERT INTO mock_bids(user_id,item_key,case_no,bid_amount,data,created_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,item_key) DO UPDATE SET "
            "bid_amount=excluded.bid_amount, data=excluded.data, "
            "case_no=excluded.case_no, created_at=excluded.created_at",
            (user_id, item_key, case_no or "", int(bid_amount or 0), data or "", _now()))
        self.conn.commit()

    def remove_mock_bid(self, user_id: int, item_key: str) -> None:
        self.conn.execute("DELETE FROM mock_bids WHERE user_id=? AND item_key=?",
                          (user_id, item_key))
        self.conn.commit()

    def list_mock_bids(self, user_id: int) -> list:
        rows = self.conn.execute(
            "SELECT item_key, case_no, bid_amount, data, created_at FROM mock_bids "
            "WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_mock_bid(self, user_id: int, item_key: str):
        r = self.conn.execute(
            "SELECT bid_amount, created_at FROM mock_bids WHERE user_id=? AND item_key=?",
            (user_id, item_key)).fetchone()
        return dict(r) if r else None

    # ---- 개인폴더 ----
    def list_folders(self, user_id: int) -> list:
        rows = self.conn.execute(
            "SELECT name FROM user_folders WHERE user_id=? ORDER BY sort_order, created_at",
            (user_id,)).fetchall()
        return [r["name"] for r in rows]

    def add_folder(self, user_id: int, name: str) -> None:
        name = (name or "").strip()
        if name:
            mx = self.conn.execute(
                "SELECT COALESCE(MAX(sort_order),-1) FROM user_folders WHERE user_id=?",
                (user_id,)).fetchone()[0]
            self.conn.execute(
                "INSERT OR IGNORE INTO user_folders(user_id,name,created_at,sort_order) VALUES(?,?,?,?)",
                (user_id, name, _now(), mx + 1))
            self.conn.commit()

    def rename_folder(self, user_id: int, old: str, new: str) -> bool:
        """개인폴더 이름변경 — 그 폴더의 관심물건도 새 이름으로 이동. 중복명/없음이면 False."""
        old = (old or "").strip(); new = (new or "").strip()
        if not old or not new or old == new:
            return False
        if self.conn.execute("SELECT 1 FROM user_folders WHERE user_id=? AND name=?",
                             (user_id, new)).fetchone():
            return False                                          # 같은 이름 이미 있음
        cur = self.conn.execute("UPDATE user_folders SET name=? WHERE user_id=? AND name=?",
                               (new, user_id, old))
        if cur.rowcount:
            self.conn.execute("UPDATE favorites SET folder=? WHERE user_id=? AND folder=?",
                             (new, user_id, old))
        self.conn.commit()
        return cur.rowcount > 0

    def reorder_folders(self, user_id: int, names: list) -> None:
        """개인폴더 정렬순서 일괄 지정(▲▼) — names 순서대로 sort_order=index."""
        for i, name in enumerate(names or []):
            self.conn.execute("UPDATE user_folders SET sort_order=? WHERE user_id=? AND name=?",
                             (i, user_id, name))
        self.conn.commit()

    def remove_folder(self, user_id: int, name: str) -> None:
        """개인폴더 삭제 — 그 폴더의 관심물건은 '기타'로 이동(고아 방지)."""
        self.conn.execute("DELETE FROM user_folders WHERE user_id=? AND name=?",
                          (user_id, name))
        self.conn.execute("UPDATE favorites SET folder='기타' WHERE user_id=? AND folder=?",
                          (user_id, name))
        self.conn.commit()

    # ---- 회원관리(관리자) ----
    def list_users(self, q: str = "", limit: int = 500) -> list[dict]:
        """회원 목록(최근 가입순). q로 이름/이메일/연락처 부분검색."""
        sql = "SELECT * FROM users"
        params: tuple = ()
        if q:
            like = f"%{q.strip()}%"
            sql += " WHERE name LIKE ? OR email LIKE ? OR phone LIKE ?"
            params = (like, like, like)
        sql += " ORDER BY id DESC LIMIT ?"
        params = params + (int(limit),)
        return [_user_dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def set_grade(self, uid: int, grade: str) -> Optional[dict]:
        self.conn.execute("UPDATE users SET grade=? WHERE id=?", (grade, uid))
        self.conn.commit()
        return self.get_user(uid)

    def set_role(self, uid: int, role: str) -> Optional[dict]:
        if role not in ("user", "admin"):
            raise ValueError("role은 user 또는 admin")
        self.conn.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
        self.conn.commit()
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
        self.conn.execute("UPDATE users SET mileage=? WHERE id=?", (new_bal, uid))
        self.conn.execute(
            "INSERT INTO mileage_log(user_id,amount,balance,reason,created_at) VALUES(?,?,?,?,?)",
            (uid, amount, new_bal, reason or ("적립" if amount >= 0 else "차감"), _now()))
        self.conn.commit()
        return self.get_user(uid)

    def mileage_log(self, uid: int, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT amount,balance,reason,created_at FROM mileage_log "
            "WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, int(limit))).fetchall()
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
            self.conn.execute(
                "INSERT INTO coupons(code,name,amount,user_id,expires_at,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (code, name or "쿠폰", amount, user_id, expires_at or None, _now()))
            out.append(self._coupon_by_code(code))
        self.conn.commit()
        return out

    def _coupon_by_code(self, code: str) -> Optional[dict]:
        r = self.conn.execute("SELECT * FROM coupons WHERE code=?", (code,)).fetchone()
        return dict(r) if r else None

    def list_coupons(self, limit: int = 500) -> list[dict]:
        rows = self.conn.execute(
            "SELECT c.*, u.name AS user_name, u.email AS user_email "
            "FROM coupons c LEFT JOIN users u ON u.id=c.user_id "
            "ORDER BY c.id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def delete_coupon(self, coupon_id: int) -> bool:
        """미사용 쿠폰만 회수(삭제). 사용된 쿠폰은 보존."""
        cur = self.conn.execute("DELETE FROM coupons WHERE id=? AND used=0", (int(coupon_id),))
        self.conn.commit()
        return cur.rowcount > 0

    def redeem_coupon(self, code: str, user_id: int) -> dict:
        """회원이 쿠폰코드 사용 → 마일리지 적립. {mileage, amount, name} 반환."""
        code = (code or "").strip().upper()
        r = self.conn.execute("SELECT * FROM coupons WHERE code=?", (code,)).fetchone()
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
        self.conn.execute(
            "UPDATE coupons SET used=1, used_by=?, used_at=? WHERE id=?",
            (user_id, _now(), r["id"]))
        self.conn.commit()
        u = self.adjust_mileage(user_id, r["amount"], f"쿠폰 사용: {r['name']} ({code})")
        return {"mileage": u["mileage"], "amount": r["amount"], "name": r["name"]}

    # ---- 결제(이용권) ----
    def create_payment(self, user_id: int, merchant_uid: str, plan: str,
                       months: int, amount: int, grade: str = "전국") -> dict:
        """주문 생성(결제 전 'ready'). merchant_uid·금액을 서버가 확정해 변조 방지."""
        self.conn.execute(
            "INSERT INTO payments(merchant_uid,user_id,plan,months,amount,status,grade,created_at) "
            "VALUES(?,?,?,?,?,'ready',?,?)",
            (merchant_uid, user_id, plan, int(months), int(amount), grade, _now()))
        self.conn.commit()
        return self.get_payment(merchant_uid)

    def get_payment(self, merchant_uid: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT * FROM payments WHERE merchant_uid=?", (merchant_uid,)).fetchone()
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
            self.conn.execute(
                "UPDATE payments SET status='failed', imp_uid=? WHERE merchant_uid=?",
                (imp_uid, merchant_uid))
            self.conn.commit()
            raise ValueError(f"결제금액 불일치(주문 {p['amount']}원 / 결제 {paid_amount}원)")
        u = self._grant_membership(p["user_id"], int(p["months"]), p["grade"])
        self.conn.execute(
            "UPDATE payments SET status='paid', imp_uid=?, paid_at=? WHERE merchant_uid=?",
            (imp_uid, _now(), merchant_uid))
        self.conn.commit()
        return {"already": False, "user": u, "payment": self.get_payment(merchant_uid)}

    def _grant_membership(self, uid: int, months: int, grade: str) -> dict:
        """이용권 부여: 만료일을 max(오늘, 기존만료일)에서 months개월 연장 + 등급 설정."""
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
        # months개월 연장(달력월 근사: 30일×개월 + 윤년무관 단순). 정확한 월말처리 대신 30일 기준.
        new_until = base + timedelta(days=30 * int(months))
        self.conn.execute("UPDATE users SET paid_until=?, grade=? WHERE id=?",
                          (new_until.isoformat(), grade, uid))
        self.conn.commit()
        return self.get_user(uid)

    def grant_membership_admin(self, uid: int, months: int, grade: str = "전국") -> dict:
        """관리자 수동 부여(무통장입금 확인 등)."""
        return self._grant_membership(uid, months, grade)

    def list_payments(self, limit: int = 300) -> list[dict]:
        rows = self.conn.execute(
            "SELECT p.*, u.name AS user_name, u.email AS user_email "
            "FROM payments p LEFT JOIN users u ON u.id=p.user_id "
            "ORDER BY p.id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    # ---- 게시판 ----
    def list_posts(self, board: str, page: int = 1, size: int = 15) -> dict:
        page = max(1, int(page)); size = max(1, min(int(size), 100))
        tot = self.conn.execute(
            "SELECT COUNT(*) FROM posts WHERE board=?", (board,)).fetchone()[0]
        rows = self.conn.execute(
            "SELECT id,board,title,author_name,views,created_at FROM posts "
            "WHERE board=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (board, size, (page - 1) * size)).fetchall()
        return {"total": tot, "page": page, "size": size,
                "items": [dict(r) for r in rows]}

    def get_post(self, post_id: int, bump: bool = True) -> Optional[dict]:
        if bump:                                   # 먼저 증가 → 반환 row가 현재 조회수 반영
            cur = self.conn.execute(
                "UPDATE posts SET views=views+1 WHERE id=?", (int(post_id),))
            if cur.rowcount:
                self.conn.commit()
        r = self.conn.execute("SELECT * FROM posts WHERE id=?", (int(post_id),)).fetchone()
        return dict(r) if r else None

    def create_post(self, board: str, title: str, content: str,
                    author_id: int, author_name: str = "") -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("제목을 입력하세요.")
        cur = self.conn.execute(
            "INSERT INTO posts(board,title,content,author_id,author_name,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (board, title[:200], content or "", author_id, author_name, _now()))
        self.conn.commit()
        return self.get_post(cur.lastrowid, bump=False)

    def delete_post(self, post_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM posts WHERE id=?", (int(post_id),))
        self.conn.execute("DELETE FROM comments WHERE post_id=?", (int(post_id),))  # 댓글도 함께
        self.conn.commit()
        return cur.rowcount > 0

    # ---- 댓글 ----
    def list_comments(self, post_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id,post_id,author_id,author_name,content,created_at FROM comments "
            "WHERE post_id=? ORDER BY id ASC", (int(post_id),)).fetchall()
        return [dict(r) for r in rows]

    def create_comment(self, post_id: int, author_id: int,
                       author_name: str, content: str) -> dict:
        content = (content or "").strip()
        if not content:
            raise ValueError("댓글 내용을 입력하세요.")
        cur = self.conn.execute(
            "INSERT INTO comments(post_id,author_id,author_name,content,created_at) "
            "VALUES(?,?,?,?,?)", (int(post_id), author_id, author_name, content[:1000], _now()))
        self.conn.commit()
        r = self.conn.execute("SELECT * FROM comments WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(r)

    def get_comment(self, cid: int) -> Optional[dict]:
        r = self.conn.execute("SELECT * FROM comments WHERE id=?", (int(cid),)).fetchone()
        return dict(r) if r else None

    def delete_comment(self, cid: int) -> bool:
        cur = self.conn.execute("DELETE FROM comments WHERE id=?", (int(cid),))
        self.conn.commit()
        return cur.rowcount > 0

    # ---- 등급(관리자 생성·관리) ----
    def list_grades(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM grades ORDER BY rank DESC, id ASC").fetchall()
        return [dict(r) for r in rows]

    def create_grade(self, name: str, rank: int = 0, lecture: bool = False,
                     color: str = "", comment: str = "") -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("등급명을 입력하세요.")
        if self.conn.execute("SELECT 1 FROM grades WHERE name=?", (name,)).fetchone():
            raise ValueError("이미 존재하는 등급명입니다.")
        self.conn.execute(
            "INSERT INTO grades(name,rank,lecture,color,comment,created_at) VALUES(?,?,?,?,?,?)",
            (name, int(rank), 1 if lecture else 0, color or "", comment or "", _now()))
        self.conn.commit()
        return self._grade_by_name(name)

    def update_grade(self, gid: int, rank: Optional[int] = None,
                     lecture: Optional[bool] = None, color: Optional[str] = None,
                     name: Optional[str] = None, comment: Optional[str] = None) -> dict:
        g = self.conn.execute("SELECT * FROM grades WHERE id=?", (int(gid),)).fetchone()
        if not g:
            raise ValueError("등급을 찾을 수 없습니다.")
        old = dict(g)
        new_name = old["name"]
        if name and name.strip() and name.strip() != old["name"]:
            new_name = name.strip()
            if self.conn.execute("SELECT 1 FROM grades WHERE name=? AND id<>?",
                                 (new_name, int(gid))).fetchone():
                raise ValueError("이미 존재하는 등급명입니다.")
            # 이름 변경 시 해당 등급 회원들도 일괄 변경
            self.conn.execute("UPDATE users SET grade=? WHERE grade=?", (new_name, old["name"]))
        self.conn.execute(
            "UPDATE grades SET name=?, rank=?, lecture=?, color=?, comment=? WHERE id=?",
            (new_name,
             int(rank) if rank is not None else old["rank"],
             (1 if lecture else 0) if lecture is not None else old["lecture"],
             color if color is not None else old["color"],
             comment if comment is not None else (old["comment"] if "comment" in old else ""),
             int(gid)))
        self.conn.commit()
        return self._grade_by_name(new_name)

    def delete_grade(self, gid: int) -> dict:
        g = self.conn.execute("SELECT * FROM grades WHERE id=?", (int(gid),)).fetchone()
        if not g:
            raise ValueError("등급을 찾을 수 없습니다.")
        if g["name"] == "무료":
            raise ValueError("'무료' 등급은 삭제할 수 없습니다.")
        used = self.conn.execute("SELECT COUNT(*) FROM users WHERE grade=?",
                                 (g["name"],)).fetchone()[0]
        if used:
            # 사용 중이면 그 회원들을 '무료'로 강등 후 삭제
            self.conn.execute("UPDATE users SET grade='무료' WHERE grade=?", (g["name"],))
        self.conn.execute("DELETE FROM grades WHERE id=?", (int(gid),))
        self.conn.commit()
        return {"deleted": g["name"], "downgraded": used}

    def _grade_by_name(self, name: str) -> Optional[dict]:
        r = self.conn.execute("SELECT * FROM grades WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None

    def grade_counts(self) -> dict:
        """등급명 → 회원 수."""
        rows = self.conn.execute(
            "SELECT grade, COUNT(*) c FROM users GROUP BY grade").fetchall()
        return {r["grade"]: r["c"] for r in rows}

    def grade_can_lecture(self, grade_name: Optional[str]) -> bool:
        """해당 등급명이 등급전용 게시판 이용 가능한지(grades.lecture=1)."""
        if not grade_name:
            return False
        r = self.conn.execute("SELECT lecture FROM grades WHERE name=?", (grade_name,)).fetchone()
        return bool(r and r["lecture"])

    # ---- 게시판 권한 설정 ----
    def ensure_board(self, board: str, title: str, read_perm: str,
                     write_perm: str, comment_perm: str) -> None:
        """없으면 기본값으로 게시판 설정 행 생성(있으면 유지)."""
        self.conn.execute(
            "INSERT OR IGNORE INTO board_settings"
            "(board,title,read_perm,write_perm,comment_perm,updated_at) VALUES(?,?,?,?,?,?)",
            (board, title, read_perm, write_perm, comment_perm, _now()))
        self.conn.commit()

    def get_board_setting(self, board: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT * FROM board_settings WHERE board=?", (board,)).fetchone()
        return dict(r) if r else None

    def list_board_settings(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM board_settings").fetchall()
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
        self.conn.execute(
            "UPDATE board_settings SET title=?, read_perm=?, write_perm=?, "
            "comment_perm=?, updated_at=? WHERE board=?",
            (title if title is not None else s["title"],
             read_perm if read_perm is not None else s["read_perm"],
             write_perm if write_perm is not None else s["write_perm"],
             comment_perm if comment_perm is not None else s["comment_perm"],
             _now(), board))
        self.conn.commit()
        return self.get_board_setting(board)

    def close(self) -> None:
        self.conn.close()
