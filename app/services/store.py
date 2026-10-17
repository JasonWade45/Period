"""تخزين محلي ودائم للدورات والأعراض والملفات الشخصية والحسابات (SQLite).

ملاحظات خصوصية وأمان:
- يدعم هذا المخزن وضعين متكاملين:
  1) الوضع الموثّق بحساب حقيقي (`user:<id>`): حساب باسم مستخدم/بريد وكلمة مرور
     مشفّرة بـ PBKDF2-HMAC-SHA256 وجلسات قابلة للتجديد والإبطال، مع حفظ دائم
     للدورة والأعراض والملف الشخصي على الخادم.
  2) الوضع المحلي بلا حساب (`user_key` عشوائي): لمن تفضّل عدم إنشاء حساب، مع
     إمكانية الترقية وترحيل كل السجلات تلقائيًا إلى حساب حقيقي في أي وقت.
- لا نخزّن نص رسائل المحادثة هنا؛ سجل التدقيق منفصل (audit/) وبلا PII.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .auth import (
    AuthError,
    REFRESH_TOKEN_TTL_SECONDS,
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    hash_secret,
    verify_secret,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT    NOT NULL UNIQUE,
    display_name   TEXT    NOT NULL DEFAULT '',
    password_hash  TEXT    NOT NULL,
    password_salt  TEXT    NOT NULL,
    pin_hash       TEXT    NOT NULL DEFAULT '',
    pin_salt       TEXT    NOT NULL DEFAULT '',
    disguise_mode  INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT    NOT NULL UNIQUE,
    expires_at         TEXT    NOT NULL,
    revoked_at         TEXT,
    created_at         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions (user_id);

CREATE TABLE IF NOT EXISTS profiles (
    user_key                  TEXT PRIMARY KEY,
    user_id                   INTEGER REFERENCES users(id) ON DELETE CASCADE,
    goal                      TEXT NOT NULL DEFAULT 'track',
    age                       INTEGER,
    country_code              TEXT NOT NULL DEFAULT 'EG',
    pregnancy_status          TEXT,
    contraception             TEXT,
    diagnosed_conditions_json TEXT NOT NULL DEFAULT '[]',
    medications_json          TEXT NOT NULL DEFAULT '[]',
    typical_cycle_days        INTEGER,
    typical_bleeding_days     INTEGER,
    onboarding_completed      INTEGER NOT NULL DEFAULT 0,
    updated_at                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_profiles_user_id ON profiles (user_id);

CREATE TABLE IF NOT EXISTS cycles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key    TEXT    NOT NULL,
    start_date  TEXT    NOT NULL,              -- ISO date لأول يوم نزيف
    length_days INTEGER,                       -- طول النزيف بالأيام (اختياري)
    created_at  TEXT    NOT NULL,
    UNIQUE (user_key, start_date)
);
CREATE INDEX IF NOT EXISTS idx_cycles_user ON cycles (user_key, start_date);

CREATE TABLE IF NOT EXISTS daily_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key    TEXT    NOT NULL,
    log_date    TEXT    NOT NULL,
    flow        TEXT,
    mood        TEXT,
    sleep_hours REAL,
    water_cups  INTEGER,
    exercise    TEXT,
    medications TEXT,
    note        TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (user_key, log_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_logs_user ON daily_logs (user_key, log_date);

CREATE TABLE IF NOT EXISTS symptoms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key   TEXT    NOT NULL,
    log_date   TEXT    NOT NULL,
    symptom    TEXT    NOT NULL,
    severity   INTEGER,                        -- 1..5 (شدة ذكرتها المستخدمة)
    note       TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symptoms_user ON symptoms (user_key, log_date);

CREATE TABLE IF NOT EXISTS predictions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key     TEXT    NOT NULL,
    cycle_id     INTEGER,
    window_start TEXT    NOT NULL,
    window_end   TEXT    NOT NULL,
    confidence   TEXT    NOT NULL,
    basis_json   TEXT    NOT NULL DEFAULT '{}',
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_user ON predictions (user_key, window_start);

CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key   TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    time_hhmm  TEXT    NOT NULL DEFAULT '20:00',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders (user_key);

CREATE TABLE IF NOT EXISTS consents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key     TEXT    NOT NULL,
    consent_type TEXT    NOT NULL,
    granted_at   TEXT,
    revoked_at   TEXT,
    updated_at   TEXT    NOT NULL,
    UNIQUE (user_key, consent_type)
);
CREATE INDEX IF NOT EXISTS idx_consents_user ON consents (user_key, consent_type);

CREATE TABLE IF NOT EXISTS partner_shares (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key    TEXT    NOT NULL,
    share_token TEXT    NOT NULL UNIQUE,
    partner_label TEXT  NOT NULL DEFAULT '',
    scope       TEXT    NOT NULL DEFAULT 'next_window_only',
    revoked_at  TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_partner_shares_user ON partner_shares (user_key);
"""

SEVERITY_MIN, SEVERITY_MAX = 1, 5
# طول النزيف: مدى ضيق عن قصد. الدلالة هنا «كم يومًا استمر النزيف» لا «طول الدورة»؛
# طول الدورة يُشتق من فروق تواريخ البداية (انظر cycle_stats) لأنه التعريف الطبي.
BLEEDING_MIN, BLEEDING_MAX = 1, 30
ALLOWED_GOALS = {"track", "ttc", "avoid_pregnancy", "pregnant", "postpartum", "perimenopause"}


class StoreError(ValueError):
    """مدخلات غير صالحة (تاريخ مقلوب، شدة خارج المدى، …)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_date(value: str, field: str = "date") -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise StoreError(f"{field} غير صالح (المطلوب YYYY-MM-DD): {value!r}") from exc


def _check_length(value: int | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise StoreError("length_days يجب أن يكون عددًا صحيحًا")
    if not BLEEDING_MIN <= value <= BLEEDING_MAX:
        raise StoreError(
            f"length_days (طول النزيف) خارج المدى المعقول ({BLEEDING_MIN}–{BLEEDING_MAX}). "
            "لو كنتِ تقصدين طول الدورة فالتطبيق يحسبه تلقائيًا من تواريخ البداية."
        )
    return value


def _check_severity(value: int | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise StoreError("severity يجب أن يكون عددًا صحيحًا")
    if not SEVERITY_MIN <= value <= SEVERITY_MAX:
        raise StoreError(f"severity خارج المدى ({SEVERITY_MIN}–{SEVERITY_MAX})")
    return value


def _check_user_key(value: str) -> str:
    key = (value or "").strip()
    if not 1 <= len(key) <= 128:
        raise StoreError("user_key مطلوب (من 1 إلى 128 حرفًا)")
    return key


def _normalize_username(username: str) -> str:
    clean = (username or "").strip().lower()
    if len(clean) < 3 or len(clean) > 80:
        raise StoreError("username_length_invalid")
    return clean


def _validate_password(password: str) -> str:
    if not password or len(password) < 6 or len(password) > 128:
        raise StoreError("password_length_invalid")
    return password


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=10000")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ users & auth
    def register_user(
        self,
        username: str,
        password: str,
        display_name: str = "",
    ) -> dict[str, Any]:
        uname = _normalize_username(username)
        pwd = _validate_password(password)
        pwd_hash, pwd_salt = hash_secret(pwd)
        now = _now()
        dname = (display_name or "").strip()[:80] or uname.split("@")[0]
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO users (username, display_name, password_hash, password_salt,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (uname, dname, pwd_hash, pwd_salt, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreError("username_taken") from exc
            user_id = int(cur.lastrowid)
        user_key = f"user:{user_id}"
        self.upsert_profile(user_key, {}, user_id=user_id)
        return self._user_public_dict(
            {
                "id": user_id,
                "username": uname,
                "display_name": dname,
                "pin_hash": "",
                "disguise_mode": 0,
                "created_at": now,
            }
        )

    def authenticate_user(self, username: str, password: str) -> dict[str, Any]:
        uname = (username or "").strip().lower()
        if not uname or not password:
            raise AuthError("invalid_credentials")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, display_name, password_hash, password_salt,"
                " pin_hash, disguise_mode, created_at FROM users WHERE username = ?",
                (uname,),
            ).fetchone()
        if row is None or not verify_secret(password, row["password_hash"], row["password_salt"]):
            raise AuthError("invalid_credentials")
        return self._user_public_dict(dict(row))

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, display_name, pin_hash, disguise_mode, created_at"
                " FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
        return self._user_public_dict(dict(row)) if row else None

    @staticmethod
    def _user_public_dict(row: dict[str, Any]) -> dict[str, Any]:
        uid = int(row["id"])
        return {
            "id": uid,
            "username": str(row["username"]),
            "display_name": str(row.get("display_name") or row["username"]),
            "user_key": f"user:{uid}",
            "has_pin": bool(row.get("pin_hash")),
            "disguise_mode": bool(row.get("disguise_mode")),
            "created_at": str(row.get("created_at") or ""),
        }

    def create_session(self, user_id: int, username: str) -> tuple[str, str]:
        """ينشئ جلسة جديدة ويُرجع (access_token, refresh_token)."""
        access_token = create_access_token(user_id, username)
        raw_refresh, refresh_hash = generate_refresh_token()
        now_dt = datetime.now(timezone.utc)
        expires_at = (now_dt + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS)).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO auth_sessions (user_id, refresh_token_hash, expires_at, created_at)"
                " VALUES (?, ?, ?, ?)",
                (int(user_id), refresh_hash, expires_at, now_dt.isoformat()),
            )
        return access_token, raw_refresh

    def rotate_refresh_token(self, raw_refresh_token: str) -> tuple[dict[str, Any], str, str]:
        """يتحقق من توكن التجديد، يُبطله، ويُصدر زوجًا جديدًا (تدوير آمن)."""
        if not raw_refresh_token or not raw_refresh_token.strip():
            raise AuthError("invalid_refresh_token")
        rhash = hash_refresh_token(raw_refresh_token)
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, user_id, expires_at, revoked_at FROM auth_sessions"
                " WHERE refresh_token_hash = ?",
                (rhash,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                raise AuthError("invalid_refresh_token")
            if str(row["expires_at"]) <= now_iso:
                raise AuthError("refresh_token_expired")
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE id = ?",
                (now_iso, int(row["id"])),
            )
        user = self.get_user_by_id(int(row["user_id"]))
        if user is None:
            raise AuthError("user_not_found")
        new_access, new_refresh = self.create_session(user["id"], user["username"])
        return user, new_access, new_refresh

    def revoke_refresh_token(self, raw_refresh_token: str) -> bool:
        if not raw_refresh_token or not raw_refresh_token.strip():
            return False
        rhash = hash_refresh_token(raw_refresh_token)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE refresh_token_hash = ? AND revoked_at IS NULL",
                (_now(), rhash),
            )
        return cur.rowcount > 0

    def set_user_security(
        self,
        user_id: int,
        *,
        pin: str | None = None,
        clear_pin: bool = False,
        disguise_mode: bool | None = None,
    ) -> dict[str, Any]:
        """يضبط رمز قفل التطبيق (PIN من 4 إلى 6 أرقام) و/أو وضع التمويه."""
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, pin_hash, pin_salt, disguise_mode FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
            if row is None:
                raise StoreError("user_not_found")
            pin_hash = str(row["pin_hash"] or "")
            pin_salt = str(row["pin_salt"] or "")
            disguise = int(row["disguise_mode"] or 0)

            if clear_pin:
                pin_hash, pin_salt = "", ""
            elif pin is not None:
                clean_pin = pin.strip()
                if not (4 <= len(clean_pin) <= 6 and clean_pin.isdigit()):
                    raise StoreError("pin_format_invalid")
                pin_hash, pin_salt = hash_secret(clean_pin)

            if disguise_mode is not None:
                disguise = 1 if disguise_mode else 0

            conn.execute(
                "UPDATE users SET pin_hash = ?, pin_salt = ?, disguise_mode = ?, updated_at = ?"
                " WHERE id = ?",
                (pin_hash, pin_salt, disguise, now, int(user_id)),
            )
        user = self.get_user_by_id(int(user_id))
        assert user is not None
        return user

    def verify_user_pin(self, user_id: int, pin: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT pin_hash, pin_salt FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
        if row is None or not row["pin_hash"]:
            return False
        return verify_secret((pin or "").strip(), row["pin_hash"], row["pin_salt"])

    # ------------------------------------------------------------ profiles & consents
    def get_profile(self, user_key: str) -> dict[str, Any]:
        ukey = _check_user_key(user_key)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_key, user_id, goal, age, country_code, pregnancy_status,"
                " contraception, diagnosed_conditions_json, medications_json,"
                " typical_cycle_days, typical_bleeding_days, onboarding_completed, updated_at"
                " FROM profiles WHERE user_key = ?",
                (ukey,),
            ).fetchone()
        if row is None:
            return {
                "user_key": ukey,
                "goal": "track",
                "age": None,
                "country_code": "EG",
                "pregnancy_status": None,
                "contraception": None,
                "conditions": [],
                "medications": [],
                "typical_cycle_days": None,
                "typical_bleeding_days": None,
                "onboarding_completed": False,
                "updated_at": None,
            }
        return {
            "user_key": str(row["user_key"]),
            "goal": str(row["goal"] or "track"),
            "age": row["age"],
            "country_code": str(row["country_code"] or "EG"),
            "pregnancy_status": row["pregnancy_status"],
            "contraception": row["contraception"],
            "conditions": json.loads(row["diagnosed_conditions_json"] or "[]"),
            "medications": json.loads(row["medications_json"] or "[]"),
            "typical_cycle_days": row["typical_cycle_days"],
            "typical_bleeding_days": row["typical_bleeding_days"],
            "onboarding_completed": bool(row["onboarding_completed"]),
            "updated_at": row["updated_at"],
        }

    def upsert_profile(
        self,
        user_key: str,
        data: dict[str, Any],
        *,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        ukey = _check_user_key(user_key)
        existing = self.get_profile(ukey)
        goal = str(data.get("goal") if "goal" in data and data["goal"] else existing["goal"]).strip().lower()
        if goal not in ALLOWED_GOALS:
            goal = "track"

        age = data.get("age", existing["age"])
        if age is not None:
            age = int(age)
            if not (8 <= age <= 75):
                raise StoreError("age_out_of_range")

        country_code = str(
            data.get("country_code") if data.get("country_code") else existing["country_code"]
        ).strip().upper()[:4] or "EG"

        pregnancy_status = data.get("pregnancy_status", existing["pregnancy_status"])
        if pregnancy_status is not None:
            pregnancy_status = str(pregnancy_status).strip()[:60] or None

        contraception = data.get("contraception", existing["contraception"])
        if contraception is not None:
            contraception = str(contraception).strip()[:120] or None

        conditions = data.get("conditions", existing["conditions"])
        if not isinstance(conditions, list):
            conditions = []

        medications = data.get("medications", existing["medications"])
        if not isinstance(medications, list):
            medications = []

        typical_cycle = data.get("typical_cycle_days", existing["typical_cycle_days"])
        if typical_cycle is not None:
            typical_cycle = int(typical_cycle)

        typical_bleed = data.get("typical_bleeding_days", existing["typical_bleeding_days"])
        if typical_bleed is not None:
            typical_bleed = int(typical_bleed)

        onboarding_done = int(bool(data.get("onboarding_completed", existing["onboarding_completed"])))
        now = _now()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profiles (
                    user_key, user_id, goal, age, country_code, pregnancy_status,
                    contraception, diagnosed_conditions_json, medications_json,
                    typical_cycle_days, typical_bleeding_days, onboarding_completed, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_key) DO UPDATE SET
                    user_id = COALESCE(excluded.user_id, profiles.user_id),
                    goal = excluded.goal,
                    age = excluded.age,
                    country_code = excluded.country_code,
                    pregnancy_status = excluded.pregnancy_status,
                    contraception = excluded.contraception,
                    diagnosed_conditions_json = excluded.diagnosed_conditions_json,
                    medications_json = excluded.medications_json,
                    typical_cycle_days = excluded.typical_cycle_days,
                    typical_bleeding_days = excluded.typical_bleeding_days,
                    onboarding_completed = excluded.onboarding_completed,
                    updated_at = excluded.updated_at
                """,
                (
                    ukey,
                    user_id,
                    goal,
                    age,
                    country_code,
                    pregnancy_status,
                    contraception,
                    json.dumps(conditions, ensure_ascii=False),
                    json.dumps(medications, ensure_ascii=False),
                    typical_cycle,
                    typical_bleed,
                    onboarding_done,
                    now,
                ),
            )
        return self.get_profile(ukey)

    def get_consents(self, user_key: str) -> list[dict[str, Any]]:
        ukey = _check_user_key(user_key)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT consent_type, granted_at, revoked_at, updated_at"
                " FROM consents WHERE user_key = ? ORDER BY consent_type",
                (ukey,),
            ).fetchall()
        return [
            {
                "consent_type": str(r["consent_type"]),
                "granted": bool(r["granted_at"] and not r["revoked_at"]),
                "granted_at": r["granted_at"],
                "revoked_at": r["revoked_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def set_consent(self, user_key: str, consent_type: str, granted: bool) -> dict[str, Any]:
        ukey = _check_user_key(user_key)
        ctype = (consent_type or "").strip().lower()[:60]
        if not ctype:
            raise StoreError("consent_type_required")
        now = _now()
        granted_at = now if granted else None
        revoked_at = None if granted else now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO consents (user_key, consent_type, granted_at, revoked_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_key, consent_type) DO UPDATE SET
                    granted_at = CASE WHEN ? THEN excluded.granted_at ELSE consents.granted_at END,
                    revoked_at = excluded.revoked_at,
                    updated_at = excluded.updated_at
                """,
                (ukey, ctype, granted_at, revoked_at, now, 1 if granted else 0),
            )
        return {
            "consent_type": ctype,
            "granted": bool(granted),
            "granted_at": granted_at,
            "revoked_at": revoked_at,
            "updated_at": now,
        }

    def migrate_user_data(self, from_user_key: str, to_user_key: str) -> dict[str, int]:
        """ينقل الدورات والأعراض والملف الشخصي من معرّف محلي إلى الحساب الموثّق."""
        src = (from_user_key or "").strip()
        dst = _check_user_key(to_user_key)
        if not src or src == dst or src.startswith("user:"):
            return {"cycles_migrated": 0, "symptoms_migrated": 0}

        cycles_migrated = 0
        symptoms_migrated = 0
        with self._connect() as conn:
            src_cycles = conn.execute(
                "SELECT id, start_date, length_days, created_at FROM cycles WHERE user_key = ?",
                (src,),
            ).fetchall()
            for row in src_cycles:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO cycles (user_key, start_date, length_days, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (dst, row["start_date"], row["length_days"], row["created_at"]),
                )
                cycles_migrated += cur.rowcount
            conn.execute("DELETE FROM cycles WHERE user_key = ?", (src,))

            cur_sym = conn.execute(
                "UPDATE symptoms SET user_key = ? WHERE user_key = ?",
                (dst, src),
            )
            symptoms_migrated = cur_sym.rowcount

            # نسخ بيانات الملف الشخصي المحلي إن لم يكن الحساب يحتوي على ملف شخصي محدّث
            src_prof = conn.execute(
                "SELECT goal, age, country_code, pregnancy_status, contraception,"
                " diagnosed_conditions_json, medications_json, typical_cycle_days,"
                " typical_bleeding_days, onboarding_completed FROM profiles WHERE user_key = ?",
                (src,),
            ).fetchone()
            if src_prof is not None:
                dst_prof = conn.execute(
                    "SELECT age, contraception, pregnancy_status FROM profiles WHERE user_key = ?",
                    (dst,),
                ).fetchone()
                if dst_prof is None or (
                    dst_prof["age"] is None
                    and dst_prof["contraception"] is None
                    and dst_prof["pregnancy_status"] is None
                ):
                    conn.execute(
                        """
                        UPDATE profiles SET
                            goal = ?, age = COALESCE(age, ?), country_code = ?,
                            pregnancy_status = COALESCE(pregnancy_status, ?),
                            contraception = COALESCE(contraception, ?),
                            diagnosed_conditions_json = ?,
                            medications_json = ?,
                            typical_cycle_days = COALESCE(typical_cycle_days, ?),
                            typical_bleeding_days = COALESCE(typical_bleeding_days, ?),
                            onboarding_completed = MAX(onboarding_completed, ?),
                            updated_at = ?
                        WHERE user_key = ?
                        """,
                        (
                            src_prof["goal"],
                            src_prof["age"],
                            src_prof["country_code"],
                            src_prof["pregnancy_status"],
                            src_prof["contraception"],
                            src_prof["diagnosed_conditions_json"],
                            src_prof["medications_json"],
                            src_prof["typical_cycle_days"],
                            src_prof["typical_bleeding_days"],
                            src_prof["onboarding_completed"],
                            _now(),
                            dst,
                        ),
                    )
                conn.execute("DELETE FROM profiles WHERE user_key = ?", (src,))

        return {"cycles_migrated": cycles_migrated, "symptoms_migrated": symptoms_migrated}

    # ------------------------------------------------------------------ cycles
    def add_cycle(self, user_key: str, start_date: str, length_days: int | None = None) -> dict:
        ukey = _check_user_key(user_key)
        start = _check_date(start_date, "start_date")
        length = _check_length(length_days)
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO cycles (user_key, start_date, length_days, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (ukey, start, length, _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreError(f"يوجد نزيف مسجّل بنفس تاريخ البداية: {start}") from exc
            row_id = cur.lastrowid
        return {"id": row_id, "start_date": start, "length_days": length}

    def list_cycles(self, user_key: str, limit: int = 24) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, start_date, length_days FROM cycles"
                " WHERE user_key = ? ORDER BY start_date DESC LIMIT ?",
                (user_key, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_cycle(self, user_key: str, cycle_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM cycles WHERE id = ? AND user_key = ?",
                               (cycle_id, user_key))
        return cur.rowcount > 0

    def correct_cycle_start(
        self,
        user_key: str,
        actual_start_date: str,
        length_days: int | None = None,
    ) -> dict[str, Any]:
        """يصحح تاريخ بداية أحدث دورة («ليست دقيقة؟») أو يضيفها ثم يعيد الحساب."""
        ukey = _check_user_key(user_key)
        actual = _check_date(actual_start_date, "actual_start_date")
        length = _check_length(length_days)
        actual_dt = date.fromisoformat(actual)

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, start_date, length_days FROM cycles WHERE user_key = ? ORDER BY start_date DESC LIMIT 3",
                (ukey,),
            ).fetchall()
            target_id = None
            target_len = length
            for r in rows:
                r_dt = date.fromisoformat(r["start_date"])
                if abs((actual_dt - r_dt).days) <= 10:
                    target_id = int(r["id"])
                    if target_len is None:
                        target_len = r["length_days"]
                    break

            if target_id is not None:
                conn.execute("DELETE FROM cycles WHERE user_key = ? AND start_date = ? AND id != ?",
                             (ukey, actual, target_id))
                conn.execute(
                    "UPDATE cycles SET start_date = ?, length_days = ? WHERE id = ? AND user_key = ?",
                    (actual, target_len, target_id, ukey),
                )
                return {"id": target_id, "start_date": actual, "length_days": target_len, "corrected": True}

            cur = conn.execute(
                "INSERT OR REPLACE INTO cycles (user_key, start_date, length_days, created_at) VALUES (?, ?, ?, ?)",
                (ukey, actual, target_len, _now()),
            )
            return {"id": int(cur.lastrowid), "start_date": actual, "length_days": target_len, "corrected": True}

    # ------------------------------------------------------------ daily_logs (Stage 2)
    def upsert_daily_log(self, user_key: str, log_date: str, data: dict[str, Any]) -> dict[str, Any]:
        ukey = _check_user_key(user_key)
        day = _check_date(log_date, "log_date")
        now = _now()
        flow = str(data.get("flow") or "").strip()[:30] or None
        mood = str(data.get("mood") or "").strip()[:60] or None
        sleep_hours = float(data["sleep_hours"]) if data.get("sleep_hours") is not None else None
        water_cups = int(data["water_cups"]) if data.get("water_cups") is not None else None
        exercise = str(data.get("exercise") or "").strip()[:120] or None
        medications = str(data.get("medications") or "").strip()[:200] or None
        note = str(data.get("note") or "").strip()[:500] or None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_logs (
                    user_key, log_date, flow, mood, sleep_hours, water_cups,
                    exercise, medications, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_key, log_date) DO UPDATE SET
                    flow = excluded.flow,
                    mood = excluded.mood,
                    sleep_hours = excluded.sleep_hours,
                    water_cups = excluded.water_cups,
                    exercise = excluded.exercise,
                    medications = excluded.medications,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (ukey, day, flow, mood, sleep_hours, water_cups, exercise, medications, note, now, now),
            )
        return {
            "log_date": day,
            "flow": flow,
            "mood": mood,
            "sleep_hours": sleep_hours,
            "water_cups": water_cups,
            "exercise": exercise,
            "medications": medications,
            "note": note,
        }

    def list_daily_logs(self, user_key: str, limit: int = 60) -> list[dict[str, Any]]:
        ukey = _check_user_key(user_key)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT log_date, flow, mood, sleep_hours, water_cups, exercise, medications, note"
                " FROM daily_logs WHERE user_key = ? ORDER BY log_date DESC LIMIT ?",
                (ukey, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def copy_yesterday_log(self, user_key: str, target_date: str | None = None) -> dict[str, Any]:
        """قالب «اليوم يشبه أمس»: ينسخ سجل الأمس وأعراضه إلى اليوم بضغطة واحدة."""
        ukey = _check_user_key(user_key)
        today_dt = date.fromisoformat(_check_date(target_date, "target_date")) if target_date else date.today()
        yesterday_iso = (today_dt - timedelta(days=1)).isoformat()
        today_iso = today_dt.isoformat()

        copied_symptoms = 0
        copied_daily = False
        with self._connect() as conn:
            y_daily = conn.execute(
                "SELECT flow, mood, sleep_hours, water_cups, exercise, medications, note"
                " FROM daily_logs WHERE user_key = ? AND log_date = ?",
                (ukey, yesterday_iso),
            ).fetchone()
            if y_daily is not None:
                now = _now()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO daily_logs (
                        user_key, log_date, flow, mood, sleep_hours, water_cups,
                        exercise, medications, note, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ukey,
                        today_iso,
                        y_daily["flow"],
                        y_daily["mood"],
                        y_daily["sleep_hours"],
                        y_daily["water_cups"],
                        y_daily["exercise"],
                        y_daily["medications"],
                        y_daily["note"],
                        now,
                        now,
                    ),
                )
                copied_daily = True

            y_syms = conn.execute(
                "SELECT symptom, severity, note FROM symptoms WHERE user_key = ? AND log_date = ?",
                (ukey, yesterday_iso),
            ).fetchall()
            for s in y_syms:
                conn.execute(
                    "INSERT INTO symptoms (user_key, log_date, symptom, severity, note, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (ukey, today_iso, s["symptom"], s["severity"], s["note"], _now()),
                )
                copied_symptoms += 1

        return {
            "source_date": yesterday_iso,
            "target_date": today_iso,
            "copied_daily_log": copied_daily,
            "copied_symptoms": copied_symptoms,
        }

    # ------------------------------------------------------------ partner sharing (Stage 5)
    def create_partner_share(
        self,
        user_key: str,
        partner_label: str = "",
        scope: str = "next_window_only",
    ) -> dict[str, Any]:
        import secrets
        ukey = _check_user_key(user_key)
        token = secrets.token_urlsafe(24)
        clean_scope = scope if scope in ("next_window_only", "window_and_phase") else "next_window_only"
        now = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO partner_shares (user_key, share_token, partner_label, scope, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (ukey, token, (partner_label or "").strip()[:60], clean_scope, now),
            )
            share_id = int(cur.lastrowid)
        return {
            "id": share_id,
            "share_token": token,
            "partner_label": (partner_label or "").strip()[:60],
            "scope": clean_scope,
            "created_at": now,
        }

    def list_partner_shares(self, user_key: str) -> list[dict[str, Any]]:
        ukey = _check_user_key(user_key)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, share_token, partner_label, scope, revoked_at, created_at"
                " FROM partner_shares WHERE user_key = ? AND revoked_at IS NULL ORDER BY id DESC",
                (ukey,),
            ).fetchall()
        return [dict(r) for r in rows]

    def revoke_partner_share(self, user_key: str, share_id: int) -> bool:
        ukey = _check_user_key(user_key)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE partner_shares SET revoked_at = ? WHERE id = ? AND user_key = ? AND revoked_at IS NULL",
                (_now(), int(share_id), ukey),
            )
        return cur.rowcount > 0

    def resolve_partner_share(self, share_token: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_key, partner_label, scope, revoked_at FROM partner_shares WHERE share_token = ?",
                ((share_token or "").strip(),),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        return dict(row)

    # ---------------------------------------------------------------- symptoms
    def add_symptom(self, user_key: str, log_date: str, symptom: str,
                    severity: int | None = None, note: str | None = None) -> dict:
        day = _check_date(log_date, "log_date")
        text = (symptom or "").strip()
        if not 1 <= len(text) <= 200:
            raise StoreError("اسم العرض مطلوب (حتى 200 حرف)")
        sev = _check_severity(severity)
        clean_note = (note or "").strip()[:500] or None
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO symptoms (user_key, log_date, symptom, severity, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (user_key, day, text, sev, clean_note, _now()),
            )
            row_id = cur.lastrowid
        return {"id": row_id, "log_date": day, "symptom": text,
                "severity": sev, "note": clean_note}

    def list_symptoms(self, user_key: str, limit: int = 100, since_days: int | None = None) -> list[dict]:
        query = ("SELECT id, log_date, symptom, severity, note FROM symptoms WHERE user_key = ?")
        params: list = [user_key]
        if since_days is not None:
            query += " AND log_date >= ?"
            params.append((date.today() - timedelta(days=since_days)).isoformat())
        query += " ORDER BY log_date DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def delete_symptom(self, user_key: str, symptom_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM symptoms WHERE id = ? AND user_key = ?",
                               (symptom_id, user_key))
        return cur.rowcount > 0

    # ------------------------------------------------------------ context/stat
    def cycle_stats(self, user_key: str) -> dict:
        """إحصاءات مشتقة من البيانات المسجّلة فقط — تُغذّي محرك القواعد.

        avg_cycle_days يُحسب من الفروق بين تواريخ البداية المتتالية (وهو
        التعريف الطبي لطول الدورة) لا من الأطوال المُدخلة يدويًا.
        """
        cycles = self.list_cycles(user_key, limit=24)
        ordered = sorted(cycles, key=lambda c: c["start_date"])
        last = [(c["start_date"], c["length_days"]) for c in reversed(ordered)]

        gaps: list[int] = []
        for earlier, later in zip(ordered, ordered[1:]):
            delta = (date.fromisoformat(later["start_date"])
                     - date.fromisoformat(earlier["start_date"])).days
            if 1 <= delta <= 400:  # تجاهل الفجوات غير المعقولة كإدخال خاطئ
                gaps.append(delta)

        avg = round(sum(gaps) / len(gaps)) if gaps else None
        return {
            "cycles_recorded": len(ordered),
            "avg_cycle_days": avg,
            "cycle_gaps": gaps,              # أطوال الدورات الفعلية (فروق التواريخ)
            "last_cycles": [{"start_date": d, "length_days": l} for d, l in last[:6]],
        }

    def delete_all(self, user_key: str) -> dict:
        """حذف كل بيانات مستخدمة (حق الوصول والحذف على الجهاز والخادم)."""
        with self._connect() as conn:
            cycles = conn.execute("DELETE FROM cycles WHERE user_key = ?", (user_key,)).rowcount
            symptoms = conn.execute("DELETE FROM symptoms WHERE user_key = ?", (user_key,)).rowcount
            conn.execute("DELETE FROM daily_logs WHERE user_key = ?", (user_key,))
            conn.execute("DELETE FROM predictions WHERE user_key = ?", (user_key,))
            conn.execute("DELETE FROM reminders WHERE user_key = ?", (user_key,))
            conn.execute("DELETE FROM consents WHERE user_key = ?", (user_key,))
            conn.execute("DELETE FROM partner_shares WHERE user_key = ?", (user_key,))
            conn.execute("DELETE FROM profiles WHERE user_key = ?", (user_key,))
        return {"cycles_deleted": cycles, "symptoms_deleted": symptoms}
