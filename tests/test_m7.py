"""اختبارات م٧: إسقاط النشر، اللقطة والذيل، توقيع المراسي."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core.acquisitions import Acquisition, SourceRegister
from core.corpus import CorpusCatalog, CorpusFile
from core.knowledge import KnowledgeItem
from core.ledger import Ledger, LedgerCorrupt
from core.seal import sealed_append
from core.signing import (SigningRefused, load_key, require_signed_seal,
                          sign_anchor, verify_anchor_signature)
from core.snapshot import full_entries, rotate, snapshots_of, verify_full
from publish_projection import build


def _sealed(path, n=5):
    led = Ledger(path)
    for i in range(n):
        sealed_append(led, {"kind": "x", "i": i}, "اختبار")
    return led


# ── اللقطة والذيل ──

def test_rotate_rebuild_matches_full(tmp_path):
    led = _sealed(tmp_path / "l.jsonl", 5)
    before = led.entries()
    snap = rotate(tmp_path / "l.jsonl")
    sealed_append(Ledger(tmp_path / "l.jsonl"), {"kind": "x", "i": 99},
                  "ذيل")
    rebuilt = full_entries(tmp_path / "l.jsonl")
    assert [e["digest"] for e in rebuilt[:5]] \
        == [e["digest"] for e in before]
    assert len(rebuilt) == 6 and verify_full(tmp_path / "l.jsonl")
    assert snapshots_of(tmp_path / "l.jsonl") == [snap]


def test_rotate_generations_chain(tmp_path):
    _sealed(tmp_path / "l.jsonl", 3)
    rotate(tmp_path / "l.jsonl")
    sealed_append(Ledger(tmp_path / "l.jsonl"), {"kind": "x", "i": 10}, "ذ")
    rotate(tmp_path / "l.jsonl")
    sealed_append(Ledger(tmp_path / "l.jsonl"), {"kind": "x", "i": 20}, "ذ")
    rebuilt = full_entries(tmp_path / "l.jsonl")
    # 3 + (وصل1 + قيد) في اللقطة الثانية + (وصل2 + قيد) في الذيل:
    # الوصلان يُجرَّدان من كل جيل عند القراءة الكاملة
    assert [e["record"].get("i") for e in rebuilt] == [0, 1, 2, 10, 20]


def test_rotate_refuses_empty_and_fresh_tail(tmp_path):
    Ledger(tmp_path / "l.jsonl")
    with pytest.raises(LedgerCorrupt):
        rotate(tmp_path / "l.jsonl")
    _sealed(tmp_path / "m.jsonl", 2)
    rotate(tmp_path / "m.jsonl")
    with pytest.raises(LedgerCorrupt):      # ذيل بلا قيود جديدة
        rotate(tmp_path / "m.jsonl")


def test_missing_snapshot_detected(tmp_path):
    _sealed(tmp_path / "l.jsonl", 2)
    snap = rotate(tmp_path / "l.jsonl")
    snap.unlink()
    with pytest.raises(LedgerCorrupt):
        full_entries(tmp_path / "l.jsonl")


# ── التوقيع ──

def test_signing_named_codes(tmp_path, monkeypatch):
    led = _sealed(tmp_path / "g.jsonl", 2)
    monkeypatch.delenv("DIWAN_ANCHOR_KEY", raising=False)
    monkeypatch.setattr("core.signing._keychain_key", lambda: None)
    with pytest.raises(SigningRefused) as e:
        load_key()
    assert e.value.code == "signing_key_missing"
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "سر-اختباري")
    with pytest.raises(SigningRefused) as e2:
        verify_anchor_signature(led)
    assert e2.value.code == "signature_missing"
    sign_anchor(led)
    assert verify_anchor_signature(led)
    require_signed_seal(led, "اختبار")
    led.anchor_path.write_text(
        led.anchor_path.read_text() + " ", encoding="utf-8")
    with pytest.raises(SigningRefused) as e3:
        verify_anchor_signature(led)
    assert e3.value.code == "signature_mismatch"
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "مفتاح-آخر")
    sign_anchor(led)      # إعادة توقيع بعد اعتماد جديد مشروعة
    assert verify_anchor_signature(led)


# ── إسقاط النشر ──

def _pub_root(tmp_path, dist_flag):
    reg = SourceRegister(tmp_path / "sources" / "acquisitions.jsonl")
    reg.acquire(Acquisition("s1", "ت", "https://x", "2026-09-20",
                            True, dist_flag, "دليل"))
    d = tmp_path / "corpus" / "maritime"
    d.mkdir(parents=True)
    cf = CorpusFile(d / "001__و.jsonl")
    it = KnowledgeItem(text="متن الوثيقة التجريبي هنا.", lang="ar",
                       domain="maritime", use_internal=True,
                       use_distribution=dist_flag, source_id="s1",
                       locus="ص1", originality="original", part="و")
    h = cf.ingest("001__و", [it], reg)
    cf.anchor()
    cat = CorpusCatalog(d / "_catalog.jsonl")
    cat.record("001__و", "corpus/maritime/001__و.jsonl", 1, h)
    cat.anchor()
    return tmp_path


def test_publish_projection_distributable_and_internal(tmp_path):
    open_root = _pub_root(tmp_path / "a", True)
    r = build(open_root)
    assert r["published"] == 1 and not r["rights_refused"]
    blob = "\n".join(p.read_text(encoding="utf-8")
                     for p in (open_root / "publish").glob("*.jsonl"))
    assert "متن الوثيقة" in blob
    closed_root = _pub_root(tmp_path / "b", False)
    r2 = build(closed_root)
    assert r2["published"] == 0 and r2["skipped"] == 1
    assert not [p for p in (closed_root / "publish").glob("*.jsonl")
                if not p.name.startswith("_")]


def test_publish_rebuild_is_stateless_query(tmp_path):
    root = _pub_root(tmp_path / "c", True)
    build(root)
    r2 = build(root)      # إعادة البناء استعلام من صفر لا تراكم
    assert r2["published"] == 1
    files = list((root / "publish").glob("maritime__*.jsonl"))
    assert len(files) == 1


# ── تدقيق م٧: التدوير لا يمحو حقيقة الدرء (ق٢٢ عبر الأجيال) ──

def test_rotation_preserves_router_dedup(tmp_path):
    from core.knowledge import KnowledgeEvent, NodeManifest
    from core.registry import NodeRegistry
    from core.router import main_ledger, route_once
    from core.snapshot import rotate as _rotate
    from core.streams import inbox, outbox
    reg = NodeRegistry(tmp_path / "registry" / "nodes.jsonl")
    for nm in ("a", "b"):
        reg.register(NodeManifest(name=nm, contract_version=1,
                                  domains=("d",), accepts=("query",),
                                  data_policy_ceiling="regulated"))
    reg.anchor()
    outbox(tmp_path, "a").emit(KnowledgeEvent(
        kind="query", from_node="a", to="b", correlation_id="c",
        data_policy="internal", payload={"question": "س", "domain": "d"},
        idempotency_key="k", budget_cap_micros=1))
    route_once(tmp_path, "2026-09-20T12:00:00+00:00")
    _rotate(main_ledger(tmp_path).path)
    # فقد الإزاحات + تدوير السجل الرئيس معًا: الدرء من التاريخ الكامل
    (tmp_path / "ledger" / "router-offsets.json").unlink()
    stats = route_once(tmp_path, "2026-09-20T12:01:00+00:00")
    assert stats["delivered"] == 0
    assert len(inbox(tmp_path, "b").read_from(0)) == 1


def test_rotate_refuses_streams(tmp_path):
    from core.snapshot import rotate as _rotate
    p = tmp_path / "streams" / "x" / "outbox.jsonl"
    p.parent.mkdir(parents=True)
    _sealed(p, 2)
    with pytest.raises(LedgerCorrupt):
        _rotate(p)


def test_rotate_numbering_max_not_count(tmp_path):
    from core.snapshot import rotate as _rotate, snapshots_of
    _sealed(tmp_path / "l.jsonl", 2)
    s1 = _rotate(tmp_path / "l.jsonl")
    sealed_append(Ledger(tmp_path / "l.jsonl"), {"kind": "x"}, "ذ")
    s2 = _rotate(tmp_path / "l.jsonl")
    assert s2.name.endswith("snap-2.jsonl")
    # حذف اللقطة الأحدث لا يعيد سك اسمها: التاريخ الكامل يرفض أولًا
    s2.unlink()
    sealed_append(Ledger(tmp_path / "l.jsonl"), {"kind": "y"}, "ذ")
    with pytest.raises(LedgerCorrupt):
        _rotate(tmp_path / "l.jsonl")


# ── تدقيق م٧: التوقيع موصول بالقراءة والكتابة الحاكمتين ──

def test_require_seal_verifies_existing_signature(tmp_path, monkeypatch):
    from core.seal import require_seal
    monkeypatch.setattr("core.signing._keychain_key", lambda: None)
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "سر")
    led = _sealed(tmp_path / "g.jsonl", 2)
    sign_anchor(led)
    require_seal(led, "اختبار")            # موقعة سليمة تمر
    led.anchor_path.write_text(
        led.anchor_path.read_text() + " ", encoding="utf-8")
    with pytest.raises(SigningRefused) as e:
        require_seal(led, "اختبار")        # كل مسار قراءة يتحقق
    assert e.value.code == "signature_mismatch"


def test_sealed_append_resigns_signed_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr("core.signing._keychain_key", lambda: None)
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "سر")
    led = _sealed(tmp_path / "g.jsonl", 2)
    sign_anchor(led)
    sealed_append(led, {"kind": "x"}, "اختبار")   # يعيد التوقيع تلقائيًا
    assert verify_anchor_signature(led)
    # وبلا مفتاح: الكتابة في سجل موقَّع تُرفض لا توقيعًا يتيمًا
    monkeypatch.delenv("DIWAN_ANCHOR_KEY")
    with pytest.raises(SigningRefused) as e:
        sealed_append(led, {"kind": "y"}, "اختبار")
    assert e.value.code == "signing_key_missing"


def test_signature_not_transferable_between_ledgers(tmp_path, monkeypatch):
    import shutil as _sh
    monkeypatch.setattr("core.signing._keychain_key", lambda: None)
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "سر")
    a = _sealed(tmp_path / "a.jsonl", 2)
    sign_anchor(a)
    # سجل آخر بمحتوى بايتات مطابق — التوقيع مفصول النطاق بالاسم
    _sh.copy(a.path, tmp_path / "b.jsonl")
    _sh.copy(a.anchor_path, tmp_path / "b.jsonl.anchor")
    _sh.copy(str(a.anchor_path) + ".sig", tmp_path / "b.jsonl.anchor.sig")
    with pytest.raises(SigningRefused) as e:
        verify_anchor_signature(Ledger(tmp_path / "b.jsonl"))
    assert e.value.code == "signature_mismatch"


# ── تدقيق م٧: بيان النشر لا يُمحى ──

def test_publish_manifest_append_only_across_runs(tmp_path):
    root = _pub_root(tmp_path / "d", True)
    r1 = build(root)
    r2 = build(root)
    assert (r1["run_seq"], r2["run_seq"]) == (1, 2)
    recs = [e["record"] for e in
            Ledger(root / "publish" / "_manifest.jsonl").entries()]
    assert sum(1 for x in recs if x["kind"] == "publish_run") == 2
    assert Ledger(root / "publish" / "_manifest.jsonl") \
        .verify_chain(strict=True)


def test_torn_anchor_named_corruption(tmp_path):
    led = _sealed(tmp_path / "l.jsonl", 2)
    led.anchor_path.write_text('{"head": "abc', encoding="utf-8")
    from core.seal import require_seal
    with pytest.raises(LedgerCorrupt) as e:
        require_seal(led, "اختبار")
    assert "مبتورة أو مشوهة" in str(e.value)


# ── تدقيق التسليم: «تعذَّر التحقق» ليس «فشل التحقق» ──

def _signed(tmp_path, monkeypatch, n=3):
    monkeypatch.setattr("core.signing._keychain_key", lambda: None)
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "سر-الاختبار")
    led = _sealed(tmp_path / "g.jsonl", n)
    sign_anchor(led)
    return led


def test_keyless_read_of_signed_register_succeeds(tmp_path, monkeypatch,
                                                  capsys):
    """قفلُ السجلات الحاكمة أمام بيئةٍ بلا مفتاح كان عيبًا قاتلًا:
    الجلسة السحابية وأي استنساخ لم يعودا يقرآن شيئًا."""
    from core.seal import require_seal
    from core.signing import UNVERIFIABLE, anchor_signature_state
    led = _signed(tmp_path, monkeypatch)
    monkeypatch.delenv("DIWAN_ANCHOR_KEY")
    require_seal(led, "سجل حاكم")            # يقرأ ولا يقتل
    assert anchor_signature_state(led, "س") == UNVERIFIABLE
    assert "تعذَّر التحقق" in capsys.readouterr().err   # مُعلَن لا صامت


def test_keyless_still_catches_broken_seal(tmp_path, monkeypatch):
    from core.seal import require_seal
    led = _signed(tmp_path, monkeypatch)
    lines = led.path.read_text().splitlines()
    led.path.write_text("\n".join(lines[:2]) + "\n")   # قصُّ ذيل
    monkeypatch.delenv("DIWAN_ANCHOR_KEY")
    with pytest.raises(LedgerCorrupt):
        require_seal(led, "سجل حاكم")


def test_keyed_catches_rewritten_anchor(tmp_path, monkeypatch):
    """إعادة كتابة السجل والمرساة معًا: التوقيع يكشفها لحامل المفتاح."""
    from core.seal import require_seal
    led = _signed(tmp_path, monkeypatch)
    lines = led.path.read_text().splitlines()
    led.path.write_text("\n".join(lines[:2]) + "\n")
    led.anchor()                                  # ختمٌ متسق كذبًا
    with pytest.raises(SigningRefused) as e:
        require_seal(led, "سجل حاكم")
    assert e.value.code == "signature_mismatch"


def test_strict_env_restores_fatal_missing_key(tmp_path, monkeypatch):
    from core.seal import require_seal
    led = _signed(tmp_path, monkeypatch)
    monkeypatch.delenv("DIWAN_ANCHOR_KEY")
    monkeypatch.setenv("DIWAN_REQUIRE_SIGNATURE", "1")
    with pytest.raises(SigningRefused) as e:
        require_seal(led, "سجل حاكم")
    assert e.value.code == "signing_key_missing"


def test_keyless_write_to_signed_ledger_refused(tmp_path, monkeypatch):
    """الكتابة تبقى لحامل المفتاح: مرساةٌ جديدة بتوقيعٍ قديم = إنذار
    عبثٍ كاذب."""
    led = _signed(tmp_path, monkeypatch)
    monkeypatch.delenv("DIWAN_ANCHOR_KEY")
    with pytest.raises(SigningRefused) as e:
        sealed_append(led, {"kind": "z"}, "سجل حاكم")
    assert e.value.code == "signing_key_missing"


def test_unsigned_ledger_state_unchanged(tmp_path, monkeypatch):
    from core.signing import UNSIGNED, anchor_signature_state
    monkeypatch.setattr("core.signing._keychain_key", lambda: None)
    monkeypatch.delenv("DIWAN_ANCHOR_KEY", raising=False)
    led = _sealed(tmp_path / "plain.jsonl", 2)      # بلا توقيع أصلًا
    assert anchor_signature_state(led) == UNSIGNED


# ── تدقيق ق٢٥: خمسةُ قواتلَ في نموذج التوقيع نفسه ──

@pytest.fixture
def governing(tmp_path, monkeypatch):
    """سجلٌّ حاكم محاكى: جذرُ مستودعٍ مؤقت وقائمةٌ تضمّه."""
    import core.signing as sg
    root = tmp_path.resolve()
    monkeypatch.setattr(sg, "ROOT", root)
    monkeypatch.setattr(sg, "PINNED_POLICY_SHA256", None)  # legacy fixture only
    monkeypatch.setattr(sg, "SIGNED_LEDGERS",
                        frozenset({"registry/nodes.jsonl"}))
    monkeypatch.setattr(sg, "_keychain_key", lambda: None)
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "سر-الاختبار")
    (root / "registry").mkdir()
    led = Ledger(root / "registry" / "nodes.jsonl")
    for i in range(3):
        sealed_append(led, {"kind": "x", "i": i}, "سجل العقد")
    sign_anchor(led)
    assert sg.is_governing(led)
    return led


def test_deleting_signature_is_not_silent_downgrade(governing, monkeypatch):
    """حذفُ `.sig` كان يعيد السجل «بلا توقيع» صامتًا حتى تحت الصرامة —
    فيفتح بالضبط ما زعم ق٢٣ إغلاقه."""
    from core.seal import require_seal
    from core.signing import MISSING_SIG, sig_path
    sig_path(governing).unlink()
    monkeypatch.setenv("DIWAN_REQUIRE_SIGNATURE", "1")
    with pytest.raises(SigningRefused) as e:
        require_seal(governing, "سجل العقد")
    assert e.value.code == "signature_missing"
    monkeypatch.delenv("DIWAN_REQUIRE_SIGNATURE")
    assert require_seal(governing, "سجل العقد") == MISSING_SIG


def test_keyless_write_leaves_no_trace(governing, monkeypatch):
    """كانت تُلحق القيد وتُرسي **ثم** ترفض، فتترك السجل في
    `signature_mismatch` دائم — عينُ الضرر الذي زعمت منعَه."""
    from core.seal import require_seal
    from core.signing import VERIFIED
    before = governing.count()
    monkeypatch.delenv("DIWAN_ANCHOR_KEY")
    with pytest.raises(SigningRefused) as e:
        sealed_append(governing, {"kind": "z"}, "سجل العقد")
    assert e.value.code == "signing_key_missing"
    assert governing.count() == before
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "سر-الاختبار")
    assert require_seal(governing, "سجل العقد") == VERIFIED


def test_signature_scope_is_path_not_basename(tmp_path, monkeypatch):
    """ثلاثةُ فهارسَ حاكمة اسمُها `_catalog.jsonl` — فكان توقيعُ
    أحدها ينتقل إلى الآخر ويُقرأ verified."""
    import shutil
    from core.seal import require_seal
    from core.signing import sig_path
    monkeypatch.setattr("core.signing._keychain_key", lambda: None)
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "سر")
    a_dir, b_dir = tmp_path / "maritime", tmp_path / "lexicons"
    a_dir.mkdir(); b_dir.mkdir()
    a = _sealed(a_dir / "_catalog.jsonl", 2)
    sign_anchor(a)
    for suffix in ("", ".anchor", ".anchor.sig"):
        shutil.copy(str(a.path) + suffix, str(b_dir / "_catalog.jsonl") + suffix)
    with pytest.raises(SigningRefused) as e:
        require_seal(Ledger(b_dir / "_catalog.jsonl"), "منقول")
    assert e.value.code == "signature_mismatch"


def test_wiped_ledger_with_orphan_signature_detected(governing):
    """سجلٌّ مُفرَّغ ومرساتُه محذوفة كان يُقرأ «سليمًا» — الفرع كان
    يعود قبل أي فحص توقيع."""
    from core.seal import require_seal
    governing.path.write_text("", encoding="utf-8")
    governing.anchor_path.unlink()
    with pytest.raises(LedgerCorrupt) as e:
        require_seal(governing, "سجل العقد")
    assert "مُحيَ" in str(e.value)


def test_keychain_invoked_by_absolute_path():
    """«security» عبر PATH كان يُفرض مفتاحُ مهاجمٍ بأولوية أعلى من
    متغير البيئة الذي هُبِّط لأجل هذا الخطر بعينه."""
    import core.signing as sg
    assert sg.SECURITY_BIN.startswith("/")


def test_governing_list_single_source():
    import sys as _s
    _s.path.insert(0, str(ROOT / "tools"))
    import sign_anchors
    from core.signing import SIGNED_LEDGERS
    assert set(sign_anchors.GOVERNING) == set(SIGNED_LEDGERS)


def test_announcements_readable_programmatically(governing, monkeypatch):
    from core.seal import require_seal
    from core.signing import announcements
    monkeypatch.delenv("DIWAN_ANCHOR_KEY")
    require_seal(governing, "سجل العقد")
    assert any("تعذَّر" in a for a in announcements())


# ── تدقيق ق٣٩: غيابُ مكتبة التعمية «تعذُّر» لا «فشل» ──

def _no_backend(monkeypatch):
    """يحاكي مفسِّرًا بلا `cryptography` — حدُّ بيئةٍ لا دليلَ عبث."""
    import core.signing as sg
    from core.signing import SigningRefused
    def boom(*a, **k):
        raise SigningRefused("signing_backend_missing",
                             "Ed25519 backend is unavailable")
    monkeypatch.setattr(sg, "verify_anchor_signature", boom)
    monkeypatch.setattr(sg, "_keychain_key", lambda *a, **k: None)
    monkeypatch.delenv("DIWAN_ANCHOR_KEY", raising=False)


def test_missing_backend_does_not_lock_governing_reads(governing,
                                                       monkeypatch, capsys):
    """كان غيابُ المكتبة يُقفل **كل** سجل حاكم أمام أي مفسِّر بلا
    `cryptography` — عودةُ لغم ق٢٥ من بابٍ آخر بعد ترحيل ق٣٨."""
    import core.signing as sg
    from core.seal import require_seal
    _no_backend(monkeypatch)
    assert require_seal(governing, "سجل العقد") == sg.UNVERIFIABLE
    assert "تعذَّر" in capsys.readouterr().err        # مُعلَن لا صامت


def test_missing_backend_is_fatal_only_under_strict(governing, monkeypatch):
    from core.seal import require_seal
    from core.signing import SigningRefused
    _no_backend(monkeypatch)
    monkeypatch.setenv("DIWAN_REQUIRE_SIGNATURE", "1")
    with pytest.raises(SigningRefused) as e:
        require_seal(governing, "سجل العقد")
    assert e.value.code == "signing_backend_missing"


def test_unverifiable_codes_cover_key_and_backend():
    """الرمزان سواءٌ في الدلالة: حدُّ بيئةٍ لا دليلُ عبث."""
    from core.signing import UNVERIFIABLE_CODES
    assert UNVERIFIABLE_CODES == {"signing_key_missing",
                                  "signing_backend_missing"}
