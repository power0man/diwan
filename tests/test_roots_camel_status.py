"""سببُ غياب CAMeL يُحفظ مع الحسم ويظهر على حكم الاحتياطيّ باسمه (مسحُ الإخفاقات الصامتة، مسار google بتسليم)."""
from __future__ import annotations

import pytest

from core.linguistics import roots


@pytest.fixture
def fresh():
    roots.camel_status.cache_clear()
    yield
    roots.camel_status.cache_clear()


def test_a_broken_camel_database_is_named_on_the_fallback_verdict(fresh, monkeypatch):
    def broken():
        raise RuntimeError("db missing")
    monkeypatch.setattr(roots, "_load_camel", broken)
    assert roots.camel_status() == (None, "camel_db_load_failed:RuntimeError")
    verdict = roots.root_verdict("كتاب")
    assert verdict.source == "template" and verdict.fallback == "camel_db_load_failed:RuntimeError"


def test_a_missing_camel_package_is_named_on_the_fallback_verdict(fresh, monkeypatch):
    def missing():
        raise ImportError("No module named camel_tools")
    monkeypatch.setattr(roots, "_load_camel", missing)
    assert roots.camel_unavailable_reason() == "camel_not_installed" and not roots.camel_available()
    assert roots.root_verdict("كتاب").fallback == "camel_not_installed"
    assert roots.root_verdict("كتاب", prefer="template").fallback is None, "الاحتياطيُّ المطلوب صراحةً ليس تراجعًا"


def test_the_sovereign_tool_reports_why_camel_was_not_used(fresh, monkeypatch):
    """ملاحظةُ Codex على #153: المستهلكُ الوحيد في الإنتاج (أداةُ الصرف) كان يُسقط الحقلَ فلا يرى الوكيلُ ولا الواجهةُ السبب."""
    from core.tools_registry import _analyze_morphology_handler

    def broken():
        raise RuntimeError("db missing")
    monkeypatch.setattr(roots, "_load_camel", broken)
    result = _analyze_morphology_handler({"word": "السفينة"})
    assert (result["root_source"], result["root_fallback"]) == ("template", "camel_db_load_failed:RuntimeError")

    class Analyzer:
        def analyze(self, word):
            return [{"root": "س.ف.ن"}]
    roots.camel_status.cache_clear()
    monkeypatch.setattr(roots, "_load_camel", Analyzer)
    result = _analyze_morphology_handler({"word": "السفينة"})
    assert (result["root_source"], result["root_fallback"]) == ("camel", None)
