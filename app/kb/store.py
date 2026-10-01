"""تخزين قاعدة المعرفة: واجهة واحدة + تنفيذ SQLite محلي.

التنفيذ المُنتَجي على PostgreSQL/pgvector موجود في `app/kb/postgres.py`، وهذا
التنفيذ بـSQLite يعمل بلا خدمة خارجية ويُستخدم للاختبار المحلي والتطوير. كلاهما
يلتزم نفس الواجهة ونفس قواعد الحالة، فلا تختلف السلوكيات بين البيئتين.

قاعدة الحالة الحاكمة (من البريف):
- مقطع جديد → `draft_unreviewed`.
- تغيّر المحتوى → يُعاد إلى `draft_unreviewed` (لأن نصًا تغيّر يجب أن يُراجَع).
- إعادة استيراد محتوى لم يتغيّر → **لا يُمَسّ status إطلاقًا**، ولا يزيد الإصدار.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Protocol

from .schemas import (
    ChunkStatus,
    KbChunk,
    KbSource,
    RetrievedChunk,
    TransitionError,
    can_transition,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kb_sources (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    language           TEXT NOT NULL DEFAULT 'ar',
    url                TEXT NOT NULL DEFAULT '',
    licence            TEXT NOT NULL DEFAULT '',
    approved_for_ingest INTEGER NOT NULL DEFAULT 0,
    notes              TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id                     TEXT PRIMARY KEY,
    source_id              TEXT NOT NULL,
    title                  TEXT NOT NULL DEFAULT '',
    topic                  TEXT NOT NULL DEFAULT '',
    language               TEXT NOT NULL DEFAULT 'ar',
    content                TEXT NOT NULL,
    status                 TEXT NOT NULL,
    reviewed_by            TEXT NOT NULL DEFAULT '',
    reviewed_at            TEXT NOT NULL DEFAULT '',
    authored_by            TEXT NOT NULL DEFAULT '',
    license_note           TEXT NOT NULL DEFAULT '',
    content_version        INTEGER NOT NULL DEFAULT 1,
    source_refs_to_verify  TEXT NOT NULL DEFAULT '[]',
    content_hash           TEXT NOT NULL DEFAULT '',
    search_text            TEXT NOT NULL DEFAULT '',   -- مقابل tsv في PostgreSQL
    updated_at             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_status ON kb_chunks (status, language);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_source ON kb_chunks (source_id);

-- في PostgreSQL المتجه عمود في kb_chunks (انظر migrations/001_kb.sql).
-- هنا جدول مصاحب لأن SQLite بلا نوع متجهات؛ نفس واجهة الاستدعاء في الحالتين.
CREATE TABLE IF NOT EXISTS kb_embeddings (
    chunk_id TEXT PRIMARY KEY,
    vector   TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KbStore(Protocol):
    """الواجهة التي يعتمد عليها الاستيراد والاسترجاع."""

    def upsert_source(self, source: KbSource) -> None: ...
    def get_source(self, source_id: str) -> Optional[KbSource]: ...
    def list_sources(self) -> list[KbSource]: ...
    def get_chunk(self, chunk_id: str) -> Optional[KbChunk]: ...
    # --------------------------------------------------------------- المتجهات
    def set_embedding(self, chunk_id: str, vector: list[float]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO kb_embeddings (chunk_id, vector) VALUES (?, ?)"
                " ON CONFLICT(chunk_id) DO UPDATE SET vector=excluded.vector",
                (chunk_id, json.dumps(vector)),
            )

    def get_embedding(self, chunk_id: str) -> Optional[list[float]]:
        with self._connect() as conn:
            row = conn.execute("SELECT vector FROM kb_embeddings WHERE chunk_id = ?",
                               (chunk_id,)).fetchone()
        return json.loads(row["vector"]) if row else None

    def all_embeddings(self) -> dict[str, list[float]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT chunk_id, vector FROM kb_embeddings").fetchall()
        return {r["chunk_id"]: json.loads(r["vector"]) for r in rows}
    def vector_search(self, vector: list[float], limit: int, statuses: list[ChunkStatus],
                      language: str | None, min_similarity: float
                      ) -> list[tuple[str, float]]: ...
    def upsert_chunk(self, chunk: KbChunk) -> str: ...
    def set_status(self, chunk_id: str, status: ChunkStatus, reviewer: str,
                   reviewed_at: str, note: str = "") -> KbChunk: ...
    def list_chunks(self, statuses: list[ChunkStatus] | None = None) -> list[KbChunk]: ...
    def keyword_search(self, query: str, limit: int, statuses: list[ChunkStatus],
                       language: str | None) -> list[tuple[str, float]]: ...


class SqliteKbStore:
    """تنفيذ محلي للاختبار والتطوير — نفس قواعد الحالة تمامًا."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """ترحيل خفيف لقواعد قائمة: أعمدة أُضيفت بعد أول إنشاء للجدول."""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(kb_chunks)")}
        for column in ("authored_by", "license_note"):
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE kb_chunks ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ sources
    def upsert_source(self, source: KbSource) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO kb_sources (id, name, language, url, licence,"
                " approved_for_ingest, notes) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET name=excluded.name, language=excluded.language,"
                " url=excluded.url, licence=excluded.licence, notes=excluded.notes",
                (source.id, source.name, source.language, source.url, source.licence,
                 int(source.approved_for_ingest), source.notes),
            )

    def get_source(self, source_id: str) -> Optional[KbSource]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM kb_sources WHERE id = ?", (source_id,)).fetchone()
        return self._row_to_source(row) if row else None

    def list_sources(self) -> list[KbSource]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM kb_sources ORDER BY id").fetchall()
        return [self._row_to_source(r) for r in rows]

    @staticmethod
    def _row_to_source(row: sqlite3.Row) -> KbSource:
        return KbSource(
            id=row["id"], name=row["name"], language=row["language"], url=row["url"],
            licence=row["licence"], approved_for_ingest=bool(row["approved_for_ingest"]),
            notes=row["notes"],
        )

    # ------------------------------------------------------------------- chunks
    def get_chunk(self, chunk_id: str) -> Optional[KbChunk]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM kb_chunks WHERE id = ?", (chunk_id,)).fetchone()
        return self._row_to_chunk(row) if row else None

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> KbChunk:
        return KbChunk(
            id=row["id"], source_id=row["source_id"], title=row["title"], topic=row["topic"],
            language=row["language"], content=row["content"],
            status=ChunkStatus(row["status"]), reviewed_by=row["reviewed_by"],
            reviewed_at=row["reviewed_at"],
            authored_by=row["authored_by"] if "authored_by" in row.keys() else "",
            license_note=row["license_note"] if "license_note" in row.keys() else "",
            content_version=row["content_version"],
            source_refs_to_verify=json.loads(row["source_refs_to_verify"] or "[]"),
            content_hash=row["content_hash"],
        )

    # --------------------------------------------------------------- المتجهات
    def set_embedding(self, chunk_id: str, vector: list[float]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO kb_embeddings (chunk_id, vector) VALUES (?, ?)"
                " ON CONFLICT(chunk_id) DO UPDATE SET vector=excluded.vector",
                (chunk_id, json.dumps(vector)),
            )

    def get_embedding(self, chunk_id: str) -> Optional[list[float]]:
        with self._connect() as conn:
            row = conn.execute("SELECT vector FROM kb_embeddings WHERE chunk_id = ?",
                               (chunk_id,)).fetchone()
        return json.loads(row["vector"]) if row else None

    def all_embeddings(self) -> dict[str, list[float]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT chunk_id, vector FROM kb_embeddings").fetchall()
        return {r["chunk_id"]: json.loads(r["vector"]) for r in rows}

    def vector_search(self, vector: list[float], limit: int, statuses: list[ChunkStatus],
                      language: str | None, min_similarity: float
                      ) -> list[tuple[str, float]]:
        """أقرب المقاطع بالتشابه الجيبي، مع تصفية الحالة واللغة والعتبة.

        في PostgreSQL يُنفَّذ هذا عبر فهرس HNSW (`embedding <=> $1`) — انظر
        app/kb/postgres.py؛ هنا نحسبه في بايثون لأن SQLite بلا نوع متجهات.
        الترتيب والتصفية والعتبة متطابقة في التنفيذين.
        """
        candidates = self.list_chunks(statuses)
        if language:
            candidates = [c for c in candidates if c.language == language]
        if not candidates:
            return []

        scored: list[tuple[str, float]] = []
        for chunk in candidates:
            stored = self.get_embedding(chunk.id)
            if stored is None:
                continue
            similarity = sum(a * b for a, b in zip(vector, stored))
            if similarity >= min_similarity:
                scored.append((chunk.id, similarity))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def upsert_chunk(self, chunk: KbChunk) -> str:
        """يُرجع: "created" | "unchanged" | "content_changed".

        هذا هو قلب الشرط: إعادة الاستيراد لا تغيّر حالة مقطع لم يتغيّر محتواه،
        فاعتماد الطبيبة لا يضيع لمجرد تشغيل الاستيراد مرة أخرى.
        """
        new_hash = chunk.compute_hash()
        existing = self.get_chunk(chunk.id)

        if existing is None:
            chunk.content_hash = new_hash
            chunk.content_version = 1
            chunk.status = ChunkStatus.DRAFT_UNREVIEWED
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO kb_chunks (id, source_id, title, topic, language, content,"
                    " status, reviewed_by, reviewed_at, authored_by, license_note,"
                    " content_version, source_refs_to_verify,"
                    " content_hash, search_text, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (chunk.id, chunk.source_id, chunk.title, chunk.topic, chunk.language,
                     chunk.content, chunk.status.value, "", "",
                     chunk.authored_by, chunk.license_note, 1,
                     json.dumps(chunk.source_refs_to_verify, ensure_ascii=False),
                     new_hash, chunk.search_text, _now()),
                )
            return "created"

        if existing.content_hash == new_hash:
            # idempotent: لا نلمس الحالة ولا الإصدار
            return "unchanged"

        # المحتوى تغيّر: يُعاد للمراجعة، ويُحذف متجهه القديم (متجه نص قديم
        # لنص جديد يجعل البحث يعقّر بنتيجة لا تخص المحتوى الحالي)
        with self._connect() as conn:
            conn.execute("DELETE FROM kb_embeddings WHERE chunk_id = ?", (chunk.id,))
        with self._connect() as conn:
            conn.execute(
                "UPDATE kb_chunks SET source_id=?, title=?, topic=?, language=?, content=?,"
                " status=?, reviewed_by='', reviewed_at='', authored_by=?, license_note=?,"
                " content_version=?, source_refs_to_verify=?, content_hash=?, search_text=?,"
                " updated_at=? WHERE id=?",
                (chunk.source_id, chunk.title, chunk.topic, chunk.language, chunk.content,
                 ChunkStatus.DRAFT_UNREVIEWED.value, chunk.authored_by, chunk.license_note,
                 existing.content_version + 1,
                 json.dumps(chunk.source_refs_to_verify, ensure_ascii=False),
                 new_hash, chunk.search_text, _now(), chunk.id),
            )
        return "content_changed"

    def set_status(self, chunk_id: str, status: ChunkStatus, reviewer: str,
                   reviewed_at: str, note: str = "") -> KbChunk:
        """نقل حالة صريح — يرفض الانتقالات غير المسموحة."""
        chunk = self.get_chunk(chunk_id)
        if chunk is None:
            raise ValueError(f"لا يوجد مقطع بالمعرّف {chunk_id}")
        # اسم المراجع شرط في كل انتقال: الاعتماد بلا مسؤولية طبية موثّقة ممنوع.
        # (ReviewDecision يتحقق كذلك عند البناء — وده حزام ثانٍ على مستوى التخزين.)
        if not (reviewer or "").strip():
            raise ValueError("تغيير الحالة يتطلب اسم مراجع — لا انتقال بدون مسؤولية")
        if not (reviewed_at or "").strip():
            raise ValueError("تغيير الحالة يتطلب تاريخ مراجعة")
        if not can_transition(chunk.status, status):
            raise TransitionError(
                f"انتقال غير مسموح: {chunk.status.value} → {status.value}"
            )
        with self._connect() as conn:
            conn.execute(
                "UPDATE kb_chunks SET status=?, reviewed_by=?, reviewed_at=?, updated_at=?"
                " WHERE id=?",
                (status.value, reviewer, reviewed_at, _now(), chunk_id),
            )
        updated = self.get_chunk(chunk_id)
        assert updated is not None
        return updated

    def list_chunks(self, statuses: list[ChunkStatus] | None = None) -> list[KbChunk]:
        query = "SELECT * FROM kb_chunks"
        params: list = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            params = [s.value for s in statuses]
        query += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_chunk(r) for r in rows]

    def keyword_search(self, query: str, limit: int, statuses: list[ChunkStatus],
                       language: str | None) -> list[tuple[str, float]]:
        """بحث كلمي بسيط: عدد كلمات الاستعلام الموجودة في نص المقطع.

        مقابل `tsv`/`ts_rank` في PostgreSQL. الدرجة هنا متجانسة (0..1) حتى
        تصلح لعتبة الصلة.
        """
        from ..services.arabic import retrieval_tokens
        base = retrieval_tokens(query)
        tokens = set(retrieval_tokens(query, expand=True))
        if not tokens:
            return []
        denominator = max(1, len(base))

        placeholders = ",".join("?" for _ in statuses)
        query_sql = f"SELECT id, search_text FROM kb_chunks WHERE status IN ({placeholders})"
        params: list = [s.value for s in statuses]
        if language:
            query_sql += " AND language = ?"
            params.append(language)

        scored: list[tuple[str, float]] = []
        with self._connect() as conn:
            rows = conn.execute(query_sql, params).fetchall()
        for row in rows:
            row_tokens = set(retrieval_tokens(row["search_text"] or ""))
            overlap = tokens & row_tokens
            if not overlap:
                continue
            scored.append((row["id"], min(1.0, len(overlap) / denominator)))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:limit]

    # -------------------------------------------------------------- للفهرسة فقط
    def all_search_texts(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, search_text FROM kb_chunks").fetchall()
        return {r["id"]: r["search_text"] for r in rows}
