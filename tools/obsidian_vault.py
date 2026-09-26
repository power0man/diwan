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
- ملاحظاتُ «البذرة» (خطواتي، واقرأني) تُكتب مرّةً إن غابت ثم هي ملكُ المالك.

**الحدُّ المعلَن:** الأداةُ لا تمنع المالكَ من وضع الخزنة في مجلّد مزامنةٍ سحابيّ؛ ذلك خيارُه (G10). والمرآةُ
لقطةٌ عند البناء: تتقادم حتى يُعاد البناء بعد الدمج.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = "docs/PLAN-20260926.json"
MIRROR_FILES = ("AGENTS.md", "docs/STATUS.md", "docs/VISION.md", "docs/DECISIONS.md", "docs/PLAN-20260926.md")
GUIDES_DIR = "docs/guides"
OUT = "Diwan"
INBOX = "Inbox"
DONE = "منجز"
MANIFEST = ".diwan-mirror.json"
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
    link = lambda tid: f"[[المهام/{safe_name(tid)}|{tid}]]"  # noqa: E731
    for t in plan["tasks"]:
        lines = [
            "---", f"id: {t['id']}", f"phase: {t['phase']}", f"assignee: {t['assignee']}", f"block: {t['block']}",
            f"effort: {t['effort']}", "---", "", f"# {t['id']} — {t['title']}", "",
            f"**المنفّذ:** {ASSIGNEE.get(t['assignee'], t['assignee'])} · **المرحلة:** [[لوحة المراحل#{t['phase']}|{t['phase']}]] · **البلوك:** {t['block']}", "",
            t.get("description", ""), "", f"**المُخرج:** {t.get('deliverable', '')}", "", f"**دليل القبول:** {t.get('acceptance_evidence', '')}",
        ]
        if t.get("depends_on"):
            lines += ["", "**يعتمد على:** " + "، ".join(link(d) if d in tasks else d for d in t["depends_on"])]
        if t.get("unblocks"):
            lines += ["", "**يفتح:** " + "، ".join(link(d) if d in tasks else d for d in t["unblocks"])]
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
                board.append(f"- {link(tid)} {t['title']} · {ASSIGNEE.get(t['assignee'], t['assignee'])}")
        board.append("")
    out["لوحة المراحل.md"] = "\n".join(board)
    steps = ["# خطواتي", "", "علّم الخطوة حين تنتهي. هذه الملاحظة لك: لا تكتب الأداةُ فوقها بعد إنشائها.", ""]
    for s in sorted(plan["owner_steps"], key=lambda s: s["order"]):
        guide = f"[[الأدلة/{s['guide_id']}|{s['guide_id']}]]" if re.fullmatch(r"G\d+", s["guide_id"]) else s["guide_id"]
        steps.append(f"- [ ] **{s['order']}. {s['title']}** · {guide} · {s['time']} · {s['cost']}")
    out["خطواتي.md"] = "\n".join(steps) + "\n"
    out["ابدأ هنا.md"] = "\n".join([
        f"# {plan['title']}", "", plan["thesis"], "",
        "- [[لوحة المراحل]]: المراحل وبواباتها ومهامّها.", "- [[خطواتي]]: ما بيدك بالترتيب.",
        "- الوثائق الحاكمة في مجلّد «الوثائق»، والأدلّة خطوةً بخطوة في «الأدلة».",
        f"- اكتب طلبك في مجلّد `{INBOX}/` ملاحظةً جديدة، فيقرؤها Claude على الماك في بداية جلسته.", "",
    ])
    return out


def _load_manifest(out_dir: Path) -> dict:
    path = out_dir / MANIFEST
    if not path.is_file():
        return {"files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def build(vault: Path, root: Path = ROOT, force: bool = False) -> dict:
    vault = check_vault_location(vault, root)
    out_dir = vault / OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    (vault / INBOX / DONE).mkdir(parents=True, exist_ok=True)
    readme = vault / INBOX / INBOX_README
    if not readme.exists():
        readme.write_text("# صندوق الوارد\n\nاكتب هنا طلبك لـClaude ملاحظةً جديدة (ملاحظة لكل طلب). يقرؤها Claude على الماك في بداية "
                          "جلسته، ويحوّل ما يلزم إلى مسألة GitHub بموافقتك، ثم ينقلها إلى «منجز». لا تكتب هنا مفتاحًا ولا توكنًا.\n",
                          encoding="utf-8")
    old = _load_manifest(out_dir)["files"]
    wanted = render(root)
    report = {"written": [], "unchanged": [], "kept_edited": [], "removed": [], "kept_seed": []}
    new_manifest: dict[str, str] = {}
    for rel, text in wanted.items():
        target = out_dir / rel
        data = text.encode("utf-8")
        if target.exists():
            current = sha(target.read_bytes())
            if rel in SEED_NOTES:
                report["kept_seed"].append(rel)
                new_manifest[rel] = old.get(rel, current)
                continue
            if current == sha(data):
                report["unchanged"].append(rel)
                new_manifest[rel] = current
                continue
            if old.get(rel) != current and not force:
                report["kept_edited"].append(rel)
                new_manifest[rel] = old.get(rel, "")
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        new_manifest[rel] = sha(data)
        report["written"].append(rel)
    for rel, recorded in old.items():
        if rel in wanted:
            continue
        target = out_dir / rel
        if target.is_file() and sha(target.read_bytes()) == recorded:
            target.unlink()
            report["removed"].append(rel)
    (out_dir / MANIFEST).write_text(json.dumps({"schema_version": 1, "files": new_manifest}, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def check(vault: Path, root: Path = ROOT) -> list[str]:
    vault = check_vault_location(vault, root)
    out_dir = vault / OUT
    drift = []
    wanted = render(root)
    for rel, recorded in _load_manifest(out_dir)["files"].items():   # ما سيحذفه build ولم يُحذف بعد
        target = out_dir / rel
        if rel not in wanted and target.is_file() and sha(target.read_bytes()) == recorded:
            drift.append(f"obsolete {rel}")
    for rel, text in wanted.items():
        target = out_dir / rel
        if rel in SEED_NOTES:
            if not target.exists():
                drift.append(f"missing {rel}")
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
