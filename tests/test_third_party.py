"""THIRD-PARTY.md يطابق القفلَ وسجلَّ رخص النماذج (جديد-license-tagging، #301)."""
from __future__ import annotations

import json

import pytest

from tools import third_party as tp

MODELS = {"a/model": {"license": "mit", "source": "https://huggingface.co/a/model", "read_on": "2026-10-05"},
          "tag:1b": {"pending": "read_with_ollama_show_license_on_the_mac"}}
LOCK = [("numpy", "2.0.0"), ("torch", "2.4.0+cpu")]


def _text(packages=None, models=None) -> str:
    rows = [(n, v, "BSD-3-Clause") for n, v in LOCK] if packages is None else packages
    return tp.render(rows, MODELS if models is None else models)


def test_the_published_file_matches_the_lock_and_the_model_registry():
    models = json.loads(tp.MODELS.read_text(encoding="utf-8"))["models"]
    assert tp.check(tp.OUT.read_text(encoding="utf-8"), tp.locked_packages(), models) == [], \
        "أعد توليده: python3 tools/third_party.py --write"


def test_a_rendered_file_checks_clean_against_what_rendered_it():
    assert tp.check(_text(), LOCK, MODELS) == []
    piped = _text(packages=[("numpy", "2.0.0", "MIT | Apache-2.0"), ("torch", "2.4.0+cpu", "BSD")])
    assert tp.check(piped, LOCK, MODELS) == [] and "MIT / Apache-2.0" in piped


def test_a_locked_package_missing_from_the_file_or_at_another_version_is_named():
    text = _text(packages=[("numpy", "1.9.0", "BSD-3-Clause")])
    assert tp.check(text, LOCK, MODELS) == [
        "package_not_listed:numpy==2.0.0", "package_not_listed:torch==2.4.0+cpu", "package_not_locked:numpy==1.9.0"]
    # خانةُ رخصةٍ فارغة لا يقرؤها نمطُ الصفّ، فتُعدّ الحزمةُ غيرَ مدرجة: يُغلق عند الشكّ
    blank = _text(packages=[("numpy", "2.0.0", " "), ("torch", "2.4.0+cpu", "BSD")])
    assert tp.check(blank, LOCK, MODELS) == ["package_not_listed:numpy==2.0.0"]


def test_a_model_missing_or_with_another_license_is_named():
    assert tp.check(_text(models={}), LOCK, MODELS) == ["model_not_listed:a/model", "model_not_listed:tag:1b"]
    other = {**MODELS, "a/model": {**MODELS["a/model"], "license": "apache-2.0"}}
    assert tp.check(_text(models=other), LOCK, MODELS) == ["model_license_differs:a/model"]
    resolved = {**MODELS, "tag:1b": {**MODELS["a/model"]}}
    # ما انتقل بين الانتظار والقراءة تخالف رخصتُه ومصدرُه كلاهما
    both = ["model_license_differs:tag:1b", "model_source_differs:tag:1b"]
    assert tp.check(_text(), LOCK, resolved) == both
    assert tp.check(_text(models=resolved), LOCK, MODELS) == both
    assert tp.check(_text(models={**MODELS, "x/y": MODELS["a/model"]}), LOCK, MODELS) == ["model_not_in_registry:x/y"]


def test_a_model_source_or_read_date_that_changed_in_the_registry_is_named():
    """ملاحظة Codex على #302: تصحيحُ مصدر الرخصة أو تاريخ قراءتها في السجلّ وحده كان لا يُرى."""
    moved = {**MODELS, "a/model": {**MODELS["a/model"], "source": "https://huggingface.co/a/model-v2"}}
    reread = {**MODELS, "a/model": {**MODELS["a/model"], "read_on": "2026-11-01"}}
    assert tp.check(_text(), LOCK, moved) == ["model_source_differs:a/model"]
    assert tp.check(_text(), LOCK, reread) == ["model_source_differs:a/model"]


def test_a_package_source_edited_by_hand_is_named():
    edited = _text().replace("https://pypi.org/project/numpy/2.0.0/", "https://example.org/numpy/")
    assert tp.check(edited, LOCK, MODELS) == ["package_source_differs:numpy==2.0.0"]


def test_a_weight_carries_its_own_license_and_attribution_and_a_change_is_named():
    """ملاحظة Codex على #307: كاشفُ CRAFT يحمل رخصةَ MIT من مستودعه وإسنادَه، لا رخصةَ EasyOCR الذي وزّعه."""
    weight = {"file": "w.pth", "license": "mit", "license_source": "https://github.com/up/r/blob/c/LICENSE",
              "read_on": "2026-10-05", "attribution": "Copyright (c) Up"}

    def weighted(**change):
        return {**MODELS, "a/model": {**MODELS["a/model"], "weights": [{**weight, **change}]}}

    text = _text(models=weighted())
    assert tp.check(text, LOCK, weighted()) == []
    assert "| `a/model` | `w.pth` | mit؛ Copyright (c) Up | https://github.com/up/r/blob/c/LICENSE (2026-10-05) |" in text
    assert tp.weight_license({**weight, "attribution": None}) == "mit"
    assert tp.check(_text(), LOCK, weighted()) == ["weight_not_listed:a/model/w.pth"]
    assert tp.check(text, LOCK, MODELS) == ["weight_not_in_registry:a/model/w.pth"]
    assert tp.check(text, LOCK, weighted(license="apache-2.0")) == ["weight_license_differs:a/model/w.pth"]
    assert tp.check(text, LOCK, weighted(attribution="Copyright (c) Other")) == ["weight_license_differs:a/model/w.pth"]
    assert tp.check(text, LOCK, weighted(license_source="https://github.com/o/r/blob/c/LICENSE")) == [
        "weight_source_differs:a/model/w.pth"]
    assert tp.check(text, LOCK, weighted(read_on="2026-11-01")) == ["weight_source_differs:a/model/w.pth"]


def test_a_model_or_weight_listed_twice_is_named():
    """ملاحظة Codex على #307: صفٌّ مناقضٌ قبل الصحيح كان يُطوى في القاموس فيمرّ الفحص."""
    weight = {"file": "w.pth", "license": "mit", "license_source": "https://github.com/up/r/blob/c/LICENSE",
              "read_on": "2026-10-05", "attribution": "Copyright (c) Up"}
    models = {**MODELS, "a/model": {**MODELS["a/model"], "weights": [weight]}}
    text = _text(models=models)
    weight_row = next(line for line in text.splitlines() if line.startswith("| `a/model` | `w.pth` |"))
    model_row = next(line for line in text.splitlines() if line.startswith("| `a/model` |") and line != weight_row)
    forged_weight = weight_row.replace("mit؛", "apache-2.0؛")
    forged_model = model_row.replace("| mit |", "| apache-2.0 |")
    assert forged_weight != weight_row and forged_model != model_row
    assert tp.check(text.replace(weight_row, f"{forged_weight}\n{weight_row}"), LOCK, models) == [
        "weight_listed_twice:a/model/w.pth"]
    assert tp.check(text.replace(model_row, f"{forged_model}\n{model_row}"), LOCK, models) == [
        "model_listed_twice:a/model"]


def test_rows_outside_their_section_do_not_count():
    text = _text().replace("## الحزم", "## غيرها")
    assert "package_not_listed:numpy==2.0.0" in tp.check(text, LOCK, MODELS)


@pytest.mark.parametrize("info, name", [
    pytest.param({"license_expression": "MIT", "license": "BSD"}, "MIT", id="expression_first"),
    pytest.param({"license": "BSD-3-Clause"}, "BSD-3-Clause", id="short_license_field"),
    pytest.param({"license": "Copyright ...\nfull text", "classifiers": [
        "License :: OSI Approved :: Apache Software License", "Programming Language :: Python"]},
        "Apache Software License", id="full_text_falls_to_classifiers"),
    pytest.param({"license": "", "classifiers": []}, tp.UNSTATED, id="unstated_named"),
])
def test_the_license_is_read_as_the_package_published_it(info, name):
    assert tp.license_of(info) == name


def test_the_local_version_is_read_at_its_public_version(monkeypatch):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"info": {"license_expression": "BSD-3-Clause"}}).encode()

    monkeypatch.setattr(tp.urllib.request, "urlopen", lambda url, timeout: seen.append(url) or Response())
    assert tp.fetch("torch", "2.4.0+cpu") == {"license_expression": "BSD-3-Clause"}
    assert seen == ["https://pypi.org/pypi/torch/2.4.0/json"]


def test_the_lock_is_read_without_the_project_and_with_every_platform_version():
    locked = tp.locked_packages()
    assert ("diwan", "0.1.0") not in locked and all(name != "diwan" for name, _ in locked)
    assert len([name for name, _ in locked if name == "torch"]) == 2


def test_write_keeps_licenses_already_read_and_reads_only_new_pairs(tmp_path, monkeypatch):
    out = tmp_path / "THIRD-PARTY.md"
    out.write_text(_text(packages=[("numpy", "2.0.0", "BSD-3-Clause")]), encoding="utf-8")
    monkeypatch.setattr(tp, "OUT", out)
    monkeypatch.setattr(tp, "locked_packages", lambda: LOCK)
    monkeypatch.setattr(tp, "MODELS", tmp_path / "m.json")
    (tmp_path / "m.json").write_text(json.dumps({"models": MODELS}), encoding="utf-8")
    fetched = []
    monkeypatch.setattr(tp, "fetch", lambda name, version: fetched.append(name) or {"license_expression": "MIT"})
    assert tp.main(["--write"]) == 0
    assert fetched == ["torch"]
    assert tp.listed_licenses(out.read_text(encoding="utf-8")) == {
        ("numpy", "2.0.0"): "BSD-3-Clause", ("torch", "2.4.0+cpu"): "MIT"}
    assert tp.main(["--write", "--refresh"]) == 0
    assert fetched == ["torch", "numpy", "torch"]


def test_the_cli_check_fails_on_a_finding(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tp, "OUT", tmp_path / "THIRD-PARTY.md")
    assert tp.main(["--check"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
