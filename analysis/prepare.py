#!/usr/bin/env python3
"""بناءُ صورة المحلّل وإيصالِها (ج٨). يشغّله المالكُ مرّةً على جهازه، ولا يُعرض أداةً للنموذج.

    python3 analysis/prepare.py --receipt ~/.config/diwan/analysis-receipt.json

الشبكةُ في هذه الخطوة وحدها (سحبُ الأساس المثبَّت ببصمته وحزمِ القفل المثبَّتة ببصماتها). وبعدها يعمل
التحليلُ بلا شبكة، ويتحقّق `core/execution.py` في كل نداءٍ من أن الصورة المحلية هي التي في الإيصال.
والإيصالُ ملفٌّ خاصّ (0600) خارج مساحات العمل؛ ولا يُكتب فوق إيصالٍ قائم إلا بـ`--replace`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
VERSION = re.compile(r"3\.(?:1[2-9]|[2-9][0-9])\.\d+\Z")
TAG = "diwan-analysis:local"


def _run(argv: list[str]) -> str:
    return subprocess.run(argv, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def lock_sha256() -> str:
    return hashlib.sha256((HERE / "requirements.lock").read_bytes()).hexdigest()


def build(docker: str, *, pip_ca: Path | None = None) -> tuple[str, str]:
    """يبني من `analysis/` (وملفُّ .dockerignore يحصر السياق في ثلاثة ملفّات)، ويعيد هويّةَ الصورة وبصمةَ القفل."""
    digest = lock_sha256()
    with tempfile.TemporaryDirectory() as scratch:
        iid = Path(scratch) / "iid"
        argv = [docker, "build", "--pull", "--build-arg", f"LOCK_SHA256={digest}", "--iidfile", str(iid),
                "--tag", TAG]
        if pip_ca is not None:
            argv += ["--secret", f"id=pip-ca,src={pip_ca}"]
        subprocess.run([*argv, str(HERE)], check=True)
        reference = iid.read_text().strip()
    image_id = _run([docker, "image", "inspect", "--format", "{{.Id}}", reference])
    if not IMAGE_ID.fullmatch(image_id):
        raise SystemExit("لم يُعِد Docker هويّةَ صورةٍ قانونية")
    return image_id, digest


def python_version(docker: str, image_id: str) -> str:
    version = _run([docker, "run", "--rm", "--network=none", "--pull=never", "--entrypoint=/opt/venv/bin/python",
                    image_id, "-I", "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"])
    if not VERSION.fullmatch(version):
        raise SystemExit(f"نسخةُ بايثون في الصورة غيرُ مقبولة: {version!r}")
    return version


def write_receipt(path: Path, receipt: dict, *, replace: bool) -> None:
    path = path.expanduser().absolute()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise SystemExit(f"الإيصالُ موجود: {path} (استعمل --replace)")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--receipt", type=Path, required=True, help="مسارُ الإيصال الخاصّ خارج مساحات العمل")
    parser.add_argument("--replace", action="store_true", help="يستبدل إيصالًا قائمًا")
    parser.add_argument("--docker", default=shutil.which("docker") or "docker")
    parser.add_argument("--pip-ca", type=Path, help="شهادةُ وكيلٍ يعيد توقيع TLS، تُمرَّر سرَّ بناءٍ لا طبقة")
    args = parser.parse_args(argv)
    image_id, digest = build(args.docker, pip_ca=args.pip_ca)
    receipt = {"schema_version": 1, "image_id": image_id, "lock_sha256": digest,
               "python_version": python_version(args.docker, image_id)}
    write_receipt(args.receipt, receipt, replace=args.replace)
    print(json.dumps({"status": "prepared", "receipt": str(args.receipt), **receipt}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
