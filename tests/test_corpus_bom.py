from pathlib import Path
from core.corpus import read_corpus_file_safe

def test_read_corpus_file_safe_with_bom(tmp_path):
    f = tmp_path / "bom_sample.txt"
    f.write_bytes(b"\xef\xbb\xbf\xd9\x85\xd8\xaa\xd9\x86 \xd8\xb9\xd8\xb1\xd8\xa8\xd9\x8a\r\n\xd8\xb3\xd8\xb7\xd8\xb1 \xd8\xab\xd8\xa7\xd9\x86")
    text = read_corpus_file_safe(f)
    assert text == "متن عربي\nسطر ثان"
    assert not text.startswith("\ufeff")

def test_read_corpus_file_safe_crlf_normalization(tmp_path):
    f = tmp_path / "crlf_sample.txt"
    f.write_bytes("سطر 1\r\nسطر 2\r\n".encode("utf-8"))
    text = read_corpus_file_safe(f)
    assert text == "سطر 1\nسطر 2\n"
    assert "\r" not in text
