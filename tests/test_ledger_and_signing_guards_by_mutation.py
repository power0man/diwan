"""خمسةُ حرّاسٍ حاملة كانت بلا اختبار — كلٌّ منها طفرةٌ نجت في ٢٣ و٢٥ سبتمبر ٢٠٢٦ (ك١٩).

الحارسُ الذي لا يُسقط اختبارًا حين يُكسر حارسٌ موصوف لا مفروض. وهذه الاختبارات
مبنيّةٌ بحيث لا يقع إلا الحارسُ المستهدف: القيدُ المزوَّر يُعاد حسابُ بصمته فلا
يُصاد بفحص البصمة، والسلسلةُ المُعاد تجذيرُها متّسقةٌ فلا يُصاد إلا بالمرساة،
والسياسةُ البديلة صالحةُ الشكل فلا يُصاد إلا بالدبّوس.
"""
from __future__ import annotations

import hashlib

import pytest

from core.canonical import canonical_bytes, digest
from core.ledger import GENESIS, Ledger, LedgerCorrupt
import core.signing as s


# ————— السلسلة: core/ledger.py —————

def _ledger(tmp_path, n=3) -> Ledger:
    led = Ledger(tmp_path / "l.jsonl")
    for i in range(n):
        led.append({"kind": "synthetic", "i": i})
    return led


def _rewrite_last(led: Ledger, **changes) -> None:
    """يعيد كتابة القيد الأخير بحقولٍ مغيَّرة **وبصمةٍ مُعادة الحساب**.

    فالقيدُ بعد التزوير سليمُ البصمة في ذاته، ولا يكشفه إلا الحارسُ الذي
    يفحص الحقلَ المغيَّر. وكونه الأخير يعني أن لا قيدَ بعده يشهد عليه.
    """
    entries = led.entries()
    body = {"prev": entries[-1]["prev"], "seq": entries[-1]["seq"],
            "record": entries[-1]["record"]}
    body.update(changes)
    forged = {"digest": digest(body), **body}
    lines = [canonical_bytes({"digest": e["digest"], "prev": e["prev"], "seq": e["seq"],
                              "record": e["record"]}).decode("utf-8")
             for e in entries[:-1]] + [canonical_bytes(forged).decode("utf-8")]
    led.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_rerooted_prev_with_a_valid_self_digest_is_refused(tmp_path):
    """`core/ledger.py`: فحصُ prev. القيدُ الأخير يُعلَّق على GENESIS بدل رأس سابقه."""
    led = _ledger(tmp_path)
    _rewrite_last(led, prev=GENESIS)
    last = led.entries()[-1]
    assert digest({"prev": last["prev"], "seq": last["seq"], "record": last["record"]}) == last["digest"], \
        "التزوير سليمُ البصمة عمدًا، فلا يصيده فحصُ البصمة"
    with pytest.raises(LedgerCorrupt, match="prev"):
        led.verify_chain()


def test_a_forged_seq_with_a_valid_self_digest_is_refused(tmp_path):
    """`core/ledger.py`: فحصُ seq."""
    led = _ledger(tmp_path)
    _rewrite_last(led, seq=41)
    with pytest.raises(LedgerCorrupt, match="seq="):
        led.verify_chain()


def test_a_consistent_chain_whose_head_left_the_anchor_is_refused_strictly(tmp_path):
    """`core/ledger.py`: رأسُ المرساة الصارم. يُقصّ الذيل ثم يُعوَّض بالعدد نفسه.

    السلسلةُ الناتجة متّسقةٌ تمامًا (تمرّ بلا strict)، والعددُ يساوي عدد المرساة،
    فلا يبقى إلا مقارنةُ الرأس عند موضع المرساة.
    """
    led = _ledger(tmp_path, n=3)
    anchored = led.anchor()
    kept = led.entries()[:2]
    led.path.write_text("".join(
        canonical_bytes({"digest": e["digest"], "prev": e["prev"], "seq": e["seq"],
                         "record": e["record"]}).decode("utf-8") + "\n" for e in kept),
        encoding="utf-8")
    led.append({"kind": "synthetic", "i": "replacement"})
    assert led.count() == anchored["count"] and led.head() != anchored["head"]
    assert led.verify_chain() is True, "بلا مرساةٍ لا يُكشف: حدٌّ معلَنٌ في رأس الوحدة"
    with pytest.raises(LedgerCorrupt, match="الرأس"):
        led.verify_chain(strict=True)


def test_a_truncated_anchor_is_a_named_corruption_not_a_raw_decode_error(tmp_path):
    """مرساةٌ مبتورة ترمي LedgerCorrupt عبر read_anchor، لا JSONDecodeError خامًا."""
    led = _ledger(tmp_path)
    led.anchor()
    led.anchor_path.write_text('{"head": "ab', encoding="utf-8")
    with pytest.raises(LedgerCorrupt, match="مبتورة"):
        led.verify_chain(strict=True)


# ————— التوقيع: core/signing.py —————

KEY_A = bytes(range(32))
KEY_B = bytes(32)


def _root_with_policy(tmp_path, monkeypatch, *, pinned_key: bytes, on_disk_key: bytes,
                      public_key_file: bytes) -> "Path":
    """جذرٌ فيه سياسةٌ مثبَّتةٌ على مفتاح، وملفُّ سياسةٍ على القرص قد يخالفها."""
    (tmp_path / "keys").mkdir()
    pinned = s.canonical_policy_bytes(s.policy_document(pinned_key))
    monkeypatch.setattr(s, "PINNED_POLICY_SHA256", hashlib.sha256(pinned).hexdigest())
    (tmp_path / s.POLICY_PATH).write_bytes(s.canonical_policy_bytes(s.policy_document(on_disk_key)))
    (tmp_path / s.PUBLIC_KEY_PATH).write_bytes(public_key_file)
    return tmp_path


def test_a_well_formed_policy_that_differs_from_the_pin_is_refused(tmp_path, monkeypatch):
    """`core/signing.py`: دبّوس السياسة. السياسةُ البديلة صالحةُ الشكل تمامًا."""
    root = _root_with_policy(tmp_path, monkeypatch, pinned_key=KEY_A, on_disk_key=KEY_B,
                             public_key_file=KEY_B)
    with pytest.raises(s.SigningRefused) as caught:
        s.load_signing_policy(root=root)
    assert caught.value.code == "signing_policy_mismatch"
    # والضبطُ الموجب: السياسةُ المثبَّتة نفسُها تُقبل
    (root / s.POLICY_PATH).write_bytes(s.canonical_policy_bytes(s.policy_document(KEY_A)))
    assert s.load_signing_policy(root=root)["public_key_sha256"] == hashlib.sha256(KEY_A).hexdigest()


def test_a_thirty_two_byte_key_that_is_not_the_approved_identity_is_refused(tmp_path, monkeypatch):
    """`core/signing.py`: بصمةُ المفتاح العام. الطولُ صحيح، والهويةُ غيرُها."""
    root = _root_with_policy(tmp_path, monkeypatch, pinned_key=KEY_A, on_disk_key=KEY_A,
                             public_key_file=KEY_B)
    with pytest.raises(s.SigningRefused) as caught:
        s.load_trusted_public_key(root=root)
    assert caught.value.code == "signing_public_key_mismatch"
    (root / s.PUBLIC_KEY_PATH).write_bytes(KEY_A)
    assert s.load_trusted_public_key(root=root) == KEY_A
