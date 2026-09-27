"""الملفُّ غيرُ المقروء بـUTF-8 يُعدّ ويُسمّى في تقرير الجمع وفي الفهرس، فلا تُقارَن فهارسُ على مجموعاتٍ مختلفة صامتًا
(مسحُ الإخفاقات الصامتة، مسار google بتسليم)."""
from __future__ import annotations

from core.vector_retrieval import HashEmbedder, VectorIndex, file_passages, file_passages_report


def test_an_undecodable_file_is_counted_and_named_not_skipped_silently(tmp_path):
    (tmp_path / "ok.txt").write_text("نصٌّ عربيٌّ مقروء", encoding="utf-8")
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\x00\xe9latin")
    passages, report = file_passages_report(tmp_path)
    assert [p.source for p in passages] == ["ok.txt"] and file_passages(tmp_path) == passages
    assert report == {"files_indexed": 1, "skipped_undecodable": 1, "skipped_undecodable_paths": ["bad.txt"]}
    index_path = tmp_path / "idx" / "vectors.sqlite"
    index_path.parent.mkdir()
    summary = VectorIndex.build(index_path, passages, HashEmbedder(), notes=report)
    assert summary["notes"] == report and VectorIndex(index_path).notes() == report
