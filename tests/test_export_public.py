"""اللقطةُ العامة تخلو من ملفات المالك بالبصمة لا بالاسم (ك٢٧).

المالك: «كلُّ ملفاتي تبقى في Drive حتى اللوائح والأنظمة، لأن بعضها مسوداتٌ غير منشورة».
فالحارسُ ثلاثُ طبقات: الاستبعادُ بالمسار، ثم بصمةُ النصّ (اثنتا عشرة كلمةً متتالية)
وعناوينُ الوثائق، ثم البياناتُ الشخصية والأسرار والمحجوب. وكلُّ طبقةٍ مُثبَتةٌ هنا بطفرة،
والحدُّ (أقلُّ من اثنتي عشرة كلمةً يفلت) مسمًّى باسمه. والمستودعُ الخاص نفسُه يُصدَّر في
اختبارٍ حقيقيّ فيثبت خلوُّ اللقطة كلِّها.
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
import sign_anchors  # noqa: E402
import check_docs  # noqa: E402
from core.public_export import MARKER_NAME, excluded_streams, read_marker  # noqa: E402
from core.signing import load_trusted_public_key  # noqa: E402
from tests.private_stores import needs_corpus

# جملةٌ مصطنعة تقوم مقام نصّ لائحة (٢٢ كلمة) — لا تقتبس شيئًا من المتن الحقيقي
SENTENCE = ("تلتزم كل وحدة عائمة مسجلة لدى الجهة المختصة بحمل شهادة صلاحية سارية "
            "وتجديدها قبل انتهاء مدتها بثلاثين يوما على الأقل وإشعار الميناء بذلك")
TITLE_FILE = "007__لائحة-تجريبية-لوحدات-الاختبار-العائمة.jsonl"


def mini_root(tmp_path, note="ملاحظةٌ عادية لا تقتبس شيئًا.", extra=None):
    root = tmp_path / "repo"
    files = {
        f"corpus/maritime/{TITLE_FILE}": json.dumps({"record": {"text": SENTENCE}}, ensure_ascii=False) + "\n",
        "corpus/maritime/_catalog.jsonl": json.dumps({"record": {"doc_id": TITLE_FILE[:-6]}}, ensure_ascii=False) + "\n",
        "docs/note.md": note,
        "core/x.py": "X = 1\n",
    }
    files.update(extra or {})
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root, list(files)


def _words(start, stop):
    return " ".join(SENTENCE.split()[start:stop])


# — الطبقة الثانية: بصمةُ النصّ وعناوينُ الوثائق —

def test_twelve_consecutive_words_of_a_regulation_refuse_the_export(tmp_path):
    root, tracked = mini_root(tmp_path, note=f"كما جاء في اللائحة: «{_words(3, 15)}».")
    report = ex.build(root, tmp_path / "out", tracked=tracked)
    assert report["status"] == "refused" and report["code"] == "private_text_in_export"
    assert [(f["path"], f["kind"]) for f in report["findings"]] == [("docs/note.md", "private_text")]
    assert not (tmp_path / "out").exists(), "لا يُكتب شيءٌ عند الرفض"


def test_limit_eleven_consecutive_words_pass_the_fingerprint(tmp_path):
    """الحدُّ المعلَن: الاقتباسُ الأقصرُ من اثنتي عشرة كلمةً يفلت من البصمة."""
    root, tracked = mini_root(tmp_path, note=f"«{_words(3, 14)}»")
    assert ex.build(root, tmp_path / "out", tracked=tracked)["status"] == "exported"


def test_diacritics_punctuation_and_line_breaks_do_not_hide_a_quotation(tmp_path):
    dressed = _words(0, 12).replace("تلتزم", "تلتزمُ").replace("وحدة", "وحدةٍ،").replace("شهادة", "شهادةَ\n")
    root, tracked = mini_root(tmp_path, note=dressed)
    assert ex.build(root, tmp_path / "out", tracked=tracked)["code"] == "private_text_in_export"


def test_a_document_title_refuses_the_export_in_prose_and_as_doc_id(tmp_path):
    root, tracked = mini_root(tmp_path, note="راجع لائحة تجريبية لوحدات الاختبار العائمة قبل البدء.")
    report = ex.build(root, tmp_path / "out", tracked=tracked)
    assert report["code"] == "private_title_in_export"
    assert report["findings"][0]["excerpt"] == "لائحة تجريبية لوحدات الاختبار العائمة"
    root, tracked = mini_root(tmp_path / "b", note=f"الوثيقة {TITLE_FILE[:-6]} في الفهرس")
    assert ex.build(root, tmp_path / "b" / "out", tracked=tracked)["code"] == "private_title_in_export"


def test_title_fingerprints_come_from_file_names_and_catalog_doc_ids(tmp_path):
    root, tracked = mini_root(tmp_path, extra={
        "corpus/maritime/_catalog.jsonl": json.dumps({"record": {"doc_id": "031__لائحة-ثانية-من-الفهرس-وحده"}}) + "\n"})
    fingerprints = ex.private_fingerprints(root, tracked)
    assert "لائحة تجريبية لوحدات الاختبار العائمة" in fingerprints.titles
    assert "لائحة ثانية من الفهرس وحده" in fingerprints.titles
    assert fingerprints.sources == (f"corpus/maritime/{TITLE_FILE}", "corpus/maritime/_catalog.jsonl")


# — الطبقة الثالثة: البياناتُ الشخصية والأسرار والمحجوب —

# العيّناتُ تُركَّب عند التشغيل، فلا يحمل هذا الملفُّ نفسُه نمطًا يرفضه المصدِّر
@pytest.mark.parametrize("planted, kind", [
    ("مسار الجهاز /Users/" + "someone/diwan-work", "home_path"),
    ("راسلني على someone@" + "gmail.com", "personal_email"),
    ("-----BEGIN " + "PRIVATE KEY-----", "private_key"),
    ("token ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4", "token"),
    ("folder 1AbCdEfGhIjKlMnOp" + "QrStUvWxYz_-0123", "drive_id"),
])
def test_personal_data_and_secrets_refuse_the_export(tmp_path, planted, kind):
    root, tracked = mini_root(tmp_path, note=f"سطرٌ فيه {planted} في آخره.")
    report = ex.build(root, tmp_path / "out", tracked=tracked)
    assert report["code"] == "personal_data_in_export"
    assert report["findings"][0]["excerpt"].startswith(kind + ":")


def test_hashes_are_not_mistaken_for_drive_ids(tmp_path):
    digest = "1" + "a" * 32 + " " + "9183bc96e050c9f09f1b191e562ec345e0e8931319ec3a60755e4ba4f4c1796c"
    root, tracked = mini_root(tmp_path, note=f"بصمات: {digest}")
    assert ex.build(root, tmp_path / "out", tracked=tracked)["status"] == "exported"


def test_sealed_content_refuses_the_export_but_the_manifest_passes(tmp_path):
    root, tracked = mini_root(tmp_path, extra={"evaluation/banks/x/sealed/case.json": "{}"})
    assert ex.build(root, tmp_path / "out", tracked=tracked)["code"] == "sealed_content_in_export"
    root, tracked = mini_root(tmp_path / "b", extra={"evaluation/banks/x/sealed/MANIFEST.json": "{}"})
    assert ex.build(root, tmp_path / "b" / "out", tracked=tracked)["status"] == "exported"


# — الطبقة الأولى: الاستبعادُ بالمسار، والعلامة —

def test_excluded_paths_never_reach_the_export_and_the_marker_names_their_streams(tmp_path):
    private = {
        "glossaries/maritime.jsonl": json.dumps({"record": {"definition": SENTENCE}}, ensure_ascii=False) + "\n",
        "sources/acquisitions.jsonl": "{}\n",
        "publish/_manifest.jsonl": "{}\n",
        "docs/probe/drive-library-index.md": "# فهرس\n",
        "tools/seed_acquisitions.py": "X = 1\n",
        "evaluation/suites/benchmark_m14.json": "{}\n",
    }
    root, tracked = mini_root(tmp_path, extra=private)
    out = tmp_path / "out"
    report = ex.build(root, out, tracked=tracked)
    assert report["status"] == "exported"
    assert report["files_excluded"] == len(private) + 2 and report["files_exported"] == 2
    for rel in list(private) + [f"corpus/maritime/{TITLE_FILE}"]:
        assert not (out / rel).exists(), rel
    assert (out / "docs/note.md").is_file() and (out / "core/x.py").is_file()
    marker = read_marker(out)
    assert marker is not None and marker["kind"] == "public_export"
    assert {"glossaries/maritime.jsonl", "sources/acquisitions.jsonl", "corpus/maritime/_catalog.jsonl",
            "corpus/lexicons/_catalog.jsonl", "publish/_manifest.jsonl"} <= excluded_streams(out)
    assert "rulings/precedence.jsonl" not in excluded_streams(out), "سوابقُ المالك عقيدةُ المشروع لا ملفًّا من Drive"


# — المضمَّنُ بقرار المالك (ق٥٨): يخرج من الاستبعاد والبصمة والمسح معًا —

LEXICON_LINE = ("الصبر حبس النفس عن الجزع والفم عن الشكوى والجوارح عن التشويش "
                "وقيل هو ثبات القلب عند موارد الاضطراب على ما تكرهه النفس")


def _lexicon_root(tmp_path, *, wasit_quotes_it: bool):
    extra = {"corpus/lexicons/qamus-muhit.jsonl": json.dumps({"record": {"text": LEXICON_LINE}}, ensure_ascii=False) + "\n",
             "corpus/lexicons/qamus-muhit.jsonl.anchor": "{}\n",
             "corpus/lexicons/mujam-wasit.jsonl": json.dumps({"record": {"text": LEXICON_LINE if wasit_quotes_it else "مادة"}}, ensure_ascii=False) + "\n"}
    return mini_root(tmp_path, note=f"يقول القاموس: {LEXICON_LINE}", extra=extra)


def test_an_included_dictionary_is_exported_and_its_text_is_public(tmp_path):
    root, tracked = _lexicon_root(tmp_path, wasit_quotes_it=False)
    out = tmp_path / "out"
    report = ex.build(root, out, tracked=tracked)
    assert report["status"] == "exported", report.get("findings")
    for rel in ex.INCLUDED_PATHS:
        assert (out / rel).is_file(), rel
    assert not (out / "corpus/lexicons/mujam-wasit.jsonl").exists(), "الوسيط يبقى مع ملفات المالك"
    assert (out / "docs/note.md").is_file(), "اقتباسُ نصٍّ صار عامًّا ليس تسرّبًا"
    marker = read_marker(out)
    assert marker["included_paths"] == list(ex.INCLUDED_PATHS)
    assert "corpus/lexicons/qamus-muhit.jsonl" not in marker["fingerprints"]["sources"]
    assert report["files_excluded"] == 3 and report["files_exported"] == len(tracked) - 3   # اللائحة وفهرسها والوسيط


def test_the_included_dictionary_is_not_scanned_but_the_rest_still_is(tmp_path):
    """الوسيطُ (خاص) ينقل عبارةً عن المحيط (عام): المحيطُ يخرج رغم التداخل، ووثيقةٌ تقتبسها تُرفَض."""
    root, tracked = _lexicon_root(tmp_path, wasit_quotes_it=True)
    report = ex.build(root, tmp_path / "out", tracked=tracked)
    assert report["status"] == "refused" and report["code"] == "private_text_in_export"
    assert {f["path"] for f in report["findings"]} == {"docs/note.md"}, "المضمَّن لا يُمسح؛ والوثيقةُ تُمسح"


def test_without_the_inclusion_the_dictionary_is_private_again(tmp_path, monkeypatch):
    """الطفرةُ اختبارًا: إفراغُ القائمة يعيد المحيط إلى الاستبعاد والبصمة، فتُرفَض الوثيقةُ التي تقتبسه."""
    monkeypatch.setattr(ex, "INCLUDED_PATHS", ())
    root, tracked = _lexicon_root(tmp_path, wasit_quotes_it=False)
    report = ex.build(root, tmp_path / "out", tracked=tracked)
    assert report["status"] == "refused" and report["code"] == "private_text_in_export"
    assert not (tmp_path / "out" / "corpus").exists()


def test_a_non_empty_destination_is_refused(tmp_path):
    root, tracked = mini_root(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "stale").write_text("x")
    assert ex.build(root, tmp_path / "out", tracked=tracked)["code"] == "dest_not_empty"


def _git_ok(*argv):
    return subprocess.run(["git", "-C", str(ROOT), *argv], capture_output=True).returncode == 0


def test_a_marker_never_coexists_with_a_tracked_private_stream():
    """المستودعُ الخاص بلا علامة؛ واللقطةُ العامة بعلامةٍ لا تتتبّع مخزنًا خاصًّا ولا تدعه يُتتبَّع (ك٣٥).

    كان الاختبارُ يشترط غيابَ المخازن من **القرص**، فسقط في كل نسخةٍ عامة وُضعت فيها المتون عمدًا
    بـ`tools/place_private_stores.py` (ك٢٩؛ قيس على الماك في ٢٥ سبتمبر). الشرطُ الصحيح: غيرُ متتبَّع
    في git ومتجاهَل، فوضعُه محليًّا لا يعرّضه للدفع."""
    marker = read_marker(ROOT)
    if marker is None:
        assert not (ROOT / MARKER_NAME).exists(), "علامةٌ معطوبة في الجذر"
        return
    for rel in marker["excluded_streams"]:
        assert not _git_ok("ls-files", "--error-unmatch", rel), f"{rel} متتبَّعٌ رغم أن العلامة تستبعده"
        assert _git_ok("check-ignore", "-q", rel), f"{rel} غيرُ متجاهَل: وضعُه محليًّا يعرّضه للدفع"


def test_no_tracked_text_file_in_this_tree_trips_the_personal_data_patterns():
    """ك٣٦: مفتاحٌ مصطنع في اختبارٍ كان يطابق نمطَ `token` فيردّ التصديرَ حيث المتنُ موضوع، ولا يُرى في CI
    لأن اختبار التصدير الحقيقي `@needs_corpus`. هذا الحارس يمسح الشجرةَ المتتبَّعة كلَّها بلا متون: أنماطُ
    البيانات الشخصية والأسرار والمحجوب وحدها (بصماتٌ فارغة)، فيسقط في CI قبل أن يسقط التصدير."""
    empty = ex.Fingerprints(frozenset(), (), ())
    hits = []
    for rel in ex.tracked_files(ROOT):
        if ex.is_excluded(rel) or ex.is_included(rel):
            continue
        try:
            text = (ROOT / rel).read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        hits.extend(ex.scan_path(rel, text, empty))
    assert hits == [], [h.as_dict() for h in hits]


@needs_corpus
def test_the_real_repository_exports_cleanly(tmp_path):
    """الدليلُ الحقيقي: المستودعُ كما هو يُصدَّر بلا إصابةٍ واحدة، والبصمةُ مبنيّةٌ من المتن فعلًا."""
    out = tmp_path / "public"
    report = ex.build(ROOT, out)
    assert report["status"] == "exported", json.dumps(report.get("findings", [])[:20], ensure_ascii=False)
    assert report["fingerprints"]["shingles"] > 100_000 and report["fingerprints"]["titles"] > 100
    for rel in ("corpus/maritime", "corpus/lexicons/_catalog.jsonl", "corpus/lexicons/mujam-wasit.jsonl",
                "glossaries", "sources", "publish", "docs/probe/drive-library-index.md",
                "evaluation/suites/benchmark_m14.json"):
        assert not (out / rel).exists(), rel
    for rel in ex.INCLUDED_PATHS:
        assert (out / rel).is_file(), f"{rel}: المضمَّن بق٥٨ يخرج"
    assert (out / MARKER_NAME).is_file() and (out / "AGENTS.md").is_file()
    # الأرقامُ المولَّدة أُعيد اشتقاقُها داخل اللقطة فيمرّ فحصُ الوثائق فيها كما هي
    assert report["docs_regenerated"] is True
    check = subprocess.run([sys.executable, str(out / "tools" / "check_docs.py"), "--check"],
                           cwd=out, capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr


# — السجلاتُ الحاكمة الغائبة في اللقطة: تخطٍّ معلَن، لا عطبٌ ولا ستر —

def _keys_root(tmp_path):
    root = tmp_path / "pub"
    (root / "keys").mkdir(parents=True)
    for name in ("anchor-ed25519.pub", "anchor-policy.json"):
        (root / "keys" / name).write_bytes((ROOT / "keys" / name).read_bytes())
    return root


def _marker(root, streams):
    (root / MARKER_NAME).write_text(json.dumps({"schema_version": 1, "kind": "public_export",
                                                "excluded_streams": list(streams)}), encoding="utf-8")


def test_verify_skips_a_wholly_absent_stream_only_when_the_marker_names_it(tmp_path):
    root = _keys_root(tmp_path)
    public = load_trusted_public_key(root=root)
    before = sign_anchors.verify_repository(root, public_key=public)
    assert before["failures"], "بلا علامةٍ غيابُ سجلٍّ حاكم عطب"
    _marker(root, sign_anchors.GOVERNING)
    after = sign_anchors.verify_repository(root, public_key=public)
    assert after["failures"] == [] and after["skipped"] >= len(sign_anchors.GOVERNING)


def test_a_present_ledger_without_anchor_still_fails_under_the_marker(tmp_path):
    root = _keys_root(tmp_path)
    _marker(root, sign_anchors.GOVERNING)
    (root / "sources").mkdir()
    (root / "sources" / "acquisitions.jsonl").write_text("")
    report = sign_anchors.verify_repository(root, public_key=load_trusted_public_key(root=root))
    assert any(f.startswith("sources/acquisitions.jsonl:") for f in report["failures"])


def test_a_malformed_marker_is_no_marker(tmp_path):
    root = _keys_root(tmp_path)
    (root / MARKER_NAME).write_text(json.dumps({"kind": "something_else", "excluded_streams": list(sign_anchors.GOVERNING)}))
    assert excluded_streams(root) == frozenset()
    (root / MARKER_NAME).write_text("{not json")
    assert read_marker(root) is None


# — أرقامُ الوثائق بلا فهرس —

def test_maritime_counts_fall_back_to_the_published_status_only_under_the_marker(tmp_path):
    root = tmp_path / "pub"
    (root / "docs" / "probe").mkdir(parents=True)
    (root / "docs" / "probe" / "project-status.json").write_text(
        json.dumps({"maritime_current_documents": 117, "maritime_current_pages": 1279}))
    with pytest.raises(check_docs.DocsError, match="maritime_catalog_missing"):
        check_docs.maritime_counts(root)
    _marker(root, ["corpus/maritime/_catalog.jsonl"])
    assert check_docs.maritime_counts(root) == (117, 1279)
    _marker(root, ["glossaries/maritime.jsonl"])
    with pytest.raises(check_docs.DocsError, match="maritime_catalog_missing"):
        check_docs.maritime_counts(root)


@needs_corpus
def test_maritime_counts_read_the_real_catalog_when_present():
    documents, pages = check_docs.maritime_counts(ROOT)
    published = json.loads((ROOT / "docs" / "probe" / "project-status.json").read_text())
    assert (documents, pages) == (published["maritime_current_documents"], published["maritime_current_pages"])


# — مشغّلُ التحقّق المشترك: الخروجُ 3 «تعذّر بحدٍّ معلن» تحت العلامة وحدها —

import run_verification as rv  # noqa: E402
from verification_checks import Check  # noqa: E402


def _only(returncode):
    return lambda python, node: (Check("fake", (python, "-c", f"raise SystemExit({returncode})")),)


def test_exit_three_passes_only_under_the_public_export_marker(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_verification.py"])
    monkeypatch.setattr(rv, "commands", _only(3))
    monkeypatch.setattr(rv, "read_marker", lambda root: {"kind": "public_export"})
    assert rv.main() == 0
    monkeypatch.setattr(rv, "read_marker", lambda root: None)
    assert rv.main() == 1, "في المستودع الخاص الخروجُ 3 فشلٌ كما كان"


def test_a_real_failure_still_fails_under_the_marker(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_verification.py"])
    monkeypatch.setattr(rv, "commands", _only(1))
    monkeypatch.setattr(rv, "read_marker", lambda root: {"kind": "public_export"})
    assert rv.main() == 1
