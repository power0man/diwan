"""ق٧٠: دليلا النماذج المجانية مراجِعين وإخفاقِ مهامّ Codex تُحسب أرقامُهما من صفوفهما، فلا يُنشر رقمٌ لا يُعاد (#193)."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "docs/probe/free-llm-pr-review-20260929.json"
CODEX = ROOT / "docs/probe/codex-task-failures-20260929.json"
VERDICTS = ("confirmed", "plausible", "rejected", "unverified_low")


def test_free_llm_review_totals_are_computed_from_its_findings():
    data = json.loads(REVIEW.read_text(encoding="utf-8"))
    rows = data["findings"]
    assert [row["n"] for row in rows] == list(range(1, len(rows) + 1))
    assert all(row["verdict"] in VERDICTS and row["reason"] and row["claim"] for row in rows)
    assert all(row.get("advisory_item") for row in rows if row["verdict"] in ("confirmed", "plausible"))
    for pr, entry in data["per_pull_request"].items():
        mine = [row for row in rows if str(row["pr"]) == pr]
        assert entry["findings_by_family"] == {family: sum(row["family"] == family for row in mine)
                                               for family in entry["findings_by_family"]}
        assert sum(entry["findings_by_family"].values()) == len(mine)
        assert {v: entry[v] for v in VERDICTS} == {v: sum(row["verdict"] == v for row in mine) for v in VERDICTS}
    counts = Counter(row["verdict"] for row in rows)
    assert data["summary"]["findings"] == len(rows)
    assert {v: data["summary"][v] for v in VERDICTS} == {v: counts[v] for v in VERDICTS}
    assert set(data["advisory_comments"]) == set(data["per_pull_request"]) == {str(pr) for pr in data["pull_requests"]}


def test_codex_task_failures_are_counted_from_their_rows():
    data = json.loads(CODEX.read_text(encoding="utf-8"))
    rows, summary = data["attempts"], data["summary"]
    assert summary["attempts"] == len(rows) == summary["assignments"] + summary["retries"]
    assert summary["retries"] == sum(bool(row.get("retry")) for row in rows)
    assert summary["failed"] == len(rows)
    assert summary["window_utc"] == [min(row["replied_utc"] for row in rows), max(row["replied_utc"] for row in rows)]
