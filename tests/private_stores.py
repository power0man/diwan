"""مخازنُ المالك الخاصة (ك٢٧): علاماتُ تخطٍّ مسمّاة لاختباراتٍ لا تعمل إلا فوقها.

اللقطةُ العامة (`tools/export_public.py`) بلا متونٍ ولا مسردٍ ولا بنك م١٤، فالاختبارُ
الذي يقرأ هذه المخازن يتخطّى باسم ما ينقصه بدل أن يسقط بخطأِ ملفٍّ مفقود. وفي
المستودع الخاص، حيث المخازنُ حاضرة، تعمل هذه الاختباراتُ كاملةً.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def needs(rel: str, what: str):
    return pytest.mark.skipif(
        not (ROOT / rel).exists(),
        reason=f"{what}_missing: {rel} من ملفات المالك الخاصة خارج اللقطة العامة (ك٢٧)")


needs_corpus = needs("corpus/maritime/_catalog.jsonl", "corpus")
needs_lexicons = needs("corpus/lexicons/_catalog.jsonl", "lexicons")
needs_m14_suite = needs("evaluation/suites/benchmark_m14.json", "suite")
