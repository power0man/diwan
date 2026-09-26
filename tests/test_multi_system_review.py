"""Automatic-review evidence must fail closed; all model/transport data is synthetic."""
from copy import deepcopy
import json
from urllib.error import URLError

import pytest

from evaluation.multi_system_review import (
    AutomaticReviewError, build_review_package, canonical_bytes, deterministic_checks,
    parse_json, pointer_value, review_binding, validate_review_package,
)
from tools import review_automatically as cli


@pytest.fixture
def engine():
    """المحرّكُ المُراجَع من عائلة Qwen بنصّ AGENTS.md §٤ (qwen3:14b في هذا المثبِّت؛ والافتراضيُّ اليوم qwen3.5:9b، ق٥٤)."""
    return {"provider": "ollama", "model": "qwen3:14b",
            "digest": "c" * 64, "family": "qwen3"}


@pytest.fixture
def inputs():
    artifact = {"run_id": "run-example-1", "answer": "المجموع ٤٢", "checks": {"total": 42}}
    rubric = {
        "schema_version": 1, "rubric_id": "arithmetic-v1",
        "criteria": [{"id": "answer", "instruction": "Does the answer state the computed total?"}],
        "deterministic_rules": [{"id": "total", "pointer": "/checks/total",
                                 "operator": "equals", "expected": 42}],
    }
    binding = review_binding(artifact, rubric)
    reviews = []
    for model, family, char in (("gemma3:12b", "gemma3", "a"), ("llama3.1:8b", "llama3", "b")):
        response = {"binding": binding, "judgments": [{
            "criterion_id": "answer", "verdict": "pass", "reason": "The answer contains the correct total.",
            "evidence": ["/answer"],
        }]}
        reviews.append({
            "identity": {"provider": "ollama", "model": model, "digest": char * 64, "family": family},
            "binding": binding, "settings": {"temperature": 0, "seed": 0, "num_ctx": 32768, "num_predict": 4096},
            "started_at": "2026-09-23T10:00:00+00:00", "elapsed_ms": 20,
            "raw_output": json.dumps(response), "response": response, "error": None,
        })
    return artifact, rubric, reviews


def change_verdict(record, verdict):
    record["response"]["judgments"][0]["verdict"] = verdict
    record["raw_output"] = json.dumps(record["response"])


def test_acceptance_is_automatic_and_is_not_product_readiness(inputs, engine):
    artifact, rubric, reviews = inputs
    package = build_review_package(artifact, rubric, reviews, engine=engine)
    assert package["status"] == "accepted"
    assert package["human_review"] is False
    assert package["product_readiness"] == "not_assessed"
    assert package["file_integrity"] == "content_bound"
    assert validate_review_package(package, artifact, rubric) == package


@pytest.mark.parametrize("votes,expected", [
    (("pass", "fail"), "inconclusive"),
    (("pass", "inconclusive"), "inconclusive"),
    (("inconclusive", "inconclusive"), "inconclusive"),
    (("fail", "fail"), "rejected"),
])
def test_disagreement_and_inconclusive_cannot_be_certified(inputs, engine, votes, expected):
    artifact, rubric, reviews = inputs
    for record, verdict in zip(reviews, votes):
        change_verdict(record, verdict)
    package = build_review_package(artifact, rubric, reviews, engine=engine)
    assert package["status"] == expected
    assert bool(package["disagreements"]) == (len(set(votes)) > 1)


def test_models_cannot_override_failing_deterministic_rule(inputs, engine):
    artifact, rubric, reviews = inputs
    artifact["checks"]["total"] = 41
    binding = review_binding(artifact, rubric)
    for record in reviews:
        record["binding"] = binding
        record["response"]["binding"] = binding
        record["raw_output"] = json.dumps(record["response"])
    package = build_review_package(artifact, rubric, reviews, engine=engine)
    assert package["status"] == "rejected"
    assert package["deterministic_checks"][0]["status"] == "fail"


@pytest.mark.parametrize("update,code", [
    ({"model": "gemma3:12b", "family": "gemma3"}, "duplicate_review_model"),
    ({"digest": "a" * 64}, "duplicate_review_model"),
    ({"model": "gemma2:9b", "family": "gemma2"}, "duplicate_review_family"),
    # العائلةُ بالمورِّد (AGENTS §٤): Gemini وGemma عائلةٌ واحدة، فلا يُحسبان نظامين
    ({"model": "gemini-2.5-flash", "family": "gemini"}, "duplicate_review_family"),
    ({"digest": "arbitrary-label"}, "model_digest_invalid"),
    # والمحرّكُ نفسُه ليس مراجعًا، لا باسمه ولا ببصمته ولا بعائلته (ك٣).
    ({"model": "qwen3:8b", "family": "qwen3"}, "reviewer_shares_engine_family"),
    ({"model": "qwen2.5:7b", "family": "qwen2"}, "reviewer_shares_engine_family"),
    ({"model": "qwq:32b", "family": "qwen2"}, "reviewer_shares_engine_family"),
    ({"model": "qwen3:14b", "family": "qwen3"}, "reviewer_shares_engine_family"),
    ({"digest": "c" * 64}, "reviewer_is_the_engine"),
    # العائلةُ تُشتقّ من الاسم ولا يُكتفى بما يُعلَن: نموذجُ Qwen يعلن عائلةً أخرى فيُردّ
    ({"model": "qwen3:8b"}, "review_family_mismatch"),
    ({"family": "qwen3"}, "review_family_mismatch"),
    ({"model": "mystery:7b", "family": "mystery"}, "review_family_unknown"),
])
def test_two_agents_or_aliases_do_not_make_two_systems(inputs, engine, update, code):
    artifact, rubric, reviews = inputs
    reviews[1]["identity"].update(update)
    with pytest.raises(AutomaticReviewError, match=code):
        build_review_package(artifact, rubric, reviews, engine=engine)


def test_one_reviewer_is_insufficient(inputs, engine):
    artifact, rubric, reviews = inputs
    with pytest.raises(AutomaticReviewError, match="insufficient_review_systems"):
        build_review_package(artifact, rubric, reviews[:1], engine=engine)


@pytest.mark.parametrize("change", ["artifact", "rubric", "run"])
def test_review_cannot_be_reused_on_another_artifact_rubric_or_run(inputs, engine, change):
    artifact, rubric, reviews = inputs
    package = build_review_package(artifact, rubric, reviews, engine=engine)
    if change == "artifact":
        artifact["answer"] = "جواب جديد"
    elif change == "rubric":
        rubric["criteria"][0]["instruction"] = "A different question."
    else:
        artifact["run_id"] = "run-example-2"
    with pytest.raises(AutomaticReviewError, match="review_binding_mismatch"):
        validate_review_package(package, artifact, rubric)


def test_explicit_run_id_is_checked(inputs, engine):
    artifact, rubric, _ = inputs
    with pytest.raises(AutomaticReviewError, match="run_mismatch"):
        review_binding(artifact, rubric, run_id="different")


@pytest.mark.parametrize("field,value", [
    ("status", "rejected"), ("human_review", True), ("product_readiness", "ready"),
    ("deterministic_checks", []), ("invented_field", True),
])
def test_stored_summary_flags_are_recomputed(inputs, engine, field, value):
    artifact, rubric, reviews = inputs
    package = build_review_package(artifact, rubric, reviews, engine=engine)
    package[field] = value
    with pytest.raises(AutomaticReviewError, match="review_package_mismatch"):
        validate_review_package(package, artifact, rubric)


def test_raw_output_is_preserved_and_cannot_differ_from_parsed_verdict(inputs, engine):
    artifact, rubric, reviews = inputs
    reviews[0]["raw_output"] = '{"binding":{},"judgments":[]}'
    with pytest.raises(AutomaticReviewError, match="raw_response_mismatch"):
        build_review_package(artifact, rubric, reviews, engine=engine)


@pytest.mark.parametrize("change,code", [
    (lambda r: r["judgments"].clear(), "judgment_coverage"),
    (lambda r: r["judgments"][0].update(reason=""), "text_invalid"),
    (lambda r: r["judgments"][0].update(evidence=[]), "judgment_evidence"),
    (lambda r: r["judgments"][0].update(evidence=["/not-present"]), "pointer_missing"),
    (lambda r: r.update(human_review=True), "schema_fields"),
])
def test_reviewer_must_cover_rubric_and_give_evidence(inputs, engine, change, code):
    artifact, rubric, reviews = inputs
    change(reviews[0]["response"])
    reviews[0]["raw_output"] = json.dumps(reviews[0]["response"])
    with pytest.raises(AutomaticReviewError, match=code):
        build_review_package(artifact, rubric, reviews, engine=engine)


def test_error_record_is_inconclusive_even_if_other_model_passes(inputs, engine):
    artifact, rubric, reviews = inputs
    reviews[0].update(error="transport_timeout", response=None, raw_output="")
    package = build_review_package(artifact, rubric, reviews, engine=engine)
    assert package["status"] == "inconclusive"
    assert package["reviews"][0]["error"] == "transport_timeout"


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"x":NaN}', '{oops', b'\xff'])
def test_invalid_json_and_duplicate_keys_fail_closed(raw):
    with pytest.raises(AutomaticReviewError):
        parse_json(raw)


def test_rules_are_type_sensitive_and_missing_paths_fail(inputs, engine):
    artifact, rubric, _ = inputs
    artifact["checks"]["total"] = True
    rubric["deterministic_rules"][0]["expected"] = 1
    assert deterministic_checks(artifact, rubric)[0]["status"] == "fail"
    rubric["deterministic_rules"][0]["pointer"] = "/missing"
    assert deterministic_checks(artifact, rubric)[0]["reason"] == "pointer_missing"


def test_json_pointer_escaping_and_no_negative_indices():
    assert pointer_value({"a/b": {"~": [42]}}, "/a~1b/~0/0") == 42
    with pytest.raises(AutomaticReviewError):
        pointer_value([42], "/-1")


def test_empty_deterministic_rule_set_cannot_be_accepted(inputs, engine):
    artifact, rubric, _ = inputs
    rubric["deterministic_rules"] = []
    with pytest.raises(AutomaticReviewError, match="rubric_entries_invalid"):
        review_binding(artifact, rubric)


@pytest.mark.parametrize("timestamp", ["yesterday", "2026-09-23", "2026-09-23T10:00:00"])
def test_reviewer_time_requires_explicit_timezone(inputs, engine, timestamp):
    artifact, rubric, reviews = inputs
    reviews[0]["started_at"] = timestamp
    with pytest.raises(AutomaticReviewError, match="timestamp_invalid"):
        build_review_package(artifact, rubric, reviews, engine=engine)


def test_a_forged_deterministic_pass_cannot_rescue_a_rejected_package(inputs, engine):
    artifact, rubric, reviews = inputs
    rubric["deterministic_rules"][0]["expected"] = 999
    binding = review_binding(artifact, rubric)
    for record in reviews:
        record["binding"] = binding
        record["response"]["binding"] = binding
        record["raw_output"] = json.dumps(record["response"])
    package = build_review_package(artifact, rubric, reviews, engine=engine)
    assert package["status"] == "rejected"
    package["status"] = "accepted"
    package["deterministic_checks"][0].update(status="pass", reason="rule_satisfied")
    with pytest.raises(AutomaticReviewError, match="review_package_mismatch"):
        validate_review_package(package, artifact, rubric)


class Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeOpener:
    def __init__(self, queued):
        self.queued = queued
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.queued.pop(0)
        if isinstance(response, Exception):
            raise response
        return Response(response if isinstance(response, bytes) else canonical_bytes(response))


ENGINE_TAG = {"name": "qwen3:14b", "digest": "c" * 64, "size": 123456,
              "details": {"format": "gguf", "family": "qwen3"}}


def tags(reviews):
    """سردُ النماذج يحمل المحرّكَ أيضًا: هويتُه تُسأل للخادم لا تُعلَن نصًّا."""
    return {"models": [{"name": r["identity"]["model"], "digest": r["identity"]["digest"],
                        "size": 123456, "details": {"format": "gguf", "family": r["identity"]["family"]}}
                       for r in reviews] + [ENGINE_TAG]}


def show(record):
    family = record["identity"]["family"]
    return {"details": {"format": "gguf", "family": family}, "capabilities": ["completion"],
            "model_info": {"general.architecture": family, f"{family}.context_length": 32768},
            "template": "{{ .Messages }}"}


def envelope(record):
    return {"model": record["identity"]["model"], "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": record["raw_output"]},
            "prompt_eval_count": 100, "eval_count": 80}


def fake_transport(monkeypatch, queued):
    opener = FakeOpener(queued)
    handlers = []

    def build(*provided):
        handlers.extend(provided)
        return opener

    monkeypatch.setattr(cli.urllib.request, "build_opener", build)
    return opener, handlers


def test_local_cli_records_actual_tags_raw_outputs_settings_and_times(inputs, engine, tmp_path, monkeypatch):
    artifact, rubric, reviews = inputs
    metadata = tags(reviews)
    opener, handlers = fake_transport(monkeypatch, [metadata, show(reviews[0]), show(reviews[1]), envelope(reviews[0]), metadata,
                                                   envelope(reviews[1]), metadata])
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:8999")
    out = tmp_path / "review"
    package = cli.run_reviews(artifact, rubric, [r["identity"]["model"] for r in reviews], out, engine_model="qwen3:14b")
    assert package["status"] == "accepted"
    assert handlers[0].proxies == {}
    assert isinstance(handlers[1], cli._NoRedirect)
    assert len(opener.requests) == 7
    assert (out / "call-004.response.bin").read_bytes() == canonical_bytes(envelope(reviews[0]))
    assert json.loads((out / "identities.json").read_text()) == [r["identity"] for r in reviews]
    assert json.loads((out / "configuration.json").read_text())["human_review"] is False
    timing = json.loads((out / "call-004.timing.json").read_text())
    assert timing["elapsed_ms"] >= 0 and timing["finished_at"]
    assert out.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in out.iterdir())
    chat_payloads = [json.loads(r.data) for r in opener.requests if r.full_url.endswith("/api/chat")]
    assert all("tools" not in p and "pull" not in r.full_url
               for p, r in zip(chat_payloads, [r for r in opener.requests if r.data]))
    # No reviewer's answer is fed to another reviewer.
    assert chat_payloads[0]["messages"] == chat_payloads[1]["messages"]
    assert all(p["truncate"] is False and p["shift"] is False for p in chat_payloads)


@pytest.mark.parametrize("bad_chat,code", [
    (URLError("offline"), "transport_error"),
    (TimeoutError(), "transport_timeout"),
    (b"not JSON", "invalid_json"),
    ({"done": False}, "chat_envelope_invalid"),
])
def test_transport_and_json_errors_preserve_evidence_and_block_acceptance(
        inputs, tmp_path, monkeypatch, bad_chat, code):
    artifact, rubric, reviews = inputs
    metadata = tags(reviews)
    fake_transport(monkeypatch, [metadata, show(reviews[0]), show(reviews[1]), bad_chat, envelope(reviews[1]), metadata])
    out = tmp_path / "failed"
    package = cli.run_reviews(artifact, rubric, [r["identity"]["model"] for r in reviews], out, engine_model="qwen3:14b")
    assert package["status"] == "inconclusive"
    assert package["reviews"][0]["error"] == code
    assert (out / "call-004.response.bin").exists()
    assert (out / "review.json").exists()


def test_metadata_change_after_generation_blocks_acceptance(inputs, engine, tmp_path, monkeypatch):
    artifact, rubric, reviews = inputs
    metadata = tags(reviews)
    changed = deepcopy(metadata)
    changed["models"][0]["digest"] = "c" * 64
    fake_transport(monkeypatch, [metadata, show(reviews[0]), show(reviews[1]), envelope(reviews[0]), changed, envelope(reviews[1]), metadata])
    package = cli.run_reviews(artifact, rubric, [r["identity"]["model"] for r in reviews], tmp_path / "changed", engine_model="qwen3:14b")
    assert package["status"] == "inconclusive"
    assert package["reviews"][0]["error"] == "model_identity_changed"


def test_preflight_family_check_prevents_any_model_execution(inputs, engine, tmp_path, monkeypatch):
    artifact, rubric, reviews = inputs
    metadata = tags(reviews)
    # الخادمُ يعلن للمراجع الثاني (llama3.1:8b) عائلةَ gemma3: المعلَنةُ تخالف ما يُشتقّ من الاسم،
    # فيُردّ قبل أيّ تنفيذ. ولا يُستعمل qwen مثالًا لأنه عائلةُ المحرّك، فيردّه حارسٌ آخر ويضيع المقصود.
    metadata["models"][1]["details"]["family"] = "gemma3"
    opener, _ = fake_transport(monkeypatch, [metadata])
    output = tmp_path / "same-family"
    with pytest.raises(AutomaticReviewError, match="review_family_mismatch"):
        cli.run_reviews(artifact, rubric, [r["identity"]["model"] for r in reviews], output, engine_model="qwen3:14b")
    assert len(opener.requests) == 1
    assert json.loads((output / "failure.json").read_text())["status"] == "inconclusive"


@pytest.mark.parametrize("url", ["https://127.0.0.1:11434", "http://example.com:11434",
                                  "http://user:password@localhost:11434", "http://localhost:11434/path"])
def test_remote_or_credentialed_endpoints_are_forbidden(url):
    with pytest.raises(AutomaticReviewError, match="local_endpoint_required"):
        cli.validate_base_url(url)


def test_redirect_handler_refuses_network_redirection():
    with pytest.raises(AutomaticReviewError, match="redirect_refused"):
        cli._NoRedirect().redirect_request(None, None, 302, "", {}, "http://remote.invalid")


def test_cli_never_overwrites_previous_evidence(inputs, engine, tmp_path):
    artifact, rubric, reviews = inputs
    out = tmp_path / "existing"
    out.mkdir()
    marker = out / "review.json"
    marker.write_text("prior evidence")
    with pytest.raises(FileExistsError):
        cli.run_reviews(artifact, rubric, [r["identity"]["model"] for r in reviews], out, engine_model="qwen3:14b")
    assert marker.read_text() == "prior evidence"


@pytest.mark.parametrize("update,code", [
    ({"size": 0}, "local_model_size_invalid"),
    ({"size": None}, "local_model_size_invalid"),
    ({"size": True}, "local_model_size_invalid"),
    ({"remote_model": "upstream"}, "remote_model_refused"),
    ({"remote_host": ""}, "remote_model_refused"),
    ({"cloud": False}, "remote_model_refused"),
    ({"details": {"format": "gguf", "family": "qwen3", "Remote-Host": None}}, "remote_model_refused"),
])
def test_local_endpoint_does_not_allow_cloud_artifact(inputs, engine, tmp_path, monkeypatch, update, code):
    artifact, rubric, reviews = inputs
    metadata = tags(reviews)
    metadata["models"][0].update(update)
    opener, _ = fake_transport(monkeypatch, [metadata])
    output = tmp_path / "cloud-blocked"
    with pytest.raises(AutomaticReviewError, match=code):
        cli.run_reviews(artifact, rubric, [r["identity"]["model"] for r in reviews], output, engine_model="qwen3:14b")
    assert len(opener.requests) == 1
    assert all(not req.full_url.endswith("/api/chat") for req in opener.requests)


def test_cloud_name_is_rejected_before_transport(inputs, engine, tmp_path, monkeypatch):
    artifact, rubric, _ = inputs
    opener, _ = fake_transport(monkeypatch, [])
    with pytest.raises(AutomaticReviewError, match="remote_model_refused"):
        cli.run_reviews(artifact, rubric, ["qwen:cloud", "llama:8b"], tmp_path / "cloud-name", engine_model="qwen3:14b")
    assert not opener.requests


@pytest.mark.parametrize("update,code", [
    ({"remote_model": None}, "remote_model_refused"),
    ({"capabilities": ["completion", {"cloud": True}]}, "remote_model_refused"),
    ({"model_info": {"general.architecture": "qwen3", "qwen3.context_length": 4096}}, "model_context_unsupported"),
    ({"template": "x" * 40000}, "review_context_budget_exceeded"),
])
def test_show_is_checked_before_any_chat(inputs, engine, tmp_path, monkeypatch, update, code):
    artifact, rubric, reviews = inputs
    metadata = show(reviews[0])
    metadata.update(update)
    opener, _ = fake_transport(monkeypatch, [tags(reviews), metadata])
    with pytest.raises(AutomaticReviewError, match=code):
        cli.run_reviews(artifact, rubric, [r["identity"]["model"] for r in reviews], tmp_path / "show-blocked", engine_model="qwen3:14b")
    assert all(not req.full_url.endswith("/api/chat") for req in opener.requests)


def test_large_artifact_is_refused_before_any_network_and_not_silently_truncated(inputs, engine, tmp_path, monkeypatch):
    artifact, rubric, reviews = inputs
    artifact["answer"] = "ع" * 5000
    opener, _ = fake_transport(monkeypatch, [])
    output = tmp_path / "large"
    with pytest.raises(AutomaticReviewError, match="review_input_too_large"):
        cli.run_reviews(artifact, rubric, [r["identity"]["model"] for r in reviews], output, engine_model="qwen3:14b")
    assert not opener.requests
    assert json.loads((output / "artifact.json").read_text())["answer"] == artifact["answer"]
    assert json.loads((output / "failure.json").read_text())["error"] == "review_input_too_large"


@pytest.mark.parametrize("update,code", [
    ({"remote_host": "cloud.invalid"}, "remote_model_refused"),
    ({"prompt_eval_count": 32768}, "chat_context_counts_invalid"),
    ({"eval_count": 8192}, "chat_context_counts_invalid"),
    ({"prompt_eval_count": None}, "chat_context_counts_invalid"),
    ({"done_reason": "length"}, "chat_envelope_invalid"),
])
def test_context_overflow_or_remote_reply_never_passes(inputs, engine, tmp_path, monkeypatch, update, code):
    artifact, rubric, reviews = inputs
    bad = envelope(reviews[0])
    bad.update(update)
    metadata = tags(reviews)
    fake_transport(monkeypatch, [metadata, show(reviews[0]), show(reviews[1]), bad, envelope(reviews[1]), metadata])
    package = cli.run_reviews(artifact, rubric, [r["identity"]["model"] for r in reviews], tmp_path / "bad-context", engine_model="qwen3:14b")
    assert package["status"] == "inconclusive"
    assert package["reviews"][0]["error"] == code


def test_response_grammar_constrains_shape_not_opinion(inputs, engine):
    artifact, rubric, _ = inputs
    schema = cli.response_schema(review_binding(artifact, rubric), rubric, artifact)
    item = schema['properties']['judgments']['items']['properties']
    assert set(item['verdict']['enum']) == {'pass', 'fail', 'inconclusive'}
    assert '/answer' in item['evidence']['items']['enum']
    assert '/answer/invented' not in item['evidence']['items']['enum']
    assert schema['properties']['judgments']['type'] == 'array'


def test_recorded_live_policy_reviews_are_refused_under_the_engine_family_rule():
    """الشاهدُ المسجَّل في ٢٣ سبتمبر يُردّ اليوم — وهو دليلُ وجوب هذه القاعدة.

    تلك المحاولاتُ الثلاث راجعها `qwen3:14b`، وهو المحرّكُ نفسُه بنصّ
    AGENTS.md §٤. فحُكم عائلةٍ على عائلتها. والملفُّ يبقى كما هو: الشاهدُ
    لا يُحذف ولا يُزوَّر (القاعدة ٨)، وإنما يُسمّى ما فيه باسمه.

    وحالاتُها وقتَ تسجيلها كانت ['inconclusive', 'accepted', 'accepted']،
    وهي حالاتٌ تحت العقد القديم لا شهادةُ صلاحٍ اليوم.
    """
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'docs/probe/automatic-review-policy-20260923.json'
    evidence = parse_json(path.read_bytes())
    engine = {"provider": "ollama", "model": "qwen3:14b", "family": "qwen3",
              "digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8"}
    for attempt in evidence['attempts']:
        # لا محرّكَ في الحزمة المسجَّلة: الغائبُ يُرفض ولا يُفترض.
        with pytest.raises(AutomaticReviewError, match='engine_missing'):
            validate_review_package(attempt['review'], attempt['artifact'], attempt['rubric'])
        # وحتى لو أُعلن محرّكُها الحقيقي، فمراجعُها من عائلته.
        with pytest.raises(AutomaticReviewError, match='reviewer_shares_engine_family'):
            build_review_package(attempt['artifact'], attempt['rubric'],
                                 attempt['review']['reviews'], engine=engine)
        assert any(r['identity']['family'] == 'qwen3'
                   for r in attempt['review']['reviews'])
    assert evidence['product_readiness'] == 'not_assessed'


# ————— ك٣: المراجعُ ليس من عائلة المحرّك المُراجَع —————

def test_a_reviewer_from_the_engine_family_is_refused_by_name(inputs, engine):
    """المحكِّمُ من عائلة المحكوم عليه يرفع درجاتِ عائلته (AGENTS.md §٤).

    وقد وقع هذا فعلًا: `docs/probe/automatic-review-policy-20260923.json`
    سجّل ثلاثَ مراجعاتٍ حكَم فيها `qwen3:14b` على مخرجاتٍ محرّكُها `qwen3:14b`.
    """
    artifact, rubric, reviews = inputs
    reviews[1]["identity"].update(model="qwen3.5:9b", family="qwen3.5")
    with pytest.raises(AutomaticReviewError, match="reviewer_shares_engine_family"):
        build_review_package(artifact, rubric, reviews, engine=engine)


def test_the_package_cannot_be_built_without_declaring_the_engine(inputs):
    """لا سبيلَ إلى تعطيل الحارس بترك المحرّك مجهولًا.

    لو كان المحرّك وسيطًا اختياريًّا لصار إغفالُه هو البابَ الخلفيّ. فهو
    كلمةٌ مفتاحيةٌ إلزامية، وغيابُه خطأُ استدعاءٍ لا مرورٌ صامت.
    """
    artifact, rubric, reviews = inputs
    with pytest.raises(TypeError, match="engine"):
        build_review_package(artifact, rubric, reviews)


@pytest.mark.parametrize("engine_update,code", [
    ({"family": "  "}, "text_invalid"),
    ({"digest": "not-a-digest"}, "engine_digest_invalid"),
    ({"provider": ""}, "text_invalid"),
])
def test_an_unusable_engine_identity_is_refused_not_ignored(inputs, engine, engine_update, code):
    """محرّكٌ مجهولُ الهوية لا يُقرأ «لا قيد» — يُرَدّ برمزٍ مسمّى."""
    artifact, rubric, reviews = inputs
    engine.update(engine_update)
    with pytest.raises(AutomaticReviewError, match=code):
        build_review_package(artifact, rubric, reviews, engine=engine)


def test_a_stored_package_is_rechecked_against_its_own_engine(inputs, engine):
    """الحزمةُ المخزَّنة تحمل محرّكَها، فيُعاد فرضُ القاعدة عند كل تحقّق.

    ولو بُدّل المحرّكُ المخزَّن بعد البناء، سقط إعادةُ الاشتقاق: الحزمةُ
    لا تُصدَّق على قولها.
    """
    artifact, rubric, reviews = inputs
    package = build_review_package(artifact, rubric, reviews, engine=engine)
    assert package["engine"] == engine
    assert "engine_identity_is_declared_not_verified" in package["limitations"]
    assert validate_review_package(package, artifact, rubric) == package

    swapped = deepcopy(package)
    swapped["engine"] = {**engine, "family": "gemma3", "model": "gemma3:27b"}
    with pytest.raises(AutomaticReviewError, match="reviewer_shares_engine_family"):
        validate_review_package(swapped, artifact, rubric)


def test_a_caller_that_knows_the_engine_pins_it_and_a_mismatch_is_refused(inputs, engine):
    artifact, rubric, reviews = inputs
    package = build_review_package(artifact, rubric, reviews, engine=engine)
    assert validate_review_package(package, artifact, rubric, engine=engine) == package
    with pytest.raises(AutomaticReviewError, match="engine_mismatch"):
        validate_review_package(package, artifact, rubric,
                                engine={**engine, "digest": "d" * 64})


def test_a_package_without_an_engine_field_is_refused(inputs, engine):
    artifact, rubric, reviews = inputs
    package = build_review_package(artifact, rubric, reviews, engine=engine)
    stripped = {k: v for k, v in package.items() if k != "engine"}
    with pytest.raises(AutomaticReviewError, match="engine_missing"):
        validate_review_package(stripped, artifact, rubric)
