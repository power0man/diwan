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
    pytest.param('labels: ["Family:OpenAI"]', "working_family_label:t.yml:Family:OpenAI", id="mixed_case_family"),
    pytest.param('labels: [" READY:GOOGLE "]', "working_family_label:t.yml: READY:GOOGLE ", id="padded_upper_ready"),
    pytest.param('labels:\n  - "family:anthropic"', "labels_unreadable:t.yml", id="block_list"),
    pytest.param("labels: [task, family:anthropic]", "labels_unreadable:t.yml", id="unquoted_items"),
    pytest.param('"labels": ["family:anthropic"]', "labels_unreadable:t.yml", id="quoted_key"),
    pytest.param('labels: ["task"]\nlabels: ["family:google"]', "labels_unreadable:t.yml", id="repeated_key"),
    pytest.param('  labels: ["family:openai"]', "labels_unreadable:t.yml", id="indented_key"),
    pytest.param('{name: t, labels: ["family:openai"], body: []}', "labels_unreadable:t.yml", id="flow_mapping"),
    pytest.param('--- {labels: ["family:openai"]}', "labels_unreadable:t.yml", id="document_start_flow"),
    pytest.param('? labels\n: ["family:openai"]', "labels_unreadable:t.yml", id="complex_key"),
    pytest.param('"labe\\u006cs": ["family:openai"]', "labels_unreadable:t.yml", id="unicode_escaped_key"),
    pytest.param('"\\x6cabels": ["family:openai"]', "labels_unreadable:t.yml", id="hex_escaped_key"),
    pytest.param('x: &k labels\n*k : ["family:openai"]', "labels_unreadable:t.yml", id="anchor_alias_key"),
])
def test_a_template_that_could_grant_an_agent_label_is_named(line, code):
    text = f"name: t\n{line}\nbody: []\n"
    assert ig.intake_findings({"t.yml": text}, "blank_issues_enabled: false\n") == [code]


def test_escapes_and_ampersands_in_values_are_not_keys():
    """القاعدتان لا تردّان قيمةً فيها تهريبٌ أو «&» داخل كلمة: التهريبُ مفتاحٌ إذا تلاه «:»، والمرساةُ بعد فاصل."""
    text = 'name: t\nlabels: ["task"]\ndescription: "tab\\tx and C:\\\\dir"\nabout: "R&D, Q&A"\nbody: []\n'
    assert ig.template_labels(text) == ["task"]


@pytest.mark.parametrize("name, text", [
    pytest.param("t.yml", '\ufefflabels: ["family:openai"]\n', id="yaml"),
    pytest.param("t.md", '\ufeff---\nlabels: ["family:openai"]\n---\nbody\n', id="markdown"),
])
def test_a_byte_order_mark_does_not_hide_the_labels(name, text):
    """ملاحظة Codex على #304: مفسّرُ YAML يُسقط علامةَ ترتيب البايتات، فلا يُخفي ما بعدها مفتاحًا ولا رأسًا."""
    assert ig.intake_findings({name: text}, "blank_issues_enabled: false\n") == [
        f"working_family_label:{name}:family:openai"]


@pytest.mark.parametrize("owner", [pytest.param("family:owner", id="lower"), pytest.param("Family:Owner", id="mixed_case")])
def test_the_owner_decision_label_is_not_a_working_family(owner):
    text = f'name: t\nlabels: ["owner-decision", "{owner}"]\n'
    assert ig.intake_findings({"t.yml": text}, "blank_issues_enabled: false\n") == []


@pytest.mark.parametrize("config", [
    pytest.param('contact_links:\n  - about: "x\nblank_issues_enabled: false\n    y"\nblank_issues_enabled: true\n',
                 id="false_inside_a_scalar_true_outside"),
    pytest.param('contact_links:\n  - about: "x\nblank_issues_enabled: false\n    y"\n"blank_issues_enabled": true\n',
                 id="false_inside_a_scalar_quoted_key_outside"),
    pytest.param('blank_issues_enabled: false\n"blank_issue\\u0073_enabled": true\n', id="escaped_key_beside"),
    pytest.param('x: &k blank_issues_enabled\nblank_issues_enabled: false\n', id="anchor"),
    pytest.param('  blank_issues_enabled: false\n', id="indented"),
    pytest.param('blank_issues_enabled: no\n', id="not_the_word_false"),
])
def test_only_one_unambiguous_top_level_false_disables_blank_issues(config):
    """ملاحظة Codex على #304: سطرُ false داخل نصٍّ مقتبسٍ متعدّد الأسطر لا يغلب المفتاحَ الفعليّ."""
    assert not ig.blank_issues_disabled(config)


def test_a_single_top_level_false_with_a_byte_order_mark_disables_blank_issues():
    assert ig.blank_issues_disabled("\ufeffblank_issues_enabled: false\ncontact_links: []\n")


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
    pytest.param('---\nname: t\nabout: |\n  نصّ\n  ---\nlabels: ["family:openai"]\n---\nبلاغ\n',
                 ["working_family_label:t.md:family:openai"], id="indented_dashes_inside_a_block_scalar"),
    pytest.param('---\nname: t\nlabels: ["family:openai"]\n---  \nبلاغ\n', ["working_family_label:t.md:family:openai"],
                 id="closing_dashes_with_trailing_space"),
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
