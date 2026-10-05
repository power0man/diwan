#!/usr/bin/env python3
"""حالةُ كلِّ مهمّةٍ في الخطة الحاكمة من إثباتها لا من النثر (ق٧٣ الخطوة 0.4): `docs/PLAN-STATUS.md`.

    python3 tools/plan_status.py --write    # يولّد الملفّ
    python3 tools/plan_status.py --check    # يخرج 1 إن كان الملفّ قديمًا

الخطةُ (`docs/PLAN-20260926.json`) لا تحمل حالةً لمهامّها الستّ والسبعين، فكانت كلُّ جلسةٍ تعيد اكتشافها. والحالةُ هنا
مشتقّةٌ من ثلاثة مصادر في المستودع وحده، بلا شبكةٍ ولا git:
- **منجزة:** سطرٌ للمعرّف في `docs/TASKS.jsonl` (طلبٌ مدموج بذيولٍ مسجَّلة، ك٤١)، أو صفُّه «منجزة: <بصمة>» في
  `docs/TASKS-ARCHIVE.md`.
- **مؤجَّلة:** حقلُ `deferred` في الخطة نفسها بقراره وموعده.
- **للمالك:** مهامُّ المالك (`ح…`) معفاةٌ من سجلّ الإثبات (`tools/issue_ledger.py`)، فلا تُعدّ مفتوحةً بغيابها عنه؛
  وما أُثبت منها نصًّا في `docs/STATUS.md` يُسمّى في `OWNER_EVIDENCE` بمصدره فيصير «منجزة».
- **مفتوحة:** ما سواها.

ويحرس `--check` تعارضًا بعينه: مهمّةٌ ما زالت مفتوحةً في جدول `AGENTS.md` §٣ والسجلُّ يُثبت إنجازَها (كما كانت ع٢
حتى ٥ أكتوبر). فيُعلَن التعارضُ في الملفّ ويخرج الفحصُ 1 حتى يُكتب الصفُّ منجزًا ويُؤرشف.

الحدّ: «منجزة» تعني إثباتَ الدمج لا القبولَ الحيّ؛ ومهمّةٌ أُنجزت بطلبٍ لم يُغلق مسألتَها بـ`Closes #N` تبقى «مفتوحة»
حتى يُثبتها السجلّ؛ ومهمّةٌ سُلِّمت بمسألةٍ بمعرّفٍ آخر تبقى «مفتوحة» ما لم تُسمَّ في `ALIASES` بمصدرها؛ ولا يُقرأ
تقدّمُ المسائل المفتوحة.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PLAN = "docs/PLAN-20260926.json"
LEDGER = "docs/TASKS.jsonl"
AGENTS = "AGENTS.md"
ARCHIVE = "docs/TASKS-ARCHIVE.md"
OUT = "docs/PLAN-STATUS.md"
PHASES = ("م٠-أ", "م٠-ب", "م١", "م٢", "م٣", "م٤", "v1.0")
# مهامُّ خطةٍ سُلِّمت بمسألةٍ بمعرّفٍ آخر، كما تقولها docs/STATUS.md §١ نصًّا؛ ولا يُستنتج غيرُها من نصوص المسائل
ALIASES = {"جديد-is-local-guard": ("ك٥٦",), "جديد-is-local-guard-openai": ("ج١٣",)}
# مهامُّ المالك التي تقول docs/STATUS.md §٥ نصًّا إن مسألتها أُغلقت؛ والسجلُّ يعفيها فلا يُثبتها (ملاحظة Codex على #312)
OWNER_EVIDENCE = {"ح١": "مسألة المالك #53 أُغلقت في ٢٨ سبتمبر (`docs/STATUS.md` §٥)"}
OWNER_PREFIX = "ح"
ARCHIVE_DONE = re.compile(r"^\| (\S+) \|.*\| منجزة: (?:diwan-private@)?([0-9a-f]{7,40})")


def derive(root: Path = ROOT) -> list[dict]:
    plan = json.loads((root / PLAN).read_text(encoding="utf-8"))["tasks"]
    proven = {}
    for line in (root / LEDGER).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            proven.setdefault(row["task"], row)
    archived = {m.group(1): m.group(2) for line in (root / ARCHIVE).read_text(encoding="utf-8").splitlines()
                if (m := ARCHIVE_DONE.match(line))}
    rows = []
    for task in plan:
        tid = task["id"]
        delivered = next((alias for alias in (tid, *ALIASES.get(tid, ())) if alias in proven), None)
        if delivered:
            p = proven[delivered]
            via = "" if delivered == tid else f"{delivered} "
            status, proof = "منجزة", f"{via}#{p['issue']} ← #{p['pull']} (`{p['merge'][:7]}`)"
        elif tid in archived:
            status, proof = "منجزة", f"الأرشيف (`{archived[tid][:7]}`)"
        elif tid in OWNER_EVIDENCE:
            status, proof = "منجزة", OWNER_EVIDENCE[tid]
        elif task.get("deferred"):
            d = task["deferred"]
            status, proof = "مؤجَّلة", f"{d['by']} حتى {d['until']}"
        elif tid.startswith(OWNER_PREFIX):
            status, proof = "للمالك", "معفاةٌ من سجلّ الإثبات؛ حالتُها في مسألتها و`docs/STATUS.md` §٥"
        else:
            status, proof = "مفتوحة", "—"
        rows.append({"id": tid, "phase": task["phase"], "assignee": task["assignee"], "status": status,
                     "proof": proof, "title": task["title"]})
    return rows


def conflicts(root: Path = ROOT) -> list[str]:
    """معرّفاتُ صفوفٍ مفتوحةٍ في AGENTS.md §٣ يُثبت السجلُّ إنجازَها؛ فلا تُعلن وثيقتان حالتين (ملاحظة Codex على #312)."""
    from tools.context_index import open_tasks
    proven = {json.loads(line)["task"] for line in (root / LEDGER).read_text(encoding="utf-8").splitlines() if line.strip()}
    return [row["id"] for row in open_tasks((root / AGENTS).read_text(encoding="utf-8")) if row["id"] in proven]


def render(rows: list[dict], clashes: list[str] = ()) -> str:
    counts = Counter(r["status"] for r in rows)
    # الأرشيفُ بصمةُ إيداعٍ كتبها العميل، والسجلُّ طلبٌ مدموج بذيولٍ مسجَّلة: لا يُسمّى الأولُ إثباتَ دمج (ملاحظة Codex على #312)
    archived = sum(r["status"] == "منجزة" and r["proof"].startswith("الأرشيف") for r in rows)
    owner = sum(r["status"] == "منجزة" and r["proof"].startswith("مسألة المالك") for r in rows)
    lines = [
        "# حالةُ مهامّ الخطة الحاكمة",
        "",
        "<!-- مولَّد بـ`tools/plan_status.py --write` من الخطة وسجلّ الإثبات والأرشيف؛ لا يُحرَّر يدويًّا -->",
        "",
        f"من `{PLAN}` ({len(rows)} مهمّة): **{counts['منجزة']} منجزة**، منها {counts['منجزة'] - archived - owner} بطلبٍ مدموج في "
        f"`{LEDGER}` و{archived} ببصمة إيداعٍ في الأرشيف وحدها و{owner} بإغلاق مسألة المالك؛ و{counts['مؤجَّلة']} مؤجَّلة بقرار، "
        f"و{counts['للمالك']} للمالك خارج السجلّ، و{counts['مفتوحة']} مفتوحة. «منجزة» دليلُ إنجازٍ لا قبولٌ حيّ.",
        "",
        "| المرحلة | منجزة | مؤجَّلة | للمالك | مفتوحة |",
        "|---|---|---|---|---|",
    ]
    for phase in PHASES:
        c = Counter(r["status"] for r in rows if r["phase"] == phase)
        if sum(c.values()):
            lines.append(f"| {phase} | {c['منجزة']} | {c['مؤجَّلة']} | {c['للمالك']} | {c['مفتوحة']} |")
    if clashes:
        lines += ["", "## تعارضٌ مع `AGENTS.md` §٣", "",
                  "مفتوحةٌ في الجدول ومثبتةٌ في السجلّ؛ تُكتب «منجزة» ببصمة دمجها ثم `context_index.py --write`: "
                  + "، ".join(f"`{c}`" for c in clashes)]
    for phase in PHASES:
        phase_rows = [r for r in rows if r["phase"] == phase]
        if not phase_rows:
            continue
        lines += ["", f"## {phase}", "", "| المهمّة | المنفّذ | الحالة | الإثبات |", "|---|---|---|---|"]
        lines += [f"| `{r['id']}` {r['title']} | {r['assignee']} | {r['status']} | {r['proof']} |" for r in phase_rows]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--write", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    clashes = conflicts(args.root)
    expected = render(derive(args.root), clashes)
    path = args.root / OUT
    stale = not path.exists() or path.read_text(encoding="utf-8") != expected
    if args.write and stale:
        path.write_text(expected, encoding="utf-8")
    elif args.check and stale:
        print(json.dumps({"status": "stale", "file": OUT}, ensure_ascii=False))
        return 1
    if args.check and clashes:
        print(json.dumps({"status": "conflict_with_agents_md", "tasks": clashes}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "updated" if args.write else "verified", "file": OUT}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
