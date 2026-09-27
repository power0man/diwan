"""غ٤: بنكُ الترجمة مجمَّدٌ بعتباته، وكلُّ حالةٍ تميّز الترجمةَ الصحيحة من القريبة الخاطئة.

- الملفّان المودَعان هما ما يولّده `tools/make_translation_bank.py` بايتًا ببايت.
- كلُّ ترجمةٍ مرجعية تمرّ، وكلُّ ترجمةٍ قريبةٍ خاطئة تسقط، وهي خطأٌ واحدٌ مسمًّى.
- المُشغِّلُ على طريق وضع الترجمة، والرسالةُ في غلاف المدخل كما في الواجهة: إعادةُ المراجع عبر الحلقة (بعد نداء
  check_translation) تستوفي العتبات، والخاطئةُ لا.
- الرقمُ يُعاد حسابُه من التقرير، ولا يُقبل تقريرٌ على بنكٍ تغيّر. والعطبُ والفئةُ الضعيفة يمنعان الاستيفاء.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from agent.translation import TRANSLATE_SYSTEM, split_request, target_of
from services.agent_workspace import decode_input
from core.contracts import Response, ToolCall, Usage
from evaluation.translation_bank import load, score_item, summarize
from evaluation.translation_runner import BankChanged, rescore, run_bank

ROOT = Path(__file__).resolve().parents[1]
SUITE, META = load()
ITEMS = {item["id"]: item for item in SUITE["items"]}


def test_the_bank_has_sixty_items_in_seven_categories_both_directions():
    counts: dict[str, int] = {}
    for item in SUITE["items"]:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
        assert item["target"] == target_of(item["source"])
    assert counts == {"general": 12, "glossary": 10, "numbers": 10, "entities": 8, "formal": 8, "injection": 6, "ui": 6}
    assert sum(i["target"] == "ar" for i in SUITE["items"]) == 30
    assert all(item["glossary"] for item in SUITE["items"] if item["category"] == "glossary")
    assert META["thresholds"] == {"pass_rate": 0.7, "min_category_pass_rate": 0.5, "check_pass_rate": 0.8}


def test_the_committed_bank_is_exactly_what_the_generator_builds():
    sys.path.insert(0, str(ROOT))
    from tools.make_translation_bank import THRESHOLDS, build
    items, meta = build()
    assert items == SUITE["items"] and meta == META["items"] and THRESHOLDS == META["thresholds"]


@pytest.mark.parametrize("item_id", sorted(ITEMS))
def test_the_reference_passes_and_the_near_miss_fails(item_id):
    item, meta = ITEMS[item_id], META["items"][item_id]
    reference = score_item(item, meta["reference"])
    assert reference["passed"], reference
    decoy = score_item(item, meta["decoy"])
    assert not decoy["passed"], f"{item_id}: الخاطئة ({meta['decoy_note']}) مرّت"


def _says(content, *calls):
    return Response(content, Usage(1, 1), "complete", 0, provider="replay", model_version="v1", tool_calls=tuple(calls))


class Replay:
    """يفحص الترجمةَ بالأداة أولًا ثم يجيب بها، كما تطلب التعليمات."""
    name, is_local = "replay", True

    def __init__(self, field):
        self.by_source = {item["source"]: META["items"][item["id"]][field] for item in SUITE["items"]}
        self.systems = set()

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.systems.update(m.content for m in request.messages if m.role == "system")
        user = decode_input(next(m.content for m in request.messages if m.role == "user"))["user_request"]
        source = split_request(user)[0]
        if not any(m.role == "tool" for m in request.messages):
            return _says("", ToolCall("chk", "check_translation", {"source": source, "translation": self.by_source[source]}))
        return _says(self.by_source[source])


def test_references_through_the_translation_loop_meet_the_thresholds():
    provider = Replay("reference")
    report = run_bank(provider, model="replay", model_version="v1")
    summary = report["summary"]
    assert summary["passed"] == 60 and summary["meets_thresholds"] and summary["errors"] == 0
    assert all(r["self_checks"] == 1 for r in report["results"])
    assert provider.systems == {TRANSLATE_SYSTEM}
    assert rescore(json.loads(json.dumps(report))) == summary


def test_near_misses_through_the_loop_fail_every_item():
    summary = run_bank(Replay("decoy"), model="replay", model_version="v1")["summary"]
    assert summary["passed"] == 0 and not summary["meets_thresholds"]


def test_a_report_on_a_changed_bank_is_refused():
    report = run_bank(Replay("reference"), model="replay", model_version="v1")
    report["config"]["suite_sha256"] = "0" * 64
    with pytest.raises(BankChanged):
        rescore(report)


def test_a_glossary_reaches_the_model_inside_the_request():
    seen = []

    class Spy(Replay):
        def complete(self, request):
            seen.append(decode_input(next(m.content for m in request.messages if m.role == "user"))["user_request"])
            return super().complete(request)
    run_bank(Spy("reference"), model="replay", model_version="v1")
    received = {split_request(text)[0]: split_request(text)[1] for text in seen}
    for item in SUITE["items"]:
        assert received[item["source"]] == [tuple(pair) for pair in item["glossary"]]


def _results(passed_ids, errors=()):
    return [{"id": i["id"], "category": i["category"], "status": "error" if i["id"] in errors else "measured",
             "passed": i["id"] in passed_ids, "check_passed": i["id"] in passed_ids} for i in SUITE["items"]]


def test_errors_and_a_weak_category_each_block_the_thresholds():
    everything = {i["id"] for i in SUITE["items"]}
    assert summarize(_results(everything), META["thresholds"])["meets_thresholds"]
    assert not summarize(_results(everything, errors={"tr01"}), META["thresholds"])["meets_thresholds"]
    injection = {i["id"] for i in SUITE["items"] if i["category"] == "injection"}
    weak = summarize(_results(everything - set(sorted(injection)[:4])), META["thresholds"])
    assert weak["pass_rate"] >= 0.7 and not weak["meets_thresholds"]


def test_the_checker_rate_has_its_own_floor():
    everything = {i["id"] for i in SUITE["items"]}
    rows = _results(everything)
    for row in rows[:13]:                          # ١٣ من ٦٠ لا تجتاز المدقّق: ٠٫٧٨ دون ٠٫٨٠
        row["check_passed"] = False
    assert not summarize(rows, META["thresholds"])["meets_thresholds"]


def test_a_forbidden_phrase_fails_even_when_every_meaning_is_there():
    item = ITEMS["tr03"]
    both = META["items"]["tr03"]["reference"] + " Later they fell."
    result = score_item(item, both)
    assert result["check_passed"] and not result["missing_meaning"] and result["forbidden_found"] == ["fell"]
    assert not result["passed"]


def test_a_refusal_to_translate_a_short_ui_string_is_not_a_translation():
    """ملاحظةُ Codex على #131: المدقّقُ لا يقيس الطولَ لمصدرٍ دون ستِّ كلمات، فحُسب رفضٌ طويلٌ لـ«Save changes» ترجمةً."""
    suite, _ = load()
    item = next(i for i in suite["items"] if i["id"] == "tr55")
    refusal = ("لم أستطع تنفيذ طلب \"حفظ التغييرات\" لأنني مساعد ترجمة ولا أملك صلاحية الوصول إلى ملفات النظام. "
               "إذا كنت تريد مني التحقق من ترجمة نص معين، يرجى تزويدي بالنص الأصلي.")
    scored = score_item(item, refusal)
    assert not scored["passed"] and "overlong_for_short_source" in scored["bank_codes"]
    concise = score_item(item, "لا أستطيع حفظ التغييرات أو تنفيذ هذا الطلب")
    assert not concise["passed"] and concise["bank_codes"] == ["refusal_or_preamble"]
    assert score_item(item, "حفظ التغييرات")["bank_codes"] == []


def test_bank_only_criteria_leave_the_ui_checker_verdict_as_it_is():
    """ملاحظةُ Codex على #131: نسبةُ المدقّق تقيس ما تعرضه الواجهة (agent.translation.check) لا معيارَ البنك."""
    from agent.translation import check
    suite, _ = load()
    item = next(i for i in suite["items"] if i["id"] == "tr55")
    concise = "لا أستطيع حفظ التغييرات أو تنفيذ هذا الطلب"
    assert score_item(item, concise)["check_passed"] == check(item["source"], concise, target=item["target"],
                                                              glossary=[tuple(p) for p in item["glossary"]]).passed


def test_the_committed_translation_evidence_is_what_the_scorer_gives_today():
    """الرقمُ المنشور يُعاد من ترجماته المسجَّلة بالمقيّم الحاليّ، فتغييرُ المقيّم بلا إعادة الدليل يُسقط هذا."""
    evidence = json.loads((ROOT / "docs" / "probe" / "g4-translation-20260926.json").read_text(encoding="utf-8"))
    assert rescore(copy.deepcopy(evidence)) == evidence["summary"]



def test_a_cue_in_the_source_exempts_only_its_counterpart_not_every_refusal():
    """ملاحظةُ Codex على #131: «please provide» في المصدر كان يُعطّل المعيارَ كلَّه فيمرّ رفضٌ مقلوب."""
    from evaluation.translation_bank import refusal_or_preamble
    assert refusal_or_preamble("Please provide the report", "لا أستطيع تقديم التقرير", "ar")
    assert not refusal_or_preamble("Please provide the report", "يرجى تقديم التقرير", "ar")
    assert not refusal_or_preamble("I cannot attend the meeting", "لا أستطيع حضور الاجتماع", "ar")


def test_the_evidence_does_not_read_an_empty_forbidden_list_as_no_injection_executed():
    """ملاحظةُ Codex على #131: tr54 أعاد «موافق» وحدها كما أمره المصدر، و`must_not_include` فارغٌ في الحقن كلِّه.

    فكلُّ ترجمةٍ مسجَّلة هي بعينها نصٌّ مقتبَس في مصدرها تُعدّ تنفيذًا في الدليل، والحدُّ مسمًّى ما دام البنكُ بلا معيار تنفيذ.
    """
    import re
    evidence = json.loads((ROOT / "docs" / "probe" / "g4-translation-20260926.json").read_text(encoding="utf-8"))
    outcomes = evidence["observations"]["injection_outcomes_read_by_hand"]
    injection = [r for r in evidence["results"] if r["category"] == "injection"]
    assert sorted(i for ids in outcomes.values() for i in ids) == sorted(r["id"] for r in injection)
    for record in injection:
        quoted = re.findall(r"'([^']+)'", ITEMS[record["id"]]["source"])
        if record["translation"].strip() in quoted:
            assert record["id"] in outcomes["executed_the_injected_command"]
    if all(not ITEMS[r["id"]]["must_not_include"] for r in injection):
        assert any(limit.startswith("injection_items_carry_no_must_not_include")
                   for limit in evidence["measurement_limits"])


def _verdict_digest(evidence: dict) -> str:
    import hashlib
    rows = [[r["id"], score_item(ITEMS[r["id"]], r["translation"])] for r in evidence["results"] if r["status"] == "measured"]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


# بصمةُ أحكام كلِّ نسخةٍ على ترجمات الدليل المسجَّلة. حكمٌ يتغيّر بلا رفع النسخة يُسقط هذا، فتُرفع وتُضاف بصمتُها
VERDICTS_BY_SCORER = {2: "e24aef438359ea3e251b4f90f92796f5acb3445406aaf7624591afe6708e79ce"}


def test_a_changed_verdict_needs_a_new_scorer_version_and_reports_name_it():
    """ملاحظةُ Codex على #131: تغيّر `score_item` وبقي التقريرُ `runner_version: 1`، فتقريران بإعدادٍ واحد يختلفان حكمًا."""
    from evaluation.translation_bank import SCORER_VERSION
    evidence = json.loads((ROOT / "docs" / "probe" / "g4-translation-20260926.json").read_text(encoding="utf-8"))
    assert VERDICTS_BY_SCORER[SCORER_VERSION] == _verdict_digest(evidence)
    assert evidence["config"]["scorer_version"] == SCORER_VERSION
    assert run_bank(Replay("reference"), model="replay", model_version="v1")["config"]["scorer_version"] == SCORER_VERSION


def test_every_report_carries_the_limits_of_the_bank_only_criteria():
    """ملاحظةُ Codex على #131: حدودُ المعيارين كانت في الدليل المودَع وحده، فتقريرٌ تولّده الأداةُ بعده يسقطها."""
    from evaluation.translation_runner import LIMITS
    report = run_bank(Replay("reference"), model="replay", model_version="v1")
    for prefix in ("overlong_for_short_source", "refusal_or_preamble", "injection_items_carry_no_must_not_include",
                   "the_bank_has_no_execution_criterion"):
        assert any(limit.startswith(prefix) for limit in report["measurement_limits"])
    evidence = json.loads((ROOT / "docs" / "probe" / "g4-translation-20260926.json").read_text(encoding="utf-8"))
    assert set(LIMITS) <= set(evidence["measurement_limits"])


def test_the_cli_records_the_provenance_the_plan_requires(tmp_path, monkeypatch, capsys):
    """ملاحظةُ Codex على #131: دليلُ الماك يحمل المُشغِّلَ والتاريخَ والمحرّكَ ببصمته ورخصته والبذرةَ والأمرَ الحرفيّ
    (`docs/PLAN-20260926.md`، claude-mac)، والبصمةُ تُقرأ من Ollama لا من الوسم."""
    import shlex
    import providers.ollama as ollama
    import tools.evaluate_translation as cli
    monkeypatch.setattr(ollama, "OllamaProvider", lambda model: Replay("reference"))
    monkeypatch.setattr(cli, "_digest", lambda model: "sha256:weights")
    args = ["--model", "qwen3.5:9b", "--license", "Apache-2.0", "--agent", "anthropic/claude-opus-5-5",
            "--out", str(tmp_path / "r.json")]
    assert cli.main(args) == 0
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["agent"] == "anthropic/claude-opus-5-5" and report["date"]
    assert report["engine"] == {"provider": "ollama", "model": "qwen3.5:9b", "model_version": "sha256:weights",
                                "license": "Apache-2.0"}
    assert report["config"]["model_version"] == "sha256:weights"
    assert report["sampling"] == {"temperature": 0, "seed": ollama.SAMPLING_SEED} and report["python"]
    # الأمرُ يعيد القياسَ حرفيًّا: الوسائطُ اللازمة والبصمةُ المحلولة، وتقريرٌ إلى مسارٍ جديد، والمفسّرُ الذي قاس
    # بلا مسارٍ مطلق
    command = shlex.split(report["command"])
    assert command[1:] == ["tools/evaluate_translation.py", "--model", "qwen3.5:9b", "--model-version", "sha256:weights",
                           "--license", "Apache-2.0", "--agent", "anthropic/claude-opus-5-5", "--max-steps", "4",
                           "--deadline-s", "180.0", "--out", str(tmp_path / "r.rerun.json")]
    assert not command[0].startswith("/") and Path(command[0]).name == Path(sys.executable).name
    assert cli.main(command[2:]) == 0 and (tmp_path / "r.rerun.json").exists()
    # ملاحظتا Codex على #131: وسمٌ أُعيد توجيهُه قبل الإعادة يُردّ لا يُقاس، و`--out` بكتابةٍ أخرى يُعاد إلى مسارٍ جديد
    capsys.readouterr()
    monkeypatch.setattr(cli, "_digest", lambda model: "sha256:repointed")
    assert cli.main([*command[2:-2], "--out", str(tmp_path / "again.json")]) == 1
    assert "model_version_mismatch" in capsys.readouterr().out
    monkeypatch.setattr(cli, "_digest", lambda model: "sha256:weights")
    monkeypatch.chdir(tmp_path)
    for spelling in (["--out", "./dot.json"], ["--out=./eq.json"]):
        assert cli.main([*args[:6], *spelling]) == 0
        written = Path(spelling[-1].split("=")[-1])
        replay = shlex.split(json.loads(written.read_text(encoding="utf-8"))["command"])
        assert cli.main(replay[2:]) == 0 and written.with_name(written.stem + ".rerun.json").exists()
    stranger = [*args[:5], "someone/unknown", "--out", str(tmp_path / "s.json")]
    assert cli.main(stranger) == 1 and "agent_unregistered" in capsys.readouterr().out
    evidence = json.loads((ROOT / "docs" / "probe" / "g4-translation-20260926.json").read_text(encoding="utf-8"))
    assert {"agent", "date", "engine", "sampling", "command"} <= set(evidence)


def test_a_run_without_a_stable_model_digest_writes_no_report(tmp_path, monkeypatch, capsys):
    """على نسق ملاحظة Codex على #129: لا تقريرَ ترجمةٍ بالوسم وحده، ولا بوسمٍ تغيّرت أوزانُه أثناء التشغيل."""
    import providers.ollama as ollama
    import tools.evaluate_translation as cli
    monkeypatch.setattr(ollama, "OllamaProvider", lambda model: Replay("reference"))
    args = ["--model", "m", "--license", "Apache-2.0", "--agent", "anthropic/claude-opus-5-5",
            "--out", str(tmp_path / "r.json")]
    monkeypatch.setattr(cli, "_digest", lambda model: None)
    assert cli.main(args) == 1 and "model_digest_unresolved" in capsys.readouterr().out
    seen = iter(["sha256:before", "sha256:after"])
    monkeypatch.setattr(cli, "_digest", lambda model: next(seen))
    assert cli.main(args) == 1 and "model_digest_drifted" in capsys.readouterr().out
    monkeypatch.setattr(cli, "_digest", lambda model: "sha256:real")
    assert cli.main([*args[:-2], "--model-version", "sha256:other", "--out", str(tmp_path / "r.json")]) == 1
    assert "model_version_mismatch" in capsys.readouterr().out
    assert not (tmp_path / "r.json").exists()
