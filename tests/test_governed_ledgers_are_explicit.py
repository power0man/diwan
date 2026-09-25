"""السجلاتُ الحاكمة لبيانات المالك لا تُنشأ ضمنًا في جذر المستودع (ك٢٧).

كشفه تشغيلُ المجموعة داخل اللقطة العامة: العقدُ والخدماتُ كانت تبني `Ledger(path)`
على مسارات الجذر الحاكمة، فتُنشئ فهرسًا ومسردًا وسجلَّ مصادرٍ فارغةً بلا مراسٍ حين
تكون المتونُ غائبة، فتسقط بعدها فحوصُ التوقيع والوثائق برسالة «لا مرساة». الإنشاءُ
الآن فعلٌ صريح (`create=True`) لأدوات البذر وحدها، والجذرُ يُحاكى هنا بمجلّدٍ مؤقّت.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import ledger as ledger_module
from core.acquisitions import SourceRegister
from core.budget import Budget
from core.corpus import CorpusCatalog
from core.glossary import Glossary
from core.ledger import Ledger
from providers.echo import EchoProvider


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "REPO_ROOT", tmp_path.resolve())
    return tmp_path


@pytest.mark.parametrize("rel", [
    "sources/acquisitions.jsonl", "glossaries/maritime.jsonl", "rulings/precedence.jsonl",
    "corpus/maritime/_catalog.jsonl", "corpus/lexicons/_catalog.jsonl", "registry/nodes.jsonl",
    "publish/_manifest.jsonl",
])
def test_a_governed_ledger_is_created_only_explicitly_at_the_repo_root(repo_root, rel):
    path = repo_root / rel
    Ledger(path)
    assert not path.exists(), "الإنشاءُ الضمني مرفوض"
    Ledger(path, create=False)
    assert not path.exists()
    Ledger(path, create=True)
    assert path.exists(), "الإنشاءُ الصريح فعلُ البذر"


@pytest.mark.parametrize("rel", [
    "ledger/main.jsonl", "streams/maritime/inbox.jsonl", "var/runs/anything.jsonl",
])
def test_runtime_state_ledgers_are_still_created_on_demand(repo_root, rel):
    path = repo_root / rel
    Ledger(path)
    assert path.exists()


def test_the_same_governed_name_outside_the_root_is_created_as_before(repo_root, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "sources" / "acquisitions.jsonl"
    Ledger(elsewhere)
    assert elsewhere.exists(), "المسارُ خارج الجذر ليس سجلًّا حاكمًا"


def test_the_typed_wrappers_pass_the_default_through(repo_root):
    SourceRegister(repo_root / "sources" / "acquisitions.jsonl")
    Glossary(repo_root / "glossaries" / "maritime.jsonl")
    CorpusCatalog(repo_root / "corpus" / "maritime" / "_catalog.jsonl")
    for rel in ("sources/acquisitions.jsonl", "glossaries/maritime.jsonl", "corpus/maritime/_catalog.jsonl"):
        assert not (repo_root / rel).exists(), rel
    SourceRegister(repo_root / "sources" / "acquisitions.jsonl", create=True)
    assert (repo_root / "sources" / "acquisitions.jsonl").exists()


def test_building_the_maritime_node_leaves_no_empty_catalog_behind(repo_root):
    from nodes.maritime.node import MaritimeNode
    MaritimeNode(repo_root, EchoProvider(), Budget(10, 10), Ledger(repo_root / "var" / "calls.jsonl"),
                 search_fn=lambda q, limit=10, match_any=False: [])
    assert not (repo_root / "corpus" / "maritime" / "_catalog.jsonl").exists()
