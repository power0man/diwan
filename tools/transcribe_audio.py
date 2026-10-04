"""تفريغ WAV عربي مختار صراحةً بمحوّل whisper.cpp التجريبي المحلي."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from multimodal.codec import read_selected
from multimodal.local_asr import transcribe


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--binary-sha256", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        selected = read_selected(args.file)
        result = transcribe(base64.b64decode(selected["data_base64"]),
                            binary=args.binary, binary_sha256=args.binary_sha256,
                            model=args.model, model_sha256=args.model_sha256,
                            timeout=args.timeout, threads=args.threads)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "error", "error_code": getattr(exc, "code", "asr_operation_failed"),
                          "release_ready": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
