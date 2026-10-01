"""Synthetic, inference-free check of the deployment-only deadline adapter."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import runpy
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import webui.server as server

original_agent, original_chat = server.AgentSession, server.ChatSession
try:
    with patch("tools.serve_ui.main", return_value=0):
        try:
            runpy.run_path(str(Path(__file__).with_name("cloud_app.py")))
        except SystemExit as exc:
            assert exc.code == 0
    assert server.AgentSession.func is original_agent
    assert server.ChatSession.func is original_chat
    assert server.AgentSession.keywords == {"deadline_s": 300}
    assert server.ChatSession.keywords == {"deadline_s": 300}
    with TemporaryDirectory(prefix="diwan-cloud-defaults-") as temporary:
        app = server.LocalApp(Path(temporary).resolve(), model="synthetic",
                              model_version="synthetic", provider_factory=lambda: None,
                              agent_provider_factory=lambda: None)
        try:
            workspace = app.default_workspace()
            project = app.project(workspace["project"]["id"])
            saved = app.agent_session(project, workspace["session"]["id"])
            assert float(saved.config["deadline_s"]) == 300
            # An explicit existing/configured value overrides functools.partial.
            explicit = server.AgentSession(project / "agent-control", "a" * 32,
                workspace_root=app.agent_workspace(project), project_id=project.name,
                registry=app.mode_registry(project, "agent"), model="synthetic",
                model_version="synthetic", deadline_s=120)
            assert float(explicit.config["deadline_s"]) == 120
            reopened = app.agent_session(project, "a" * 32)
            assert float(reopened.config["deadline_s"]) == 120
        finally:
            app.close()
    print(json.dumps({"passed": True, "new_session_deadline_s": 300,
                      "explicit_saved_session_deadline_s": 120,
                      "inference_performed": False, "synthetic_only": True}))
finally:
    server.AgentSession, server.ChatSession = original_agent, original_chat
