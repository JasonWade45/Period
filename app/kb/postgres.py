"""تخزين قاعدة المعرفة على PostgreSQL + pgvector (للنشر فقط).

⚠️ حالة التحقق: هذا الملف **لم يُشغَّل في بيئة التطوير** لأن PostgreSQL وpgvector
غير مثبّتين هنا ومستودعات الحزم محجوبة. كُتب ليطابق `SqliteKbStore` سلوكًا
(نفس قواعد الحالة، نفس عتبات الاسترجاع، نفس دلالات الإرجاع) وليكون مرجعًا
قابلاً للمراجعة. قبل أول نشر يجب تشغيله مرة واحدة مع psycopg متاحًا وتنفيذ
`pytest -m postgres` بعد ضبط `DATABASE_URL`.

التنفيذ يعتمد psycopg 3 (وليس SQLAlchemy) لسببين: الاستعلامات هنا SQL صريح
قابل للنسخ إلى psql للمراجعة، وتجنّب طبقة ORM يسهل فيها إخفاء `<=>`.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Optional

from ..config import settings
from ..services.arabic import normalize_for_search
from .embedding import EMBEDDING_DIM, to_pgvector_literal
from .schemas import (
    ChunkStatus,
    KbChunk,
    KbSource,
    TransitionError,
    can_transition,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect(dsn: str | None = None):
    """اتصال psycopg — يُستورد بتأخير حتى لا يكون psycopg شرطًا للتشغيل المحلي."""
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - يعتمد على بيئة النشر
        raise RuntimeError(
            "تنفيذ PostgreSQL يحتاج الحزمة psycopg: pip install 'psycopg[binary]'"
        ) from exc
    return psycopg.connect(dsn or settings.kb_postgres_dsn, autocommit=False)


class PostgresKbStore:
    """نفس واجهة KbStore، بلا نسخ منطق: القواعد تأتي من schemas/store."""

    backend = "postgres"

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or settings.kb_postgres_dsn

    # ------------------------------------------------------------------ sources
    def upsert_source(self, source: KbSource) -> None:
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_sources
                    (id, name, language, url, licence, approved_for_ingest, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name, language = EXCLUDED.language,
                    url = EXCLUDED.url, licence = EXCLUDED.licence,
                    approved_for_ingest = EXCLUDED.approved_for_ingest,
                    notes = EXCLUDED.notes
                """,
                (source.id, source.name, source.language, source.url, source.licence,
                 source.approved_for_ingest, source.notes),
            )

    def get_source(self, source_id: str) -> Optional[KbSource]:
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM kb_sources WHERE id = %s", (source_id,))
            row = cur.fetchone()
            return self._row_to_source(cur, row) if row else None

    def list_sources(self) -> list[KbSource]:
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM kb_sources ORDER BY id")
            return [self._row_to_source(cur, row) for row in cur.fetchall()]

    @staticmethod
    def _row_to_source(cur, row: tuple) -> KbSource:
        data = dict(zip([d.name for d in cur.description], row))
        return KbSource(
            id=data["id"], name=data["name"], language=data["language"],
            url=data["url"], licence=data["licence"],
            approved_for_ingest=bool(data["approved_for_ingest"]), notes=data["notes"],
        )

    # ------------------------------------------------------------------- chunks
    def get_chunk(self, chunk_id: str) -> Optional[KbChunk]:
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM kb_chunks WHERE id = %s", (chunk_id,))
            row = cur.fetchone()
            return self._row_to_chunk(cur, row) if row else None

    @staticmethod
    def _row_to_chunk(cur, row: tuple) -> KbChunk:
        data = dict(zip([d.name for d in cur.description], row))
        reviewed_at = data["reviewed_at"]
        if isinstance(reviewed_at, (date, datetime)):
            reviewed_at = reviewed_at.isoformat()
        refs = data["source_refs_to_verify"] or []
        if isinstance(refs, str):                    # عمود نصي في حالات الترقية
            refs = json.loads(refs)
        return KbChunk(
            id=data["id"], source_id=data["source_id"], title=data["title"],
            topic=data["topic"], language=data["language"], content=data["content"],
            status=ChunkStatus(data["status"]), reviewed_by=data["reviewed_by"] or "",
            reviewed_at=reviewed_at or "",
            authored_by=data.get("authored_by") or "",
            license_note=data.get("license_note") or "",
            content_version=data["content_version"],
            source_refs_to_verify=list(refs), content_hash=data["content_hash"] or "",
        )

    # --------------------------------------------------------------- المتجهات
    def set_embedding(self, chunk_id: str, vector: list[float]) -> None:
        if len(vector) != EMBEDDING_DIM:
            raise ValueError(
                f"بُعد المتجه {len(vector)} لا يطابق VECTOR({EMBEDDING_DIM}) في المخطط"
            )
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("UPDATE kb_chunks SET embedding = %s::vector WHERE id = %s",
                        (to_pgvector_literal(vector), chunk_id))

    def get_embedding(self, chunk_id: str) -> Optional[list[float]]:
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT embedding::text FROM kb_chunks WHERE id = %s", (chunk_id,))
            row = cur.fetchone()
        return _parse_vector(row[0]) if row and row[0] else None

    def all_embeddings(self) -> dict[str, list[float]]:
        """للاستخدام في الفحص والتشخيص فقط — الاسترجاع يستخدم `vector_search`."""
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT id, embedding::text FROM kb_chunks WHERE embedding IS NOT NULL")
            return {cid: _parse_vector(vec) for cid, vec in cur.fetchall()}

    def vector_search(self, vector: list[float], limit: int, statuses: list[ChunkStatus],
                      language: str | None, min_similarity: float
                      ) -> list[tuple[str, float]]:
        """أقرب المقاطع عبر `<=>` مع فهرس HNSW.

        التصفية بالحالة واللغة داخل نفس الاستعلام — لا نُخرج صفوفًا غير مسموح
        بها ثم نُسقطها في التطبيق. المسافة الجزئية `1 - (embedding <=> q)`
        تُقارن بالعتبة في نفس مكان الترتيب ليستفيد الفهرس من LIMIT.
        """
        literal = to_pgvector_literal(vector)
        sql = """
            SELECT id, 1 - (embedding <=> %s::vector) AS similarity
            FROM kb_chunks
            WHERE status = ANY(%s)
              AND embedding IS NOT NULL
              AND (%s::text IS NULL OR language = %s)
              AND 1 - (embedding <=> %s::vector) >= %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        params = (literal, [s.value for s in statuses], language, language,
                  literal, min_similarity, literal, limit)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [(row[0], float(row[1])) for row in cur.fetchall()]

    def keyword_search(self, query: str, limit: int, statuses: list[ChunkStatus],
                       language: str | None) -> list[tuple[str, float]]:
        """بحث كلمي على `tsv` (فهرس GIN) بنفس النص المُطبَّع المستخدَم عند الكتابة."""
        normalized = normalize_for_search(query)
        if not normalized:
            return []
        sql = """
            SELECT id, ts_rank(tsv, plainto_tsquery('simple', %s)) AS score
            FROM kb_chunks
            WHERE status = ANY(%s)
              AND (%s::text IS NULL OR language = %s)
              AND tsv @@ plainto_tsquery('simple', %s)
            ORDER BY score DESC
            LIMIT %s
        """
        params = (normalized, [s.value for s in statuses], language, language,
                  normalized, limit)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [(row[0], float(row[1])) for row in cur.fetchall()]

    # ------------------------------------------------------------------ كتابة
    def upsert_chunk(self, chunk: KbChunk) -> str:
        """«created» | «unchanged» | «content_changed» — نفس دلالات SQLite.

        نُقرأ الصف الحالي داخل معاملة واحدة مع الكتابة، ونقفل الصف (FOR UPDATE)
        حتى لا يتسابق استيرادان على نفس المقطع. الشرط كله يعتمد على content_hash
        المُحسوب من النص المُطبَّع (تشكيل/أرقام عربية/همزات لا تُعدّ تغييرًا).
        """
        new_hash = chunk.compute_hash()
        search_text = chunk.search_text or normalize_for_search(
            f"{chunk.title} {chunk.topic} {chunk.content}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT content_hash, content_version, status FROM kb_chunks"
                        " WHERE id = %s FOR UPDATE", (chunk.id,))
            existing = cur.fetchone()

            if existing is None:
                cur.execute(
                    """
                    INSERT INTO kb_chunks
                        (id, source_id, title, topic, language, content, status,
                         reviewed_by, reviewed_at, authored_by, license_note,
                         content_version,
                         source_refs_to_verify, content_hash, search_text, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'',NULL,%s,%s,1,%s::jsonb,%s,%s,%s)
                    """,
                    (chunk.id, chunk.source_id, chunk.title, chunk.topic, chunk.language,
                     chunk.content, ChunkStatus.DRAFT_UNREVIEWED.value,
                     chunk.authored_by, chunk.license_note,
                     json.dumps(chunk.source_refs_to_verify, ensure_ascii=False),
                     new_hash, search_text, _now()),
                )
                return "created"

            old_hash, old_version, _old_status = existing
            if old_hash == new_hash:
                return "unchanged"

            # المحتوى تغيّر: يُعاد للمراجعة، الإصدار يزيد، والمتجه القديم يُمسح
            # (متجه نص قديم على نص جديد يجعل البحث يعقّر بنتيجة لا تخص المحتوى).
            cur.execute(
                """
                UPDATE kb_chunks SET
                    source_id=%s, title=%s, topic=%s, language=%s, content=%s,
                    status=%s, reviewed_by='', reviewed_at=NULL,
                    authored_by=%s, license_note=%s,
                    content_version=%s, source_refs_to_verify=%s::jsonb,
                    content_hash=%s, search_text=%s, embedding=NULL, updated_at=%s
                WHERE id=%s
                """,
                (chunk.source_id, chunk.title, chunk.topic, chunk.language, chunk.content,
                 ChunkStatus.DRAFT_UNREVIEWED.value, chunk.authored_by, chunk.license_note,
                 old_version + 1,
                 json.dumps(chunk.source_refs_to_verify, ensure_ascii=False),
                 new_hash, search_text, _now(), chunk.id),
            )
            return "content_changed"

    def set_status(self, chunk_id: str, status: ChunkStatus, reviewer: str,
                   reviewed_at: str, note: str = "") -> KbChunk:
        """نقل حالة صريح — يرفض الانتقالات غير المسموحة. لا اعتماد بلا اسم مراجع."""
        if not (reviewer or "").strip():
            raise ValueError("تغيير الحالة يتطلب اسم مراجع — لا انتقال بدون مسؤولية")
        if not (reviewed_at or "").strip():
            raise ValueError("تغيير الحالة يتطلب تاريخ مراجعة")

        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM kb_chunks WHERE id = %s FOR UPDATE",
                        (chunk_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"لا يوجد مقطع بالمعرّف {chunk_id}")
            current = ChunkStatus(row[0])
            if not can_transition(current, status):
                raise TransitionError(
                    f"انتقال غير مسموح: {current.value} → {status.value}"
                )
            cur.execute(
                "UPDATE kb_chunks SET status=%s, reviewed_by=%s, reviewed_at=%s,"
                " updated_at=%s WHERE id=%s",
                (status.value, reviewer, reviewed_at, _now(), chunk_id),
            )
        updated = self.get_chunk(chunk_id)
        assert updated is not None
        return updated

    def list_chunks(self, statuses: list[ChunkStatus] | None = None) -> list[KbChunk]:
        sql = "SELECT * FROM kb_chunks"
        params: tuple = ()
        if statuses:
            sql += " WHERE status = ANY(%s)"
            params = ([s.value for s in statuses],)
        sql += " ORDER BY id"
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [self._row_to_chunk(cur, row) for row in cur.fetchall()]

    # ------------------------------------------------------------------- صحة
    def health(self) -> dict[str, Any]:
        """تشخيص سريع: الامتداد، الأبعاد، عدد المقاطع لكل حالة."""
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            version = cur.fetchone()
            cur.execute("SELECT status, count(*) FROM kb_chunks GROUP BY status")
            counts = {status: count for status, count in cur.fetchall()}
            cur.execute("SELECT count(*) FROM kb_chunks WHERE embedding IS NULL")
            missing = cur.fetchone()[0]
        return {"pgvector": version[0] if version else None, "counts": counts,
                "chunks_without_embedding": missing, "expected_dim": EMBEDDING_DIM}


def _parse_vector(raw: str | None) -> Optional[list[float]]:
    """يقرأ تمثيل pgvector النصي '[0.1,0.2,...]'."""
    if not raw:
        return None
    return [float(part) for part in raw.strip().strip("[]").split(",") if part]


def build_store(*, backend: str | None = None, sqlite_path: str | None = None):
    """اختيار التنفيذ من الإعدادات — نقطة واحدة تحكم كل الاستدعاءات."""
    chosen = (backend or settings.kb_backend or "sqlite").lower()
    if chosen == "postgres":
        return PostgresKbStore()
    from .store import SqliteKbStore
    from pathlib import Path
    return SqliteKbStore(Path(sqlite_path or settings.kb_db_path))


__all__ = ["PostgresKbStore", "build_store"]
