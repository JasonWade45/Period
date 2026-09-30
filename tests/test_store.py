"""اختبارات طبقة التخزين: تحقّق مدخلات، إحصاءات، وعزل المستخدمات."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.store import SEVERITY_MAX, SEVERITY_MIN, Store, StoreError


@pytest.fixture
def s(tmp_path) -> Store:
    return Store(tmp_path / "s.db")


def _day(offset: int) -> str:
    return (date.today() - timedelta(days=offset)).isoformat()


# ------------------------------------------------------------------- الدورات

def test_add_and_list_cycles(s):
    s.add_cycle("u1", "2026-06-05", 38)
    s.add_cycle("u1", "2026-07-13", 41)

    rows = s.list_cycles("u1")
    assert [r["start_date"] for r in rows] == ["2026-07-13", "2026-06-05"]  # الأحدث أولًا
    assert rows[0]["length_days"] == 41


def test_duplicate_start_date_is_rejected(s):
    s.add_cycle("u1", "2026-06-05")
    with pytest.raises(StoreError):
        s.add_cycle("u1", "2026-06-05")


def test_invalid_date_is_rejected(s):
    with pytest.raises(StoreError):
        s.add_cycle("u1", "13/07/2026")


def test_impossible_length_is_rejected(s):
    for bad in (0, -3, 91, 400):
        with pytest.raises(StoreError):
            s.add_cycle("u1", "2026-06-05", bad)


def test_length_and_severity_type_checks(s):
    with pytest.raises(StoreError):
        s.add_cycle("u1", "2026-06-05", "38")  # نص بدل عدد
    with pytest.raises(StoreError):
        s.add_symptom("u1", _day(1), "صداع", "3")  # نص بدل عدد


def test_users_are_isolated(s):
    s.add_cycle("u1", "2026-06-05")
    s.add_cycle("u2", "2026-07-01")
    assert len(s.list_cycles("u1")) == 1
    assert s.list_cycles("u2")[0]["start_date"] == "2026-07-01"


def test_delete_cycle(s):
    row = s.add_cycle("u1", "2026-06-05")
    assert s.delete_cycle("u1", row["id"]) is True
    assert s.list_cycles("u1") == []
    assert s.delete_cycle("u1", row["id"]) is False


def test_delete_is_scoped_to_owner(s):
    """مستخدمة لا تستطيع حذف صف مستخدمة أخرى بنفس المعرّف."""
    other = s.add_cycle("u2", "2026-06-05")
    assert s.delete_cycle("u1", other["id"]) is False
    assert len(s.list_cycles("u2")) == 1


# ------------------------------------------------------------------ الإحصاءات

def test_cycle_stats_computes_average_from_real_dates(s):
    """المتوسط يُحسب من فروق تواريخ البداية لا من الأطوال المُدخلة يدويًا."""
    s.add_cycle("u1", "2026-06-05", 5)     # الطول المُدخل خاطئ عمدًا
    s.add_cycle("u1", "2026-07-03", 5)     # الفرق الحقيقي 28 يومًا
    s.add_cycle("u1", "2026-07-31", 5)

    stats = s.cycle_stats("u1")
    assert stats["cycles_recorded"] == 3
    assert stats["avg_cycle_days"] == 28
    assert stats["last_cycles"][0]["start_date"] == "2026-07-31"


def test_cycle_stats_ignores_unrealistic_gaps(s):
    """فجوة 700 يومًا = إدخال خاطئ، لا دورة طويلة."""
    s.add_cycle("u1", "2024-01-01")
    s.add_cycle("u1", "2026-06-05")
    assert s.cycle_stats("u1")["avg_cycle_days"] is None


def test_cycle_stats_empty_user(s):
    stats = s.cycle_stats("nobody")
    assert stats == {"cycles_recorded": 0, "avg_cycle_days": None, "last_cycles": []}


# ------------------------------------------------------------------- الأعراض

def test_add_and_list_symptoms(s):
    s.add_symptom("u1", _day(2), "صداع", 3, "قبل الدورة بيوم")
    s.add_symptom("u1", _day(1), "تقلصات", 4)

    rows = s.list_symptoms("u1")
    assert [r["symptom"] for r in rows] == ["تقلصات", "صداع"]
    assert rows[1]["note"] == "قبل الدورة بيوم"


def test_symptom_severity_range_is_enforced(s):
    with pytest.raises(StoreError):
        s.add_symptom("u1", _day(1), "صداع", SEVERITY_MAX + 1)
    with pytest.raises(StoreError):
        s.add_symptom("u1", _day(1), "صداع", SEVERITY_MIN - 1)


def test_symptom_requires_a_name(s):
    with pytest.raises(StoreError):
        s.add_symptom("u1", _day(1), "   ", 3)


def test_symptom_note_is_truncated_not_rejected(s):
    row = s.add_symptom("u1", _day(1), "صداع", 3, "ط" * 900)
    assert len(row["note"]) == 500


def test_since_days_filter(s):
    s.add_symptom("u1", _day(2), "قريب", 2)
    s.add_symptom("u1", _day(200), "قديم", 2)
    assert [r["symptom"] for r in s.list_symptoms("u1", since_days=30)] == ["قريب"]


def test_delete_all_data(s):
    s.add_cycle("u1", "2026-06-05")
    s.add_symptom("u1", _day(1), "صداع", 3)
    s.add_symptom("u2", _day(1), "صداع", 3)

    result = s.delete_all("u1")
    assert result == {"cycles_deleted": 1, "symptoms_deleted": 1}
    assert s.list_cycles("u1") == []
    assert len(s.list_symptoms("u2")) == 1  # بيانات مستخدمة أخرى لم تُمس


def test_store_persists_across_instances(tmp_path):
    path = tmp_path / "persist.db"
    Store(path).add_cycle("u1", "2026-06-05")
    assert len(Store(path).list_cycles("u1")) == 1
