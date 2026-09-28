"""فهرسُ السياق (ق٦٧-٥): ما في كل وثيقةٍ حاكمة، ومتى تُقرأ، وبصمتُها؛ فلا يُقرأ السياقُ كلُّه كلَّ مرّة.

يولّد `docs/INDEX.md` (للقارئ) و`docs/INDEX.json` (للآلة) من نصوص الوثائق نفسِها، حتميًّا: لا زمنَ فيه ولا تاريخَ git،
فيتساوى ناتجُه في نسخةٍ ضحلة وكاملة، ويسقط `--check` حين تتغيّر وثيقةٌ بلا إعادة توليد. ويكتب في `AGENTS.md` §٠ كتلةً
مولَّدةً قصيرة (ما بقي مفتوحًا من §٣، وبصماتُ الوثائق الحاكمة) لقارئٍ لا يفتح إلا ملفًّا واحدًا: Codex يبتر ملفَّ
التعليمات بعد ٣٢ كيلوبايت (`project_doc_max_bytes`)، فيلزم أن يبقى `AGENTS.md` دون ذلك.

بصمةُ `AGENTS.md` في الفهرس تُحسب والكتلةُ المولَّدة فارغة، والكتلةُ لا تحمل بصمةَ الفهرس؛ فلا دورَ بين الاثنين.

الاستعمال:
    python tools/context_index.py --write          # يكتب الفهرسين والكتلة
    python tools/context_index.py --check          # يسقط بـ1 إن كان أحدُها متأخّرًا عن الوثائق
    python tools/context_index.py --print-budget   # حجمُ ما يُقرأ في §٠ بالبايت وبتقدير الرموز

الحدود: عدُّ الرموز تقديرٌ (الأحرفُ على ٢٫٤، القاسمُ المعايَر بقياس ٢٧ سبتمبر) لا عدُّ مُرمِّزٍ بعينه؛ والفهرسُ يصف الوثائق ولا يحكم صحّتَها؛ وما ليس في
`SOURCES` ليس مفهرسًا.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_MD = "docs/INDEX.md"
INDEX_JSON = "docs/INDEX.json"
ARCHIVE = "docs/TASKS-ARCHIVE.md"
PROBE = "docs/probe/context-index-20260927.json"
AGENTS = "AGENTS.md"
BLOCK = "context-index"
# حدُّ Codex الافتراضي لملفّ التعليمات (project_doc_max_bytes في openai/codex)؛ ما بعده لا يراه
CODEX_PROJECT_DOC_MAX_BYTES = 32_768
# ما يقرؤه العميلُ في §٠ قبل أن يعمل: هذا الملفُّ والفهرس، والرؤيةُ في أول جلسة
READING_SET = (AGENTS, INDEX_MD, "docs/VISION.md")
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
TASK_ROW = re.compile(r"^\| ([كجغع][٠-٩]+) \|")
DECISION_HEADING = re.compile(r"^## ق([٠-٩]+)")
HEADING = re.compile(r"^(#{2,3}) (.+?)\s*$")
HEADINGS_SHOWN = 40


class IndexError_(Exception):
    """رفضٌ مسمًّى: الكتلةُ المولَّدة مفقودة أو مكرَّرة، أو مصدرٌ غائب."""


@dataclass(frozen=True)
class Source:
    path: str
    role: str
    purpose: str
    read_when: str


SOURCES = (
    Source(AGENTS, "حاكمة", "الأدوار والقواعد وبروتوكول البدء، وما بقي مفتوحًا من جدول §٣", "كل جلسة، كاملًا"),
    Source("docs/STATUS.md", "حاكمة (الحالة)", "ما بُني، والعملُ الحالي بروايةٍ واحدة، وما يحكم وما هو تاريخ، وما ينتظر المالك",
           "كل جلسة: §٢ و§٥؛ والبقيةُ عند الحاجة"),
    Source("docs/VISION.md", "حاكمة (الاتجاه)", "ديوان مساعدٌ عام محوره العربية، والسياساتُ عقدة", "أول جلسة، وعند كلِّ شكٍّ في النطاق"),
    Source("docs/DECISIONS.md", "سجلُّ القرارات الحاكم", "القراراتُ المعتمدة بمصدرها وسببها وحدِّها المعلَن؛ يُقرأ بالعناوين لا بالترتيب",
           "القرارَ الذي تسمّيه مهمّتُك فقط، من عنوانه"),
    Source("docs/PLAN-20260926.md", "الخطةُ الحاكمة (ق٦٤)", "المراحلُ وبواباتُها، والمهامُّ بأدلّتها، والذكاءات، وخطواتُ المالك G1–G10",
           "المهمّةَ التي تعمل عليها فقط، من عنوانها"),
    Source("docs/TASKS.jsonl", "سجلُّ الإثبات", "سطرٌ لكل مسألةٍ أُثبت إنجازُها بطلبٍ مدموج بذيولٍ مسجَّلة (ك٤١)", "عند التحقّق من إنجازٍ"),
    Source("docs/TASKS-ARCHIVE.md", "أرشيف", "صفوفُ §٣ المنجزة بنصّها كما كانت عند أرشفتها (ق٦٧-٥)", "بإحالةٍ فقط"),
    Source("docs/PROJECT-PLAN-20260925.md", "تاريخ (ق٦١)", "خطةُ ما بعد الإطلاق: البلوكاتُ ب١–ب١٢ وقواعدُها باقية؛ وما خالف ق٦٤ منسوخ",
           "بإحالةٍ من مهمّتك"),
    Source("docs/LAUNCH-PLAN-20260925.md", "تاريخ (ك٢٧)", "خطةُ الإطلاق الأول وتحضيرُ فتح المصدر؛ قراراتُها حُسمت (ق٥١–ق٦٠)", "بإحالةٍ فقط"),
    Source("docs/REBUILD-2026-09-23.md", "تاريخ (ق٤٩)", "إعادةُ البناء ومراحلُها السبع وأدلّتُها", "بإحالةٍ فقط"),
    Source("docs/EVALUATION-20260925.md", "تقييم", "آخرُ تقييمٍ شامل (٥١/١٠٠) وما أُغلق منه موسومٌ داخله", "بإحالةٍ فقط"),
    Source("docs/AGENT-ONBOARD-REFUTE.md", "بروتوكول", "دحضُ آخر ثلاثة قرارات قبل البناء فوقها", "أول جلسةٍ لك فقط"),
    Source("docs/START-PROMPT.md", "برومبتُ المالك", "ما يلصقه المالك في أول جلسةٍ لأيّ ذكاءٍ مطوِّر", "لا يُقرأ؛ للمالك"),
    Source("docs/HANDOFF-PROMPT.md", "تاريخ", "برومبتُ تسليمٍ متنُه حتى ق٣٨ ورأسُه ق٤٩", "لا يُقرأ؛ الحالةُ في docs/STATUS.md"),
)


def _block_markers(name: str = BLOCK) -> tuple[str, str]:
    return f"<!-- generated:{name}:begin -->", f"<!-- generated:{name}:end -->"


def split_block(text: str, name: str = BLOCK) -> tuple[str, str, str]:
    """(ما قبل الكتلة مع علامة البدء، متنُ الكتلة، علامةُ الختام وما بعدها)؛ وكتلةٌ مفقودة أو مكرَّرة رفضٌ مسمًّى."""
    begin, end = _block_markers(name)
    if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) >= text.index(end):
        raise IndexError_("generated_block_invalid")
    left, rest = text.split(begin)
    body, right = rest.split(end)
    return left + begin, body, end + right


def with_block(text: str, value: str, name: str = BLOCK) -> str:
    left, _, right = split_block(text, name)
    return left + "\n" + value.rstrip() + "\n" + right


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


# نسبةُ الأحرف إلى الرموز على مجموعة قراءة §٠ كلِّها بمرمِّز المحرّك المعتمَد (Qwen3.5-9B): ٣٦٬٥٥٥ حرفًا ÷ ١٥٬١٦٨ رمزًا
# (`docs/probe/context-budget-20260927.json`؛ التدقيقُ `docs/CONTEXT-BUDGET-AUDIT-20260927.md`). كانت ٣ فبخست بخُمس، لأن
# جداولَ الفهرس وعلاماتَه أغلى من النثر؛ ويُعاد القياسُ بـtools/context_budget.py عند كل تغييرٍ في §٠
CHARS_PER_TOKEN = 2.4


def tokens_estimate(text: str) -> int:
    """تقديرٌ لا عدٌّ: الأحرفُ على النسبة المقيسة أعلاه؛ يقارب مجموعَ المجموعة ولا يصدق على كلِّ وثيقةٍ وحدها."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def headings(text: str) -> list[dict]:
    found = []
    for number, line in enumerate(text.split("\n"), 1):
        match = HEADING.match(line)
        if match:
            found.append({"line": number, "level": len(match.group(1)), "title": match.group(2)})
    return found


def task_rows(text: str) -> list[dict]:
    """صفوفُ جدول §٣: (الرقم، المسؤول، الحالة) من خلايا الصفّ."""
    rows = []
    for line in text.split("\n"):
        if not TASK_ROW.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) >= 5:
            rows.append({"id": cells[0], "owner": cells[2], "status": cells[-1]})
    return rows


def open_tasks(text: str) -> list[dict]:
    return [row for row in task_rows(text) if not row["status"].startswith("منجزة")]


def archive_done(agents_text: str, archive_text: str) -> tuple[str, str, list[str]]:
    """الصفُّ الذي كُتب فيه «منجزة» في §٣ يُنقل بنصّه إلى آخر جدول الأرشيف، فلا يبقى في AGENTS.md إلا المفتوح، ولا
    يُعلن الفهرسُ منجزًا مفتوحًا (ملاحظة Codex على #148). يعيد (نصَّ AGENTS.md، نصَّ الأرشيف، أرقامَ ما نُقل)."""
    lines = agents_text.split("\n")
    starts = [i for i, line in enumerate(lines) if line.startswith("## ٣ — المهام")]
    if not starts:
        return agents_text, archive_text, []
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    moved = [line for line in lines[start:end] if TASK_ROW.match(line) and task_rows(line)
             and task_rows(line)[0]["status"].startswith("منجزة")]
    if not moved:
        return agents_text, archive_text, []
    kept = lines[:start] + [line for line in lines[start:end] if line not in moved] + lines[end:]
    archive = archive_text.split("\n")
    rows = [i for i, line in enumerate(archive) if TASK_ROW.match(line)]
    if not rows:
        raise IndexError_("archive_table_missing")
    archive[rows[-1] + 1:rows[-1] + 1] = moved
    return "\n".join(kept), "\n".join(archive), [task_rows(line)[0]["id"] for line in moved]


def latest_decision(text: str) -> int:
    numbers = [int(m.group(1).translate(ARABIC_DIGITS)) for line in text.split("\n") if (m := DECISION_HEADING.match(line))]
    return max(numbers) if numbers else 0


def _masked_agents(text: str) -> str:
    """نصُّ AGENTS.md والكتلةُ المولَّدة فارغة: هو ما يُبصَم، فلا تغيّر الكتلةُ بصمةَ الملفّ الذي تصفه."""
    return with_block(text, "")


def _document(source: Source, text: str, measured: str | None = None) -> dict:
    """وصفُ وثيقةٍ من نصّها: الحجمُ والأسطرُ والرموزُ والعناوينُ من النصّ كما يُقرأ، والبصمةُ من `measured` إن أُعطي."""
    raw = text.encode("utf-8")
    return {"path": source.path, "role": source.role, "purpose": source.purpose, "read_when": source.read_when,
            "bytes": len(raw), "lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
            "tokens_estimate": tokens_estimate(text), "sha256_12": digest((measured if measured is not None else text).encode("utf-8")),
            "headings": headings(text) if source.path.endswith(".md") else []}


def _snapshot(root: Path) -> tuple[dict, str, str, list[str]]:
    """(بيانُ الفهرس، نصُّ AGENTS.md بكتلته الجديدة وبلا صفوفٍ منجزة، نصُّ الأرشيف بما نُقل إليه، أرقامُ ما نُقل). الكتلةُ تُبنى من الوثائق الأخرى ومن جدول §٣، ثم يُقاس AGENTS.md
    بنصّه البديل كما سيُقرأ (حجمًا وأسطرًا ورموزًا وعناوين)، وبصمتُه وحدها من النصّ المفرَّغ الكتلة؛ فلا دورَ، و--write ثابتٌ
    مهما كانت الكتلةُ القديمة (ملاحظتا Codex على #148)."""
    for source in SOURCES:
        if not (root / source.path).is_file():
            raise IndexError_(f"source_missing:{source.path}")
    agents_source = next(s for s in SOURCES if s.path == AGENTS)
    agents_text, archive_new, moved = archive_done((root / AGENTS).read_text(encoding="utf-8"),
                                                   (root / ARCHIVE).read_text(encoding="utf-8"))
    others = [_document(s, archive_new if s.path == ARCHIVE else (root / s.path).read_text(encoding="utf-8"))
              for s in SOURCES if s.path != AGENTS]
    decisions = (root / "docs/DECISIONS.md").read_text(encoding="utf-8")
    partial = {"documents": others, "open_tasks": open_tasks(agents_text), "latest_decision": latest_decision(decisions)}
    agents_new = with_block(agents_text, render_block(partial))
    agents_doc = _document(agents_source, agents_new, measured=_masked_agents(agents_new))
    state = {"schema_version": 1, "generator": "tools/context_index.py", "block": BLOCK,
             "codex_project_doc_max_bytes": CODEX_PROJECT_DOC_MAX_BYTES,
             "open_tasks": partial["open_tasks"], "latest_decision": partial["latest_decision"],
             "documents": [agents_doc, *others],
             "measurement_limits": [
                 "tokens_estimate_is_characters_divided_by_2_4_the_qwen3_5_9b_ratio_measured_in_docs_probe_context_budget_20260927_json_not_a_tokenizer_count",
                 "agents_md_is_measured_from_its_replacement_text_with_the_new_block_and_digested_with_the_block_emptied_so_the_block_cannot_change_the_digest_it_reports",
                 "the_index_describes_documents_by_their_text_and_never_judges_their_truth",
                 "documents_outside_SOURCES_are_not_indexed",
                 "no_git_history_is_read_so_the_output_is_identical_in_shallow_and_full_clones",
             ]}
    return state, agents_new, archive_new, moved


def describe(root: Path) -> dict:
    """بيانُ الفهرس من الوثائق وحدها؛ لا زمنَ ولا git."""
    return _snapshot(root)[0]


def _kb(n: int) -> str:
    return f"{n / 1024:.1f} ك.ب"


def render_md(state: dict) -> str:
    docs = {d["path"]: d for d in state["documents"]}
    # هذا الفهرسُ نفسُه ليس في المصادر، وحجمُه لا يُكتب داخله (لدارَ على نفسه)؛ فمجموعُ §٠ هنا بلا الفهرس، وبه في --print-budget
    counted = [p for p in READING_SET if p in docs]
    reading = sum(docs[p]["bytes"] for p in counted)
    lines = [
        "# فهرسُ السياق: ما تقرؤه وما لا تقرؤه",
        "",
        "<!-- مولَّد بـ`tools/context_index.py --write` من الوثائق نفسِها؛ لا يُحرَّر يدويًّا، و`--check` يفرض حداثته في CI -->",
        "",
        f"**كيف يُستعمل (ق٦٧-٥):** اقرأ `AGENTS.md` كاملًا ({_kb(docs[AGENTS]['bytes'])}، وهو دون حدِّ Codex "
        f"{_kb(state['codex_project_doc_max_bytes'])}) ثم هذا الفهرس، و`docs/VISION.md` في أول جلسةٍ لك. ولا تفتح وثيقةً أخرى إلا إن سمّاها",
        "الفهرسُ لمهمّتك، أو تغيّرت بصمتُها عمّا رأيتَه آخرَ مرّة؛ فالبصمةُ الثابتة تعني أن ما تعرفه عن الوثيقة ما زال صحيحًا.",
        f"حجمُ ما يُقرأ في §٠ **بلا هذا الفهرس** ({' و'.join(f'`{p}`' for p in counted)}): {_kb(reading)} "
        f"(≈{sum(docs[p]['tokens_estimate'] for p in counted)} رمزًا تقديرًا)؛ وبالفهرس معه يقوله `--print-budget`، لأن حجمَ الفهرس لا يُكتب داخله.",
        "",
        f"## المفتوحُ من جدول §٣ ({len(state['open_tasks'])})؛ وما سواه مسائلُ GitHub بوسم عائلتك",
        "",
        "| رقم | المسؤول | الحالة |",
        "|---|---|---|",
        *(f"| {t['id']} | {t['owner']} | {t['status']} |" for t in state["open_tasks"]),
        "",
        f"## الوثائق (آخرُ قرارٍ في السجلّ: ق{str(state['latest_decision']).translate(str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩'))})",
        "",
        "| الوثيقة | دورها | ما فيها | متى تُقرأ | الحجم | رموز≈ | البصمة |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in state["documents"]:
        lines.append(f"| `{d['path']}` | {d['role']} | {d['purpose']} | {d['read_when']} | {_kb(d['bytes'])} | {d['tokens_estimate']} | `{d['sha256_12']}` |")
    lines += ["", "## عناوينُ الوثائق (للقفز إلى الموضع، لا لقراءتها كلِّها)", ""]
    for d in state["documents"]:
        shown = [h for h in d["headings"] if h["level"] == 2]
        if not shown:
            continue
        lines.append(f"### `{d['path']}`")
        lines.append("")
        parts = [f"{h['line']}: {h['title']}" for h in shown[:HEADINGS_SHOWN]]
        more = len(shown) - HEADINGS_SHOWN
        if more > 0:
            parts.append(f"(+{more} في `docs/INDEX.json`)")
        lines.append(" · ".join(parts))
        lines.append("")
    lines += ["## حدودُ هذا الفهرس", ""]
    lines += [f"- `{limit}`" for limit in state["measurement_limits"]]
    return "\n".join(lines).rstrip() + "\n"


def render_block(state: dict) -> str:
    """كتلةُ §٠: أسطرٌ قليلة لقارئٍ لا يفتح غيرَ AGENTS.md."""
    docs = {d["path"]: d for d in state["documents"]}
    tasks = "، ".join(f"{t['id']} ({t['owner']}؛ {t['status'].split(' — ')[0].split(' (')[0].split('؛')[0]})"
                      for t in state["open_tasks"]) or "لا شيء"
    pinned = " · ".join(f"`{p}` `{docs[p]['sha256_12']}`" for p in
                        ("docs/STATUS.md", "docs/VISION.md", "docs/DECISIONS.md", "docs/PLAN-20260926.md"))
    return "\n".join([
        f"> **فهرسُ السياق** (`docs/INDEX.md`، مولَّد بـ`tools/context_index.py`؛ لا يُحرَّر يدويًّا). المفتوحُ من §٣: {tasks}.",
        f"> بصماتُ الوثائق الحاكمة: {pinned}؛ وآخرُ قرار ق{str(state['latest_decision']).translate(str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩'))}.",
        "> بصمةٌ تغيّرت عمّا رأيتَه في جلستك السابقة تعني أن الوثيقة تغيّرت فتُقرأ من الفهرس؛ وما لم يتغيّر لا يُعاد.",
    ])


def expected_files(root: Path) -> dict[Path, str]:
    return _expected(root)[0]


def _cut_line(raw: bytes) -> int | None:
    """السطرُ الذي يقع فيه حدُّ Codex، أو None إن كان الملفُّ كلُّه دونه."""
    if len(raw) <= CODEX_PROJECT_DOC_MAX_BYTES:
        return None
    return raw[:CODEX_PROJECT_DOC_MAX_BYTES].decode("utf-8", "ignore").count("\n") + 1


def probe_text(root: Path, state: dict, agents_new: str, archive_new: str, index_md: str) -> str | None:
    """قسمُ «بعد» في دليل القياس يُولَّد من اللقطة نفسِها التي تُكتب، فيصف دائمًا شجرةَ الإيداع الذي يحمله ولا يُحرَّر بيد
    (ملاحظات Codex على #148)؛ وقسمُ «قبل» تاريخٌ لا يُمسّ. None حين لا دليلَ في هذه النسخة."""
    path = root / PROBE
    if not path.is_file():
        return None
    probe = json.loads(path.read_text(encoding="utf-8"))
    docs = {d["path"]: d for d in state["documents"]}
    raw = agents_new.encode("utf-8")
    cut = _cut_line(raw)
    lines = agents_new.split("\n")
    section_start = next((i for i, line in enumerate(lines) if line.startswith("## ٣ — المهام")), len(lines))
    section_end = next((i for i in range(section_start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    row_lines = [i + 1 for i in range(section_start, section_end) if TASK_ROW.match(lines[i])]
    heading_lines = [i + 1 for i, line in enumerate(lines) if line.startswith("## ")]
    visible = (lambda numbers: len(numbers)) if cut is None else (lambda numbers: sum(1 for n in numbers if n < cut))
    index_raw = index_md.encode("utf-8")
    sizes = {AGENTS: (len(raw), tokens_estimate(agents_new)), INDEX_MD: (len(index_raw), tokens_estimate(index_md))}
    reading = {p: {"bytes": sizes[p][0] if p in sizes else docs[p]["bytes"],
                   "tokens_estimate": sizes[p][1] if p in sizes else docs[p]["tokens_estimate"]}
               for p in READING_SET if p in sizes or p in docs}
    after = probe.setdefault("after", {})
    after["AGENTS.md"] = {"bytes": len(raw), "lines": agents_new.count("\n"), "tokens_estimate": tokens_estimate(agents_new),
                          "codex_cut_line": cut, "task_rows": len(row_lines), "task_rows_visible_to_codex": visible(row_lines),
                          "sections": len(heading_lines), "sections_visible_to_codex": visible(heading_lines)}
    after["reading_set_in_section_0"] = reading
    after["reading_set_total"] = {"bytes": sum(v["bytes"] for v in reading.values()),
                                  "tokens_estimate": sum(v["tokens_estimate"] for v in reading.values())}
    after["archived_rows"] = sum(1 for line in archive_new.split("\n") if TASK_ROW.match(line))
    after["open_rows_in_agents_md"] = len(row_lines)
    after["regenerated_by"] = "tools/context_index.py --write"
    probe["every_indexed_document_after"] = {p: {"bytes": d["bytes"], "tokens_estimate": d["tokens_estimate"]} for p, d in docs.items()}
    probe["tool"] = "tools/context_index.py --write (قسمُ «بعد» يُولَّد مع الفهرس من اللقطة نفسِها؛ و--print-budget يقرأ القرص)"
    limit = "the_after_section_is_regenerated_by_context_index_write_from_the_same_snapshot_as_the_index_so_it_describes_the_tree_of_the_commit_that_carries_it_while_before_stays_the_113d1b4_measurement"
    limits = probe.setdefault("measurement_limits", [])
    if limit not in limits:
        limits.append(limit)
    return json.dumps(probe, ensure_ascii=False, indent=2) + "\n"


def _expected(root: Path) -> tuple[dict[Path, str], list[str]]:
    """(ما يجب أن تكون عليه الملفّاتُ المولَّدة، أرقامُ الصفوف المنجزة التي تنتظر النقلَ إلى الأرشيف)."""
    state, agents_new, archive_new, moved = _snapshot(root)
    index_md = render_md(state)
    expected = {root / INDEX_MD: index_md,
                root / INDEX_JSON: json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                root / AGENTS: agents_new, root / ARCHIVE: archive_new}
    probe = probe_text(root, state, agents_new, archive_new, index_md)
    if probe is not None:
        expected[root / PROBE] = probe
    return expected, moved


def budget(root: Path) -> dict:
    state = describe(root)
    docs = {d["path"]: d for d in state["documents"]}
    on_disk = (root / AGENTS).stat().st_size          # الحجمُ الحقيقي بالكتلة، وهو ما يراه Codex
    reading = {p: {"bytes": (on_disk if p == AGENTS else (root / p).stat().st_size),
                   "tokens_estimate": tokens_estimate((root / p).read_text(encoding="utf-8"))}
               for p in READING_SET if (root / p).is_file()}
    return {"schema_version": 1, "tool": "tools/context_index.py --print-budget",
            "codex_project_doc_max_bytes": CODEX_PROJECT_DOC_MAX_BYTES,
            "agents_md_bytes": on_disk, "agents_md_fits_codex": on_disk <= CODEX_PROJECT_DOC_MAX_BYTES,
            "open_task_rows_in_agents_md": len(state["open_tasks"]),
            "reading_set": reading,
            "reading_set_total": {"bytes": sum(v["bytes"] for v in reading.values()),
                                  "tokens_estimate": sum(v["tokens_estimate"] for v in reading.values())},
            "every_indexed_document": {p: {"bytes": d["bytes"], "tokens_estimate": d["tokens_estimate"]} for p, d in docs.items()},
            "measurement_limits": state["measurement_limits"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--write", action="store_true")
    group.add_argument("--print-budget", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        if args.print_budget:
            print(json.dumps(budget(args.root), ensure_ascii=False, indent=1, sort_keys=True))
            return 0
        expected, moved = _expected(args.root)
        stale = [path for path, value in expected.items()
                 if not path.exists() or path.read_text(encoding="utf-8") != value]
        if args.write:
            for path in stale:
                path.write_text(expected[path], encoding="utf-8")
        elif stale:
            print(json.dumps({"status": "index_stale", "files": [p.relative_to(args.root).as_posix() for p in stale],
                              "done_rows_to_archive": moved}, ensure_ascii=False))
            return 1
        print(json.dumps({"status": "updated" if args.write else "verified", "written": [p.relative_to(args.root).as_posix() for p in stale]
                          if args.write else [], "archived": moved if args.write else [],
                          "agents_md_bytes": (args.root / AGENTS).stat().st_size}, ensure_ascii=False))
        return 0
    except IndexError_ as exc:
        print(json.dumps({"status": "error", "code": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
