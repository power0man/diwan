"""ك٤٣ (#28): بنكُ العربية العامة v3 مئةٌ وخمسون حالةً على القدرات التسع، مجمَّدٌ ببصمته قبل القياس.

- الملفّان المودَعان هما ما سلّمه Kimi بايتًا ببايت، وبصمتاهما في دليل التجميد `docs/probe/k43-general-150-*.json`.
- المدقّقُ الحقيقيّ يقبلهما، وكلُّ قدرةٍ من التسع حاضرة، ولكلّ حالةٍ فحص، ولا حالةَ يمرّرها جوابٌ ثابت خارج الحاوية.
- تغييرُ حالةٍ واحدة، أو حذفُ ملفّ، أو نقصُ العدد، يُسمّى لا يُسكت عنه.
"""
from __future__ import annotations

import copy
import json

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
