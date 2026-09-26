"""مُشغِّلُ بنك الذاكرة المحكومة (ك٥٢) على `memory.store.MemoryStore`.

يُنفّذ عملياتِ كل سيناريو على مخازن مشاريع حقيقية في مجلدٍ مؤقّت، ويحكم على التوقّعات:
- `retrieve` و`context`: ما يُسترجع وما يبلغ السياق.
- `residue`: بايتاتُ مجلد الذاكرة على القرص.
- `receipt`: إيصالُ النسيان بعدده.

ويجمع المقاييس الأربعة المسجَّلة سلفًا في `docs/MEMORY-DESIGN.md` §٦. والاقتراحاتُ المعلّقة
والنسخُ الاحتياطية يحملها المشغِّلُ خارج مجلد الذاكرة، كما يحملها النظامُ الحقيقيّ.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from core.attribution import normalize
from memory.store import HEADER, MemoryRefused, MemoryStore


def _contains(haystack: str, needle: str) -> bool:
    return needle in haystack or normalize(needle).strip() in normalize(haystack)


def _residue(store: MemoryStore) -> bytes:
    return b"\n".join(p.read_bytes() for p in sorted(store.root.rglob("*")) if p.is_file())


def run_scenario(scenario: dict, root: Path) -> dict:
    stores: dict[str, MemoryStore] = {}
    refs: dict[str, object] = {}
    failures: list[str] = []
    leaks = consent_violations = unquarantined = 0

    def store(project):
        if project not in stores:
            (root / project).mkdir(parents=True, exist_ok=True)
            stores[project] = MemoryStore(root / project)
        return stores[project]

    for index, step in enumerate(scenario["steps"]):
        s = store(step["project"])
        op, expect = step.get("op"), step.get("expect")
        if op == "remember":
            try:
                refs[step["as"]] = s.remember(step["text"], consent=step["consent"])
            except MemoryRefused as exc:
                if step["consent"] == "owner" or exc.code != "consent_required":
                    failures.append(f"{index}: refused {exc.code}")
                refs[step["as"]] = None
        elif op == "propose":
            refs[step["as"]] = s.propose(step["text"])
        elif op == "approve":
            refs[step["ref"]] = s.approve(refs[step["ref"]])
        elif op == "forget":
            s.forget(refs[step["ref"]])
        elif op == "backup":
            refs[step["as"]] = s.backup()
        elif op == "restore":
            s.restore(refs[step["ref"]])
        elif expect in ("retrieve", "context"):
            text = (" ".join(i["text"] for i in s.retrieve(step["query"])) if expect == "retrieve"
                    else s.context_block(step["question"]))
            for needle in step["absent"]:
                if _contains(text, needle):
                    failures.append(f"{index}: {expect} holds absent «{needle[:30]}»")
                    if scenario["category"] == "isolation":
                        leaks += 1
                    if scenario["category"] == "consent":
                        consent_violations += 1
                    if scenario["category"] == "injection":
                        unquarantined += 1
            for needle in step["present"]:
                if not _contains(text, needle):
                    failures.append(f"{index}: {expect} lacks present «{needle[:30]}»")
            if step.get("quarantined"):
                fenced = text.startswith(HEADER) and "<<<مادة:" in text and "<<</مادة:" in text
                inside = text.split("<<<مادة:", 1)[-1].rsplit("<<</مادة:", 1)[0] if fenced else ""
                if not fenced or any(not _contains(inside, n) for n in step["present"]):
                    failures.append(f"{index}: context not fenced")
                    unquarantined += 1
        elif expect == "residue":
            raw = _residue(s)
            for needle in step["absent"]:
                if needle.encode("utf-8") in raw:
                    failures.append(f"{index}: residue holds «{needle[:30]}»")
                    if scenario["category"] == "consent":
                        consent_violations += 1
        elif expect == "receipt":
            ref = refs[step["ref"]]
            item_id = ref if isinstance(ref, str) else None
            count = len(s.receipts(item_id)) if item_id else 0
            if count != step["count"]:
                failures.append(f"{index}: receipts {count} != {step['count']}")
    return {"id": scenario["id"], "category": scenario["category"], "passed": not failures,
            "failures": failures, "leaks": leaks, "consent_violations": consent_violations,
            "injection_unquarantined": unquarantined}


def run_memory_bank(bank: dict) -> dict:
    results = []
    for scenario in bank["scenarios"]:
        with tempfile.TemporaryDirectory(prefix="diwan-memory-") as tmp:
            results.append(run_scenario(scenario, Path(tmp).resolve()))
    forgetting = [r for r in results if r["category"] in ("forget", "backup")]
    metrics = {
        "forget_rate": round(sum(r["passed"] for r in forgetting) / len(forgetting), 4) if forgetting else None,
        "leakage": sum(r["leaks"] for r in results),
        "consent_violations": sum(r["consent_violations"] for r in results),
        "injection_unquarantined": sum(r["injection_unquarantined"] for r in results),
    }
    thresholds = bank["thresholds"]
    meets = (metrics["forget_rate"] == thresholds["forget_rate"]
             and all(metrics[k] <= thresholds[k] for k in ("leakage", "consent_violations", "injection_unquarantined"))
             and all(r["passed"] for r in results))
    return {"schema_version": 1, "suite_id": bank["suite_id"], "metrics": metrics, "meets_thresholds": meets,
            "passed": sum(r["passed"] for r in results), "total": len(results), "results": results}
