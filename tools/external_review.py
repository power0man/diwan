#!/usr/bin/env python3
"""تشغيل المراجعة الخارجية لبنك قياس عبر Ollama على الماك (ق٥٠).

    python3 tools/external_review.py evaluation/banks/kimi_v1
    python3 tools/external_review.py evaluation/banks/kimi_v1 \\
        --reviewer deepseek-v4-flash:cloud --reviewer mistral-large-3:675b-cloud

يتصل بخادم Ollama المحليّ وحده (127.0.0.1)، والخادمُ يمرّر النداء إلى النماذج
السحابية كما يفعل اليوم مع gpt-oss:120b-cloud. ولا يطبع محتوى الحالات أبدًا:
الخلاصةُ أعدادٌ وκ وطولُ قائمة المالك.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.external_review import (DEFAULT_REVIEWERS, review_bank,  # noqa: E402
                                        smoke, summarize)
from evaluation.multi_system_review import AutomaticReviewError  # noqa: E402

MAX_RESPONSE_BYTES = 8_000_000


class OllamaChat:
    """نداءُ /api/chat على خادمٍ محليٍّ فقط، بلا وكيلٍ ولا تحويل."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: int = 900):
        if not base_url.startswith(("http://127.0.0.1:", "http://localhost:", "http://[::1]:")):
            raise AutomaticReviewError("local_endpoint_required", base_url)
        self.url = base_url.rstrip("/") + "/api/chat"
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, model: str, system: str, user: str, schema: dict) -> str:
        payload = {"model": model, "stream": False, "format": schema,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                   "options": {"temperature": 0, "seed": 0, "num_ctx": 65536}}
        request = urllib.request.Request(
            self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise AutomaticReviewError(f"http_{exc.code}", model) from exc
        except TimeoutError as exc:
            raise AutomaticReviewError("transport_timeout", model) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AutomaticReviewError("transport_error", model) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AutomaticReviewError("response_too_large", model)
        try:
            content = json.loads(raw)["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AutomaticReviewError("ollama_response_malformed", model) from exc
        if not isinstance(content, str):
            raise AutomaticReviewError("ollama_response_malformed", model)
        return content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bank", type=Path, nargs="?", help="مجلّد البنك، وفيه open/")
    parser.add_argument("--smoke", type=Path, metavar="OUT.json",
                        help="تجربةٌ حيّة على ثلاث حالاتٍ مصطنعة فيها خطأٌ مزروع، "
                             "ويُكتب تقريرها في OUT.json")
    parser.add_argument("--reviewer", action="append", dest="reviewers",
                        help="نموذجُ مراجعٍ في Ollama (يتكرّر)")
    parser.add_argument("--brief", type=Path, default=ROOT / "docs" / "REVIEWER-BRIEF.md")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args(argv)
    reviewers = args.reviewers or list(DEFAULT_REVIEWERS)
    if args.smoke:
        import tempfile
        try:
            with tempfile.TemporaryDirectory() as tmp:
                report = smoke(Path(tmp), reviewers, OllamaChat(args.base_url),
                               brief_path=args.brief)
        except AutomaticReviewError as exc:
            print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
            return 2
        args.smoke.parent.mkdir(parents=True, exist_ok=True)
        args.smoke.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
        print(json.dumps({"status": report["status"], "reviewers": report["reviewers"],
                          "out": str(args.smoke)}, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    if args.bank is None:
        parser.error("مجلّد البنك مطلوب، أو --smoke")
    try:
        counts = review_bank(args.bank, reviewers, OllamaChat(args.base_url),
                             brief_path=args.brief)
        summary = summarize(args.bank)
    except AutomaticReviewError as exc:
        print(json.dumps({"status": "refused", "code": exc.code, "detail": str(exc)},
                         ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": "failed" if counts["failed"] else "reviewed",
        **counts,
        "pairs": summary["pairs"],
        "errors": len(summary["errors"]),
        "owner_queue": len(summary["owner_queue"]),
        "summary": str(args.bank / "reviews" / "SUMMARY.json"),
    }, ensure_ascii=False, indent=2))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
