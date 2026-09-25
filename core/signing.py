"""Policy-bound anchor signatures: legacy HMAC and public-only Ed25519 reads.

The reviewed verifier and its policy pin are the trust boundary. A checkout that
can replace both verifier and pins is not independently authenticated by itself.
Signature publication is atomic; ledger/anchor/signature are not one transaction.
"""
from __future__ import annotations

import hashlib
import ctypes
import hmac
import os
import json
import re
import stat
import tempfile
from dataclasses import dataclass, field
import subprocess
import sys
from pathlib import Path

from core.ledger import Ledger, LedgerCorrupt

KEYCHAIN_SERVICE = "diwan-anchor"
KEYCHAIN_ACCOUNT = "diwan"
ENV_KEY = "DIWAN_ANCHOR_KEY"
STRICT_ENV = "DIWAN_REQUIRE_SIGNATURE"
ALG = "HMAC-SHA256"  # historical API and exact HMAC message remain unchanged
ED25519 = "ED25519"
ED25519_KEYCHAIN_SERVICE = "diwan-anchor-ed25519"
POLICY_PATH = "keys/anchor-policy.json"
PUBLIC_KEY_PATH = "keys/anchor-ed25519.pub"
# Activated only by reviewed migration. None permits legacy only when no policy
# exists; an unpinned policy is never an authority to switch algorithms.
PINNED_POLICY_SHA256: str | None = "742da5e260db1447f9f0065e8963ba4755c7842fac596238f90fd17ba88fe340"
ED25519_DOMAIN = b"diwan-anchor-ed25519-v1\x00"

UNSIGNED = "unsigned"
VERIFIED = "verified"
UNVERIFIABLE = "unverifiable_no_key"
MISSING_SIG = "signature_missing"

# **«تعذَّر التحقق» ليس «فشلَه»** (ق٢٥): غيابُ المفتاح وغيابُ مكتبة
# التعمية سواءٌ — كلاهما حدُّ بيئةٍ لا دليلَ عبث. إدراجُ الثاني هنا
# أُغفل عند ترحيل Ed25519 (ق٣٨) فعاد القفلُ من بابٍ آخر: بيئةٌ بلا
# `cryptography` لم تعد تقرأ أيَّ سجلٍّ حاكم، وأداةُ التحقق أعلنت
# «15 سجلًّا مكسورًا» عن سجلاتٍ سليمة تمامًا.
UNVERIFIABLE_CODES = frozenset({"signing_key_missing",
                                "signing_backend_missing"})

ROOT = Path(__file__).resolve().parent.parent

SIGNED_STREAMS = frozenset(
    f"streams/{node}/{box}.jsonl"
    for node in ("gateway", "linguistics", "maritime")
    for box in ("inbox", "outbox")
)
# Adding an obligation is not permission to bless old state automatically.
# Existing unsigned anchors in these scopes require a reviewed first-sign plan.
INITIAL_SIGNATURE_MIGRATION = SIGNED_STREAMS | {"publish/_manifest.jsonl"}

# **السجلات الحاكمة التي يجب أن تكون موقَّعة** — التوقّعُ في الكود
# المراجَع لا في وجود ملفٍ مجاور: حذفُ `.sig` كان يُعيد السجل «بلا
# توقيع» صامتًا حتى تحت الصرامة، فيُفتح بالضبط ما زعم ق٢٣ إغلاقه
# (قاتل تدقيق ق٢٥). المسارات نسبيةٌ لجذر المستودع.
SIGNED_LEDGERS = frozenset({
    "ledger/main.jsonl",
    "sources/acquisitions.jsonl",
    "registry/nodes.jsonl",
    "glossaries/maritime.jsonl",
    "rulings/precedence.jsonl",
    "corpus/maritime/_catalog.jsonl",
    "corpus/lexicons/_catalog.jsonl",
    "corpus/lexicons-local/_catalog.jsonl",
}) | INITIAL_SIGNATURE_MIGRATION

# إنذارُ العجز يُعلَن مرةً لكل (سجلٍّ، سبب) في العملية — إعلانٌ لا
# ثرثرة. و`announcements()` تتيح للمستدعي قراءةَ ما أُعلن برمجيًّا
# (كان الإعلان قناةً واحدة إلى stderr لا أثرَ لها في أي تقرير)
_announced: dict = {}


def _announce(ledger: Ledger, msg: str) -> None:
    key = (str(ledger.path), msg.split("—")[0].strip())
    if key not in _announced:
        _announced[key] = msg
        print(msg, file=sys.stderr)


def announcements() -> list:
    """ما أُعلن في هذه العملية — للتقارير وأدلة القبول."""
    return sorted(_announced.values())


class SigningRefused(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code = code
        self.reason = reason


SECURITY_BIN = "/usr/bin/security"   # مطلقٌ لا عبر PATH: مساره متغيرُ
# بيئةٍ أيضًا، و«security» مزيَّفٌ في PATH كان يفرض مفتاح المهاجم
# بأولويةٍ أعلى من متغير البيئة الذي هُبِّط لأجل هذا الخطر (تدقيق ق٢٥)


def _keychain_key() -> bytes | None:
    if not Path(SECURITY_BIN).exists():
        return None            # لا سلسلة مفاتيح على هذا النظام أصلًا
    try:
        r = subprocess.run(
            [SECURITY_BIN, "find-generic-password", "-s", KEYCHAIN_SERVICE,
             "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # عطلُ الاستدعاء ليس «لا قيد»: يُعلَن ولا يُبتلع
        print(f"⚠ تعذَّر استدعاء سلسلة المفاتيح: {exc}", file=sys.stderr)
        return None
    if r.returncode != 0:
        if r.returncode not in (44, 36):   # 44: لا قيد، 36: مستخدم ألغى
            print(f"⚠ سلسلة المفاتيح ردّت {r.returncode}: "
                  f"{r.stderr.strip()[:80]}", file=sys.stderr)
        return None
    # يُشذَّب سطرُ security الختامي وحده — السرُّ لا يُطبَّع (تدقيق م٧)
    key = r.stdout.removesuffix("\n")
    return key.encode("utf-8") if key else None


def load_key() -> bytes:
    """**سلسلة المفاتيح أولًا** — جذرُ الثقة خارج نظام الملفات والبيئة
    كليهما؛ والبيئة احتياطُ اختبار/CI حيث لا سلسلة (كان الترتيب معكوسًا
    فمن يحقن البيئة يوقّع بمفتاحه — تدقيق م٧). السرُّ لا يُطبَّع."""
    kc = _keychain_key()
    if kc:
        return kc
    env = os.environ.get(ENV_KEY, "")
    if env:
        return env.encode("utf-8")
    raise SigningRefused(
        "signing_key_missing",
        f"لا مفتاح توقيع: لا {ENV_KEY} في البيئة ولا قيد "
        f"«{KEYCHAIN_SERVICE}» في سلسلة المفاتيح — إنشاؤه بيد المالك: "
        f"security add-generic-password -s {KEYCHAIN_SERVICE} "
        f"-a {KEYCHAIN_ACCOUNT} -w '<سر-عشوائي-طويل>'")


def sig_path(ledger: Ledger) -> Path:
    return ledger.anchor_path.with_suffix(
        ledger.anchor_path.suffix + ".sig")


def policy_document(public_key: bytes) -> dict:
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        raise SigningRefused("signing_public_key_invalid", "public key must be 32 bytes")
    return {"schema_version": 1, "algorithm": ED25519,
            "public_key_path": PUBLIC_KEY_PATH,
            "public_key_sha256": hashlib.sha256(public_key).hexdigest()}


def canonical_policy_bytes(policy: dict) -> bytes:
    return (json.dumps(policy, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("ascii")


def _absolute_signing_path(path: Path) -> Path:
    # Ledger opens its original path. Collapsing '..' before checking a symlink
    # would validate a different file than the OS actually opens.
    path = Path(path)
    if ".." in path.parts:
        raise SigningRefused("signing_path_invalid", "parent traversal in signing path")
    return Path(os.path.abspath(path))


def _require_canonical_spelling(path: Path) -> None:
    """Reject filesystem aliases, without equating distinct case-sensitive names.

    resolve() does not canonicalize case or Unicode on APFS. Compare each
    existing component to the exact directory-entry spelling and its lstat
    identity instead. Missing suffixes remain possible for new ledger creation;
    every existing prefix is checked. This is not a hostile rename-race sandbox.
    """
    path = _absolute_signing_path(path)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        candidate = current / part
        try:
            expected = candidate.lstat()
        except FileNotFoundError:
            return
        except OSError:
            raise SigningRefused("signing_path_invalid", "cannot validate signing path spelling") from None
        try:
            with os.scandir(current) as entries:
                found = next((entry for entry in entries if entry.name == part), None)
                if found is None:
                    raise SigningRefused("signing_path_invalid", "noncanonical filesystem spelling")
                actual = found.stat(follow_symlinks=False)
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise SigningRefused("signing_path_invalid", "signing path identity changed")
        except OSError:
            raise SigningRefused("signing_path_invalid", "cannot validate signing path identity") from None
        current = candidate


def read_regular(path: Path, *, root: Path | None = None) -> bytes:
    """Reject aliases and special files. The caller owns a stable snapshot/root."""
    path = _absolute_signing_path(path)
    _require_canonical_spelling(path)
    resolved_path = path.resolve()
    if resolved_path != path:
        _require_canonical_spelling(resolved_path)
    if root is not None:
        root = _absolute_signing_path(root)
        _require_canonical_spelling(root)
        resolved_root = root.resolve()
        if resolved_root != root:
            _require_canonical_spelling(resolved_root)
        if root.is_symlink():
            raise SigningRefused("signing_path_invalid", "aliased trusted root")
        try:
            relative = path.absolute().relative_to(root)
        except ValueError as exc:
            raise SigningRefused("signing_path_invalid", "path outside trusted root") from exc
        current = root
        for part in relative.parts[:-1]:
            current /= part
            if current.is_symlink() or not current.is_dir():
                raise SigningRefused("signing_path_invalid", "aliased or missing parent")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SigningRefused("signing_path_invalid", "not an independent regular file")
            return stream.read()
    except OSError as exc:
        raise SigningRefused("signing_path_invalid", "cannot read regular signing file") from exc


def load_signing_policy(*, root: Path | None = None) -> dict | None:
    root = ROOT if root is None else Path(root)
    path = root / POLICY_PATH
    if PINNED_POLICY_SHA256 is None:
        if path.exists() or path.is_symlink():
            raise SigningRefused("signing_policy_untrusted", "policy has no reviewed pin")
        return None
    raw = read_regular(path, root=root)
    if hashlib.sha256(raw).hexdigest() != PINNED_POLICY_SHA256:
        raise SigningRefused("signing_policy_mismatch", "policy differs from reviewed pin")
    try:
        policy = json.loads(raw)
        if (not isinstance(policy, dict)
                or set(policy) != {"schema_version", "algorithm", "public_key_path", "public_key_sha256"}
                or type(policy["schema_version"]) is not int or policy["schema_version"] != 1
                or policy["algorithm"] != ED25519
                or policy["public_key_path"] != PUBLIC_KEY_PATH
                or not isinstance(policy["public_key_sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", policy["public_key_sha256"]) is None
                or raw != canonical_policy_bytes(policy)):
            raise ValueError("invalid policy schema or encoding")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise SigningRefused("signing_policy_invalid", "invalid canonical signing policy") from exc
    return policy


def load_trusted_public_key(*, root: Path | None = None) -> bytes:
    root = ROOT if root is None else Path(root)
    policy = load_signing_policy(root=root)
    if policy is None:
        raise SigningRefused("signing_policy_missing", "Ed25519 requires an approved policy")
    public = read_regular(root / PUBLIC_KEY_PATH, root=root)
    if len(public) != 32 or hashlib.sha256(public).hexdigest() != policy["public_key_sha256"]:
        raise SigningRefused("signing_public_key_mismatch", "public key differs from approved identity")
    return public


def _ed25519_backend():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:
        raise SigningRefused("signing_backend_missing", "Ed25519 backend is unavailable") from exc
    return Ed25519PrivateKey, Ed25519PublicKey, InvalidSignature


def load_ed25519_private_key() -> bytes:
    """Signing only: read the hex seed in this Python process, with no fallback.

    Uses the same Security.framework client identity as anchor_keys.create.
    The OS may still require owner interaction/Keychain unlock; this function
    never changes ACLs, replaces an item, or launches a child to carry the seed.
    """
    if sys.platform != "darwin":
        raise SigningRefused("signing_keychain_platform_required", "Ed25519 private key requires macOS Keychain")
    try:
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        find = security.SecKeychainFindGenericPassword
        find.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
                         ctypes.c_uint32, ctypes.c_char_p,
                         ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p),
                         ctypes.POINTER(ctypes.c_void_p)]
        find.restype = ctypes.c_int32
        release = security.SecKeychainItemFreeContent
        release.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        release.restype = ctypes.c_int32
    except (OSError, AttributeError, ctypes.ArgumentError):
        raise SigningRefused("signing_keychain_unavailable", "Security.framework is unavailable") from None
    service = ED25519_KEYCHAIN_SERVICE.encode("utf-8")
    account = KEYCHAIN_ACCOUNT.encode("utf-8")
    length = ctypes.c_uint32(0)
    data = ctypes.c_void_p()
    try:
        status = find(None, len(service), service, len(account), account,
                      ctypes.byref(length), ctypes.byref(data), None)
        if status != 0:
            code = {
                -25300: "signing_key_missing",                 # errSecItemNotFound
                -25308: "signing_keychain_interaction_required", # errSecInteractionNotAllowed
                -25315: "signing_keychain_interaction_required", # errSecInteractionRequired
                -25293: "signing_keychain_access_denied",       # errSecAuthFailed
                -128: "signing_keychain_cancelled",             # errSecUserCanceled
                -25291: "signing_keychain_unavailable",         # errSecNotAvailable
            }.get(status, "signing_keychain_read_refused")
            raise SigningRefused(code, "Ed25519 private-key read refused by Keychain")
        # Check length and pointer BEFORE reading native memory. Keychain stores
        # exactly 64 lowercase ASCII hex bytes, without newline or a C terminator.
        if length.value != 64 or not data.value:
            raise SigningRefused("signing_key_invalid", "Ed25519 private seed must be canonical hex")
        encoded = ctypes.string_at(data, length.value)
        if re.fullmatch(rb"[0-9a-f]{64}", encoded) is None:
            raise SigningRefused("signing_key_invalid", "Ed25519 private seed must be canonical hex")
        return bytes.fromhex(encoded.decode("ascii"))
    except (OSError, AttributeError, ctypes.ArgumentError):
        raise SigningRefused("signing_keychain_read_refused", "Native Keychain read failed") from None
    finally:
        # Find allocates passwordData. itemRef was NULL, so no retained item
        # reference exists. Free the allocated content even on refusal/format
        # failure; never include native diagnostics or key bytes in exceptions.
        if data.value:
            try:
                released = release(None, data)
            except (OSError, AttributeError, ctypes.ArgumentError):
                raise SigningRefused("signing_keychain_release_failed", "Native Keychain content release failed") from None
            if released != 0:
                raise SigningRefused("signing_keychain_release_failed", "Native Keychain content release failed")


def parse_signature(raw: bytes) -> tuple[str, bytes]:
    """One exact ASCII algorithm/hex line. No strip, unknown fields or suffixes."""
    for algorithm, size in ((ALG, 64), (ED25519, 128)):
        if re.fullmatch(algorithm.encode() + b":[0-9a-f]{" + str(size).encode() + b"}\n", raw):
            return algorithm, bytes.fromhex(raw[len(algorithm) + 1:-1].decode("ascii"))
    raise LedgerCorrupt("signature format invalid")


def signature_message(ledger: Ledger, anchor_bytes: bytes, *, algorithm: str = ALG,
                      root: Path | None = None) -> bytes:
    scoped = ledger_scope(ledger, root=root).encode("utf-8") + b"\x00" + anchor_bytes
    if algorithm == ALG:
        return scoped
    if algorithm == ED25519:
        return ED25519_DOMAIN + scoped
    raise SigningRefused("signature_algorithm_invalid", "unsupported signature algorithm")


def _mac(key: bytes, ledger: Ledger, anchor_bytes: bytes) -> str:
    return hmac.new(key, signature_message(ledger, anchor_bytes), hashlib.sha256).hexdigest()


def verify_signature_bytes(raw: bytes, message: bytes, *, public_key: bytes | None = None,
                           legacy_key: bytes | None = None) -> bool:
    """Pure cryptographic verification with explicit key material. No key lookup."""
    algorithm, signature = parse_signature(raw)
    if algorithm == ALG:
        if legacy_key is None:
            raise SigningRefused("signing_key_missing", "historical HMAC verification needs its key")
        valid = hmac.compare_digest(signature, hmac.new(legacy_key, message, hashlib.sha256).digest())
    else:
        _, PublicKey, InvalidSignature = _ed25519_backend()
        if not isinstance(public_key, bytes) or len(public_key) != 32:
            raise SigningRefused("signing_public_key_invalid", "expected public key must be 32 bytes")
        try:
            PublicKey.from_public_bytes(public_key).verify(signature, message)
            valid = True
        except InvalidSignature:
            valid = False
    if not valid:
        raise SigningRefused("signature_mismatch", "signature does not match anchor and scope")
    return True


@dataclass(frozen=True)
class Signer:
    algorithm: str
    key: bytes = field(repr=False)
    public_key: bytes | None = None

    @property
    def public_key_sha256(self) -> str | None:
        return hashlib.sha256(self.public_key).hexdigest() if self.public_key is not None else None

    def signature_bytes(self, ledger: Ledger, *, anchor_bytes: bytes | None = None,
                        root: Path | None = None) -> bytes:
        return make_signature(ledger, self.key, algorithm=self.algorithm,
                              public_key=self.public_key, root=root, anchor_bytes=anchor_bytes)


def make_signature(ledger: Ledger, key: bytes, *, algorithm: str = ALG,
                   public_key: bytes | None = None, root: Path | None = None,
                   anchor_bytes: bytes | None = None) -> bytes:
    """Create bytes only; migration supplies approved public identity explicitly."""
    if anchor_bytes is None:
        anchor_bytes = read_regular(ledger.anchor_path)
    message = signature_message(ledger, anchor_bytes, algorithm=algorithm, root=root)
    if algorithm == ALG:
        signature = hmac.new(key, message, hashlib.sha256).digest()
    else:
        PrivateKey, _, _ = _ed25519_backend()
        if not isinstance(key, bytes) or len(key) != 32:
            raise SigningRefused("signing_key_invalid", "Ed25519 private seed must be 32 bytes")
        private = PrivateKey.from_private_bytes(key)
        derived = private.public_key().public_bytes_raw()
        if public_key is None or not hmac.compare_digest(derived, public_key):
            raise SigningRefused("signing_key_mismatch", "private key does not match approved public identity")
        signature = private.sign(message)
    return algorithm.encode("ascii") + b":" + signature.hex().encode("ascii") + b"\n"


def _project_scope(ledger: Ledger, root: Path) -> str | None:
    """Use both lexical and resolved identity; an internal alias never escapes policy."""
    root = _absolute_signing_path(root)
    path = _absolute_signing_path(ledger.path)
    _require_canonical_spelling(root)
    resolved_root = root.resolve()
    if resolved_root != root:
        _require_canonical_spelling(resolved_root)
    _require_canonical_spelling(path)
    resolved_path = path.resolve()
    if resolved_path != path:
        _require_canonical_spelling(resolved_path)
    if root.is_symlink():
        raise SigningRefused("signing_path_invalid", "aliased repository root")
    # An external hardlink to governed bytes must not become an unsigned
    # external ledger. Refuse shared file identities before scope classification.
    for artifact in (path, ledger.anchor_path):
        try:
            info = artifact.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise SigningRefused("signing_path_invalid", "cannot validate ledger identity") from None
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise SigningRefused("signing_path_invalid", "shared ledger file identity")
    try:
        relative = path.relative_to(root)
    except ValueError:
        # An external alias into this repository still refers to governed data.
        try:
            return resolved_path.relative_to(resolved_root).as_posix()
        except ValueError:
            return None
    current = root
    if current.is_symlink():
        raise SigningRefused("signing_path_invalid", "aliased repository root")
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise SigningRefused("signing_path_invalid", "aliased repository ledger path")
    return relative.as_posix()


def expected_algorithm(ledger: Ledger) -> str:
    if _project_scope(ledger, ROOT) is None:
        return ALG  # external legacy fixtures are not project policy scopes
    return ED25519 if load_signing_policy() is not None else ALG


def prepare_signer(ledger: Ledger, key: bytes | None = None) -> Signer:
    algorithm = expected_algorithm(ledger)
    if algorithm == ALG:
        return Signer(ALG, load_key() if key is None else key)
    public = load_trusted_public_key()
    PrivateKey, _, _ = _ed25519_backend()  # fail before a secret lookup, never fallback
    key = load_ed25519_private_key() if key is None else key
    if not isinstance(key, bytes) or len(key) != 32:
        raise SigningRefused("signing_key_invalid", "Ed25519 private seed must be 32 bytes")
    if not hmac.compare_digest(PrivateKey.from_private_bytes(key).public_key().public_bytes_raw(), public):
        raise SigningRefused("signing_key_mismatch", "private key does not match approved public identity")
    return Signer(ED25519, key, public)


def atomic_write_signature(path: Path, raw: bytes) -> None:
    """Publish one complete signature; existing readers see old or new bytes."""
    parse_signature(raw)
    if path.exists() or path.is_symlink():
        read_regular(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sign_anchor(ledger: Ledger, key: bytes | None = None, *, signer: Signer | None = None) -> Path:
    if not ledger.anchor_path.exists():
        raise SigningRefused("anchor_missing", "no anchor to sign")
    signer = prepare_signer(ledger, key) if signer is None else signer
    if signer.algorithm != expected_algorithm(ledger):
        raise SigningRefused("signature_algorithm_mismatch", "signer differs from project policy")
    if signer.algorithm == ED25519 and signer.public_key != load_trusted_public_key():
        raise SigningRefused("signing_key_mismatch", "signer identity differs from policy")
    atomic_write_signature(sig_path(ledger), signer.signature_bytes(ledger))
    return sig_path(ledger)


def verify_anchor_signature(ledger: Ledger, key: bytes | None = None) -> bool:
    if not ledger.anchor_path.exists():
        raise SigningRefused("anchor_missing", "no anchor")
    if not sig_path(ledger).exists():
        raise SigningRefused("signature_missing", "anchor has no signature")
    raw = read_regular(sig_path(ledger))
    algorithm, _ = parse_signature(raw)
    if algorithm != expected_algorithm(ledger):
        raise SigningRefused("signature_algorithm_mismatch", "signature differs from project policy")
    message = signature_message(ledger, read_regular(ledger.anchor_path), algorithm=algorithm)
    if algorithm == ED25519:
        return verify_signature_bytes(raw, message, public_key=load_trusted_public_key())
    return verify_signature_bytes(raw, message, legacy_key=load_key() if key is None else key)


def strict_signature_required() -> bool:
    """هل تشترط هذه البيئةُ التوقيعَ شرطًا قاتلًا؟ (جهاز المالك وCI)."""
    return os.environ.get(STRICT_ENV, "").strip() in ("1", "true", "yes")


def ledger_scope(ledger: Ledger, *, root: Path | None = None) -> str:
    """Relative repository identity; no basename collisions or escaping aliases."""
    scope = _project_scope(ledger, ROOT if root is None else root)
    if scope is not None:
        return scope
    if root is not None:
        raise SigningRefused("signing_path_invalid", "scope is outside explicit repository root")
    path = ledger.path.resolve()
    return f"ext:{path.parent.name}/{path.name}"


def is_governing(ledger: Ledger) -> bool:
    scope = _project_scope(ledger, ROOT)
    return scope is not None and (scope in SIGNED_LEDGERS or is_stream_scope(scope)
                                  or snapshot_base_scope(scope) in SIGNED_LEDGERS)


def is_stream_scope(scope: str) -> bool:
    """All node streams are governed, including nodes registered in the future."""
    parts = scope.split("/")
    return (len(parts) == 3 and parts[0] == "streams"
            and parts[1] not in ("", ".", "..")
            and parts[2] in ("inbox.jsonl", "outbox.jsonl"))


def initial_signature_scope(scope: str) -> bool:
    return scope == "publish/_manifest.jsonl" or is_stream_scope(scope)


def require_initial_signature(ledger: Ledger) -> None:
    """A normal write cannot approve an unsigned checkpoint implicitly."""
    if (is_governing(ledger) and initial_signature_scope(ledger_scope(ledger))
            and not sig_path(ledger).exists()):
        raise SigningRefused("initial_signature_plan_required",
                             "توقيع أول موثق مطلوب قبل كتابة البيان أو التيار")


def discovered_stream_scopes(root: Path) -> frozenset[str]:
    """Inventory actual local node directories without following aliases."""
    directory = root / "streams"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise SigningRefused("stream_path_invalid", "مجلد التيارات رابط أو غير مجلد")
    if not directory.exists():
        return frozenset()
    scopes = set()
    for node in directory.iterdir():
        if node.is_symlink() or not node.is_dir():
            raise SigningRefused("stream_path_invalid", "مدخل عقدة ليس مجلدًا مستقلاً")
        scopes.update(f"streams/{node.name}/{box}.jsonl" for box in ("inbox", "outbox"))
    return frozenset(scopes)


def snapshot_base_scope(scope: str) -> str | None:
    match = re.fullmatch(r"(.+)\.snap-([0-9]+)\.jsonl", scope)
    return match[1] + ".jsonl" if match else None


def discovered_snapshot_scopes(root: Path, *, scopes=None) -> frozenset[str]:
    """Inventory governing snapshot families, including orphan sidecars, by name."""
    root = Path(root)
    found = set()
    for scope in (SIGNED_LEDGERS if scopes is None else scopes):
        if is_stream_scope(scope):
            continue  # streams never rotate
        parent = root
        for part in Path(scope).parts[:-1]:
            parent /= part
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise SigningRefused("signing_path_invalid", "aliased snapshot parent")
        if not parent.exists():
            continue
        stem = Path(scope).name.removesuffix(".jsonl")
        pattern = re.compile(re.escape(stem) + r"\.snap-[0-9]+\.jsonl(?:\.anchor(?:\.sig)?)?\Z")
        for artifact in parent.iterdir():
            if pattern.fullmatch(artifact.name):
                name = artifact.name.removesuffix(".sig").removesuffix(".anchor")
                found.add((parent / name).relative_to(root).as_posix())
    return frozenset(found)


def expects_signature(ledger: Ledger) -> bool:
    """هل يجب أن يكون هذا السجل موقَّعًا؟ — بالقائمة المراجَعة في
    الكود أو بوجود توقيعٍ فعلي (فحذفُ الملف لا يُسقط التوقّع)."""
    return is_governing(ledger) or sig_path(ledger).exists()


def anchor_signature_state(ledger: Ledger, what: str = "") -> str:
    """حالةُ توقيع المرساة بلا قتلٍ لمن لا يملك المفتاح.

    يعيد `unsigned` (غيرُ متوقَّع أصلًا) أو `verified` أو
    `unverifiable_no_key` أو `signature_missing` (متوقَّعٌ وغائب —
    مُعلَنان كلاهما)، ويرمي `signature_mismatch` عند اختلافٍ فعلي في
    كل بيئة، و`signing_key_missing`/`signature_missing` قاتلَين إن
    رفعت البيئةُ علمَ الصرامة."""
    if not expects_signature(ledger):
        return UNSIGNED
    if not sig_path(ledger).exists():
        if strict_signature_required() or expected_algorithm(ledger) == ED25519:
            raise SigningRefused(
                "signature_missing",
                f"سجلٌّ حاكم بلا توقيع: {what or ledger_scope(ledger)} — "
                f"حُذف توقيعُه أو لم يُوقَّع بعد")
        _announce(ledger, f"⚠ سجلٌّ حاكم **بلا توقيع**: "
                  f"{what or ledger_scope(ledger)} — حُذف توقيعُه أو لم "
                  f"يُوقَّع؛ ضمانةُ الختم وحدها. للتشديد: {STRICT_ENV}=1")
        return MISSING_SIG
    try:
        verify_anchor_signature(ledger)
    except SigningRefused as exc:
        if exc.code not in UNVERIFIABLE_CODES or strict_signature_required():
            raise
        why = ("لا مفتاح في هذه البيئة"
               if exc.code == "signing_key_missing"
               else "مكتبة التعمية غائبة عن هذا المفسِّر")
        _announce(ledger, f"⚠ تعذَّر التحقق من توقيع المرساة ({why}): "
                  f"{what or ledger_scope(ledger)} — الختمُ مفروضٌ "
                  f"كاملًا والتوقيعُ غير مفحوص. "
                  f"للتشديد: {STRICT_ENV}=1")
        return UNVERIFIABLE
    return VERIFIED


def require_signed_seal(ledger: Ledger, what: str,
                        key: bytes | None = None) -> None:
    """الختم التام + توقيعُه **شرطًا قاطعًا** — لمن يملك المفتاح."""
    from core.seal import require_seal
    require_seal(ledger, what)
    verify_anchor_signature(ledger, key)
