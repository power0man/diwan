"""ك٦: استلامُ تسليم Kimi قبل توزيعه — المدقّقاتُ الحقيقية على الشطرين، وشروطُ v1.2، ولا محتوى في التقرير.

- تسليمٌ سليم يمرّ، وكلُّ عيبٍ من عيوب v1.1 يُسمّى برمزه.
- المحجوبُ أعدادٌ ورموزٌ بمسار الملف: لا نصَّ حالةٍ ولا معرّفَها.
- في الحاوية: المهمّةُ تسقط قبل الحلّ، وتمرّ بحلّها المرجعيّ، والحلُّ الخاطئ إن وُجد يسقط.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.kimi_intake import (AGENTIC_MIN_TASKS, GENERAL_CAPABILITIES, GENERAL_MIN_CASES, check_agentic,
                               intake)


def _case(case_id, capability="a", checks=True, text="q"):
    return {"case_id": case_id, "capability": capability, "messages": [{"role": "user", "content": text}],
            "reference": "r", "rubric": ["r"], "critical": False,
            "checks": [{"kind": "contains", "value": "r"}] if checks else []}


def _suite(suite_id, cases, split="development"):
    return {"schema_version": 1, "suite_id": suite_id, "split": split, "description": "d", "cases": cases}


def _task(task_id, passes_before=False):
    return {"task_id": task_id, "capability": "debug_and_fix", "instruction": "أصلح",
            "workspace": {"notes.txt": "new" if passes_before else "old", "tests/check.txt": "t"},
            "success": {"kind": "file_contains", "path": "notes.txt", "value": "new"},
            "forbidden": ["tests/check.txt"], "rubric": ["r"], "max_steps": 4}


def _agentic(suite_id, tasks):
    return {"schema_version": 1, "suite_id": suite_id, "kind": "agentic_tasks", "description": "d", "tasks": tasks}


def _write(path: Path, value) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False).encode()
    path.write_bytes(raw)
    return raw


def delivery(tmp: Path, *, sealed_cases=None, dev=True, agentic_ids=("t1",), sealed_agentic_ids=("t2",)) -> Path:
    src = tmp / "kimi-benchmark"
    _write(src / "open" / "tier_a" / "kimi_a_001.json", _suite("kimi_a_001", [_case("o1"), _case("o2")]))
    _write(src / "open" / "tier_d" / "kimi_d_001.json", _agentic("kimi_d_001", [_task(i) for i in agentic_ids]))
    files = []
    for relative, value in (("sealed/tier_a/kimi_a_101.json",
                             _suite("kimi_a_101", sealed_cases or [_case("s1"), _case("s2")])),
                            ("sealed/tier_d/kimi_d_101.json",
                             _agentic("kimi_d_101", [_task(i) for i in sealed_agentic_ids]))):
        raw = _write(src / relative, value)
        files.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest(),
                      "count": len(value.get("cases") or value.get("tasks")), "kind": "suite"})
    _write(src / "sealed" / "MANIFEST.json", {"files": files})
    (src / "REPORT.md").write_text("# v1.2", encoding="utf-8")
    (src / "disputed.json").write_text("{}", encoding="utf-8")
    if dev:
        caps = sorted(GENERAL_CAPABILITIES)
        general = [_case(f"g{i}", caps[i % len(caps)]) for i in range(GENERAL_MIN_CASES)]
        _write(src / "arabic_general_v3_1.json", _suite("arabic_general_v3_1", general[:100]))
        _write(src / "arabic_general_v3_2.json", _suite("arabic_general_v3_2", general[100:]))
        _write(src / "agentic_v3.json", _agentic("agentic_v3", [_task(f"d{i}") for i in range(AGENTIC_MIN_TASKS)]))
        _write(src / "agentic_v3.meta.json",
               {"tasks": {f"d{i}": {"reference_solution": {"notes.txt": "new"}} for i in range(AGENTIC_MIN_TASKS)}})
    return src


def _codes(report, stage):
    return {f["code"] for f in report[stage]["failures"]}


def test_a_clean_delivery_passes_with_counts(tmp_path):
    report = intake(delivery(tmp_path))
    assert report["passed"], report
    assert report["manifest"]["files"] == 2
    assert report["bank"]["counts"]["open"] == {"files": 2, "cases": 2, "tasks": 1}
    assert report["bank"]["counts"]["sealed"] == {"files": 2, "cases": 2, "tasks": 1}
    assert report["dev"]["general"]["cases"] == GENERAL_MIN_CASES and report["dev"]["agentic"]["without_reference"] == 0


def test_the_v1_1_defects_are_named(tmp_path):
    src = delivery(tmp_path / "a", sealed_cases=[_case("s1", checks=False)], agentic_ids=("t1",),
                   sealed_agentic_ids=("t1",))
    report = intake(src)
    assert not report["passed"]
    assert {"cases_without_checks", "task_id_not_unique_across_bank"} <= _codes(report, "bank")
    assert report["bank"]["without_checks"] == {"open": 0, "sealed": 1}


def test_the_manifest_must_match_every_sealed_file(tmp_path):
    src = delivery(tmp_path)
    (src / "sealed" / "tier_a" / "kimi_a_101.json").write_text(json.dumps(_suite("kimi_a_101", [_case("x")])))
    _write(src / "sealed" / "tier_a" / "kimi_a_102.json", _suite("kimi_a_102", [_case("y")]))
    codes = _codes(intake(src), "manifest")
    assert {"sealed_digest_mismatch", "sealed_count_mismatch", "sealed_file_unlisted"} <= codes


def test_a_schema_violation_is_rejected_by_the_real_validator(tmp_path):
    src = delivery(tmp_path)
    bad = _suite("kimi_a_001", [_case("o1")])
    bad["cases"][0]["extra"] = 1
    _write(src / "open" / "tier_a" / "kimi_a_001.json", bad)
    report = intake(src)
    assert not report["passed"] and {"file": "open/tier_a/kimi_a_001.json", "code": "schema_fields"} \
        in report["bank"]["failures"]


def test_the_development_banks_meet_the_request(tmp_path):
    src = delivery(tmp_path)
    (src / "arabic_general_v3_2.json").unlink()
    _write(src / "arabic_general_v3_1.json", _suite("arabic_general_v3_1", [_case("g1", "programming")]))
    meta = json.loads((src / "agentic_v3.meta.json").read_text())
    del meta["tasks"]["d0"]
    _write(src / "agentic_v3.meta.json", meta)
    codes = _codes(intake(src), "dev")
    assert {"general_file_missing", "capabilities_missing", "too_few_cases", "reference_solution_missing"} <= codes


def test_a_single_general_file_over_one_hundred_cases_is_what_the_validator_refuses(tmp_path):
    """التكليفُ يطلب ملفّين لهذا: فوق مئة حالةٍ في ملفٍّ يردّه المدقّقُ الحقيقيّ."""
    src = delivery(tmp_path)
    caps = sorted(GENERAL_CAPABILITIES)
    _write(src / "arabic_general_v3_1.json",
           _suite("arabic_general_v3_1", [_case(f"g{i}", caps[i % len(caps)]) for i in range(101)]))
    report = intake(src)
    assert {"file": "arabic_general_v3_1.json", "code": "cases_invalid"} in report["dev"]["failures"]


def test_case_ids_are_unique_across_the_two_general_files(tmp_path):
    src = delivery(tmp_path)
    second = json.loads((src / "arabic_general_v3_2.json").read_text())
    second["cases"][0]["case_id"] = "g0"
    _write(src / "arabic_general_v3_2.json", second)
    assert "case_id_not_unique_across_files" in _codes(intake(src), "dev")


def test_sealed_case_text_and_ids_never_reach_the_report(tmp_path):
    secret = [_case("SECRET_ID_77", checks=False, text="SECRET_TEXT_88")]
    report = intake(delivery(tmp_path, sealed_cases=secret), agentic=True)
    text = json.dumps(report, ensure_ascii=False)
    assert "SECRET_ID_77" not in text and "SECRET_TEXT_88" not in text
    assert not report["passed"]


def test_in_the_container_tasks_fail_before_and_pass_with_their_reference(tmp_path):
    report = intake(delivery(tmp_path), agentic=True)
    counts = report["agentic"]["counts"]
    assert counts["tasks"] == 2 + AGENTIC_MIN_TASKS and counts["fail_before_fix"] == counts["tasks"]
    assert counts["reference_passes"] == AGENTIC_MIN_TASKS and report["passed"]


def test_a_task_that_passes_before_its_fix_or_a_passing_decoy_is_named(tmp_path):
    src = delivery(tmp_path)
    _write(src / "open" / "tier_d" / "kimi_d_001.json", _agentic("kimi_d_001", [_task("t1", passes_before=True)]))
    meta = json.loads((src / "agentic_v3.meta.json").read_text())
    meta["tasks"]["d0"]["decoy_solution"] = {"notes.txt": "new"}
    meta["tasks"]["d1"]["reference_solution"] = {"notes.txt": "still old"}
    _write(src / "agentic_v3.meta.json", meta)
    report = check_agentic(src)
    assert {f["code"] for f in report["failures"]} >= {"passes_before_fix", "decoy_solution_passes",
                                                        "reference_solution_fails"}
    assert report["counts"]["pass_before_fix"] == 1 and report["counts"]["reference_fails"] == 1


def test_an_unjudgeable_task_is_not_counted_as_failing_before_its_fix(tmp_path):
    src = delivery(tmp_path, dev=False)
    unjudged = lambda task, overlay: ({"passed": False, "code": "success_command_unavailable"}, [])  # noqa: E731
    counts = check_agentic(src, judge=unjudged)["counts"]
    assert counts["unjudged"] == 2 and counts["fail_before_fix"] == 0


def test_an_incomplete_delivery_stops_at_the_structure(tmp_path):
    src = tmp_path / "kimi-benchmark"
    (src / "open").mkdir(parents=True)
    report = intake(src)
    assert not report["passed"] and "sealed/MANIFEST.json" in report["structure"]["missing"]
    assert "bank" not in report


def test_an_open_only_delivery_passes_without_a_manifest_and_refuses_a_sealed_folder(tmp_path):
    """دورةُ الشطر المفتوح: لا يصل Kimi محجوبٌ، فلا بيانَ يُطلب، ويُرفض تسليمٌ فيه sealed/."""
    import shutil
    src = delivery(tmp_path)
    current = tmp_path / "current_open"
    shutil.copytree(src / "open", current)
    with_sealed = intake(src, open_only=True, current=current)
    assert not with_sealed["passed"] and with_sealed["structure"]["missing"]
    shutil.rmtree(src / "sealed")
    assert not intake(src)["passed"], "بلا open_only يبقى البيانُ مطلوبًا"
    report = intake(src, open_only=True, current=current)
    assert report["passed"], report
    assert report["manifest"]["skipped"] == "open_only"
    assert report["bank"]["counts"]["sealed"]["files"] == 0


def test_an_open_only_delivery_must_carry_every_current_file_and_case(tmp_path):
    """ملاحظةُ Codex على #128: التوزيعُ يستبدل المفتوح، فالتسليمُ الفارغ أو الناقص كان يمرّ ثم يمحو البنك."""
    import shutil
    src = delivery(tmp_path)
    shutil.rmtree(src / "sealed")
    current = tmp_path / "current_open"
    shutil.copytree(src / "open", current)
    _write(current / "tier_a" / "kimi_a_002.json", _suite("kimi_a_002", [_case("o9")]))
    assert _codes(intake(src, open_only=True, current=current), "replacement") == {"open_file_missing"}
    _write(src / "open" / "tier_a" / "kimi_a_002.json", _suite("kimi_a_002", [_case("o8")]))
    assert _codes(intake(src, open_only=True, current=current), "replacement") == {"open_case_missing"}
    _write(src / "open" / "tier_a" / "kimi_a_002.json", _suite("kimi_a_002", [_case("o9"), _case("o10")]))
    _write(src / "open" / "tier_a" / "kimi_a_003.json", _suite("kimi_a_003", [_case("n1")]))
    assert intake(src, open_only=True, current=current)["passed"], "الزيادةُ لا تمحو شيئًا"
    shutil.rmtree(src / "open")
    (src / "open").mkdir()
    empty = intake(src, open_only=True, current=current)
    assert not empty["passed"] and "open_file_missing" in _codes(empty, "replacement")
    assert _codes(intake(src, open_only=True, current=tmp_path / "absent"), "replacement") == {
        "current_open_bank_missing"}


def test_an_open_only_delivery_must_keep_every_sidecar_and_its_tasks(tmp_path):
    """ملاحظةُ Codex على #128: الملفُّ الجانبيّ كان خارج الحصر، فيمحو التوزيعُ حلولَه المرجعية بصمت."""
    import shutil
    src = delivery(tmp_path)
    shutil.rmtree(src / "sealed")
    meta = {"tasks": {"t1": {"reference_solution": {"notes.txt": "new"}}}}
    _write(src / "open" / "tier_d" / "kimi_d_001.meta.json", meta)
    current = tmp_path / "current_open"
    shutil.copytree(src / "open", current)
    assert intake(src, open_only=True, current=current)["passed"]
    _write(src / "open" / "tier_d" / "kimi_d_001.meta.json", {"tasks": {}})
    assert _codes(intake(src, open_only=True, current=current), "replacement") == {"open_case_missing"}
    (src / "open" / "tier_d" / "kimi_d_001.meta.json").unlink()
    assert _codes(intake(src, open_only=True, current=current), "replacement") == {"open_file_missing"}


def test_a_case_sidecar_keeps_its_cases_and_an_unreadable_one_is_refused(tmp_path):
    """ملاحظةُ Codex على #128: ملفّاتُ الطبقات أ–ج الجانبية مفتاحُها cases لا tasks، فكان حذفُ مصادرها يمرّ."""
    import shutil
    src = delivery(tmp_path)
    shutil.rmtree(src / "sealed")
    sidecar = src / "open" / "tier_a" / "kimi_a_001.meta.json"
    _write(sidecar, {"suite_id": "kimi_a_001", "cases": {"o1": {"source": "s"}, "o2": {"source": "s"}}})
    current = tmp_path / "current_open"
    shutil.copytree(src / "open", current)
    assert intake(src, open_only=True, current=current)["passed"]
    _write(sidecar, {"suite_id": "kimi_a_001", "cases": {"o1": {"source": "s"}}})
    assert _codes(intake(src, open_only=True, current=current), "replacement") == {"open_case_missing"}
    sidecar.write_text("{not json", encoding="utf-8")
    assert _codes(intake(src, open_only=True, current=current), "replacement") == {"sidecar_unreadable"}
