"""تصليبُ GitHub Actions في كلِّ سير (جديد-actions-hardening ١، الخطة §٣).

كلُّ ملفٍّ في `.github/workflows/` يُعلن صلاحياته في أعلاه، فلا يرث صلاحياتِ المستودع الافتراضيّة. ولا يُشغَّل بـ`pull_request_target`
الذي يعطي شيفرةَ الطلب أسرارَ المستودع وصلاحيةَ الكتابة. وكلُّ فعلٍ خارجيٍّ (`uses:`) مثبَّتٌ ببصمة إيداعٍ كاملة، لا بوسمٍ يتحرّك.
الفحصُ نصّيٌّ على الملفات المودَعة، لا قراءةٌ لـYAML عامّة؛ وكلُّ اختبارٍ يجمع المخالفاتِ في الملفات كلِّها فيسمّيها معًا.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.M)
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")


def _texts():
    return {path.name: path.read_text(encoding="utf-8") for path in WORKFLOWS}


def test_every_workflow_declares_its_permissions_at_the_top():
    assert len(WORKFLOWS) >= 10, [p.name for p in WORKFLOWS]     # النمطُ يجد الملفاتِ فعلًا
    assert [name for name, text in _texts().items() if not re.search(r"^permissions:", text, re.M)] == []


def test_no_workflow_runs_on_pull_request_target():
    assert [name for name, text in _texts().items() if "pull_request_target" in text] == []


def test_every_external_action_is_pinned_to_a_full_commit():
    unpinned = [(name, action) for name, text in _texts().items() for action in USES.findall(text)
                if not action.startswith("./") and not PINNED.fullmatch(action)]
    assert unpinned == []
