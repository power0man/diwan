#!/usr/bin/env python3
"""خزنةُ Obsidian للمالك: مرآةُ الوثائق الحاكمة، ولوحةُ المهامّ، وصندوقُ الوارد (جديد-obsidian-vault، ق٦٤).

المالكُ طلب في ٢٦ سبتمبر ٢٠٢٦ إضافةَ Obsidian «وتوظيفه للعمل». فالخزنةُ غرفةُ تحكّمه:

    Diwan/            ما تكتبه هذه الأداة: مرآةٌ للقراءة من الوثائق الحاكمة والأدلّة (G1–G10)،
                      ولوحةُ المراحل، وملاحظةٌ لكل مهمّة، وقائمةُ خطوات المالك بمربّعات اختيار.
    Inbox/            ما يكتبه المالك: طلباتُه لـClaude. الأداةُ لا تكتب فيه إلا ملاحظةَ «اقرأني»
                      إن غابت، ولا تحذف منه شيئًا؛ و`mark-done` ينقل الملاحظةَ المعالَجة إلى `Inbox/منجز/`.

    python tools/obsidian_vault.py build --vault ~/Diwan-Vault
    python tools/obsidian_vault.py check --vault ~/Diwan-Vault
    python tools/obsidian_vault.py list-inbox --vault ~/Diwan-Vault
    python tools/obsidian_vault.py mark-done --vault ~/Diwan-Vault "Inbox/طلب.md"

الحرّاس (مُثبَتةٌ بالطفرة في `tests/test_obsidian_vault.py`):
- الخزنةُ خارج المستودع دائمًا (`vault_inside_repository`)، فلا تُدفع ملاحظاتُ المالك إلى المستودع العام.
- لا خزنةَ ولا مصدرَ في مسارٍ فيه `sealed` بأيّ حالة أحرف (`sealed_path_refused`): المحجوبُ لا يُقرأ (AGENTS §٤).
- لا يُنسخ إلا ما في القائمة المسمّاة `MIRROR_FILES` والأدلّةُ `docs/guides/G*.md`؛ لا مجلّدات ولا أنماطٌ عامة.
- لا يُكتب فوق ملفٍّ عدّله المالك في `Diwan/` (بصمتُه تخالف البيان) إلا بـ`--force`، ولا يُحذف إلا ما سجّله
  البيانُ ملفًّا كتبته الأداة ولم يتغيّر.
- «اقرأني» في صندوق الوارد تُكتب مرّةً إن غابت ثم هي ملكُ المالك. أمّا «خطواتي» فتُعاد كتابتُها من الخطة عند كل بناء
  **مع الحفاظ على علامات المالك** (`[x]` تبقى، وما أُنجز ثم أُجّل يبقى منجزًا) وعلى ما كتبه تحت «## ملاحظاتي»؛ فتأجيلٌ جديد
  في الخطة (ق٦٨) يبلغ خزنةً قائمة لا الخزنةَ الجديدة وحدها (ملاحظة Codex على #161). والتمييزُ بين نصّ الأداة ونصّ المالك من سجلّ
  ما كتبته الأداةُ نفسُها لكل خطوة (`Diwan/.diwan-steps.json`، يُكتب دفعةً واحدة) لا من شكل السطر: «⏸» أو «— مؤجَّلة (…)» كتبهما
  المالكُ بيده يبقيان له (ملاحظتا Codex الثالثة عشرة والرابعة عشرة). وبلا سجلٍّ (خزنةٌ من قبله، أو فُقد أو تلف) لا يُنزع إلا ما تولّده
  الخطةُ الآن بنصّه.
- لا يُكتب ولا يُقرأ ولا يُحذف عبر رابطٍ رمزيّ في `Diwan/` (`symlink_refused`)، ولا في غير ملفٍّ عاديّ (`not_a_regular_file`):
  يُفحص كلُّ مسارٍ ستمسّه الأداةُ قبل أيّ كتابة، فرابطٌ وضعه المالكُ (خطواتي مربوطةٌ بمجلّد ملاحظاتٍ آخر) يُرفض باسمه ولا يُكتب
  فوق ما يشير إليه ولو بـ`--force` (ملاحظة Codex الثانية عشرة على #161). و«اقرأني» إن كانت رابطًا تُترك كما هي.
- كلُّ كتابةٍ في الخزنة ذرّية (`_write_atomic`: ملفٌّ مؤقّت، ثم `fsync`، ثم `os.replace`)، فانقطاعُ البناء لا يترك «خطواتي» ولا غيرَها
  نصفَ مكتوبة؛ و«خطواتي» تُكتب قبل سجلّها (ملاحظة Codex الثامنة عشرة على #161).

**الحدُّ المعلَن:** الأداةُ لا تمنع المالكَ من وضع الخزنة في مجلّد مزامنةٍ سحابيّ؛ ذلك خيارُه (G10). والمرآةُ
لقطةٌ عند البناء: تتقادم حتى يُعاد البناء بعد الدمج. وفحصُ الروابط يسبق الكتابة ولا يلازمها: رابطٌ يُنشأ بينهما لا يُرى،
وجذرُ الخزنة نفسُه يُحلّ إلى مساره الحقيقيّ، ومجلّدُ `Inbox/` للمالك يربطه حيث شاء. وسجلُّ «خطواتي» يُكتب بعدها: إن انقطع البناءُ
بينهما قُرئ سجلٌّ أقدم منها، فيُنقل سطرٌ للأداة إلى قسم الملاحظات أو تبقى علامتُها لاحقةً للمالك، ولا يُمحى له شيء. و«[ ]» غيابُ
علامة المالك لا علامة: خطوةٌ أجّلتها الخطةُ وأعادها المالكُ «[ ]» بيده تعود «⏸» ما دام التأجيلُ قائمًا.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = "docs/PLAN-20260926.json"
MIRROR_FILES = ("AGENTS.md", "docs/STATUS.md", "docs/VISION.md", "docs/DECISIONS.md", "docs/PLAN-20260926.md")
GUIDES_DIR = "docs/guides"
OUT = "Diwan"
INBOX = "Inbox"
DONE = "منجز"
MANIFEST = ".diwan-mirror.json"
STEPS_STATE = ".diwan-steps.json"          # ما كتبته الأداةُ نفسُها في «خطواتي» لكل خطوة: مصدرُ التمييز بين نصّها ونصّ المالك
SEED_NOTES = ("خطواتي.md",)
INBOX_README = "اقرأني.md"
HEADER = "> **مرآةٌ للقراءة** من `{src}` في المستودع. لا تحرّرها هنا: أعيد بناؤها بعد كل دمج.\n\n"
ASSIGNEE = {
    "claude-cloud": "Claude السحابي", "claude-mac": "Claude على الماك", "claude-nitro": "Claude على Nitro",
    "jules": "Jules", "codex": "Codex", "opencode": "OpenCode", "gemini-reviewer": "مراجِع Gemini",
    "kimi": "Kimi", "deepseek-mistral": "DeepSeek وMistral", "owner": "المالك", "hermes": "Hermes",
}


class VaultError(Exception):
    """رفضٌ مسمًّى: الرمزُ في `code`."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _refuse_sealed(path: Path) -> None:
    if any("sealed" in part.lower() for part in path.parts):
        raise VaultError("sealed_path_refused", str(path))


def check_vault_location(vault: Path, root: Path = ROOT) -> Path:
    vault = vault.expanduser().resolve()
    _refuse_sealed(vault)
    root = root.resolve()
    if vault == root or root in vault.parents:
        raise VaultError("vault_inside_repository", str(vault))
    return vault


def safe_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|#^\[\]]', "-", text).strip() or "-"


def _sources(root: Path) -> list[Path]:
    files = [root / f for f in MIRROR_FILES]
    guides = root / GUIDES_DIR
    if guides.is_dir():
        files += sorted(p for p in guides.iterdir() if re.fullmatch(r"G\d+\.md", p.name))
    for f in files:
        _refuse_sealed(f.relative_to(root))
    return [f for f in files if f.is_file()]


def deferred_label(entry: dict) -> str | None:
    """بندٌ في الخطة (مهمّةٌ أو خطوةُ مالك) أُرجئ بقرارٍ: `deferred: {by, until, reason}` في `docs/PLAN-20260926.json`. الخزنةُ
    تسمّيه مؤجَّلًا ولا تعرضه عملًا نشطًا (ق٦٨: Nitro؛ ملاحظة Codex على #161)."""
    d = entry.get("deferred")
    return f"مؤجَّلة ({d['by']} حتى {d['until']}: {d['reason']})" if d else None


_TASK_TEXT = ("title", "description", "deliverable", "acceptance_evidence")


def task_deferral(task: dict, agents: dict[str, dict]) -> str | None:
    """تأجيلُ المهمّة: تأجيلُها هي، أو تأجيلُ الدور المسنَدة إليه — فالخطةُ تؤجّل دورًا كاملًا (`agents[].deferred`، ق٦٨: دورُ
    `claude-nitro`)، ومهمّةٌ مسنَدةٌ إليه بلا حقلٍ باسمها (غ١١) كانت تُعرض عملًا نشطًا (ملاحظة Codex العاشرة على #161)."""
    return deferred_label(task) or deferred_label(agents.get(task["assignee"], {}))


def deferred_roles(task: dict, agents: dict[str, dict]) -> list[str]:
    """أدوارٌ مؤجَّلة يسمّيها نصُّ مهمّةٍ نشطة مسنَدةٍ إلى غيرها (مهمّةٌ مختلطة): «claude-nitro يركّبه على ويندوز (`winget …`)» في
    مهمّةٍ لـclaude-mac كانت تُعرض على الدور المؤجَّل عملًا حاليًّا (ملاحظة Codex العاشرة على #161). الاسمُ يُطابَق معرّفًا لاتينيًّا
    تامًّا، فلا يُحسب `claude-nitro-2` مثلًا `claude-nitro`؛ وحرفٌ عربيٌّ متّصلٌ به («وclaude-nitro») لا يمنع المطابقة."""
    text = "\n".join(str(task.get(k, "")) for k in _TASK_TEXT)
    return [name for name, agent in agents.items()
            if agent.get("deferred") and name != task["assignee"]
            and re.search(rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])", text)]


def role_deferral_note(name: str, agent: dict) -> str:
    d = agent["deferred"]
    return (f"دورُ {ASSIGNEE.get(name, name)} (`{name}`) في هذه المهمّة مؤجَّل ({d['by']} حتى {d['until']}: {d['reason']})؛ "
            "فما نُسب إليه فيها لا يُنفَّذ الآن، وباقيها نشط")


def deferred_steps(task: dict, owner_steps: list[dict]) -> list[dict]:
    """خطواتُ المالك المؤجَّلة التي تفتح مهمّةً نشطة (`owner_steps[].unblocks`): كانت ح٢ «فكّ حجب جلستَي الماك وNitro» تفتحها خطوةُ
    المالك ٤ (صلاحياتُ جلسة Nitro، مؤجَّلةٌ بق٦٨)، فكان شطرُ Nitro منها يُعرض عملًا حاليًّا — من صنف ملاحظة Codex العاشرة على #161.
    ثم فُصل ذلك الشطرُ في ح٢-ن المؤجَّلة (الجولة السادسة عشرة)؛ والوسمُ باقٍ لكل مهمّةٍ نشطة تفتحها خطوةٌ مؤجَّلة، و`tests/test_plan_deferrals.py`
    يمنع الخطةَ الحاكمة من ذلك إلا لمهمّةٍ فيها شطرٌ مؤجَّل يسمّي دورَه."""
    return [s for s in sorted(owner_steps, key=lambda s: s["order"]) if s.get("deferred") and task["id"] in s.get("unblocks", [])]


def step_deferral_note(step: dict) -> str:
    d = step["deferred"]
    return (f"خطوةُ المالك {step['order']} «{step['title']}» التي تفتح هذه المهمّة مؤجَّلة ({d['by']} حتى {d['until']}: {d['reason']})؛ "
            "فما يتوقّف عليها منها لا يُنتظر الآن، وباقيها نشط")


def render(root: Path = ROOT) -> dict[str, str]:
    """ما يجب أن يكون في `Diwan/`: المسارُ النسبيّ ← النصّ."""
    out: dict[str, str] = {}
    for src in _sources(root):
        rel = src.relative_to(root).as_posix()
        folder = "الأدلة" if rel.startswith(GUIDES_DIR + "/") else "الوثائق"
        out[f"{folder}/{src.name}"] = HEADER.format(src=rel) + src.read_text(encoding="utf-8")
    plan_path = root / PLAN
    if not plan_path.is_file():
        return out
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    tasks = {t["id"]: t for t in plan["tasks"]}
    agents = {a["name"]: a for a in plan.get("agents", [])}
    link = lambda tid: f"[[المهام/{safe_name(tid)}|{tid}]]"  # noqa: E731
    # مهمّةٌ تعتمد على مؤجَّلةٍ أو تفتحها تسمّيها مؤجَّلةً في سطرها (جديد-v1-acceptance تعتمد على ع٣)
    ref = lambda tid: link(tid) + (" (⏸ مؤجَّلة)" if task_deferral(tasks[tid], agents) else "")  # noqa: E731
    for t in plan["tasks"]:
        deferred = task_deferral(t, agents)
        roles = [] if deferred else deferred_roles(t, agents)
        held = [] if deferred else deferred_steps(t, plan["owner_steps"])
        lines = [
            "---", f"id: {t['id']}", f"phase: {t['phase']}", f"assignee: {t['assignee']}", f"block: {t['block']}",
            f"effort: {t['effort']}", *(["status: deferred"] if deferred else []),
            *([f"deferred_roles: {', '.join(roles)}"] if roles else []),
            *([f"deferred_steps: {', '.join(str(s['order']) for s in held)}"] if held else []), "---", "", f"# {t['id']} — {t['title']}", "",
            f"**المنفّذ:** {ASSIGNEE.get(t['assignee'], t['assignee'])} · **المرحلة:** [[لوحة المراحل#{t['phase']}|{t['phase']}]] · **البلوك:** {t['block']}", "",
            *([f"> ⏸ **{deferred}**", ""] if deferred else []),
            *[x for name in roles for x in (f"> ⏸ **{role_deferral_note(name, agents[name])}**", "")],
            *[x for s in held for x in (f"> ⏸ **{step_deferral_note(s)}**", "")],
            t.get("description", ""), "", f"**المُخرج:** {t.get('deliverable', '')}", "", f"**دليل القبول:** {t.get('acceptance_evidence', '')}",
        ]
        if t.get("depends_on"):
            lines += ["", "**يعتمد على:** " + "، ".join(ref(d) if d in tasks else d for d in t["depends_on"])]
        if t.get("unblocks"):
            lines += ["", "**يفتح:** " + "، ".join(ref(d) if d in tasks else d for d in t["unblocks"])]
        if t.get("guide_id"):
            lines += ["", f"**الدليل:** [[الأدلة/{t['guide_id']}|{t['guide_id']}]]"]
        out[f"المهام/{safe_name(t['id'])}.md"] = "\n".join(lines) + "\n"
    board = ["# لوحة المراحل", "", HEADER.format(src=PLAN).strip(), ""]
    for p in plan["phases"]:
        board += [f"## {p['id']}", "", f"**{p['name']}** ({p['start']} ← {p['end']})", "", p["goal"], "", "**بوابة الخروج:**"]
        board += [f"- {g}" for g in p["gate"]] + ["", "**المهامّ:**"]
        for tid in p["task_ids"]:
            t = tasks.get(tid)
            if t:
                deferred = task_deferral(t, agents)
                roles = [] if deferred else deferred_roles(t, agents)
                held = [] if deferred else deferred_steps(t, plan["owner_steps"])
                board.append(f"- {'⏸ ' if deferred else ''}{link(tid)} {t['title']} · {ASSIGNEE.get(t['assignee'], t['assignee'])}"
                             + (f" — **{deferred}**" if deferred else "")
                             + "".join(f" — ⏸ دورُ {ASSIGNEE.get(n, n)} فيها مؤجَّل ({agents[n]['deferred']['by']})" for n in roles)
                             + "".join(f" — ⏸ خطوةُ المالك {s['order']} لها مؤجَّلة ({s['deferred']['by']})" for s in held))
        board.append("")
    out["لوحة المراحل.md"] = "\n".join(board)
    steps = ["# خطواتي", "", INSTRUCTION, ""]
    postponed = []
    for s in sorted(plan["owner_steps"], key=lambda s: s["order"]):
        guide = f"[[الأدلة/{s['guide_id']}|{s['guide_id']}]]" if re.fullmatch(r"G\d+", s["guide_id"]) else s["guide_id"]
        deferred = deferred_label(s)
        if deferred:
            # خطوةٌ مؤجَّلة لا تُعرض عملًا نشطًا على المالك (ق٦٨)؛ تُذكر باسمها في قسمها حتى يُعلن استئنافَها
            postponed.append(f"- ⏸ **{s['order']}. {s['title']}** · {guide} · {s['time']} · {s['cost']} — {deferred}")
        else:
            steps.append(f"- [ ] **{s['order']}. {s['title']}** · {guide} · {s['time']} · {s['cost']}")
    if postponed:
        steps += ["", "## خطواتٌ مؤجَّلة", "", "لا تُطلب منك الآن؛ تعود إلى القائمة بإشعارك.", "", *postponed]
    out["خطواتي.md"] = "\n".join(steps) + "\n"
    out["ابدأ هنا.md"] = "\n".join([
        f"# {plan['title']}", "", plan["thesis"], "",
        "- [[لوحة المراحل]]: المراحل وبواباتها ومهامّها.", "- [[خطواتي]]: ما بيدك بالترتيب.",
        "- الوثائق الحاكمة في مجلّد «الوثائق»، والأدلّة خطوةً بخطوة في «الأدلة».",
        f"- اكتب طلبك في مجلّد `{INBOX}/` ملاحظةً جديدة، فيقرؤها Claude على الماك في بداية جلسته.", "",
    ])
    return out


def _refuse_links(vault: Path, rels) -> None:
    """كلُّ مسارٍ ستكتبه الأداةُ أو تقرؤه للترحيل أو تحذفه (نسبيًّا إلى الخزنة) يُفحص بـ`lstat` مكوّنًا مكوّنًا قبل أيّ كتابة: رابطٌ رمزيّ
    في أيّ مكوّن يُرفض باسمه (`symlink_refused`)، والملفُّ الموجود غيرُ العاديّ (مجلّدٌ أو أنبوب) يُرفض (`not_a_regular_file`). كان
    ترحيلُ «خطواتي» يكتب عبر الرابط فيستبدل ملاحظةً للمالك خارج الخزنة ولو بلا `--force` (ملاحظة Codex الثانية عشرة على #161)."""
    seen: set[Path] = set()
    for rel in rels:
        parts, cur = Path(rel).parts, vault
        for i, part in enumerate(parts):
            cur = cur / part
            if cur in seen:
                continue
            seen.add(cur)
            try:
                mode = os.lstat(cur).st_mode
            except (FileNotFoundError, NotADirectoryError):
                break                                                   # ما لم يوجد بعدُ يُنشأ ملفًّا عاديًّا
            if stat.S_ISLNK(mode):
                raise VaultError("symlink_refused", cur.relative_to(vault).as_posix())
            if i == len(parts) - 1 and not stat.S_ISREG(mode):
                raise VaultError("not_a_regular_file", cur.relative_to(vault).as_posix())


def _checked_manifest(vault: Path, wanted) -> dict[str, str]:
    """البيانُ بعد فحص كلِّ ما ستمسّه الأداة: البيانُ نفسُه والمولَّدُ قبل قراءته، ثم ما سجّله البيانُ مما قد يُحذف."""
    _refuse_links(vault, [f"{OUT}/{MANIFEST}", f"{OUT}/{STEPS_STATE}", *(f"{OUT}/{rel}" for rel in wanted)])
    old = _load_manifest(vault / OUT)["files"]
    _refuse_links(vault, [f"{OUT}/{rel}" for rel in old])
    return old


def _load_manifest(out_dir: Path) -> dict:
    path = out_dir / MANIFEST
    if not path.is_file():
        return {"files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


NOTES_HEADING = "## ملاحظاتي"
LEGACY_HEADING = "### سطورٌ نُقلت من النسخة السابقة"
DONE_BEFORE_DEFERRAL = " — أُنجزت قبل التأجيل"
_STEP_LINE = re.compile(r"^- (\[[ xX]\]|⏸) \*\*(\d+)\.")
_DEFERRAL_LABEL = " — مؤجَّلة ("
INSTRUCTION = ("علّم الخطوة حين تنتهي. تُعاد كتابةُ القائمة من الخطة عند كل بناء وتبقى علاماتُك؛ وما تكتبه تحت "
               "«## ملاحظاتي» في آخر الملاحظة يبقى كما هو.")
LEGACY_INSTRUCTION = "علّم الخطوة حين تنتهي. هذه الملاحظة لك: لا تكتب الأداةُ فوقها بعد إنشائها."
# ما تولّده الأداةُ من سطورٍ غير الخطوات، بنصّه التامّ: سطرٌ زاد عليه المالكُ شيئًا ليس منها فيُنقل ولا يُمحى — كان سطرُ التعليمات
# يُعرف ببدايته فيُمحى تعليقٌ ألحقه المالكُ به («… — اتصلتُ بالفريق»)، والنسخةُ القديمة دعته إلى التعديل حيث شاء (ملاحظة Codex الحادية عشرة على #161)
_KNOWN_LINES = ("# خطواتي", INSTRUCTION, LEGACY_INSTRUCTION, "## خطواتٌ مؤجَّلة", "لا تُطلب منك الآن؛ تعود إلى القائمة بإشعارك.")


_OWNER_MARKS = ("[x]", "[X]", "⏸")          # علامةٌ وضعها المالكُ بيده: أنجز الخطوة، أو أوقفها؛ و«[ ]» غيابُ علامته لا علامة


def _load_steps_state(out_dir: Path) -> dict | None:
    """سجلُّ ما كتبته الأداةُ في «خطواتي» في البناء السابق، لكل خطوةٍ بهويّتها: `{mark, text, override}` — العلامةُ التي كتبتها،
    ونصُّها بعد الرقم (بعلامة التأجيل وعلامة الإنجاز قبل التأجيل إن ولّدتهما)، وعلامةُ المالك التي حفظتها. سجلٌّ مفقود (أولُ بناء، أو خزنةٌ
    من قبل هذا التغيير) أو تالفٌ أو بغير صيغته: `None`، فيُرحَّل الملفُّ بالقاعدة المحافِظة ولا يُسقط البناء."""
    try:
        data = json.loads((out_dir / STEPS_STATE).read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    steps = data.get("steps") if isinstance(data, dict) and data.get("schema_version") == 1 else None
    ok = isinstance(steps, dict) and all(
        isinstance(r, dict) and isinstance(r.get("mark"), str) and isinstance(r.get("text"), str) and r.get("override") in (None, *_OWNER_MARKS)
        for r in steps.values())
    return steps if ok else None


def _write_atomic(path: Path, data: bytes) -> None:
    """الكتابةُ الوحيدة في الخزنة: ملفٌّ مؤقّتٌ فريد بجانب الهدف، ثم `fsync`، ثم يحلّ محلَّه دفعةً واحدة (`os.replace` لا يتبع رابطًا).
    فانقطاعُ العملية أو خطأُ كتابةٍ جزئيّ يترك الملفَّ السابق كما هو بايتًا بايتًا ولا يترك المؤقّت: كان `write_bytes` يمسح «خطواتي»
    قبل أن يكتبها فتضيع علاماتُ المالك وملاحظاتُه (ملاحظة Codex الثامنة عشرة على #161). ويبقى للملف القائم إذنُه، وللجديد إذنُ `umask`.
    و`tests/test_obsidian_vault.py` يفحص المصدرَ نفسَه: لا كتابةَ إلى ملفٍّ في الأداة إلا هنا."""
    try:
        mode = stat.S_IMODE(os.lstat(path).st_mode)
    except FileNotFoundError:
        mask = os.umask(0)
        os.umask(mask)
        mode = 0o666 & ~mask
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _owner_mark(mine: tuple | None, record: dict | None, default: str) -> str | None:
    """علامةُ المالك على خطوته من مصدرها لا من شكلها: إن بقيت العلامةُ كما كتبتها الأداةُ في البناء السابق فعلامتُه ما حفظه السجلّ؛ وإن
    غيّرها فهي علامتُه ما كانت «[x]» أو «⏸» — فخطوةٌ نشطة أوقفها بيده (⏸) تبقى موقوفة ولا تعود «[ ]»، وكان «⏸» يُحسب علامةَ الأداة
    لأنه شكلُها (ملاحظة Codex الرابعة عشرة على #161). وبلا سجلٍّ لا يُعرف المصدر، فالقاعدةُ المحافِظة: ما يطابق ما تولّده الخطةُ الآن
    (`default`) لها، وكلُّ «[x]» و«⏸» سواه للمالك."""
    if mine is None:
        return None
    if record is not None and mine[1] == record["mark"]:
        return record.get("override")
    if record is None and mine[1] == default:
        return None
    return mine[1] if mine[1] in _OWNER_MARKS else None


def _owner_suffix(existing: str, rendered: str, record: dict | None) -> str | None:
    """لاحقةُ المالك على سطر خطوته: ما بعد النصّ الذي كتبته الأداةُ نفسُها لهذه الخطوة كما حفظه السجلّ — فلا يُنزع إلا ما كتبته هي
    بايتًا بايتًا (علامةُ تأجيلٍ زالت أو تغيّر سببُها، وعلامةُ الإنجاز قبل التأجيل)، وكلُّ ما عداه للمالك: تأجيلٌ كتبه بيده، وتعليقٌ بعد
    علامة الأداة ولو بقوسين. و`None` إن عدّل النصَّ الذي كتبته الأداةُ نفسَه فيُنقل سطرُه كاملًا (ملاحظاتُ Codex على #161: الخامسة
    والسادسة والسابعة والثالثة عشرة والرابعة عشرة).
    وبلا سجلٍّ لهذه الخطوة القاعدةُ المحافِظة: لا يُنزع إلا ما تولّده الخطةُ الآن بنصّه — علامةُ تأجيلها الحالية، ثم علامةُ الإنجاز
    قبل التأجيل إن تلتها مباشرةً، أيًّا كانت علامةُ الخطوة الآن: فالإنجازُ يُعاد حسابُه من علامتها الحالية وحدها، ومالكٌ رفع «[x]» بيده
    لا تبقى على سطره «أُنجزت قبل التأجيل» (ملاحظة Codex الخامسة عشرة على #161) — وما سواه يبقى للمالك ولو كان علامةَ تأجيلٍ قديمة."""
    if record is not None:
        written = record["text"]
        return existing[len(written):] if existing.startswith(written) else None
    start = rendered.find(_DEFERRAL_LABEL)
    body, label = (rendered, "") if start < 0 else (rendered[:start], rendered[start:])
    if not existing.startswith(body):
        return None
    rest = existing[len(body):]
    if label and rest.startswith(label):
        rest = rest[len(label):]
        if rest.startswith(DONE_BEFORE_DEFERRAL):
            rest = rest[len(DONE_BEFORE_DEFERRAL):]
    return rest


def _title(tail: str) -> str:
    """عنوانُ الخطوة من ذيل سطرها (ما بين رقمها وإغلاق التخطيط `**`): هويّةُ الخطوة التي تنتقل بها علامةُ الإنجاز، لا رقمُها —
    فخطوةٌ أخرى أخذت رقمَ خطوةٍ منجزة حُذفت أو أُعيد ترقيمُها لا تظهر منجزةً (ملاحظة Codex الثامنة على #161)."""
    return tail.split("**", 1)[0].strip()


def migrate_steps(existing: str, rendered: str, state: dict | None = None) -> tuple[str, dict]:
    """«خطواتي» تُعاد كتابتُها من الخطة مع الحفاظ على ما للمالك فيها، ويُعاد معها سجلُّ ما كتبته الأداةُ لكل خطوة (`STEPS_STATE`).
    الخطوةُ يوافقها سطرُها الموجود بهويّتها — رقمُها وعنوانُها معًا — لا برقمها وحده. علامةُ المالك («[x]» أنجزها، «⏸» أوقفها بيده)
    تبقى، وما أُنجز ثم أُجّل يبقى منجزًا باسمه؛ ولاحقتُه بعد النصّ الذي كتبته الأداةُ تبقى على سطرها؛ وسطرٌ عدّل فيه نصَّ الأداة نفسَه
    يُنقل كما هو؛ وما تحت «## ملاحظاتي» يُنقل كما هو؛ وما كتبه في غير ذلك من سطور (النسخةُ القديمة دعته إلى التعديل حيث شاء) يُنقل
    تحت «### سطورٌ نُقلت من النسخة السابقة» في قسم ملاحظاته لا يُمحى بصمت. وسطورُ الرقم الواحد تُحفظ كلُّها لا آخرُها: تأخذ الخطوةُ
    المولَّدة سطرًا واحدًا يوافقها هويّةً، وما لم تأخذه خطوةٌ يُنقل كما هو بترتيبه (ملاحظة Codex التاسعة على #161).
    ومصدرُ التمييز بين نصّ الأداة ونصّ المالك هو السجلّ لا شكلُ السطر: كانت علامةُ «⏸» أو لفظُ «— مؤجَّلة (…)» يُحسبان للأداة أينما
    وقعا فيُمحى تأجيلٌ كتبه المالكُ بيده (ملاحظتا Codex الثالثة عشرة والرابعة عشرة على #161). كانت البذرةُ تُحفظ حرفيًّا حتى مع
    `--force`، فلا يبلغ التأجيلُ (ق٦٨) خزنةً قائمة (ملاحظات Codex على #161)."""
    head, sep, notes = existing.partition("\n" + NOTES_HEADING)
    entries, carry = [], {}                                           # سطورُ الخطوات في الموجود كلُّها: (الموضع، العلامة، الرقم، الذيل، السطر)
    for pos, line in enumerate(head.splitlines()):
        if m := _STEP_LINE.match(line):
            entries.append((pos, m.group(1), m.group(2), line[m.end():], line))
        elif line.strip() and line.strip() not in _KNOWN_LINES:
            carry[pos] = line
    lines, claimed, written = [], set(), {}
    for line in rendered.splitlines():
        if not (m := _STEP_LINE.match(line)):
            lines.append(line)
            continue
        default, order, tail = m.group(1), m.group(2), line[m.end():]
        key = f"{order}. {_title(tail)}"
        # سطرُ الخطوة في الموجود هو ما وافقها هويّةً — رقمًا وعنوانًا — ولم يأخذه سطرٌ مولَّدٌ قبلها؛ وكلُّ سطرٍ لا يأخذه أحدٌ يُنقل
        mine = next((e for e in entries if e[0] not in claimed and e[2] == order and _title(e[3]) == _title(tail)), None)
        if mine is not None:
            claimed.add(mine[0])
        record = (state or {}).get(key) if mine is not None else None
        override = _owner_mark(mine, record, default)
        completed = override in ("[x]", "[X]")
        mark = "[x]" if completed else (override or default)
        text = tail + (DONE_BEFORE_DEFERRAL if completed and default == "⏸" else "")
        suffix = ""
        if mine is not None:
            # لاحقةُ المالك هي ما زاد على النصّ الذي كتبته الأداةُ نفسُها (`_owner_suffix`)؛ وما عُدّل داخل ذلك النصّ يُنقل سطرًا كاملًا
            owned = _owner_suffix(mine[3], tail, record)
            if owned is not None:
                suffix = owned
            else:
                carry[mine[0]] = mine[4]
        written[key] = {"mark": mark, "text": text, "override": override}
        lines.append(f"- {mark} **{order}.{text}{suffix}")
    # وسطرٌ مرقَّم لم يوافق خطوةً في الخطة الجديدة لا يُمحى: يُنقل كما هو — خطوةٌ أضافها المالك بنفسه (برقمٍ جديد أو برقم خطوةٍ
    # مولَّدة، قبلها أو بعدها)، أو خطوةٌ حُذفت أو أُعيد ترقيمُها وعليها تعليقُه؛ والمنقولُ كلُّه بترتيبه في الموجود
    carry.update({e[0]: e[4] for e in entries if e[0] not in claimed})
    stray = [carry[pos] for pos in sorted(carry)]
    text = "\n".join(lines) + "\n"
    if sep or stray:
        text += "\n" + NOTES_HEADING + (notes.rstrip("\n") if sep else "") + "\n"
    if stray:
        text += "\n" + LEGACY_HEADING + "\n\n" + "\n".join(stray) + "\n"
    return text, written


def migrate_seed(rel: str, existing: str, rendered: str, state: dict | None = None) -> tuple[str, dict | None]:
    return migrate_steps(existing, rendered, state) if rel == "خطواتي.md" else (existing, None)


def build(vault: Path, root: Path = ROOT, force: bool = False) -> dict:
    vault = check_vault_location(vault, root)
    out_dir = vault / OUT
    wanted = render(root)
    old = _checked_manifest(vault, wanted)                             # يُرفض الرابطُ قبل أن يُكتب شيء
    state, steps_state = _load_steps_state(out_dir), None
    out_dir.mkdir(parents=True, exist_ok=True)
    (vault / INBOX / DONE).mkdir(parents=True, exist_ok=True)
    readme = vault / INBOX / INBOX_README
    if not readme.exists() and not readme.is_symlink():                # رابطٌ للمالك مكانَها — ولو معلَّقًا — يُترك
        _write_atomic(readme, ("# صندوق الوارد\n\nاكتب هنا طلبك لـClaude ملاحظةً جديدة (ملاحظة لكل طلب). يقرؤها Claude على الماك في "
                               "بداية جلسته، ويحوّل ما يلزم إلى مسألة GitHub بموافقتك، ثم ينقلها إلى «منجز». لا تكتب هنا مفتاحًا ولا "
                               "توكنًا.\n").encode("utf-8"))
    report = {"written": [], "unchanged": [], "kept_edited": [], "removed": [], "migrated": []}
    new_manifest: dict[str, str] = {}
    for rel, text in wanted.items():
        target = out_dir / rel
        data = text.encode("utf-8")
        if rel in SEED_NOTES:
            # البذرةُ لا تُكتب فوقها حرفيًّا: تُرحَّل بما للمالك فيها (علاماتُه وملاحظاتُه) وتُطبَّق عليها الخطةُ الحالية، ويُحفظ ما كتبته الأداة
            existing = target.read_bytes().decode("utf-8") if target.exists() else ""
            merged, steps_state = migrate_seed(rel, existing, text, state if existing else None)
            data = merged.encode("utf-8")
            new_manifest[rel] = sha(data)
            if target.exists() and data == target.read_bytes():
                report["unchanged"].append(rel)
                continue
            report["migrated" if target.exists() else "written"].append(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(target, data)                               # قائمةُ المالك لا تُمسح قبل أن تُكتب، وتُكتب قبل سجلّها
            continue
        if target.exists():
            current = sha(target.read_bytes())
            if current == sha(data):
                report["unchanged"].append(rel)
                new_manifest[rel] = current
                continue
            if old.get(rel) != current and not force:
                report["kept_edited"].append(rel)
                new_manifest[rel] = old.get(rel, "")
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(target, data)
        new_manifest[rel] = sha(data)
        report["written"].append(rel)
    for rel, recorded in old.items():
        if rel in wanted:
            continue
        target = out_dir / rel
        if target.is_file() and sha(target.read_bytes()) == recorded:
            target.unlink()
            report["removed"].append(rel)
    _write_atomic(out_dir / MANIFEST, json.dumps({"schema_version": 1, "files": new_manifest}, ensure_ascii=False, indent=1).encode("utf-8"))
    if steps_state is not None:
        blob = json.dumps({"schema_version": 1, "steps": steps_state}, ensure_ascii=False, indent=1).encode("utf-8")
        if not ((out_dir / STEPS_STATE).is_file() and (out_dir / STEPS_STATE).read_bytes() == blob):
            _write_atomic(out_dir / STEPS_STATE, blob)                # بعد «خطواتي»: سجلٌّ أقدمُ منها يُقرأ بالقاعدة المحافِظة
    return report


def check(vault: Path, root: Path = ROOT) -> list[str]:
    vault = check_vault_location(vault, root)
    out_dir = vault / OUT
    drift = []
    wanted = render(root)
    manifest = _checked_manifest(vault, wanted)
    state = _load_steps_state(out_dir)
    for rel, recorded in manifest.items():                           # ما سيحذفه build ولم يُحذف بعد
        target = out_dir / rel
        if rel not in wanted and target.is_file() and sha(target.read_bytes()) == recorded:
            drift.append(f"obsolete {rel}")
    for rel, text in wanted.items():
        target = out_dir / rel
        if rel in SEED_NOTES:
            if not target.exists():
                drift.append(f"missing {rel}")
            elif migrate_seed(rel, target.read_bytes().decode("utf-8"), text, state)[0] != target.read_bytes().decode("utf-8"):
                drift.append(f"stale {rel}")
            continue
        if not target.is_file() or target.read_bytes() != text.encode("utf-8"):
            drift.append(f"stale {rel}")
    return drift


def list_inbox(vault: Path, root: Path = ROOT) -> list[dict]:
    vault = check_vault_location(vault, root)
    inbox = vault / INBOX
    notes = []
    for p in sorted(inbox.glob("*.md")) if inbox.is_dir() else []:
        if p.name == INBOX_README:
            continue
        text = p.read_text(encoding="utf-8")
        first = next((line.lstrip("# ").strip() for line in text.splitlines() if line.strip()), p.stem)
        notes.append({"path": f"{INBOX}/{p.name}", "title": first[:120], "sha256": sha(text.encode("utf-8"))})
    return notes


def mark_done(vault: Path, note: str, root: Path = ROOT) -> Path:
    vault = check_vault_location(vault, root)
    inbox = (vault / INBOX).resolve()
    src = (vault / note).resolve()
    if src.parent != inbox or not src.is_file() or src.name == INBOX_README:
        raise VaultError("not_an_inbox_note", note)
    dest = inbox / DONE / src.name
    n = 2
    while dest.exists():                       # لا يُكتب فوق ملاحظةٍ منجزةٍ سابقة بالاسم نفسه
        dest = inbox / DONE / f"{src.stem}-{n}{src.suffix}"
        n += 1
    shutil.move(str(src), str(dest))
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "check", "list-inbox", "mark-done"):
        p = sub.add_parser(name)
        p.add_argument("--vault", required=True, type=Path)
        if name == "build":
            p.add_argument("--force", action="store_true", help="اكتب فوق ما عدّله المالك في Diwan/")
        if name == "mark-done":
            p.add_argument("note")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "build":
            print(json.dumps({k: len(v) if k != "kept_edited" else v for k, v in build(a.vault, force=a.force).items()}, ensure_ascii=False))
            return 0
        if a.cmd == "check":
            drift = check(a.vault)
            print(json.dumps({"drift": drift}, ensure_ascii=False))
            return 1 if drift else 0
        if a.cmd == "list-inbox":
            for n in list_inbox(a.vault):
                print(json.dumps(n, ensure_ascii=False))
            return 0
        print(mark_done(a.vault, a.note))
        return 0
    except VaultError as e:
        print(json.dumps({"error": e.code, "detail": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
