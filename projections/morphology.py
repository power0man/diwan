#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""المسقط الصرفي العربي: تخزين SQLite خارج النواة الحسابية.

    from projections.morphology import analyze, build_projection, search_by_root

يحفظ واجهة projections السابقة؛ النواة توفر التحليل الحسابي فقط.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.linguistics.morphology import (
    BARE_TEMPLATES,
    DEFINITE_PREFIXES,
    MIN_STEM,
    NOT_ARABIC,
    PREFIXES,
    SINGLE_PREFIXES,
    SUFFIXES,
    TEMPLATES,
    TOO_SHORT,
    UNDETERMINED,
    Analysis,
    MorphologicalAnalysis,
    _ALEF,
    _ARABIC_ONLY,
    _DIACRITICS,
    _WEAK_LETTERS,
    _candidates,
    _strip_suffixes,
    analyze,
    extract_pattern,
    extract_root,
    normalize,
    segment,
    singularize_broken_plural,
)

__all__ = [
    "BARE_TEMPLATES",
    "DEFINITE_PREFIXES",
    "MIN_STEM",
    "NOT_ARABIC",
    "PREFIXES",
    "SCHEMA",
    "SINGLE_PREFIXES",
    "SUFFIXES",
    "TEMPLATES",
    "TOO_SHORT",
    "UNDETERMINED",
    "Analysis",
    "MorphologicalAnalysis",
    "analyze",
    "build_projection",
    "expand_query",
    "extract_pattern",
    "extract_root",
    "normalize",
    "search_by_root",
    "segment",
    "singularize_broken_plural",
]

SCHEMA = (
    "CREATE VIRTUAL TABLE morphology USING fts5("
    "word, root, pattern, stem, tokenize='unicode61 remove_diacritics 0')"
)


def build_projection(words, db_path: Path | str) -> int:
    """بناءُ مسقطٍ جديدٍ من المفردات؛ يعيد عددَ الكلمات المفهرسة بجذرٍ محدَّد."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.execute(SCHEMA)
        rows = []
        seen = set()
        for word in words:
            result = analyze(word)
            if result.root is None:
                continue
            key = (result.normalized, result.root)
            if key in seen:
                continue
            seen.add(key)
            rows.append((result.normalized, result.root, result.pattern, result.stem))
        connection.executemany(
            "INSERT INTO morphology(word, root, pattern, stem) VALUES (?, ?, ?, ?)", rows
        )
        connection.commit()
        return len(rows)
    finally:
        connection.close()


def search_by_root(db_path: Path | str, root: str, limit: int = 50) -> list[dict]:
    """كلُّ مشتقٍّ مفهرسٍ تحت جذرٍ بعينه — قراءةً فقط."""
    path = Path(db_path)
    if not path.exists():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cursor = connection.execute(
            "SELECT word, root, pattern, stem FROM morphology WHERE root MATCH ? LIMIT ?",
            (f'"{normalize(root)}"', limit),
        )
        return [{"word": w, "root": r, "pattern": p, "stem": s} for w, r, p, s in cursor.fetchall()]
    finally:
        connection.close()


def expand_query(query: str, db_path: Path | str, limit: int = 50) -> list[str]:
    """توسيعُ استعلامٍ بمشتقّات جذوره — ما لا جذرَ له يبقى كما ورد."""
    expanded: list[str] = []
    for token in query.split():
        result = analyze(token)
        expanded.append(result.normalized)
        if result.root is None:
            continue
        for row in search_by_root(db_path, result.root, limit):
            if row["word"] not in expanded:
                expanded.append(row["word"])
    return expanded
