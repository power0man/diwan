"""قراءةُ رخص وسوم Ollama على الماك أداةً لا يدًا، وما لم يُقرأ يبقى منتظِرًا بما حدث (جديد-license-tagging، #301)."""
from __future__ import annotations

import hashlib
import json
from subprocess import CompletedProcess

from tools import model_licenses as ml
from tools import ollama_license_read as olr

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


def test_apache_text_with_its_appendix_filled_in_is_named_from_its_title_lines():
    # ملاحظة Codex الرابعة على #301: `TITLES` لم يكن فيه Apache، فنصُّ Apache-2.0 بملحقٍ مملوء (حالة `qwen3:14b` في دليل
    # ٦ أكتوبر) لا تطابقه بصمةُ SPDX ولا عنوان، فيُقيَّد `UNNAMED` وهو رخصةٌ معروفة
    unfilled = (olr.ROOT / "LICENSE").read_bytes()
    assert olr.name_license(unfilled) == ("apache-2.0", "spdx_body_digest_via_weight_provenance_identify_license")
    filled = unfilled.replace(b"Copyright [yyyy] [name of copyright owner]", b"Copyright 2024 Alibaba Cloud")
    assert filled != unfilled
    assert olr.name_license(filled) == ("apache-2.0", "title_lines_as_printed")
    # والعنوانُ سطران فلا يُسمّى من «Apache License» وحدها، ولا من نسخةٍ أخرى
    assert olr.name_license(b"Apache License\nVersion 1.1\n...") == (None, None)
    assert olr.name_license(b"The Apache Software License, Version 1.1\n...") == (None, None)
    evidence = olr.probe(["qwen3:14b"], "2026-10-07", _runner({"qwen3:14b": (0, filled, b"")},
                                                               listing=(0, b"NAME ID SIZE\nqwen3:14b bdbd181c33f2 9.3 GB\n", b"")))
    assert evidence["licenses"] == {"qwen3:14b": "apache-2.0"} and evidence["unresolved_readings"] == []
    assert evidence["models"]["qwen3:14b"]["license_named_by"] == "title_lines_as_printed"


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
