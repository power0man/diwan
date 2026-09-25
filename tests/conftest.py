import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolate_signing_env(monkeypatch):
    """عزلُ بيئة التوقيع عن كل اختبار — علمُ الصرامة ومفتاحُ البيئة
    كانا يتسربان من صدفة المشغِّل فتصير خضرةُ الاختبارات مصادفةَ بيئة
    (تدقيق ق٢٥). من يحتاجهما يرفعهما بنفسه."""
    monkeypatch.delenv("DIWAN_REQUIRE_SIGNATURE", raising=False)
    monkeypatch.delenv("DIWAN_ANCHOR_KEY", raising=False)


@pytest.fixture(scope="session", autouse=True)
def _ensure_fts_projections():
    """ضمان وجود إسقاطات FTS للبيئات المعزولة والنظيفة (مثل CI وحاويات Docker)."""
    root = Path(__file__).resolve().parents[1]
    m_db = root / "projections" / "maritime-fts.sqlite"
    l_db = root / "projections" / "lexicons-fts.sqlite"
    # اللقطةُ العامة بلا متون (ك٢٧): لا إسقاطَ يُبنى لمخزنٍ غائب، واختباراتُ المتن تتخطّى باسمها
    m_cat = root / "corpus" / "maritime" / "_catalog.jsonl"
    l_cat = root / "corpus" / "lexicons" / "_catalog.jsonl"
    if (not m_db.exists() and m_cat.exists()) or (not l_db.exists() and l_cat.exists()):
        sys.path.insert(0, str(root / "tools"))
        import rebuild_index as ri
        if not m_db.exists() and m_cat.exists():
            ri.rebuild("maritime")
        if not l_db.exists() and l_cat.exists():
            ri.rebuild("lexicons")

