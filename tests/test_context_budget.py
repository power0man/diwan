"""تدقيقُ ميزانية السياق (ECC ٥): عدّاداتٌ محقونة تكفي لإثبات كل قاعدةٍ فيه بلا حزمة tokenizers وبلا شبكة."""

from __future__ import annotations

import hashlib
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
    assert set(report["reading_set"]) == set(ci.READING_SET)
    for path, entry in report["reading_set"].items():
        assert entry["bytes"] == (ROOT / path).stat().st_size
        text = (ROOT / path).read_text(encoding="utf-8")
        assert entry["tokens"] == {"ws": _ws(text), "thirds": _thirds(text)} and entry["tokens_estimate"] == _thirds(text)
        assert entry["estimate_ratio"]["thirds"] == 1.0 and entry["chars_per_token"]["thirds"] == round(len(text) / _thirds(text), 2)
        assert entry["sha256_12"] == hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], "البصمةُ تربط التقريرَ بالنصّ المقيس"


def test_no_counter_is_refused_not_estimated(capsys):
    """لا رقمَ بلا مرمِّز: بلا عدّادٍ لا يُنشر تقديرُ الفهرس بصفة قياس."""
    with pytest.raises(cb.Refused) as caught:
        cb.audit(ROOT, {})
    assert caught.value.code == "no_tokenizer_named"
    assert cb.main([]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "no_tokenizer_named"


def test_a_missing_reading_set_file_is_refused_not_skipped(monkeypatch):
    """ملاحظةُ Codex على #157: لقطةٌ ناقصة كانت تُقاس بصمتٍ فيُنشر مجموعٌ جزئيّ بصفة `reading_set_totals`."""
    monkeypatch.setattr(ci, "READING_SET", (*ci.READING_SET, "docs/NOT-IN-THIS-TREE.md"))
    with pytest.raises(cb.Refused) as caught:
        cb.audit(ROOT, {"ws": _ws})
    assert caught.value.code == "reading_set_incomplete" and "docs/NOT-IN-THIS-TREE.md" in caught.value.detail


def test_a_root_other_than_the_tool_s_own_checkout_is_refused(tmp_path):
    """ملاحظةُ Codex على #157: نصوصُ التشغيل تُستورد من شجرة الأداة، فجذرٌ آخر كان ينسب أرقامَ تشغيلٍ حديثة إلى إيداعٍ غيرِ
    إيداعها؛ الأداةُ تقيس النسخةَ التي تعمل منها وحدها، ولا خيارَ `--root` في سطر الأوامر."""
    for path in ci.READING_SET:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text((ROOT / path).read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(cb.Refused) as caught:
        cb.audit(tmp_path, {"ws": _ws})
    assert caught.value.code == "root_is_not_this_checkout"
    with pytest.raises(SystemExit):
        cb.main(["--tokenizer", "x=y", "--root", str(tmp_path)])


def test_totals_and_the_share_of_the_window_are_sums_and_ratios():
    report = cb.audit(ROOT, {"ws": _ws}, window=1000)
    reading = report["reading_set"]
    assert report["reading_set_totals"] == {"bytes": sum(e["bytes"] for e in reading.values()),
                                            "tokens_estimate": sum(e["tokens_estimate"] for e in reading.values()),
                                            "tokens": {"ws": sum(e["tokens"]["ws"] for e in reading.values())}}
    prefix = report["runtime_prefix"]
    assert prefix["headline"] == cb.HEADLINE and report["context_window_tokens"] == 1000
    for configuration in prefix["configurations"].values():
        total = configuration["system"]["tokens"]["ws"] + configuration["tools"]["tokens"]["ws"] + prefix["envelope"]["tokens"]["ws"]
        assert configuration["total_tokens"] == {"ws": total}
        assert configuration["share_of_context_window"] == {"ws": round(total / 1000, 4)}


def test_the_registries_builder_closes_both_apps_it_opens(monkeypatch):
    """ملاحظةُ Codex على #157: `_registries` تفتح تطبيقَين ولم تكن تغلقهما، فكلُّ `audit()` في عمليةٍ طويلة يسرّب أربعةَ واصفات."""
    import webui.server as server
    closed = []

    class Closing(server.LocalApp):
        def close(self):
            closed.append(self.root)
            super().close()
    monkeypatch.setattr(server, "LocalApp", Closing)
    found = cb._registries()
    assert set(found) == {c[0] for c in cb.CONFIGURATIONS} and len(closed) == 2 and len(set(closed)) == 2


def test_the_runtime_prefix_is_built_by_the_web_app_s_own_registries_per_configuration():
    """ملاحظةُ Codex على #157: مخطّطاتُ الأدوات ليست قائمةً ثابتة؛ التطبيقُ يحذف أداتَي التنفيذ بلا Docker ويضيف البحثَ والتحليل
    حين يُضبطان ويبني سجلّاتٍ أخرى للأنماط. فتُقاس كلُّ تهيئةٍ كما يبنيها `LocalApp.mode_registry` نفسُه."""
    from agent.builtin_tools import DEFAULT_TOOLS
    from agent.loop import SYSTEM as AGENT_SYSTEM
    from conversation.session import SYSTEM as TEXT_SYSTEM
    from providers.ollama import CONTEXT_TOKENS

    report = cb.audit(ROOT, {"chars": _chars})
    prefix = report["runtime_prefix"]
    assert list(prefix["configurations"]) == [c[0] for c in cb.CONFIGURATIONS] and report["context_window_tokens"] == CONTEXT_TOKENS
    names = {name: set(c["tool_names"]) for name, c in prefix["configurations"].items()}
    defaults = {t.spec.name for t in DEFAULT_TOOLS}
    assert names["agent_minimal"] == (defaults - {"run_command", "run_tests"}) | {"propose_memory"}
    assert names["agent_execution"] == defaults | {"propose_memory"}
    assert names["agent_search"] == names["agent_minimal"] | {"web_search"}
    assert names["agent_analysis"] == names["agent_minimal"] | {"analyze_data"}
    assert names["agent_full"] == defaults | {"propose_memory", "web_search", "analyze_data"}
    assert "run_command" not in names["coder"] and "run_command" in names["coder_execution"] and "propose_memory" not in names["coder"]
    assert names["research"] == {"web_search"} and names["translate"] == {"check_translation"}
    for name, configuration in prefix["configurations"].items():
        assert list(configuration["each_tool"]) == configuration["tool_names"]
        assert sum(e["tokens"]["chars"] for e in configuration["each_tool"].values()) < configuration["tools"]["tokens"]["chars"]
    assert prefix["configurations"]["agent_minimal"]["system"]["chars"] == len(AGENT_SYSTEM)
    assert prefix["text"]["system"]["chars"] == len(TEXT_SYSTEM)


def test_findings_are_derived_from_the_numbers_with_their_thresholds():
    exact = cb.audit(ROOT, {"thirds": _thirds}, window=10**9)
    codes = [f["code"] for f in exact["findings"]]
    assert "index_estimate_drifts_from_measured" not in codes and "prefix_share_of_window_high" not in codes
    # نافذةٌ تجعل نصيبَ التهيئة الرئيسة بين العتبة (١٠٪) والكلّ (١٠٠٪) — ربعَها — فطفرةُ رفع العتبة إلى ١٫٠ تُسقط النتيجةَ ولا تبقيها
    window = 4 * cb.audit(ROOT, {"ws": _ws}, window=10**9)["runtime_prefix"]["configurations"][cb.HEADLINE]["total_tokens"]["ws"]
    drift = cb.audit(ROOT, {"ws": _ws}, window=window)
    by_code = {f["code"]: f for f in drift["findings"] if f.get("configuration") in (None, cb.HEADLINE)}
    assert by_code["index_estimate_drifts_from_measured"]["tokenizer"] == "ws"
    assert by_code["index_estimate_drifts_from_measured"]["ratio"] == round(
        drift["reading_set_totals"]["tokens_estimate"] / drift["reading_set_totals"]["tokens"]["ws"], 3)
    assert by_code["prefix_share_of_window_high"]["share"] == 0.25
    heavy = {f["configuration"] for f in drift["findings"] if f["code"] == "prefix_share_of_window_high"}
    configurations = drift["runtime_prefix"]["configurations"]
    assert heavy == {name for name, c in configurations.items() if c["share_of_context_window"]["ws"] > cb.PREFIX_SHARE_WARNING}
    assert configurations["agent_full"]["share_of_context_window"]["ws"] > configurations[cb.HEADLINE]["share_of_context_window"]["ws"]
    tools = drift["runtime_prefix"]["configurations"][cb.HEADLINE]["each_tool"]
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

    def encode(self, text, add_special_tokens=True):
        assert add_special_tokens is False, "الرموزُ الخاصة تُستثنى فالمقيس حدٌّ أدنى معلَن"
        return _FakeEncoding(text.split())


SHA = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"       # بصمةُ إيداعٍ كاملة: المراجعةُ الوحيدة المقبولة لمرمِّز الـHub


def _fake_tokenizers(monkeypatch):
    monkeypatch.setitem(sys.modules, "tokenizers", types.SimpleNamespace(Tokenizer=_FakeTokenizer))


def _fake_hub(monkeypatch, tmp_path):
    """بديلُ `huggingface_hub.hf_hub_download`: ملفٌّ محلّيّ لكل مستودعٍ إلا `gone/`، ويسجّل المراجعةَ المطلوبة."""
    path = tmp_path / "hub-tokenizer.json"
    path.write_text('{"hub": true}', encoding="utf-8")
    asked = []

    def download(repo, filename, revision=None):
        asked.append((repo, filename, revision))
        if repo.startswith("gone/"):
            raise RuntimeError("404")
        return str(path)
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(hf_hub_download=download))
    monkeypatch.setattr(cb, "tree_state", lambda root: {"dirty": False, "changed_paths": []})
    return path, asked


def test_tokenizer_loading_refuses_by_name(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "tokenizers", None)
    with pytest.raises(cb.Refused) as caught:
        cb.load_tokenizers([f"q=Qwen/x@{SHA}"], [])
    assert caught.value.code == "tokenizers_unavailable"
    _fake_tokenizers(monkeypatch)
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    with pytest.raises(cb.Refused) as caught:
        cb.load_tokenizers([f"q=org/model@{SHA}"], [])
    assert caught.value.code == "huggingface_hub_unavailable"
    hub_file, asked = _fake_hub(monkeypatch, tmp_path)
    # ملاحظةُ Codex على #157: مرمِّزُ الـHub بلا مراجعةٍ ثابتة يتحرّك مع الفرع الافتراضيّ فيعطي الأمرُ نفسُه أرقامًا أخرى بلا أثر
    for named, files, code in (([f"q=gone/x@{SHA}"], [], "tokenizer_unavailable"), ([], ["f=/nowhere/missing.json"], "tokenizer_unavailable"),
                               (["noequals"], [], "tokenizer_spec_invalid"), (["=x"], [], "tokenizer_spec_invalid"),
                               ([f"q=a@{SHA}", f"q=b@{SHA}"], [], "tokenizer_spec_invalid"),
                               (["q=org/model"], [], "tokenizer_revision_unpinned"), (["q=org/model@"], [], "tokenizer_revision_unpinned"),
                               ([f"q=@{SHA}"], [], "tokenizer_revision_unpinned"),
                               # وفرعٌ أو وسمٌ أو بصمةٌ مبتورة تتحرّك أو تُعاد، فليست مراجعةً ثابتة (ملاحظة Codex الثانية على #157)
                               (["q=org/model@main"], [], "tokenizer_revision_unpinned"), (["q=org/model@v1.0"], [], "tokenizer_revision_unpinned"),
                               (["q=org/model@abc123"], [], "tokenizer_revision_unpinned"), ([f"q=org/model@{SHA[:39]}"], [], "tokenizer_revision_unpinned"),
                               ([f"q=org/model@{SHA.upper()}"], [], "tokenizer_revision_unpinned")):
        with pytest.raises(cb.Refused) as caught:
            cb.load_tokenizers(named, files)
        assert caught.value.code == code, (named, files)
    path = tmp_path / "tok.json"
    path.write_text("{}", encoding="utf-8")
    counters, sources = cb.load_tokenizers([f"hub=org/model@{SHA}"], [f"file={path}"])
    assert counters["hub"]("a b c") == 3 and counters["file"]("واحد اثنان") == 2
    assert asked[-1] == ("org/model", "tokenizer.json", SHA)
    assert sources["hub"] == {"source": "org/model", "revision": SHA, "loaded_from": "hub",
                              "file_sha256_12": hashlib.sha256(hub_file.read_bytes()).hexdigest()[:12]}
    assert sources["file"] == {"source": str(path), "loaded_from": "file",
                               "file_sha256_12": hashlib.sha256(path.read_bytes()).hexdigest()[:12]}


def test_a_dirty_tree_is_refused_by_the_cli_and_recorded_by_the_audit(monkeypatch, tmp_path, capsys):
    """ملاحظةُ Codex على #157: نصوصُ التشغيل تُستورد من الشجرة، فتعديلٌ غيرُ مودَع كان يُقاس ويُنسب إلى HEAD بلا علامة."""
    live = cb.tree_state(ROOT)
    assert isinstance(live["dirty"], bool) and isinstance(live["changed_paths"], list)
    _fake_tokenizers(monkeypatch)
    _fake_hub(monkeypatch, tmp_path)
    for state, code in (({"dirty": True, "changed_paths": ["agent/loop.py"]}, "worktree_dirty"),
                        ({"dirty": None, "changed_paths": [], "error": "OSError"}, "worktree_state_unknown")):
        monkeypatch.setattr(cb, "tree_state", lambda root, _s=state: _s)
        assert cb.main(["--tokenizer", f"fake=org/model@{SHA}"]) == 2
        assert json.loads(capsys.readouterr().out)["code"] == code
        assert cb.audit(ROOT, {"ws": _ws})["tree_state"] == state, "المكتبةُ تسجّل الحالةَ ولا ترفض؛ سطرُ الأوامر هو الذي ينشر"


def test_the_cli_writes_the_report_and_summarises_it(monkeypatch, tmp_path, capsys):
    _fake_tokenizers(monkeypatch)
    hub_file, _ = _fake_hub(monkeypatch, tmp_path)
    out = tmp_path / "budget.json"
    assert cb.main(["--tokenizer", f"fake=org/model@{SHA}", "--report", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert summary["status"] == "measured" and summary["tokenizers"] == ["fake"] and summary["report"] == str(out)
    assert summary["reading_set_tokens"] == report["reading_set_totals"]["tokens"]
    assert summary["prefix_tokens"] == {name: c["total_tokens"] for name, c in report["runtime_prefix"]["configurations"].items()}
    assert summary["findings"] == [f["code"] for f in report["findings"]]
    assert report["schema_version"] == cb.SCHEMA_VERSION and report["tool"] == cb.TOOL
    assert report["measurement_limits"] == cb.LIMITS and len(cb.LIMITS) >= 7
    assert report["tokenizers"] == {"fake": {"source": "org/model", "revision": SHA, "loaded_from": "hub",
                                             "file_sha256_12": hashlib.sha256(hub_file.read_bytes()).hexdigest()[:12]}}
    assert report["tree_state"] == {"dirty": False, "changed_paths": []}
    assert cb.main(["--tokenizer", f"fake=gone/model@{SHA}"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "tokenizer_unavailable"
