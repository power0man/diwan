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


def _runner(outputs: dict[str, tuple[int, bytes, bytes]]):
    def run(command):
        if command == ["ollama", "--version"]:
            return CompletedProcess(command, 0, b"ollama version is 0.35.0\n", b"")
        if command == ["ollama", "list"]:
            return CompletedProcess(command, 0, LIST.encode(), b"")
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
