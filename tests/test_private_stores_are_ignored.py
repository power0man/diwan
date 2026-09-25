"""مخازنُ المالك الخاصة لا تدخل المستودعَ العام ولو وُضعت محليًّا (ك٢٩).

في نسخة اللقطة العامة (التي تحمل `PUBLIC-EXPORT.json`) كلُّ مسارٍ في
`tools/export_public.py::EXCLUDED_PATHS` متجاهَلٌ في git، فوضعُ المخازن محليًّا
بـ`tools/place_private_stores.py` لا يعرّضها للدفع. والأداةُ نفسُها لا تضع ولا ترفع إلا تحت
العلامة، ولا تنسخ إلا المخازن، ولا ترفع إلا ما وضعته. في المستودع الخاص (بلا علامة)
تتخطّى اختباراتُ التجاهل لأنه يتتبّع مخازنه عمدًا.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import export_public as ex  # noqa: E402
import place_private_stores as pps  # noqa: E402
from core.public_export import MARKER_NAME, read_marker  # noqa: E402

# عيّنةٌ لكل دليلٍ ليست ملفَّ jsonl، حتى لا تُخفي قاعدةُ `*.jsonl` العامة غيابَ قاعدة الدليل
SAMPLES = {
    "corpus/": "corpus/maritime/_catalog.jsonl.anchor",
    "glossaries/": "glossaries/maritime.jsonl.anchor",
    "sources/": "sources/acquisitions.jsonl.anchor",
    "publish/": "publish/_manifest.jsonl.anchor",
}
public_only = pytest.mark.skipif(read_marker(ROOT) is None,
                                 reason="المستودعُ الخاص يتتبّع مخازنه عمدًا؛ الحارسُ للقطة العامة")


def _ignored(rel: str) -> bool:
    return subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", rel]).returncode == 0


@public_only
@pytest.mark.parametrize("entry", ex.EXCLUDED_PATHS)
def test_every_private_store_path_is_ignored_in_the_public_checkout(entry):
    sample = SAMPLES.get(entry, entry)
    assert _ignored(sample), f"{sample} غيرُ متجاهَل، فقد يدخل المستودعَ العام بالخطأ"


@public_only
@pytest.mark.parametrize("rel", ex.INCLUDED_PATHS)
def test_the_included_dictionary_is_tracked_not_ignored(rel):
    assert not _ignored(rel), f"{rel}: مضمَّنٌ بق٥٨ ويجب أن يُتتبَّع"


@public_only
@pytest.mark.parametrize("rel", ["corpus/lexicons/mujam-wasit.jsonl", "corpus/lexicons/_catalog.jsonl",
                                 "corpus/lexicons/_catalog.jsonl.anchor.sig", "corpus/lexicons-local/x.jsonl"])
def test_the_rest_of_the_lexicon_store_stays_ignored(rel):
    assert _ignored(rel), rel


@public_only
def test_the_node_registry_ledger_is_tracked_not_ignored():
    assert not _ignored("registry/nodes.jsonl"), "سجلُّ العقد مصدرٌ يُدفع لا مخزنًا خاصًّا"


def _public_root(tmp_path):
    root = tmp_path / "pub"
    root.mkdir()
    (root / MARKER_NAME).write_text(json.dumps({"kind": "public_export", "excluded_streams": []}))
    return root


def _source(tmp_path):
    src = tmp_path / "private"
    (src / "corpus" / "maritime").mkdir(parents=True)
    (src / "corpus" / "maritime" / "_catalog.jsonl").write_text("{}\n")
    (src / "glossaries").mkdir()
    (src / "glossaries" / "maritime.jsonl").write_text("{}\n")
    (src / "core").mkdir()
    (src / "core" / "x.py").write_text("X = 1\n")
    return src


def test_placing_copies_the_private_stores_and_nothing_else(tmp_path):
    root = _public_root(tmp_path)
    report = pps.place(root, _source(tmp_path), verify=False)
    assert report["status"] == "placed"
    assert (root / "corpus" / "maritime" / "_catalog.jsonl").is_file()
    assert (root / "glossaries" / "maritime.jsonl").is_file()
    assert not (root / "core" / "x.py").exists(), "لا يُنسخ إلا المخازن"
    assert set(report["placed"]) == {"corpus", "glossaries"}
    assert "sources" in report["missing"] and "publish" in report["missing"]


def test_placing_and_removing_refuse_outside_a_public_checkout(tmp_path):
    root = tmp_path / "private_like"
    (root / "corpus").mkdir(parents=True)
    (root / "corpus" / "keep.jsonl").write_text("x")
    assert pps.place(root, _source(tmp_path), verify=False)["code"] == "not_a_public_checkout"
    assert pps.remove(root)["code"] == "not_a_public_checkout"
    assert (root / "corpus" / "keep.jsonl").exists(), "لا حذفَ في نسخةٍ بلا علامة"


def test_removing_deletes_the_placed_stores_only(tmp_path):
    root = _public_root(tmp_path)
    pps.place(root, _source(tmp_path), verify=False)
    (root / "core").mkdir()
    (root / "core" / "x.py").write_text("X")
    report = pps.remove(root)
    assert report["status"] == "removed" and set(report["removed"]) == {"corpus", "glossaries"}
    assert not (root / "corpus").exists() and not (root / "glossaries").exists()
    assert (root / "core" / "x.py").exists()


def test_placing_never_overwrites_the_included_dictionary_and_removing_keeps_it(tmp_path):
    root = _public_root(tmp_path)
    src = _source(tmp_path)
    (src / "corpus" / "lexicons").mkdir()
    (src / "corpus" / "lexicons" / "qamus-muhit.jsonl").write_text("SOURCE\n")
    (src / "corpus" / "lexicons" / "mujam-wasit.jsonl").write_text("{}\n")
    public_copy = root / "corpus" / "lexicons" / "qamus-muhit.jsonl"
    public_copy.parent.mkdir(parents=True)
    public_copy.write_text("PUBLIC\n")
    assert pps.place(root, src, verify=False)["status"] == "placed"
    assert public_copy.read_text() == "PUBLIC\n", "المتتبَّعُ في العام لا يُكتب فوقه"
    assert (root / "corpus" / "lexicons" / "mujam-wasit.jsonl").is_file()
    assert (root / "corpus" / "maritime" / "_catalog.jsonl").is_file()
    report = pps.remove(root)
    assert report["status"] == "removed" and "corpus/lexicons/qamus-muhit.jsonl" in report["kept"]
    assert public_copy.read_text() == "PUBLIC\n", "الرفعُ لا يمسّ المضمَّن"
    assert not (root / "corpus" / "lexicons" / "mujam-wasit.jsonl").exists()
    assert not (root / "corpus" / "maritime").exists() and not (root / "glossaries").exists()


def test_a_source_without_any_store_is_refused(tmp_path):
    root = _public_root(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    report = pps.place(root, empty, verify=False)
    assert report["status"] == "refused" and report["code"] == "no_store_found"
