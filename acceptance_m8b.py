#!/usr/bin/env python3
"""قبول آلية م٨-ب ببيانات مصطنعة؛ لا يمثل حكمًا بشريًا أو بنكًا مستقلاً."""
from pathlib import Path
import json
import tempfile

from evaluation.acceptance_store import AcceptanceStore
from evaluation.disclosure import Case, DisclosureRefused


def main():
    cases = tuple(Case(f"fixture-{i}", f"synthetic task {i}",
                       f"synthetic reference {i}", "synthetic criterion") for i in range(6))
    checks = {}
    with tempfile.TemporaryDirectory(prefix="diwan-m8b-") as temp:
        def open_store():
            return AcceptanceStore(Path(temp).resolve() / "store", cases, suite_id="synthetic-only",
                                   candidate_sha256="a" * 64, max_attempts=1)
        store = open_store()
        checks["attempt_persisted"] = store.begin("fixture-run") == 1 and open_store().status()["attempts_used"] == 1
        checks["resume_not_new_attempt"] = open_store().begin("fixture-run") == 1
        tasks = store.visible_tasks("fixture-run")
        checks["references_not_in_tasks"] = all(set(t) == {"case_id", "prompt"} for t in tasks)
        store.record("fixture-run", "fixture-0", True)
        store.reveal("fixture-0", actor="synthetic-test", reason="testing disclosure only")
        checks["disclosure_survives_restart"] = open_store().status()["revealed_cases"] == 1
        for case in cases[1:]:
            store.record("fixture-run", case.case_id, True)
        aggregate = store.aggregate("fixture-run")
        checks["aggregate_closed_and_replayed"] = aggregate == open_store().aggregate("fixture-run") and aggregate["n"] == 5
        try:
            open_store().begin("new-fixture-run")
            checks["attempt_cap_survives_restart"] = False
        except DisclosureRefused as exc:
            checks["attempt_cap_survives_restart"] = exc.code == "attempts_exhausted"
    print(json.dumps({"checks": checks, "passed": sum(checks.values()),
                      "total": len(checks), "scope": "synthetic_mechanism_only",
                      "independent_bank": "not_provided", "human_review": "not_required_q49",
                      "automated_multi_system_review": "pending",
                      "m8b_complete": False, "release_ready": False}, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
