"""بصمةُ النموذج مشتركةٌ بين مُشغِّلات القياس، بخادم Ollama مصطنع محلي.

المُشغِّلاتُ الأربعة من #190، ثم `measure_engine` و`evaluate_capabilities` (#285).
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

from tools import (evaluate_ablation, evaluate_agentic, evaluate_capabilities, evaluate_research,
                   evaluate_translation, measure_engine, model_digest)


class _TagsHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — اسمُ واجهة http.server
        if self.path != "/api/tags":
            self.send_error(404)
            return
        payload = json.dumps({"models": self.server.models}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # noqa: A002 — توقيع http.server
        return


@pytest.fixture
def fake_ollama(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TagsHandler)
    server.models = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(model_digest, "BASE_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture(autouse=True)
def isolated_run_root(tmp_path, monkeypatch):
    """مجلّدُ التشغيلات في مجلّدٍ مؤقّت: حجرُ الانحراف (ملاحظة Codex على #290) لا يلمس var/capabilities في النسخة."""
    root = tmp_path / "root"
    (root / "var" / "capabilities").mkdir(parents=True)
    monkeypatch.setattr(evaluate_capabilities, "ROOT", root)
    monkeypatch.setattr(measure_engine, "ROOT", root)
    return root / "var" / "capabilities"


def _translation_report() -> dict:
    return {"summary": {"attempted": 1, "measured": 1, "errors": 0, "passed": 1,
                        "pass_rate": 1.0, "check_pass_rate": 1.0, "meets_thresholds": True,
                        "categories": {}}}


def _agentic_report() -> dict:
    return {"suite_id": "fixture", "summary": {"attempted": 1, "measured": 1, "errors": 0,
                                                  "passed": 1, "pass_rate_of_measured": 1.0,
                                                  "forbidden_violations": 0},
            "by_capability": {}, "measurement_limits": []}


def _research_report() -> dict:
    return {"summary": {"attempted": 1, "measured": 1, "errors": 0, "passed": 1,
                        "pass_rate": 1.0, "citation_valid_rate": 1.0, "fabricated_sources": 0,
                        "meets": True, "by_category": {}}}


def _ablation_report() -> dict:
    return {"judgment": {"decision": "keep", "reason": "fixture", "overall": {}},
            "config": {}, "arms": {}, "measurement_limits": []}


def _engine_report() -> dict:
    return {"model": "fixture", "overall": {}, "suites_failed": 0, "elapsed_s": 0, "measurement_limits": []}


def _configure(monkeypatch, name: str, after_run, seen: list[str]) -> None:
    import providers.ollama as ollama
    monkeypatch.setattr(ollama, "OllamaProvider", lambda model: object())

    def measured(report):
        def run(*args, **kwargs):
            seen.append(kwargs["model_version"])
            after_run()
            return report()
        return run

    if name == "translation":
        monkeypatch.setattr(evaluate_translation, "run_bank", measured(_translation_report))
    elif name == "agentic":
        monkeypatch.setattr(evaluate_agentic, "run_agentic_suite", measured(_agentic_report))
        monkeypatch.setattr(evaluate_agentic, "attach_thresholds", lambda report, *args: report)
        monkeypatch.setattr(evaluate_agentic, "select_tools", lambda *args, **kwargs: ())
    elif name == "research":
        monkeypatch.setattr(evaluate_research, "run_bank", measured(_research_report))
    elif name == "engine":
        monkeypatch.setattr(measure_engine, "measure", measured(_engine_report))
    else:
        monkeypatch.setattr(evaluate_ablation, "run_component", measured(_ablation_report))


def _invoke(name: str, tmp_path: Path, suffix: str, *, expected: str | None = None) -> tuple[int, Path]:
    out = tmp_path / f"{name}-{suffix}.json"
    version = ["--model-version", expected] if expected is not None else []
    if name == "translation":
        argv = ["--model", "fixture", *version, "--license", "Apache-2.0", "--agent", "openai/codex",
                "--out", str(out)]
        return evaluate_translation.main(argv), out
    if name == "agentic":
        suite = tmp_path / "agentic.json"
        suite.write_text("{}\n", encoding="utf-8")
        argv = ["--suite", str(suite), "--model", "fixture", *version, "--out", str(out)]
        return evaluate_agentic.main(argv), out
    if name == "research":
        return evaluate_research.main(["--model", "fixture", *version, "--out", str(out)]), out
    if name == "engine":
        suites = tmp_path / "suites"
        suites.mkdir(exist_ok=True)
        (suites / "fixture.json").write_text("{}\n", encoding="utf-8")
        return measure_engine.main([str(suites), "--model", "fixture", *version, "--out", str(out)]), out
    argv = ["--component", "search", "--model", "fixture", *version, "--out", str(out)]
    return evaluate_ablation.main(argv), out


RUNNERS = ("translation", "agentic", "research", "ablation", "engine")


def test_all_four_runners_pin_the_digest_resolved_by_the_fake_ollama(tmp_path, monkeypatch, fake_ollama):
    fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:weights"}]
    for name in RUNNERS:
        seen: list[str] = []
        _configure(monkeypatch, name, lambda: None, seen)
        code, out = _invoke(name, tmp_path, "pinned")
        assert code == 0 and out.exists(), name
        assert seen == ["sha256:weights"], name


def test_all_four_runners_refuse_an_unresolved_or_mismatched_digest(tmp_path, monkeypatch, fake_ollama, capsys):
    for name in RUNNERS:
        seen: list[str] = []
        _configure(monkeypatch, name, lambda: None, seen)
        fake_ollama.models = []
        code, out = _invoke(name, tmp_path, "unresolved")
        assert code != 0 and not out.exists() and seen == [], name
        assert "model_digest_unresolved" in capsys.readouterr().out

        fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:real"}]
        code, out = _invoke(name, tmp_path, "mismatch", expected="sha256:other")
        assert code != 0 and not out.exists() and seen == [], name
        assert "model_version_mismatch" in capsys.readouterr().out


def test_all_four_runners_recheck_after_measurement_and_write_no_drifted_report(tmp_path, monkeypatch,
                                                                               fake_ollama, capsys):
    for name in RUNNERS:
        seen: list[str] = []
        fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:before"}]

        def drift():
            fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:after"}]

        _configure(monkeypatch, name, drift, seen)
        code, out = _invoke(name, tmp_path, "drift")
        assert code != 0 and not out.exists(), name
        assert seen == ["sha256:before"], name
        assert "model_digest_drifted" in capsys.readouterr().out


def _capabilities(monkeypatch, after_run, seen: list[str]) -> None:
    """`evaluate_capabilities` لا يكتب `--out`: دليلُه ما يطبعه، وتشغيلتُه في var/capabilities."""
    monkeypatch.setattr(evaluate_capabilities, "OllamaProvider", lambda model: object())
    monkeypatch.setattr(evaluate_capabilities, "load_suite", lambda path: {})

    def run(*args, **kwargs):
        seen.append(kwargs["model_version"])
        after_run()
        return {"suite_id": "fixture", "run_id": "run-fixture",
                "summary": {"collection_complete": True, "release_ready": False}}
    monkeypatch.setattr(evaluate_capabilities, "evaluate_suite", run)


def test_capabilities_pins_refuses_and_rechecks_the_digest(monkeypatch, fake_ollama, capsys):
    seen: list[str] = []
    _capabilities(monkeypatch, lambda: None, seen)
    fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:weights"}]
    assert evaluate_capabilities.main(["--model", "fixture"]) == 0
    assert seen == ["sha256:weights"] and json.loads(capsys.readouterr().out)["run_id"] == "run-fixture"

    seen.clear()
    fake_ollama.models = []
    assert evaluate_capabilities.main(["--model", "fixture"]) == 2 and seen == []
    assert json.loads(capsys.readouterr().out)["error_code"] == "model_digest_unresolved"

    fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:real"}]
    assert evaluate_capabilities.main(["--model", "fixture", "--model-version", "sha256:other"]) == 2
    assert seen == [] and json.loads(capsys.readouterr().out)["error_code"] == "model_version_mismatch"

    def drift():
        fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:after"}]
    _capabilities(monkeypatch, drift, seen)
    assert evaluate_capabilities.main(["--model", "fixture"]) == 2 and seen == ["sha256:real"]
    output = capsys.readouterr().out
    assert json.loads(output)["error_code"] == "model_digest_drifted" and "run-fixture" not in output


@pytest.mark.parametrize("tool", ["capabilities", "engine"])
def test_a_run_rejected_for_drift_is_quarantined_so_a_returning_digest_cannot_replay_it(
        tmp_path, monkeypatch, fake_ollama, isolated_run_root, capsys, tool):
    """ملاحظةُ Codex على #290: معرّفُ التشغيلة من إعدادها وفيه البصمة، فوسمٌ انحرف ثم عاد كان يعيد في الاستدعاء التالي عرضَ
    دفترٍ كُتب بعد الانحراف ويمرّ التحقّقُ اللاحق. فالتشغيلةُ المرفوضة تُنقل إلى الحجر، وما سبق القياسَ يبقى."""
    import os
    earlier = isolated_run_root / "run-earlier"
    earlier.mkdir()
    (earlier / "ledger.jsonl").write_text("{}\n", encoding="utf-8")
    past = 1_000_000_000
    os.utime(earlier / "ledger.jsonl", (past, past))
    os.utime(earlier, (past, past))
    fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:before"}]

    def drift():
        written = isolated_run_root / "run-drifted"
        written.mkdir()
        (written / "ledger.jsonl").write_text("{}\n", encoding="utf-8")
        fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:after"}]

    seen: list[str] = []
    if tool == "capabilities":
        _capabilities(monkeypatch, drift, seen)
        code = evaluate_capabilities.main(["--model", "fixture"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 2 and payload["error_code"] == "model_digest_drifted"
    else:
        _configure(monkeypatch, "engine", drift, seen)
        code, out = _invoke("engine", tmp_path, "quarantine")
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert code == 1 and payload["code"] == "model_digest_drifted" and not out.exists()
    assert payload["runs_quarantined"] == 1
    assert sorted(p.name for p in isolated_run_root.iterdir()) == ["drift-quarantine", "run-earlier"]
    assert [p.name.rsplit("-", 1)[0] for p in (isolated_run_root / "drift-quarantine").iterdir()] == ["run-drifted"]


@pytest.mark.parametrize("tool", ["capabilities", "engine"])
def test_a_measurement_that_fails_after_writing_is_still_rechecked_and_quarantined(
        tmp_path, monkeypatch, fake_ollama, isolated_run_root, capsys, tool):
    """ملاحظةُ Codex على #290: قياسٌ كتب جوابًا ثم رمى استثناءً كان يخرج قبل التحقّق اللاحق والحجر، فتبقى تشغيلتُه
    قابلةً لإعادة العرض. صار التحقّقُ والحجرُ على كلِّ خروجٍ بعد بدء القياس، والخطأُ الأصليّ يُرفع إن لم تنحرف البصمة."""
    fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:before"}]

    def write_then_fail(drifts: bool, error: BaseException):
        def run(*args, **kwargs):
            written = isolated_run_root / "run-partial"
            written.mkdir(exist_ok=True)
            (written / "ledger.jsonl").write_text("{}\n", encoding="utf-8")
            if drifts:
                fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:after"}]
            raise error
        return run

    if tool == "capabilities":
        monkeypatch.setattr(evaluate_capabilities, "OllamaProvider", lambda model: object())
        monkeypatch.setattr(evaluate_capabilities, "load_suite", lambda path: {})
        monkeypatch.setattr(evaluate_capabilities, "evaluate_suite", write_then_fail(True, OSError("disk")))
        assert evaluate_capabilities.main(["--model", "fixture"]) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["error_code"] == "model_digest_drifted" and payload["runs_quarantined"] == 1
    else:
        monkeypatch.setattr(measure_engine, "measure", write_then_fail(True, RuntimeError("boom")))
        code, out = _invoke("engine", tmp_path, "partial")
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert code == 1 and payload["code"] == "model_digest_drifted" and payload["runs_quarantined"] == 1
        assert not out.exists()
    assert sorted(p.name for p in isolated_run_root.iterdir()) == ["drift-quarantine"]

    # بلا انحراف: الخطأُ الأصليّ يبقى كما كان، والتشغيلةُ لا تُحجر
    fake_ollama.models = [{"name": "fixture:latest", "digest": "sha256:before"}]
    if tool == "capabilities":
        monkeypatch.setattr(evaluate_capabilities, "evaluate_suite", write_then_fail(False, OSError("disk")))
        assert evaluate_capabilities.main(["--model", "fixture"]) == 2
        assert json.loads(capsys.readouterr().out)["error_code"] == "filesystem_error"
    else:
        monkeypatch.setattr(measure_engine, "measure", write_then_fail(False, RuntimeError("boom")))
        with pytest.raises(RuntimeError, match="boom"):
            _invoke("engine", tmp_path, "partial-clean")
    assert (isolated_run_root / "run-partial").is_dir()


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_a_quarantine_that_is_a_link_or_not_a_directory_is_refused_and_nothing_moves(tmp_path, kind):
    """ملاحظة Codex على #289: رابطٌ رمزيّ مُسبَقٌ باسم drift-quarantine كان يُنقل إليه الدفاترُ إلى حيث يشير (ولو المستودع)،
    فيُردّ برمزٍ مسمًّى قبل أن يُنقل شيء؛ وما ليس مجلّدًا كذلك."""
    from tools.model_digest import QUARANTINE_DIR, ModelDigestError, quarantine_runs_since
    run = tmp_path / "run-drifted"
    run.mkdir()
    (run / "ledger.jsonl").write_text("{}\n", encoding="utf-8")
    elsewhere = tmp_path.parent / f"{tmp_path.name}-checkout"
    elsewhere.mkdir()
    if kind == "symlink":
        (tmp_path / QUARANTINE_DIR).symlink_to(elsewhere, target_is_directory=True)
    else:
        (tmp_path / QUARANTINE_DIR).write_text("", encoding="utf-8")
    with pytest.raises(ModelDigestError) as refused:
        quarantine_runs_since(tmp_path, 0)
    assert refused.value.code == "quarantine_dir_unsafe"
    assert (run / "ledger.jsonl").exists() and not any(elsewhere.iterdir())
