"""Duplicate owner memories keep distinct items and one exposure fingerprint."""
import pytest

from evaluation.memory_runner import _Wired
from memory.store import MemoryStore, turn_memory, valid_turn_memory
from tests.test_memory_provenance_forget import Derived
from tests.test_memory_wired import _ask


def test_repeated_text_has_one_valid_exposure_fingerprint(tmp_path):
    store = MemoryStore(tmp_path.resolve())
    first = store.remember("Azure polygon record 75109", consent="owner")
    second = store.remember("Azure polygon record 75109", consent="owner")
    assert first != second
    memory = turn_memory(store, "Azure polygon record")
    assert valid_turn_memory(memory)
    assert len(memory["items"]) == 1
    assert len(store.items()) == 2


@pytest.mark.parametrize("kind", ["agent", "text"])
def test_duplicate_memories_do_not_corrupt_the_next_turn(tmp_path, kind):
    wired = _Wired(tmp_path.resolve() / "ui")
    wired.provider = Derived()
    try:
        ids = wired.project("duplicates")
        for _ in range(2):
            wired.api("memory_remember", project=ids["id"], text="Azure polygon record 75109")
        for _ in range(2):
            response, _ = _ask(wired, "duplicates", kind, "Azure polygon record")
            assert response["status"] == "complete"
        wired.close()
        wired._open()
        response, _ = _ask(wired, "duplicates", kind, "Azure polygon record")
        assert response["status"] == "complete"
        assert len(wired.api("memory", project=ids["id"])["items"]) == 2
    finally:
        wired.close()
