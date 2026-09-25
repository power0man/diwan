"""Only mocked C functions: no OS Keychain, subprocess or real key access."""
import ctypes
from types import SimpleNamespace

import pytest
import core.signing as s


class NativeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class FakeSecurity:
    def __init__(self, payload=b"0123456789abcdef"*4, *, status=0, reported_length=None,
                 null=False, free_status=0, find_error=False):
        self.payload = payload
        self.buffer = ctypes.create_string_buffer(payload)
        self.status, self.null, self.free_status = status, null, free_status
        self.reported_length = len(payload) if reported_length is None else reported_length
        self.find_error = find_error
        self.calls, self.releases = [], []
        self.SecKeychainFindGenericPassword = NativeFunction(self.find)
        self.SecKeychainItemFreeContent = NativeFunction(self.release)

    def find(self, chain, service_length, service, account_length, account, length, data, item):
        self.calls.append((chain, service_length, service, account_length, account, item))
        ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32))[0] = self.reported_length
        ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0] = None if self.null else ctypes.addressof(self.buffer)
        if self.find_error:
            raise OSError("synthetic native diagnostics must not be disclosed")
        return self.status

    def release(self, attributes, data):
        self.releases.append((attributes, data.value))
        return self.free_status


@pytest.fixture(autouse=True)
def never_real_keychain(monkeypatch):
    monkeypatch.setattr(s.sys, "platform", "darwin")
    def forbidden(*args, **kwargs): pytest.fail("real OS/subprocess/legacy lookup attempted")
    monkeypatch.setattr(s.ctypes, "CDLL", forbidden)
    monkeypatch.setattr(s.subprocess, "run", forbidden)
    monkeypatch.setattr(s, "_keychain_key", forbidden)
    monkeypatch.setenv(s.ENV_KEY, "synthetic-env-must-never-be-used")
    monkeypatch.setenv("DIWAN_ED25519_PRIVATE_KEY", "also-not-an-authority")


def install(monkeypatch, fake):
    loads = []
    def load(path):
        loads.append(path)
        return fake
    monkeypatch.setattr(s.ctypes, "CDLL", load)
    return loads


def test_native_seed_read_uses_exact_c_abi_and_releases_buffer(monkeypatch, capsys):
    fake = FakeSecurity()
    loads = install(monkeypatch, fake)
    assert s.load_ed25519_private_key() == bytes.fromhex(fake.payload.decode())
    assert loads == ["/System/Library/Frameworks/Security.framework/Security"]
    service, account = s.ED25519_KEYCHAIN_SERVICE.encode(), s.KEYCHAIN_ACCOUNT.encode()
    assert fake.calls == [(None, len(service), service, len(account), account, None)]
    assert fake.releases == [(None, ctypes.addressof(fake.buffer))]
    assert fake.SecKeychainFindGenericPassword.argtypes == [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    assert fake.SecKeychainFindGenericPassword.restype is ctypes.c_int32
    assert fake.SecKeychainItemFreeContent.argtypes == [ctypes.c_void_p, ctypes.c_void_p]
    assert fake.SecKeychainItemFreeContent.restype is ctypes.c_int32
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("status,code", [
    (-25300, "signing_key_missing"), (-25308, "signing_keychain_interaction_required"),
    (-25315, "signing_keychain_interaction_required"), (-25293, "signing_keychain_access_denied"),
    (-128, "signing_keychain_cancelled"), (-25291, "signing_keychain_unavailable"),
    (-50, "signing_keychain_read_refused")])
def test_native_failures_are_named_without_fallback_or_data_read(monkeypatch, status, code):
    fake = FakeSecurity(status=status)
    install(monkeypatch, fake)
    monkeypatch.setattr(s.ctypes, "string_at", lambda *args: pytest.fail("read native buffer on failed status"))
    with pytest.raises(s.SigningRefused) as caught: s.load_ed25519_private_key()
    assert caught.value.code == code
    assert len(fake.releases) == 1
    assert fake.payload.decode() not in str(caught.value)


@pytest.mark.parametrize("payload", [b"a"*63, b"a"*65, b"a"*64+b"\n", b"A"*64,
    b"g"*64, b"\xff"*64, b"a"*63+b"\x00", b" a"*32, b""])
def test_native_seed_format_is_exact_and_buffer_always_freed(monkeypatch, payload):
    fake = FakeSecurity(payload)
    install(monkeypatch, fake)
    with pytest.raises(s.SigningRefused, match="signing_key_invalid"):
        s.load_ed25519_private_key()
    assert len(fake.releases) == 1


@pytest.mark.parametrize("length,null", [(0, False), (2**32-1, False), (63, False), (64, True)])
def test_bad_lengths_or_null_pointer_are_rejected_before_memory_read(monkeypatch, length, null):
    fake = FakeSecurity(reported_length=length, null=null)
    install(monkeypatch, fake)
    monkeypatch.setattr(s.ctypes, "string_at", lambda *args: pytest.fail("unsafe native memory read"))
    with pytest.raises(s.SigningRefused, match="signing_key_invalid"):
        s.load_ed25519_private_key()
    assert len(fake.releases) == (0 if null else 1)


def test_native_call_exception_is_sanitized_and_allocated_data_freed(monkeypatch):
    fake = FakeSecurity(find_error=True)
    install(monkeypatch, fake)
    with pytest.raises(s.SigningRefused, match="signing_keychain_read_refused") as caught:
        s.load_ed25519_private_key()
    assert "diagnostics" not in str(caught.value)
    assert len(fake.releases) == 1


def test_release_failure_is_named_and_does_not_return_secret(monkeypatch):
    fake = FakeSecurity(free_status=-50)
    install(monkeypatch, fake)
    with pytest.raises(s.SigningRefused, match="signing_keychain_release_failed"):
        s.load_ed25519_private_key()
    assert len(fake.releases) == 1


@pytest.mark.parametrize("mode", ["missing_library", "missing_symbol"])
def test_unavailable_native_api_has_no_fallback(monkeypatch, mode):
    def fail(*args): raise OSError("synthetic loader diagnostics")
    monkeypatch.setattr(s.ctypes, "CDLL", fail if mode == "missing_library" else lambda *args: SimpleNamespace())
    with pytest.raises(s.SigningRefused, match="signing_keychain_unavailable") as caught:
        s.load_ed25519_private_key()
    assert "diagnostics" not in str(caught.value)


def test_non_darwin_does_not_even_load_library(monkeypatch):
    monkeypatch.setattr(s.sys, "platform", "linux")
    with pytest.raises(s.SigningRefused, match="signing_keychain_platform_required"):
        s.load_ed25519_private_key()
