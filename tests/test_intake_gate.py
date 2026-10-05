"""حارسُ الاستلام: لا يضع قالبُ مسألةٍ وسمَ عائلةٍ عاملة تلقائيًّا (البندان ٤ و٦ من #296؛ `tools/intake_gate.py`)."""
from __future__ import annotations

import pytest

from tools import intake_gate as ig


def test_no_published_template_grants_a_working_family_label():
    templates, config = ig.published()
    assert templates and ig.intake_findings(templates, config) == []


@pytest.mark.parametrize("line, code", [
    pytest.param('labels: ["task", "family:anthropic"]', "working_family_label:t.yml:family:anthropic", id="anthropic"),
    pytest.param('labels: ["family:openai"]', "working_family_label:t.yml:family:openai", id="openai"),
    pytest.param('labels: ["ready:google"]', "working_family_label:t.yml:ready:google", id="ready_label"),
    pytest.param('labels:\n  - "family:anthropic"', "labels_unreadable:t.yml", id="block_list"),
    pytest.param("labels: [task, family:anthropic]", "labels_unreadable:t.yml", id="unquoted_items"),
    pytest.param('"labels": ["family:anthropic"]', "labels_unreadable:t.yml", id="quoted_key"),
    pytest.param('labels: ["task"]\nlabels: ["family:google"]', "labels_unreadable:t.yml", id="repeated_key"),
])
def test_a_template_that_could_grant_an_agent_label_is_named(line, code):
    text = f"name: t\n{line}\nbody: []\n"
    assert ig.intake_findings({"t.yml": text}, "blank_issues_enabled: false\n") == [code]


def test_the_owner_decision_label_is_not_a_working_family():
    text = 'name: t\nlabels: ["owner-decision", "family:owner"]\n'
    assert ig.intake_findings({"t.yml": text}, "blank_issues_enabled: false\n") == []


def test_blank_issues_must_stay_disabled():
    templates, _ = ig.published()
    assert ig.intake_findings(templates, "blank_issues_enabled: true\n") == ["blank_issues_enabled"]


@pytest.mark.parametrize("text, found", [
    pytest.param('---\nname: t\nlabels: ["family:openai"]\n---\nبلاغ\n', ["working_family_label:t.md:family:openai"],
                 id="front_matter_grants"),
    pytest.param("---\nname: t\nlabels: family:openai\n---\n", ["labels_unreadable:t.md"], id="front_matter_bare"),
    pytest.param('---\nname: t\nlabels: ["family:openai"]\n', ["labels_unreadable:t.md"], id="front_matter_unclosed"),
    pytest.param("---\nname: t\n---\nlabels: anything in the body\n", [], id="body_is_not_front_matter"),
    pytest.param("# بلا رأس\nlabels: anything\n", [], id="no_front_matter"),
])
def test_markdown_templates_are_read_from_their_front_matter(text, found):
    assert ig.intake_findings({"t.md": text}, "blank_issues_enabled: false\n") == found


def test_every_template_extension_is_published_to_the_gate(tmp_path, monkeypatch):
    (tmp_path / "config.yml").write_text("blank_issues_enabled: false\n", encoding="utf-8")
    (tmp_path / "a.yaml").write_text('labels: ["family:google"]\n', encoding="utf-8")
    (tmp_path / "b.md").write_text('---\nlabels: ["family:openai"]\n---\n', encoding="utf-8")
    monkeypatch.setattr(ig, "TEMPLATES", tmp_path)
    assert ig.intake_findings(*ig.published()) == ["working_family_label:a.yaml:family:google",
                                                   "working_family_label:b.md:family:openai"]
