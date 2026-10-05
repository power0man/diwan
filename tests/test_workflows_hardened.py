"""تصليبُ GitHub Actions في كلِّ سير (جديد-actions-hardening ١، الخطة §٣).

كلُّ ملفٍّ في `.github/workflows/` يُعلن صلاحياته في أعلاه، فلا يرث صلاحياتِ المستودع الافتراضيّة. ولا يُشغَّل بـ`pull_request_target`
الذي يعطي شيفرةَ الطلب أسرارَ المستودع وصلاحيةَ الكتابة. وكلُّ فعلٍ خارجيٍّ (`uses:`) مثبَّتٌ ببصمة إيداعٍ كاملة، لا بوسمٍ يتحرّك.
وكلُّ صلاحيةٍ فيه، في أعلاه أو في مهمّة، في جدول `LEAST` بنطاقها ومستواها، فلا يمرّ `write-all` ولا نطاقٌ أوسع (ملاحظة Codex
على #297). الفحصُ نصّيٌّ على الملفات المودَعة، لا قراءةٌ لـYAML عامّة؛ وكلُّ اختبارٍ يجمع المخالفاتِ في الملفات كلِّها فيسمّيها معًا.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.M)
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
PERMISSIONS = re.compile(r"^( *)permissions:[ \t]*(.*)$", re.M)
SCOPE = re.compile(r"^( *)([\w-]+):[ \t]*(\S+)[ \t]*(?:#.*)?$")


def _grants(text: str) -> list[dict | str]:
    """كلُّ كتلة `permissions:` في الملف، في أعلاه أو في مهمّة: قاموسُ نطاقاتها، أو نصُّها إن لم تكن قائمةً ولا `{}`."""
    lines, found = text.splitlines(), []
    for match in PERMISSIONS.finditer(text):
        indent, inline = len(match[1]), match[2].split("#")[0].strip()
        if inline:
            found.append({} if inline == "{}" else inline)
            continue
        start, block = text[:match.start()].count("\n") + 1, {}
        for line in lines[start:]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            scope = SCOPE.match(line)
            if len(line) - len(line.lstrip()) <= indent or not scope:
                break
            block[scope[2]] = scope[3]
        found.append(block)
    return found


def _texts():
    return {path.name: path.read_text(encoding="utf-8") for path in WORKFLOWS}


def test_every_workflow_declares_its_permissions_at_the_top():
    assert len(WORKFLOWS) >= 10, [p.name for p in WORKFLOWS]     # النمطُ يجد الملفاتِ فعلًا
    assert [name for name, text in _texts().items() if not re.search(r"^permissions:", text, re.M)] == []


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
    unpinned = [(name, action) for name, text in _texts().items() for action in USES.findall(text)
                if not action.startswith("./") and not PINNED.fullmatch(action)]
    assert unpinned == []
