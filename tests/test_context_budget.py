"""تدقيقُ ميزانية السياق (ECC ٥): عدّاداتٌ محقونة تكفي لإثبات كل قاعدةٍ فيه بلا حزمة tokenizers وبلا شبكة."""

from __future__ import annotations

import hashlib
import json
import re
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
    # طريقُ الطفل في العملية نفسِها: البدائلُ المحقونة لا تبلغ مفسّرًا آخر، والآليةُ الحقيقية (اللقطةُ والمفسّرُ المعزول) تُختبر وحدها
    monkeypatch.setattr(cb, "measure_in_snapshot",
                        lambda argv, commit, state: cb.main([*argv, "--in-snapshot", commit, "--tree-state", json.dumps(state)]))
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


def test_an_untracked_python_file_makes_the_tree_dirty_but_an_untracked_report_does_not(tmp_path):
    """ملاحظةُ Codex الثانية على #157: `--untracked-files=no` كان يترك `tools/webui/server.py` غيرَ المتتبَّع يحجب
    `webui.server` والحالةُ «نظيفة»؛ ملفُّ بايثون غيرُ متتبَّع يُعدّ، وتقريرٌ غيرُ متتبَّع لا يُستورد فلا يُعدّ."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *args: subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    run("init", "-q")
    run("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "root")
    assert cb.tree_state(repo) == {"dirty": False, "changed_paths": [], "untracked_importable": []}
    (repo / "report.json").write_text("{}", encoding="utf-8")
    assert cb.tree_state(repo)["dirty"] is False
    (repo / "tools" / "webui").mkdir(parents=True)
    (repo / "tools" / "webui" / "server.py").write_text("LocalApp = None\n", encoding="utf-8")
    state = cb.tree_state(repo)
    assert state["dirty"] is True and state["untracked_importable"] == ["tools/webui/server.py"] and state["changed_paths"] == []
    # ملاحظةُ Codex السابعة على #157: رابطٌ رمزيّ غيرُ متتبَّع إلى حزمةٍ خارجية يُدرجه git باسمه وحده (بلا لاحقة) وكان يمرّ نظيفًا؛
    # وbytecode بلا مصدر يُحمَّل من مجلّدٍ على sys.path (السادسة)
    (repo / "tools" / "webui" / "server.py").unlink()
    (repo / "tools" / "webui").rmdir()
    (tmp_path / "elsewhere").mkdir()
    (repo / "tools" / "webui").symlink_to(tmp_path / "elsewhere")
    (repo / "tools" / "json.pyc").write_bytes(b"\x00")
    assert cb.tree_state(repo)["untracked_importable"] == ["tools/json.pyc", "tools/webui"]
    (repo / "tracked.txt").write_text("a", encoding="utf-8")
    run("add", "tracked.txt")
    assert cb.tree_state(repo)["changed_paths"] == ["tracked.txt"]


def test_a_snapshot_holds_the_commit_s_tracked_files_only(tmp_path):
    """ملاحظتا Codex السادسة والسابعة على #157: القياسُ يجري في لقطة `git archive` من الإيداع، فلا ملفَّ غيرَ متتبَّع ولا متجاهَلًا
    (pyc بلا مصدر) ولا رابطًا رمزيًّا فيها، أيًّا كانت حالُ شجرة العمل."""
    import shutil, subprocess
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    run = lambda *args: subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    run("init", "-q")
    (repo / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    (repo / "tools" / "a.py").write_text("A = 1\n", encoding="utf-8")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "tracked")
    (repo / "tools" / "b.py").write_text("B = 2\n", encoding="utf-8")                 # غيرُ متتبَّع
    (repo / "tools" / "json.pyc").write_bytes(b"\x00")                               # متجاهَل
    (tmp_path / "elsewhere").mkdir()
    (repo / "tools" / "webui").symlink_to(tmp_path / "elsewhere")                     # رابطٌ غيرُ متتبَّع
    snapshot = cb.snapshot_head(repo, "HEAD")
    try:
        assert snapshot != repo and snapshot.is_dir()
        assert sorted(str(p.relative_to(snapshot)) for p in snapshot.rglob("*") if not p.is_dir()) == [".gitignore", "tools/a.py"]
        assert not (snapshot / ".git").exists()
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


def test_the_cli_measures_a_git_archive_snapshot_of_head_with_an_isolated_interpreter(monkeypatch, tmp_path):
    """ملاحظةُ Codex السادسة على #157: `tools/json.pyc` بلا مصدر كان يُحمَّل من أول `sys.path` قبل العزل والحالةُ نظيفة. سطرُ
    الأوامر لا يقيس الشجرةَ: يفكّ لقطةَ HEAD في مجلّدٍ مؤقّت ويشغّل فيها نسخةَ الأداة بالمفسّر نفسِه معزولًا (`-I -P`)، بمساراتٍ
    مطلقة، ويمرّر الإيداعَ وحالةَ الشجرة، ويحذف اللقطةَ بعد القياس، ويعيد رمزَ خروج الطفل."""
    real = cb.measure_in_snapshot
    _fake_tokenizers(monkeypatch)
    _fake_hub(monkeypatch, tmp_path)
    monkeypatch.setattr(cb, "measure_in_snapshot", real)
    seen = {}

    def child(command, cwd):
        snapshot = Path(command[3]).parents[1]
        seen.update(command=command, cwd=Path(cwd), snapshot=snapshot, existed=snapshot.is_dir(),
                    tool=(snapshot / "tools" / "context_budget.py").is_file() and (snapshot / "tools" / "context_index.py").is_file()
                    and (snapshot / "AGENTS.md").is_file(), git=(snapshot / ".git").exists(),
                    stray=sorted(str(p) for p in snapshot.rglob("*") if p.is_symlink() or p.suffix == ".pyc" or p.name == "__pycache__"))
        return 7
    monkeypatch.setattr(cb, "_run_child", child)
    out = tmp_path / "r.json"
    assert cb.main(["--tokenizer", f"fake=org/model@{SHA}", "--tokenizer-file", "f=rel/tok.json", "--report", str(out)]) == 7
    command, snapshot = seen["command"], seen["snapshot"]
    assert command[:3] == [sys.executable, "-I", "-P"] and command[3] == str(snapshot / "tools" / "context_budget.py")
    assert snapshot != cb.ROOT and seen["cwd"] == snapshot and seen["existed"] and seen["tool"] and not seen["git"] and seen["stray"] == []
    assert not snapshot.exists(), "اللقطةُ تُحذف بعد القياس"
    commit = command[command.index("--in-snapshot") + 1]
    assert re.fullmatch(r"[0-9a-f]{40}", commit) and commit == cb._commit(cb.ROOT)
    assert json.loads(command[command.index("--tree-state") + 1]) == {"dirty": False, "changed_paths": []}
    assert command[command.index("--tokenizer-file") + 1] == f"f={Path('rel/tok.json').resolve()}"
    assert command[command.index("--report") + 1] == str(out.resolve()) and "--tokenizer" in command


def test_the_child_records_the_snapshot_commit_and_the_parent_s_tree_state(monkeypatch, tmp_path, capsys):
    """داخل اللقطة لا git: الإيداعُ وحالةُ الشجرة يأتيان من الأب ويُسجَّلان كما هما، وبصمةٌ ليست كاملة تُرفض باسمها."""
    _fake_tokenizers(monkeypatch)
    _fake_hub(monkeypatch, tmp_path)
    out, commit = tmp_path / "child.json", "a" * 40
    state = {"dirty": False, "changed_paths": [], "untracked_importable": []}
    assert cb.main(["--tokenizer", f"fake=org/model@{SHA}", "--report", str(out), "--in-snapshot", commit, "--tree-state", json.dumps(state)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["commit"] == commit and report["tree_state"] == state
    assert report["measured_in"] == {"snapshot_of_commit": commit, "interpreter_isolated": False}
    assert json.loads(capsys.readouterr().out)["status"] == "measured"
    assert cb.main(["--tokenizer", f"fake=org/model@{SHA}", "--in-snapshot", "abc123"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "snapshot_commit_invalid"


def test_the_cli_re_executes_itself_isolated_before_any_hijackable_import():
    """ملاحظةُ Codex الثامنة على #157: الأبُ نفسُه كان يستورد tarfile وsubprocess من أول sys.path (tools/) قبل العزل، فpyc بلا
    مصدر لأحدهما يدسّ ملفاتٍ في اللقطة. الآن أولُ ما يفعله السكربت — قبل أيّ استيرادٍ غيرِ sys وos المضمَّنين — إعادةُ تشغيل
    نفسه بـ`-I -P`؛ وتحت هذين العلمين لا يعيد."""
    import subprocess
    source = (ROOT / "tools" / "context_budget.py").read_text(encoding="utf-8")
    boot = source.index("os.execv(sys.executable, [sys.executable, \"-I\", \"-P\", *sys.argv])")
    assert boot < source.index("\nimport argparse") and "\nimport context_index" not in source
    assert source.index("\nimport sys") < boot and "sys.path.insert" not in source[:boot]
    probe = ("import json, os, sys\n"
             "os.execv = lambda exe, args: (print(json.dumps([exe] + list(args))), sys.exit(97))\n"
             "import runpy\n"
             "sys.argv = ['tools/context_budget.py', '--tokenizer', 'x=y@" + SHA + "']\n"
             "runpy.run_path('tools/context_budget.py', run_name='__main__')\n")
    for flags in ([], ["-P"]):                                   # -I وحده يستلزم -P منذ 3.11 فلا يعيد
        run = subprocess.run([sys.executable, *flags, "-c", probe], cwd=ROOT, capture_output=True, text=True)
        assert run.returncode == 97, (flags, run.stderr[-400:])
        assert json.loads(run.stdout.strip().splitlines()[-1]) == [sys.executable, sys.executable, "-I", "-P", "tools/context_budget.py",
                                                                    "--tokenizer", f"x=y@{SHA}"], flags
    run = subprocess.run([sys.executable, "-I", "-P", "-c", probe], cwd=ROOT, capture_output=True, text=True)
    assert run.returncode != 97 and "no_tokenizer_named" not in run.stdout, "تحت -I -P لا إعادةَ تشغيل؛ يمضي إلى القياس"
    assert "context_index" not in subprocess.run(
        [sys.executable, "-I", "-P", "-c", "import sys; sys.path.insert(0, 'tools'); import context_budget as cb; "
         "print(sorted(m for m in sys.modules if m in ('context_index', 'webui', 'webui.server')), str(cb.ROOT) in sys.path)"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.replace("False", ""), "استيرادُ الأداة لا يستورد الفهرسَ ولا المنتج ولا يضيف الجذر"


def test_snapshot_extraction_checks_members_itself_and_works_without_the_filter_argument(tmp_path, monkeypatch):
    """ملاحظةُ Codex التاسعة على #157: `extractall(filter=)` من 3.11.4، وpyproject يعلن 3.11؛ الأعضاءُ تُفحص هنا (ملفٌّ أو مجلّد
    بمسارٍ نسبيّ) فلا يعتمد الأمانُ على المرشّح، ويُمرَّر المرشّحُ حيث يوجد فقط."""
    import io, tarfile
    def archive(*entries):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for name, kind in entries:
                info = tarfile.TarInfo(name)
                if kind == "file":
                    info.size = 3
                    tar.addfile(info, io.BytesIO(b"abc"))
                elif kind == "dir":
                    info.type = tarfile.DIRTYPE
                    tar.addfile(info)
                else:
                    info.type = tarfile.SYMTYPE
                    info.linkname = kind
                    tar.addfile(info)
        buf.seek(0)
        return buf
    real = tarfile.TarFile.extractall
    def without_filter(self, path=".", members=None, *, numeric_owner=False):            # 3.11.0–3.11.3
        return real(self, path, members=members, numeric_owner=numeric_owner)
    monkeypatch.setattr(tarfile.TarFile, "extractall", without_filter)
    target = tmp_path / "snap"
    target.mkdir()
    with tarfile.open(fileobj=archive(("tools", "dir"), ("tools/a.py", "file"))) as tar:
        cb._extract(tar, target)
    assert (target / "tools" / "a.py").read_bytes() == b"abc"
    for entries in ((("tools/webui", "/etc"),), (("../escape.py", "file"),), (("/abs.py", "file"),)):
        with tarfile.open(fileobj=archive(*entries)) as tar, pytest.raises(cb.Refused) as caught:
            cb._extract(tar, tmp_path / "bad")
        assert caught.value.code == "snapshot_member_unsafe", entries
    monkeypatch.setattr(tarfile.TarFile, "extractall", real)
    with tarfile.open(fileobj=archive(("b.py", "file"))) as tar:
        cb._extract(tar, target)
    assert (target / "b.py").is_file()


def test_tampered_bytecode_in_the_tree_s_pycache_is_not_loaded_once_bytecode_is_isolated(tmp_path, monkeypatch):
    """ملاحظةُ Codex الثالثة على #157: ملفُّ `.pyc` متجاهَلٌ في git ومبنيٌّ بوضع unchecked-hash يُحمَّل بدل المصدر المتتبَّع والحالةُ
    «نظيفة». تحت `sys.pycache_prefix` خاصٍّ لا يُقرأ `__pycache__` الشجرة، فيُحمَّل المصدر."""
    import importlib, importlib.util, py_compile, sys as _sys, uuid
    name = f"budget_probe_{uuid.uuid4().hex[:8]}"
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    source = pkg / f"{name}.py"
    source.write_text('VALUE = "source"\n', encoding="utf-8")
    evil = tmp_path / "evil.py"
    evil.write_text('VALUE = "tampered"\n', encoding="utf-8")
    monkeypatch.setattr(_sys, "pycache_prefix", None)
    monkeypatch.setattr(cb, "_PYCACHE_PREFIX", None)
    py_compile.compile(str(evil), cfile=importlib.util.cache_from_source(str(source)), dfile=str(source),
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    monkeypatch.syspath_prepend(str(pkg))
    importlib.invalidate_caches()
    assert importlib.import_module(name).VALUE == "tampered", "الثغرةُ التي يُغلقها العزل: الـpyc المزوَّر يُحمَّل بدل المصدر"
    monkeypatch.delitem(_sys.modules, name)
    prefix = cb.isolate_bytecode()
    assert _sys.pycache_prefix == prefix and prefix.startswith(("/tmp", "/home", "/private", "/var"))
    importlib.invalidate_caches()
    assert importlib.import_module(name).VALUE == "source"
    monkeypatch.delitem(_sys.modules, name)


def test_the_tool_isolates_bytecode_before_importing_context_index():
    """ملاحظةُ Codex الرابعة على #157: `import context_index` كان يسبق العزل، فpyc مزوَّر له في `tools/__pycache__` يبدّل مجموعةَ
    القراءة والتقدير. في عمليةٍ جديدة يُستورد context_index بعد ضبط `pycache_prefix` فمسارُ ذاكرته تحت المجلّد الخاصّ."""
    import subprocess
    script = ("import sys; sys.path.insert(0, 'tools'); import context_budget as cb, context_index as ci; "
              "print(ci.__cached__); print(cb._PYCACHE_PREFIX); print(sys.pycache_prefix)")
    out = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    cached, prefix, live = out[0], out[1], out[2]
    assert prefix and live == prefix and cached.startswith(prefix), out
    assert "/tools/__pycache__/" not in cached


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
