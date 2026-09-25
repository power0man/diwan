"""دليل القبول يفحص الإحالات في النص، لا الشواهد المفلترة وحدها."""
from types import SimpleNamespace

import pytest

from acceptance_m4 import evidence_matches_answer


@pytest.mark.parametrize("text,refs,expected", [
    ("معنى [ش١] وشرط [ش3].", [1, 3], True),
    ("معنى [ش1] وخاطئ [ش14] [ش15].", [1], False),
    ("معنى [ش١] وخاطئ [ش١٥].", [1], False),
    ("نص بلا إحالة", [], False),
    ("معنى [ش1].", [1, 1], False),
    ("معنى [ش1].", [1, 2], False),
    ("معنى [ش1] وخاطئ [ش-1].", [1], False),
    ("معنى [ش1] وخاطئ [ش1.5].", [1], False),
    ("معنى [ش1] وخاطئ [ش15", [1], False),
    ("معنى [ش۱].", [1], True),
    ("معنى [ش１].", [1], True),
])
def test_acceptance_checks_every_citation(text, refs, expected):
    catalog = SimpleNamespace(find_page=lambda root, dg: {"item_digest": dg})
    evidence = [{"ref": ref, "item_digest": str(ref)} for ref in refs]
    assert evidence_matches_answer(text, evidence, catalog, None) is expected


def test_acceptance_rejects_missing_source_page():
    catalog = SimpleNamespace(find_page=lambda root, dg: None)
    assert not evidence_matches_answer(
        "معنى [ش1].", [{"ref": 1, "item_digest": "absent"}], catalog, None)
