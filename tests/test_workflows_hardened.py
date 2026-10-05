"""تصليبُ GitHub Actions في كلِّ سير (جديد-actions-hardening ١، الخطة §٣).

كلُّ ملفٍّ في `.github/workflows/` يُعلن صلاحياته في أعلاه، فلا يرث صلاحياتِ المستودع الافتراضيّة. ولا يُشغَّل بـ`pull_request_target`
الذي يعطي شيفرةَ الطلب أسرارَ المستودع وصلاحيةَ الكتابة. وكلُّ فعلٍ خارجيٍّ (`uses:`) مثبَّتٌ ببصمة إيداعٍ كاملة، لا بوسمٍ يتحرّك.
وكلُّ صلاحيةٍ فيه، في أعلاه أو في مهمّة، في جدول `LEAST` بنطاقها ومستواها، فلا يمرّ `write-all` ولا نطاقٌ أوسع (ملاحظة Codex
على #297). الفحصُ نصّيٌّ على الملفات المودَعة، لا قراءةٌ لـYAML عامّة؛ فهو مغلقٌ عند الشكّ: كلُّ سطرٍ يرد فيه `permissions` أو `uses`
مفتاحًا بأيّ صورة (عاريًا، أو بين علامتين، أو في خريطةٍ مضمَّنة) يُقرأ بصورته المعتمدة وحدها، وما سواها مخالفةٌ لا تجاوز
(ملاحظتا Codex على #297). وكي لا يُلتفّ على القراءة بصورةٍ أخرى للمفتاح (`"permiss\\u0069ons"`، أو وسم، أو مرساة)، فكلُّ مفاتيح
السير عاريةٌ خارج محتوى الكتل النصّية (`run: |`)، وما سواها مخالفة (ملاحظة Codex الثالثة على #297). وكلُّ اختبارٍ يجمع المخالفاتِ
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


BLOCK_SCALAR = re.compile(r":[ \t]*[|>][+-]?[0-9]?[ \t]*(?:#.*)?$")
# كلُّ صورةٍ لمفتاحٍ غيرِ عارٍ، أو لقيمةٍ تُخفي مفاتيحَ عن قراءة السطر؛ فما يقرؤه الحارسان هو ما يقرؤه GitHub
NOT_PLAIN = {
    "quoted_key": re.compile(r"""^[ \t]*(?:-[ \t]+)?(["']).*?(?<!\\)\1[ \t]*:(?:[ \t]|$)"""),
    # خريطةٌ مضمَّنة في أوّل السطر، أو بعد `- ` أو `: ` أو فاصلةٍ في مجموعةٍ ممتدّة على أسطر، أو داخل `[...]`
    "flow_mapping": re.compile(r"(?:^[ \t]*(?:-[ \t]+)?|:[ \t]+|,[ \t]*)\{(?!\}[ \t]*(?:#.*)?$)|\[[^\]]*\{"),
    "explicit_key": re.compile(r"^[ \t]*(?:-[ \t]+)?\?(?:[ \t]|$)"),
    "tag_anchor_alias": re.compile(r"(?:^[ \t]*(?:-[ \t]+)?|:[ \t]+)[!&*]"),
    "merge_key": re.compile(r"(?:^|[ \t])<<[ \t]*:"),
}


def _structure(text: str) -> list[tuple[int, str]]:
    """أسطرُ البنية وحدها: بلا تعليقٍ ولا محتوى كتلةٍ نصّية (`run: |`)، فما في الشيفرة من أقواسٍ وعلاماتٍ نصٌّ لا مفاتيح."""
    found, block = [], None
    for number, line in enumerate(text.splitlines(), 1):
        stripped, indent = line.strip(), len(line) - len(line.lstrip())
        if block is not None:
            if not stripped or indent > block:
                continue
            block = None
        if not stripped or stripped.startswith("#"):
            continue
        found.append((number, line))
        if BLOCK_SCALAR.search(line):
            block = indent
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
    """ملاحظة Codex الثالثة على #297: `"permiss\\u0069ons": write-all` أو `"us\\u0065s": …` يفكّه قارئُ YAML إلى المفتاح، ولا يراه
    حارسٌ يقرأ النصّ. فبدل ملاحقة كلِّ صورة، المفاتيحُ في أسطر البنية عاريةٌ كلُّها: لا علامتي تنصيص، ولا خريطةٌ مضمَّنة غيرُ `{}`،
    ولا مفتاحٌ صريحٌ (`? `)، ولا وسمٌ أو مرساةٌ أو اسمٌ مستعار، ولا مفتاحُ دمج (`<<`)."""
    found = [(name, number, rule) for name, text in _texts().items() for number, line in _structure(text)
             for rule, pattern in NOT_PLAIN.items() if pattern.search(line)]
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
