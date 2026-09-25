#!/usr/bin/env python3
"""Owner-run build of reviewed CI images; never run from a candidate checkout.

Network/package installation is limited to this explicit preparation step.
Afterwards the push gate uses the receipt's immutable image ID, offline.
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
import tempfile

IMAGE_REF = re.compile(r"(?:docker\.io/)?(?:library/)?(?:python|node):[A-Za-z0-9_.-]+@sha256:[0-9a-f]{64}\Z")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")


def run(argv: list[str], **kwargs) -> str:
    return subprocess.run(argv, check=True, text=True, stdout=subprocess.PIPE, **kwargs).stdout.strip()


def canonical_image_id(reference: str) -> str:
    """Resolve BuildKit's manifest/index IID to Docker's config image ID.

    Receipts and docker run/image inspect must use the same identity namespace.
    This inspects the local image store only; it never pulls an image.
    """
    image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", reference])
    if not IMAGE_ID.fullmatch(image_id):
        raise ValueError("Docker inspect did not return one canonical image ID")
    return image_id


def build_image_id(iid: Path) -> str:
    reference = iid.read_text().strip()
    if not IMAGE_ID.fullmatch(reference):
        raise ValueError("build did not return a SHA-256 image reference")
    return canonical_image_id(reference)


def local_runtime_tag(image_id: str) -> str:
    """Give FROM a valid local reference without overwriting another image.

    Docker parses FROM sha256:<config-ID> as an image repository, not an ID.
    The deterministic tag is scoped to this image's full immutable identity.
    A pre-existing tag with a different target is an error, never overwritten.
    Docker/its owner remain trusted; this is not protection from a hostile daemon.
    """
    if not IMAGE_ID.fullmatch(image_id) or canonical_image_id(image_id) != image_id:
        raise ValueError("runtime must be a canonical local image ID")
    tag = "diwan-runtime:sha256-" + image_id.removeprefix("sha256:")
    present = run(["docker", "image", "ls", "--quiet", "--no-trunc",
                   "--filter", f"reference={tag}"])
    if present:
        if canonical_image_id(tag) != image_id:
            raise ValueError("local runtime tag already refers to another image")
    else:
        run(["docker", "image", "tag", image_id, tag])
    if canonical_image_id(tag) != image_id:
        raise ValueError("local runtime tag identity verification failed")
    return tag


def validate_trust(path: Path) -> str:
    raw = path.read_bytes()
    trust = json.loads(raw)
    if (set(trust) != {"schema_version", "algorithm", "public_key_hex", "policy_sha256"}
            or trust["schema_version"] != 1 or trust["algorithm"] != "ED25519"
            or not HEX.fullmatch(trust["public_key_hex"])
            or not HEX.fullmatch(trust["policy_sha256"])):
        raise ValueError("invalid external public trust receipt")
    return hashlib.sha256(raw).hexdigest()


def write_receipt(path: Path, value: dict) -> None:
    # Refuse overwriting an existing trusted receipt as a side effect of build.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def probe(image_id: str) -> dict:
    code = ("import importlib.metadata as m,json,platform,subprocess; "
            "print(json.dumps({'python_version':platform.python_version(),"
            "'node_major':int(subprocess.check_output(['/usr/local/bin/node','-p',"
            "'process.versions.node.split(\".\")[0]'],text=True)),"
            "'pytest':m.version('pytest'),'cryptography':m.version('cryptography')}))")
    result = json.loads(run(["docker", "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                            "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt",
                            "no-new-privileges", image_id, "/opt/venv/bin/python", "-I", "-c", code]))
    if not result["python_version"].startswith("3.14.") or result["node_major"] != 24:
        raise ValueError("runtime versions do not satisfy Python 3.14 / Node 24")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-image", required=True, help="official python:3.14-slim-bookworm@sha256:...")
    parser.add_argument("--node-image", required=True, help="official node:24-bookworm-slim@sha256:...")
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--trust-json", type=Path, required=True, help="reviewed external public-key trust file")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--runner-receipt", type=Path, help="also build the disposable runner image")
    args = parser.parse_args()
    for ref, prefix in ((args.python_image, "python:3.14"), (args.node_image, "node:24")):
        if not IMAGE_REF.fullmatch(ref) or prefix not in ref:
            parser.error("base images must be the expected official runtimes pinned by SHA-256 digest")
    if args.receipt.exists() or (args.runner_receipt and args.runner_receipt.exists()):
        parser.error("receipt already exists; choose a new reviewed cache location")
    trust_hash = validate_trust(args.trust_json)
    lock = args.lock.read_bytes()
    lock_hash = hashlib.sha256(lock).hexdigest()
    source = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="diwan-trusted-build-") as temporary:
        context = Path(temporary)
        shutil.copyfile(source / "Dockerfile.runtime", context / "Dockerfile")
        (context / "requirements-ci.lock").write_bytes(lock)
        iid = context / "image-id"
        subprocess.run(["docker", "build", "--platform", "linux/arm64", "--iidfile", str(iid),
                        "--build-arg", f"PYTHON_IMAGE={args.python_image}", "--build-arg", f"NODE_IMAGE={args.node_image}",
                        "--build-arg", f"LOCK_SHA256={lock_hash}", str(context)], check=True)
        image_id = build_image_id(iid)
        versions = probe(image_id)
        receipt = {"schema_version": 1, "image_id": image_id, "lock_sha256": lock_hash,
                   "python_version": versions["python_version"], "node_major": 24,
                   "trust_sha256": trust_hash}
        write_receipt(args.receipt, receipt)
        if args.runner_receipt:
            # A separate minimal context; neither source code nor trust/keys is copied.
            runner_context = context / "runner"
            runner_context.mkdir()
            shutil.copyfile(source / "Dockerfile.runner", runner_context / "Dockerfile")
            shutil.copyfile(source / "runner_entrypoint.py", runner_context / "runner_entrypoint.py")
            runtime_tag = local_runtime_tag(image_id)
            # Resolve immediately before use; --pull=false avoids refreshing the
            # existing local reference. Use the local Docker engine builder.
            if canonical_image_id(runtime_tag) != image_id:
                raise ValueError("runtime tag changed before runner build")
            subprocess.run(["docker", "build", "--pull=false", "--platform", "linux/arm64",
                            "--iidfile", str(iid), "--build-arg",
                            f"RUNTIME_IMAGE={runtime_tag}", str(runner_context)], check=True)
            if canonical_image_id(runtime_tag) != image_id:
                raise ValueError("runtime tag changed during runner build")
            runner_id = build_image_id(iid)
            write_receipt(args.runner_receipt, {**receipt, "image_id": runner_id,
                                               "runtime_image_id": image_id, "runner_version": "2.337.0",
                                               "runner_mode": "ephemeral-only-v1"})
    print("Prepared immutable runtime receipt; no runner was registered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
