"""حزمة المعرفة وصلت من صاحبة المشروع — هل تبقى بواباتها مغلقة؟

الاختبارات هنا لا تفحص «قراءة ملفات» فقط، بل تفحص أن الثوابت الإلزامية تُفرض
على المحتوى حتى لو ادّعى ملف الاستيراد عكسها:

- مقطع بذرة يقول `status="approved"` و`reviewed_by="د. فلانة"` → يُستورد
  `draft_unreviewed` بلا مراجع، مع تسجيل الادّعاء المرفوض.
- مصدر يقول `approved_for_ingest=true` → لا يُستورد منه شيء من هذا المسار.

وتُستخدم ملفات مؤقتة، لا ملفات الحزمة الحقيقية: الحزمة لم تصل بعد، والاختبار
الذي ينتظر محتوى خارجيًا يتحوّل إلى اختبار «هل وصل المحتوى» لا «هل الكود سليم».
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.kb.pack import (
    EVAL_SEED_STATUS,
    SEED_SOURCE_ID,
    SEED_AUTHORED_BY,
    SEED_LICENSE_NOTE,
    SEED_STATUS,
    PackError,
    kb_root,
    load_eval_seed,
    load_glossary,
    load_knowledge_seed,
    load_sources_registry,
    main,
    pack_report,
)
from app.kb.ingest import ingest_records
from app.kb.schemas import ChunkStatus, KbSource
from app.kb.store import SqliteKbStore

SEED = [
    {"id": "seed-1", "source_id": "src-a", "title": "الدورة", "topic": "أساسيات",
     "language": "ar", "content": "ملخص مكتوب آليًا للمراجعة.",
     "source_refs_to_verify": [{"ref": "NHS: Periods"}]},
    {"id": "seed-2", "source_id": "src-a", "title": "ألم", "topic": "أعراض",
     "language": "ar", "content": "ملخص آخر للمراجعة.",
     "source_refs_to_verify": [{"ref": "ACOG: Dysmenorrhea"}]},
]

EVAL = [
    {"id": "q1", "question": "إيه أعراض ما قبل الدورة؟", "category": "education"},
    {"id": "q2", "question": "بنزف كتير، أعمل إيه؟", "category": "emergency"},
    {"id": "q3", "question": "إيه تكيّس المبايض؟", "category": "education"},
]

REGISTRY = {"sources": [
    {"id": "src-a", "name": "NHS", "language": "ar", "licence": "",
     "approved_for_ingest": True, "notes": "ادّعاء اعتماد آلي"},
    {"id": "src-b", "name": "ACOG", "language": "ar", "licence": "unknown",
     "approved_for_ingest": False},
]}

GLOSSARY = "term,preferred,avoid,status\nتكيّس المبايض,تكيّس المبايض,كيس على المبيض,approved\n"


@pytest.fixture
def kb_pack(tmp_path) -> Path:
    root = tmp_path / "kb"
    (root / "knowledge").mkdir(parents=True)
    (root / "eval").mkdir(parents=True)
    (root / "knowledge" / "knowledge_seed.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in SEED) + "\n", encoding="utf-8")
    (root / "eval" / "eval_questions_seed.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in EVAL) + "\n", encoding="utf-8")
    (root / "knowledge" / "sources_registry.json").write_text(
        json.dumps(REGISTRY, ensure_ascii=False), encoding="utf-8")
    (root / "knowledge" / "glossary_ar.csv").write_text(GLOSSARY, encoding="utf-8")
    return root


# ------------------------------------------------------------------ حالة الحزمة

def test_pack_report_detects_missing_files(tmp_path):
    report = pack_report(tmp_path / "kb")
    assert report["complete"] is False
    assert all(not info["exists"] for info in report["files"].values())
    assert len(report["files"]) == 4


def test_pack_report_complete_when_the_four_files_exist(kb_pack):
    report = pack_report(kb_pack)
    assert report["complete"] is True
    assert report["kb_root"] == str(kb_pack)


def test_check_command_fails_loudly_when_the_pack_never_arrived(tmp_path, capsys):
    assert main(["check", "--root", str(tmp_path / "kb")]) == 1
    out = capsys.readouterr().out
    assert "الحزمة ناقصة" in out
    assert "غائب" in out


def test_missing_seed_file_raises_instead_of_fabricating_content(tmp_path):
    with pytest.raises(PackError, match="غير موجود"):
        load_knowledge_seed(root=tmp_path / "kb")


# ------------------------------------------------------------------- ثوابت البذرة

def test_seed_loader_forces_unreviewed_constants(kb_pack):
    loaded = load_knowledge_seed(root=kb_pack)
    assert [r.id for r in loaded.records] == ["seed-1", "seed-2"]
    # ما لا يُسمح بقراءته من الملف لا يصل إلى السجل أصلًا
    for record in loaded.records:
        assert not hasattr(record, "status")
        assert not hasattr(record, "reviewed_by")
    assert loaded.license_note == SEED_LICENSE_NOTE


def test_seed_loader_ignores_review_claims_and_reports_them(tmp_path):
    root = tmp_path / "kb"
    (root / "knowledge").mkdir(parents=True)
    (root / "knowledge" / "knowledge_seed.jsonl").write_text(
        json.dumps({"id": "seed-x", "source_id": "src-a", "content": "نص.",
                    "status": "approved", "reviewed_by": "د. فلانة",
                    "reviewed_at": "2026-01-01"}, ensure_ascii=False) + "\n",
        encoding="utf-8")

    loaded = load_knowledge_seed(root=root)
    assert [r.id for r in loaded.records] == ["seed-x"]
    # لاحظي: القيمة «approved» + اسم طبيب مُنسب — أُلغيت الاثنتان وسُجّلتا
    assert ("seed-x", "status", "'approved'") in loaded.ignored_claims
    assert ("seed-x", "reviewed_by", "'د. فلانة'") in loaded.ignored_claims


def _human_approved(source_id: str = "src-a") -> dict[str, KbSource]:
    """اعتماد بشري صريح (كما يكتبه إنسان في السجل) — لا يمنحه مسار الحزمة."""
    return {source_id: KbSource(id=source_id, name="مصدر", approved_for_ingest=True)}


def test_ingested_seed_chunks_are_unreviewed_ai_drafts(kb_pack, tmp_path):
    store = SqliteKbStore(tmp_path / "kb.db")
    loaded = load_knowledge_seed(root=kb_pack)
    approved = _human_approved()
    for source in approved.values():
        store.upsert_source(source)

    report = ingest_records(store, loaded.records, approved)
    assert report.created == ["seed-1", "seed-2"]

    for chunk_id in ("seed-1", "seed-2"):
        chunk = store.get_chunk(chunk_id)
        assert chunk.status is ChunkStatus.DRAFT_UNREVIEWED
        assert chunk.reviewed_by == "" and chunk.reviewed_at == ""
        assert chunk.authored_by == SEED_AUTHORED_BY
        assert chunk.license_note == SEED_LICENSE_NOTE


def test_ai_draft_survives_a_content_update_and_goes_back_to_review(kb_pack, tmp_path):
    store = SqliteKbStore(tmp_path / "kb.db")
    loaded = load_knowledge_seed(root=kb_pack)
    approved = _human_approved()
    ingest_records(store, loaded.records, approved)

    # الطبيبة راجعت الأول واعتمدته (عبر المراجعة الطبية أولًا: لا قفز للحالة النهائية)
    store.set_status("seed-1", ChunkStatus.PHYSICIAN_REVIEWED, reviewer="د. فلانة",
                     reviewed_at="2026-10-01")
    store.set_status("seed-1", ChunkStatus.APPROVED, reviewer="د. فلانة",
                     reviewed_at="2026-10-01")
    assert store.get_chunk("seed-1").status is ChunkStatus.APPROVED

    # ووصل محتوى مُحدَّث للبذرة: يعود للمراجعة، مع الحفاظ على كونه مكتوبًا آليًا
    updated = loaded.records[0].model_copy(update={"content": "ملخص محدَّث للمراجعة."})
    ingest_records(store, [updated], approved)
    chunk = store.get_chunk("seed-1")
    assert chunk.status is ChunkStatus.DRAFT_UNREVIEWED
    assert chunk.content_version == 2
    assert chunk.authored_by == SEED_AUTHORED_BY


# ---------------------------------------------------------------- أسئلة التقييم

def test_eval_seed_is_marked_needs_review(kb_pack):
    questions = load_eval_seed(root=kb_pack)
    assert len(questions) == len(EVAL)          # لا دمج ولا إعادة ترقيم
    assert [q["id"] for q in questions] == ["q1", "q2", "q3"]
    assert all(q["status"] == EVAL_SEED_STATUS for q in questions)


def test_eval_seed_ignores_a_status_claimed_in_the_file(tmp_path):
    root = tmp_path / "kb" / "eval"
    root.mkdir(parents=True)
    (root / "eval_questions_seed.jsonl").write_text(
        json.dumps({"id": "q1", "question": "سؤال؟", "status": "approved"},
                   ensure_ascii=False) + "\n", encoding="utf-8")
    assert load_eval_seed(root=tmp_path / "kb")[0]["status"] == EVAL_SEED_STATUS


def test_eval_seed_requires_a_question(tmp_path):
    root = tmp_path / "kb" / "eval"
    root.mkdir(parents=True)
    (root / "eval_questions_seed.jsonl").write_text(
        json.dumps({"id": "q1"}, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(PackError, match="question"):
        load_eval_seed(root=tmp_path / "kb")


# ------------------------------------------------------------------ سجل المصادر

def test_registry_never_flips_approved_for_ingest(kb_pack):
    registry = load_sources_registry(root=kb_pack)
    assert [s["approved_for_ingest"] for s in registry.sources] == [False, False]
    assert registry.rejected_auto_approvals == ["src-a"]


def test_ingest_from_a_self_approved_source_is_skipped(kb_pack, tmp_path):
    """حتى لو ادّعى السجل الاعتماد، لا يمرّ مقطع من هذا المسار."""
    store = SqliteKbStore(tmp_path / "kb.db")
    loaded = load_knowledge_seed(root=kb_pack)
    registry = load_sources_registry(root=kb_pack)
    approved = {s["id"]: KbSource(**{k: v for k, v in s.items() if k in KbSource.model_fields})
                for s in registry.sources if s["approved_for_ingest"]}

    report = ingest_records(store, loaded.records, approved)
    assert report.created == []
    assert [cid for cid, _ in report.skipped_unapproved_source] == ["seed-1", "seed-2"]
    assert store.get_chunk("seed-1") is None


# --------------------------------------------------------------- استيراد البذرة

def test_ingest_cli_puts_the_seed_in_as_drafts_only(kb_pack, tmp_path, capsys):
    """أمر واحد يستورد البذرة كمسودات، ولا شيء يصبح قابلًا للاسترجاع."""
    from app.kb.retrieval import producible_statuses
    from app.kb.store import SqliteKbStore

    db = tmp_path / "kb.db"
    code = main(["ingest", "--root", str(kb_pack), "--db", str(db)])
    assert code == 0

    out = capsys.readouterr().out
    assert "مسودات draft_unreviewed" in out
    assert "ادّعاءات مراجعة أُلغيت" in out

    store = SqliteKbStore(db)
    assert store.list_chunks(producible_statuses()) == []          # لا شيء قابل للاسترجاع
    drafts = store.list_chunks([ChunkStatus.DRAFT_UNREVIEWED])
    assert sorted(c.id for c in drafts) == ["seed-1", "seed-2"]
    for chunk in drafts:
        assert chunk.reviewed_by == "" and chunk.reviewed_at == ""
        assert chunk.authored_by == SEED_AUTHORED_BY
        assert chunk.license_note == SEED_LICENSE_NOTE
        assert chunk.source_refs_to_verify                     # المراجع محفوظة للتحقق


def test_ingest_is_idempotent_and_reingest_does_not_create_citable_content(kb_pack, tmp_path):
    from app.kb.retrieval import producible_statuses
    from app.kb.store import SqliteKbStore

    db = tmp_path / "kb.db"
    main(["ingest", "--root", str(kb_pack), "--db", str(db)])
    main(["ingest", "--root", str(kb_pack), "--db", str(db)])

    store = SqliteKbStore(db)
    assert store.list_chunks(producible_statuses()) == []
    assert all(c.content_version == 1
               for c in store.list_chunks([ChunkStatus.DRAFT_UNREVIEWED]))


def test_pack_ingest_refuses_to_invent_a_missing_pack(tmp_path, capsys):
    code = main(["ingest", "--root", str(tmp_path / "nope"), "--db", str(tmp_path / "x.db")])
    assert code == 2
    assert "لا يمكن إكمال الأمر" in capsys.readouterr().err


def test_strict_path_still_refuses_a_self_approved_registry(kb_pack, tmp_path, capsys):
    """المسار الصارم (للنصوص المنسوخة) لا يستورد من مصدر لم يعتمده إنسان حقيقي."""
    code = main(["ingest", "--root", str(kb_pack), "--db", str(tmp_path / "kb.db"),
                 "--require-approved-source"])
    assert code == 0
    out = capsys.readouterr().out
    assert "بوابة الرخصة" in out


# ----------------------------------------------------------------------- القاموس

def test_glossary_loader_reads_the_pack_dictionary(kb_pack):
    rows = load_glossary(root=kb_pack)
    assert [r["term"] for r in rows] == ["تكيّس المبايض"]


def test_kb_root_defaults_to_the_repository_kb_directory():
    assert kb_root().name == "kb"


# ------------------------------------------- مخطط حزمة صاحبة المشروع (وصل فعلًا)

def test_seed_without_source_id_gets_a_neutral_internal_id(tmp_path):
    """بذرتها لا تحمل source_id: نضع معرّفًا محايدًا، ولا ننسب مقطعًا لجهة."""
    root = tmp_path / "kb" / "knowledge"
    root.mkdir(parents=True)
    (root / "knowledge_seed.jsonl").write_text(
        json.dumps({"id": "c1", "title": "عنوان", "topic": "موضوع", "language": "ar",
                    "content": "نص.", "source_refs_to_verify": ["NHS Periods"]},
                   ensure_ascii=False) + "\n", encoding="utf-8")

    loaded = load_knowledge_seed(root=tmp_path / "kb")
    assert loaded.placeholder_source_ids == ["c1"]
    assert loaded.records[0].source_id == SEED_SOURCE_ID


def test_registry_row_keeps_licence_and_maps_to_kb_source():
    from app.kb.pack import to_kb_source

    row = {"id": "nhs", "name": "NHS website (UK)", "type": "gov_public_health",
           "url": "https://www.nhs.uk", "languages": "en",
           "license_status": "check before ingest",
           "ingestion_method": "official_content_api_if_permitted",
           "approved_for_ingest": True}          # ادّعاء اعتماد: لا يمرّ

    source = to_kb_source(row)
    assert source.id == "nhs" and source.language == "en"
    assert source.licence == "check before ingest"          # الرخصة محفوظة كما هي
    assert source.approved_for_ingest is False              # ولا تُقلب أبدًا
    assert "gov_public_health" in source.notes
    assert "official_content_api_if_permitted" in source.notes


def test_pack_glossary_columns_are_read_as_the_source_of_truth(tmp_path):
    """قاموسها بأعمدة مختلفة: يُقرأ، والصيغة الرسمية البديلة ليست خطأ."""
    from tools.check_glossary import load_glossary

    path = tmp_path / "glossary_ar.csv"
    path.write_text(
        "english,arabic_preferred_user_facing,arabic_formal_alt,note\n"
        "amenorrhea,غياب الدورة,انقطاع الطمث,Do not confuse with menopause\n",
        encoding="utf-8")

    terms, issues = load_glossary(path)
    assert issues == []
    assert terms[0].term_en == "amenorrhea"
    assert terms[0].preferred_ar == "غياب الدورة"
    assert terms[0].not_preferred == []          # البديل الرسمي مسموح لا ممنوع
    assert terms[0].needs_review is True         # لا حالة مراجعة في الملف ⇒ لا نخترعها
