#!/usr/bin/env python3
"""يتحقّق من أن حكمًا بشريًّا موقَّعٌ بمفتاح المالك — أو يوقّعه على ماكه (ق٤٥).

`verify` يعمل في أيِّ مكان: لا يحتاج إلا المفتاحَ العامّ في المستودع.
`sign` لا يعمل إلا على ماك المالك: البذرةُ الخاصة في سلسلة المفاتيح، ولا
يملكها عميل، ولا تمرّ بملفٍّ ولا بمتغيّر بيئة.

ورقمٌ يُنشر عن أداءٍ بشريِّ التحكيم يلزمه خروجُ صفرٍ من `verify` — وإلّا
فهو رقمٌ آليّ يُسمّى باسمه.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.canonical import PayloadRejected
from evaluation.human_review import load_json
from evaluation.verdict_signature import certify_review, sign_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "sign"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="جذرُ المستودع الذي يُقرأ منه المفتاح العام")
    args = parser.parse_args(argv)
    try:
        report = load_json(args.report)
        review = load_json(args.review)
        if args.command == "verify":
            result = certify_review(report, review, root=args.root)
            result["status"] = "certified"
        else:
            from core.signing import load_ed25519_private_key
            signature = sign_review(report, review,
                                    private_seed=load_ed25519_private_key())
            result = {"status": "signed", "owner_signature": signature,
                      "hint": "ضَع القيمة في حقل owner_signature داخل ملف الحكم"}
    except PayloadRejected as exc:
        result = {"status": "refused", "code": exc.code,
                  "path": getattr(exc, "path", None),
                  "reason": getattr(exc, "reason", None)}
    except Exception as exc:  # مثل SigningRefused على غير ماك
        result = {"status": "refused", "code": getattr(exc, "code", "verdict_check_failed"),
                  "reason": str(exc)[:300]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in ("certified", "signed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
