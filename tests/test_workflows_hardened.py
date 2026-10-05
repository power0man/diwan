"""تصليبُ GitHub Actions في كلِّ سير (جديد-actions-hardening ١، الخطة §٣).

كلُّ ملفٍّ في `.github/workflows/` يُعلن صلاحياته في أعلاه، فلا يرث صلاحياتِ المستودع الافتراضيّة. ولا يُشغَّل بـ`pull_request_target`
الذي يعطي شيفرةَ الطلب أسرارَ المستودع وصلاحيةَ الكتابة. وكلُّ فعلٍ خارجيٍّ (`uses:`) مثبَّتٌ ببصمة إيداعٍ كاملة، لا بوسمٍ يتحرّك.
وكلُّ صلاحيةٍ فيه، في أعلاه أو في مهمّة، في جدول `LEAST` بنطاقها ومستواها، فلا يمرّ `write-all` ولا نطاقٌ أوسع (ملاحظة Codex
على #297). الفحصُ نصّيٌّ على الملفات المودَعة، لا قراءةٌ لـYAML عامّة؛ فهو مغلقٌ عند الشكّ: كلُّ سطرٍ يرد فيه `permissions` أو `uses`
مفتاحًا بأيّ صورة (عاريًا، أو بين علامتين، أو في خريطةٍ مضمَّنة) يُقرأ بصورته المعتمدة وحدها، وما سواها مخالفةٌ لا تجاوز
(ملاحظتا Codex على #297). وكي لا يُلتفّ على القراءة بصورةٍ أخرى للمفتاح، فأسطرُ البنية كلُّها في نحوٍ
محدودٍ مثبَت (مفاتيحُ عارية وقيمٌ مفردة)، وما خرج عنه مخالفة (مراجعاتُ Codex على #297). وكلُّ اختبارٍ يجمع المخالفاتِ
في الملفات كلِّها فيسمّيها معًا.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
CANONICAL_USES = re.compile(r"^[ \t]*(?:-[ \t]+)?uses:[ \t]*(\S+)[ \t]*(?:#.*)?$")
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")


# أقلُّ الصلاحيات لكل سير، وسببُ كلِّ كتابة. وتوسيعُها تعديلٌ في هذا الجدول يراه المراجع، لا في ملفّ السير وحده.
LEAST = {
    "analysis.yml": {"contents": "read"},
    "container-smoke.yml": {"contents": "read"},
    "export-pdf.yml": {"contents": "read"},
    "family-review-recheck.yml": {"actions": "write", "pull-requests": "read"},  # يعيد تشغيلَ family-review على الرأس
    "family-review.yml": {"contents": "read", "pull-requests": "read"},
    "free-llm-review.yml": {"contents": "read", "models": "read"},
    "issue-ledger.yml": {"contents": "read", "issues": "write"},             # يعيد فتحَ المسألة بلا إثبات ويَسِمها
    "labels.yml": {"contents": "read", "issues": "write"},                   # يزامن الوسومَ والمراحل
    "mutation-check.yml": {"contents": "read"},
    "runner-canary.yml": {},
    "verify-hosted.yml": {"contents": "read"},
    "verify.yml": {"contents": "read"},
}
LEVEL = {"none": 0, "read": 1, "write": 2}
CANONICAL_PERMISSIONS = re.compile(r"^( *)permissions:[ \t]*(\{\})?[ \t]*(?:#.*)?$")
GRANT = re.compile(r"^ +([a-z-]+):[ \t]+(read|write|none)[ \t]*(?:#.*)?$")


# نحوٌ محدودٌ لأسطر البنية، وما خرج عنه مخالفة (مراجعاتُ Codex على #297): ملاحقةُ صور YAML واحدةً واحدة لا تنتهي — مفتاحٌ
# بين علامتين، ومهروب، وصريح، ووسمٌ ومرساة، وخريطةٌ مضمَّنة بأقواس، وزوجٌ مضغوطٌ بلا أقواس داخل `[...]`. فكلُّ سطرٍ بنيويّ إمّا
# `مفتاحٌ_عارٍ: قيمة` أو عنصرٌ `- قيمة`، والقيمةُ: فارغة، أو `{}`، أو مؤشّرُ كتلة (`|`، `>-`…)، أو مفردةٌ مقتبسةٌ تامّة، أو
# مفردةٌ عاديّةٌ لا تبدأ بمؤشّرٍ ولا فيها `: `، أو `[…]` عناصرُها مفرداتٌ كذلك. والمجموعةُ الممتدّة على أسطر سطرٌ منطقيٌّ واحد.
KEY = re.compile(r"([A-Za-z0-9_.-]+):(?=[ \t]|$)")
QUOTED = re.compile(r"""("(?:[^"\\]|\\.)*"|'(?:[^']|'')*')""")
BLOCK = re.compile(r"[|>][+-]?[0-9]?")
INDICATORS = set("[]{}#&*!|>'\"%@`,?:-")


def _scalar_problem(text: str) -> str | None:
    if not text:
        return "empty_scalar"
    if QUOTED.fullmatch(text):
        # الهروبُ في المقتبس المزدوج (`\\u0065`) يجعل النصَّ الخامَ غيرَ ما يفكّه قارئُ YAML، فيعمى عنه فحصُ النصّ
        # (`on: ["pull_requ\\u0065st_target"]`، مراجعة Codex على 2b92bce)؛ وبلا هروبٍ فالخامُ هو المفكوك
        return "escape_in_quoted" if text.startswith('"') and "\\" in text else None
    if text[0] in INDICATORS and not (text[0] in "-?:" and len(text) > 1 and not text[1].isspace()):
        return "indicator_scalar"
    if ": " in text or text.endswith(":") or "\t" in text:
        return "mapping_in_scalar"
    return None


def _value(value: str) -> tuple[str, str | None]:
    """القيمةُ بلا تعليقها، ومشكلتُها إن كان بعد المقتبس التامّ غيرُ تعليق. و`مفتاح: # تعليق` قيمتُه فارغة."""
    if value.startswith("#"):
        return "", None
    quoted = QUOTED.match(value)
    if quoted:
        rest = value[quoted.end():]
        return quoted[0], ("text_after_quoted" if rest.strip() and not re.match(r"[ \t]+#", rest) else None)
    return re.split(r"[ \t]#", value, maxsplit=1)[0].rstrip(), None


def _value_problem(raw: str) -> str | None:
    value, problem = _value(raw)
    if problem or value in ("", "{}") or BLOCK.fullmatch(value):
        return problem
    if value.startswith("["):
        if not value.endswith("]"):
            return "open_flow"
        inner = value[1:-1].strip()
        return next((p for p in map(_scalar_problem, [i.strip() for i in inner.split(",")] if inner else []) if p), None)
    return _scalar_problem(value)


def _parse(line: str) -> tuple[str | None, int, str]:
    """(المشكلة، موضعُ المفتاح أو العنصر، القيمةُ الخام) لسطرٍ بنيويّ."""
    lead = len(line) - len(line.lstrip(" "))
    if "\t" in line[:lead + 1]:
        return "tab_indent", lead, ""
    rest, item = line[lead:], False
    while rest.startswith("- ") or rest == "-":
        rest, item = (rest[2:].lstrip(" ") if rest != "-" else ""), True
    column = len(line) - len(rest)
    key = KEY.match(rest)
    if key:
        raw = rest[key.end():].strip()
        return _value_problem(raw), column, raw
    if item:
        return (_value_problem(rest) if rest else None), column, rest
    return "not_a_key_or_item", column, rest


def _structure(text: str) -> list[tuple[int, str, str | None]]:
    """أسطرُ البنية منطقيًّا ومشكلةُ كلٍّ منها: بلا تعليقٍ ولا محتوى كتلةٍ نصّية (ما زاد على موضع مفتاحها)، والمجموعةُ `[...]`
    الممتدّة على أسطر سطرٌ واحد."""
    lines, found, block, index = text.splitlines(), [], None, 0
    while index < len(lines):
        number, line = index + 1, lines[index]
        index += 1
        stripped, indent = line.strip(), len(line) - len(line.lstrip(" "))
        if block is not None:
            if not stripped or indent > block:
                continue
            block = None
        if not stripped or stripped.startswith("#"):
            continue
        problem, column, raw = _parse(line)
        while problem == "open_flow" and index < len(lines):
            more = lines[index].strip()
            index += 1
            if more and not more.startswith("#"):
                line = line.rstrip() + " " + re.split(r"[ \t]#", more, maxsplit=1)[0].strip()
                problem, column, raw = _parse(line)
        found.append((number, line, problem))
        if problem is None and BLOCK.fullmatch(_value(raw)[0]):
            block = column
    return found


def _keys(text: str, name: str) -> list[tuple[int, str]]:
    """كلُّ سطرٍ غيرِ تعليقٍ يرد فيه `name` مفتاحًا بأيّ صورة: عاريًا، أو بين علامتي تنصيص، أو داخل خريطةٍ مضمَّنة."""
    key = re.compile(r"(?:^|[\s{,\[])([\"']?)" + name + r"\1[ \t]*:")
    return [(number, line) for number, line in enumerate(text.splitlines(), 1)
            if not line.lstrip().startswith("#") and key.search(line)]


def _grants(text: str) -> list[dict | str]:
    """كلُّ كتلة صلاحياتٍ في الملف، في أعلاه أو في مهمّة: قاموسُ نطاقاتها، أو نصُّ السطر الذي لم يُقرأ بالصورة المعتمدة
    (`write-all`، أو مفتاحٌ بين علامتين، أو خريطةٌ مضمَّنة، أو نطاقٌ بقيمةٍ غير read/write/none)."""
    lines, found = text.splitlines(), []
    for number, line in _keys(text, "permissions"):
        block = CANONICAL_PERMISSIONS.match(line)
        if not block:
            found.append(line.strip())
            continue
        grants: dict | str = {}
        for inner in [] if block[2] else lines[number:]:
            if not inner.strip() or inner.lstrip().startswith("#"):
                continue
            if len(inner) - len(inner.lstrip()) <= len(block[1]):
                break
            grant = GRANT.match(inner)
            if not grant:
                grants = inner.strip()
                break
            grants[grant[1]] = grant[2]
        found.append(grants)
    return found


def _texts():
    return {path.name: path.read_text(encoding="utf-8") for path in WORKFLOWS}


def test_every_workflow_declares_its_permissions_at_the_top():
    assert len(WORKFLOWS) >= 10, [p.name for p in WORKFLOWS]     # النمطُ يجد الملفاتِ فعلًا
    assert [name for name, text in _texts().items() if not re.search(r"^permissions:", text, re.M)] == []


def test_every_workflow_key_is_plain_so_the_guards_read_what_github_reads():
    """مراجعاتُ Codex على #297: ما يفكّه قارئُ YAML ولا يراه حارسٌ يقرأ النصّ — `"permiss\\u0069ons": write-all`، و`? uses`،
    و`steps: ["us\\u0065s": …]`، ووسمٌ أو مرساة — يمرّر صلاحيةً أو فعلًا. فأسطرُ البنية كلُّها في النحو المحدود أعلاه، وما خرج
    عنه مخالفةٌ باسم سطرها؛ فما يقرؤه الحارسان (الصلاحياتُ والأفعال) هو ما يقرؤه GitHub."""
    found = [(name, number, problem) for name, text in _texts().items()
             for number, _line, problem in _structure(text) if problem]
    assert found == []


def test_every_permission_is_within_the_least_listed_for_its_workflow():
    """ملاحظة Codex على #297: وجودُ المفتاح لا يكفي، فـ`permissions: write-all` يمرّ به. فكلُّ سيرٍ في الجدول، وكلُّ كتلةٍ فيه
    (في أعلاه أو في مهمّة) قائمةٌ نطاقاتُها فيه بمستوى لا يعلوه؛ والكتلةُ نصًّا (`write-all`، `read-all`) مخالفة."""
    texts = _texts()
    assert sorted(texts) == sorted(LEAST)
    broader = []
    for name, text in texts.items():
        for grant in _grants(text):
            if isinstance(grant, str):
                broader.append((name, grant))
                continue
            broader += [(name, scope, level) for scope, level in grant.items()
                        if LEVEL.get(level, 3) > LEVEL[LEAST[name].get(scope, "none")]]
    assert broader == []


def test_no_workflow_runs_on_pull_request_target():
    assert [name for name, text in _texts().items() if "pull_request_target" in text] == []


def test_every_external_action_is_pinned_to_a_full_commit():
    """وفعلٌ في خريطةٍ مضمَّنة (`- {uses: …@v4}`) أو بمفتاحٍ بين علامتين لا يُقرأ بالصورة المعتمدة، فهو مخالفةٌ لا تجاوز.

    والمرجعُ المحلّيّ (`./…`) مقبولٌ إلى سيرٍ قابلٍ لإعادة الاستعمال في `.github/workflows/` وحده، فهو ممّا تفحصه هذه الحرّاس.
    أمّا الفعلُ المركّب المحلّيّ (`./.github/actions/x`) فملفُّ `action.yml` فيه قد يستدعي فعلًا بوسمٍ يتحرّك ولا يقرؤه الحارس
    (ملاحظة Codex على #297)؛ ولا فعلَ مركّبًا في المستودع اليوم، فيُرفض حتى يُضاف معه فحصُ ملفّه."""
    scanned = {f".github/workflows/{path.name}" for path in WORKFLOWS}
    unpinned, seen = [], 0
    for name, text in _texts().items():
        for number, line in _keys(text, "uses"):
            seen += 1
            use = CANONICAL_USES.match(line)
            action = use[1] if use else line.strip()
            allowed = action[2:] in scanned if action.startswith("./") else PINNED.fullmatch(action)
            if not use or not allowed:
                unpinned.append((name, number, action))
    assert seen >= len(WORKFLOWS) and unpinned == []
