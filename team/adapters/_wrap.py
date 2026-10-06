"""غلافُ إطلاق العامل: يسجّل معرّفَ عملية الوكيل نفسِه ورمزَ خروجه في ملفين، فلا يعتمد الدليلُ على بقاء المرسِل حيًّا.

    python -m team.adapters._wrap <exit_file> <child_pid_file> -- <argv...>

المدخلُ (stdin) يُورَّث إلى الوكيل كما هو. معرّفُ الغلاف نفسِه لا يكفي لإثبات الغياب (ملاحظة Codex على #344)، فيُكتب معرّفُ الابن فور إطلاقه.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def write_atomic(path: Path, text: str) -> None:
    """ملفٌّ مؤقت ثم استبدال: قتلُ الغلاف وسطَ الكتابة لا يترك ملفَّ خروجٍ فارغًا يُقرأ صفرًا."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[2] != "--":
        sys.stderr.write("usage: _wrap <exit_file> <child_pid_file> -- <command...>\n")
        return 64
    exit_file, pid_file, command = Path(argv[0]), Path(argv[1]), argv[3:]
    try:
        proc = subprocess.Popen(command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
    except OSError as exc:
        write_atomic(exit_file, "127")
        sys.stderr.write(f"launch_failed: {exc}\n")
        return 127
    write_atomic(pid_file, str(proc.pid))
    rc = proc.wait()
    write_atomic(exit_file, str(rc))
    return rc if rc >= 0 else 128 + (-rc)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
