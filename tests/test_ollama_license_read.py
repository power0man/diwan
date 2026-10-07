"""قراءةُ رخص وسوم Ollama على الماك أداةً لا يدًا، وما لم يُقرأ يبقى منتظِرًا بما حدث (جديد-license-tagging، #301)."""
from __future__ import annotations

import hashlib
import json
import threading
from subprocess import CompletedProcess

import pytest

from tools import model_licenses as ml
from tools import ollama_license_read as olr
from tools import probe_evidence
from tests.test_weight_provenance import MIT_BODY

LIST = "NAME                 ID              SIZE      MODIFIED\nllama3.1:8b          46e0c10c039e    4.9 GB    2 days ago\nqwen3-embedding:0.6b ac6da0dfba84    639 MB    3 days ago\ndead:cloud           d3f1c8744721    -         1 day ago\nbroken:1b            000000000000    1 GB      1 day ago\nodd:1b               111111111111    1 GB      1 day ago\n"
LLAMA = b"LLAMA 3.1 COMMUNITY LICENSE AGREEMENT\nLlama 3.1 Version Release Date: July 23, 2024\n..."
ODD = b"Some Model Terms\nnobody has digested this text\n"


def _runner(outputs: dict[str, tuple[int, bytes, bytes]], listing: tuple[int, bytes, bytes] = (0, LIST.encode(), b"")):
    def run(command):
        if command == ["ollama", "--version"]:
            return CompletedProcess(command, 0, b"ollama version is 0.35.0\n", b"")
        if command == ["ollama", "list"]:
            return CompletedProcess(command, *listing)
        code, out, err = outputs[command[-1]]
        return CompletedProcess(command, code, out, err)
    return run


def _registry(**models) -> dict:
    return {"schema_version": 1, "enforced_from": "2026-10-06", "models": models, "historical_evidence": {}}


def test_a_tag_with_a_printed_license_is_resolved_and_named_from_its_text_alone():
    evidence = olr.probe(["llama3.1:8b"], "2026-10-07", _runner({"llama3.1:8b": (0, LLAMA, b"")}))
    digest = hashlib.sha256(LLAMA).hexdigest()
    assert evidence["models"]["llama3.1:8b"] == {
        "tag": "llama3.1:8b", "ollama_list_id": "46e0c10c039e", "command": "ollama show --license", "exit_code": 0,
        "license_text_bytes": len(LLAMA), "read_on": "2026-10-07",
        "license_provenance": {"source": "https://ollama.com/library/llama3.1:8b", "license_text_sha256": digest,
                               "license": "llama3.1", "read_on": "2026-10-07"},
        "license_named_by": "title_lines_as_printed"}
    assert evidence["licenses"] == {"llama3.1:8b": "llama3.1"} and evidence["unresolved_readings"] == []
    assert evidence["spend"]["cost_usd"] == 0 and evidence["tool"].endswith("ollama 0.35.0")
    registry = olr.apply(_registry(**{"llama3.1:8b": {"pending": "read_with_ollama_show_license_on_the_mac"}}), evidence)
    assert registry["models"]["llama3.1:8b"] == {
        "license": "llama3.1", "source": "https://ollama.com/library/llama3.1:8b", "read_on": "2026-10-07",
        "read_via": "ollama_show_license_on_the_mac", "license_text_sha256": digest, "ollama_list_id": "46e0c10c039e"}
    # والدليلُ المكتوب يمرّ على الحارس بالسجلّ المحلول: يسمّي نموذجًا مقروءًا ويعلن رخصتَه ونصَّه
    assert ml.findings(registry, {"model-licenses-ollama-20261007.json": evidence}, None) == []


RESTRICTION = b"All commercial use is forbidden."
QWEN_LIST = (0, b"NAME ID SIZE\nqwen3:14b bdbd181c33f2 9.3 GB\n", b"")


@pytest.mark.parametrize("holder", [
    pytest.param(b"Copyright 2024 Alibaba Cloud", id="a_holder_name"),
    pytest.param(b"Copyright 2024 Example. " + RESTRICTION, id="a_restriction_on_the_copyright_line"),
])
def test_apache_text_with_its_appendix_copyright_line_filled_is_not_named_and_stays_pending_by_its_own_reason(holder):
    # المراجعةُ المرفوضة على #350: إرجاعُ سطر الحقوق المملوء كلِّه إلى قالبه كان يمحو ما بعد الاسم، فسُمّي `apache-2.0`
    # نصٌّ سطرُ حقوقه «Copyright 2024 Example. All commercial use is forbidden.». ولا قاعدةَ تفرّق الاسمَ من الشرط بلا تخمين،
    # فلا يُسمّى ملحقٌ مملوء، ويبقى منتظِرًا بسببٍ يقول إنّ الجسمَ والملحقَ نصُّ SPDX إلّا ذلك السطر
    unfilled = (olr.ROOT / "LICENSE").read_bytes()
    assert olr.name_license(unfilled) == ("apache-2.0", "spdx_body_digest_via_weight_provenance_identify_license")
    filled = unfilled.replace(b"Copyright [yyyy] [name of copyright owner]", holder)
    assert filled != unfilled and olr.name_license(filled) == (None, None)
    # والعنوانُ لا يسمّيها، فلا يُسمّى «Apache License» وحدها، ولا نسخةٌ أخرى
    assert olr.name_license(b"Apache License\nVersion 1.1\n...") == (None, None)
    assert olr.name_license(b"The Apache Software License, Version 1.1\n...") == (None, None)
    evidence = olr.probe(["qwen3:14b"], "2026-10-07", _runner({"qwen3:14b": (0, filled, b"")}, listing=QWEN_LIST))
    assert evidence["models"] == {} and evidence["licenses"] == {}
    [record] = evidence["unresolved_readings"]
    assert record["pending"] == olr.FILLED and record["license_text_sha256"] == hashlib.sha256(filled).hexdigest()
    registry = olr.apply(_registry(**{"qwen3:14b": {"pending": "read_with_ollama_show_license_on_the_mac"}}), evidence)
    assert registry["models"]["qwen3:14b"] == {"pending": olr.FILLED}
    assert ml.findings(registry, {"model-licenses-ollama-20261007.json": evidence}, None) == []


def test_a_resolved_tag_whose_text_is_a_filled_apache_appendix_keeps_its_registry_entry():
    # ووسمٌ محلولٌ من قبل (كـ`qwen3:14b` في السجلّ) لا يُعاد كتابةُ قيده حين لا تسمّيه الأداة
    filled = (olr.ROOT / "LICENSE").read_bytes().replace(b"Copyright [yyyy] [name of copyright owner]", b"Copyright 2024 Alibaba Cloud")
    entry = {"license": "apache-2.0", "source": "https://ollama.com/library/qwen3:14b", "read_on": "2026-10-06",
             "read_via": "ollama_show_license_on_the_mac", "license_text_sha256": hashlib.sha256(filled).hexdigest()}
    evidence = olr.probe(["qwen3:14b"], "2026-10-07", _runner({"qwen3:14b": (0, filled, b"")}, listing=QWEN_LIST),
                         registered={"qwen3:14b": entry["license_text_sha256"]})
    assert [r["pending"] for r in evidence["unresolved_readings"]] == [olr.FILLED]
    assert olr.apply(_registry(**{"qwen3:14b": dict(entry)}), evidence)["models"]["qwen3:14b"] == entry




@pytest.mark.parametrize("text", [
    pytest.param(b"Apache License\nVersion 2.0, January 2004\n\n" + RESTRICTION + b"\n", id="title_then_a_restriction"),
    pytest.param("filled_then_a_restriction", id="a_restriction_after_the_appendix"),
    pytest.param("a_restriction_in_the_body", id="a_restriction_before_the_end_of_terms"),
    pytest.param("two_copyright_lines", id="two_appendix_copyright_lines"),
])
def test_text_under_an_apache_title_that_adds_terms_is_not_named_and_stays_pending(text):
    # ملاحظة Codex السادسة على #301: العنوانُ وحده كان يسمّي `apache-2.0` نصًّا يتبعه منعُ الاستخدام التجاري، فيُصنَّف `osi`
    # ويمرّ الحارس. فالآن لا يُسمّى من عنوانه ما في قائمة OSI، وApache تُسمّى بجسمها وملحقها وحدهما
    filled = (olr.ROOT / "LICENSE").read_bytes().replace(b"Copyright [yyyy] [name of copyright owner]", b"Copyright 2024 Alibaba Cloud")
    data = text if isinstance(text, bytes) else {
        "filled_then_a_restriction": filled + b"\n   " + RESTRICTION + b"\n",
        "a_restriction_in_the_body": filled.replace(b"   END OF TERMS AND CONDITIONS", b"   " + RESTRICTION + b"\n\n   END OF TERMS AND CONDITIONS"),
        "two_copyright_lines": filled.replace(b"Copyright 2024 Alibaba Cloud", b"Copyright 2024 Alibaba Cloud\n   Copyright 2025 Someone Else"),
    }[text]
    assert data != filled and olr.name_license(data) == (None, None)
    evidence = olr.probe(["x:1b"], "2026-10-07", _runner({"x:1b": (0, data, b"")}, listing=(0, b"NAME ID\nx:1b 0123\n", b"")))
    assert evidence["licenses"] == {} and [r["pending"] for r in evidence["unresolved_readings"]] == [olr.UNNAMED]
    # ولا عنوانَ في `TITLES` يسمّي رخصةً من قائمة OSI
    assert [name for name in olr.TITLES.values() if ml.license_class(name) == "osi"] == []


def test_readings_that_yield_no_license_stay_pending_by_what_happened():
    tags = ["gemma3:12b", "dead:cloud", "broken:1b", "qwen3-embedding:0.6b", "odd:1b"]
    outputs = {"dead:cloud": (1, b"", b"Error: dead:0731 was retired at 2026-09-25 (ref: x)\n"),
               "broken:1b": (1, b"", b"Error: something else\n"), "qwen3-embedding:0.6b": (0, b"\n", b""),
               "odd:1b": (0, ODD, b"")}
    evidence = olr.probe(tags, "2026-10-07", _runner(outputs))
    assert evidence["models"] == {} and evidence["licenses"] == {}
    assert [(r["tag"], r["pending"]) for r in evidence["unresolved_readings"]] == [
        ("gemma3:12b", olr.NOT_PULLED), ("dead:cloud", olr.RETIRED), ("broken:1b", olr.FAILED),
        ("qwen3-embedding:0.6b", olr.EMPTY), ("odd:1b", olr.UNNAMED)]
    by_tag = {r["tag"]: r for r in evidence["unresolved_readings"]}
    assert by_tag["gemma3:12b"]["present_in_ollama_list"] is False
    assert by_tag["dead:cloud"]["stderr_first_line"].startswith("Error: dead:0731 was retired")
    assert by_tag["odd:1b"]["license_text_sha256"] == hashlib.sha256(ODD).hexdigest() and "license_provenance" not in by_tag["odd:1b"]
    pending = {tag: {"pending": "read_with_ollama_show_license_on_the_mac"} for tag in tags}
    registry = olr.apply(_registry(**pending, resolved={"license": "mit", "source": "https://x.y/z", "read_on": "2026-10-01"}), evidence)
    assert {tag: registry["models"][tag] for tag in tags} == {tag: {"pending": by_tag[tag]["pending"]} for tag in tags}
    assert registry["models"]["resolved"]["license"] == "mit"
    assert ml.findings(registry, {"model-licenses-ollama-20261007.json": evidence}, None) == []
    assert olr.pending_tags(registry) == sorted(tags)


def test_the_cli_writes_the_probe_and_the_registry_and_fails_while_a_tag_stays_pending(tmp_path, monkeypatch, capsys):
    registry = tmp_path / "registry.json"
    probes = tmp_path / "probe"
    probes.mkdir()
    registry.write_text(json.dumps(_registry(**{"llama3.1:8b": {"pending": "read_with_ollama_show_license_on_the_mac"},
                                                "gemma3:12b": {"pending": "read_with_ollama_show_license_on_the_mac"}})))
    monkeypatch.setattr(olr, "run", _runner({"llama3.1:8b": (0, LLAMA, b"")}))
    args = ["--registry", str(registry), "--probe-dir", str(probes), "--day", "2026-10-07"]
    assert olr.main(args) == 2
    assert not list(probes.glob("*.json"))
    assert olr.main([*args, "--write"]) == 2
    written = json.loads((probes / "model-licenses-ollama-20261007.json").read_text())
    assert written["licenses"] == {"llama3.1:8b": "llama3.1"} and written["unresolved_readings"][0]["pending"] == olr.NOT_PULLED
    models = json.loads(registry.read_text())["models"]
    assert models["llama3.1:8b"]["license"] == "llama3.1" and models["gemma3:12b"] == {"pending": olr.NOT_PULLED}
    assert olr.main(["--registry", str(registry), "--probe-dir", str(probes), "--day", "2026-10-07", "--tag", "llama3.1:8b"]) == 0
    assert json.loads("{" + capsys.readouterr().out.rsplit("\n{", 1)[-1])["unresolved_readings"] == []


def test_a_second_run_on_the_same_day_keeps_the_evidence_the_first_run_resolved_the_registry_against(tmp_path, monkeypatch):
    # ملاحظة Codex الأولى على #301: التشغيلُ الثاني كان يستبدل ملفَّ اليوم فيُفقد دليلُ ما حُلّ في الأوّل وتبقى بصمتُه في السجلّ
    registry = tmp_path / "registry.json"
    probes = tmp_path / "probe"
    probes.mkdir()
    registry.write_text(json.dumps(_registry(**{"llama3.1:8b": {"pending": "read_with_ollama_show_license_on_the_mac"},
                                                "gemma3:12b": {"pending": "read_with_ollama_show_license_on_the_mac"}})))
    args = ["--registry", str(registry), "--probe-dir", str(probes), "--day", "2026-10-07", "--write"]
    monkeypatch.setattr(olr, "run", _runner({"llama3.1:8b": (0, LLAMA, b"")}))
    assert olr.main(args) == 2
    first = (probes / "model-licenses-ollama-20261007.json").read_bytes()
    # في التشغيل الثاني سُحب gemma3 فظهر في القائمة بنصٍّ لا يُسمّى؛ ولا يُقرأ llama ثانيةً لأنّه حُلّ
    pulled = LIST + "gemma3:12b           a2af6cc3eb7f    8.1 GB    1 minute ago\n"
    monkeypatch.setattr(olr, "run", _runner({"gemma3:12b": (0, ODD, b"")}, listing=(0, pulled.encode(), b"")))
    assert olr.main(args) == 2
    assert sorted(p.name for p in probes.glob("*.json")) == ["model-licenses-ollama-20261007.json",
                                                             "model-licenses-ollama-20261007b.json"]
    assert (probes / "model-licenses-ollama-20261007.json").read_bytes() == first
    second = json.loads((probes / "model-licenses-ollama-20261007b.json").read_text())
    assert [(r["tag"], r["pending"]) for r in second["unresolved_readings"]] == [("gemma3:12b", olr.UNNAMED)]
    registry_now = json.loads(registry.read_text())
    assert registry_now["models"]["llama3.1:8b"]["license"] == "llama3.1"
    assert registry_now["models"]["gemma3:12b"] == {"pending": olr.UNNAMED}
    evidence = {p.name: json.loads(p.read_text()) for p in probes.glob("*.json")}
    assert ml.findings(registry_now, evidence, None) == []
    # والثالثُ في اليوم نفسِه يأخذ `c`
    assert olr.evidence_path(probes, "2026-10-07").name == "model-licenses-ollama-20261007c.json"


def test_rereading_a_resolved_tag_updates_the_read_fields_and_keeps_the_rest_of_its_entry():
    # ملاحظة Codex الثانية على #301: `--tag` لوسمٍ محلول كان يستبدل قيدَه كلَّه فيسقط `weights` بما يحمله من أدلّة
    weights = [{"file": "ara.traineddata", "sha256": "e3" * 32, "origin": "https://x.y/ara", "origin_sha256": "e3" * 32,
                "license": "apache-2.0", "license_source": "https://x.y/LICENSE", "read_on": "2026-10-05",
                "license_text_sha256": "cf" * 32}]
    previous = {"license": "apache-2.0", "source": "https://ollama.com/library/llama3.1:8b", "read_on": "2026-10-01",
                "read_via": "hugging_face_hub_model_metadata_via_the_session_connector", "weights": weights,
                "attribution": "Copyright (c) someone"}
    evidence = olr.probe(["llama3.1:8b"], "2026-10-07", _runner({"llama3.1:8b": (0, LLAMA, b"")}))
    registry = olr.apply(_registry(**{"llama3.1:8b": dict(previous)}), evidence)
    entry = registry["models"]["llama3.1:8b"]
    assert entry["weights"] == weights and entry["attribution"] == "Copyright (c) someone"
    assert {k: entry[k] for k in olr.READ_FIELDS} == {
        "license": "llama3.1", "source": "https://ollama.com/library/llama3.1:8b", "read_on": "2026-10-07",
        "read_via": "ollama_show_license_on_the_mac", "license_text_sha256": hashlib.sha256(LLAMA).hexdigest(),
        "ollama_list_id": "46e0c10c039e"}
    assert set(entry) == set(olr.READ_FIELDS) | {"weights", "attribution"}


REFORMATTED = LLAMA.replace(b"\n", b"\n\n")


def test_rereading_a_resolved_tag_with_another_text_keeps_its_entry_and_records_both_digests(tmp_path, monkeypatch, capsys):
    # ملاحظة Codex الخامسة على #301: إعادةُ قراءة وسمٍ محلول بنصٍّ تغيّر، ولو بتنسيقه، كانت تستبدل بصمتَه في السجلّ ويبقى
    # الدليلُ الذي حلّه بالبصمة الأولى، فيرفضه الحارس (`license_provenance_conflicts`) والأداةُ تعود بالنجاح
    pending = {"llama3.1:8b": {"pending": "read_with_ollama_show_license_on_the_mac"}}
    first = olr.probe(["llama3.1:8b"], "2026-10-06", _runner({"llama3.1:8b": (0, LLAMA, b"")}))
    registry = olr.apply(_registry(**pending), first)
    digest, other = hashlib.sha256(LLAMA).hexdigest(), hashlib.sha256(REFORMATTED).hexdigest()
    assert olr.registered_texts(registry) == {"llama3.1:8b": digest}
    # بلا بصمات السجلّ تُحلّ القراءةُ ثانيةً فيتعارض السجلُّ مع الدليل الأوّل: هذا ما وصفه Codex
    blind = olr.probe(["llama3.1:8b"], "2026-10-07", _runner({"llama3.1:8b": (0, REFORMATTED, b"")}))
    assert ml.findings(olr.apply(json.loads(json.dumps(registry)), blind), {"a.json": first, "b.json": blind}, None) == [
        "license_provenance_conflicts:a.json:llama3.1:8b"]
    # وببصماته تُقيَّد القراءةُ بالبصمتين ولا تُطبَّق، فيبقى القيدُ والدليلُ الأوّل متّفقين
    second = olr.probe(["llama3.1:8b"], "2026-10-07", _runner({"llama3.1:8b": (0, REFORMATTED, b"")}),
                       registered=olr.registered_texts(registry))
    assert second["models"] == {} and second["licenses"] == {}
    [record] = second["unresolved_readings"]
    assert record["not_applied"] == olr.TEXT_CHANGED and "pending" not in record and "license_provenance" not in record
    assert (record["license_text_sha256"], record["registered_license_text_digest"], record["license_named_from_text"]) == (
        other, digest, "llama3.1")
    kept = olr.apply(json.loads(json.dumps(registry)), second)
    assert kept == registry and kept["models"]["llama3.1:8b"]["license_text_sha256"] == digest
    assert ml.findings(kept, {"a.json": first, "b.json": second}, None) == []
    # والنصُّ نفسُه يُقرأ ثانيةً فيُحلّ ويُحدَّث تاريخُه
    same = olr.probe(["llama3.1:8b"], "2026-10-07", _runner({"llama3.1:8b": (0, LLAMA, b"")}), registered=olr.registered_texts(registry))
    assert same["licenses"] == {"llama3.1:8b": "llama3.1"} and same["unresolved_readings"] == []
    # والأداةُ من سطر الأوامر تسلك الطريقَ نفسَه: التشغيلُ الثاني يكتب دليلَه ولا يمسّ البصمة، ويعود 2 لا 0
    path = tmp_path / "registry.json"
    probes = tmp_path / "probe"
    probes.mkdir()
    path.write_text(json.dumps(_registry(**pending)))
    monkeypatch.setattr(olr, "run", _runner({"llama3.1:8b": (0, LLAMA, b"")}))
    assert olr.main(["--registry", str(path), "--probe-dir", str(probes), "--day", "2026-10-06", "--write"]) == 0
    monkeypatch.setattr(olr, "run", _runner({"llama3.1:8b": (0, REFORMATTED, b"")}))
    assert olr.main(["--registry", str(path), "--probe-dir", str(probes), "--day", "2026-10-07", "--tag", "llama3.1:8b", "--write"]) == 2
    assert "license text differs from the registered digest" in capsys.readouterr().err
    written = json.loads((probes / "model-licenses-ollama-20261007.json").read_text())
    assert written["unresolved_readings"][0]["not_applied"] == olr.TEXT_CHANGED
    registry_now = json.loads(path.read_text())
    assert registry_now["models"]["llama3.1:8b"]["license_text_sha256"] == digest
    assert registry_now["models"]["llama3.1:8b"]["read_on"] == "2026-10-06"
    assert ml.findings(registry_now, {p.name: json.loads(p.read_text()) for p in probes.glob("*.json")}, None) == []


def test_the_cli_writes_nothing_the_license_guard_would_refuse(tmp_path, monkeypatch, capsys):
    # ملاحظة Codex الخامسة على #301: الأداةُ كانت تكتب ما يرفضه الحارسُ على مجموع الأدلّة وتعود بالنجاح. فقيدٌ محلول من بيانات
    # Hugging Face برخصة apache-2.0 أعلنها دليلٌ جديد، يُقرأ وسمُه على الماك فيُسمّى نصُّه llama3.1: لو حُلّ لخالف ذلك الدليلَ
    # (`recorded_license_disagrees`)، فلا يُكتب شيء والرمزُ 4
    path = tmp_path / "registry.json"
    probes = tmp_path / "probe"
    probes.mkdir()
    hub = {"license": "apache-2.0", "source": "https://huggingface.co/meta-llama/x", "read_on": "2026-10-01",
           "read_via": "hugging_face_hub_model_metadata_via_the_session_connector"}
    before = _registry(**{"llama3.1:8b": hub})
    path.write_text(json.dumps(before))
    earlier = {"date": "2026-10-06", "model": "llama3.1:8b", "licenses": {"llama3.1:8b": "apache-2.0"}}
    (probes / "earlier.json").write_text(json.dumps(earlier))
    assert ml.findings(before, {"earlier.json": earlier}, None) == []
    monkeypatch.setattr(olr, "run", _runner({"llama3.1:8b": (0, LLAMA, b"")}))
    args = ["--registry", str(path), "--probe-dir", str(probes), "--day", "2026-10-07", "--tag", "llama3.1:8b"]
    assert olr.main([*args, "--write"]) == 4
    assert [p.name for p in probes.glob("*.json")] == ["earlier.json"] and json.loads(path.read_text()) == before
    err = capsys.readouterr().err
    assert "nothing written" in err and "recorded_license_disagrees:earlier.json:llama3.1:8b" in err
    # وبلا `--write` لا فحصَ ولا كتابة، والرمزُ 0 لأنّ القراءةَ نفسَها تمّت
    assert olr.main(args) == 0
    # وما كان من مخالفاتٍ قبل التشغيل لا يُحسب عليه: المحرّكُ الافتراضيّ غائبٌ عن سجلّ الاختبار قبله وبعده
    evidence = olr.probe(["llama3.1:8b"], "2026-10-07", _runner({"llama3.1:8b": (0, LLAMA, b"")}))
    applied = olr.apply(json.loads(json.dumps(before)), evidence)
    (probes / "earlier.json").unlink()
    assert olr.introduced_findings(before, applied, probes, probes / "new.json", evidence, "qwen3.5:9b") == []
    assert "default_engine_not_in_registry:qwen3.5:9b" in ml.findings(applied, {"new.json": evidence}, "qwen3.5:9b")


def test_a_failed_ollama_list_reads_no_tag_and_writes_nothing(tmp_path, monkeypatch, capsys):
    # ملاحظة Codex الثالثة على #301: رمزُ خروج `ollama list` لم يُفحص، فكان الإخفاقُ يُقرأ قائمةً فارغةً والوسومُ «لم تُسحب»
    down = (1, b"", b"Error: could not connect to ollama server, run 'ollama serve' to start it\n")
    evidence = olr.probe(["llama3.1:8b", "gemma3:12b"], "2026-10-07", _runner({}, listing=down))
    assert evidence["models"] == {} and evidence["licenses"] == {}
    assert evidence["ollama_list_failed"] == {"exit_code": 1, "read_on": "2026-10-07",
                                              "stderr_first_line": "Error: could not connect to ollama server, run 'ollama serve' to start it"}
    assert evidence["unresolved_readings"] == [{"tag": "llama3.1:8b", "not_read": olr.LIST_FAILED, "read_on": "2026-10-07"},
                                               {"tag": "gemma3:12b", "not_read": olr.LIST_FAILED, "read_on": "2026-10-07"}]
    assert all("pending" not in r and "present_in_ollama_list" not in r for r in evidence["unresolved_readings"])
    before = _registry(**{"llama3.1:8b": {"pending": "ollama_show_license_returned_empty_text_on_the_mac"},
                          "gemma3:12b": {"pending": "read_with_ollama_show_license_on_the_mac"}})
    assert olr.apply(json.loads(json.dumps(before)), evidence) == before
    # والأداةُ لا تكتب دليلًا ولا تمسّ السجلّ، وترجع 3
    registry = tmp_path / "registry.json"
    probes = tmp_path / "probe"
    probes.mkdir()
    registry.write_text(json.dumps(before))
    monkeypatch.setattr(olr, "run", _runner({}, listing=down))
    assert olr.main(["--registry", str(registry), "--probe-dir", str(probes), "--day", "2026-10-07", "--write"]) == 3
    assert not list(probes.glob("*.json")) and json.loads(registry.read_text()) == before
    assert "nothing read, nothing written" in capsys.readouterr().err
    # وغيابُ الأمر نفسِه إخفاقٌ مسمًّى لا انفجار
    monkeypatch.undo()
    monkeypatch.setattr(olr.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("ollama")))
    missing = olr.run(["ollama", "list"])
    assert missing.returncode == 127 and missing.stderr == b"FileNotFoundError: ollama"


PENDING = {"pending": "read_with_ollama_show_license_on_the_mac"}


def test_the_evidence_and_the_registry_are_written_together_or_neither(tmp_path, monkeypatch):
    # ملاحظة Codex السابعة على #301: كان الدليلُ يُكتب ثم السجلّ، فإن أخفق السجلُّ بقي دليلٌ يسمّي وسمًا ما زال منتظِرًا
    # فيرفضه الحارس (`license_not_read_before_new_evidence`)
    registry = tmp_path / "registry.json"
    probes = tmp_path / "probe"
    probes.mkdir()
    before = json.dumps(_registry(**{"llama3.1:8b": PENDING}))
    registry.write_text(before)
    monkeypatch.setattr(olr, "run", _runner({"llama3.1:8b": (0, LLAMA, b"")}))
    real = olr.os.replace

    def replace(source, target):
        if str(target) == str(registry):
            raise OSError(28, "No space left on device")
        return real(source, target)

    monkeypatch.setattr(olr.os, "replace", replace)
    with pytest.raises(OSError):
        olr.main(["--registry", str(registry), "--probe-dir", str(probes), "--day", "2026-10-07", "--write"])
    assert registry.read_text() == before
    assert sorted(p.name for p in probes.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["probe", "registry.json", "registry.json.lock"]
    # وبلا إخفاقٍ يُكتبان كلاهما ولا يبقى ملفٌّ مؤقّت
    monkeypatch.setattr(olr.os, "replace", real)
    assert olr.main(["--registry", str(registry), "--probe-dir", str(probes), "--day", "2026-10-07", "--write"]) == 0
    assert [p.name for p in probes.iterdir()] == ["model-licenses-ollama-20261007.json"]
    assert json.loads(registry.read_text())["models"]["llama3.1:8b"]["license"] == "llama3.1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["probe", "registry.json", "registry.json.lock"]


def test_a_writing_run_waits_for_the_registry_lock_and_reads_the_registry_after_it(tmp_path, monkeypatch):
    # ملاحظة Codex السادسة على #301: تشغيلان متزامنان كانا يختاران اسمَ الدليل نفسَه ويقرآن السجلَّ القديم، فيستبدل آخرُهما
    # دليلَ الأوّل ويفقد تحديثاتِه. فالتشغيلُ الكاتب لا يقرأ السجلَّ ولا يكتب حتى يأخذ القفل
    registry = tmp_path / "registry.json"
    probes = tmp_path / "probe"
    probes.mkdir()
    registry.write_text(json.dumps(_registry(**{"llama3.1:8b": PENDING})))
    monkeypatch.setattr(olr, "run", _runner({"llama3.1:8b": (0, LLAMA, b"")}))
    codes = []
    worker = threading.Thread(target=lambda: codes.append(
        olr.main(["--registry", str(registry), "--probe-dir", str(probes), "--day", "2026-10-07", "--write"])))
    with olr.registry_lock(registry):
        worker.start()
        worker.join(0.5)
        assert worker.is_alive() and list(probes.iterdir()) == []
        # تشغيلٌ آخر يكتب تحت القفل: دليلُه في اسم اليوم الأوّل، وقيدٌ جديد في السجلّ
        (probes / "model-licenses-ollama-20261007.json").write_text(json.dumps({"date": "2026-10-07", "licenses": {}}))
        other = {"license": "mit", "source": "https://x.y/z", "read_on": "2026-10-01"}
        registry.write_text(json.dumps(_registry(**{"llama3.1:8b": PENDING, "other:1b": other})))
    worker.join(10)
    assert codes == [0]
    assert sorted(p.name for p in probes.iterdir()) == ["model-licenses-ollama-20261007.json", "model-licenses-ollama-20261007b.json"]
    models = json.loads(registry.read_text())["models"]
    assert models["other:1b"] == other and models["llama3.1:8b"]["license"] == "llama3.1"


@pytest.mark.parametrize("notice", [
    pytest.param("Copyright 2024 Example. All commercial use is forbidden.\n\n", id="a_restriction_on_the_copyright_line"),
    pytest.param("MIT License\n\nCopyright (c) Microsoft Corporation.\n\n", id="a_title_and_a_holder"),
])
def test_mit_text_with_a_copyright_line_is_not_named_and_stays_pending_by_its_own_reason(notice):
    # ملاحظة Codex على #350: `identify_license` يقبل سطرَ الحقوق قبل جسم MIT كلَّه، فسُمّي `mit` (و`osi`) نصٌّ سطرُ حقوقه
    # «Copyright 2024 Example. All commercial use is forbidden.»؛ وهي مسألةُ ملحق Apache المملوء نفسُها، فتُعالَج بمثل علاجها
    data = (notice + MIT_BODY).encode()
    assert olr.name_license(data) == (None, None)
    evidence = olr.probe(["phi:3b"], "2026-10-07", _runner({"phi:3b": (0, data, b"")}, listing=(0, b"NAME ID\nphi:3b 0123\n", b"")))
    assert evidence["licenses"] == {} and [r["pending"] for r in evidence["unresolved_readings"]] == [olr.MIT_NOTICE]
    registry = olr.apply(_registry(**{"phi:3b": {"pending": "read_with_ollama_show_license_on_the_mac"}}), evidence)
    assert registry["models"]["phi:3b"] == {"pending": olr.MIT_NOTICE}
    assert ml.findings(registry, {"model-licenses-ollama-20261007.json": evidence}, None) == []
    # وجسمُ MIT بلا سطر حقوقٍ قبله يُسمّى ببصمته كما كان
    assert olr.name_license(MIT_BODY.encode()) == ("mit", "spdx_body_digest_via_weight_provenance_identify_license")

REFUSED = b"Error: Post \"http://127.0.0.1:11434/api/show\": dial tcp 127.0.0.1:11434: connect: connection refused\n"


def test_the_evidence_is_redacted_and_validated_before_it_is_written(tmp_path, monkeypatch, capsys):
    # ملاحظة Codex على #350: خطأُ `ollama show` بعنوان الخادم المحليّ كان يُكتب في الدليل كما طُبع، ولا يُفحص الدليلُ إلا
    # بحارس الرخص، فيُكتب دليلٌ عامّ يردّه `tools/probe_evidence.py` (`private_operational_metadata`)
    evidence = olr.probe(["broken:1b"], "2026-10-07", _runner({"broken:1b": (1, b"", REFUSED)}))
    [record] = evidence["unresolved_readings"]
    assert record["pending"] == olr.FAILED and record["stderr_first_line"] == probe_evidence.REDACTION_MARKER
    assert probe_evidence.validate_payload(evidence, current_name="x.json") == []
    assert olr.public_line("Error: dead:0731 was retired") == "Error: dead:0731 was retired"
    registry = tmp_path / "registry.json"
    probes = tmp_path / "probe"
    probes.mkdir()
    before = json.dumps(_registry(**{"broken:1b": {"pending": "read_with_ollama_show_license_on_the_mac"}}))
    registry.write_text(before)
    args = ["--registry", str(registry), "--probe-dir", str(probes), "--day", "2026-10-07", "--write"]
    monkeypatch.setattr(olr, "run", _runner({"broken:1b": (1, b"", REFUSED)}))
    assert olr.main(args) == 2
    [written] = probes.glob("*.json")
    assert probe_evidence.validate_payload(json.loads(written.read_text()), current_name=written.name) == []
    written.unlink()
    registry.write_text(before)
    # وما يردّه المدقّقُ ولا يحجبه الحجبُ (هنا سطرُ الإصدار يكشف الخادم) لا يُكتب، والرمزُ 4
    leaky = _runner({"broken:1b": (1, b"", b"Error: something else\n")})

    def run(command):
        if command == ["ollama", "--version"]:
            return CompletedProcess(command, 0, b"Warning: could not connect to 127.0.0.1:11434\n", b"")
        return leaky(command)

    monkeypatch.setattr(olr, "run", run)
    assert olr.main(args) == 4
    assert list(probes.iterdir()) == [] and registry.read_text() == before
    assert "probe_evidence:private_operational_metadata" in capsys.readouterr().err
