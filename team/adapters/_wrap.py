"""غلافُ إطلاق العامل: يسجّل معرّفَ عملية الوكيل نفسِه ورمزَ خروجه في ملفين، فلا يعتمد الدليلُ على بقاء المرسِل حيًّا.

    python -m team.adapters._wrap <exit_file> <child_pid_file> -- <argv...>

المدخلُ (stdin) يُورَّث إلى الوكيل كما هو. معرّفُ الغلاف نفسِه لا يكفي لإثبات الغياب (ملاحظة Codex على #344)، فيُكتب معرّفُ الابن فور إطلاقه.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[2] != "--":
        sys.stderr.write("usage: _wrap <exit_file> <child_pid_file> -- <command...>\n")
        return 64
    exit_file, pid_file, command = Path(argv[0]), Path(argv[1]), argv[3:]
    try:
        proc = subprocess.Popen(command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
    except OSError as exc:
        exit_file.write_text("127", encoding="utf-8")
        sys.stderr.write(f"launch_failed: {exc}\n")
        return 127
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    rc = proc.wait()
    exit_file.write_text(str(rc), encoding="utf-8")
    return rc if rc >= 0 else 128 + (-rc)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
