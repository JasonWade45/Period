"""تخزين محلي للدورات والأعراض (SQLite من المكتبة القياسية، بلا تبعيات).

ملاحظات خصوصية:
- هذا تخزين «محلي أولًا»: ملف واحد على الخادم، ويمكن حذف كل بيانات مستخدمة
  بمعرّفها (`user_key`) عبر DELETE.
- `user_key` معرّف جهاز تُنشئه الواجهة عشوائيًا وتخزّنه محليًا. **ليس مصادقة**،
  الغرض منه عزل صفوف المستخدمات عن بعضها فقط. المصادقة الحقيقية في app/auth.py.
- لا نخزّن نص الرسائل هنا؛ سجل التدقيق منفصل (audit/) وله اعتباراته الخاصة.

التزامن: اتصال واحد لكل عملية + WAL و busy_timeout لتفادي أخطاء القفل عند
الطلبات المتوازية.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key    TEXT    NOT NULL,
    start_date  TEXT    NOT NULL,              -- ISO date لأول يوم نزيف
    length_days INTEGER,                       -- طول الدورة بالأيام (اختياري)
    created_at  TEXT    NOT NULL,
    UNIQUE (user_key, start_date)
);
CREATE INDEX IF NOT EXISTS idx_cycles_user ON cycles (user_key, start_date);

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
"""

SEVERITY_MIN, SEVERITY_MAX = 1, 5


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
    if not 1 <= value <= 90:
        raise StoreError("length_days خارج المدى المعقول (1–90)")
    return value


def _check_severity(value: int | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise StoreError("severity يجب أن يكون عددًا صحيحًا")
    if not SEVERITY_MIN <= value <= SEVERITY_MAX:
        raise StoreError(f"severity خارج المدى ({SEVERITY_MIN}–{SEVERITY_MAX})")
    return value


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
            conn.execute("PRAGMA busy_timeout=10000")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ cycles
    def add_cycle(self, user_key: str, start_date: str, length_days: int | None = None) -> dict:
        start = _check_date(start_date, "start_date")
        length = _check_length(length_days)
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO cycles (user_key, start_date, length_days, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (user_key, start, length, _now()),
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
            "last_cycles": [{"start_date": d, "length_days": l} for d, l in last[:6]],
        }

    def delete_all(self, user_key: str) -> dict:
        """حذف كل بيانات مستخدمة (حق الوصول والحذف)."""
        with self._connect() as conn:
            cycles = conn.execute("DELETE FROM cycles WHERE user_key = ?", (user_key,)).rowcount
            symptoms = conn.execute("DELETE FROM symptoms WHERE user_key = ?", (user_key,)).rowcount
        return {"cycles_deleted": cycles, "symptoms_deleted": symptoms}
