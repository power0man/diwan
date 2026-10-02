"""Run independent deterministic guard mutations in disposable directories."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run():
    root = Path(__file__).resolve().parent
    rows = [json.loads(line) for line in (root / "mutations.jsonl").read_text().splitlines()]
    results = []
    for row in rows:
        with tempfile.TemporaryDirectory(prefix="diwan-evidence-mutation-") as name:
            target = Path(name)
            for file in ("check.py", "test_check.py"):
                shutil.copyfile(root / file, target / file)
            command = [sys.executable, "-m", "unittest", "test_check.PacketTests." + row["test"]]
            before = subprocess.run(command, cwd=target, capture_output=True, text=True, timeout=30)
            source = (target / "check.py").read_text()
            if before.returncode != 0:
                status = "failing_before_mutation"
            elif source.count(row["old"]) != 1:
                status = "stale"
            else:
                changed = source.replace(row["old"], row["new"])
                compile(changed, "check.py", "exec")
                (target / "check.py").write_text(changed)
                # Avoid bytecode timestamp/size reuse when a replacement has equal length.
                shutil.rmtree(target / "__pycache__", ignore_errors=True)
                after = subprocess.run(command, cwd=target, capture_output=True, text=True, timeout=30)
                status = "killed" if after.returncode != 0 and "FAIL:" in after.stderr else "survived"
            results.append({"id": row["id"], "test": row["test"], "status": status})
    print(json.dumps(results, indent=2))
    return 0 if all(row["status"] == "killed" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(run())
