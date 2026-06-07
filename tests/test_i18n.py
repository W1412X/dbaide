"""Tests for the i18n core, config persistence, and model answer language."""

from __future__ import annotations

from pathlib import Path

import pytest

from dbaide import i18n
from dbaide.config import ConfigManager
from dbaide.models import ConnectionConfig


@pytest.fixture(autouse=True)
def _reset_language():
    prev = i18n.get_language()
    yield
    i18n.set_language(prev)


def test_translation_switches_language():
    i18n.set_language("en")
    assert i18n.t("tab.ask") == "Ask"
    i18n.set_language("zh")
    assert i18n.t("tab.ask") == "提问"


def test_unknown_key_returns_key():
    assert i18n.t("no.such.key") == "no.such.key"


def test_normalize_aliases():
    assert i18n.normalize("zh-CN") == "zh"
    assert i18n.normalize("English") == "en"
    assert i18n.normalize("nonsense") == "en"


def test_answer_language_directive_uses_explicit_question_language():
    i18n.set_language("en")
    assert "Chinese" in i18n.answer_language_directive("zh")
    i18n.set_language("zh")
    assert "English" in i18n.answer_language_directive("en")
    assert "question language" in i18n.answer_language_directive("en")


def test_detect_user_language():
    assert i18n.detect_user_language("统计订单数量") == "zh"
    assert i18n.detect_user_language("count orders") == "en"


def test_on_change_listener_fires():
    seen = []
    off = i18n.on_change(lambda lang: seen.append(lang))
    i18n.set_language("zh")
    i18n.set_language("en")
    off()
    i18n.set_language("zh")
    assert seen == ["zh", "en"]  # not the post-unsubscribe change


def test_config_ui_language_roundtrip(tmp_path: Path):
    cfg = ConfigManager(path=tmp_path / "c.toml")
    assert cfg.ui_language() == "en"  # default
    cfg.set_ui_language("zh")
    reloaded = ConfigManager(path=tmp_path / "c.toml")
    assert reloaded.ui_language() == "zh"
    # coexists with other config sections
    reloaded.upsert_connection(ConnectionConfig(name="c1", type="sqlite", path="/x.db"))
    again = ConfigManager(path=tmp_path / "c.toml")
    assert again.ui_language() == "zh" and "c1" in again.connections()


def test_result_interpreter_uses_question_language():
    from dbaide.agent.controllers import ResultInterpreter
    i18n.set_language("zh")
    out = ResultInterpreter().interpret(question="show data", sql="SELECT 1", row_count=0,
                                        columns=[], elapsed_ms=1, truncated=False, warnings=[])
    assert "query" in out["summary"].lower()
    i18n.set_language("en")
    out2 = ResultInterpreter().interpret(question="显示数据", sql="SELECT 1", row_count=0,
                                         columns=[], elapsed_ms=1, truncated=False, warnings=[])
    assert "查询" in out2["summary"]
