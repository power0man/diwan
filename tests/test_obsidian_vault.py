"""خزنةُ Obsidian للمالك (جديد-obsidian-vault، ق٦٤): مرآةٌ خارج المستودع لا تمسّ المحجوبَ ولا ملاحظاتِ المالك.

كلُّ حارسٍ هنا مُثبَتٌ بالطفرة: كُسر في `tools/obsidian_vault.py` فسقط اختبارُه، ثم أُعيد.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("obsidian_vault", ROOT / "tools/obsidian_vault.py")
ov = importlib.util.module_from_spec(spec)
sys.modules["obsidian_vault"] = ov
spec.loader.exec_module(ov)

PLAN = {
    "title": "خطة تجريبية", "thesis": "أطروحة",
    "phases": [{"id": "م٠", "name": "الأولى", "start": "2026-09-28", "end": "2026-10-04", "goal": "هدف", "gate": ["بند"], "task_ids": ["ك١", "جديد-x"]}],
    "tasks": [
        {"id": "ك١", "title": "مهمّة", "assignee": "claude-cloud", "block": "ب٩", "phase": "م٠", "effort": "S", "depends_on": [],
         "description": "وصف", "deliverable": "مخرج", "acceptance_evidence": "دليل", "unblocks": ["جديد-x"], "guide_id": "G1"},
        {"id": "جديد-x", "title": "ثانية", "assignee": "owner", "block": "ب٩", "phase": "م٠", "effort": "S", "depends_on": ["ك١"],
         "description": "وصف", "deliverable": "مخرج", "acceptance_evidence": "دليل"},
    ],
    "owner_steps": [{"order": 1, "guide_id": "G1", "title": "خطوة", "time": "٥ دقائق", "cost": "$0"}],
}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "docs/guides").mkdir(parents=True)
    for f in ov.MIRROR_FILES:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(f"# {f}\n", encoding="utf-8")
    (root / "docs/guides/G1.md").write_text("# G1\n", encoding="utf-8")
    (root / "docs/secret.md").write_text("لا يُنسخ\n", encoding="utf-8")
    (root / ov.PLAN).write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    return root


@pytest.fixture
def vault(tmp_path):
    return tmp_path / "Diwan-Vault"


def test_build_writes_mirror_board_tasks_steps_and_inbox(repo, vault):
    report = ov.build(vault, root=repo)
    out = vault / "Diwan"
    assert (out / "الوثائق/STATUS.md").read_text(encoding="utf-8").startswith("> **مرآةٌ للقراءة**")
    assert (out / "الأدلة/G1.md").is_file()
    assert "[[المهام/ك١|ك١]]" in (out / "لوحة المراحل.md").read_text(encoding="utf-8")
    assert "[[المهام/ك١|ك١]]" in (out / "المهام/جديد-x.md").read_text(encoding="utf-8")
    assert "- [ ] **1. خطوة** · [[الأدلة/G1|G1]]" in (out / "خطواتي.md").read_text(encoding="utf-8")
    assert (vault / "Inbox/اقرأني.md").is_file() and (vault / "Inbox/منجز").is_dir()
    assert report["kept_edited"] == [] and ov.check(vault, root=repo) == []


def test_vault_inside_the_repository_is_refused(repo):
    with pytest.raises(ov.VaultError) as e:
        ov.build(repo / "vault", root=repo)
    assert e.value.code == "vault_inside_repository"
    with pytest.raises(ov.VaultError):
        ov.build(repo, root=repo)


@pytest.mark.parametrize("name", ["diwan-sealed", "Sealed", "x_SEALED_y"])
def test_a_sealed_path_is_refused_for_the_vault(tmp_path, repo, name):
    with pytest.raises(ov.VaultError) as e:
        ov.build(tmp_path / name / "vault", root=repo)
    assert e.value.code == "sealed_path_refused"


def test_only_the_named_files_are_mirrored(repo, vault):
    ov.build(vault, root=repo)
    names = {p.name for p in (vault / "Diwan").rglob("*.md")}
    assert "secret.md" not in names
    (repo / "docs/guides/sealed-notes.md").write_text("x", encoding="utf-8")
    ov.build(vault, root=repo)
    assert not any("sealed" in p.name for p in (vault / "Diwan").rglob("*"))


def test_a_sealed_source_in_the_list_raises(repo, vault, monkeypatch):
    (repo / "docs/Sealed").mkdir()
    (repo / "docs/Sealed/x.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ov, "MIRROR_FILES", ov.MIRROR_FILES + ("docs/Sealed/x.md",))
    with pytest.raises(ov.VaultError) as e:
        ov.build(vault, root=repo)
    assert e.value.code == "sealed_path_refused"


def test_an_owner_edit_is_kept_unless_forced(repo, vault):
    ov.build(vault, root=repo)
    board = vault / "Diwan/لوحة المراحل.md"
    board.write_text("ملاحظتي", encoding="utf-8")
    (repo / ov.PLAN).write_text(json.dumps({**PLAN, "title": "جديد"}, ensure_ascii=False), encoding="utf-8")
    report = ov.build(vault, root=repo)
    assert "لوحة المراحل.md" in report["kept_edited"]
    assert board.read_text(encoding="utf-8") == "ملاحظتي"
    ov.build(vault, root=repo, force=True)
    assert board.read_text(encoding="utf-8") != "ملاحظتي"


def test_the_steps_note_keeps_the_owner_s_checkmarks_and_notes_while_the_plan_s_deferrals_reach_it(repo, vault):
    """ملاحظةُ Codex على #161 (الجولة الثالثة): البذرةُ «خطواتي» كانت تُحفظ حرفيًّا حتى مع `--force`، فتأجيلٌ جديد في الخطة (ق٦٨)
    لا يبلغ خزنةً قائمة، وتبقى خطواتُ Nitro القديمة عملًا نشطًا. صارت تُرحَّل: علاماتُ المالك تبقى، وما أُنجز ثم أُجّل يبقى
    منجزًا باسمه، وما كتبه تحت «## ملاحظاتي» يُنقل، والتأجيلُ يصل."""
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    plan["owner_steps"] += [{"order": 2, "guide_id": "G5", "title": "تجهيز Nitro", "time": "ساعة", "cost": "$0"},
                            {"order": 3, "guide_id": "G4", "title": "صلاحيات Nitro", "time": "١٠ دقائق", "cost": "$0"}]
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    text = steps.read_text(encoding="utf-8")
    text = text.replace("- [ ] **1. خطوة**", "- [x] **1. خطوة**").replace("- [ ] **3. صلاحيات Nitro**", "- [x] **3. صلاحيات Nitro**")
    steps.write_text(text + "\n## ملاحظاتي\n\nسألت المحامي عن الخطوة ٢.\n", encoding="utf-8")
    why = {"by": "ق٦٨", "until": "2026-10-19", "reason": "الجهازُ غيرُ قابلٍ للوصول"}
    for step in plan["owner_steps"]:
        if step["order"] in (2, 3):
            step["deferred"] = why
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    assert ov.check(vault, root=repo) == ["stale خطواتي.md"]
    report = ov.build(vault, root=repo)                       # بلا --force: الترحيلُ يحفظ ما للمالك
    assert report["migrated"] == ["خطواتي.md"] and ov.check(vault, root=repo) == []
    migrated = steps.read_text(encoding="utf-8")
    active, postponed = migrated.split("## خطواتٌ مؤجَّلة")
    assert "- [x] **1. خطوة**" in active and "تجهيز Nitro" not in active
    assert "- ⏸ **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 — مؤجَّلة (ق٦٨" in postponed
    assert "- [x] **3. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ١٠ دقائق · $0 — مؤجَّلة (ق٦٨ حتى 2026-10-19: الجهازُ غيرُ قابلٍ للوصول) — أُنجزت قبل التأجيل" in postponed
    assert migrated.rstrip().endswith("## ملاحظاتي\n\nسألت المحامي عن الخطوة ٢.")
    assert ov.build(vault, root=repo, force=True)["migrated"] == [] and steps.read_text(encoding="utf-8") == migrated


def test_an_annotation_appended_to_a_checklist_line_survives_the_migration(repo, vault):
    """ملاحظةُ Codex على #161 (الجولة الخامسة): لاحقةٌ كتبها المالك على سطر الخطوة نفسِه («… — سألت المحامي») كانت تُمحى مع السطر
    كلِّه عند الترحيل. صارت تبقى على سطرها (وعلى الخطوة المؤجَّلة بعد إنجازها كذلك)، وسطرُ خطوةٍ عُدّل داخل نصّه المولَّد يُنقل كما هو
    إلى قسم الملاحظات، ولا تتضاعف اللواحقُ في البناء التالي."""
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    plan["owner_steps"] += [{"order": 2, "guide_id": "G5", "title": "تجهيز Nitro", "time": "ساعة", "cost": "$0"},
                            {"order": 3, "guide_id": "G4", "title": "صلاحيات Nitro", "time": "١٠ دقائق", "cost": "$0"}]
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    text = steps.read_text(encoding="utf-8")
    text = (text.replace("- [ ] **1. خطوة** · [[الأدلة/G1|G1]] · ٥ دقائق · $0", "- [x] **1. خطوة** · [[الأدلة/G1|G1]] · ٥ دقائق · $0 — سألت المحامي")
                .replace("- [ ] **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0", "- [x] **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 (نصف ساعة فقط)")
                .replace("- [ ] **3. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ١٠ دقائق · $0", "- [ ] **3. صلاحيات Nitro (على الماك أولًا)** · [[الأدلة/G4|G4]] · ١٠ دقائق · $0"))
    steps.write_text(text, encoding="utf-8")
    why = {"by": "ق٦٨", "until": "2026-10-19", "reason": "الجهازُ غيرُ قابلٍ للوصول"}
    plan["owner_steps"][1]["deferred"] = why
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    assert ov.build(vault, root=repo)["migrated"] == ["خطواتي.md"]
    migrated = steps.read_text(encoding="utf-8")
    active, rest = migrated.split("## خطواتٌ مؤجَّلة")
    assert "- [x] **1. خطوة** · [[الأدلة/G1|G1]] · ٥ دقائق · $0 — سألت المحامي" in active
    assert "- [ ] **3. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ١٠ دقائق · $0" in active, "السطرُ المولَّد يعود والمعدَّلُ داخله يُنقل"
    assert "- [x] **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 — مؤجَّلة (ق٦٨ حتى 2026-10-19: الجهازُ غيرُ قابلٍ للوصول) — أُنجزت قبل التأجيل (نصف ساعة فقط)" in rest
    carried = [line for line in rest.split("### سطورٌ نُقلت من النسخة السابقة")[1].splitlines() if line.strip()]
    assert carried == ["- [ ] **3. صلاحيات Nitro (على الماك أولًا)** · [[الأدلة/G4|G4]] · ١٠ دقائق · $0"]
    assert ov.check(vault, root=repo) == [] and ov.build(vault, root=repo)["migrated"] == []
    assert steps.read_text(encoding="utf-8") == migrated, "لا تتضاعف اللواحق"


def test_an_annotation_after_a_deferral_label_survives_the_migration(repo, vault):
    """ملاحظةُ Codex على #161 (الجولة السابعة): تعليقٌ كتبه المالك بقوسين بعد علامة التأجيل المولَّدة «… مؤجَّلة (…) (سألت المالك)»
    كان يُحسب من العلامة (كان قوسُها يُطلَب عند آخر السطر) فيُمحى في البناء التالي. صارت العلامةُ تُنزع حتى قوسها الذي يُغلقها
    بعدّ الأقواس، فيبقى التعليقُ حتى لو حمل سببُ التأجيل قوسين مثل «(ق٦٨)»، ويتبع العلامةَ الجديدة إذا تغيّر السبب."""
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    why = {"by": "ق٦٨", "until": "2026-10-19", "reason": "الجهازُ غيرُ قابلٍ للوصول (ق٦٨)؛ يعود بإشعار المالك"}
    plan["owner_steps"].append({"order": 2, "guide_id": "G5", "title": "تجهيز Nitro", "time": "ساعة", "cost": "$0", "deferred": why})
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    label = "- ⏸ **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 — مؤجَّلة (ق٦٨ حتى 2026-10-19: الجهازُ غيرُ قابلٍ للوصول (ق٦٨)؛ يعود بإشعار المالك)"
    text = steps.read_text(encoding="utf-8")
    assert label in text
    steps.write_text(text.replace(label, label + " (سألت المالك)"), encoding="utf-8")
    assert ov.build(vault, root=repo)["migrated"] == ["خطواتي.md"] or ov.check(vault, root=repo) == []
    assert label + " (سألت المالك)" in steps.read_text(encoding="utf-8"), "التعليقُ بعد العلامة يبقى"
    why["reason"] = "الجهازُ غيرُ قابلٍ للوصول"
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    assert ov.build(vault, root=repo)["migrated"] == ["خطواتي.md"]
    migrated = steps.read_text(encoding="utf-8")
    assert "- ⏸ **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 — مؤجَّلة (ق٦٨ حتى 2026-10-19: الجهازُ غيرُ قابلٍ للوصول) (سألت المالك)" in migrated
    assert migrated.count("مؤجَّلة (") == 1 and "### سطورٌ نُقلت" not in migrated, "العلامةُ القديمة تُنزع ولا يُنقل السطرُ إلى الملاحظات"
    assert ov.build(vault, root=repo)["migrated"] == [] and steps.read_text(encoding="utf-8") == migrated


def test_a_lifted_deferral_and_a_step_gone_from_the_plan_migrate_without_losing_the_owner_s_text(repo, vault):
    """ملاحظتا Codex على #161 (الجولة السادسة): (١) حين يزول التأجيلُ عن خطوةٍ أو يتغيّر سببُه كانت علامةُ التأجيل القديمة تُحسب
    لاحقةً للمالك فتعود الخطوةُ النشطة تقول إنها مؤجَّلة؛ (٢) سطرٌ مرقَّم أضافه المالك بنفسه أو خطوةٌ حُذفت من الخطة وعليها تعليقُه
    كانا يُمحيان بصمت. صار ما تولّده الأداةُ من لواحق يُنزع قبل قراءة لاحقة المالك، وما ليس في الخطة يُنقل إلى قسم الملاحظات."""
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    why = {"by": "ق٦٨", "until": "2026-10-19", "reason": "الجهازُ غيرُ قابلٍ للوصول"}
    plan["owner_steps"] += [{"order": 2, "guide_id": "G5", "title": "تجهيز Nitro", "time": "ساعة", "cost": "$0", "deferred": why},
                            {"order": 3, "guide_id": "G4", "title": "صلاحيات Nitro", "time": "١٠ دقائق", "cost": "$0", "deferred": why}]
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    text = steps.read_text(encoding="utf-8")
    label = "مؤجَّلة (ق٦٨ حتى 2026-10-19: الجهازُ غيرُ قابلٍ للوصول)"
    assert f"- ⏸ **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 — {label}" in text
    text = (text.replace(f"- ⏸ **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 — {label}",
                         f"- [x] **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 — {label} — أُنجزت قبل التأجيل (بالرام الجديدة)")
                .replace(f"- ⏸ **3. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ١٠ دقائق · $0 — {label}",
                         f"- ⏸ **3. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ١٠ دقائق · $0 — {label}")
            + "- [ ] **9. خطوةٌ أضفتُها بنفسي** · بلا دليل · ساعة · $0\n")
    steps.write_text(text, encoding="utf-8")
    # يعود Nitro: يزول التأجيلُ عن ٢، ويتغيّر سببُه على ٣، وتُحذف الخطوةُ ١ من الخطة
    plan["owner_steps"] = [s for s in plan["owner_steps"] if s["order"] != 1]
    del plan["owner_steps"][0]["deferred"]
    plan["owner_steps"][1]["deferred"] = {"by": "ق٦٩", "until": "2026-11-01", "reason": "سببٌ آخر"}
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    assert ov.build(vault, root=repo)["migrated"] == ["خطواتي.md"]
    migrated = steps.read_text(encoding="utf-8")
    active, rest = migrated.split("## خطواتٌ مؤجَّلة")
    assert "- [x] **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 (بالرام الجديدة)" in active, "لا علامةَ تأجيلٍ قديمة ولا علامةَ إنجازٍ قبله"
    assert "مؤجَّلة (ق٦٨" not in active and "أُنجزت قبل التأجيل" not in active
    assert "- ⏸ **3. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ١٠ دقائق · $0 — مؤجَّلة (ق٦٩ حتى 2026-11-01: سببٌ آخر)" in rest
    assert "ق٦٨" not in rest.split("### سطورٌ نُقلت من النسخة السابقة")[0]
    carried = [line for line in rest.split("### سطورٌ نُقلت من النسخة السابقة")[1].splitlines() if line.strip()]
    assert carried == ["- [ ] **1. خطوة** · [[الأدلة/G1|G1]] · ٥ دقائق · $0", "- [ ] **9. خطوةٌ أضفتُها بنفسي** · بلا دليل · ساعة · $0"]
    assert ov.check(vault, root=repo) == [] and ov.build(vault, root=repo)["migrated"] == []
    assert steps.read_text(encoding="utf-8") == migrated


def test_a_legacy_checklist_s_free_lines_are_carried_into_the_notes_section_not_discarded(repo, vault):
    """ملاحظةُ Codex على #161 (الجولة الرابعة): النسخةُ القديمة من «خطواتي» دعت المالكَ إلى التعديل حيث شاء بلا قسم «## ملاحظاتي»،
    فالترحيلُ الذي لا يحفظ إلا ما تحت ذلك العنوان كان يمحو سطورَه الأخرى بصمت عند أول بناء. صارت تُنقل تحت عنوانٍ باسمها في قسم
    ملاحظاته، وتبقى العلامات، ولا يتكرّر النقلُ في البناء التالي."""
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    legacy = ["# خطواتي", "", "علّم الخطوة حين تنتهي. هذه الملاحظة لك: لا تكتب الأداةُ فوقها بعد إنشائها.", "",
              "- [x] **1. خطوة** · [[الأدلة/G1|G1]] · ٥ دقائق · $0", "  أنجزتها يوم الأحد مع المحامي.", "",
              "تذكير: اسأل عن الفاتورة قبل الخطوة التالية.", ""]
    steps.write_text("\n".join(legacy), encoding="utf-8")
    assert ov.check(vault, root=repo) == ["stale خطواتي.md"]
    assert ov.build(vault, root=repo)["migrated"] == ["خطواتي.md"]
    text = steps.read_text(encoding="utf-8")
    assert "- [x] **1. خطوة**" in text and "لا تكتب الأداةُ فوقها" not in text
    body, notes = text.split("\n## ملاحظاتي")
    assert "أنجزتها يوم الأحد" not in body and "تذكير" not in body
    carried = [line for line in notes.split("### سطورٌ نُقلت من النسخة السابقة")[1].splitlines() if line.strip()]
    assert carried == ["  أنجزتها يوم الأحد مع المحامي.", "تذكير: اسأل عن الفاتورة قبل الخطوة التالية."], "بنصّها وترتيبها ومسافاتها"
    assert ov.check(vault, root=repo) == [] and ov.build(vault, root=repo)["migrated"] == [], "لا يُعاد النقلُ في كل بناء"


def test_inbox_notes_survive_rebuilds_and_only_written_files_are_removed(repo, vault):
    ov.build(vault, root=repo)
    note = vault / "Inbox/طلب.md"
    note.write_text("# شغّل القياس الليلة\n", encoding="utf-8")
    mine = vault / "Diwan/ملاحظتي.md"
    mine.write_text("لي", encoding="utf-8")
    plan = {**PLAN, "tasks": PLAN["tasks"][:1], "phases": [{**PLAN["phases"][0], "task_ids": ["ك١"]}]}
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    report = ov.build(vault, root=repo)
    assert "المهام/جديد-x.md" in report["removed"]
    assert note.is_file() and mine.is_file()


def test_list_inbox_and_mark_done(repo, vault):
    ov.build(vault, root=repo)
    (vault / "Inbox/طلب.md").write_text("# شغّل القياس الليلة\nتفاصيل", encoding="utf-8")
    notes = ov.list_inbox(vault, root=repo)
    assert [n["title"] for n in notes] == ["شغّل القياس الليلة"]
    dest = ov.mark_done(vault, "Inbox/طلب.md", root=repo)
    assert dest.parent.name == "منجز" and ov.list_inbox(vault, root=repo) == []
    with pytest.raises(ov.VaultError):
        ov.mark_done(vault, "Diwan/لوحة المراحل.md", root=repo)
    with pytest.raises(ov.VaultError):
        ov.mark_done(vault, "Inbox/اقرأني.md", root=repo)


def test_check_reports_drift(repo, vault):
    ov.build(vault, root=repo)
    (repo / "docs/STATUS.md").write_text("# تغيّر\n", encoding="utf-8")
    assert ov.check(vault, root=repo) == ["stale الوثائق/STATUS.md"]


def test_the_real_repository_builds(tmp_path):
    report = ov.build(tmp_path / "vault", root=ROOT)
    assert report["written"] and (tmp_path / "vault/Diwan/لوحة المراحل.md").is_file()
    plan = json.loads((ROOT / ov.PLAN).read_text(encoding="utf-8"))
    assert len(list((tmp_path / "vault/Diwan/المهام").glob("*.md"))) == len(plan["tasks"])


def test_deferred_plan_entries_are_labelled_and_leave_the_active_checklist(repo, vault):
    """ملاحظةُ Codex على #161: بندٌ مؤجَّل في الخطة (`deferred` — ق٦٨: Nitro) كان يُعرض في الخزنة عملًا نشطًا: مهمّةً عاديةً على
    اللوحة، وخطوةً غيرَ معلَّمة في «خطواتي». الآن يُسمّى مؤجَّلًا في ملاحظته وعلى اللوحة، وخطوةُ المالك تنتقل إلى قسمها؛ والخطةُ
    الحقيقية تؤجّل ح٥ وع٣ ومعايرةَ lm-eval وخطوتَي المالك على Nitro بالحقل نفسِه."""
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    why = {"by": "ق٦٨", "until": "2026-10-19", "reason": "الجهازُ غيرُ قابلٍ للوصول"}
    plan["tasks"][1]["deferred"] = why
    plan["owner_steps"].append({"order": 2, "guide_id": "G5", "title": "تجهيز Nitro", "time": "ساعة", "cost": "$0", "deferred": why})
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    out = ov.render(root=repo)
    label = "مؤجَّلة (ق٦٨ حتى 2026-10-19: الجهازُ غيرُ قابلٍ للوصول)"
    note = out["المهام/جديد-x.md"]
    assert "status: deferred" in note.split("---")[1] and f"> ⏸ **{label}**" in note and "status: deferred" not in out["المهام/ك١.md"]
    board = out["لوحة المراحل.md"]
    assert f"- ⏸ [[المهام/جديد-x|جديد-x]] ثانية · " in board and label in board and "- [[المهام/ك١|ك١]] مهمّة" in board
    steps = out["خطواتي.md"]
    active, postponed = steps.split("## خطواتٌ مؤجَّلة")
    assert "- [ ] **1. خطوة**" in active and "**2. تجهيز Nitro**" not in active
    assert f"- ⏸ **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 — {label}" in postponed
    real = json.loads((ROOT / ov.PLAN).read_text(encoding="utf-8"))
    deferred_tasks = sorted(t["id"] for t in real["tasks"] if t.get("deferred"))
    assert deferred_tasks == sorted(t["id"] for t in real["tasks"] if t["title"].startswith(("G5: تجهيز Nitro", "المشغّل الزائل diwan-live على Nitro", "معايرة المُشغِّل على لوحة عامة")))
    deferred_steps = sorted(s["order"] for s in real["owner_steps"] if s.get("deferred"))
    assert len(deferred_tasks) == 3 and deferred_steps == [4, 24, 25], "خطواتُ المالك الثلاث التي لا تُنفَّذ إلا على Nitro"
    assert all(s["deferred"]["by"] == "ق٦٨" for s in real["owner_steps"] if s.get("deferred"))
    assert any(o["decision"] == "ق٦٨" for o in real.get("overrides", []))


def test_every_plan_task_is_on_the_board_of_the_phase_it_declares():
    """اللوحةُ تُبنى من phases[].task_ids وحدها: مهمّةٌ غائبةٌ عن قائمة مرحلتها تُكتب ملاحظتُها وتسقط من اللوحة صامتة
    (ملاحظة Codex على #141 في مهمّتَي ق٦٦ المقسومتين)."""
    plan = json.loads((ROOT / ov.PLAN).read_text(encoding="utf-8"))
    listed = {(tid, p["id"]) for p in plan["phases"] for tid in p["task_ids"]}
    assert [t["id"] for t in plan["tasks"] if (t["id"], t["phase"]) not in listed] == []


def test_a_written_file_the_owner_edited_is_not_removed_when_it_leaves_the_plan(repo, vault):
    ov.build(vault, root=repo)
    task = vault / "Diwan/المهام/جديد-x.md"
    task.write_text("ملاحظتي على المهمّة", encoding="utf-8")
    plan = {**PLAN, "tasks": PLAN["tasks"][:1], "phases": [{**PLAN["phases"][0], "task_ids": ["ك١"]}]}
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    report = ov.build(vault, root=repo)
    assert "المهام/جديد-x.md" not in report["removed"] and task.read_text(encoding="utf-8") == "ملاحظتي على المهمّة"


def test_mark_done_never_overwrites_an_earlier_completed_note(repo, vault):
    ov.build(vault, root=repo)
    for body in ("# الأول\n", "# الثاني\n"):
        (vault / "Inbox/طلب.md").write_text(body, encoding="utf-8")
        ov.mark_done(vault, "Inbox/طلب.md", root=repo)
    done = sorted(p.read_text(encoding="utf-8") for p in (vault / "Inbox/منجز").glob("*.md"))
    assert done == ["# الأول\n", "# الثاني\n"]


def test_check_reports_an_obsolete_managed_note(repo, vault):
    ov.build(vault, root=repo)
    plan = {**PLAN, "tasks": PLAN["tasks"][:1], "phases": [{**PLAN["phases"][0], "task_ids": ["ك١"]}]}
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    assert "obsolete المهام/جديد-x.md" in ov.check(vault, root=repo)
    ov.build(vault, root=repo)
    assert ov.check(vault, root=repo) == []
