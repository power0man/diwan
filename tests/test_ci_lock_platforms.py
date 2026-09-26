"""ج٥: قفلُ المنصّات الثلاث يثبّت ما يثبّته `requirements-ci.lock` بعينه، ويزيد بصماتِ Linux x86_64.

`requirements-ci.lock` يحمل بصماتِ arm64 وحدها، فيردّ pip عجلتَي `cffi` و`cryptography` على Nitro.
وتغييرُه يُبطل إيصالَ الماك، لأن `tools/verify_gate.py` يطابق قفلَ كل مرشّحٍ ببصمة الإيصال.
فالقفلُ الثاني `ci/requirements-ci.all-platforms.lock` يُبنى منه صورةُ Nitro اليوم، ويحلّ محلّه عند أول تغييرٍ للقفل.
ولا يصحّ ذلك إلا إن بقيا متطابقَين: الإصداراتُ نفسُها، وبصماتُ الماك كلُّها، وبصمةٌ لكل منصّةٍ في العجلات الثنائية.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAC = ROOT / "requirements-ci.lock"
ALL = ROOT / "ci" / "requirements-ci.all-platforms.lock"
PIN = re.compile(r"([A-Za-z0-9_.-]+)==(\S+)((?: --hash=sha256:[0-9a-f]{64})+)")
PLATFORMS = 3                                   # macOS arm64، وLinux arm64، وLinux x86_64


def pins(path: Path) -> list[tuple[str, str, frozenset[str]]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        match = PIN.fullmatch(line)
        assert match, f"{path.name}: سطرٌ غيرُ مثبَّتٍ ببصمة: {line!r}"
        rows.append((match[1].lower(), match[2], frozenset(re.findall(r"[0-9a-f]{64}", match[3]))))
    return rows


def test_both_locks_pin_the_same_packages_at_the_same_versions_in_order():
    assert [(name, version) for name, version, _ in pins(ALL)] == \
        [(name, version) for name, version, _ in pins(MAC)]


def test_every_mac_hash_is_kept_so_the_mac_installs_the_same_files_from_either():
    for (name, _, mac), (_, _, every) in zip(pins(MAC), pins(ALL)):
        assert mac <= every, name


def test_platform_wheels_carry_one_hash_per_platform_and_pure_wheels_stay_single():
    for (name, _, mac), (_, _, every) in zip(pins(MAC), pins(ALL)):
        if len(mac) == 1:
            assert every == mac, name           # py3-none-any: عجلةٌ واحدة لكل المنصّات
        else:
            assert len(every) == PLATFORMS, name


def test_the_header_names_the_three_platforms():
    header = ALL.read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("#") and all(p in header for p in ("macOS arm64", "Linux arm64", "Linux x86_64"))
