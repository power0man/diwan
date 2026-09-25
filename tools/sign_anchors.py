#!/usr/bin/env python3
"""توقيع مراسي السجلات الحاكمة كلها (م٧، ق١٥/ق٢١).

    python3 tools/sign_anchors.py          # يوقّع بمفتاح البيئة/السلسلة
    python3 tools/sign_anchors.py verify   # يتحقق التواقيع والأختام

رموز الخروج في `verify` تفرّق ما كان مختلطًا: **0** كلُّ الأختام
سليمة وكلُّ التواقيع **مفحوصةٌ** متطابقة؛ **3** الأختام سليمة
والتواقيعُ **تعذَّر** فحصها لغياب المفتاح (حدُّ بيئةٍ لا عبث — حالُ
الجلسة السحابية وأي استنساخ)؛ **2** البيئةُ صارمةٌ ولا مفتاحَ فيها
(عقدُ بيئةٍ مخروق، لا عبث)؛ **1** فشلٌ حقيقي: ختمٌ مكسور، أو توقيعٌ
مخالف، أو **سجلٌّ حاكم بلا توقيع** (حُذف `.sig`).

والحكمُ من **حالة كل سجل** لا من حضور المفتاح في العملية: كانت تطبع
«✓ ختمٌ وتوقيعٌ سليمان» لسجلٍ حُذف توقيعُه (تدقيق ق٢٥). ووضعُ
التوقيع يفحص الختمَ والسلسلة أولًا، ولا يمحو `signature_mismatch`
قائمًا إلا بـ`--resign` صريحة — فالتوقيعُ إقرارٌ لا مسحُ إنذار.
"""
from __future__ import annotations

import sys
import os
import hmac
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ledger import Ledger, LedgerCorrupt
from core.public_export import MARKER_NAME, excluded_streams
from core.seal import require_seal
from core.snapshot import full_entries
from core.signing import (UNVERIFIABLE_CODES,
                          MISSING_SIG,
                          SIGNED_LEDGERS, SIGNED_STREAMS, UNVERIFIABLE,
                          VERIFIED, SigningRefused, load_key, sign_anchor,
                          sig_path, strict_signature_required, is_stream_scope,
                          initial_signature_scope, discovered_stream_scopes,
                          ED25519, load_trusted_public_key, parse_signature,
                          read_regular, signature_message, verify_signature_bytes,
                          discovered_snapshot_scopes)

ROOT = Path(__file__).resolve().parent.parent

# القائمة مصدرُها الوحيد `core.signing.SIGNED_LEDGERS` — فلا تفترقان
GOVERNING = tuple(sorted(SIGNED_LEDGERS))
# هذان محليان غير مدفوعين؛ غيابهما الكامل متوقع في استنساخ جديد.
# بقية القائمة حاكمة مطلوبة؛ غيابها عطب لا تخطٍّ.
OPTIONAL_LOCAL = frozenset({
    "ledger/main.jsonl", "corpus/lexicons-local/_catalog.jsonl",
}) | SIGNED_STREAMS


def _check_content_before_signing(ledger: Ledger) -> None:
    """التوقيع الأول أو تجديده لا يعفي السلسلة والختم من المطابقة.

    require_seal يفحص التوقيع قبل محتوى الختم؛ قد يرمي signature_missing
    في التوقيع الأول الصارم أو signature_mismatch في --resign. لذلك
    نثبت المحتوى مستقلًا قبل التعامل مع هذين الإذنين المحدودين.
    """
    ledger.verify_chain()
    anchor = ledger.read_anchor()
    if (type(anchor["count"]) is not int or anchor["count"] != ledger.count()
            or anchor["head"] != ledger.head()):
        raise LedgerCorrupt("السلسلة لا تطابق رأس وعدد ختم الاعتماد تمامًا")


def _verify_public_generation(ledger: Ledger, *, root: Path, public_key: bytes) -> None:
    for path in (ledger.path, ledger.anchor_path, sig_path(ledger)):
        read_regular(path, root=root)
    _check_content_before_signing(ledger)
    raw = read_regular(sig_path(ledger), root=root)
    algorithm, _ = parse_signature(raw)
    if algorithm != ED25519:
        raise SigningRefused("signature_algorithm_mismatch", "gate requires Ed25519")
    message = signature_message(ledger, read_regular(ledger.anchor_path, root=root),
                                algorithm=ED25519, root=root)
    verify_signature_bytes(raw, message, public_key=public_key)


def verify_repository(root: Path, *, public_key: bytes) -> dict:
    """Public-only gate for a stable candidate snapshot using this trusted code.

    No candidate module is imported, no private getter is called, and HMAC is
    never accepted. The caller supplies its independently approved key bytes;
    the candidate policy must also match this verifier's reviewed policy pin.
    """
    root = Path(root).absolute()
    result = {"verified": 0, "skipped": 0, "failures": []}
    try:
        if not isinstance(public_key, bytes) or len(public_key) != 32:
            raise SigningRefused("signing_public_key_invalid", "expected key must be raw32")
        if root.is_symlink() or not root.is_dir():
            raise SigningRefused("signing_path_invalid", "snapshot root must be a directory")
        trusted = load_trusted_public_key(root=root)
        if not hmac.compare_digest(trusted, public_key):
            raise SigningRefused("signing_public_key_mismatch", "candidate key differs from caller's trust")
        scopes = sorted(SIGNED_LEDGERS | discovered_stream_scopes(root)
                        | discovered_snapshot_scopes(root, scopes=SIGNED_LEDGERS))
    except SigningRefused as exc:
        result["failures"].append(exc.code)
        return result
    # اللقطةُ العامة (ك٢٧) تسمّي في علامتها السجلاتِ الحاكمةَ المستبعَدة؛ غيابُها
    # الكامل تخطٍّ معلن، وسجلٌّ حاضرٌ بلا مرساة يبقى عطبًا مهما قالت العلامة.
    excluded = excluded_streams(root)
    for rel in scopes:
        ledger = Ledger(root / rel, create=False)
        files = (ledger.path, ledger.anchor_path, sig_path(ledger))
        present = tuple(p.exists() or p.is_symlink() for p in files)
        # Validate parents even when all children are absent: a dangling alias
        # cannot masquerade as an optional local store that was never present.
        try:
            parent = root
            for part in Path(rel).parts[:-1]:
                parent /= part
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    raise SigningRefused("signing_path_invalid", "aliased parent")
            if (rel in OPTIONAL_LOCAL or is_stream_scope(rel) or rel in excluded) and not any(present):
                result["skipped"] += 1
                continue
            full_entries(ledger.path, generation_check=lambda generation:
                         _verify_public_generation(generation, root=root, public_key=public_key))
            result["verified"] += 1
        except (SigningRefused, LedgerCorrupt, OSError, ValueError, KeyError, TypeError) as exc:
            code = exc.code if isinstance(exc, SigningRefused) else "ledger_or_signature_invalid"
            result["failures"].append(f"{rel}: {code}")
    return result


def main() -> int:
    verify_only = len(sys.argv) > 1 and sys.argv[1] == "verify"
    if "--public-only" in sys.argv:
        if not verify_only:
            print("--public-only requires verify")
            return 1
        try:
            public = load_trusted_public_key(root=ROOT)
            report = verify_repository(ROOT, public_key=public)
        except SigningRefused as exc:
            report = {"verified": 0, "skipped": 0, "failures": [exc.code]}
        # **«تعذَّر» ليس «فشلًا»** (ق٢٥): غيابُ مكتبة التعمية حدُّ بيئة
        # لا دليلَ عبث — كان يخرج 1 معلنًا سجلاتٍ سليمةً «مكسورة»،
        # وهو النمط الذي أوقف جلسةً كاملة من قبل (تدقيق ق٣٩)
        blocked = [f for f in report["failures"]
                   if f.rsplit(": ", 1)[-1] in UNVERIFIABLE_CODES]
        real = [f for f in report["failures"] if f not in blocked]
        report["failures"] = real
        if blocked:
            report["unverifiable"] = len(blocked)
            report["unverifiable_reason"] = blocked[0].rsplit(": ", 1)[-1]
        print(json.dumps(report, sort_keys=True))
        if real:
            return 1
        if blocked:
            return 2 if strict_signature_required() else 3
        return 0
    if "--require-signature" in sys.argv:
        os.environ["DIWAN_REQUIRE_SIGNATURE"] = "1"
    environment_missing = 0
    resign = "--resign" in sys.argv
    verified = unverifiable = missing = signed = 0
    failures: list[str] = []
    try:
        scopes = sorted(set(GOVERNING) | discovered_stream_scopes(ROOT)
                        | discovered_snapshot_scopes(ROOT, scopes=GOVERNING))
    except SigningRefused as exc:
        print(f"✗ {exc.code}")
        return 1
    excluded = excluded_streams(ROOT)
    for rel in scopes:
        led = Ledger(ROOT / rel, create=False)
        files = (led.path, led.anchor_path, sig_path(led))
        present = tuple(p.exists() or p.is_symlink() for p in files)
        if (rel in OPTIONAL_LOCAL or is_stream_scope(rel)) and not any(present):
            print(f"  تخطٍّ معلن (مخزن محلي غائب): {rel}")
            continue
        if rel in excluded and not any(present):
            print(f"  تخطٍّ معلن (مستبعدٌ من اللقطة العامة، {MARKER_NAME}): {rel}")
            continue
        if any(p.is_symlink() or (p.exists() and not p.is_file()) for p in files):
            failures.append(rel)
            print(f"  ✗ ledger_path_invalid: {rel} — ملف غير عادي أو رابط رمزي")
            continue
        if not present[0]:
            failures.append(rel)
            print(f"  ✗ ledger_missing: {rel} — سجل حاكم غائب أو آثار يتيمة")
            continue
        if not present[1]:
            failures.append(rel)
            print(f"  ✗ anchor_missing: {rel} — سجل موجود بلا مرساة اعتماد")
            continue

        if not verify_only:
            try:
                full_entries(led.path, generation_check=_check_content_before_signing)
                if initial_signature_scope(rel) and not sig_path(led).exists():
                    raise SigningRefused(
                        "initial_signature_plan_required",
                        "التوقيع الأول يتطلب خطة tools/migrate_signatures.py موثقة")
                state = require_seal(led, rel)   # لا توقيعَ فوق ختمٍ مكسور
            except SigningRefused as exc:
                if exc.code != "signature_missing" and not (
                        exc.code == "signature_mismatch" and resign):
                    failures.append(rel)
                    print(f"  ✗ {exc.code}: {rel} — "
                          "التوقيع المخالف لا يُمحى إلا بـ--resign بعد فحصٍ يدوي")
                    continue
            except LedgerCorrupt as exc:
                failures.append(rel)
                print(f"  ✗ ختمٌ مكسور، لا يُوقَّع: {rel} — {str(exc)[:60]}")
                continue
            try:
                sign_anchor(led)
            except (SigningRefused, LedgerCorrupt) as exc:
                failures.append(rel)
                print(f"  ✗ signing refused: {rel} — {exc}")
                continue
            print(f"  وُقّع: {rel}")
            signed += 1
            continue

        try:
            state = require_seal(led, rel)       # الختم أولًا في كل حال
            full_entries(led.path)
        except (LedgerCorrupt, SigningRefused) as exc:
            if isinstance(exc, SigningRefused) and exc.code in UNVERIFIABLE_CODES:
                environment_missing += 1
            else:
                failures.append(rel)
            print(f"  ✗ فشل: {rel} — {type(exc).__name__}: {str(exc)[:60]}")
            continue
        # الحكمُ من حالة السجل نفسه لا من حضور المفتاح
        if state == VERIFIED:
            verified += 1
            print(f"  ✓ ختمٌ وتوقيعٌ مفحوصان: {rel}")
        elif state == UNVERIFIABLE:
            unverifiable += 1
            print(f"  ◻ ختمٌ سليم، التوقيع متعذِّر (لا مفتاح): {rel}")
        elif state == MISSING_SIG:
            missing += 1
            print(f"  ✗ سجلٌّ حاكم **بلا توقيع**: {rel}")
        else:
            failures.append(rel)
            print(f"  ✗ حالة غير متوقعة ({state}): {rel}")

    if not verify_only:
        print(f"\nوُقّع {signed} مرساة"
              + (f"، وفشل {len(failures)}" if failures else "") + ".")
        return 1 if failures else 0
    if failures or missing:
        print(f"\n✗ فشلٌ حقيقي: {len(failures)} سجلًّا مكسورًا"
              f"{f' و{missing} بلا توقيع' if missing else ''} — "
              f"يُفحص يدويًّا.")
        return 1
    if environment_missing:
        print("✗ strict historical HMAC verification needs its key")
        return 2
    if unverifiable:
        print(f"\n◻ {unverifiable + verified} ختمًا سليمًا؛ التوقيعُ "
              f"تعذَّر فحصه في {unverifiable} لغياب المفتاح — **ليس "
              f"دليل عبث**. ضمانةُ الختم (ق٢١) قائمة كاملةً.")
        return 3
    print(f"\n✓ {verified} مرساة: ختمًا وتوقيعًا مفحوصين.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
