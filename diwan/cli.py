"""Run the installed application without depending on a source checkout."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def runtime_root() -> Path:
    bundled = Path(__file__).resolve().parent / "_runtime"
    return bundled if bundled.is_dir() else Path(__file__).resolve().parents[1]


def data_root() -> Path:
    """Mutable state stays outside the installation, including after an upgrade."""
    configured = os.environ.get("DIWAN_DATA_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return (base / "diwan").resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ديوان — مساعد عام محوره العربية")
    parser.add_argument("command", choices=("serve", "check", "camel-data"),
                        help="serve: الواجهة، check: فحص الجهاز، camel-data: قاعدة الصرف")
    args = list(sys.argv[1:] if argv is None else argv)
    # Let each command own its options, including --help.
    parsed = parser.parse_args(args[:1])
    rest = args[1:]
    root = runtime_root()
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "tools"))
    if parsed.command == "serve":
        from serve_ui import main as serve
        return serve(rest, default_root=data_root() / "daily-ui")
    if parsed.command == "check":
        from launch_check import main as check
        return check(rest)
    return subprocess.call([sys.executable, "-m", "camel_tools.cli.camel_data", *rest])
