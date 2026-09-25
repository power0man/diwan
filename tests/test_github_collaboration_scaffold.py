"""بنيةُ العمل الجماعي على GitHub تحمل قواعد ديوان لا قوالبَ فارغة (ك٣٩، ق٦١).

قالبُ طلب الدمج يحمل خاناتِ ديوان (المعرّف و`Closes`، والتسليم، وما قيس، والطفرة، والحدود، وخانةُ المحجوب)؛ وملفُّ الوسوم يغطّي
البلوكاتِ الاثني عشر والعائلاتِ والمراحلَ والقدرات، وأوصافُه في حدّ GitHub؛ ومهمّةُ مزامنتها بلا أسرارٍ وبصلاحيةٍ ضيّقة. ما لا يُفحص
هنا، معلنًا: أن GitHub يقبل أشكالَ المسائل (يُرى حين تُفتح المسألة).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GITHUB = ROOT / ".github"


def test_the_pull_request_template_carries_every_diwan_field():
    text = (GITHUB / "pull_request_template.md").read_text(encoding="utf-8")
    for field in ("Closes #", "«تسليم:»", "## ما قيس وما لم يُقَس", "## الطفرة", "## الحدود",
                  "Diwan-Agent:", "tools/lane_handoff.py", "لا نصَّ محجوبًا ولا خاصًّا"):
        assert field in text, field


def _labels():
    return json.loads((GITHUB / "labels.json").read_text(encoding="utf-8"))


def test_labels_cover_blocks_families_phases_and_capabilities():
    names = {label["name"] for label in _labels()["labels"]}
    assert {f"block:b{n}" for n in range(1, 13)} <= names
    assert {"family:anthropic", "family:openai", "family:google", "family:owner"} <= names
    assert {"phase:m0", "phase:m1", "phase:m2", "phase:m3", "phase:m4", "phase:v1"} <= names
    assert {"cap:analyst", "cap:coder", "cap:image-gen", "cap:voice", "cap:ocr", "cap:export", "cap:memory"} <= names
    assert {"task", "measurement", "owner-decision", "owner-action", "arabic-defect", "proof-missing", "node:policies"} <= names


@pytest.mark.parametrize("label", _labels()["labels"], ids=lambda l: l["name"])
def test_every_label_is_acceptable_to_github(label):
    assert set(label) == {"name", "color", "description"}
    assert re.fullmatch(r"[0-9a-f]{6}", label["color"]), label
    assert 0 < len(label["description"]) <= 100 and len(label["name"]) <= 50


def test_milestones_follow_the_plan_phases_in_order():
    milestones = _labels()["milestones"]
    assert [m["title"].split(" ")[0] for m in milestones] == ["v0.1.x", "v0.2", "v0.3", "v0.4", "v0.5", "v1.0"]
    dues = [m["due_on"] for m in milestones]
    assert dues == sorted(dues) and all(re.fullmatch(r"\d{4}-\d\d-\d\dT23:59:59Z", d) for d in dues)


def test_the_label_sync_writes_issues_only_and_reads_no_secret():
    workflow = (GITHUB / "workflows" / "labels.yml").read_text(encoding="utf-8")
    assert "issues: write" in workflow and "contents: read" in workflow
    assert "contents: write" not in workflow and "pull-requests: write" not in workflow
    assert "secrets." not in workflow and "persist-credentials: false" in workflow
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
    assert "pull_request" not in workflow, "لا تُشغَّل على طلبات الدمج"


def test_issue_forms_exist_and_the_arabic_defect_form_requires_the_privacy_pledge():
    forms = {p.name for p in (GITHUB / "ISSUE_TEMPLATE").glob("*.yml")}
    assert {"task.yml", "measurement.yml", "owner-decision.yml", "arabic-defect.yml", "config.yml"} <= forms
    defect = (GITHUB / "ISSUE_TEMPLATE" / "arabic-defect.yml").read_text(encoding="utf-8")
    assert "لا يحوي هذا البلاغ بياناتٍ شخصية" in defect and "required: true" in defect.split("id: privacy", 1)[1]
    measurement = (GITHUB / "ISSUE_TEMPLATE" / "measurement.yml").read_text(encoding="utf-8")
    for field in ("id: decision", "id: rule", "id: sample"):
        assert "required: true" in measurement.split(field, 1)[1].split("- type:", 1)[0], field


def test_every_block_in_the_task_form_is_a_labelled_block():
    form = (GITHUB / "ISSUE_TEMPLATE" / "task.yml").read_text(encoding="utf-8")
    blocks = re.findall(r'- "ب([٠-٩]+) ', form)
    assert len(blocks) == 12
