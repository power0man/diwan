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


def test_a_completion_mark_follows_the_step_s_title_not_its_number(repo, vault):
    """ملاحظةُ Codex على #161 (الجولة الثامنة): حين تحذف الخطةُ خطوةً منجزة أو تعيد ترقيمها ويأخذ رقمَها خطوةٌ أخرى كانت علامةُ
    `[x]` تنتقل بالرقم فتظهر الخطوةُ الجديدة منجزةً وقد تُتخطّى. صارت العلامةُ تنتقل بهويّة الخطوة (رقمُها وعنوانُها) لا برقمها:
    الخطوةُ الأخرى بالرقم نفسِه تعود `[ ]` وسطرُ المنجزة القديم يُنقل إلى قسم الملاحظات، والخطوةُ نفسُها إذا تغيّر وقتُها تبقى منجزة."""
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    plan["owner_steps"].append({"order": 2, "guide_id": "G5", "title": "تجهيز Nitro", "time": "ساعة", "cost": "$0"})
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    old = "- [ ] **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0"
    steps.write_text(steps.read_text(encoding="utf-8").replace(old, old.replace("[ ]", "[x]")), encoding="utf-8")
    plan["owner_steps"][-1] = {"order": 2, "guide_id": "G4", "title": "صلاحيات Nitro", "time": "١٠ دقائق", "cost": "$0"}
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    assert ov.build(vault, root=repo)["migrated"] == ["خطواتي.md"]
    migrated = steps.read_text(encoding="utf-8")
    assert "- [ ] **2. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ١٠ دقائق · $0" in migrated, "خطوةٌ أخرى بالرقم نفسِه ليست منجزة"
    carried = [line for line in migrated.split("### سطورٌ نُقلت من النسخة السابقة")[1].splitlines() if line.strip()]
    assert carried == ["- [x] **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0"], "المنجزةُ القديمة لا تُمحى"
    # والخطوةُ نفسُها بوقتٍ آخر في الخطة تبقى منجزة، لأن هويّتها لم تتغيّر
    active = "- [ ] **2. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ١٠ دقائق · $0"
    steps.write_text(migrated.replace(active, active.replace("[ ]", "[x]")), encoding="utf-8")
    plan["owner_steps"][-1]["time"] = "٥ دقائق"
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    assert ov.build(vault, root=repo)["migrated"] == ["خطواتي.md"]
    again = steps.read_text(encoding="utf-8")
    assert "- [x] **2. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ٥ دقائق · $0" in again
    assert "١٠ دقائق" not in again, "سطرُها القديم كما كتبته الأداةُ لا شيءَ فيه للمالك غيرُ علامته، فلا يُنقل (الجولة الرابعة عشرة)"
    # أمّا إن عدّل المالكُ داخل النصّ الذي كتبته الأداة فيُنقل سطرُه كما هو ولا يُمحى
    edited = "- [x] **2. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ٥ دقائق (أخذت ساعة) · $0"
    steps.write_text(again.replace("- [x] **2. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ٥ دقائق · $0", edited), encoding="utf-8")
    assert ov.build(vault, root=repo)["migrated"] == ["خطواتي.md"]
    body, notes = steps.read_text(encoding="utf-8").split("\n## ملاحظاتي")
    assert "- [x] **2. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ٥ دقائق · $0\n" in body and edited in notes, "سطرُها المعدَّل يُنقل لا يُمحى"


def test_an_owner_line_that_reuses_a_generated_step_s_number_is_carried_whichever_side_it_sits(repo, vault):
    """ملاحظةُ Codex على #161 (الجولة التاسعة): سطرٌ مرقَّم كتبه المالك برقم خطوةٍ مولَّدة ووضعه قبلها كان يُكتب فوقه في قاموس
    السطور (الرقمُ مفتاحٌ واحد) فلا يُرى إلا المولَّد: لا يبقى سطرُه ولا يُنقل، بل يُمحى بصمت في البناء التالي. صارت سطورُ الرقم
    الواحد تُحفظ كلُّها: ما وافق الخطوةَ بهويّتها يبقى على سطرها بعلامته، وما سواه يُنقل إلى قسم الملاحظات — قبل المولَّد كان أم بعده."""
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    plan["owner_steps"].append({"order": 2, "guide_id": "G5", "title": "تجهيز Nitro", "time": "ساعة", "cost": "$0"})
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    text = steps.read_text(encoding="utf-8")
    gen = "- [ ] **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0"
    done, custom = gen.replace("[ ]", "[x]"), "- [ ] **2. مهمّتي: شراء قرص خارجي** · بلا دليل"
    assert gen in text
    for arrangement in (f"{custom}\n{done}", f"{done}\n{custom}"):
        steps.write_text(text.replace(gen, arrangement), encoding="utf-8")
        assert ov.build(vault, root=repo)["migrated"] == ["خطواتي.md"]
        migrated = steps.read_text(encoding="utf-8")
        active, notes = migrated.split("\n## ملاحظاتي")
        assert done in active and custom not in active, "المولَّدةُ تبقى بعلامتها وسطرُ المالك لا يبقى في القائمة النشطة"
        carried = [line for line in notes.split("### سطورٌ نُقلت من النسخة السابقة")[1].splitlines() if line.strip()]
        assert carried == [custom], "سطرُ المالك يُنقل بنصّه لا يُمحى، قبل المولَّد كان أم بعده"
        assert ov.check(vault, root=repo) == [] and ov.build(vault, root=repo)["migrated"] == []
        assert steps.read_text(encoding="utf-8") == migrated, "لا يتكرّر النقلُ في البناء التالي"


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
    steps.write_text(text.replace(f"- ⏸ **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 — {label}",
                                  f"- [x] **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 — {label} (بالرام الجديدة)"), encoding="utf-8")
    ov.build(vault, root=repo)                                  # الأداةُ تكتب علامةَ الإنجاز قبل التأجيل بنفسها
    text = steps.read_text(encoding="utf-8")
    assert f"- [x] **2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0 — {label} — أُنجزت قبل التأجيل (بالرام الجديدة)" in text
    steps.write_text(text + "- [ ] **9. خطوةٌ أضفتُها بنفسي** · بلا دليل · ساعة · $0\n", encoding="utf-8")
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


def test_an_owner_written_deferral_on_an_active_step_survives_and_the_tool_s_own_label_is_not_duplicated(repo, vault):
    """ملاحظةُ Codex على #161 (الجولة الثالثة عشرة): كانت كلُّ علامةٍ بلفظ « — مؤجَّلة (…)» تُنزع من سطر الخطوة أينما وقعت على أنها
    مولَّدة، فالمالكُ الذي أجّل خطوةً نشطة بيده («- [ ] **1. …** — مؤجَّلة (بقرار شخصي)») يُمحى نصُّه في البناء التالي. صارت العلامةُ
    تُنزع حيث ولّدتها الأداةُ وحدها: في أول اللاحقة، والسطرُ مؤجَّل (⏸) أو منجزٌ قبل التأجيل أو العلامةُ نصُّ ما تولّده الخطةُ الآن.
    والاتجاهُ الآخر: علامةُ الأداة تُعرف ولا تتكرّر — بعد أن يحوّل المالكُ ⏸ إلى [x] بيده، وبعد تغيّر السبب، وبعد رفع التأجيل."""
    why = {"by": "ق٦٨", "until": "2026-10-19", "reason": "الجهازُ غيرُ قابلٍ للوصول (Nitro)"}
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    plan["owner_steps"] += [{"order": 2, "guide_id": "G5", "title": "تجهيز Nitro", "time": "ساعة", "cost": "$0", "deferred": why},
                            {"order": 3, "guide_id": "G4", "title": "صلاحيات Nitro", "time": "١٠ دقائق", "cost": "$0", "deferred": why}]
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    label = lambda d: f" — مؤجَّلة ({d['by']} حتى {d['until']}: {d['reason']})"  # noqa: E731
    one = "- [ ] **1. خطوة** · [[الأدلة/G1|G1]] · ٥ دقائق · $0"
    two = "**2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0"
    three = "**3. صلاحيات Nitro** · [[الأدلة/G4|G4]] · ١٠ دقائق · $0"
    text = steps.read_text(encoding="utf-8")
    assert f"- ⏸ {two}{label(why)}\n" in text and f"- ⏸ {three}{label(why)}\n" in text
    mine = one + " — مؤجَّلة (بقرار شخصي)"
    text = (text.replace(one + "\n", mine + "\n")
            .replace(f"- ⏸ {two}{label(why)}\n", f"- [x] {two}{label(why)} — تمّ\n")          # حوّل ⏸ إلى [x] بيده
            .replace(f"- ⏸ {three}{label(why)}\n", f"- ⏸ {three}{label(why)} — مؤجَّلة (وأنا أيضًا)\n"))
    steps.write_text(text, encoding="utf-8")
    ov.build(vault, root=repo)
    text = steps.read_text(encoding="utf-8")
    assert mine + "\n" in text, "تأجيلُ المالك بيده على خطوةٍ نشطة يبقى بايتًا بايتًا"
    assert f"- [x] {two}{label(why)}{ov.DONE_BEFORE_DEFERRAL} — تمّ\n" in text and text.count("مؤجَّلة (ق٦٨") == 2
    assert f"- ⏸ {three}{label(why)} — مؤجَّلة (وأنا أيضًا)\n" in text
    assert ov.build(vault, root=repo)["migrated"] == [] and ov.check(vault, root=repo) == [], "ولا يتغيّر في البناء التالي"
    # تغيّر السبب: علامةُ الأداة تُستبدل مرّةً واحدة، ونصُّ المالك في مكانه
    new_why = {"by": "ق٦٩", "until": "2026-11-01", "reason": "سببٌ آخر"}
    for step in plan["owner_steps"][1:]:
        step["deferred"] = new_why
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    text = steps.read_text(encoding="utf-8")
    assert mine + "\n" in text and "ق٦٨" not in text
    assert f"- [x] {two}{label(new_why)}{ov.DONE_BEFORE_DEFERRAL} — تمّ\n" in text
    assert f"- ⏸ {three}{label(new_why)} — مؤجَّلة (وأنا أيضًا)\n" in text
    # رُفع التأجيل: تزول علامةُ الأداة، ويبقى ما كتبه المالك — ولو كان بلفظ التأجيل
    for step in plan["owner_steps"][1:]:
        del step["deferred"]
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    text = steps.read_text(encoding="utf-8")
    assert mine + "\n" in text and f"- [x] {two} — تمّ\n" in text and f"- [ ] {three} — مؤجَّلة (وأنا أيضًا)\n" in text
    assert "ق٦٩" not in text and ov.DONE_BEFORE_DEFERRAL not in text


ONE = "**1. خطوة** · [[الأدلة/G1|G1]] · ٥ دقائق · $0"
TWO = "**2. تجهيز Nitro** · [[الأدلة/G5|G5]] · ساعة · $0"
PAUSED = f"- ⏸ {ONE} — مؤجَّلة (بقرار شخصي)"


def _label(d):
    return f" — مؤجَّلة ({d['by']} حتى {d['until']}: {d['reason']})"


def _paused_vault(repo, vault, why):
    """خطةٌ فيها خطوتان: ١ نشطة أوقفها المالكُ بيده بعلامته ونصّه، و٢ أجّلتها الخطةُ وكتب المالكُ بعد علامة الأداة تعليقَه."""
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    plan["owner_steps"].append({"order": 2, "guide_id": "G5", "title": "تجهيز Nitro", "time": "ساعة", "cost": "$0", "deferred": why})
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    text = steps.read_text(encoding="utf-8")
    assert f"- [ ] {ONE}\n" in text and f"- ⏸ {TWO}{_label(why)}\n" in text
    steps.write_text(text.replace(f"- [ ] {ONE}\n", PAUSED + "\n")
                     .replace(f"- ⏸ {TWO}{_label(why)}\n", f"- ⏸ {TWO}{_label(why)} (سألت المورّد)\n"), encoding="utf-8")
    return plan, steps


def test_an_active_step_the_owner_paused_by_hand_keeps_its_pause_and_text_through_every_rebuild(repo, vault):
    """ملاحظةُ Codex على #161 (الجولة الرابعة عشرة): المالكُ أوقف خطوةً نشطة بيده («- ⏸ **1. …** — مؤجَّلة (بقرار شخصي)») فكان البناءُ
    يحسب «⏸» علامةَ الأداة لأنه شكلُها، فيعيد «[ ]» ويمحو نصَّه. صار المصدرُ سجلَّ ما كتبته الأداةُ نفسُها (`.diwan-steps.json`) لا
    شكلَ السطر: ما غيّره المالكُ عمّا كتبته الأداةُ له. فتبقى وقفتُه ونصُّه بايتًا بايتًا عبر إعادة البناء، وتأجيلِ الخطة للخطوة نفسِها،
    وتغيّرِ السبب، ورفعِ التأجيل؛ ويُحترم رفعُه لها بيده. وعلامةُ الأداة على الخطوة المؤجَّلة بالخطة تُعرف ولا تتكرّر وتزول برفع
    التأجيل، ولو تبعها نصُّ المالك."""
    why = {"by": "ق٦٨", "until": "2026-10-19", "reason": "الجهازُ غيرُ قابلٍ للوصول (Nitro)"}
    plan, steps = _paused_vault(repo, vault, why)
    ov.build(vault, root=repo)
    text = steps.read_text(encoding="utf-8")
    assert PAUSED + "\n" in text, "وقفةُ المالك بيده تبقى ولا تعود «[ ]»"
    assert f"- ⏸ {TWO}{_label(why)} (سألت المورّد)\n" in text and text.count("مؤجَّلة (ق٦٨") == 1
    assert ov.build(vault, root=repo)["migrated"] == [] and ov.check(vault, root=repo) == [] and steps.read_text(encoding="utf-8") == text
    # الخطةُ تؤجّل الخطوةَ ١ نفسَها، ثم يتغيّر السبب، ثم يُرفع التأجيل: وقفةُ المالك ونصُّه لا يزولان، وعلامةُ الأداة وحدها تتبدّل
    new_why = {"by": "ق٦٩", "until": "2026-11-01", "reason": "سببٌ آخر"}
    for why_now in (why, new_why, None):
        for step in plan["owner_steps"]:
            step.pop("deferred", None)
            if why_now:
                step["deferred"] = why_now
        (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        ov.build(vault, root=repo)
        text = steps.read_text(encoding="utf-8")
        tool = _label(why_now) if why_now else ""
        assert f"- ⏸ {ONE}{tool} — مؤجَّلة (بقرار شخصي)\n" in text, why_now
        assert f"- {'⏸' if why_now else '[ ]'} {TWO}{tool} (سألت المورّد)\n" in text, why_now
        assert text.count(" — مؤجَّلة (ق") == (2 if why_now else 0), "علامةُ الأداة مرّةً واحدة لكل خطوةٍ مؤجَّلة، وتزول برفع التأجيل"
        assert ov.build(vault, root=repo)["migrated"] == [] and steps.read_text(encoding="utf-8") == text, "والبناءُ التالي لا يغيّر شيئًا"
    # ويرفع المالكُ وقفتَه بيده: يُحترم، ونصُّه يبقى حتى يمحوه هو
    steps.write_text(text.replace(PAUSED, f"- [ ] {ONE} — مؤجَّلة (بقرار شخصي)"), encoding="utf-8")
    ov.build(vault, root=repo)
    assert f"- [ ] {ONE} — مؤجَّلة (بقرار شخصي)\n" in steps.read_text(encoding="utf-8")
    assert ov.build(vault, root=repo)["migrated"] == []


@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong_schema"])
def test_a_missing_or_corrupt_steps_record_never_deletes_the_owner_s_text(repo, vault, damage):
    """من الجولة الرابعة عشرة: سجلُّ ما كتبته الأداةُ مفقودٌ (خزنةٌ من قبله، أو حُذف) أو تالف أو بغير صيغته: لا يسقط البناء ولا يُمحى
    شيءٌ للمالك. القاعدةُ المحافِظة: لا يُنزع إلا ما تولّده الخطةُ الآن بنصّه — علامةُ الخطوة المؤجَّلة الحالية — ووقفةُ المالك على
    خطوةٍ نشطة ونصُّه باقيان؛ ثم يُكتب سجلٌّ سليم فيعود التمييزُ بالمصدر."""
    why = {"by": "ق٦٨", "until": "2026-10-19", "reason": "الجهازُ غيرُ قابلٍ للوصول (Nitro)"}
    plan, steps = _paused_vault(repo, vault, why)
    state = vault / "Diwan" / ov.STEPS_STATE
    assert json.loads(state.read_text(encoding="utf-8"))["schema_version"] == 1
    if damage == "missing":
        state.unlink()
    else:
        state.write_text("{لا json" if damage == "corrupt" else '{"schema_version": 1, "steps": {"1. خطوة": {"mark": "[ ]", "text": 5}}}',
                         encoding="utf-8")
    ov.build(vault, root=repo)
    text = steps.read_text(encoding="utf-8")
    assert PAUSED + "\n" in text, "وقفةُ المالك ونصُّه يبقيان بلا سجلّ"
    assert f"- ⏸ {TWO}{_label(why)} (سألت المورّد)\n" in text and text.count("مؤجَّلة (ق٦٨") == 1, "علامةُ الأداة الحالية وحدها تُنزع ولا تتكرّر"
    assert json.loads(state.read_text(encoding="utf-8"))["steps"]["1. خطوة"]["override"] == "⏸", "ثم يُكتب سجلٌّ سليم"
    assert ov.build(vault, root=repo)["migrated"] == [] and steps.read_text(encoding="utf-8") == text
    del plan["owner_steps"][-1]["deferred"]
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    text = steps.read_text(encoding="utf-8")
    assert PAUSED + "\n" in text and f"- [ ] {TWO} (سألت المورّد)\n" in text and "ق٦٨" not in text


@pytest.mark.parametrize("unchecked", ["[ ]", "⏸"], ids=["blank", "paused"])
@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong_schema"])
def test_an_uncheck_after_a_completed_deferral_leaves_no_stale_completion_text_without_a_record(repo, vault, damage, unchecked):
    """ملاحظةُ Codex على #161 (الجولة الخامسة عشرة): بلا سجلّ (مفقود أو تالف أو بغير صيغته) كانت «— أُنجزت قبل التأجيل» التي كتبتها
    الأداةُ على خطوةٍ منجزةٍ مؤجَّلة لا تُنزع إلا إن بقيت منجزة؛ فالمالكُ الذي رفع «[x]» بيده («[ ]» أو «⏸») يعود سطرُه «⏸» وهو يقول
    إنها أُنجزت. صارت تُنزع متى تلت علامةَ التأجيل الحالية بنصّها مباشرةً، أيًّا كانت العلامةُ الآن، ويُعاد حسابُ الإنجاز من العلامة
    الحالية وحدها؛ ونصُّ المالك بعدها يبقى."""
    why = {"by": "ق٦٨", "until": "2026-10-19", "reason": "الجهازُ غيرُ قابلٍ للوصول (Nitro)"}
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    plan["owner_steps"].append({"order": 2, "guide_id": "G5", "title": "تجهيز Nitro", "time": "ساعة", "cost": "$0", "deferred": why})
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    paused = f"- ⏸ {TWO}{_label(why)}"
    steps.write_text(steps.read_text(encoding="utf-8").replace(paused + "\n", f"- [x] {TWO}{_label(why)}\n"), encoding="utf-8")
    ov.build(vault, root=repo)                                  # الأداةُ تكتب «أُنجزت قبل التأجيل» بنفسها
    done = f"- [x] {TWO}{_label(why)}{ov.DONE_BEFORE_DEFERRAL}"
    assert done + "\n" in steps.read_text(encoding="utf-8")
    # ثم يرفع المالكُ علامتَه بيده ويكتب بعدها، ويضيع السجلّ
    steps.write_text(steps.read_text(encoding="utf-8").replace(
        done + "\n", f"- {unchecked} {TWO}{_label(why)}{ov.DONE_BEFORE_DEFERRAL} (لم أُكملها بعد)\n"), encoding="utf-8")
    state = vault / "Diwan" / ov.STEPS_STATE
    if damage == "missing":
        state.unlink()
    else:
        state.write_text("{لا json" if damage == "corrupt" else '{"schema_version": 1, "steps": {"2. تجهيز Nitro": {"mark": "⏸"}}}',
                         encoding="utf-8")
    ov.build(vault, root=repo)
    text = steps.read_text(encoding="utf-8")
    assert f"{paused} (لم أُكملها بعد)\n" in text, "لا «أُنجزت قبل التأجيل» على خطوةٍ رفع المالكُ علامتَها، ونصُّه باقٍ"
    assert ov.DONE_BEFORE_DEFERRAL not in text and "[x]" not in text
    assert ov.build(vault, root=repo)["migrated"] == [] and steps.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("instruction", [ov.LEGACY_INSTRUCTION, ov.INSTRUCTION], ids=["legacy", "current"])
def test_an_annotation_appended_to_the_instruction_line_is_carried_not_deleted(repo, vault, instruction):
    """ملاحظةُ Codex على #161 (الجولة الحادية عشرة): سطرُ التعليمات كان يُعرف ببدايته «علّم الخطوة حين تنتهي»، فتعليقٌ ألحقه المالكُ
    به — والنسخةُ القديمة دعته إلى التعديل حيث شاء بلا قسم ملاحظات — يُعدّ مولَّدًا كلُّه ويُمحى بصمت في أول بناء. صار السطرُ يُعرف
    بنصّه التامّ: المعدَّلُ يُنقل كما هو إلى قسم ملاحظات المالك، والتعليماتُ الحالية تُكتب مكانه، والسطرُ غيرُ المعدَّل لا يُنقل."""
    assert ov.build(vault, root=repo)["written"] and ov.check(vault, root=repo) == [], "خزنةٌ مبنيّةٌ للتوّ لا تنجرف"
    steps = vault / "Diwan/خطواتي.md"
    edited = f"{instruction} — اتصلتُ بالفريق يوم الأحد"
    steps.write_text("\n".join(["# خطواتي", "", edited, "", "- [x] **1. خطوة** · [[الأدلة/G1|G1]] · ٥ دقائق · $0", ""]), encoding="utf-8")
    assert ov.build(vault, root=repo)["migrated"] == ["خطواتي.md"]
    text = steps.read_text(encoding="utf-8")
    assert "اتصلتُ بالفريق" in text, "تعليقُ المالك على سطر التعليمات يُنقل ولا يُمحى"
    body, notes = text.split("\n## ملاحظاتي")
    assert f"\n{ov.INSTRUCTION}\n" in body and "اتصلتُ بالفريق" not in body and "- [x] **1. خطوة**" in body
    carried = [line for line in notes.split(ov.LEGACY_HEADING)[1].splitlines() if line.strip()]
    assert carried == [edited], "تعليقُ المالك يبقى بسطره كاملًا"
    assert ov.check(vault, root=repo) == [] and ov.build(vault, root=repo)["migrated"] == [], "لا يُعاد النقلُ في كل بناء"
    # والسطرُ كما ولّدته الأداةُ (القديمُ أو الحالي) لا يُنقل
    steps.write_text("\n".join(["# خطواتي", "", instruction, "", "- [x] **1. خطوة** · [[الأدلة/G1|G1]] · ٥ دقائق · $0", ""]), encoding="utf-8")
    ov.build(vault, root=repo)
    rebuilt = steps.read_text(encoding="utf-8")
    assert ov.LEGACY_HEADING not in rebuilt and "\n" + ov.NOTES_HEADING not in rebuilt and rebuilt.count("علّم الخطوة") == 1


@pytest.mark.parametrize("force", [False, True], ids=["plain", "force"])
def test_a_symlinked_note_is_refused_by_name_and_what_it_points_to_is_untouched(repo, vault, tmp_path, force):
    """ملاحظةُ Codex على #161 (الجولة الثانية عشرة): «خطواتي» مربوطةٌ برابطٍ رمزيّ إلى مجلّد ملاحظاتٍ آخر كان الترحيلُ يكتب عبره
    فيستبدل ملاحظةَ المالك الخارجية ولو بلا `--force`، والقالبُ القديم لم يكن يمسّ البذرةَ القائمة. صار كلُّ مسارٍ ستمسّه الأداةُ
    يُفحص قبل أيّ كتابة: الرابطُ يُرفض باسمه (`symlink_refused`) نسبيًّا إلى الخزنة، ولا يُكتب شيءٌ في البناء، ويبقى ما يشير إليه
    بايتًا بايتًا؛ وكذلك ملاحظةٌ مولَّدة مربوطة، ومجلّدُ المهامّ مربوطًا، والبيانُ مربوطًا، والمهمّةُ المتقادمة مربوطةً قبل حذفها."""
    ov.build(vault, root=repo)
    out = vault / "Diwan"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    plan["owner_steps"].append({"order": 2, "guide_id": "G1", "title": "أخرى", "time": "٥ دقائق", "cost": "$0"})
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")   # البناءُ التالي يكتب ما تغيّر
    board_before = (out / "لوحة المراحل.md").read_bytes()

    def refused(rel, target, content="# ملاحظتي خارج الخزنة\n- [x] **1. خطوة** · أنجزتها\n".encode("utf-8")):
        """يستبدل `rel` برابطٍ إلى `target` (والمجلّدُ يُنقل إليه بما فيه)، ويتحقّق الرفضَ باسمه وبقاءَ الهدف والخزنة كما هما، ثم يعيده."""
        path = out / rel
        saved = None if path.is_dir() else path.read_bytes()
        if saved is None:
            path.rename(target)
        else:
            path.unlink()
            target.write_bytes(content)
        path.symlink_to(target)
        snapshot = sorted((p.relative_to(elsewhere).as_posix(), p.read_bytes() if p.is_file() else b"") for p in elsewhere.rglob("*"))
        with pytest.raises(ov.VaultError) as e:
            ov.build(vault, root=repo, force=force)
        assert (e.value.code, str(e.value)) == ("symlink_refused", f"symlink_refused: Diwan/{rel}")
        with pytest.raises(ov.VaultError):
            ov.check(vault, root=repo)
        assert sorted((p.relative_to(elsewhere).as_posix(), p.read_bytes() if p.is_file() else b"") for p in elsewhere.rglob("*")) == snapshot
        assert path.is_symlink() and (out / "لوحة المراحل.md").read_bytes() == board_before, "لا يُكتب شيءٌ في بناءٍ مرفوض"
        path.unlink()
        if saved is None:
            target.rename(path)
        else:
            path.write_bytes(saved)

    refused("خطواتي.md", elsewhere / "قائمتي.md")
    refused("المهام/ك١.md", elsewhere / "ك١.md")
    refused("المهام", elsewhere / "مهام", content=None)
    refused(".diwan-mirror.json", elsewhere / "بيان.json", content=b'{"files": {}}')
    refused(".diwan-steps.json", elsewhere / "سجل.json", content=b'{"schema_version": 1, "steps": {}}')
    # مهمّةٌ متقادمة (حُذفت من الخطة) مربوطةٌ بملفٍّ بنصّها المسجَّل: لا يُحذف الرابطُ ولا يُقرأ الهدف
    plan["tasks"] = plan["tasks"][:1]
    plan["phases"][0]["task_ids"] = ["ك١"]
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    refused("المهام/جديد-x.md", elsewhere / "جديد-x.md", content=(out / "المهام/جديد-x.md").read_bytes())
    assert ov.build(vault, root=repo, force=force)["removed"] == ["المهام/جديد-x.md"], "بعد إزالة الرابط يعود البناءُ كما كان"


def test_a_non_regular_file_in_place_of_a_note_is_refused_and_a_readme_link_is_left_alone(repo, vault, tmp_path):
    """من الجولة الثانية عشرة أيضًا: مجلّدٌ مكانَ «خطواتي» يُرفض باسمه (`not_a_regular_file`) قبل أيّ كتابة لا بخطأ قراءةٍ خام؛
    و«اقرأني» في صندوق الوارد إن كانت رابطًا — ولو معلَّقًا — تُترك: لا يُنشأ الملفُّ الذي يشير إليه."""
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    steps.unlink()
    steps.mkdir()
    with pytest.raises(ov.VaultError) as e:
        ov.build(vault, root=repo)
    assert (e.value.code, str(e.value)) == ("not_a_regular_file", "not_a_regular_file: Diwan/خطواتي.md")
    steps.rmdir()
    readme = vault / "Inbox/اقرأني.md"
    readme.unlink()
    readme.symlink_to(tmp_path / "لم-يوجد.md")
    ov.build(vault, root=repo)
    assert readme.is_symlink() and not (tmp_path / "لم-يوجد.md").exists() and steps.is_file()


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


def test_a_deferred_role_reaches_its_own_tasks_and_the_mixed_tasks_that_name_it(repo, vault):
    """ملاحظةُ Codex على #161 (الجولة العاشرة): الخطةُ تؤجّل دورَ `claude-nitro` كلَّه (`agents[].deferred`، ق٦٨)، لكن البناء كان
    لا يقرأ إلا حقلَ `deferred` في المهمّة نفسِها: فمهمّةٌ مسنَدةٌ إلى الدور بلا حقلٍ باسمها (غ١١) تُعرض نشطة، ومهمّةٌ نشطة لغيره
    تأمر الدورَ المؤجَّل («وclaude-nitro يركّبه على ويندوز (`winget …`)» في جديد-obsidian-setup) تُعرض عليه عملًا حاليًّا. صارت
    الأولى تَرِث تأجيلَ دورها، والثانيةُ تحمل في ملاحظتها وعلى اللوحة أن دورَه فيها مؤجَّل وباقيها نشط؛ ويزول ذلك بعودة الدور."""
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    plan["agents"] = [{"name": "claude-nitro", "deferred": {"by": "ق٦٨", "until": "2026-10-19", "reason": "Nitro غيرُ قابلٍ للوصول"}},
                      {"name": "claude-mac"}]
    plan["tasks"][0].update(assignee="claude-mac",
                            description="١) claude-mac يركّب Obsidian وclaude-nitro يركّبه على ويندوز (`winget install -e --id Obsidian.Obsidian`).")
    plan["tasks"][1]["assignee"] = "claude-nitro"
    plan["tasks"].append({"id": "جديد-y", "title": "ثالثة", "assignee": "claude-mac", "block": "ب٩", "phase": "م٠", "effort": "S",
                          "depends_on": [], "description": "claude-nitro-2 عقدةٌ أخرى لم تُؤجَّل", "deliverable": "مخرج", "acceptance_evidence": "دليل"})
    plan["phases"][0]["task_ids"].append("جديد-y")
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    out = vault / "Diwan"
    read = lambda rel: (out / rel).read_text(encoding="utf-8")  # noqa: E731
    mixed = read("المهام/ك١.md")
    assert "deferred_roles: claude-nitro" in mixed.split("---")[1] and "status: deferred" not in mixed
    assert ("> ⏸ **دورُ Claude على Nitro (`claude-nitro`) في هذه المهمّة مؤجَّل (ق٦٨ حتى 2026-10-19: Nitro غيرُ قابلٍ للوصول)؛ "
            "فما نُسب إليه فيها لا يُنفَّذ الآن، وباقيها نشط**") in mixed, "الحرفُ العربيُّ المتّصل «وclaude-nitro» لا يُخفي الدور"
    own = read("المهام/جديد-x.md")
    assert "status: deferred" in own.split("---")[1] and "> ⏸ **مؤجَّلة (ق٦٨ حتى 2026-10-19: Nitro غيرُ قابلٍ للوصول)**" in own
    assert "⏸" not in read("المهام/جديد-y.md") and "deferred_roles" not in read("المهام/جديد-y.md"), "claude-nitro-2 ليس claude-nitro"
    board = read("لوحة المراحل.md")
    assert "- [[المهام/ك١|ك١]] مهمّة · Claude على الماك — ⏸ دورُ Claude على Nitro فيها مؤجَّل (ق٦٨)\n" in board
    assert "- ⏸ [[المهام/جديد-x|جديد-x]] ثانية · Claude على Nitro — **مؤجَّلة (ق٦٨ حتى 2026-10-19: Nitro غيرُ قابلٍ للوصول)**\n" in board
    assert "- [[المهام/جديد-y|جديد-y]] ثالثة · Claude على الماك\n" in board
    # يعود الدور بإشعار المالك: تزول العلاماتُ في البناء التالي
    del plan["agents"][0]["deferred"]
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    assert all("⏸" not in read(rel) for rel in ("المهام/ك١.md", "المهام/جديد-x.md", "لوحة المراحل.md"))
    # والخطةُ الحقيقية: غ١١ لـclaude-nitro مؤجَّلةٌ بدوره، وجديد-obsidian-setup (الماك) تحمل أن دورَ Nitro فيها مؤجَّل
    real = ov.render(root=ROOT)
    assert "status: deferred" in real["المهام/غ١١.md"].split("---")[1]
    assert "deferred_roles: claude-nitro" in real["المهام/جديد-obsidian-setup.md"].split("---")[1]


def test_a_deferred_owner_step_marks_the_active_task_it_unblocks_and_a_deferred_dependency_is_named(repo, vault):
    """من صنف ملاحظة Codex العاشرة على #161 (عملُ Nitro في مهمّةٍ مختلطة يُعرض حاليًّا): ح٢ «فكّ حجب جلستَي الماك وNitro» مسنَدةٌ
    إلى المالك ولا تسمّي `claude-nitro`، وشطرُ Nitro منها خطوةُ المالك ٤ (G4 ١٧–١٨) المؤجَّلةُ بق٦٨ التي تفتحها، فكانت ملاحظتُها
    تعرضه عملًا حاليًّا؛ وجديد-v1-acceptance تعتمد على ع٣ المؤجَّلة وسطرُ اعتمادها لا يقول ذلك. صارت المهمّةُ النشطة التي تفتحها
    خطوةٌ مؤجَّلة تحمل ذلك في رأسها وملاحظتها وعلى اللوحة، والمهمّةُ المؤجَّلة تُسمّى مؤجَّلةً في سطرَي «يعتمد على» و«يفتح»؛ ويزول
    ذلك كلُّه بعودة الخطوة والمهمّة."""
    plan = json.loads((repo / ov.PLAN).read_text(encoding="utf-8"))
    why = {"by": "ق٦٨", "until": "2026-10-19", "reason": "الجهازُ غيرُ قابلٍ للوصول"}
    plan["tasks"][1]["deferred"] = why
    plan["tasks"].append({"id": "جديد-z", "title": "ثالثة", "assignee": "claude-mac", "block": "ب٩", "phase": "م٠", "effort": "S",
                          "depends_on": ["ك١", "جديد-x"], "description": "وصف", "deliverable": "مخرج", "acceptance_evidence": "دليل"})
    plan["phases"][0]["task_ids"].append("جديد-z")
    plan["owner_steps"] += [{"order": 2, "guide_id": "G4", "title": "صلاحيات Nitro", "time": "١٠ دقائق", "cost": "$0",
                             "unblocks": ["ك١", "جديد-x"], "deferred": why},
                            {"order": 3, "guide_id": "G4", "title": "صلاحيات الماك", "time": "١٠ دقائق", "cost": "$0", "unblocks": ["ك١", "جديد-z"]}]
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    out = vault / "Diwan"
    read = lambda rel: (out / rel).read_text(encoding="utf-8")  # noqa: E731
    held = read("المهام/ك١.md")
    assert "deferred_steps: 2\n" in held.split("---")[1] and "status: deferred" not in held
    assert ("> ⏸ **خطوةُ المالك 2 «صلاحيات Nitro» التي تفتح هذه المهمّة مؤجَّلة (ق٦٨ حتى 2026-10-19: الجهازُ غيرُ قابلٍ للوصول)؛ "
            "فما يتوقّف عليها منها لا يُنتظر الآن، وباقيها نشط**") in held and "خطوةُ المالك 3" not in held
    assert "**يفتح:** [[المهام/جديد-x|جديد-x]] (⏸ مؤجَّلة)\n" in held
    own = read("المهام/جديد-x.md")
    assert "status: deferred" in own.split("---")[1] and "deferred_steps" not in own, "المؤجَّلةُ كلُّها لا تُوسم بخطوتها"
    other = read("المهام/جديد-z.md")
    assert "deferred_steps" not in other and "خطوةُ المالك" not in other, "خطوةٌ مؤجَّلة لا تفتح جديد-z"
    assert "**يعتمد على:** [[المهام/ك١|ك١]]، [[المهام/جديد-x|جديد-x]] (⏸ مؤجَّلة)\n" in other
    board = read("لوحة المراحل.md")
    assert "- [[المهام/ك١|ك١]] مهمّة · Claude السحابي — ⏸ خطوةُ المالك 2 لها مؤجَّلة (ق٦٨)\n" in board
    assert "- [[المهام/جديد-z|جديد-z]] ثالثة · Claude على الماك\n" in board
    # تعود الخطوةُ والمهمّة بإشعار المالك: تزول العلاماتُ في البناء التالي
    del plan["tasks"][1]["deferred"], plan["owner_steps"][1]["deferred"]
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ov.build(vault, root=repo)
    assert all("⏸" not in read(rel) and "deferred_" not in read(rel) for rel in ("المهام/ك١.md", "المهام/جديد-z.md", "لوحة المراحل.md"))
    # والخطةُ الحقيقية: ح٢ تفتحها خطوةُ المالك ٤ المؤجَّلة، وجديد-v1-acceptance تعتمد على ع٣ المؤجَّلة
    real = ov.render(root=ROOT)
    assert "deferred_steps: 4\n" in real["المهام/ح٢.md"].split("---")[1]
    assert "[[المهام/ع٣|ع٣]] (⏸ مؤجَّلة)" in real["المهام/جديد-v1-acceptance.md"]


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
