"""تخزين محلي للبيانات الصحية (SQLite من المكتبة القياسية، بلا تبعيات).

نموذج البيانات (المسميات الجديدة):
- `bleeding_logs`   سجلات النزيف (كانت `cycles`): تاريخ البداية + طول النزيف
  بالأيام (`bleeding_days`، كان `length_days`). طول الدورة يُشتق من فروق
  التواريخ لا يُخزَّن.
- `symptom_logs`    سجلات الأعراض (كانت `symptoms`).
- `health_profile`  ملف المستخدم (لغة، بلد، إعدادات عرض، عمر، وحمل/وقاية).
- `consents`        موافقات صريحة على أنشطة معالجة محددة (موديل خارجي، تدقيق…).

الهجرة (migration): قوائم النسخة القديمة تُعاد تسميتها في مكانها مع الاحتفاظ
بالبيانات والمعرّفات (`cycles`→`bleeding_logs`، `length_days`→`bleeding_days`)،
ولا يُنشأ جدول جديد قبل نقل القديم حتى لا تُهجَر البيانات.

ملاحظات خصوصية:
- تخزين «محلي أولًا»: ملف واحد على الخادم، ويمكن حذف كل بيانات مستخدمة
  بمعرّفها (`user_key`) عبر DELETE /v1/data وDELETE /v1/account.
- `user_key` معرّف جهاز تُنشئه الواجهة عشوائيًا وتخزّنه محليًا. **ليس مصادقة**،
  الغرض منه عزل صفوف المستخدمات عن بعضها فقط.
- لا نخزّن نص الرسائل هنا؛ سجل التدقيق منفصل (audit/) وله اعتباراته الخاصة.

التزامن: اتصال واحد لكل عملية + WAL و busy_timeout لتفادي أخطاء القفل عند
الطلبات المتوازية.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS bleeding_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key      TEXT    NOT NULL,
    start_date    TEXT    NOT NULL,              -- ISO date لأول يوم نزيف
    bleeding_days INTEGER,                       -- طول النزيف بالأيام (اختياري)
    created_at    TEXT    NOT NULL,
    UNIQUE (user_key, start_date)
);
CREATE INDEX IF NOT EXISTS idx_bleeding_logs_user ON bleeding_logs (user_key, start_date);

CREATE TABLE IF NOT EXISTS symptom_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key   TEXT    NOT NULL,
    log_date   TEXT    NOT NULL,
    symptom    TEXT    NOT NULL,
    severity   INTEGER,                          -- 1..5 (شدة ذكرتها المستخدمة)
    note       TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symptom_logs_user ON symptom_logs (user_key, log_date);

CREATE TABLE IF NOT EXISTS health_profile (
    user_key         TEXT PRIMARY KEY,
    locale           TEXT,                       -- ar | en
    country_code     TEXT,                       -- ISO 3166-1 alpha-2 (لوحة الأرقام)
    digits_style     TEXT,                       -- western | arabic_indic
    timezone         TEXT,
    week_start       TEXT,                       -- monday..sunday
    age              INTEGER,
    contraception    TEXT,
    pregnancy_status TEXT,
    conditions       TEXT,                       -- JSON list[ConditionStatus]
    updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS consents (
    user_key    TEXT    NOT NULL,
    consent_key TEXT    NOT NULL,                -- نشاط معالجة مسمى ( ConsentKey )
    granted     INTEGER NOT NULL,                -- 1 نعم / 0 لا
    version     TEXT,                            -- إصدار النص الذي وُافق عليه
    decided_at  TEXT    NOT NULL,
    PRIMARY KEY (user_key, consent_key)
);
"""

SEVERITY_MIN, SEVERITY_MAX = 1, 5
# طول النزيف: مدى ضيق عن قصد. الدلالة هنا «كم يومًا استمر النزيف» لا «طول الدورة»؛
# طول الدورة يُشتق من فروق تواريخ البداية (انظر cycle_stats) لأنه التعريف الطبي.
BLEEDING_MIN, BLEEDING_MAX = 1, 30
AGE_MIN, AGE_MAX = 0, 120


class StoreError(ValueError):
    """مدخلات غير صالحة (تاريخ مقلوب، شدة خارج المدى، …)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_date(value: str, field: str = "date") -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise StoreError(f"{field} غير صالح (المطلوب YYYY-MM-DD): {value!r}") from exc


def _check_bleeding(value: int | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise StoreError("bleeding_days يجب أن يكون عددًا صحيحًا")
    if not BLEEDING_MIN <= value <= BLEEDING_MAX:
        raise StoreError(
            f"bleeding_days (طول النزيف) خارج المدى المعقول ({BLEEDING_MIN}–{BLEEDING_MAX}). "
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


def _check_age(value: int | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise StoreError("age يجب أن يكون عددًا صحيحًا")
    if not AGE_MIN <= value <= AGE_MAX:
        raise StoreError(f"age خارج المدى ({AGE_MIN}–{AGE_MAX})")
    return value


def _check_country(value: str | None) -> str | None:
    if value is None:
        return None
    code = str(value).strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise StoreError(f"country_code غير صالح (حرفا لاتينيان فقط): {value!r}")
    return code


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def _migrate_legacy(conn: sqlite3.Connection) -> None:
    """يهيّل قوائم النسخة القديمة إلى المسميات الجديدة — بالبيانات وبقية المعرّفات.

    الترتيب مهم: يُنفَّذ **قبل** SCHEMA، وإلا أُنشئ الجدول الجديد فارغًا إلى جانب
    القديم الممتلئ وتهجر البيانات. عمليات الهجرة مُراقَبة (IF NOT EXISTS / فحص
    الأعمدة) فتعيد التشغيل بلا ضرر.
    """
    tables = _table_names(conn)
    if "cycles" in tables and "bleeding_logs" not in tables:
        conn.execute("ALTER TABLE cycles RENAME TO bleeding_logs")
    if "symptoms" in tables and "symptom_logs" not in tables:
        conn.execute("ALTER TABLE symptoms RENAME TO symptom_logs")

    # SQLite لا يدعم تسمية الفهارس (ALTER INDEX) — نحذف الاسم القديم ويعيد
    # SCHEMA إنشاء الجديد بنفس الأعمدة.
    indexes = _index_names(conn)
    if "idx_cycles_user" in indexes:
        conn.execute("DROP INDEX IF EXISTS idx_cycles_user")
    if "idx_symptoms_user" in indexes:
        conn.execute("DROP INDEX IF EXISTS idx_symptoms_user")

    if "bleeding_logs" in _table_names(conn):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(bleeding_logs)")}
        if "length_days" in cols and "bleeding_days" not in cols:
            conn.execute("ALTER TABLE bleeding_logs RENAME COLUMN length_days TO bleeding_days")


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            _migrate_legacy(conn)
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -------------------------------------------------------- bleeding logs
    @staticmethod
    def _bleeding_row(row: sqlite3.Row | dict) -> dict:
        """صف بالاسم الجديد + المفتاح القديم `length_days` للتوافق (نفس القيمة)."""
        data = dict(row)
        data["length_days"] = data.get("bleeding_days")
        return data

    def add_bleeding_log(self, user_key: str, start_date: str,
                         bleeding_days: int | None = None) -> dict:
        start = _check_date(start_date, "start_date")
        days = _check_bleeding(bleeding_days)
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO bleeding_logs (user_key, start_date, bleeding_days, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (user_key, start, days, _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreError(f"يوجد نزيف مسجّل بنفس تاريخ البداية: {start}") from exc
            row_id = cur.lastrowid
        return self._bleeding_row({"id": row_id, "start_date": start, "bleeding_days": days})

    def list_bleeding_logs(self, user_key: str, limit: int = 24) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, start_date, bleeding_days FROM bleeding_logs"
                " WHERE user_key = ? ORDER BY start_date DESC LIMIT ?",
                (user_key, limit),
            ).fetchall()
        return [self._bleeding_row(r) for r in rows]

    def delete_bleeding_log(self, user_key: str, log_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM bleeding_logs WHERE id = ? AND user_key = ?",
                               (log_id, user_key))
        return cur.rowcount > 0

    # ------------------------------------------------------------ symptom logs
    def add_symptom_log(self, user_key: str, log_date: str, symptom: str,
                        severity: int | None = None, note: str | None = None) -> dict:
        day = _check_date(log_date, "log_date")
        text = (symptom or "").strip()
        if not 1 <= len(text) <= 200:
            raise StoreError("اسم العرض مطلوب (حتى 200 حرف)")
        sev = _check_severity(severity)
        clean_note = (note or "").strip()[:500] or None
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO symptom_logs (user_key, log_date, symptom, severity, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (user_key, day, text, sev, clean_note, _now()),
            )
            row_id = cur.lastrowid
        return {"id": row_id, "log_date": day, "symptom": text,
                "severity": sev, "note": clean_note}

    def list_symptom_logs(self, user_key: str, limit: int = 100,
                          since_days: int | None = None) -> list[dict]:
        query = ("SELECT id, log_date, symptom, severity, note FROM symptom_logs"
                 " WHERE user_key = ?")
        params: list = [user_key]
        if since_days is not None:
            query += " AND log_date >= ?"
            params.append((date.today() - timedelta(days=since_days)).isoformat())
        query += " ORDER BY log_date DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def delete_symptom_log(self, user_key: str, log_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM symptom_logs WHERE id = ? AND user_key = ?",
                               (log_id, user_key))
        return cur.rowcount > 0

    # --------------------------------------------------------- health profile
    @staticmethod
    def _profile_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        raw = data.pop("conditions", None)
        try:
            data["conditions"] = json.loads(raw) if raw else []
        except ValueError:
            data["conditions"] = []
        return data

    def get_health_profile(self, user_key: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM health_profile WHERE user_key = ?",
                               (user_key,)).fetchone()
        return None if row is None else self._profile_row(row)

    def upsert_health_profile(self, user_key: str, data: dict) -> dict:
        """استبدال كامل لملف المستخدم (PUT): الحقول الغائبة تُمسح إلى NULL."""
        conditions = data.get("conditions")
        payload = (
            user_key,
            data.get("locale"),
            _check_country(data.get("country_code")),
            data.get("digits_style"),
            data.get("timezone"),
            data.get("week_start"),
            _check_age(data.get("age")),
            data.get("contraception"),
            data.get("pregnancy_status"),
            json.dumps(conditions, ensure_ascii=False) if conditions else None,
            _now(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO health_profile"
                " (user_key, locale, country_code, digits_style, timezone, week_start,"
                "  age, contraception, pregnancy_status, conditions, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(user_key) DO UPDATE SET"
                "  locale=excluded.locale, country_code=excluded.country_code,"
                "  digits_style=excluded.digits_style, timezone=excluded.timezone,"
                "  week_start=excluded.week_start, age=excluded.age,"
                "  contraception=excluded.contraception,"
                "  pregnancy_status=excluded.pregnancy_status,"
                "  conditions=excluded.conditions, updated_at=excluded.updated_at",
                payload,
            )
        return self.get_health_profile(user_key)  # type: ignore[return-value]

    # ---------------------------------------------------------------- consents
    @staticmethod
    def _consent_row(row: sqlite3.Row | dict) -> dict:
        data = dict(row)
        data["granted"] = bool(data.get("granted"))
        return data

    def list_consents(self, user_key: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT consent_key, granted, version, decided_at FROM consents"
                " WHERE user_key = ? ORDER BY consent_key",
                (user_key,),
            ).fetchall()
        return [self._consent_row(r) for r in rows]

    def set_consent(self, user_key: str, consent_key: str, granted: bool,
                    version: str | None = None) -> dict:
        key = (consent_key or "").strip()
        if not 1 <= len(key) <= 64:
            raise StoreError("consent_key مطلوب (حتى 64 حرفًا)")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO consents (user_key, consent_key, granted, version, decided_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(user_key, consent_key) DO UPDATE SET"
                "  granted=excluded.granted, version=excluded.version,"
                "  decided_at=excluded.decided_at",
                (user_key, key, 1 if granted else 0, version, _now()),
            )
            row = conn.execute(
                "SELECT consent_key, granted, version, decided_at FROM consents"
                " WHERE user_key = ? AND consent_key = ?",
                (user_key, key),
            ).fetchone()
        return self._consent_row(row)

    # ------------------------------------------------------------ context/stat
    def cycle_stats(self, user_key: str) -> dict:
        """إحصاءات مشتقة من البيانات المسجّلة فقط — تُغذّي محرك القواعد.

        avg_cycle_days يُحسب من الفروق بين تواريخ البداية المتتالية (وهو
        التعريف الطبي لطول الدورة) لا من الأطوال المُدخلة يدويًا.
        """
        logs = self.list_bleeding_logs(user_key, limit=24)
        ordered = sorted(logs, key=lambda c: c["start_date"])
        last = [(c["start_date"], c["bleeding_days"]) for c in reversed(ordered)]

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
            # المفتاح الجديد والقديم معًا: المدخل يقرأان نفس القيمة.
            "last_cycles": [{"start_date": d, "bleeding_days": v, "length_days": v}
                            for d, v in last[:6]],
        }

    def delete_all(self, user_key: str) -> dict:
        """حذف كل بيانات هذه المستخدمة في القاعدة (حق الوصول والحذف).

        سجل التدقيق منفصل في audit/ — يُمحى عبر `audit.purge_user` في مسار
        DELETE /v1/account لا هنا.
        """
        with self._connect() as conn:
            bleeding = conn.execute("DELETE FROM bleeding_logs WHERE user_key = ?",
                                    (user_key,)).rowcount
            symptoms = conn.execute("DELETE FROM symptom_logs WHERE user_key = ?",
                                    (user_key,)).rowcount
            profile = conn.execute("DELETE FROM health_profile WHERE user_key = ?",
                                   (user_key,)).rowcount
            consents = conn.execute("DELETE FROM consents WHERE user_key = ?",
                                    (user_key,)).rowcount
        return {
            "bleeding_logs_deleted": bleeding,
            "symptom_logs_deleted": symptoms,
            "health_profile_deleted": profile,
            "consents_deleted": consents,
        }
