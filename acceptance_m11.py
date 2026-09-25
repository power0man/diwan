#!/usr/bin/env python3
"""قبول HTTP محلي لم١١؛ لا يحكم العرض البصري أو جودة الاستخدام اليومية.

الافتراضي مزود مصطنع. --live يستخدم المزود المحلي القائم لطلب واحد
بملف مصطنع، ثم يعيد تشغيل الخادم ويسترجع الجولة بلا نداء إضافي.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import http.client
import json
from pathlib import Path
import re
import tempfile
import threading

from acceptance_m9 import AcceptanceError, _new_private_run, _write_private
from core.contracts import Response, Usage
from webui.server import LocalApp, Server
import webui.server as ui_module

ROOT = Path(ui_module.__file__).resolve().parents[1]
FILE_TEXT = "ملاحظة اختبار: موعد مراجعة ديوان الثلاثاء الساعة العاشرة."
MESSAGE = "لخص الملاحظة المرفقة في جملة عربية واحدة."
SYNTHETIC_MODEL = "synthetic-ui-acceptance"
SYNTHETIC_VERSION = "a" * 64


class SyntheticProvider:
    model = SYNTHETIC_MODEL
    name = "synthetic-ui-acceptance"
    is_local = True

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        return Response("موعد المراجعة الثلاثاء الساعة العاشرة.", Usage(25, 8), "complete", 0,
                        provider=self.name, model_version=SYNTHETIC_VERSION)


class CountedFactory:
    def __init__(self, factory):
        self.factory = factory
        self.delegates = []
        self.requests = []

    def __call__(self):
        delegate = self.factory()
        self.delegates.append(delegate)
        owner = self
        class Provider:
            model = delegate.model
            name = delegate.name
            is_local = delegate.is_local

            def estimate_micros(self, request):
                return delegate.estimate_micros(request)

            def complete(self, request):
                owner.requests.append(request)
                return delegate.complete(request)
        return Provider()

    def counts(self):
        result = {"provider_calls": len(self.requests), "provider_instances": len(self.delegates)}
        for name in ("chat_calls", "metadata_calls"):
            if self.delegates and all(type(getattr(d, name, None)) is int for d in self.delegates):
                result[name] = sum(getattr(d, name) for d in self.delegates)
        return result


@contextmanager
def running(root, model, model_version, factory):
    app = LocalApp(root, model=model, model_version=model_version, provider_factory=factory)
    server = None
    thread = None
    try:
        server = Server(app, 0)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
        thread.start()
        yield app, server
    finally:
        if server is not None:
            server.shutdown()
            if thread is not None:
                thread.join(timeout=5)
            server.server_close()
        app.close()


def request(server, payload=None, *, headers=None, path="/api", method="POST"):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=180)
    values = {"Origin": server.origin, "X-Diwan-CSRF": server.token,
              "Content-Type": "application/json"}
    values.update(headers or {})
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        conn.request(method, path, body=body, headers=values)
        response = conn.getresponse()
        raw = response.read()
        if response.getheader("Content-Type", "").startswith("application/json"):
            data = json.loads(raw)
        else:
            data = raw
        return response.status, data, dict(response.getheaders())
    finally:
        conn.close()


def ok(server, action, **values):
    status, result, _ = request(server, {"action": action, **values})
    if status != 200:
        raise AcceptanceError(result.get("error_code", "http_failure"))
    return result


def snapshot(root, *, include_mtime=False):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns) if include_mtime else path.read_bytes()
            for path in root.rglob("*") if path.is_file() and not path.name.endswith(".lock")}


def public_report(checks, *, scope, factory, before_replay):
    after = factory.counts()
    result = {"schema_version": 1, "scope": scope, "checks": checks,
              "passed": sum(value is True for value in checks.values()), "total": len(checks),
              "counter_scope": "one_selected_file_turn", "quality_pending": True,
              "human_review": "not_required_q49", "automated_review": "pending",
              "daily_task_efficiency": "not_measured",
              "browser_rendering": "not_tested", "independent_bank": "not_provided",
              "snapshot_scope": "state_bytes; explicit_apply_bytes_and_mtime",
              "write_approval": "explicit_synthetic_test_call", "release_ready": False}
    for name, value in before_replay.items():
        result[f"initial_{name}"] = value
        result[f"replay_{name}"] = after.get(name, value) - value
    return result


def run_case(root, *, model, model_version, provider_factory, live=False):
    factory = CountedFactory(provider_factory)
    checks, evidence = {}, {}
    before_replay = {"provider_calls": 0}
    try:
        with running(root, model, model_version, factory) as (app, server):
            checks["loopback_only"] = server.server_address[0] == "127.0.0.1"
            status, page, headers = request(server, method="GET", path="/")
            checks["arabic_page_and_security_headers"] = (
                status == 200 and b'lang="ar"' in page and b'dir="rtl"' in page and
                headers.get("X-Frame-Options") == "DENY" and
                "default-src 'none'" in headers.get("Content-Security-Policy", ""))
            before = snapshot(root)
            statuses = [request(server, {"action": "create_project", "name": "unauthorized"}, headers=h)[0]
                        for h in ({"Origin": "https://outside.invalid"},
                                  {"X-Diwan-CSRF": "wrong"}, {"Host": "outside.invalid"})]
            checks["boundary_refuses_without_effect"] = statuses == [403, 403, 403] and snapshot(root) == before and not factory.requests
            first = ok(server, "create_project", name="مشروع الاختبار الأول")
            other = ok(server, "create_project", name="مشروع الاختبار الثاني")
            session = ok(server, "create_session", project=first["id"], name="جلسة الاختبار")
            other_session = ok(server, "create_session", project=other["id"], name="جلسة مستقلة")
            ctx = {"project": first["id"], "session": session["id"]}
            other_ctx = {"project": other["id"], "session": other_session["id"]}
            turn_id = "1" * 32
            preferences = ok(server, "set_preference", project=first["id"], key="response_language", value="ar", revision=0)
            upload = ok(server, "upload", project=first["id"], upload="2" * 32, name="ملاحظة.txt", content=FILE_TEXT)
            checks["upload_bound_to_bytes"] = (
                upload["sha256"] == hashlib.sha256(FILE_TEXT.encode("utf-8")).hexdigest() and
                upload["size_bytes"] == len(FILE_TEXT.encode("utf-8")))
            checks["project_files_and_preferences_isolated"] = (
                ok(server, "files", project=other["id"])["files"] == [] and
                ok(server, "preferences", project=other["id"])["values"] == {})
            result = ok(server, "ask", **ctx, turn=turn_id, message=MESSAGE, files=[upload["path"]])
            evidence.update(context=ctx, turn=turn_id, upload=upload, result=result)
            before_replay = factory.counts()
            checks["one_fresh_unverified_answer"] = (
                before_replay["provider_calls"] == 1 and result["status"] == "complete" and
                result["verification"] == "unverified" and result["replayed"] is False)
            checks["no_model_tools_or_remote_policy"] = (
                len(factory.requests) == 1 and factory.requests[0].tools == () and
                factory.requests[0].data_policy == "local_only")
            checks["answer_does_not_change_preferences"] = ok(server, "preferences", project=first["id"]) == preferences
            other_history = ok(server, "history", **other_ctx, before=None)
            cross_status, _, _ = request(server, {"action": "history", "project": other["id"],
                                                 "session": session["id"], "before": None})
            checks["session_history_isolated"] = other_history["turns"] == [] and cross_status != 200
            frozen = ok(server, "inspect", **ctx, turn=turn_id)
            checks["visible_frozen_input_witness"] = (
                frozen["verification"] == "unverified" and frozen["preferences"] == preferences and
                len(frozen["attachments"]) == 1 and frozen["attachments"][0]["content"] == FILE_TEXT and
                frozen["attachments"][0]["sha256"] == upload["sha256"])
            # Deliberate fixture changes must not alter what an earlier answer used.
            ok(server, "set_preference", project=first["id"], key="verbosity", value="concise", revision=preferences["revision"])
            (app.project(first["id"]) / "uploads" / upload["path"]).unlink()
            checks["witness_survives_source_and_preference_changes"] = ok(server, "inspect", **ctx, turn=turn_id) == frozen
            durable_before = snapshot(root)

        # New application, process lease, HTTP port and CSRF token; same persisted turn.
        with running(root, model, model_version, factory) as (app, server):
            replay = ok(server, "replay", **ctx, turn=turn_id)
            evidence["replay"] = replay
            checks["restart_replays_same_answer"] = replay == {**result, "replayed": True}
            checks["replay_zero_provider_or_network_calls"] = factory.counts() == before_replay
            checks["restart_replay_preserves_state_bytes"] = snapshot(root) == durable_before
            checks["restarted_witness_is_original"] = ok(server, "inspect", **ctx, turn=turn_id) == frozen
            if result["status"] == "complete" and result["content"].strip():
                proposal = ok(server, "propose", **ctx, turn=turn_id, name="مسودة.txt", request="3" * 32)
                target = app.project(first["id"]) / "outputs" / proposal["path"]
                review = ok(server, "review", project=first["id"], proposal=proposal["proposal_id"])
                checks["proposal_review_has_no_target_effect"] = (
                    not target.exists() and review["content"] == result["content"] and
                    review["sha256"] == hashlib.sha256(result["content"].encode("utf-8")).hexdigest())
                bad_status, bad, _ = request(server, {"action": "apply", "project": first["id"],
                    "proposal": proposal["proposal_id"], "sha256": "0" * 64})
                checks["wrong_approval_rejected"] = bad_status != 200 and bad["error_code"] == "approval_mismatch" and not target.exists()
                applied = ok(server, "apply", project=first["id"], proposal=proposal["proposal_id"], sha256=proposal["sha256"])
                checks["explicit_apply_matches_reviewed_bytes"] = (
                    applied["status"] == "applied" and target.read_text(encoding="utf-8") == review["content"])
                applied_before = snapshot(app.project(first["id"]) / "outputs", include_mtime=True)
                again = ok(server, "apply", project=first["id"], proposal=proposal["proposal_id"], sha256=proposal["sha256"])
                checks["repeated_apply_no_new_effect"] = again["replayed"] is True and snapshot(app.project(first["id"]) / "outputs", include_mtime=True) == applied_before
                other_proposal = ok(server, "propose", **ctx, turn=turn_id, name="مسودة.txt", request="4" * 32)
                _, collision, _ = request(server, {"action": "apply", "project": first["id"],
                    "proposal": other_proposal["proposal_id"], "sha256": other_proposal["sha256"]})
                checks["overwrite_refused_honestly"] = (
                    collision.get("error_code") == "target_exists" and collision.get("status") != "applied" and
                    target.read_text(encoding="utf-8") == review["content"])
                evidence.update(proposal=proposal, applied=applied)
            checks["no_additional_generation_for_file_actions"] = factory.counts() == before_replay
            checks["flow_complete"] = True
    except Exception as exc:
        evidence["error_code"] = getattr(exc, "code", type(exc).__name__)
        checks["flow_complete"] = False
    scope = "live_development_local_http_mechanism" if live else "synthetic_local_http_mechanism"
    return public_report(checks, scope=scope, factory=factory, before_replay=before_replay), evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--model-version")
    parser.add_argument("--run-id")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)
    if args.live and not all((args.model, args.model_version, args.run_id)):
        parser.error("--live يتطلب --model و--model-version و--run-id")
    if not args.live and any(value is not None for value in (args.model, args.model_version, args.run_id, args.root)):
        parser.error("خيارات التشغيل الحي تتطلب --live")
    try:
        if args.live:
            if not re.fullmatch(r"[a-f0-9]{64}", args.model_version):
                raise AcceptanceError("model_version_invalid")
            from providers.local_chat import LocalChatProvider
            run = _new_private_run(args.root or ROOT / "var/ui-acceptance", args.run_id)
            report, evidence = run_case(run / "app", model=args.model, model_version=args.model_version,
                provider_factory=lambda: LocalChatProvider(args.model, args.model_version), live=True)
            report["run_id"] = args.run_id
            _write_private(run / "report.json", {"summary": report, "evidence": evidence,
                "runtime_model": args.model, "model_version": args.model_version,
                "file_text": FILE_TEXT, "message": MESSAGE, "quality_pending": True, "release_ready": False})
        else:
            with tempfile.TemporaryDirectory(prefix="diwan-m11-") as temp:
                report, evidence = run_case(Path(temp).resolve() / "app", model=SYNTHETIC_MODEL,
                    model_version=SYNTHETIC_VERSION, provider_factory=SyntheticProvider)
    except (AcceptanceError, OSError) as exc:
        print(json.dumps({"error_code": getattr(exc, "code", "filesystem_error"), "release_ready": False}))
        return 1
    if "error_code" in evidence:
        report["error_code"] = evidence["error_code"]
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
