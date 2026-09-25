# -*- coding: utf-8 -*-
"""النواة اللسانية العربية الأصيلة لمشروع ديوان (core/linguistics).

توفر هذه الحزمة:
1. التحليل الصرفي واستخراج الجذور والأوزان ورد جموع التكسير لمفرداتها (morphology.py).
2. الضبط التشكيل الصواتي وفك اللبس بين المتشابهات رسماً بنقاط الأساس (tashkeel.py).
3. التدقيق النحوي والتركيبي لمخرجات اللغة العربية (syntax_guard.py).
"""
from __future__ import annotations

from core.linguistics.morphology import (
    Analysis,
    MorphologicalAnalysis,
    analyze,
    extract_pattern,
    extract_root,
    segment,
    singularize_broken_plural,
)
from core.linguistics.roots import (
    RootVerdict,
    camel_available,
    root_verdict,
)
from core.linguistics.tashkeel import (
    DisambiguationResult,
    disambiguate_homographs,
    has_tashkeel,
    strip_tashkeel,
    tashkeel_similarity_bp,
)
from core.linguistics.syntax_guard import (
    SyntaxIssue,
    SyntaxVerdict,
    check_syntax,
)

__all__ = [
    # الصرف والأوزان
    "MorphologicalAnalysis",
    "Analysis",
    "analyze",
    "extract_root",
    "extract_pattern",
    "segment",
    # الجذرُ بمصدرٍ مسمًّى (غ١)
    "RootVerdict",
    "root_verdict",
    "camel_available",
    "singularize_broken_plural",
    # التشكيل وفك اللبس
    "strip_tashkeel",
    "has_tashkeel",
    "tashkeel_similarity_bp",
    "disambiguate_homographs",
    "DisambiguationResult",
    # النحو والتركيب
    "check_syntax",
    "SyntaxVerdict",
    "SyntaxIssue",
]
