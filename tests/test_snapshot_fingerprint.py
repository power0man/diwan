from core.snapshot import get_snapshot_sha256

def test_snapshot_deterministic_hash():
    d1 = {"b": 2, "a": 1, "c": [3, 4]}
    d2 = {"c": [3, 4], "a": 1, "b": 2}
    h1 = get_snapshot_sha256(d1)
    h2 = get_snapshot_sha256(d2)
    assert h1 == h2
    assert len(h1) == 64

def test_snapshot_arabic_keys_deterministic():
    d = {"حالة": "سليمة", "معرف": 100}
    h = get_snapshot_sha256(d)
    assert len(h) == 64
