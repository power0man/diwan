"""Synthetic acceptance-store tests; no evaluation bank or live provider is read.

The manifest is an application boundary. These tests do not claim protection
against an owner who deliberately rewrites the entire store and its identity.
"""
from dataclasses import replace
import fcntl
import hashlib
import json

import pytest

from evaluation import acceptance_store
from evaluation.acceptance_store import AcceptanceStore
from evaluation.disclosure import Case, DisclosureRefused


@pytest.fixture
def cases():
    return tuple(
        Case(f"synthetic-{i}", f"Synthetic task {i}",
             f"PRIVATE-REFERENCE-{i}", f"PRIVATE-RUBRIC-{i}")
        for i in range(6)
    )


@pytest.fixture
def make_store(tmp_path, cases):
    directory = tmp_path.resolve() / "acceptance"

    def make(**changes):
        settings = dict(suite_id="synthetic-suite", candidate_sha256="a" * 64,
                        max_attempts=2, min_aggregate=5)
        settings.update(changes)
        current_cases = settings.pop("cases", cases)
        current_directory = settings.pop("directory", directory)
        return AcceptanceStore(current_directory, current_cases, **settings)

    return make


def assert_refused(code, operation):
    with pytest.raises(DisclosureRefused) as caught:
        operation()
    assert caught.value.code == code


def score_all(store, cases, token="attempt-1"):
    for case in cases:
        store.record(token, case.case_id, True)


def test_restart_resumes_open_attempt_and_preserves_its_scores(make_store, cases):
    first = make_store()
    assert first.begin("attempt-1") == 1
    first.record("attempt-1", cases[0].case_id, False)

    restarted = make_store()
    assert restarted.status()["attempts_used"] == 1
    assert restarted.status()["attempt_open"] is True
    assert restarted.begin("attempt-1") == 1
    assert_refused("attempt_already_open", lambda: restarted.begin("attempt-2"))
    for case in cases[1:]:
        restarted.record("attempt-1", case.case_id, True)
    assert restarted.aggregate("attempt-1")["passed"] == 5


def test_max_attempts_cannot_be_reset_by_restart(make_store, cases):
    for number in (1, 2):
        store = make_store()
        token = f"attempt-{number}"
        assert store.begin(token) == number
        score_all(store, cases, token)
        store.aggregate(token)
    restarted = make_store()
    assert restarted.status()["attempts_used"] == 2
    assert_refused("attempts_exhausted", lambda: restarted.begin("attempt-3"))
    assert_refused("attempt_closed", lambda: restarted.begin("attempt-1"))


@pytest.mark.parametrize("limit", [False, True, -1, 0, 11, 1.5, "2"])
def test_invalid_attempt_caps_are_rejected(make_store, limit):
    assert_refused("max_attempts", lambda: make_store(max_attempts=limit))


@pytest.mark.parametrize("minimum", [False, True, -1, 0, 1, 4, 4.5, "5"])
def test_aggregate_floor_cannot_be_lowered_below_five(make_store, minimum):
    assert_refused("min_aggregate", lambda: make_store(min_aggregate=minimum))


def test_five_is_valid_but_a_smaller_bank_is_not(make_store, cases):
    store = make_store(cases=cases[:5])
    store.begin("attempt-1")
    score_all(store, cases[:5])
    assert store.aggregate("attempt-1")["n"] == 5
    assert_refused("suite_size", lambda: make_store(cases=cases[:4]))


def test_revealing_cases_below_floor_prevents_a_new_attempt(make_store, cases):
    store = make_store()
    for case in cases[:2]:
        store.reveal(case.case_id, actor="test-reviewer", reason="development")
    restarted = make_store()
    assert_refused("holdout_too_small", lambda: restarted.begin("attempt-1"))
    assert restarted.status()["attempts_used"] == 0


@pytest.mark.parametrize("score", [0, 1, "true", "false", None, [], {}])
def test_scores_require_explicit_booleans_without_mutating_state(make_store, cases, score):
    store = make_store()
    store.begin("attempt-1")
    state_path = store.directory / "state.json"
    before = state_path.read_bytes()
    assert_refused("score_type", lambda: store.record("attempt-1", cases[0].case_id, score))
    assert state_path.read_bytes() == before


def test_conflicting_scores_are_refused_after_restart(make_store, cases):
    store = make_store()
    store.begin("attempt-1")
    store.record("attempt-1", cases[0].case_id, False)
    restarted = make_store()
    restarted.record("attempt-1", cases[0].case_id, False)
    assert_refused("score_conflict",
                   lambda: restarted.record("attempt-1", cases[0].case_id, True))
    for case in cases[1:]:
        restarted.record("attempt-1", case.case_id, True)
    assert restarted.aggregate("attempt-1")["passed"] == 5


def test_aggregate_cannot_select_five_successes_and_omit_a_sixth_case(make_store, cases):
    store = make_store()
    store.begin("attempt-1")
    score_all(store, cases[:5])
    assert_refused("scores_incomplete", lambda: store.aggregate("attempt-1"))
    assert make_store().status()["attempt_open"] is True
    store.record("attempt-1", cases[5].case_id, False)
    result = store.aggregate("attempt-1")
    assert (result["n"], result["passed"]) == (6, 5)


def test_aggregate_replay_is_stable_across_restart_reveal_and_later_attempt(make_store, cases):
    store = make_store()
    store.begin("attempt-1")
    score_all(store, cases)
    original = store.aggregate("attempt-1")
    expected = dict(original)
    original["passed"] = -100
    assert store.aggregate("attempt-1") == expected
    restarted = make_store()
    restarted.reveal(cases[0].case_id, actor="reviewer", reason="inspect solution")
    restarted.begin("attempt-2")
    score_all(restarted, cases[1:], "attempt-2")
    assert restarted.aggregate("attempt-2")["n"] == 5
    assert restarted.aggregate("attempt-1") == expected
    assert expected["attempts_used"] == 1
    assert_refused("attempt_closed",
                   lambda: restarted.record("attempt-1", cases[1].case_id, True))


def test_reveal_is_sticky_and_original_provenance_is_preserved(make_store, cases):
    store = make_store()
    store.begin("attempt-1")
    store.record("attempt-1", cases[0].case_id, True)
    revealed = store.reveal(cases[0].case_id, actor="first-reviewer", reason="first reason")
    assert revealed == cases[0]
    restarted = make_store()
    assert cases[0].case_id not in {item["case_id"] for item in restarted.visible_tasks("attempt-1")}
    assert_refused("case_moved_to_dev",
                   lambda: restarted.record("attempt-1", cases[0].case_id, True))
    restarted.reveal(cases[0].case_id, actor="second-reviewer", reason="second reason")
    saved = json.loads((store.directory / "state.json").read_text())["state"]
    assert saved["revealed"][cases[0].case_id] == {
        "actor": "first-reviewer", "reason": "first reason"}
    assert cases[0].case_id not in saved["attempts"][0]["scores"]
    score_all(restarted, cases[1:])
    assert restarted.aggregate("attempt-1")["n"] == 5
    assert make_store().status()["revealed_cases"] == 1


@pytest.mark.parametrize("changes", [
    {"suite_id": "another-suite"}, {"candidate_sha256": "b" * 64},
    {"max_attempts": 3}, {"min_aggregate": 6},
])
def test_manifest_binds_suite_candidate_and_disclosure_policy(make_store, changes):
    store = make_store()
    store.begin("attempt-1")
    assert_refused("manifest_conflict", lambda: make_store(**changes))
    assert make_store().status()["attempts_used"] == 1


@pytest.mark.parametrize("field", ["prompt", "reference", "rubric"])
def test_manifest_binds_every_part_of_each_case(make_store, cases, field):
    make_store()
    changed = (replace(cases[0], **{field: "Different synthetic content"}), *cases[1:])
    assert_refused("manifest_conflict", lambda: make_store(cases=changed))


def test_missing_state_is_refused_by_existing_and_restarted_store(make_store):
    store = make_store()
    store.begin("attempt-1")
    (store.directory / "state.json").unlink()
    assert_refused("state_corrupt", store.status)
    assert_refused("state_corrupt", make_store)


def test_missing_manifest_cannot_reinitialize_an_existing_store(make_store):
    store = make_store()
    store.begin("attempt-1")
    (store.directory / "manifest.json").unlink()
    assert_refused("manifest_missing", make_store)


@pytest.mark.parametrize("mutation", ["delete", "change"])
def test_existing_instance_rechecks_the_manifest_before_accepting_more_work(make_store, mutation):
    store = make_store()
    store.begin("attempt-1")
    manifest = store.directory / "manifest.json"
    if mutation == "delete":
        manifest.unlink()
    else:
        changed = json.loads(manifest.read_text())
        changed["candidate_sha256"] = "b" * 64
        manifest.write_text(json.dumps(changed))
    with pytest.raises(DisclosureRefused):
        store.status()


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("max_attempts", 2.0), ("min_aggregate", 5.0),
])
def test_manifest_identity_distinguishes_boolean_float_and_integer(make_store, field, value):
    store = make_store()
    manifest_path = store.directory / "manifest.json"
    changed = json.loads(manifest_path.read_text())
    changed[field] = value
    manifest_path.write_text(json.dumps(changed))
    with pytest.raises(DisclosureRefused):
        store.status()
    with pytest.raises(DisclosureRefused):
        make_store()


@pytest.mark.parametrize("corrupt", [
    "", "{", "null", "[]", '{"state": {}, "sha256": "wrong"}',
    '{"state": {}, "state": {}, "sha256": "wrong"}',
])
def test_corrupted_state_fails_closed_without_reset(make_store, corrupt):
    store = make_store()
    store.begin("attempt-1")
    state_path = store.directory / "state.json"
    state_path.write_text(corrupt)
    assert_refused("state_corrupt", store.status)
    assert_refused("state_corrupt", make_store)
    assert state_path.read_text() == corrupt


def test_valid_state_from_another_candidate_cannot_be_swapped_in(make_store, tmp_path):
    first = make_store()
    second = make_store(directory=tmp_path.resolve() / "other", candidate_sha256="b" * 64)
    (first.directory / "state.json").write_bytes((second.directory / "state.json").read_bytes())
    assert_refused("state_corrupt", first.status)


def rewrite_state_with_valid_checksum(path, mutate):
    """Exercise validation of decoded state, independently of its checksum.

    This tests the closed output contract, not resistance to an owner replacing
    an entire store. A checksum alone does not make a loaded object well-typed.
    """
    envelope = json.loads(path.read_text())
    mutate(envelope["state"])
    canonical = json.dumps(envelope["state"], ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
    envelope["sha256"] = hashlib.sha256(canonical).hexdigest()
    path.write_text(json.dumps(envelope))


@pytest.mark.parametrize("field,value", [
    ("per_case", {"synthetic-0": False}),
    ("reference", "SYNTHETIC-LEAK"),
    ("rubric", "SYNTHETIC-RUBRIC-LEAK"),
    ("n", True), ("passed", False),
    ("n", 5), ("passed", 7), ("passed", 5),
    ("attempts_used", 0), ("attempts_used", 2),
    ("attempts_declared", 10), ("live_cases", 5), ("revealed_cases", 1),
])
def test_cached_aggregate_requires_exact_schema_and_binding_even_with_valid_checksum(
        make_store, cases, field, value):
    store = make_store()
    store.begin("attempt-1")
    score_all(store, cases)
    store.aggregate("attempt-1")
    rewrite_state_with_valid_checksum(
        store.directory / "state.json",
        lambda state: state["attempts"][0]["aggregate"].update({field: value}),
    )
    assert_refused("state_corrupt", lambda: store.aggregate("attempt-1"))
    assert_refused("state_corrupt", make_store)


@pytest.mark.parametrize("aggregate", [[], "not-an-aggregate", {}, {"n": 6}])
def test_cached_aggregate_rejects_wrong_shape_before_replay(make_store, cases, aggregate):
    store = make_store()
    store.begin("attempt-1")
    score_all(store, cases)
    store.aggregate("attempt-1")
    rewrite_state_with_valid_checksum(
        store.directory / "state.json",
        lambda state: state["attempts"][0].update({"aggregate": aggregate}),
    )
    assert_refused("state_corrupt", lambda: store.aggregate("attempt-1"))


def test_cached_aggregate_must_agree_with_saved_case_scores(make_store, cases):
    store = make_store()
    store.begin("attempt-1")
    score_all(store, cases)
    store.aggregate("attempt-1")
    rewrite_state_with_valid_checksum(
        store.directory / "state.json",
        lambda state: state["attempts"][0]["scores"].update({cases[0].case_id: False}),
    )
    assert_refused("state_corrupt", lambda: store.aggregate("attempt-1"))


def test_closed_scores_cannot_omit_a_case_that_was_never_revealed(make_store, cases):
    store = make_store()
    store.begin("attempt-1")
    score_all(store, cases)
    store.aggregate("attempt-1")

    def omit_unrevealed_case(state):
        attempt = state["attempts"][0]
        attempt["scores"].pop(cases[0].case_id)
        attempt["aggregate"].update(n=5, passed=5, live_cases=5, revealed_cases=1)
        assert state["revealed"] == {}

    rewrite_state_with_valid_checksum(store.directory / "state.json", omit_unrevealed_case)
    assert_refused("state_corrupt", lambda: store.aggregate("attempt-1"))
    assert_refused("state_corrupt", make_store)


@pytest.mark.parametrize("operation", [
    "constructor", "status", "begin", "visible_tasks", "record", "aggregate", "reveal",
])
def test_busy_lock_refuses_without_waiting_or_mutating(make_store, cases, operation):
    store = make_store()
    store.begin("attempt-1")
    score_all(store, cases)
    calls = {
        "constructor": make_store,
        "status": store.status,
        "begin": lambda: store.begin("attempt-1"),
        "visible_tasks": lambda: store.visible_tasks("attempt-1"),
        "record": lambda: store.record("attempt-1", cases[0].case_id, True),
        "aggregate": lambda: store.aggregate("attempt-1"),
        "reveal": lambda: store.reveal(cases[0].case_id, actor="reviewer", reason="inspect"),
    }
    before = (store.directory / "state.json").read_bytes()
    with (store.directory / "store.lock").open("r+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            assert_refused("store_busy", calls[operation])
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)
    assert (store.directory / "state.json").read_bytes() == before


def test_public_outputs_and_persisted_files_do_not_contain_references_or_rubrics(make_store, cases):
    store = make_store()
    store.begin("attempt-1")
    tasks = store.visible_tasks("attempt-1")
    assert all(set(task) == {"case_id", "prompt"} for task in tasks)
    score_all(store, cases)
    result = store.aggregate("attempt-1")
    public_text = json.dumps([tasks, result, store.status()])
    file_text = "\n".join(path.read_text() for path in store.directory.iterdir() if path.is_file())
    for text in (public_text, file_text):
        assert '"reference"' not in text
        assert '"rubric"' not in text
        for case in cases:
            assert case.reference not in text
            assert case.rubric not in text
    assert set(result) == {"n", "passed", "attempts_used", "attempts_declared",
                           "live_cases", "revealed_cases"}


@pytest.mark.parametrize("operation", ["begin", "record", "aggregate", "reveal"])
def test_failed_atomic_replacement_does_not_publish_a_mutation(make_store, cases, monkeypatch, operation):
    store = make_store()
    if operation != "begin":
        store.begin("attempt-1")
    if operation == "aggregate":
        score_all(store, cases)
    before_status = store.status()
    state_path = store.directory / "state.json"
    before_bytes = state_path.read_bytes()
    calls = {
        "begin": lambda: store.begin("attempt-1"),
        "record": lambda: store.record("attempt-1", cases[0].case_id, True),
        "aggregate": lambda: store.aggregate("attempt-1"),
        "reveal": lambda: store.reveal(cases[0].case_id, actor="reviewer", reason="inspect"),
    }

    def fail_replace(source, destination):
        raise OSError("synthetic failure before atomic replacement")

    with monkeypatch.context() as patch:
        patch.setattr(acceptance_store.os, "replace", fail_replace)
        with pytest.raises(OSError, match="synthetic failure"):
            calls[operation]()
    assert state_path.read_bytes() == before_bytes
    assert make_store().status() == before_status
    if operation == "reveal":
        assert cases[0].case_id in {task["case_id"] for task in store.visible_tasks("attempt-1")}


def test_reveal_is_saved_before_the_reference_is_returned(make_store, cases, monkeypatch):
    store = make_store()
    save = store._save
    observed = []

    def verify_persisted_reveal(state):
        save(state)
        saved = json.loads((store.directory / "state.json").read_text())["state"]
        observed.append(cases[0].case_id in saved["revealed"])

    monkeypatch.setattr(store, "_save", verify_persisted_reveal)
    revealed = store.reveal(cases[0].case_id, actor="reviewer", reason="inspect")
    assert observed == [True]
    assert revealed.reference == cases[0].reference
    assert make_store().status()["revealed_cases"] == 1
