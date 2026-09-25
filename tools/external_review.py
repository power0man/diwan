#!/usr/bin/env python3
"""تشغيل المراجعة الخارجية لبنك قياس عبر Ollama (ق٥٠)، محليًّا أو على واجهة ollama.com (ق٦٠، ك٣١).

    python3 tools/external_review.py evaluation/banks/kimi_v1
    python3 tools/external_review.py evaluation/banks/kimi_v1 \\
        --reviewer deepseek-v4-flash:cloud --reviewer mistral-large-3:675b-cloud
    OLLAMA_API_KEY=… python3 tools/external_review.py --base-url https://ollama.com --smoke out.json

نقطتان لا ثالثَ لهما: خادمُ Ollama المحليّ (127.0.0.1، بلا وكيلٍ ولا تحويل) يمرّر النداء إلى
النماذج السحابية كما على الماك؛ أو واجهةُ `https://ollama.com` مباشرةً بمفتاحٍ يُقرأ من
البيئة `OLLAMA_API_KEY` وحدها (لا من سطر الأوامر، ولا يُطبع، ولا يُودَع) — وهي طريقُ الجلسات
السحابية التي لا خادمَ فيها. أيُّ نقطةٍ أخرى مرفوضة. ولا يطبع الأداةُ محتوى الحالات أبدًا:
الخلاصةُ أعدادٌ وκ وطولُ قائمة المالك.

**الحدُّ المعلَن:** النداءُ السحابيُّ المباشر لم يُجرَّب حيًّا بعد (ينتظر المفتاح في بيئة الجلسات)،
وأسماءُ النماذج تُمرَّر كما أُعطيت: فإن كانت واجهةُ ollama.com تسمّيها بلا لاحقة `:cloud` فتُعطى كذلك.
"""
from __future__ import annotations

import argparse
import json
import os
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
LOCAL_PREFIXES = ("http://127.0.0.1:", "http://localhost:", "http://[::1]:")
CLOUD_ENDPOINT = "https://ollama.com"          # النقطةُ السحابية الوحيدة المسموحة (ق٦٠)
CLOUD_KEY_ENV = "OLLAMA_API_KEY"               # يُقرأ من البيئة وحدها، ولا يُطبع ولا يُودَع


class OllamaChat:
    """نداءُ /api/chat على خادمٍ محليّ (بلا وكيلٍ ولا تحويل)، أو على ollama.com بمفتاحٍ من البيئة.

    المفتاحُ لا يُحفظ إلا في ترويسة الطلب، ولا يظهر في أي خطأٍ أو تقرير: الأخطاءُ رموزٌ باسم النموذج.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: int = 900,
                 api_key: str | None = None):
        base = base_url.rstrip("/")
        self._headers = {"Content-Type": "application/json"}
        if base_url.startswith(LOCAL_PREFIXES):
            self.cloud = False
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        elif base == CLOUD_ENDPOINT:
            if not api_key:
                raise AutomaticReviewError("cloud_key_missing", f"{CLOUD_KEY_ENV} غيرُ مضبوطٍ في البيئة")
            self.cloud = True
            self.opener = urllib.request.build_opener()      # يمرّ بوكيل البيئة إن وُجد
            self._headers["Authorization"] = f"Bearer {api_key}"
        else:
            raise AutomaticReviewError("local_endpoint_required", base_url)
        self.url = base + "/api/chat"
        self.timeout = timeout

    def __call__(self, model: str, system: str, user: str, schema: dict) -> str:
        payload = {"model": model, "stream": False, "format": schema,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                   "options": {"temperature": 0, "seed": 0, "num_ctx": 65536}}
        request = urllib.request.Request(
            self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=dict(self._headers))
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


def build_transport(base_url: str, environ=os.environ) -> OllamaChat:
    """النقلُ من النقطة المطلوبة؛ والمفتاحُ من البيئة وحدها (لا خيارَ له في سطر الأوامر)."""
    return OllamaChat(base_url, api_key=environ.get(CLOUD_KEY_ENV) or None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bank", type=Path, nargs="?", help="مجلّد البنك، وفيه open/")
    parser.add_argument("--smoke", type=Path, metavar="OUT.json",
                        help="تجربةٌ حيّة على ثلاث حالاتٍ مصطنعة فيها خطأٌ مزروع، "
                             "ويُكتب تقريرها في OUT.json")
    parser.add_argument("--reviewer", action="append", dest="reviewers",
                        help="نموذجُ مراجعٍ في Ollama (يتكرّر)")
    parser.add_argument("--brief", type=Path, default=ROOT / "docs" / "REVIEWER-BRIEF.md")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434",
                        help=f"خادمُ Ollama المحلي، أو {CLOUD_ENDPOINT} بمفتاح {CLOUD_KEY_ENV} من البيئة")
    args = parser.parse_args(argv)
    reviewers = args.reviewers or list(DEFAULT_REVIEWERS)
    if args.smoke:
        import tempfile
        try:
            with tempfile.TemporaryDirectory() as tmp:
                report = smoke(Path(tmp), reviewers, build_transport(args.base_url),
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
        counts = review_bank(args.bank, reviewers, build_transport(args.base_url),
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
