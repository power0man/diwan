"""رخصةُ كلِّ نموذجٍ في أدلّة docs/probe مقروءةٌ من مصدرها (جديد-license-tagging، #301؛ ق٦٢-٨)."""
from __future__ import annotations

import hashlib
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
    # وزنٌ مسجَّلٌ للمحرّك برخصةٍ غير تجاريّة تحت غلافٍ مفتوح (ملاحظة Codex على #307)
    nc_weight = {**READ, "weights": [{**WEIGHT, "license": "cc-by-nc-4.0"}]}
    found = ml.findings(_registry(**{"qwen3.5:9b": nc_weight}), {}, "qwen3.5:9b")
    assert "default_engine_weight_non_commercial:qwen3.5:9b:w.pth" in found
    assert not any(code.startswith("default_engine") for code in ml.findings(
        _registry(**{"qwen3.5:9b": {**READ, "weights": [WEIGHT]}}), {}, "qwen3.5:9b")), "وزنٌ برخصةٍ مفتوحة لا يُسمّى"


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
LICENSE_TEXT, OTHER_TEXT = "9" * 64, "8" * 64
WEIGHT = {"file": "w.pth", "sha256": DIGEST, "origin": "https://example.org/w.zip", "license": "mit",
          "license_source": "https://example.org/LICENSE", "read_on": "2026-10-05", "license_text_sha256": LICENSE_TEXT}
PROVENANCE = {**{field: WEIGHT[field] for field in ml.PROVENANCE_FIELDS}, "license_text_sha256": LICENSE_TEXT}
OCR_EVIDENCE = {"ocr.json": {"engine": {"name": "ocr", "settings": {"models_sha256": {"w.pth": DIGEST, "v.pth": SIBLING}},
                                        "weight_provenance": [PROVENANCE]}},
                "other.json": {"model": "a/model", "artifact_sha256": OTHER_DIGEST},
                "tess.json": {"engine": {"name": "ocr", "settings": {"traineddata_sha256": TRAINED, "pth_sha256": PTH_KIND}}}}


def _weights(*weights) -> dict:
    return {"ocr": {**READ, "weights": list(weights)}, "a/model": READ}


def test_a_weight_whose_digest_its_model_evidence_recorded_passes():
    assert ml.weight_findings(_weights(WEIGHT), OCR_EVIDENCE) == []
    assert ml.findings(_registry(["ocr.json", "other.json", "tess.json"], **_weights(WEIGHT)), OCR_EVIDENCE, None) == []
    changed = _weights({**WEIGHT, "sha256": "c" * 64})
    assert ml.findings(_registry(["ocr.json", "other.json", "tess.json"], **changed), OCR_EVIDENCE, None) == [
        "weight_digest_not_in_evidence:ocr:w.pth", "weight_provenance_not_in_evidence:ocr:w.pth"]


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
    pytest.param({"license_text_sha256": None}, "weight_field_missing:ocr:w.pth:license_text_sha256",
                 id="license_text_omitted"),
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


def test_two_weights_of_one_kind_are_bound_only_by_their_file_names():
    """ملاحظة Codex على #307: لـEasyOCR وزنا `.pth` برخصتين؛ فبصمتان تحت نوعٍ بلا اسم ملفّ تُقبلان لأيٍّ من الملفّين فتتبادلان
    الرخصة. فالنوعُ يربط الوزنَ الوحيد من نوعه، ووزنان من نوعٍ واحد يُطلب لكلٍّ منهما ملفُّه."""
    pair = (WEIGHT, {**WEIGHT, "file": "v.pth", "sha256": SIBLING})
    by_kind = {"k.json": {"engine": {"name": "ocr", "settings": {"pth_sha256": DIGEST, "sha256s": {"pth_sha256": SIBLING}}}}}
    assert ml.weight_findings(_weights(*pair), by_kind) == [
        "weight_digest_not_in_evidence:ocr:w.pth", "weight_digest_not_in_evidence:ocr:v.pth"]
    assert ml.weight_findings(_weights(*pair), OCR_EVIDENCE) == [], "ملفّاهما مسمّيان في دليل OCR فيُقبلان"
    assert sorted(ml.weight_findings(_weights(*pair), {**OCR_EVIDENCE, **by_kind}, frozenset({"k.json"}))) == [
        "weight_not_measured_in_new_evidence:k.json:ocr:v.pth", "weight_not_measured_in_new_evidence:k.json:ocr:w.pth"]


def test_a_wrapped_weight_is_bound_by_its_unwrapped_kind():
    """ملاحظة Codex على #307: `checkpoint.pth.tar` نوعُه `pth` في الربط أيضًا، فبصمةُ `pth_sha256` تربطه."""
    wrapped = {**WEIGHT, "file": "checkpoint.pth.tar"}
    by_kind = {"k.json": {"engine": {"name": "ocr", "settings": {"pth_sha256": DIGEST}}}}
    assert ml.weight_findings(_weights(wrapped), by_kind) == []
    assert ml.weight_findings(_weights(wrapped), by_kind, frozenset({"k.json"})) == []


@pytest.mark.parametrize("evidence, registered, found", [
    pytest.param({"m.json": {"runs": [{"engine": {"name": "ocr", "settings": {"models_sha256": {"w.pth": DIGEST}}}},
                                      {"engine": {"name": "ocr2", "settings": {"models_sha256": {"w.pth": SIBLING}}}}]}},
                 DIGEST, ["weight_digest_not_in_evidence:ocr2:w.pth"], id="engine_subtrees"),
    pytest.param({"m.json": {"runs": [{"model": "ocr", "w.pth": DIGEST}, {"model": "ocr2", "w.pth": SIBLING}]}},
                 DIGEST, ["weight_digest_not_in_evidence:ocr2:w.pth"], id="model_fields"),
    pytest.param({"m.json": {"models": {"ocr": {"w.pth": DIGEST}, "ocr2": {"w.pth": SIBLING}}}},
                 DIGEST, ["weight_digest_not_in_evidence:ocr2:w.pth"], id="model_map"),
    pytest.param({"m.json": {"models": {"ocr": {"w.pth": DIGEST}, "ocr2": {"w.pth": SIBLING}}}}, SIBLING, [],
                 id="own_subtree_passes"),
    pytest.param({"m.json": {"model": "ocr2", "details": {"w.pth": DIGEST}}}, DIGEST, [], id="inherited_owner"),
    pytest.param({"m.json": {"w.pth": DIGEST, "note": {"model": "ocr2"}}}, DIGEST,
                 ["weight_digest_not_in_evidence:ocr2:w.pth"], id="no_owner_on_the_path"),
    pytest.param({"m.json": {"models": [{"name": "ocr", "w.pth": DIGEST}, {"name": "ocr2", "w.pth": SIBLING}]}}, DIGEST,
                 ["weight_digest_not_in_evidence:ocr2:w.pth"], id="model_list"),
    pytest.param({"m.json": {"models": [{"name": "ocr", "w.pth": DIGEST}, {"name": "ocr2", "w.pth": SIBLING}]}}, SIBLING,
                 [], id="model_list_owns_its_item"),
])
def test_a_digest_belongs_only_to_the_model_whose_subtree_records_it(evidence, registered, found):
    """ملاحظة Codex على #307: دليلٌ يسمّي نموذجين لا تُنسب بصمةُ أحدهما إلى الآخر."""
    models = {"ocr2": {**READ, "weights": [{**WEIGHT, "sha256": registered}]}}
    assert ml.weight_findings(models, evidence) == found


def test_new_evidence_names_every_weight_it_records_in_the_registry():
    """ملاحظة Codex على #307: دليلٌ جديد يسجّل لنموذجٍ مقيَّدٍ وزنًا (بملفّه أو بنوعه) ليس في قيوده يُسمّى، والتاريخيُّ لا."""
    new = {"new.json": {"engine": {"name": "ocr", "settings": {
        "models_sha256": {"w.pth": DIGEST, "x.pth": SIBLING, "o01.jpg": SIBLING}, "traineddata_sha256": TRAINED}}}}
    expected = ["weight_not_registered:new.json:ocr:x.pth", "weight_not_registered:new.json:ocr:traineddata"]
    assert ml.weight_findings(_weights(WEIGHT), new, frozenset({"new.json"})) == expected
    assert ml.weight_findings(_weights(WEIGHT), new) == [], "الدليلُ التاريخيّ لا يُطالَب"
    trained = {**WEIGHT, "file": "ara.traineddata", "sha256": TRAINED}
    assert ml.weight_findings(_weights(WEIGHT, trained), new, frozenset({"new.json"})) == expected[:1]
    registry = _registry(["ocr.json", "other.json", "tess.json"], **_weights(WEIGHT))
    assert ml.findings(registry, {**OCR_EVIDENCE, **new}, None) == [
        "license_not_stated_in_new_evidence:new.json:ocr", *sorted(expected)]


@pytest.mark.parametrize("file, weight", [
    pytest.param("checkpoint.pth.tar", True, id="weight_in_an_archive"),
    pytest.param("w.safetensors.gz", True, id="compressed_weight"),
    pytest.param("spm.model", True, id="sentencepiece_model"),
    pytest.param("model.keras", True, id="keras_model"),
    pytest.param("graph.pb", True, id="tensorflow_graph"),
    pytest.param("w.unheard_of", True, id="unknown_suffix"),
    pytest.param("bundle.tar", True, id="bare_archive"),
    pytest.param("o01.jpg", False, id="bank_image"),
    pytest.param("data.model.json", False, id="json_named_model"),
    pytest.param("scores.CSV", False, id="data_in_capitals"),
])
def test_a_weight_file_is_any_file_not_known_to_be_data(file, weight):
    """ملاحظات Codex على #307: `checkpoint.pth.tar` و`.model` و`.keras` و`.pb` كانت تُعدّ بياناتٍ فتمرّ بايتاتٌ بلا قيدٍ ولا رخصة؛
    فكلُّ ما ليس بياناتٍ معروفةً وزنٌ، ويُغلق عند الشكّ."""
    assert ml.is_weight_file(file) is weight
    new = {"new.json": {"engine": {"name": "ocr", "settings": {"models_sha256": {"w.pth": DIGEST, file: SIBLING}}}}}
    assert ml.weight_findings(_weights(WEIGHT), new, frozenset({"new.json"})) == (
        [f"weight_not_registered:new.json:ocr:{file}"] if weight else [])


@pytest.mark.parametrize("payload, found", [
    pytest.param({"engine": {"name": "ocr"}}, ["weight_not_measured_in_new_evidence:new.json:ocr:w.pth"],
                 id="no_digest"),
    pytest.param({"engine": {"name": "ocr"}, "w.pth": DIGEST}, [], id="digest_beside_its_engine"),
    pytest.param({"runs": [{"model": "ocr"}, {"model": "a/model", "w.pth": DIGEST}]},
                 ["weight_not_measured_in_new_evidence:new.json:ocr:w.pth",
                  "weight_not_registered:new.json:a/model:w.pth"], id="digest_for_another_model"),
    pytest.param({"engine": {"name": "ocr", "settings": {"models_sha256": {"v.pth": DIGEST}}}},
                 ["weight_not_measured_in_new_evidence:new.json:ocr:w.pth", "weight_not_registered:new.json:ocr:v.pth"],
                 id="digest_for_another_file"),
    pytest.param({"engine": {"name": "ocr", "settings": {"pth_sha256": DIGEST}}}, [], id="digest_by_kind"),
    pytest.param({"engine": {"name": "ocr", "settings": {"models_sha256": {"w.pth": DIGEST}}}}, [], id="digest_by_file"),
    pytest.param({"model": "a/model"}, [], id="model_without_registered_weights"),
    pytest.param({"models": [{"name": "ocr", "models_sha256": {"w.pth": DIGEST}}]}, [], id="digest_under_a_models_list"),
])
def test_new_evidence_naming_a_model_with_registered_weights_records_their_digests(payload, found):
    """ملاحظة Codex على #307: دليلٌ جديد يسمّي نموذجًا مقيَّدَ الأوزان بلا بصماتها يُسمّى، فلا تمرّ بايتاتٌ مستبدَلةٌ اتّكالًا
    على دليلٍ أقدم سجّلها؛ والتاريخيُّ لا يُطالَب."""
    evidence = {**OCR_EVIDENCE, "new.json": payload}
    assert sorted(ml.weight_findings(_weights(WEIGHT), evidence, frozenset({"new.json"}))) == found
    assert ml.weight_findings(_weights(WEIGHT), evidence) == []


@pytest.mark.parametrize("change, evidence, found", [
    pytest.param({}, OCR_EVIDENCE, [], id="recorded_passes"),
    pytest.param({"origin": "https://evil.invalid/unrelated.bin"}, OCR_EVIDENCE,
                 ["weight_provenance_not_in_evidence:ocr:w.pth"], id="origin_replaced"),
    pytest.param({"license_source": "https://evil.invalid/LICENSE"}, OCR_EVIDENCE,
                 ["weight_provenance_not_in_evidence:ocr:w.pth"], id="license_source_replaced"),
    pytest.param({}, {"p.json": {"model": "ocr", "weight_provenance": [{**PROVENANCE, "sha256": SIBLING}]}},
                 ["weight_provenance_not_in_evidence:ocr:w.pth"], id="record_of_other_bytes"),
    pytest.param({}, {"p.json": {"model": "ocr", "weight_provenance": [{**PROVENANCE, "file": "v.pth"}]}},
                 ["weight_provenance_not_in_evidence:ocr:w.pth"], id="record_of_another_file"),
    pytest.param({}, {"p.json": {"model": "a/model", "weight_provenance": [PROVENANCE]}},
                 ["weight_provenance_not_in_evidence:ocr:w.pth"], id="record_of_another_model"),
    pytest.param({}, {"p.json": {"weight_provenance": [PROVENANCE]}},
                 ["weight_provenance_not_in_evidence:ocr:w.pth"], id="record_without_an_owner"),
    pytest.param({}, {"p.json": {"model": "ocr", "weight_provenance": [
        {k: v for k, v in PROVENANCE.items() if k != "license_source"}]}},
                 ["weight_provenance_not_in_evidence:ocr:w.pth"], id="record_missing_a_field"),
])
def test_a_weight_whose_provenance_no_evidence_recorded_is_named(change, evidence, found):
    """ملاحظة Codex على #307: أصلٌ أو مصدرُ رخصةٍ صحيحُ الصيغة لا علاقة له بالبايتات المقيسة كان يمرّ. فالوزنُ يطابقه سجلُّ
    مصدرٍ في شجرة نموذجه بملفّه وبصمته وأصله ومصدر رخصته معًا."""
    assert ml.provenance_findings(_weights({**WEIGHT, **change}), evidence) == found


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


ASR_WEIGHT = {**WEIGHT, "file": "ggml.bin"}


@pytest.mark.parametrize("named, weights, digest, found", [
    pytest.param("asr", [], DIGEST, ["weight_not_registered:new.json:asr:model"], id="unregistered_model_artifact"),
    pytest.param("asr", [], _sha_text(json.dumps("asr")), [], id="json_name_hash"),
    pytest.param("asr", [], _sha_text("asr"), [], id="plain_name_hash"),
    pytest.param("ollama:asr", [], _sha_text(json.dumps("ollama:asr")), [], id="alias_name_hash"),
    pytest.param("asr", [ASR_WEIGHT], DIGEST, [], id="sole_registered_weight"),
    pytest.param("asr", [ASR_WEIGHT], SIBLING,
                 ["weight_digest_not_in_evidence:asr:ggml.bin", "weight_not_measured_in_new_evidence:new.json:asr:ggml.bin",
                  "weight_not_registered:new.json:asr:model"], id="other_bytes"),
    pytest.param("asr", [ASR_WEIGHT, {**WEIGHT, "file": "v.bin", "sha256": SIBLING}], DIGEST,
                 ["weight_digest_not_in_evidence:asr:ggml.bin", "weight_digest_not_in_evidence:asr:v.bin",
                  "weight_not_measured_in_new_evidence:new.json:asr:ggml.bin",
                  "weight_not_measured_in_new_evidence:new.json:asr:v.bin"], id="two_weights_not_bound_by_model_kind"),
])
def test_a_model_artifact_digest_is_a_registered_weight_or_the_models_name_hash(named, weights, digest, found):
    """ملاحظة Codex على #307: `model_sha256` يكتبه مسارُ ASR لملفّ الوزن، فكان يمرّ بلا قيدٍ لأن `model` ليس نوعَ وزن.
    فهو يُطالَب بقيدٍ ببصمته ما لم يكن بصمةَ اسم النموذج (كما يكتبها `evaluation/capabilities.py`)، ويربط وزنَ النموذج الوحيد."""
    models = {"asr": {**READ, "weights": weights}}
    evidence = {"new.json": {"model": named, "model_sha256": digest}}
    assert sorted(ml.weight_findings(models, evidence, frozenset({"new.json"}))) == found
    assert [f for f in ml.weight_findings(models, evidence) if "new.json" in f] == [], "الدليلُ التاريخيّ لا يُطالَب"


@pytest.mark.parametrize("registered, recorded, found", [
    pytest.param(LICENSE_TEXT, LICENSE_TEXT, [], id="recorded_text_passes"),
    pytest.param(None, LICENSE_TEXT, ["weight_provenance_not_in_evidence:ocr:w.pth"], id="weight_without_a_text_digest"),
    pytest.param(OTHER_TEXT, LICENSE_TEXT, ["weight_provenance_not_in_evidence:ocr:w.pth"], id="registered_text_changed"),
    pytest.param(LICENSE_TEXT, None, ["weight_provenance_not_in_evidence:ocr:w.pth"], id="record_without_its_text"),
])
def test_a_weights_license_text_digest_is_the_one_its_provenance_read(registered, recorded, found):
    """ملاحظتا Codex على #307: بصمةُ نصّ رخصة الوزن كانت تُكتب ولا تُقارن، ثم كانت اختياريّة فلا يُربط نصُّ وزنٍ بلا بصمة.
    فهي لازمةٌ لكلّ وزنٍ وجزءٌ من ربط مصدره."""
    weight = {k: v for k, v in WEIGHT.items() if k != "license_text_sha256"}
    weight.update({} if registered is None else {"license_text_sha256": registered})
    record = {k: v for k, v in PROVENANCE.items() if k != "license_text_sha256"}
    record.update({} if recorded is None else {"license_text_sha256": recorded})
    assert ml.provenance_findings(_weights(weight), {"p.json": {"model": "ocr", "weight_provenance": [record]}}) == found


@pytest.mark.parametrize("evidence, found", [
    pytest.param({"p.json": {"model": "ocr", "license_provenance": {"source": READ["source"], "license_text_sha256": LICENSE_TEXT}}},
                 [], id="recorded_text_passes"),
    pytest.param({"p.json": {"model": "ocr", "license_provenance": {"source": READ["source"], "license_text_sha256": OTHER_TEXT}}},
                 ["license_text_not_in_evidence:ocr"], id="other_text_recorded"),
    pytest.param({"p.json": {"model": "ocr", "license_provenance": {"source": "https://example.org/other", "license_text_sha256": LICENSE_TEXT}}},
                 ["license_text_not_in_evidence:ocr"], id="text_of_another_source"),
    pytest.param({"p.json": {"model": "a/model", "license_provenance": {"source": READ["source"], "license_text_sha256": LICENSE_TEXT}}},
                 ["license_text_not_in_evidence:ocr"], id="text_of_another_model"),
    pytest.param({}, ["license_text_not_in_evidence:ocr"], id="no_evidence"),
])
def test_a_models_license_text_digest_is_one_read_from_its_source(evidence, found):
    """ملاحظة Codex على #307: بصمةُ نصّ رخصة النموذج 64 محرفًا تُكتب؛ فهي تُطابَق بنصٍّ قيس من مصدره المقيَّد في شجرة نموذجه."""
    models = {"ocr": {**READ, "license_text_sha256": LICENSE_TEXT}, "a/model": READ}
    assert ml.provenance_findings(models, evidence) == found


@pytest.mark.parametrize("kind, digest, weights, found", [
    pytest.param("checkpoint", DIGEST, [], ["weight_not_registered:new.json:asr:checkpoint"], id="checkpoint_unregistered"),
    pytest.param("model_artifact", DIGEST, [], ["weight_not_registered:new.json:asr:model_artifact"],
                 id="model_artifact_unregistered"),
    pytest.param("checkpoint", DIGEST, [{**WEIGHT, "file": "ggml.bin"}], [], id="checkpoint_of_a_registered_weight"),
    pytest.param("suite", DIGEST, [], [], id="known_data_kind"),
    pytest.param("model_version", DIGEST, [], [], id="model_version_is_data"),
])
def test_an_artifact_digest_of_an_unrecognized_kind_needs_its_registered_weight(kind, digest, weights, found):
    """ملاحظة Codex على #307: `checkpoint_sha256` و`model_artifact_sha256` كانا يُجمعان ثم يُتجاهلان لأنهما خارج قائمة الأوزان.
    فكلُّ نوعٍ ليس بياناتٍ معروفةً يُطالَب بقيدٍ ببصمته."""
    models = {"asr": {**READ, "weights": weights}}
    evidence = {"new.json": {"model": "asr", f"{kind}_sha256": digest}}
    assert [f for f in ml.weight_findings(models, evidence, frozenset({"new.json"})) if "new.json" in f] == found


@pytest.mark.parametrize("payload", [
    pytest.param({"engine": {"name": "asr"}, "checkpoint_sha256": DIGEST}, id="engine_named_by_an_object"),
    pytest.param({"model": {"repo": "asr", "revision": "main"}, "checkpoint_sha256": DIGEST}, id="model_named_by_an_object"),
])
def test_a_model_named_by_an_object_owns_the_digests_beside_it(payload):
    """ملاحظة Codex على #307: `{"engine": {"name": …}, "checkpoint_sha256": …}` يسمّي نموذجًا يعرفه `all_named_models`، وكان
    حسابُ المالك لا يقرأ إلا الاسمَ النصّيّ، فتُسجَّل البصمةُ بجانبه بلا مالكٍ وتُهمَل صامتةً."""
    models = {"asr": {**READ, "weights": []}}
    assert ml.weight_findings(models, {"new.json": payload}, frozenset({"new.json"})) == [
        "weight_not_registered:new.json:asr:checkpoint"]


@pytest.mark.parametrize("name, weights, found", [
    pytest.param("checkpoint", [], ["weight_not_registered:new.json:asr:checkpoint"], id="unregistered_extensionless_file"),
    pytest.param("checkpoint", [{**WEIGHT, "file": "checkpoint"}], [], id="registered_extensionless_file"),
    pytest.param("scores.csv", [], [], id="data_file"),
])
def test_an_extensionless_name_in_a_digest_map_is_a_file(name, weights, found):
    """ملاحظة Codex على #307: مفتاحٌ بلا لاحقة في خريطة بصماتٍ (`{"models_sha256": {"checkpoint": …}}`) كان يُسقط صامتًا، فيمرّ
    وزنٌ بلا قيد. فكلُّ مفتاحٍ في خريطة `*_sha256` اسمُ ملفّ، ويحكم عليه `is_weight_file`."""
    models = {"asr": {**READ, "weights": weights}}
    evidence = {"new.json": {"model": "asr", "models_sha256": {name: DIGEST}}}
    assert [f for f in ml.weight_findings(models, evidence, frozenset({"new.json"})) if "new.json" in f] == found


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
