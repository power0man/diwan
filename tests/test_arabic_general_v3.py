"""ك٤٣ (#28): بنكُ العربية العامة v3 مئةٌ وخمسون حالةً على القدرات التسع، مجمَّدٌ ببصمته قبل القياس.

- الملفّان المودَعان هما ما سلّمه Kimi بايتًا ببايت، وبصمتاهما في دليل التجميد `docs/probe/k43-general-150-*.json`.
- المدقّقُ الحقيقيّ يقبلهما، وكلُّ قدرةٍ من التسع حاضرة، ولكلّ حالةٍ فحص، ولا حالةَ يمرّرها جوابٌ ثابت خارج الحاوية.
- تغييرُ حالةٍ واحدة، أو حذفُ ملفّ، أو نقصُ العدد، يُسمّى لا يُسكت عنه.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys

from tools import general_bank_freeze as gbf

PROBE = gbf.latest_probe()
FROZEN = json.loads(PROBE.read_text(encoding="utf-8")) if PROBE else {}
CURRENT = gbf.profile()


def test_the_bank_is_frozen_at_the_digests_its_evidence_publishes():
    assert PROBE is not None and FROZEN["kind"] == gbf.KIND and FROZEN["status"] == "frozen_not_measured"
    assert gbf.freeze_findings(CURRENT, FROZEN) == []
    assert [f["sha256"] for f in FROZEN["files"]] == [f["sha256"] for f in CURRENT["files"]]
    assert FROZEN["measurement_limits"] and FROZEN["spend"]["cost_basis"] == "local_no_charge"


def test_the_bank_has_150_cases_on_nine_capabilities_each_with_a_check_none_gameable():
    assert CURRENT["findings"] == []
    assert CURRENT["cases"] >= gbf.MIN_CASES and set(CURRENT["capabilities"]) == gbf.GENERAL_CAPABILITIES
    assert min(CURRENT["capabilities"].values()) >= gbf.MIN_PER_CAPABILITY and CURRENT["checks_per_case_min"] >= 1
    probes = CURRENT["fixed_answer_probes"]
    assert set(probes) <= {"not_gameable", "needs_sandbox"}
    assert CURRENT["independence"]["case_id_overlap"] == 0 and CURRENT["independence"]["first_message_overlap"] == 0


def test_a_changed_or_missing_file_is_named_against_the_freeze():
    edited = copy.deepcopy(CURRENT)
    edited["files"][0]["sha256"] = "0" * 64
    assert f"digest_changed:{CURRENT['files'][0]['path']}" in gbf.freeze_findings(edited, FROZEN)
    del edited["files"][1]
    assert f"frozen_file_missing:{CURRENT['files'][1]['path']}" in gbf.freeze_findings(edited, FROZEN)
    assert "bank_digest_changed" in gbf.freeze_findings({**CURRENT, "bank_sha256": "x"}, FROZEN)


def test_a_short_unchecked_or_gameable_bank_is_named(tmp_path):
    suites = tmp_path / "suites"
    suites.mkdir()
    bank = tmp_path / "bank"
    bank.mkdir()
    first = json.loads((gbf.SUITES_DIR / gbf.SUITES[0]).read_text(encoding="utf-8"))
    first["cases"] = first["cases"][:12]
    first["cases"][0]["checks"] = []
    first["cases"][1]["checks"] = [{"kind": "excludes", "value": "zzz"}]
    (suites / gbf.SUITES[0]).write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
    (bank / "b.json").write_text(json.dumps({"cases": [{"case_id": first["cases"][2]["case_id"], "messages": []}]},
                                            ensure_ascii=False), encoding="utf-8")
    findings = gbf.profile(suites, measurement_bank=bank)["findings"]
    assert f"file_missing:{gbf.SUITES[1]}" in findings
    assert "too_few_cases:12" in findings
    assert f"case_without_checks:{first['cases'][0]['case_id']}" in findings
    assert f"gameable_by_fixed_answer:{first['cases'][1]['case_id']}:empty" in findings
    assert f"overlaps_measurement_bank:{first['cases'][2]['case_id']}" in findings
    assert any(f.startswith("capability_under_minimum:") for f in findings)


def _earlier(suites, name, cases):
    (suites / name).write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")


def test_a_case_repeated_from_an_earlier_suite_by_id_or_first_message_is_named(tmp_path):
    """بنكا v1 وv2 كبنك القياس: معرّفٌ مشترك وحده، أو رسالةٌ أولى مشتركة وحدها، يُسمّى ويمنع التجميد."""
    suites = tmp_path / "suites"
    suites.mkdir()
    for name in gbf.SUITES:
        (suites / name).write_bytes((gbf.SUITES_DIR / name).read_bytes())
    current = json.loads((suites / gbf.SUITES[0]).read_text(encoding="utf-8"))["cases"]
    by_id, by_text = current[0], current[1]
    _earlier(suites, gbf.EARLIER_SUITES[0],
             [{"case_id": by_id["case_id"], "messages": [{"role": "user", "content": "نصٌّ آخر لا يتكرّر"}]}])
    _earlier(suites, gbf.EARLIER_SUITES[1],
             [{"case_id": "earlier_only_0001", "messages": [{"role": "user", "content": gbf._first_user(by_text)}]}])
    result = gbf.profile(suites, measurement_bank=tmp_path / "none")
    assert result["findings"] == [f"overlaps_earlier_suite:{gbf.EARLIER_SUITES[0]}:{by_id['case_id']}",
                                  f"overlaps_earlier_suite:{gbf.EARLIER_SUITES[1]}:{by_text['case_id']}"]
    assert result["independence"][gbf.EARLIER_SUITES[0]] == {"case_id_overlap": 1, "first_message_overlap": 0}
    assert result["independence"][gbf.EARLIER_SUITES[1]] == {"case_id_overlap": 0, "first_message_overlap": 1}


def test_unrelated_or_unreadable_earlier_suites(tmp_path):
    """الضابط: سابقٌ لا يشارك الحاليَّ شيئًا لا يُسمّى؛ وسابقٌ لا يُقرأ يُسمّى ولا يُسقط الأداة."""
    suites = tmp_path / "suites"
    suites.mkdir()
    for name in gbf.SUITES:
        (suites / name).write_bytes((gbf.SUITES_DIR / name).read_bytes())
    _earlier(suites, gbf.EARLIER_SUITES[0],
             [{"case_id": "earlier_only_0001", "messages": [{"role": "user", "content": "نصٌّ آخر لا يتكرّر"}]}])
    assert gbf.profile(suites, measurement_bank=tmp_path / "none")["findings"] == []
    (suites / gbf.EARLIER_SUITES[1]).write_text("{not json", encoding="utf-8")
    assert gbf.profile(suites, measurement_bank=tmp_path / "none")["findings"] == [
        f"earlier_suite_unreadable:{gbf.EARLIER_SUITES[1]}"]


def _with_current(tmp_path):
    suites = tmp_path / "suites"
    suites.mkdir()
    for name in gbf.SUITES:
        (suites / name).write_bytes((gbf.SUITES_DIR / name).read_bytes())
    return suites, json.loads((suites / gbf.SUITES[0]).read_text(encoding="utf-8"))["cases"][0]


def _malformed_shapes(case):
    """أشكالٌ لا تشهد بالاستقلال: كلٌّ منها كان يُرشَّح صامتًا إلى صفر حالات أو يُسقط الأداة."""
    return {
        "cases_dict": {"cases": {case["case_id"]: case}},
        "cases_string": {"cases": case["case_id"]},
        "cases_null": {"cases": None},
        "cases_missing": {"suite": "x"},
        "top_level_list": [case],
        "non_object_member": {"cases": [case["case_id"]]},
        "object_beside_non_object": {"cases": [case, 7]},
        "member_without_id": {"cases": [{"messages": case["messages"]}]},
        "member_without_first_message": {"cases": [{"case_id": case["case_id"], "messages": []}]},
        "member_with_non_text_first_message": {"cases": [{"case_id": "x", "messages": [{"role": "user", "content": 1}]}]},
    }


def test_a_malformed_earlier_suite_is_unreadable_not_independent(tmp_path):
    """شكلٌ مشوَّه لا يُرشَّح صامتًا إلى «لا تداخل»: يُسمّى `earlier_suite_unreadable` ولا يُحسب استقلاله."""
    suites, case = _with_current(tmp_path)
    name = gbf.EARLIER_SUITES[0]
    for label, data in _malformed_shapes(case).items():
        (suites / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = gbf.profile(suites, measurement_bank=tmp_path / "none")
        assert result["findings"] == [f"earlier_suite_unreadable:{name}"], label
        assert name not in result["independence"], label


def test_valid_earlier_suite_controls_keep_their_verdict(tmp_path):
    """الضابط: قائمةٌ فارغة سليمة، وقائمةٌ بالحالة نفسها تُسمّى تداخلًا لا شكلًا مشوَّهًا."""
    suites, case = _with_current(tmp_path)
    name = gbf.EARLIER_SUITES[0]
    _earlier(suites, name, [])
    assert gbf.profile(suites, measurement_bank=tmp_path / "none")["findings"] == []
    _earlier(suites, name, [case])
    assert gbf.profile(suites, measurement_bank=tmp_path / "none")["findings"] == [
        f"overlaps_earlier_suite:{name}:{case['case_id']}"]


def _cli_check(suites):
    """`main(["--check"])` في عمليّةٍ مستقلّة على دليل التجميد الفعليّ، والبنوكُ من `suites`."""
    script = ("import sys; from pathlib import Path; from tools import general_bank_freeze as g; "
              "g.SUITES_DIR = Path(sys.argv[1]); raise SystemExit(g.main(['--check']))")
    return subprocess.run([sys.executable, "-c", script, str(suites)], cwd=gbf.ROOT,
                          capture_output=True, text=True, timeout=120)


def test_the_cli_refuses_a_malformed_earlier_suite_against_the_actual_freeze(tmp_path):
    suites, case = _with_current(tmp_path)
    name = gbf.EARLIER_SUITES[0]
    _earlier(suites, name, [])
    control = _cli_check(suites)
    assert control.returncode == 0, control.stdout + control.stderr
    assert json.loads(control.stdout)["freeze"] == []
    (suites / name).write_text(json.dumps({"cases": {case["case_id"]: case}}, ensure_ascii=False), encoding="utf-8")
    refused = _cli_check(suites)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    report = json.loads(refused.stdout)
    assert report["findings"] == [f"earlier_suite_unreadable:{name}"] and report["freeze"] == []
