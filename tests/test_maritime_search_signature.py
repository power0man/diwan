"""توقيعُ دالّة البحث يُقرأ قبل النداء، فـTypeError من داخل البحث عطبٌ يُسمّى لا مجسُّ توقيع (مسحُ الإخفاقات الصامتة، مسار google بتسليم)."""
from __future__ import annotations

import pytest

from nodes.maritime.node import MaritimeNode, RetrievalFailed


def _node(search_fn):
    node = MaritimeNode.__new__(MaritimeNode)
    node.search = search_fn
    node.top_k = 7
    return node


def test_a_type_error_inside_a_modern_search_is_a_retrieval_failure_not_a_signature_probe():
    calls = []

    def modern(q, limit=10, match_any=False):
        calls.append(q)
        raise TypeError("'NoneType' object is not subscriptable")
    with pytest.raises(RetrievalFailed) as exc:
        _node(modern)._search("سفينة", limit=10, match_any=True)
    assert "TypeError" in str(exc.value) and calls == ["سفينة"], "أُعيد النداءُ كأن الخطأَ مجسُّ توقيع"


def test_a_legacy_search_without_match_any_is_called_by_its_own_signature():
    seen = []

    def legacy(q, limit=10):
        seen.append((q, limit))
        return [{"doc_id": "x"}]
    assert _node(legacy)._search("سفينة", limit=3, match_any=True) == [{"doc_id": "x"}]
    assert seen == [("سفينة", 3)]
