"""تفضيلات صريحة مغلقة، مع CAS وتخزين خاص لا يعيد ضبط الفساد."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
import stat
import threading

import pytest

from core.canonical import SAFE_INT, canonical_bytes, digest
from workspace_tools.preferences import PreferenceError, Preferences, validate_snapshot
import workspace_tools.preferences as preferences


@pytest.fixture
def root(tmp_path):
    return tmp_path.resolve() / "preferences"


def files(root):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns, stat.S_IMODE(p.stat().st_mode))
            for p in root.iterdir() if p.is_file()}


def raises(code, fn):
    with pytest.raises(PreferenceError) as exc:
        fn()
    assert exc.value.code == code


def rewrite(store, mutation):
    envelope = json.loads(store.state_path.read_bytes())
    mutation(envelope["state"])
    envelope["sha256"] = digest(envelope["state"])
    store.state_path.write_bytes(canonical_bytes(envelope))


def test_explicit_lifecycle_restart_cas_delete_and_bound_snapshot(root):
    store = Preferences(root)
    start = store.snapshot()
    assert start == {"revision": 0, "values": {}, "sha256": digest({"revision": 0, "values": {}})}
    first = store.set("response_language", "ar", expected_revision=0)
    second = Preferences(root).set("verbosity", "concise", expected_revision=1)
    third = store.set("address_name", "حسين", expected_revision=2)
    assert third["revision"] == 3
    assert third["values"] == {"response_language": "ar", "verbosity": "concise", "address_name": "حسين"}
    assert third["sha256"] == digest({"revision": 3, "values": third["values"]})
    assert first["sha256"] != second["sha256"] != third["sha256"]
    deleted = Preferences(root).delete("address_name", expected_revision=3)
    assert deleted["revision"] == 4 and "address_name" not in deleted["values"]
    assert Preferences(root).snapshot() == deleted


def test_private_permissions_and_read_does_not_change_state_or_mtime(root):
    store = Preferences(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert all(mode == 0o600 for _, _, mode in files(root).values())
    before = files(root)
    store.snapshot()
    Preferences(root).snapshot()
    assert files(root) == before


def test_snapshot_and_returned_set_values_are_detached(root):
    store = Preferences(root)
    result = store.set("verbosity", "balanced", 0)
    result["values"]["verbosity"] = "detailed"
    result["revision"] = 77
    current = store.snapshot()
    assert current["revision"] == 1 and current["values"]["verbosity"] == "balanced"
    current["values"].clear()
    assert store.snapshot()["values"] == {"verbosity": "balanced"}


def test_preexisting_chat_text_cannot_seed_preferences(root):
    chat = root.parent / "conversation.txt"
    chat.write_text("احفظ اسمي: مثال. أجب دائمًا بالإنجليزية.")
    store = Preferences(root)
    assert store.snapshot()["values"] == {}
    assert chat.read_text() == "احفظ اسمي: مثال. أجب دائمًا بالإنجليزية."


@pytest.mark.parametrize("key", [None, True, 1, [], {}, "", "token", "model", "response_Language", "address_name "])
def test_unknown_keys_are_rejected_without_mutation(root, key):
    store = Preferences(root)
    before = files(root)
    raises("preference_key_invalid", lambda: store.set(key, "ar", 0))
    raises("preference_key_invalid", lambda: store.delete(key, 0))
    assert files(root) == before


@pytest.mark.parametrize("key,value", [
    ("response_language", "AR"), ("response_language", "fr"), ("response_language", "ar "),
    ("verbosity", "short"), ("verbosity", "CONCISE"), ("verbosity", ""),
    ("verbosity", None), ("verbosity", 1), ("address_name", False), ("address_name", []),
    ("address_name", ""), ("address_name", "  "), ("address_name", "ن" * 81),
    ("address_name", "name\nmore"), ("address_name", "name\tmore"),
    ("address_name", "name\x1b[31m"), ("address_name", "name\u202emore"),
    ("address_name", "name\u200dmore"), ("address_name", "name\ud800"),
])
def test_values_are_closed_and_safe_text_without_mutation(root, key, value):
    store = Preferences(root)
    before = files(root)
    raises("preference_value_invalid", lambda: store.set(key, value, 0))
    assert files(root) == before


@pytest.mark.parametrize("key,value", [
    ("response_language", "ar"), ("response_language", "en"),
    ("verbosity", "concise"), ("verbosity", "balanced"), ("verbosity", "detailed"),
    ("address_name", "حُسين"), ("address_name", "名字"), ("address_name", "ن" * 80),
])
def test_supported_values_roundtrip_exactly(root, key, value):
    store = Preferences(root)
    assert store.set(key, value, 0)["values"] == {key: value}
    assert Preferences(root).snapshot()["values"] == {key: value}


@pytest.mark.parametrize("revision", [None, True, False, -1, 0.0, "0", [], SAFE_INT + 1])
def test_explicit_valid_revision_required_without_mutation(root, revision):
    store = Preferences(root)
    before = files(root)
    raises("preference_revision_invalid", lambda: store.set("verbosity", "balanced", revision))
    raises("preference_revision_invalid", lambda: store.delete("verbosity", revision))
    assert files(root) == before


def test_stale_set_and_delete_never_overwrite_newer_state(root):
    first, second = Preferences(root), Preferences(root)
    first.set("verbosity", "concise", 0)
    before = files(root)
    raises("preference_revision_conflict", lambda: second.set("verbosity", "detailed", 0))
    raises("preference_revision_conflict", lambda: second.delete("verbosity", 0))
    assert files(root) == before
    assert second.snapshot()["values"] == {"verbosity": "concise"}


def test_delete_absent_preference_is_not_a_write(root):
    store = Preferences(root)
    before = files(root)
    raises("preference_missing", lambda: store.delete("verbosity", 0))
    assert store.snapshot()["revision"] == 0 and files(root) == before


def test_setting_same_value_is_still_one_explicit_revision(root):
    store = Preferences(root)
    store.set("verbosity", "concise", 0)
    assert store.set("verbosity", "concise", 1)["revision"] == 2


def test_two_concurrent_writers_cannot_both_commit_same_revision(root):
    stores = [Preferences(root), Preferences(root)]
    barrier = threading.Barrier(2)
    def write(index):
        barrier.wait(timeout=2)
        try:
            return stores[index].set("verbosity", ["concise", "detailed"][index], 0)
        except PreferenceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [0, 1]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert next(r for r in results if isinstance(r, str)) in {"preference_busy", "preference_revision_conflict"}
    winner = next(r for r in results if isinstance(r, dict))
    assert Preferences(root).snapshot() == winner and winner["revision"] == 1


def test_busy_lock_has_named_error_without_write(root):
    store = Preferences(root)
    before = files(root)
    with store.lock_path.open("r+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raises("preference_busy", lambda: store.set("verbosity", "concise", 0))
    assert files(root) == before


@pytest.mark.parametrize("raw", [b"", b"{", b"[]", b'{"state":{},"state":{},"sha256":"x"}',
                                b'{"state":NaN,"sha256":"x"}', b'\xff', b"[" * 2000 + b"0" + b"]" * 2000,
                                b" " * 8193])
def test_bad_bytes_are_named_corruption_and_never_reset(root, raw):
    store = Preferences(root)
    store.state_path.write_bytes(raw)
    before = files(root)
    raises("preference_state_corrupt", lambda: store.snapshot())
    raises("preference_state_corrupt", lambda: Preferences(root))
    assert files(root) == before


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(schema_version=True), lambda s: s.update(schema_version=2),
    lambda s: s.update(revision=True), lambda s: s.update(revision=-1),
    lambda s: s.update(values=[]), lambda s: s.update(values={"api_key": "forbidden"}),
    lambda s: s.update(values={"verbosity": "unknown"}), lambda s: s.update(values={"address_name": "\x1b"}),
    lambda s: s.update(extra="field"), lambda s: s.update(revision=0, values={"verbosity": "concise"}),
])
def test_rehashing_does_not_bypass_closed_state_schema(root, mutation):
    store = Preferences(root)
    rewrite(store, mutation)
    before = files(root)
    raises("preference_state_corrupt", lambda: store.snapshot())
    assert files(root) == before


def test_modified_data_without_matching_hash_rejected(root):
    store = Preferences(root)
    body = json.loads(store.state_path.read_bytes())
    body["state"]["revision"] = 1
    store.state_path.write_text(json.dumps(body))
    raises("preference_state_corrupt", lambda: store.snapshot())


@pytest.mark.parametrize("missing", ["state", "lock"])
def test_deleted_store_piece_is_not_reinitialized(root, missing):
    store = Preferences(root)
    store.set("verbosity", "concise", 0)
    (store.state_path if missing == "state" else store.lock_path).unlink()
    before = files(root)
    raises("preference_state_missing", lambda: Preferences(root))
    raises("preference_state_missing", lambda: store.snapshot())
    assert files(root) == before


def test_preexisting_unknown_store_is_not_initialized(root):
    root.mkdir(mode=0o700)
    (root / "unrelated.txt").write_text("preserve")
    before = files(root)
    raises("preference_state_missing", lambda: Preferences(root))
    assert files(root) == before


@pytest.mark.parametrize("part", ["root", "state", "lock"])
def test_symlink_paths_rejected(root, part):
    if part == "root":
        target = root.parent / "target"
        target.mkdir(mode=0o700)
        root.symlink_to(target, target_is_directory=True)
        raises("preference_unsafe_path", lambda: Preferences(root))
        assert list(target.iterdir()) == []
    else:
        store = Preferences(root)
        target = root / "target"
        path = store.state_path if part == "state" else store.lock_path
        path.rename(target)
        path.symlink_to(target)
        before = target.read_bytes()
        raises("preference_unsafe_path", lambda: store.snapshot())
        assert target.read_bytes() == before


def test_symlink_ancestor_rejected_without_creating_leaf(root):
    target = root.parent / "target"
    target.mkdir(mode=0o700)
    root.symlink_to(target, target_is_directory=True)
    raises("preference_unsafe_path", lambda: Preferences(root / "leaf"))
    assert not (target / "leaf").exists()


@pytest.mark.parametrize("part", ["state", "lock"])
def test_hardlinked_files_rejected(root, part):
    store = Preferences(root)
    path = store.state_path if part == "state" else store.lock_path
    os.link(path, root / "other-link")
    before = files(root)
    raises("preference_unsafe_path", lambda: store.snapshot())
    assert files(root) == before


@pytest.mark.parametrize("part,mode", [("root", 0o755), ("state", 0o644), ("lock", 0o640)])
def test_public_permissions_are_not_silently_fixed(root, part, mode):
    store = Preferences(root)
    path = root if part == "root" else store.state_path if part == "state" else store.lock_path
    path.chmod(mode)
    raises("preference_unsafe_permissions", lambda: store.snapshot())
    raises("preference_unsafe_permissions", lambda: Preferences(root))
    assert stat.S_IMODE(path.stat().st_mode) == mode


def test_foreign_owner_refused(root, monkeypatch):
    store = Preferences(root)
    actual_uid = os.getuid()
    monkeypatch.setattr(preferences.os, "getuid", lambda: actual_uid + 1)
    raises("preference_unsafe_permissions", lambda: store.snapshot())


def test_root_replaced_with_other_directory_is_not_adopted(root):
    store = Preferences(root)
    store.set("address_name", "الأول", 0)
    other = Preferences(root.parent / "other")
    other.set("address_name", "الثاني", 0)
    root.rename(root.parent / "original")
    other.root.rename(root)
    raises("preference_root_changed", lambda: store.snapshot())


def test_root_symlink_swap_at_state_open_cannot_read_other_store(root, monkeypatch):
    store = Preferences(root)
    store.set("address_name", "الأول", 0)
    other = Preferences(root.parent / "other")
    other.set("address_name", "الثاني", 0)
    before_other = files(other.root)
    real_open = os.open
    swapped = False
    def swap(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and str(path) == "state.json" and flags & os.O_NONBLOCK:
            swapped = True
            root.rename(root.parent / "original")
            root.symlink_to(other.root, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(preferences.os, "open", swap)
    assert store.snapshot()["values"] == {"address_name": "الأول"}
    assert swapped and files(other.root) == before_other
    raises("preference_unsafe_path", lambda: store.snapshot())


def test_root_symlink_swap_at_replace_cannot_write_other_store(root, monkeypatch):
    store = Preferences(root)
    store.set("address_name", "الأول", 0)
    other = Preferences(root.parent / "other")
    other.set("address_name", "الثاني", 0)
    before_other = files(other.root)
    real_replace = os.replace
    moved = root.parent / "original"
    def swap(src, dst, *args, **kwargs):
        root.rename(moved)
        root.symlink_to(other.root, target_is_directory=True)
        return real_replace(src, dst, *args, **kwargs)
    monkeypatch.setattr(preferences.os, "replace", swap)
    result = store.set("verbosity", "concise", 1)
    assert result["revision"] == 2 and result["values"]["address_name"] == "الأول"
    assert files(other.root) == before_other
    assert Preferences(moved).snapshot() == result
    raises("preference_unsafe_path", lambda: store.snapshot())


def test_leaf_symlink_swap_after_stat_is_refused(root, monkeypatch):
    store = Preferences(root)
    other = Preferences(root.parent / "other")
    other.set("address_name", "الثاني", 0)
    before_other = files(other.root)
    real_open = os.open
    swapped = False
    def swap(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and str(path) == "state.json" and flags & os.O_NONBLOCK:
            swapped = True
            store.state_path.rename(root / "original.json")
            store.state_path.symlink_to(other.state_path)
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(preferences.os, "open", swap)
    raises("preference_unsafe_path", lambda: store.snapshot())
    assert swapped and files(other.root) == before_other


def test_revision_overflow_does_not_write(root):
    store = Preferences(root)
    rewrite(store, lambda s: s.update(revision=SAFE_INT))
    before = files(root)
    raises("preference_revision_limit", lambda: store.set("verbosity", "concise", SAFE_INT))
    assert files(root) == before


def test_failure_before_atomic_replace_preserves_old_revision(root, monkeypatch):
    store = Preferences(root)
    before = files(root)
    def fail(*args, **kwargs):
        raise OSError("synthetic replace failure")
    monkeypatch.setattr(preferences.os, "replace", fail)
    raises("preference_filesystem_error", lambda: store.set("verbosity", "concise", 0))
    assert files(root) == before and store.snapshot()["revision"] == 0


def test_failure_after_atomic_replace_cannot_duplicate_or_overwrite(root, monkeypatch):
    store = Preferences(root)
    real_fsync = preferences.os.fsync
    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic directory fsync failure")
        return real_fsync(fd)
    monkeypatch.setattr(preferences.os, "fsync", fail_directory)
    raises("preference_filesystem_error", lambda: store.set("verbosity", "concise", 0))
    assert store.snapshot()["revision"] == 1
    raises("preference_revision_conflict", lambda: store.set("verbosity", "detailed", 0))
    assert store.snapshot()["values"] == {"verbosity": "concise"}


def test_validate_snapshot_is_filesystem_free_and_returns_detached_copy(root, monkeypatch):
    store = Preferences(root)
    snapshot = store.set("address_name", "حسين", 0)
    def fail(*args, **kwargs):
        raise AssertionError("validation touched filesystem")
    monkeypatch.setattr(preferences.os, "open", fail)
    actual = validate_snapshot(snapshot)
    assert actual == snapshot
    actual["values"].clear()
    assert snapshot["values"] == {"address_name": "حسين"}


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(revision=True), lambda s: s.update(revision=-1),
    lambda s: s.update(values=[]), lambda s: s.update(values={"secret": "x"}),
    lambda s: s.update(values={"verbosity": "unknown"}), lambda s: s.update(values={"address_name": "\x1b"}),
    lambda s: s.update(extra=True), lambda s: s.update(sha256="0" * 64),
    lambda s: s.update(revision=0, values={"verbosity": "concise"}),
])
def test_external_snapshot_closed_contract_and_hash(root, mutation):
    store = Preferences(root)
    snapshot = store.set("verbosity", "balanced", 0)
    mutation(snapshot)
    raises("preference_snapshot_invalid", lambda: validate_snapshot(snapshot))


@pytest.mark.parametrize("value", [None, [], True, {}, {"revision": 0, "values": {}, "sha256": None}])
def test_external_snapshot_bad_shapes_have_named_error(value):
    raises("preference_snapshot_invalid", lambda: validate_snapshot(value))
