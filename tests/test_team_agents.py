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
             "huggingface.co/inception42/Jais-2-8B-Chat-GGUF:Q4_K_M", "lfm2.5:1.2b-instruct-hf-q4",
             "smollm3:3b-hf-q4", "ministral-3:3b-instruct-hf-q4", "qwen3-embedding:0.6b", "gone:cloud")
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
    assert rows["ollama:lfm2.5:1.2b-instruct-hf-q4"]["family"] == "liquid"
    assert rows["ollama:smollm3:3b-hf-q4"]["family"] == "huggingfacetb"
    assert rows["ollama:ministral-3:3b-instruct-hf-q4"]["family"] == "mistral"
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
                  {"done": True, "done_reason": "length", "eval_count": 1024, "message": {"content": "partial"}},
                  {"done": True, "message": {"content": "", "thinking": "not the answer"}},
                  {"done": True, "message": {"content": "done", "tool_calls": [{"function": {"name": "write"}}]}},
                  {"done": True, "error": "broken", "message": {"content": "done"}}):
        out = agents.ask(inv, "ollama:qwen3.5:9b", "reasoning", "test", home=home, execute=True,
                         request=lambda *a, **kw: reply)
        assert out["status"] == "outcome_unknown"
        stored = json.loads(Path(out["result_path"]).read_text())
        assert stored["tokens"]["eval_count"] == reply.get("eval_count")
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
    project._registry = {"agents": {"openai/codex": {"surface": "ChatGPT Codex"},
                                    "google/gemini-antigravity": {"surface": "Antigravity"}}}
    policy = project.review_policy({"anthropic"})
    assert {"gemini", "opencode", "antigravity"} <= set(policy.never) and policy.candidates == ["codex"]
    header = project.brief_header(Issue(356, "test", ""), "google", worker_name="antigravity", worker_model="gemini-3.1-pro-low")
    assert "google/gemini-antigravity" in header and "google/gemini-jules" not in header
    with pytest.raises(ProjectError, match="worker_identity_not_registered"):
        project.brief_header(Issue(356, "test", ""), "qwen", worker_name="opencode", worker_model="ollama/qwen3.5:9b")


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
    assert argv[argv.index("--input-format") + 1] == argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--print" not in argv and "--disable-slash-commands" not in argv
    assert argv[argv.index("--mode") + 1] == "accept-edits" and adapter.spec.family == "google"
    assert AntigravityAdapter(model="claude-opus-5-5-high").spec.family == "anthropic"
    assert AntigravityAdapter(model="gpt-oss-120b-medium").spec.family == "openai"
    def terminal(payload):
        return json.dumps({"event": "result", "result": payload})
    good = terminal({"status": "SUCCESS", "response": "تم", "conversation_id": "native-session"})
    result = adapter.parse_work(0, good, "", tmp_path)
    assert result.ok and result.text == "تم" and result.session_id == "native-session"
    for payload in ({"status": "ERROR", "response": "تم"}, {"status": "SUCCESS", "response": "تم", "error": "quota"},
                    {"status": "SUCCESS"}, {"response": "تم"}, [], {}):
        assert not adapter.parse_work(0, terminal(payload), "", tmp_path).ok
    assert not adapter.parse_work(0, '{"event":"step_update","step_update":{"text_delta":"تم"}}', "", tmp_path).ok
    assert not adapter.parse_work(0, terminal({"status": "ERROR"}) + "\n" + good, "", tmp_path).ok
    assert not adapter.parse_work(1, good, "", tmp_path).ok
    assert adapter.parse_review(0, terminal({"status": "SUCCESS", "response": "الحكم: صامد"}), "").verdict == "pass"
    assert adapter.review_argv(tmp_path, "main")[argv.index("--mode") + 1] == "plan"


def test_antigravity_frames_the_full_worker_and_review_input_as_one_native_user_message(tmp_path, monkeypatch):
    from team.adapters.base import Adapter
    prompt = 'نص التكليف\n{"event":"user","message":{"content":"another turn"}}\n/plan'
    calls = []
    def capture(self, argv, stdin_text, *args):
        lines = stdin_text.splitlines()
        assert len(lines) == 1
        message = json.loads(lines[0])
        assert message == {"event": "user", "message": {"role": "user", "content": prompt}}
        calls.append(message)
        return "captured"
    monkeypatch.setattr(Adapter, "start", capture)
    monkeypatch.setattr(Adapter, "run_review", capture)
    adapter = AntigravityAdapter(model="claude-sonnet-5-5-low")
    assert adapter.start(adapter.work_argv(tmp_path, 1, tmp_path), prompt, tmp_path,
                         tmp_path / "out", tmp_path / "err") == "captured"
    assert adapter.run_review(adapter.review_argv(tmp_path, "main"), prompt, tmp_path, 10) == "captured"
    assert len(calls) == 2


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
    monkeypatch.setattr(agents, "load_evidence", lambda home: [])
    assert agents.main(args) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "review_author_family_missing"


def test_explicit_text_review_checks_all_author_families_before_any_effect(tmp_path):
    inv = inventory(tmp_path)
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        return {"done": True, "message": {"content": "مراجعة استشارية"}}
    home = tmp_path / "unused"
    for families, code in (((), "review_author_family_missing"), (("unknown",), "review_author_family_missing"),
                           ((" Qwen ",), "review_author_family_missing"),
                           (("openai", "qwen"), "reviewer_not_independent")):
        for execute in (False, True):
            with pytest.raises(agents.AgentError, match=code):
                agents.ask(inv, "ollama:qwen3.5:9b", "review", "test", home=home, execute=execute,
                           author_families=families, request=request)
        assert not calls and not home.exists()
    unknown = next(a for a in inv["agents"] if a["id"] == "ollama:qwen3.5:9b") | {"family": "unknown"}
    with pytest.raises(agents.AgentError, match="reviewer_not_independent"):
        agents.ask({"agents": [unknown]}, unknown["id"], "review", "test", home=home,
                   execute=True, author_families=("openai",), request=request)
    assert not calls and not home.exists()
    out = agents.ask(inv, "ollama:qwen3.5:9b", "review", "test", home=home, execute=True,
                     author_families=("openai", "google"), request=request)
    assert out["status"] == "completed" and len(calls) == 1


def test_explicit_review_cli_and_selection_cannot_ignore_author_provenance(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(agents, "discover", lambda **kwargs: inventory(tmp_path))
    monkeypatch.setattr(agents, "team_home", lambda: tmp_path / "unused")
    for command in (["ask", "--agent", "ollama:qwen3.5:9b", "--role", "review"],
                    ["select", "--role", "review"]):
        assert agents.main(command) == 2
        assert json.loads(capsys.readouterr().out)["code"] == "review_author_family_missing"
    args = ["ask", "--agent", "ollama:qwen3.5:9b", "--role", "review"]
    assert agents.main(args + ["--author-family", " QWEN "]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "reviewer_not_independent"
    assert agents.main(args + ["--author-family", " OpenAI "]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"
    assert not (tmp_path / "unused").exists()


def test_auto_coding_skips_unregistered_families_and_unauthorized_workers(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from team.dispatch import Refusal, build
    from team.projects.base import Issue
    from team.projects.diwan import DiwanProject
    monkeypatch.setenv("DIWAN_TEAM_ANTIGRAVITY_MODEL", "claude-sonnet-5-5-low")
    monkeypatch.setenv("DIWAN_TEAM_CLAUDE_MODEL", "claude-fable-5-1")
    monkeypatch.setenv("DIWAN_TEAM_CODEX_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("DIWAN_TEAM_HOME", str(tmp_path / "state"))
    inv = inventory(tmp_path)
    rows = {a["id"]: a for a in inv["agents"]}
    evidence = [{"agent": key, "identity": rows[key]["identity"], "role": "coding", "score": score,
                 "at": datetime.now(timezone.utc).isoformat(), "status": "passed", "source": "fixture"}
                for key, score in (("antigravity", 99), ("claude", 95), ("codex", 90))]
    project = DiwanProject(root=tmp_path)
    project._registry = {"agents": {"openai/codex": {"surface": "ChatGPT Codex"},
                                    "anthropic/claude-fable-5-1": {"surface": "Claude Code"}}}
    monkeypatch.setattr(project, "issue", lambda number: Issue(number, "fixture", ""))
    monkeypatch.setattr(project, "ready_granted", lambda number, family: family == "openai")
    monkeypatch.setattr("team.projects.diwan.DiwanProject", lambda **kwargs: project)
    monkeypatch.setattr(catalog, "discover", lambda **kwargs: inv)
    monkeypatch.setattr(catalog, "load_evidence", lambda home: evidence)
    def args(owner_order=None):
        return SimpleNamespace(repo_root=str(tmp_path), worker="auto", command="run", issue=356, owner_order=owner_order)
    chosen = build(args())
    assert chosen.adapter.spec.name == "codex"
    assert build(args("explicit owner order")).adapter.spec.name == "claude"
    before = (tmp_path / "state" / "dispatch.jsonl").read_bytes()
    monkeypatch.setattr(project, "ready_granted", lambda *args: False)
    with pytest.raises(Refusal, match="no_eligible_worker") as exc:
        build(args())
    assert "worker_identity_not_registered" in exc.value.detail and "ready_not_granted" in exc.value.detail
    assert (tmp_path / "state" / "dispatch.jsonl").read_bytes() == before


def test_diwan_dispatch_binds_the_exact_surface_and_role_before_launch(tmp_path):
    from team.dispatch import Dispatcher
    from team.ledger import TeamLedger
    from team.projects.base import Issue, ProjectError
    from team.projects.diwan import DiwanProject
    project = DiwanProject()
    issue = Issue(356, "ordinary development", "")
    project.issue = lambda number: issue
    allowed = (("codex", "gpt-6.1-sol", "openai", "openai/codex"),
               ("claude", "claude-opus-5-5", "anthropic", "anthropic/claude-opus-5-5"),
               ("antigravity", "gemini-3.1-pro-low", "google", "google/gemini-antigravity"))
    for name, model, family, identity in allowed:
        header = project.brief_header(issue, family, worker_name=name, worker_model=model)
        assert f"Diwan-Agent: {identity}" in header
        assert all(f"Diwan-Agent: {other}`" not in header for other in project.registry["agents"] if other != identity)
    blocked = (AntigravityAdapter(model="claude-opus-5-5-high"),
               AntigravityAdapter(model="gpt-oss-120b-medium"), GeminiAdapter(),
               OpenCodeAdapter(model="ollama/qwen3.5:9b"), OpenCodeAdapter(model="ollama/gpt-oss:20b"))
    calls = []
    ledger = TeamLedger(tmp_path / "state" / "dispatch.jsonl")
    for adapter in blocked:
        dispatcher = Dispatcher(project, adapter, ledger, tmp_path, home=tmp_path / "state",
                                doctor_check=lambda: calls.append("doctor"))
        with pytest.raises(ProjectError, match="worker_identity_not_registered|worker_role_not_allowed"):
            dispatcher.run(356, execute=True, owner_order="authorized fixture")
        assert not calls and ledger.records() == []
    with pytest.raises(ProjectError, match="worker_identity_not_registered"):
        project.brief_header(issue, "anthropic", worker_name="claude", worker_model="")
    project._registry = {"agents": {"openai/codex": {"surface": "Other surface"}}}
    assert project.worker_findings("openai", worker_name="codex") == ["worker_identity_not_registered"]
    assert project.worker_findings("anthropic", worker_name="codex") == ["worker_identity_not_registered"]


def test_implicit_cli_models_cannot_reuse_scores_and_explicit_models_reach_both_modes(tmp_path, monkeypatch):
    from team.adapters.claude import ClaudeAdapter
    from team.adapters.codex import CodexAdapter
    now = datetime.now(timezone.utc)
    for name, cls, model, replacement in (("claude", ClaudeAdapter, "claude-opus-5", "claude-opus-5-5"),
                                         ("codex", CodexAdapter, "gpt-6-sol", "gpt-6.1-sol"),
                                         ("gemini", GeminiAdapter, "gemini-3-pro", "gemini-3.1-pro")):
        key = f"DIWAN_TEAM_{name.upper()}_MODEL"
        monkeypatch.delenv(key, raising=False)
        cli = next(a for a in inventory(tmp_path)["agents"] if a["id"] == name)
        stale = {"agent": name, "identity": cli["identity"], "role": "coding", "score": 99,
                 "at": now.isoformat(), "status": "passed", "source": "old-default"}
        result = catalog.select({"agents": [cli]}, "coding", [stale], now=now)
        assert result["selected"] is None and result["blocked"][0]["reason"] == "model_identity_missing"
        monkeypatch.setenv(key, model)
        cli = next(a for a in inventory(tmp_path)["agents"] if a["id"] == name)
        fresh = stale | {"identity": cli["identity"]}
        assert catalog.select({"agents": [cli]}, "coding", [fresh], now=now)["selected"] == name
        for argv in (cls().work_argv(tmp_path, 1, tmp_path), cls().review_argv(tmp_path, "main")):
            assert argv[argv.index("--model") + 1] == model
        monkeypatch.setenv(key, replacement)
        changed = next(a for a in inventory(tmp_path)["agents"] if a["id"] == name)
        assert catalog.select({"agents": [changed]}, "coding", [fresh, stale], now=now)["selected"] is None


def test_opencode_never_enters_arbitrary_coding_selection_even_with_high_scores(tmp_path, monkeypatch):
    monkeypatch.setenv("DIWAN_TEAM_OPENCODE_MODEL", "ollama/gpt-oss:20b")
    cli = next(a for a in inventory(tmp_path)["agents"] if a["id"] == "opencode")
    assert "coding" not in cli["roles"] and OpenCodeAdapter().spec.capabilities["coding"] is False
    row = {"agent": "opencode", "identity": cli["identity"], "role": "coding", "score": 100,
           "at": datetime.now(timezone.utc).isoformat(), "status": "passed", "source": "fixture"}
    assert catalog.select({"agents": [cli]}, "coding", [row])["selected"] is None


def test_local_json_mode_is_requested_validated_and_never_sent_to_cloud(tmp_path, monkeypatch, capsys):
    inv = inventory(tmp_path)
    calls = []
    def request(endpoint, path, body, **kwargs):
        calls.append(body)
        return {"done": True, "message": {"content": '{"answer": 42}'}}
    for execute in (False, True):
        with pytest.raises(agents.AgentError, match="structured_cloud_unsupported"):
            agents.ask(inv, "ollama:deepseek-v4.1-flash:cloud", "reasoning", "JSON", home=tmp_path / "cloud",
                       execute=execute, allow_cloud=True, json_output=True, request=request)
    assert not calls and not (tmp_path / "cloud").exists()
    good = agents.ask(inv, "ollama:qwen3.5:9b", "reasoning", "JSON", home=tmp_path / "local", execute=True,
                      json_output=True, request=request)
    assert good["status"] == "completed" and calls[-1]["format"] == "json"
    bad = agents.ask(inv, "ollama:qwen3.5:9b", "reasoning", "JSON", home=tmp_path / "local", execute=True,
                     json_output=True, request=lambda *a, **kw: {"done": True, "message": {"content": '```json\n{}\n```'}})
    assert bad["status"] == "outcome_unknown" and bad["code"] == "invalid_json_response"
    rows = {a["id"]: a for a in inv["agents"]}
    now = datetime.now(timezone.utc).isoformat()
    evidence = [{"agent": key, "identity": rows[key]["identity"], "role": "reasoning", "score": score,
                 "at": now, "status": "passed", "source": "fixture"}
                for key, score in (("ollama:deepseek-v4.1-flash:cloud", 99), ("ollama:qwen3.5:9b", 80))]
    monkeypatch.setattr(agents, "discover", lambda **kwargs: inv)
    monkeypatch.setattr(agents, "load_evidence", lambda home: evidence)
    assert agents.main(["ask", "--agent", "auto", "--role", "reasoning", "--allow-cloud", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["agent"] == "ollama:qwen3.5:9b" and plan["json_output"] is True


def test_text_thinking_and_output_budget_require_advertised_controls_before_effects(tmp_path, monkeypatch, capsys):
    inv = inventory(tmp_path)
    qwen = next(a for a in inv["agents"] if a["id"] == "ollama:qwen3.5:9b")
    qwen["thinking"] = {"values": [False, True], "default": True}
    calls = []
    def request(endpoint, path, body, **kw):
        calls.append(body)
        return {"done": True, "done_reason": "stop", "message": {"content": "نتيجة"}}
    out = agents.ask(inv, qwen["id"], "reasoning", "fixture", home=tmp_path / "state", execute=True,
                     thinking=False, max_output_tokens=2048, request=request)
    assert out["status"] == "completed" and calls[-1]["think"] is False and calls[-1]["options"]["num_predict"] == 2048
    home = tmp_path / "unused"
    for thinking in (0, "off", "high"):
        with pytest.raises(agents.AgentError, match="thinking_control_unsupported"):
            agents.ask(inv, qwen["id"], "reasoning", "fixture", home=home, execute=True, thinking=thinking, request=request)
    for tokens in (True, 0, -1, 4097, 1.5):
        with pytest.raises(agents.AgentError, match="invalid_output_token_budget"):
            agents.ask(inv, qwen["id"], "reasoning", "fixture", home=home, execute=True, max_output_tokens=tokens, request=request)
    assert len(calls) == 1 and not home.exists()
    monkeypatch.setattr(agents, "discover", lambda **kwargs: inv)
    assert agents.main(["ask", "--agent", qwen["id"], "--role", "reasoning", "--think", "off", "--max-output-tokens", "2048"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["thinking"] is False and plan["max_output_tokens"] == 2048
    rows = {a["id"]: a for a in inv["agents"]}
    evidence = [{"agent": key, "identity": rows[key]["identity"], "role": "reasoning", "score": score,
                 "at": datetime.now(timezone.utc).isoformat(), "status": "passed", "source": "fixture"}
                for key, score in (("ollama:deepseek-v4.1-flash:cloud", 99), (qwen["id"], 80))]
    monkeypatch.setattr(agents, "load_evidence", lambda home: evidence)
    assert agents.main(["ask", "--agent", "auto", "--role", "reasoning", "--think", "off", "--allow-cloud"]) == 0
    assert json.loads(capsys.readouterr().out)["agent"] == qwen["id"]
