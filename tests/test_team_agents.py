"""Agent discovery does not grant tools, cloud egress or a fabricated quality ranking."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from team import agents, catalog, transport
from team.adapters import registry
from team.adapters.base import assert_no_bypass
from team.adapters.gemini import GeminiAdapter
from team.adapters.opencode import ModelUnconfigured, OpenCodeAdapter
from team.adapters.antigravity import AntigravityAdapter, AntigravityModelUnconfigured


def inventory(tmp_path):
    binary = tmp_path / "cli"
    binary.write_text("installed")
    names = ("qwen3.5:9b", "deepseek-v4.1-flash:cloud", "kimi-k2.6:cloud", "minimax-m3:cloud",
             "gemma4:latest", "gpt-oss:120b-cloud", "llama3.1:8b", "mistral-large-3:675b-cloud",
             "huggingface.co/inception42/Jais-2-8B-Chat-GGUF:Q4_K_M", "qwen3-embedding:0.6b", "gone:cloud")
    def request(endpoint, path, body=None):
        if path == "/api/tags":
            return {"models": [{"name": name, "digest": "digest:" + name} for name in names]}
        if body["model"] == "gone:cloud":
            raise transport.TransportError("http_404")
        return {"capabilities": ["tools", "embedding"] if "embedding" in body["model"] else ["completion", "tools"]}
    return catalog.discover(request=request, which=lambda _: binary)


def test_discovery_distinguishes_models_families_routes_and_missing_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("DIWAN_TEAM_OPENCODE_MODEL", raising=False)
    monkeypatch.delenv("DIWAN_TEAM_ANTIGRAVITY_MODEL", raising=False)
    inv = inventory(tmp_path)
    rows = {a["id"]: a for a in inv["agents"]}
    assert rows["ollama:qwen3.5:9b"]["family"] == "qwen"
    assert rows["ollama:deepseek-v4.1-flash:cloud"]["placement"] == "cloud"
    assert rows["ollama:mistral-large-3:675b-cloud"]["placement"] == "cloud"
    assert rows["ollama:gpt-oss:120b-cloud"]["family"] == "openai"
    assert rows["ollama:llama3.1:8b"]["family"] == "meta"
    assert rows["ollama:gemma4:latest"]["family"] == "google"
    assert rows["ollama:minimax-m3:cloud"]["family"] == "minimax"
    assert rows["ollama:huggingface.co/inception42/Jais-2-8B-Chat-GGUF:Q4_K_M"]["family"] == "inception"
    assert not rows["kimi"]["repository_access"] and rows["kimi"]["route"] == "external_kimi_driver"
    assert not rows["hermes"]["repository_access"] and rows["hermes"]["route"] == "external_hermes_research"
    assert "coding" not in rows["hermes"]["roles"] and "review" not in rows["hermes"]["roles"]
    assert "review" not in rows["ollama:kimi-k2.6:cloud"]["roles"]
    assert "coding" not in rows["ollama:qwen3.5:9b"]["roles"]
    assert rows["opencode"]["status"] == "model_unconfigured"
    assert rows["antigravity"]["status"] == "model_unconfigured" and rows["antigravity"]["family"] == "unknown"
    assert {r["reason"] for r in inv["excluded"]} == {"not_a_completion_model", "http_404"}
    assert "ollama:qwen3-embedding:0.6b" not in rows


def test_unknown_family_and_unsupported_endpoint_fail_closed(tmp_path):
    assert catalog.model_family("relay/custom-name") == "unknown"
    for endpoint in ("http://example.org:11434", "https://127.0.0.1:11434", "http://127.0.0.1:11434/x",
                     "http://user:password@localhost:11434", "http://localhost:11434?token=x"):
        with pytest.raises(transport.TransportError, match="non_loopback_gateway_refused"):
            transport.validate_endpoint(endpoint)
    transport.validate_endpoint("http://127.0.0.1:11434")
    with pytest.raises(transport.TransportError, match="endpoint_path_refused"):
        transport.request_json("http://127.0.0.1:11434", "/api/pull")
    with pytest.raises(transport.TransportError, match="redirect_refused"):
        transport.NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.org")


def test_quality_selection_needs_fresh_identity_bound_task_evidence_and_independence(tmp_path):
    inv = inventory(tmp_path)
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    qwen = next(a for a in inv["agents"] if a["id"] == "ollama:qwen3.5:9b")
    row = {"agent": qwen["id"], "identity": qwen["identity"], "role": "review", "score": 90,
           "status": "passed", "source": "synthetic-fixture", "at": now.isoformat()}
    assert catalog.select(inv, "review", [], now=now)["selected"] is None
    assert catalog.select(inv, "review", [row], now=now)["selected"] == qwen["id"]
    assert catalog.select(inv, "review", [row], author_families=("qwen",), now=now)["selected"] is None
    for change in ({"identity": "old-model"}, {"role": "coding"}, {"at": "2026-08-01T00:00:00+00:00"},
                   {"at": "2027-01-01T00:00:00+00:00"}, {"score": float("nan")}, {"score": 101},
                   {"status": "failed"}, {"source": ""}, {"at": "bad-time"}):
        assert catalog.select(inv, "review", [row | change], now=now)["selected"] is None
    candidate = qwen | {"id": "unknown", "family": "unknown"}
    assert catalog.select({"agents": [candidate]}, "review", [row | {"agent": "unknown"}], now=now)["selected"] is None


def test_recorded_evidence_is_validated_deduplicated_and_bound_to_identity(tmp_path):
    inv = inventory(tmp_path)
    qwen = next(a for a in inv["agents"] if a["id"] == "ollama:qwen3.5:9b")
    home = tmp_path / "state"
    for score, source in ((float("nan"), "probe"), (101, "probe"), (50, "")):
        with pytest.raises(ValueError, match="invalid_task_evidence"):
            catalog.record_evidence(home, qwen, "arabic", score, source)
    assert not home.exists()
    home.mkdir()
    (home / "agent-evidence.json").write_text('[null, 42, "bad"]')
    catalog.record_evidence(home, qwen, "arabic", 80, "probe")
    catalog.record_evidence(home, qwen, "arabic", 90, "probe")
    rows = catalog.load_evidence(home)
    assert len(rows) == 1 and rows[0]["score"] == 90 and rows[0]["identity"] == qwen["identity"]
    assert catalog.select(inv, "arabic", rows)["selected"] == qwen["id"]
    assert catalog.select(inv, "coding", rows)["selected"] is None


def test_cloud_and_cli_routes_cannot_be_invoked_accidentally_and_dry_run_has_no_effect(tmp_path):
    inv = inventory(tmp_path)
    home = tmp_path / "state"
    called = []
    def request(*a, **kw):
        called.append(a)
        return {"done": True, "message": {"content": "unexpected generation"}}
    with pytest.raises(agents.AgentError, match="cloud_egress_not_enabled"):
        agents.ask(inv, "ollama:kimi-k2.6:cloud", "drafting", "test", home=home, execute=True, request=request)
    with pytest.raises(agents.AgentError, match="use_dispatch_or_external_kimi_driver"):
        agents.ask(inv, "kimi", "benchmark_author", "test", home=home, execute=True, request=request)
    with pytest.raises(agents.AgentError, match="role_not_supported"):
        agents.ask(inv, "ollama:qwen3.5:9b", "coding", "test", home=home, execute=True, request=request)
    plan = agents.ask(inv, "ollama:deepseek-v4.1-flash:cloud", "reasoning", "test", home=home,
                      allow_cloud=True, request=request)
    assert plan["status"] == "dry_run" and plan["tools"] == [] and not plan["repository_access"]
    assert not called and not home.exists()


def test_text_execution_has_no_tools_stores_only_bounded_result_and_never_retries(tmp_path):
    inv = inventory(tmp_path)
    calls = []
    def request(endpoint, path, body, **kw):
        calls.append(body)
        return {"done": True, "message": {"content": "نتيجة"}, "eval_count": 3}
    home = tmp_path / "state"
    out = agents.ask(inv, "ollama:qwen3.5:9b", "reasoning", "2+2", home=home, execute=True, request=request)
    stored = json.loads(Path(out["result_path"]).read_text())
    assert out["status"] == "completed" and stored["response"] == "نتيجة"
    assert "tools" not in calls[0] and calls[0]["keep_alive"] == 0 and "prompt" not in stored
    assert stored["spend_basis"] == "local_no_charge"
    assert len(calls) == 1
    for reply in ({"done": False, "message": {"content": "partial"}},
                  {"done": True, "message": {"content": "", "thinking": "not the answer"}},
                  {"done": True, "message": {"content": "done", "tool_calls": [{"function": {"name": "write"}}]}},
                  {"done": True, "error": "broken", "message": {"content": "done"}}):
        out = agents.ask(inv, "ollama:qwen3.5:9b", "reasoning", "test", home=home, execute=True,
                         request=lambda *a, **kw: reply)
        assert out["status"] == "outcome_unknown"
    with pytest.raises(agents.AgentError, match="prompt_empty_or_too_large"):
        agents.ask(inv, "ollama:qwen3.5:9b", "reasoning", "x" * (agents.MAX_PROMPT_BYTES + 1), home=home, execute=True)


def test_gemini_json_error_and_missing_result_are_not_success(tmp_path):
    adapter = GeminiAdapter(binary=tmp_path / "gemini")
    argv = adapter.work_argv(tmp_path, 1, tmp_path)
    assert_no_bypass(argv)
    assert "--sandbox" in argv and argv[argv.index("--approval-mode") + 1] == "auto_edit"
    good = adapter.parse_work(0, json.dumps({"response": "تم"}), "", tmp_path)
    assert good.ok and good.text == "تم"
    for payload in ({"error": {"message": "quota"}}, {}, {"response": "تم", "error": "bad"}, []):
        assert not adapter.parse_work(0, json.dumps(payload), "", tmp_path).ok
    assert not adapter.parse_work(1, json.dumps({"response": "تم"}), "", tmp_path).ok
    assert adapter.parse_review(0, json.dumps({"response": "الحكم: صامد"}), "").verdict == "pass"
    assert adapter.spec.trust["reviewer_eligible"] is False


def test_opencode_requires_explicit_known_model_family_and_terminal_event(tmp_path):
    for model in ("", "relay/custom", "ollama/kimi-k2.6:cloud"):
        with pytest.raises(ModelUnconfigured):
            OpenCodeAdapter(model=model).work_argv(tmp_path, 1, tmp_path)
    adapter = OpenCodeAdapter(binary=tmp_path / "opencode", model="ollama/qwen3.5:9b")
    argv = adapter.work_argv(tmp_path, 1, tmp_path)
    assert_no_bypass(argv)
    assert adapter.spec.family == "qwen" and argv[argv.index("--model") + 1] == "ollama/qwen3.5:9b"
    events = [{"type": "text", "sessionID": "s1", "part": {"text": "الحكم: صامد"}},
              {"type": "step_finish", "part": {"reason": "stop"}}]
    encoded = "\n".join(map(json.dumps, events))
    assert adapter.parse_work(0, encoded, "", tmp_path).ok
    assert adapter.parse_review(0, encoded, "").verdict == "pass"
    assert not adapter.parse_work(0, json.dumps(events[0]), "", tmp_path).ok
    assert not adapter.parse_work(0, encoded + '\n{"type":"error"}', "", tmp_path).ok
    assert not adapter.parse_work(1, encoded, "", tmp_path).ok


def test_worker_registry_and_parser_expose_only_supported_cli_workers():
    from team.dispatch import build_parser
    from team.doctor import default_adapters
    assert set(registry()) == {"claude", "codex", "gemini", "opencode", "antigravity"}
    assert {a.spec.name for a in default_adapters()} == set(registry())
    for name in ("gemini", "opencode", "antigravity", "auto"):
        assert build_parser().parse_args(["run", "356", "--worker", name]).worker == name
    # Text models cannot impersonate a repository worker.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "356", "--worker", "kimi"])


def test_new_workers_do_not_gain_counted_reviews_or_unregistered_commit_identities(tmp_path):
    from team.projects.base import Issue, ProjectError
    from team.projects.diwan import DiwanProject
    project = DiwanProject(root=tmp_path)
    project._registry = {"agents": {"openai/codex": {}, "google/gemini-jules": {}}}
    policy = project.review_policy({"anthropic"})
    assert {"gemini", "opencode", "antigravity"} <= set(policy.never) and policy.candidates == ["codex"]
    assert "google/gemini-jules" in project.brief_header(Issue(356, "test", ""), "google")
    with pytest.raises(ProjectError, match="worker_family_not_registered"):
        project.brief_header(Issue(356, "test", ""), "qwen")


def test_auto_dispatch_cannot_invent_a_best_worker(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from team.dispatch import Refusal, build
    monkeypatch.setenv("DIWAN_TEAM_HOME", str(tmp_path / "state"))
    inv = inventory(tmp_path)
    monkeypatch.setattr(catalog, "discover", lambda **kw: inv)
    monkeypatch.setattr(catalog, "load_evidence", lambda home: [])
    args = SimpleNamespace(repo_root=str(tmp_path), worker="auto")
    with pytest.raises(Refusal, match="task_evidence_missing"):
        build(args)
    assert not (tmp_path / "state" / "dispatch.jsonl").exists()


def test_recent_health_failures_block_selection_but_cannot_poison_another_model_identity(tmp_path):
    inv = inventory(tmp_path)
    qwen = next(a for a in inv["agents"] if a["id"] == "ollama:qwen3.5:9b")
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    home = tmp_path / "state"
    (home / "agents").mkdir(parents=True)
    row = {"agent": qwen["id"], "identity": qwen["identity"], "at": now.isoformat(),
           "status": "outcome_unknown", "code": "quota_exhausted"}
    path = home / "agents" / "failed.json"
    malformed = row.copy()
    del malformed["status"]
    path.write_text(json.dumps(malformed))
    catalog.apply_health(inv["agents"], home, now=now)
    assert qwen["status"] == "catalogued_unverified"
    path.write_text(json.dumps(row | {"identity": "other-model"}))
    catalog.apply_health(inv["agents"], home, now=now)
    assert qwen["status"] == "catalogued_unverified"
    path.write_text(json.dumps(row))
    catalog.apply_health(inv["agents"], home, now=now)
    assert qwen["status"] == "recently_unavailable"
    evidence = row | {"role": "arabic", "status": "passed", "score": 90, "source": "fixture"}
    assert catalog.select(inv, "arabic", [evidence], now=now)["selected"] is None


def test_antigravity_binds_family_to_explicit_model_and_rejects_error_or_missing_output(tmp_path):
    for model in ("", "custom-model", "qwen3.5:9b", "kimi-k2.6:cloud"):
        with pytest.raises(AntigravityModelUnconfigured):
            AntigravityAdapter(model=model).work_argv(tmp_path, 1, tmp_path)
    adapter = AntigravityAdapter(binary=tmp_path / "agy", model="gemini-3.1-pro-high")
    argv = adapter.work_argv(tmp_path, 1, tmp_path)
    assert_no_bypass(argv)
    assert "--sandbox" in argv and argv[argv.index("--model") + 1] == "gemini-3.1-pro-high"
    assert argv[argv.index("--mode") + 1] == "accept-edits" and adapter.spec.family == "google"
    assert AntigravityAdapter(model="claude-opus-5-5-high").spec.family == "anthropic"
    assert AntigravityAdapter(model="gpt-oss-120b-medium").spec.family == "openai"
    assert adapter.parse_work(0, '{"result":"تم","is_error":false}', "", tmp_path).ok
    for payload in ({"result": "تم", "is_error": True}, {"result": "تم", "error": "quota"}, {}, []):
        assert not adapter.parse_work(0, json.dumps(payload), "", tmp_path).ok
    assert not adapter.parse_work(1, '{"result":"تم"}', "", tmp_path).ok
    assert adapter.parse_review(0, '{"result":"الحكم: صامد"}', "").verdict == "pass"
    assert adapter.review_argv(tmp_path, "main")[argv.index("--mode") + 1] == "plan"


def test_auto_text_selection_uses_allowed_routes_and_requires_review_author_family(tmp_path, monkeypatch, capsys):
    inv = inventory(tmp_path)
    rows = {a["id"]: a for a in inv["agents"]}
    local, cloud = "ollama:qwen3.5:9b", "ollama:deepseek-v4.1-flash:cloud"
    evidence = [{"agent": key, "identity": rows[key]["identity"], "role": "review", "score": score,
                 "at": datetime.now(timezone.utc).isoformat(), "status": "passed", "source": "fixture"}
                for key, score in ((local, 70), (cloud, 90))]
    monkeypatch.setattr(agents, "discover", lambda **kw: inv)
    monkeypatch.setattr(agents, "load_evidence", lambda home: evidence)
    monkeypatch.setattr(agents, "team_home", lambda: tmp_path / "unused-state")
    args = ["ask", "--agent", "auto", "--role", "review"]
    assert agents.main(args) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "review_author_family_missing"
    assert agents.main(args + ["--author-family", "openai"]) == 0
    assert json.loads(capsys.readouterr().out)["agent"] == local
    assert agents.main(args + ["--author-family", "openai", "--allow-cloud"]) == 0
    assert json.loads(capsys.readouterr().out)["agent"] == cloud
    assert agents.main(args + ["--author-family", "deepseek", "--allow-cloud"]) == 0
    assert json.loads(capsys.readouterr().out)["agent"] == local
    assert not (tmp_path / "unused-state").exists()
