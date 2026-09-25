#!/usr/bin/env python3
"""قبول آلية الحوار متعدد الجولات، مع تشخيص محلي اختياري لا يحكم الجودة.

التشغيل الافتراضي مصطنع بلا شبكة. --live يطلب هوية نموذج مثبت صراحةً؛
يحفظ الأجوبة والهوية في دليل خاص ويطبع فحوص الآلية فقط. م٨-ب معلقة.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from conversation import ChatSession, ConversationError
from core.contracts import Response, Usage
from providers.base import ProviderError

ROOT = Path(__file__).resolve().parent
PROMPTS = (
    "أعد صياغة هذه المعلومة بجملة قصيرة: موعد التجربة يوم الأحد الساعة التاسعة.",
    "تصحيح: الموعد يوم الاثنين الساعة العاشرة. اعتمد هذا التصحيح في هذه المحادثة.",
    "ما الموعد المصحح؟ أجب بجملة واحدة.",
)
ANSWERS = (
    "موعد التجربة يوم الأحد الساعة التاسعة.",
    "الموعد المصحح يوم الاثنين الساعة العاشرة.",
    "موعد التجربة يوم الاثنين الساعة العاشرة.",
)
SYNTHETIC_MODEL = "synthetic-acceptance-only"
SYNTHETIC_VERSION = "a" * 64


class AcceptanceError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class CapturingProvider:
    """يسجل استدعاءات complete، ولا يسميها تلقائيًا نداءات شبكة."""
    def __init__(self, delegate):
        self.delegate = delegate
        self.model = delegate.model
        self.name = delegate.name
        self.is_local = delegate.is_local
        self.requests = []
        self.estimates = 0

    def estimate_micros(self, request):
        self.estimates += 1
        return self.delegate.estimate_micros(request)

    def complete(self, request):
        self.requests.append(request)
        return self.delegate.complete(request)


class SyntheticProvider:
    model = SYNTHETIC_MODEL
    name = "synthetic-acceptance"
    is_local = True

    def __init__(self, responses):
        self.responses = list(responses)

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        if not self.responses:
            raise AssertionError("unexpected synthetic provider call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _answer(text, stop_reason="complete"):
    return Response(text, Usage(10, 8), stop_reason, 0,
                    provider="synthetic-acceptance", model_version=SYNTHETIC_VERSION)


def _snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.name.endswith(".lock")}


def _context_checks(requests, results):
    complete = len(results) == len(PROMPTS) and all(r["status"] == "complete" for r in results)
    checks = {"three_complete_turns": complete,
              "ordered_multi_turn_context": False,
              "context_changes_request_identity": False,
              "local_only_without_tools": False,
              "unverified_assistant_type": False}
    if not complete or len(requests) != len(PROMPTS):
        return checks
    first = requests[0].messages
    if not first or first[0].role != "system":
        return checks
    expected = [("system", first[0].content)]
    correct = True
    for request, prompt, result in zip(requests, PROMPTS, results):
        expected.append(("user", prompt))
        correct &= [(m.role, m.content) for m in request.messages] == expected
        expected.append(("assistant", result["content"]))
    checks["ordered_multi_turn_context"] = bool(correct)
    checks["context_changes_request_identity"] = (
        len({r["request_sha256"] for r in results}) == 3 and
        len({r["context_sha256"] for r in results}) == 3)
    checks["local_only_without_tools"] = all(r.data_policy == "local_only" and r.tools == () for r in requests)
    checks["unverified_assistant_type"] = all(
        r["kind"] == "assistant_message" and r["verification"] == "unverified" for r in results)
    return checks


def _dialogue(root, provider, *, model, model_version):
    def reopen():
        return ChatSession(root, session_id="dialogue", model=model, model_version=model_version)
    session = reopen()
    results = []
    for i, prompt in enumerate(PROMPTS, 1):
        result = session.turn(f"turn-{i}", prompt, provider)
        results.append(result)
        if result["status"] != "complete":
            break
    initial_calls = len(provider.requests)
    initial_estimates = provider.estimates
    network_before = {name: getattr(provider.delegate, name)
                      for name in ("chat_calls", "metadata_calls")
                      if type(getattr(provider.delegate, name, None)) is int}
    before = _snapshot(root)
    replays = [reopen().turn(f"turn-{i}", prompt, provider)
               for i, prompt in enumerate(PROMPTS[:len(results)], 1)]
    checks = _context_checks(provider.requests[:initial_calls], results)
    checks.update({
        "three_fresh_calls": initial_calls == 3 and not any(r["replayed"] for r in results),
        "restart_replays_same_results": len(replays) == 3 and all(r["replayed"] for r in replays) and
            [{k: v for k, v in r.items() if k != "replayed"} for r in results] ==
            [{k: v for k, v in r.items() if k != "replayed"} for r in replays],
        "replay_zero_provider_calls": len(provider.requests) == initial_calls and provider.estimates == initial_estimates,
        "replay_preserves_files": _snapshot(root) == before,
    })
    network = {}
    if network_before:
        network = {f"initial_{name}": value for name, value in network_before.items()}
        network.update({f"replay_{name}": getattr(provider.delegate, name) - value
                        for name, value in network_before.items()})
        checks["replay_zero_network_requests"] = all(
            getattr(provider.delegate, name) == value for name, value in network_before.items())
        if "chat_calls" in network_before:
            checks["three_initial_chat_requests"] = network_before["chat_calls"] == 3
    return checks, results, replays, initial_calls, network


def _public_report(checks, *, scope, initial_calls, replay_calls):
    return {"schema_version": 1, "scope": scope, "checks": checks,
            "passed": sum(value is True for value in checks.values()), "total": len(checks),
            "counter_scope": "dialogue_only",
            "initial_provider_calls": initial_calls, "replay_provider_calls": replay_calls,
            "quality_pending": True, "human_review": "not_required_q49",
            "automated_review": "pending", "independent_bank": "not_provided",
            "m8b_complete": False, "release_ready": False}


def run_synthetic(root):
    """فحوص المسار ببيانات مصطنعة؛ لا أحكام على استدلال نموذج."""
    provider = CapturingProvider(SyntheticProvider([_answer(text) for text in ANSWERS]))
    checks, results, _, initial_calls, _ = _dialogue(
        root / "dialogue", provider, model=SYNTHETIC_MODEL, model_version=SYNTHETIC_VERSION)
    calls_before = len(provider.requests)
    session = ChatSession(root / "dialogue", session_id="dialogue", model=SYNTHETIC_MODEL,
                          model_version=SYNTHETIC_VERSION)
    try:
        session.turn("turn-1", "حمولة مختلفة للمفتاح نفسه", provider)
        conflict = False
    except ConversationError as exc:
        conflict = exc.code == "turn_conflict"
    checks["conflicting_turn_rejected_without_call"] = conflict and len(provider.requests) == calls_before

    isolated = CapturingProvider(SyntheticProvider([_answer("لا أعرف الموعد في هذه الجلسة.")]))
    ChatSession(root / "dialogue", session_id="separate", model=SYNTHETIC_MODEL,
                model_version=SYNTHETIC_VERSION).turn("turn-1", "ما الموعد؟", isolated)
    messages = isolated.requests[0].messages
    checks["separate_session_has_no_prior_dialogue"] = (
        len(messages) == 2 and messages[0].role == "system" and
        messages[1].role == "user" and messages[1].content == "ما الموعد؟")

    overflow_provider = CapturingProvider(SyntheticProvider([]))
    overflow_session = ChatSession(root / "overflow", session_id="overflow", model=SYNTHETIC_MODEL,
                                   model_version=SYNTHETIC_VERSION, max_context_chars=24000)
    try:
        overflow_session.turn("too-long", "ا" * 24001, overflow_provider)
        overflow = False
    except ConversationError as exc:
        overflow = exc.code == "context_limit"
    checks["context_overflow_rejected_without_call"] = (
        overflow and not overflow_provider.requests and overflow_provider.estimates == 0)

    for label, response, expected_status in (
        ("error", ProviderError("synthetic_failure", "synthetic only", retryable=False), "error"),
        ("truncated", _answer("جزء مبتور غير موثوق", "max_output"), "truncated"),
    ):
        p = CapturingProvider(SyntheticProvider([response, _answer("جواب مستقل مكتمل")]))
        s = ChatSession(root / label, session_id=label, model=SYNTHETIC_MODEL,
                        model_version=SYNTHETIC_VERSION)
        failed = s.turn("failed", "طلب لن يكتمل", p)
        s.turn("next", "طلب مستقل تالٍ", p)
        later = p.requests[1].messages
        checks[f"{label}_honest_and_excluded_from_context"] = (
            failed["status"] == expected_status and
            len(later) == 2 and later[0].role == "system" and
            later[1].role == "user" and later[1].content == "طلب مستقل تالٍ" and
            s.history()[0]["status"] == expected_status)
    return _public_report(checks, scope="synthetic_mechanism_only", initial_calls=initial_calls,
                          replay_calls=len(provider.requests) - initial_calls)


def _new_private_run(root, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
        raise AcceptanceError("run_id_invalid")
    root = Path(root).expanduser().absolute()
    for part in (root, *root.parents):
        if part.is_symlink():
            raise AcceptanceError("root_symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = root / run_id
    try:
        target.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise AcceptanceError("run_already_exists") from exc
    if stat.S_IMODE(target.stat().st_mode) != 0o700:
        raise AcceptanceError("run_permissions")
    return target


def _write_private(path, payload):
    wire = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(wire)
        handle.flush()
        os.fsync(handle.fileno())


def run_live(root, *, model, model_version, run_id, provider_factory=None):
    """عينة تطوير معلنة؛ الأجوبة والهوية تبقيان في التقرير المحلي الخاص."""
    if not isinstance(model, str) or not model.strip():
        raise AcceptanceError("model_required")
    if not isinstance(model_version, str) or not re.fullmatch(r"[0-9a-f]{64}", model_version):
        raise AcceptanceError("model_version_invalid")
    run = _new_private_run(root, run_id)
    if provider_factory is None:
        from providers.local_chat import LocalChatProvider
        provider_factory = LocalChatProvider
    delegate = provider_factory(model, model_version)
    provider = CapturingProvider(delegate)
    checks, results, replays, initial_calls, network = _dialogue(
        run / "sessions", provider, model=model, model_version=model_version)
    public = _public_report(checks, scope="live_development_dialogue_mechanism",
                            initial_calls=initial_calls, replay_calls=len(provider.requests) - initial_calls)
    public["run_id"] = run_id
    public.update(network)
    private = {"summary": public, "runtime_model": model, "model_version": model_version,
               "prompts": list(PROMPTS), "results": results, "replays": replays,
               "human_review": "not_required_q49", "automated_review": "pending",
               "quality_pending": True, "release_ready": False}
    _write_private(run / "report.json", private)
    return public


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--model-version")
    parser.add_argument("--run-id")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)
    if not args.live and any(value is not None for value in (args.model, args.model_version, args.run_id, args.root)):
        parser.error("خيارات النموذج والجذر والهوية تتطلب --live")
    if args.live and not all((args.model, args.model_version, args.run_id)):
        parser.error("--live يتطلب --model و--model-version و--run-id")
    try:
        if args.live:
            report = run_live(args.root or ROOT / "var/conversation-acceptance", model=args.model,
                              model_version=args.model_version, run_id=args.run_id)
        else:
            with tempfile.TemporaryDirectory(prefix="diwan-m9-") as temp:
                report = run_synthetic(Path(temp).resolve())
    except (AcceptanceError, ConversationError, ProviderError, OSError) as exc:
        print(json.dumps({"error_code": getattr(exc, "code", "filesystem_error"),
                          "quality_pending": True, "release_ready": False}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
