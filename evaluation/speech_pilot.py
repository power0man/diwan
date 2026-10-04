"""Synthetic ASR diagnostics; never a substitute for the frozen speech bank."""
from __future__ import annotations

from evaluation.media_bank import normalize


def _edits(reference, hypothesis) -> int:
    previous = list(range(len(hypothesis) + 1))
    for i, a in enumerate(reference, 1):
        current = [i]
        for j, b in enumerate(hypothesis, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def score(reference: str, hypothesis: str) -> dict:
    ref, hyp = normalize(reference), normalize(hypothesis)
    words, predicted = ref.split(), hyp.split()
    return {"word_edits": _edits(words, predicted), "reference_words": len(words),
            "character_edits": _edits(ref, hyp), "reference_characters": len(ref),
            "silence_hallucination": not ref and bool(hyp)}


def report(cases: list[dict]) -> dict:
    """Corpus ratios, errors separate from recognition mistakes, silence separate."""
    scored = [score(case["reference"], case["text"]) for case in cases if case["status"] in ("ok", "no_speech")]
    words = sum(row["reference_words"] for row in scored)
    chars = sum(row["reference_characters"] for row in scored)
    speech = [row for row in scored if row["reference_words"] > 0]
    return {"attempted": len(cases), "completed": len(scored), "errors": len(cases) - len(scored),
            "wer": sum(row["word_edits"] for row in speech) / words if words else None,
            "cer": sum(row["character_edits"] for row in speech) / chars if chars else None,
            "silence_hallucinations": sum(row["silence_hallucination"] for row in scored),
            "synthetic_only": True, "release_ready": False}
