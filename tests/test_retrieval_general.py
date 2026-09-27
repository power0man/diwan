"""غ٣: البنكُ العامّ مجمَّدٌ ببصمته، والهجينُ دمجٌ حقيقيٌّ للقناتين، والتقريرُ يُعاد بلا نموذج.

المُضمِّنُ في هذه الاختبارات `HashEmbedder` الحتميّ غيرُ الدلاليّ، فلا يشهد برقمٍ دلاليّ؛
الرقمُ الحيّ في `docs/probe/g3-hybrid-vs-bm25-<التاريخ>.json`.
"""
from __future__ import annotations

import json

import pytest

from core.vector_retrieval import HashEmbedder
from evaluation import ablation
from evaluation.retrieval_general import (BANK, MIN_QUERIES, ROOT, RetrievalBankError, cluster_bootstrap, comparisons,
                                          arm_rows_from_channels, design_effect, hit_interval, load_bank, paired,
                                          rows_from_report, rrf, run, summaries, wilson)

EVIDENCE = ROOT / "docs" / "probe" / "g3-hybrid-vs-bm25-20260927.json"


def test_the_bank_is_frozen_general_and_large_enough():
    bank = load_bank()
    assert len(bank["queries"]) >= MIN_QUERIES
    assert {q["type"] for q in bank["queries"]} == {"lexical", "paraphrase"}
    assert len({d["topic"] for d in bank["documents"]}) >= 10


def test_an_edited_bank_is_refused_before_measuring(tmp_path):
    edited = tmp_path / "bank.json"
    data = json.loads(BANK.read_text(encoding="utf-8"))
    data["queries"][0]["text"] += " "
    edited.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RetrievalBankError) as err:
        load_bank(edited)
    assert err.value.code == "bank_not_frozen"


def test_rrf_lifts_a_passage_both_channels_found_above_either_channels_first():
    fused = rrf([["a", "b", "c"], ["c", "d"]])
    assert fused[0] == "c", "المقطعُ الذي وجدته القناتان يتقدّم"
    assert set(fused) == {"a", "b", "c", "d"}, "الهجينُ يضمّ ما وجدته كلُّ قناة"


def test_the_full_run_reports_three_arms_and_the_hybrid_is_the_fusion_of_the_two():
    report = run(load_bank(), HashEmbedder())
    rows = report["rows"]
    assert set(report["arms"]) == {"bm25", "vectors", "hybrid"}
    assert all(len(rows[arm]) == len(rows["bm25"]) >= MIN_QUERIES for arm in rows)
    differs = sum(h["top5"] != b["top5"] for h, b in zip(rows["hybrid"], rows["bm25"]))
    assert differs > 0, "الهجينُ لا يختلف عن BM25 في أي استعلام: الدمجُ معطَّل"
    on, off = paired(rows["hybrid"], rows["bm25"])
    assert [r["id"] for r in on] == [r["id"] for r in off]


def test_wilson_interval_is_inside_zero_one_and_contains_the_rate():
    low, high = wilson(90, 120)
    assert 0 <= low < 0.75 < high <= 1


def _recorded():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    rows = rows_from_report(evidence["rows"], load_bank())
    return evidence, rows, summaries(rows)


def test_the_published_numbers_are_recomputed_from_the_recorded_rows():
    """الدليلُ يُعاد من صفوفه المسجَّلة والبنك وحدهما، بلا مُضمِّن: الأذرعُ ومجالاتُها والمقارناتُ وحكمُ البروتوكول."""
    evidence, rows, arms = _recorded()
    assert evidence["arms"] == arms
    assert evidence["comparisons"] == comparisons(rows, arms)
    # وصفوفُ الأذرع الثلاث تُعاد من رتبتَي القناتين المسجَّلتين، فلا صفَّ يخالف رتبتَه
    assert evidence["rows"] == arm_rows_from_channels(evidence["channels"], load_bank())


def test_a_full_run_records_both_channels_so_the_fusion_is_recomputed_without_a_model():
    report = run(load_bank(), HashEmbedder())
    channels = {qid: {name: " ".join(ids) for name, ids in pair.items()} for qid, pair in report["channels"].items()}
    recorded = {arm: [{k: r[k] for k in ("id", "type", "rank", "hit_at_5", "ndcg_at_10")} for r in rows]
                for arm, rows in report["rows"].items()}
    assert arm_rows_from_channels(channels, load_bank()) == recorded


def test_rrf_ties_go_to_the_earlier_channel_as_in_the_product():
    """ملاحظةُ Codex على #132: `HybridRetriever` يرتّب بالنقاط وحدها ترتيبًا مستقرًّا، فالتعادلُ لـBM25 لا للمعرّف."""
    assert rrf([["b"], ["a"]]) == ["b", "a"]
    assert rrf([["a"], ["b"]]) == ["a", "b"]


def test_a_sample_where_everything_succeeds_keeps_its_uncertainty():
    """ملاحظةُ Codex على #132: ٦٠/٦٠ نُشرت بمجال [1.0, 1.0]؛ Wilson على الحجم الفعليّ يُبقي الحدَّ الأدنى دون الواحد،
    واستعلاما المقطع المتطابقان يُعدّان واحدًا (أثرُ التصميم ٢)."""
    singles = [{"cluster": f"p{i}", "hit_at_5": True} for i in range(60)]
    assert hit_interval(singles)[0] < 0.95 and hit_interval(singles)[1] == 1.0
    twins = [{"cluster": f"p{i}", "hit_at_5": i % 2 == 0} for i in range(60) for _ in range(2)]
    assert design_effect(twins, lambda r: float(r["hit_at_5"])) == 2.0
    assert hit_interval(twins) == wilson(30, 60)
    # وملاحظتُه الثانية: ستون مقطعًا نجح استعلاماها كلاهما لا تُقدِّر ارتباطًا (المقامُ صفر)، فتُعدّ ستين لا مئةً وعشرين
    perfect = [{"cluster": f"p{i}", "hit_at_5": True} for i in range(60) for _ in range(2)]
    assert design_effect(perfect, lambda r: float(r["hit_at_5"])) == 2.0
    assert hit_interval(perfect) == wilson(60, 60)


def test_these_synthetic_arms_never_yield_a_protocol_decision(monkeypatch):
    """ملاحظاتُ Codex على #132: قاعدةُ ك٤٦ تقرّر قناةَ المتّجهات في `HybridRetriever` المنتج، وهذه الأذرعُ ليست هو
    (BM25 بلا توسيعٍ صرفيّ، والهجينُ بلا exact/any، والمقاطعُ عناقيدُ لا يراها الحكم). فلا حكمَ منها ولو صار المكوّنُ
    جاهزًا، ولا يُستدعى `ablation.judge`."""
    _, rows, arms = _recorded()
    before = comparisons(rows, arms)
    ready = ablation.protocol()
    ready["components"]["vectors"]["status"] = "ready"
    monkeypatch.setattr(ablation, "protocol", lambda: ready)
    monkeypatch.setattr(ablation, "judge", lambda *a, **k: pytest.fail("حكمٌ من أذرعٍ ليست المنتج"))
    result = comparisons(rows, arms)
    assert {v["protocol"]["decision"] for v in result.values()} == {"not_applied"}
    # ولا تُقرأ حالةُ البروتوكول الحيّة، فانتقالُه لا يغيّر ما يُعاد من الصفوف
    assert result == before


def test_a_report_without_the_embedder_digest_is_refused_before_any_embedding(tmp_path, monkeypatch, capsys):
    """ملاحظةُ Codex على #132: تعذّرُ `/api/tags` كان يُنتج تقريرًا بالوسم وحده، فلا يُعرف أيُّ أوزانٍ ضمّنت."""
    import tools.evaluate_retrieval as cli
    monkeypatch.setattr(cli, "OllamaEmbedder", lambda model: pytest.fail("تضمينٌ قبل التحقّق من البصمة"))
    monkeypatch.setattr(cli, "_digest", lambda model: None if model == "bge-m3" else "sha256:weights")
    args = ["--embedder", "qwen3-embedding:0.6b", "--license", "Apache-2.0", "--baseline", "bge-m3", "--agent", "anthropic/claude-opus-5-5",
            "--out", str(tmp_path / "r.json")]
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "code": "embedder_digest_unresolved",
                                                   "models": ["bge-m3"]}
    assert not (tmp_path / "r.json").exists()


def test_intervals_resample_gold_passages_not_queries():
    """ملاحظةُ Codex على #132: استعلاما المقطع الواحد مترابطان، فإعادةُ المعاينة بالمقطع.
    ستون مقطعًا لكلٍّ استعلامان متطابقان: مجالُها مجالُ ستين قيمة، أعرضُ من مئةٍ وعشرين مستقلّة بنحو √٢."""
    values = [float(i % 2) for i in range(60)]
    twins = [{"cluster": f"p{i}", "v": v} for i, v in enumerate(values) for _ in range(2)]
    singles = [{"cluster": f"q{i}", "v": row["v"]} for i, row in enumerate(twins)]
    width = lambda ci: ci[1] - ci[0]
    assert width(cluster_bootstrap(twins, lambda r: r["v"])) > 1.3 * width(cluster_bootstrap(singles, lambda r: r["v"]))


def test_an_embedder_repointed_during_the_run_writes_no_report(tmp_path, monkeypatch, capsys):
    """ملاحظةُ Codex على #132: وسمٌ أُعيد توجيهُه أثناء التضمين كان يُنسب إلى البصمة الأخيرة."""
    import tools.evaluate_retrieval as cli
    monkeypatch.setattr(cli, "OllamaEmbedder", lambda model: HashEmbedder())
    calls: dict[str, int] = {}

    def digest(model):
        calls[model] = calls.get(model, 0) + 1
        return "sha256:before" if model == "bge-m3" or calls[model] == 1 else "sha256:after"

    monkeypatch.setattr(cli, "_digest", digest)
    args = ["--embedder", "qwen3-embedding:0.6b", "--license", "Apache-2.0", "--baseline", "bge-m3", "--agent", "anthropic/claude-opus-5-5",
            "--out", str(tmp_path / "r.json")]
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "code": "embedder_digest_drifted",
                                                   "models": ["qwen3-embedding:0.6b"]}
    assert not (tmp_path / "r.json").exists()


def test_a_run_without_the_bge_m3_baseline_is_refused(tmp_path, monkeypatch):
    """ملاحظتا Codex على #132: مواصفةُ غ٣ تقارن بـbge-m3، وكان إغفالُ `--baseline` أو تسميةُ مُضمِّنٍ آخر يكتب تقريرًا
    ناجحًا بلا خطّ الأساس المسجَّل."""
    import tools.evaluate_retrieval as cli
    monkeypatch.setattr(cli, "OllamaEmbedder", lambda model: pytest.fail("تضمينٌ بلا خطّ أساس"))
    monkeypatch.setattr(cli, "_digest", lambda model: "sha256:weights")
    for extra in ([], ["--baseline", "qwen3-embedding:0.6b"], ["--baseline", "nomic-embed-text"]):
        with pytest.raises(SystemExit) as exit_:
            cli.main(["--embedder", "qwen3-embedding:0.6b", "--license", "Apache-2.0", *extra,
                      "--agent", "anthropic/claude-opus-5-5",
                      "--out", str(tmp_path / "r.json")])
        assert exit_.value.code == 2
    assert not (tmp_path / "r.json").exists()


def test_every_report_carries_the_engine_license_and_bootstrap_it_was_measured_with(tmp_path, monkeypatch):
    """ملاحظةُ Codex على #132: التقريرُ كان يكتب `engine: null` بلا رخصةٍ ولا إعداد إعادة المعاينة، فلا تُعاد المجالاتُ
    منه وحده إن تغيّرت القيمُ الافتراضية. والدليلُ المنشور يحملها، ومجالاتُه تُعاد بها."""
    import tools.evaluate_retrieval as cli
    from evaluation.retrieval_general import BOOTSTRAP
    monkeypatch.setattr(cli, "OllamaEmbedder", lambda model: HashEmbedder())
    monkeypatch.setattr(cli, "_digest", lambda model: "sha256:" + model)
    out = tmp_path / "r.json"
    assert cli.main(["--embedder", "qwen3-embedding:0.6b", "--license", "Apache-2.0", "--baseline", "bge-m3",
                     "--agent", "anthropic/claude-opus-5-5", "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["engine"] == {"provider": "ollama-local", "model": "qwen3-embedding:0.6b",
                                "digest": "sha256:qwen3-embedding:0.6b", "license": "Apache-2.0"}
    assert report["config"]["bootstrap"] == BOOTSTRAP
    assert report["baseline"]["embedder"] == {"model": "bge-m3", "digest": "sha256:bge-m3", "license": "MIT"}
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["config"]["bootstrap"] == BOOTSTRAP
    assert evidence["engine"]["license"] == "Apache-2.0" and evidence["baseline"]["embedder"]["license"] == "MIT"
    assert evidence["engine"]["digest"] == evidence["config"]["embedder"]["digest"]
