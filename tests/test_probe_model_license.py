"""رخصةُ كلِّ نموذجٍ في أدلّة docs/probe مقروءةٌ من مصدرها (جديد-license-tagging، #301؛ ق٦٢-٨)."""
from __future__ import annotations

import json

import pytest

from tools import model_licenses as ml

READ = {"license": "apache-2.0", "source": "https://huggingface.co/org/model", "read_on": "2026-10-05"}
PENDING = {"pending": "read_with_ollama_show_license_on_the_mac"}


def _registry(historical=(), **models) -> dict:
    return {"schema_version": 1, "enforced_from": "2026-10-06", "models": models, "historical_evidence": list(historical)}


def test_every_model_the_published_evidence_names_has_a_license_entry():
    registry = json.loads(ml.REGISTRY.read_text(encoding="utf-8"))
    assert ml.findings(registry, ml.load_evidence(), ml.default_engine()) == []


def test_a_model_the_registry_does_not_list_is_named_with_its_evidence():
    evidence = {"k.json": {"date": "2026-09-01", "model": "gemma3:12b"}}
    assert ml.findings(_registry(), evidence, None) == ["model_not_in_registry:k.json:gemma3:12b"]


def test_a_read_license_carries_its_https_source_and_the_day_it_was_read():
    registry = _registry(a={"license": "mit"}, b={**READ, "source": "huggingface.co/org/model"},
                         c={**READ, "read_on": "2026-10"}, d={**READ, "license": " "}, e={**READ, "read_on": "20261005"})
    assert ml.findings(registry, {}, None) == [
        "license_missing:d", "read_on_missing:a", "read_on_missing:c", "read_on_missing:e", "source_missing:a",
        "source_missing:b"]


def test_a_pending_entry_names_its_reason_and_carries_nothing_else():
    registry = _registry(a={"pending": "later"}, b={**PENDING, "license": "mit"}, c=PENDING, d="mit")
    assert ml.findings(registry, {}, None) == [
        "entry_not_an_object:d", "pending_without_a_named_reason:a", "pending_without_a_named_reason:b"]


def test_new_evidence_cannot_name_a_model_whose_license_was_not_read():
    registry = _registry(["old.json", "new.json"], **{"qwen3.5:9b": PENDING})
    old = {"date": "2026-10-05", "engine": {"model": "qwen3.5:9b"}}
    new = {"date": "2026-10-06T08:00:00+00:00", "engine": {"model": "qwen3.5:9b"}}
    assert ml.findings(registry, {"old.json": old}, None) == []
    assert ml.findings(registry, {"new.json": new}, None) == [
        "license_not_read_before_new_evidence:new.json:qwen3.5:9b"]


def test_a_file_outside_the_historical_list_is_new_even_without_a_date():
    """اثنا عشر دليلًا تاريخيًّا بلا تاريخ، فالجِدّةُ من القائمة المقيّدة لا من حقل التاريخ وحده."""
    payload = {"model": "qwen3.5:9b"}
    assert ml.findings(_registry(["k.json"], **{"qwen3.5:9b": PENDING}), {"k.json": payload}, None) == []
    assert ml.findings(_registry(**{"qwen3.5:9b": PENDING}), {"k.json": payload}, None) == [
        "license_not_read_before_new_evidence:k.json:qwen3.5:9b"]


def test_new_evidence_states_every_license_it_relies_on():
    """ملاحظة Codex على #302: دليلٌ جديد يسمّي نموذجًا محلولًا ولا يعلن رخصتَه كان يمرّ."""
    registry = _registry(**{"microsoft/phi-4": {**READ, "license": "mit"}})
    bare = {"date": "2026-10-06", "model": "microsoft/phi-4"}
    assert ml.findings(registry, {"n.json": bare}, None) == ["license_not_stated_in_new_evidence:n.json:microsoft/phi-4"]
    wrong = {**bare, "licenses": {"microsoft/phi-4": "apache-2.0"}}
    assert ml.findings(registry, {"n.json": wrong}, None) == ["recorded_license_disagrees:n.json:microsoft/phi-4"]
    stated = {**bare, "licenses": {"microsoft/phi-4": "MIT"}}
    assert ml.findings(registry, {"n.json": stated}, None) == []


def test_new_evidence_is_read_at_every_depth_and_historical_at_its_top_level():
    """ملاحظة Codex على #302: الأدلّةُ تسمّي نماذجها في config.model وbaseline.embedder.model وproviders[].models[].model."""
    registry = _registry(["old.json"], **{"a/x": READ, "b/y": READ})
    nested = {"config": {"model": "a/x"}, "providers": [{"models": [{"model": "b/y"}]}],
              "baseline": {"embedder": {"model": "c/z"}}, "licenses": {"a/x": "apache-2.0", "b/y": "apache-2.0"}}
    assert ml.findings(registry, {"new.json": nested}, None) == ["model_not_in_registry:new.json:c/z"]
    assert ml.findings(registry, {"old.json": nested}, None) == []


def test_a_license_the_evidence_records_must_agree_with_the_registry():
    registry = _registry(["a.json", "d.json"], **{"qwen3.5:9b": READ})
    agrees = {"engine": {"model": "qwen3.5:9b", "license": "Apache-2.0"}}
    disagrees = {"engine": {"model": "qwen3.5:9b", "license": "llama3.1"}}
    assert ml.findings(registry, {"a.json": agrees}, None) == []
    assert ml.findings(registry, {"d.json": disagrees}, None) == ["recorded_license_disagrees:d.json:qwen3.5:9b"]


def test_the_default_engine_is_listed_and_never_non_commercial():
    """ق٦٢-٨: لا نموذجَ برخصةٍ غير تجاريّة داخل المنتج، والمحرّكُ الافتراضيّ داخله."""
    nc = {**READ, "license": "cc-by-nc-4.0"}
    assert ml.findings(_registry(), {}, "qwen3.5:9b") == ["default_engine_not_in_registry:qwen3.5:9b"]
    assert ml.findings(_registry(**{"qwen3.5:9b": nc}), {}, "qwen3.5:9b") == [
        "default_engine_non_commercial:qwen3.5:9b"]
    assert ml.findings(_registry(**{"qwen3.5:9b": PENDING}), {}, "qwen3.5:9b") == []


def test_the_default_engine_is_read_from_its_single_definition():
    assert ml.default_engine() == "qwen3.5:9b"


@pytest.mark.parametrize("raw, name", [
    pytest.param("ollama:gemma3:12b", "gemma3:12b", id="ollama_prefix"),
    pytest.param("qwen3.5:9b", "qwen3.5:9b", id="ollama_tag_kept"),
    pytest.param("huggingface.co/inception42/Jais-2-8B-Chat-GGUF:Q4_K_M", "inception42/Jais-2-8B-Chat-GGUF",
                 id="hf_domain_and_quant"),
    pytest.param("hf.co/inceptionai/Jais-2-8B-Chat-GGUF:Q4_K_M", "inceptionai/Jais-2-8B-Chat-GGUF", id="hf_short_domain"),
    pytest.param("meta-llama/Llama-3.1-8B-Instruct:novita", "meta-llama/Llama-3.1-8B-Instruct", id="router_provider"),
    pytest.param("microsoft/phi-4", "microsoft/phi-4", id="hf_repo_kept"),
])
def test_identifiers_are_read_in_their_canonical_form(raw, name):
    assert ml.canonical(raw) == name


def test_every_known_evidence_shape_names_its_models():
    payload = {
        "engine": {"name": "ollama:gemma3:12b"},
        "model": {"requested": "hf.co/inceptionai/x:Q4", "repo": "inception42/x", "name": "ignored"},
        "local_model": {"model": "qwen3:14b"},
        "models": [{"model": "llama3.1:8b"}, {"model": "qwen3:14b"}],
        "reviewers": {"deepseek-v4-flash:cloud": {}},
        "model_requested": "meta-llama/Llama-3.3-70B-Instruct:novita",
        "model_returned": "meta-llama/llama-3.3-70b-instruct",
    }
    assert ml.named_models(payload) == [
        "ollama:gemma3:12b", "inception42/x", "qwen3:14b", "llama3.1:8b", "deepseek-v4-flash:cloud",
        "meta-llama/Llama-3.3-70B-Instruct:novita"]
    assert ml.named_models({"models": {"deepseek-ai/DeepSeek-V3-0324": {}}}) == ["deepseek-ai/DeepSeek-V3-0324"]


def test_every_model_field_is_found_at_any_depth():
    payload = {
        "engine": {"provider": "ollama", "model": "qwen3.5:9b"},
        "config": {"model": "a/x", "embedder": {"model": "b/y"}},
        "providers": [{"models": [{"model": "c/z"}], "smoke": {"reviewers": ["d/w"]}}],
        "pairs": [{"reviewers": {"e/v": {"family": "e"}}}],
        "calls": [{"model_requested": "f/u:novita", "model_returned": "f/u"}],
        "local_model": {"model": "g:1b"},
        "environment": {"engine": "free text, refused unless registered"},
    }
    assert ml.all_named_models(payload) == [
        "qwen3.5:9b", "a/x", "b/y", "c/z", "d/w", "e/v", "f/u:novita", "g:1b", "free text, refused unless registered"]


@pytest.mark.parametrize("name, kind", [
    pytest.param("Apache-2.0", "osi", id="apache_any_case"), pytest.param("mit", "osi", id="mit"),
    pytest.param("cc-by-nc-4.0", "non_commercial", id="cc_by_nc"),
    pytest.param("cc-by-nc-sa-4.0", "non_commercial", id="cc_by_nc_sa"),
    pytest.param("llama3.1", "custom_terms", id="llama"), pytest.param("gemma", "custom_terms", id="gemma"),
])
def test_the_license_class_is_read_from_its_name_alone(name, kind):
    assert ml.license_class(name) == kind


DIGEST, OTHER_DIGEST, SIBLING, TRAINED, PTH_KIND = "a" * 64, "b" * 64, "d" * 64, "e" * 64, "f" * 64
WEIGHT = {"file": "w.pth", "sha256": DIGEST, "origin": "https://example.org/w.zip", "license": "mit",
          "license_source": "https://example.org/LICENSE", "read_on": "2026-10-05"}
OCR_EVIDENCE = {"ocr.json": {"engine": {"name": "ocr", "settings": {"models_sha256": {"w.pth": DIGEST, "v.pth": SIBLING}}}},
                "other.json": {"model": "a/model", "artifact_sha256": OTHER_DIGEST},
                "tess.json": {"engine": {"name": "ocr", "settings": {"traineddata_sha256": TRAINED, "pth_sha256": PTH_KIND}}}}


def _weights(*weights) -> dict:
    return {"ocr": {**READ, "weights": list(weights)}, "a/model": READ}


def test_a_weight_whose_digest_its_model_evidence_recorded_passes():
    assert ml.weight_findings(_weights(WEIGHT), OCR_EVIDENCE) == []
    assert ml.findings(_registry(["ocr.json", "other.json", "tess.json"], **_weights(WEIGHT)), OCR_EVIDENCE, None) == []
    changed = _weights({**WEIGHT, "sha256": "c" * 64})
    assert ml.findings(_registry(["ocr.json", "other.json", "tess.json"], **changed), OCR_EVIDENCE, None) == [
        "weight_digest_not_in_evidence:ocr:w.pth"]


@pytest.mark.parametrize("change, code", [
    pytest.param({"sha256": "c" * 64}, "weight_digest_not_in_evidence:ocr:w.pth", id="digest_changed"),
    pytest.param({"sha256": OTHER_DIGEST}, "weight_digest_not_in_evidence:ocr:w.pth", id="another_models_digest"),
    pytest.param({"sha256": SIBLING}, "weight_digest_not_in_evidence:ocr:w.pth", id="sibling_files_digest"),
    pytest.param({"sha256": TRAINED}, "weight_digest_not_in_evidence:ocr:w.pth", id="another_kinds_digest"),
    pytest.param({"sha256": PTH_KIND}, "weight_digest_not_in_evidence:ocr:w.pth", id="kind_digest_for_a_named_file"),
    pytest.param({"sha256": DIGEST.upper()}, "weight_digest_not_in_evidence:ocr:w.pth", id="digest_malformed"),
    pytest.param({"sha256": ""}, "weight_field_missing:ocr:w.pth:sha256", id="digest_omitted"),
    pytest.param({"origin": None}, "weight_field_missing:ocr:w.pth:origin", id="origin_omitted"),
    pytest.param({"origin": "http://example.org/w.zip"}, "weight_source_not_https:ocr:w.pth:origin", id="origin_http"),
    pytest.param({"license_source": "ftp://x"}, "weight_source_not_https:ocr:w.pth:license_source",
                 id="license_source_not_https"),
    pytest.param({"read_on": "05/10/2026"}, "weight_read_on_invalid:ocr:w.pth", id="read_on_invalid"),
])
def test_a_weight_whose_identity_is_not_the_measured_bytes_is_named(change, code):
    """ملاحظة Codex على #307: بصمةٌ مخطوءةٌ أو مغيَّرةٌ أو غائبة، أو أصلٌ غائب، تُلصق رخصةً ببايتاتٍ لم تُقس."""
    assert ml.weight_findings(_weights({**WEIGHT, **change}), OCR_EVIDENCE) == [code]


def test_a_digest_recorded_by_kind_binds_the_weight_of_that_kind():
    """دليلُ Tesseract يسجّل `traineddata_sha256` بلا اسم ملفّ، فيُقبل لوزنٍ امتدادُه `.traineddata` وحده."""
    trained = {**WEIGHT, "file": "ara.traineddata", "sha256": TRAINED}
    assert ml.weight_findings(_weights(trained), OCR_EVIDENCE) == []
    assert ml.weight_findings(_weights({**trained, "file": "ara.bin"}), OCR_EVIDENCE) == [
        "weight_digest_not_in_evidence:ocr:ara.bin"]


@pytest.mark.parametrize("weights", [pytest.param("w.pth", id="text"), pytest.param(["w.pth"], id="list_of_text"),
                                     pytest.param(5, id="number")])
def test_a_malformed_weights_list_is_named(weights):
    assert ml.weight_findings({"ocr": {**READ, "weights": weights}}, OCR_EVIDENCE) == ["weights_malformed:ocr"]


def test_a_malformed_registry_is_refused_by_name():
    assert ml.findings({"models": {}, "historical_evidence": []}, {}, None) == ["registry_malformed"]
    assert ml.findings({"enforced_from": "2026-10-06", "models": [], "historical_evidence": []}, {}, None) == [
        "registry_malformed"]
    assert ml.findings({"enforced_from": "2026-10-06", "models": {}}, {}, None) == ["registry_malformed"]
    assert ml.findings({"enforced_from": "2026-10-06", "models": {}, "historical_evidence": [1]}, {}, None) == [
        "registry_malformed"]


def test_the_cli_fails_on_a_finding_and_names_it(tmp_path, capsys):
    registry = tmp_path / "r.json"
    registry.write_text(json.dumps(_registry(["x.json"], **{"qwen3.5:9b": PENDING})), encoding="utf-8")
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "x.json").write_text(json.dumps({"model": "gemma4:latest"}), encoding="utf-8")
    assert ml.main(["--registry", str(registry), "--probe", str(probe)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed" and report["findings"] == ["model_not_in_registry:x.json:gemma4:latest"]
    (probe / "x.json").write_text(json.dumps({"model": "qwen3.5:9b"}), encoding="utf-8")
    assert ml.main(["--registry", str(registry), "--probe", str(probe)]) == 0
    assert json.loads(capsys.readouterr().out)["pending"] == ["qwen3.5:9b"]
