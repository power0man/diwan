#!/usr/bin/env python3
"""قبول مصطنع لبنك الجودة المقيس م١٤؛ لا استهلاك للرصيد الحي أثناء الفحص التلقائي."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.contracts import Response, Usage
from evaluation.benchmark_runner import BenchmarkRunner, BenchmarkIncompleteError


class SyntheticBenchmarkProvider:
    model = "synthetic:qwen-eval"
    name = "synthetic"
    is_local = True

    def __init__(self):
        self.calls = 0

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        prompt = request.messages[-1].content
        # إرجاع جواب اصطناعي يحتوي على شاهد مطابق
        return Response(
            content=f"إجابة مصطنعة محكومة [ش1]: تتضمن الحقائق المطلوبة لاختبار القبول عن {prompt[:20]}.",
            usage=Usage(10, 20),
            cost_micros=0,
            model_version="synthetic-v1",
            provider="synthetic",
            stop_reason="complete"
        )


def run_synthetic_m14(tmp_dir: Path) -> dict:
    suite_file = ROOT / "evaluation" / "suites" / "benchmark_m14.json"
    if not suite_file.exists():
        raise FileNotFoundError(f"Suite missing: {suite_file}")

    suite_data = json.loads(suite_file.read_text(encoding="utf-8"))
    assert len(suite_data["cases"]) >= 40, f"Expected >= 40 cases, got {len(suite_data['cases'])}"

    provider = SyntheticBenchmarkProvider()
    runner = BenchmarkRunner(ROOT, provider, out_dir=tmp_dir / "benchmarks")

    report = runner.run_benchmark(limit=2)
    assert report["status"] == "pending_automated_review"
    assert report["human_review_verified"] is False
    sample_file = tmp_dir / "review.json"
    sample_file.write_text(json.dumps({"attestation": True, "reviews": {
        c["case_id"]: {"verdict": "ok"} for c in suite_data["cases"][:2]}}))
    try:
        runner.run_benchmark(limit=2, human_review_file=sample_file)
    except BenchmarkIncompleteError:
        pass
    else:
        raise AssertionError("legacy review must not certify")
    diagnostic = runner.run_benchmark(limit=1, require_human_review=False)
    assert diagnostic["status"] == "diagnostic_only"
    return {"suite_cases": len(suite_data["cases"]), "synthetic_calls": provider.calls,
            "product_readiness": "not_assessed", "human_review": False,
            "checks": {"suite_integrity": True, "producer_cannot_certify": True,
                       "legacy_human_claim_rejected": True, "diagnostic_not_certified": True}}


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        res = run_synthetic_m14(Path(td))
        print("اجتياز قبول م١٤ المصطنع:", json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
