"""تدقيقُ ميزانية السياق (ECC ٥): عدّاداتٌ محقونة تكفي لإثبات كل قاعدةٍ فيه بلا حزمة tokenizers وبلا شبكة."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import context_budget as cb  # noqa: E402
import context_index as ci  # noqa: E402


def _ws(text: str) -> int:
    return len(text.split())


def _thirds(text: str) -> int:
    return ci.tokens_estimate(text)


def _chars(text: str) -> int:
    return len(text)


def test_the_reading_set_is_the_index_s_reading_set_measured_by_every_named_counter():
    report = cb.audit(ROOT, {"ws": _ws, "thirds": _thirds})
    assert set(report["reading_set"]) == {p for p in ci.READING_SET if (ROOT / p).is_file()}
    for path, entry in report["reading_set"].items():
        assert entry["bytes"] == (ROOT / path).stat().st_size
        text = (ROOT / path).read_text(encoding="utf-8")
        assert entry["tokens"] == {"ws": _ws(text), "thirds": _thirds(text)} and entry["tokens_estimate"] == _thirds(text)
        assert entry["estimate_ratio"]["thirds"] == 1.0 and entry["chars_per_token"]["thirds"] == round(len(text) / _thirds(text), 2)


def test_no_counter_is_refused_not_estimated(capsys):
    """لا رقمَ بلا مرمِّز: بلا عدّادٍ لا يُنشر تقديرُ الفهرس بصفة قياس."""
    with pytest.raises(cb.Refused) as caught:
        cb.audit(ROOT, {})
    assert caught.value.code == "no_tokenizer_named"
    assert cb.main([]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "no_tokenizer_named"


def test_totals_and_the_share_of_the_window_are_sums_and_ratios():
    report = cb.audit(ROOT, {"ws": _ws}, window=1000)
    reading = report["reading_set"]
    assert report["reading_set_totals"] == {"bytes": sum(e["bytes"] for e in reading.values()),
                                            "tokens_estimate": sum(e["tokens_estimate"] for e in reading.values()),
                                            "tokens": {"ws": sum(e["tokens"]["ws"] for e in reading.values())}}
    agent = report["runtime_prefix"]["agent"]
    total = agent["system"]["tokens"]["ws"] + agent["tools"]["tokens"]["ws"] + agent["envelope"]["tokens"]["ws"]
    assert agent["total_tokens"] == {"ws": total} and report["context_window_tokens"] == 1000
    assert agent["share_of_context_window"] == {"ws": round(total / 1000, 4)}


def test_the_runtime_prefix_comes_from_the_product_modules_not_from_copies():
    from agent.builtin_tools import DEFAULT_TOOLS
    from agent.loop import SYSTEM as AGENT_SYSTEM
    from conversation.session import SYSTEM as TEXT_SYSTEM
    from providers.ollama import CONTEXT_TOKENS

    report = cb.audit(ROOT, {"chars": _chars})
    prefix = report["runtime_prefix"]
    assert prefix["agent"]["system"]["chars"] == len(AGENT_SYSTEM) and prefix["text"]["system"]["chars"] == len(TEXT_SYSTEM)
    expected = [t.spec.name for t in DEFAULT_TOOLS] + ["propose_memory"]
    assert list(prefix["agent_tools"]) == expected
    assert sum(e["tokens"]["chars"] for e in prefix["agent_tools"].values()) < prefix["agent"]["tools"]["tokens"]["chars"]
    assert set(prefix["modes"]) == {"coder", "research", "translate"} and report["context_window_tokens"] == CONTEXT_TOKENS


def test_findings_are_derived_from_the_numbers_with_their_thresholds():
    exact = cb.audit(ROOT, {"thirds": _thirds}, window=10**9)
    codes = [f["code"] for f in exact["findings"]]
    assert "index_estimate_drifts_from_measured" not in codes and "agent_prefix_share_of_window_high" not in codes
    # نافذةٌ تجعل نصيبَ السابقة بين العتبة (١٠٪) والكلّ (١٠٠٪) — ربعَها — فطفرةُ رفع العتبة إلى ١٫٠ تُسقط النتيجةَ ولا تبقيها
    window = 4 * cb.audit(ROOT, {"ws": _ws}, window=10**9)["runtime_prefix"]["agent"]["total_tokens"]["ws"]
    drift = cb.audit(ROOT, {"ws": _ws}, window=window)
    by_code = {f["code"]: f for f in drift["findings"]}
    assert by_code["agent_prefix_share_of_window_high"]["share"] == 0.25
    assert by_code["index_estimate_drifts_from_measured"]["tokenizer"] == "ws"
    assert by_code["index_estimate_drifts_from_measured"]["ratio"] == round(
        drift["reading_set_totals"]["tokens_estimate"] / drift["reading_set_totals"]["tokens"]["ws"], 3)
    assert by_code["agent_prefix_share_of_window_high"]["share"] == drift["runtime_prefix"]["agent"]["share_of_context_window"]["ws"]
    tools = drift["runtime_prefix"]["agent_tools"]
    assert by_code["costliest_tool_schema"]["tool"] == max(tools, key=lambda t: tools[t]["tokens"]["ws"])
    assert drift["thresholds"] == {"estimate_drift": cb.ESTIMATE_DRIFT, "prefix_share_warning": cb.PREFIX_SHARE_WARNING}


def test_the_arabic_token_tax_is_measured_on_the_fixed_pair():
    report = cb.audit(ROOT, {"chars": _chars})
    tax = report["arabic_token_tax"]["chars"]
    assert tax["arabic_tokens"] == len(cb.ARABIC_SAMPLE) and tax["english_tokens"] == len(cb.ENGLISH_SAMPLE)
    assert tax["tokens_ratio_arabic_to_english"] == round(len(cb.ARABIC_SAMPLE) / len(cb.ENGLISH_SAMPLE), 2)
    assert tax["arabic_chars_per_token"] == 1.0 and tax["english_chars_per_token"] == 1.0


class _FakeEncoding:
    def __init__(self, ids):
        self.ids = ids


class _FakeTokenizer:
    """بديلُ `tokenizers.Tokenizer`: يعدّ الكلمات، ويعطّل حسب المصدر."""

    def __init__(self, source):
        self.source = source

    @classmethod
    def from_file(cls, source):
        if source.endswith("missing.json"):
            raise OSError("no such file")
        return cls(source)

    @classmethod
    def from_pretrained(cls, source):
        if source.startswith("gone/"):
            raise RuntimeError("404")
        return cls(source)

    def encode(self, text, add_special_tokens=True):
        assert add_special_tokens is False, "الرموزُ الخاصة تُستثنى فالمقيس حدٌّ أدنى معلَن"
        return _FakeEncoding(text.split())


def _fake_tokenizers(monkeypatch):
    monkeypatch.setitem(sys.modules, "tokenizers", types.SimpleNamespace(Tokenizer=_FakeTokenizer))


def test_tokenizer_loading_refuses_by_name(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "tokenizers", None)
    with pytest.raises(cb.Refused) as caught:
        cb.load_tokenizers(["q=Qwen/x"], [])
    assert caught.value.code == "tokenizers_unavailable"
    _fake_tokenizers(monkeypatch)
    for named, files, code in ((["q=gone/x"], [], "tokenizer_unavailable"), ([], ["f=/nowhere/missing.json"], "tokenizer_unavailable"),
                               (["noequals"], [], "tokenizer_spec_invalid"), (["=x"], [], "tokenizer_spec_invalid"),
                               (["q=a", "q=b"], [], "tokenizer_spec_invalid")):
        with pytest.raises(cb.Refused) as caught:
            cb.load_tokenizers(named, files)
        assert caught.value.code == code, (named, files)
    path = tmp_path / "tok.json"
    path.write_text("{}", encoding="utf-8")
    counters, sources = cb.load_tokenizers(["hub=org/model"], [f"file={path}"])
    assert counters["hub"]("a b c") == 3 and counters["file"]("واحد اثنان") == 2
    assert sources["hub"] == {"source": "org/model", "loaded_from": "hub"}
    assert sources["file"]["loaded_from"] == "file" and len(sources["file"]["file_sha256_12"]) == 12


def test_the_cli_writes_the_report_and_summarises_it(monkeypatch, tmp_path, capsys):
    _fake_tokenizers(monkeypatch)
    out = tmp_path / "budget.json"
    assert cb.main(["--tokenizer", "fake=org/model", "--report", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert summary["status"] == "measured" and summary["tokenizers"] == ["fake"] and summary["report"] == str(out)
    assert summary["reading_set_tokens"] == report["reading_set_totals"]["tokens"]
    assert summary["agent_prefix_tokens"] == report["runtime_prefix"]["agent"]["total_tokens"]
    assert summary["findings"] == [f["code"] for f in report["findings"]]
    assert report["schema_version"] == cb.SCHEMA_VERSION and report["tool"] == cb.TOOL
    assert report["measurement_limits"] == cb.LIMITS and len(cb.LIMITS) >= 5
    assert report["tokenizers"] == {"fake": {"source": "org/model", "loaded_from": "hub"}}
    assert cb.main(["--tokenizer", "fake=gone/model"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "tokenizer_unavailable"
