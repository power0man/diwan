"""طبيبُ الأدوات: يثبّت إصدارَ كل CLI وبصمةَ ثنائيّه، ويرفض الإطلاقَ إن تغيّر أحدُهما عن آخر تثبيتٍ ناجح.

الراياتُ أسرعُ ما يتعفّن (Codex وثائقُه متأخّرة عن إصداره، وClaude يغيّر راياته كل أسابيع). فقبل كل إرسالٍ يُقارَن الإصدارُ
والبصمةُ بما ثُبّت في `~/.diwan-team/doctor.json`؛ وما تغيّر يُسمّى (`version_changed:<name>`، `binary_changed:<name>`) ولا
يُطلق حتى يعيد المالك التثبيتَ بـ`--pin` بعد دخانٍ ناجح. **الحدُّ المعلَن:** الطبيبُ يشهد أن الأداة هي التي جُرّبت آخرَ مرة،
لا أن سلوكَها صحيح.

    python3 -m team.doctor            # فحص
    python3 -m team.doctor --pin      # تثبيت الحالي بعد دخانٍ ناجح
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from team import team_home
from team.adapters.base import Adapter

PINS_FILE = "doctor.json"


def fingerprint(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).resolve().read_bytes()).hexdigest()
    except (OSError, RuntimeError):
        return None


def version_of(argv: list[str], runner=subprocess.run) -> str | None:
    try:
        done = runner(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (done.stdout or done.stderr or "").strip().splitlines()
    return out[0].strip() if out else None


def probe(adapter: Adapter, runner=subprocess.run) -> dict:
    exists = Path(adapter.binary).exists()
    return {"name": adapter.spec.name, "family": adapter.spec.family, "exists": exists,
            "version": version_of(adapter.version_argv(), runner) if exists else None,
            "sha256": fingerprint(adapter.binary) if exists else None}


def load_pins(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def check(adapters: list[Adapter], pins_path: Path, *, pin: bool = False, runner=subprocess.run) -> dict:
    pins = load_pins(pins_path)
    tools, findings = [], []
    for adapter in adapters:
        current = probe(adapter, runner)
        name = current["name"]
        if not current["exists"]:
            findings.append(f"binary_missing:{name}")
        else:
            pinned = pins.get(name)
            if pinned is None:
                findings.append(f"unpinned:{name}")
            else:
                if pinned.get("version") != current["version"]:
                    findings.append(f"version_changed:{name}")
                if pinned.get("sha256") != current["sha256"]:
                    findings.append(f"binary_changed:{name}")
        tools.append(current)
    if pin and not any(f.startswith("binary_missing") for f in findings):
        pins_path.parent.mkdir(parents=True, exist_ok=True)
        pins_path.write_text(json.dumps({t["name"]: {"version": t["version"], "sha256": t["sha256"]} for t in tools},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
        findings = [f for f in findings if f.startswith("binary_missing")]
    return {"schema_version": 1, "status": "passed" if not findings else "refused", "findings": findings, "tools": tools,
            "pins": str(pins_path),
            "measurement_limits": ["the_doctor_attests_the_binary_is_the_one_last_smoke_tested_not_that_it_behaves",
                                   "a_shim_or_wrapper_script_is_fingerprinted_as_the_file_on_disk_not_what_it_launches"]}


def default_adapters() -> list[Adapter]:
    from team.adapters import registry
    return list(registry().values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pin", action="store_true")
    args = parser.parse_args(argv)
    report = check(default_adapters(), team_home() / PINS_FILE, pin=args.pin)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
