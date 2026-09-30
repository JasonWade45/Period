"""اختبارات تحميل الإعدادات من البيئة ومن ملف .env."""
from __future__ import annotations

import os
from pathlib import Path

import app.config as config


def test_missing_env_file_is_not_an_error(tmp_path: Path) -> None:
    assert config.load_env_file(tmp_path / "does-not-exist.env") is False


def test_env_file_fills_missing_variables(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CYCLECARE_TEST_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("CYCLECARE_TEST_KEY=from-file\n", encoding="utf-8")

    assert config.load_env_file(env_file) is True
    assert os.environ["CYCLECARE_TEST_KEY"] == "from-file"


def test_real_environment_wins_over_env_file(tmp_path: Path, monkeypatch) -> None:
    """بيئة النشر لا يجب أن يستبدلها ملف محلي."""
    monkeypatch.setenv("CYCLECARE_TEST_KEY", "from-env")
    env_file = tmp_path / ".env"
    env_file.write_text("CYCLECARE_TEST_KEY=from-file\n", encoding="utf-8")

    config.load_env_file(env_file)
    assert os.environ["CYCLECARE_TEST_KEY"] == "from-env"


def test_cors_origins_are_parsed_and_trimmed() -> None:
    s = config.Settings(cors_allow_origins=" https://a.example ,https://b.example ,")
    assert s.cors_origins == ["https://a.example", "https://b.example"]


def test_cors_defaults_to_same_origin_only() -> None:
    assert config.Settings().cors_origins == []


def test_default_emergency_number_is_flagged() -> None:
    assert config.Settings().emergency_number_is_default is True
    assert config.Settings(emergency_number="999").emergency_number_is_default is False
