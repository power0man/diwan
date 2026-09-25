"""المراجعة الخارجية لبنوك القياس: تُختبر بمراجعَين مزيَّفين، بلا شبكة.

التشغيلُ الحيّ على الماك (AGENTS.md، المهمة ك٥). وهنا يُثبت ما يجب أن يصمد أيًّا كان
المراجع: لا يسقط ملفٌّ صمتًا، ولا يُرسل المحجوب، ولا يُقبل مراجعٌ من عائلةٍ ممنوعة،
وκ صحيحةٌ حسابًا.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from evaluation.external_review import (ENGINE_FAMILY, check_reviewers, cohen_kappa,
                                        review_bank, reviewer_family, summarize)
from evaluation.multi_system_review import AutomaticReviewError

ROOT = Path(__file__).resolve().parent.parent
BRIEF = ROOT / "docs" / "REVIEWER-BRIEF.md"
REVIEWERS = ["deepseek-v4-flash:cloud", "mistral-large-3:675b-cloud"]


def _case(case_id: str) -> dict:
    return {"case_id": case_id, "capability": "arithmetic",
            "messages": [{"role": "user", "content": "كم 2+2؟"}],
            "reference": "4", "rubric": ["الجواب 4"], "checks": [], "critical": False}


def _bank(tmp_path: Path, ids=("c1", "c2", "c3")) -> Path:
    bank = tmp_path / "bank"
    suite = {"schema_version": 1, "suite_id": "t", "split": "development",
             "description": "d", "cases": [_case(i) for i in ids]}
    path = bank / "open" / "tier_a" / "kimi_t_a_001.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
    (bank / "open" / "tier_a" / "kimi_t_a_001.meta.json").write_text("{}", encoding="utf-8")
    return bank


def _judgment(i, reference="correct", rubric="sufficient"):
    return {"id": i, "reference": reference, "rubric": rubric,
            "my_answer": "4", "reason": "حسبتُها", "fix": None}


class Fake:
    """يردّ لكل نموذجٍ بما كُتب له، ويعدّ النداءات."""

    def __init__(self, replies: dict[str, list]):
        self.replies = {m: list(r) for m, r in replies.items()}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, model, system, user, schema):
        self.calls.append((model, user))
        reply = self.replies[model].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply, ensure_ascii=False) if isinstance(reply, dict) else reply


def _ok(ids, **kw):
    return {"judgments": [_judgment(i, **kw) for i in ids]}


# ————— الطريق الصادق —————

def test_two_reviewers_are_called_independently_and_recorded(tmp_path):
    bank = _bank(tmp_path)
    fake = Fake({REVIEWERS[0]: [_ok(["c1", "c2", "c3"])],
                 REVIEWERS[1]: [_ok(["c1", "c2", "c3"])]})
    counts = review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)
    assert counts == {"reviewed": 2, "skipped": 0, "failed": 0}
    assert [m for m, _ in fake.calls] == REVIEWERS, "نداءٌ لكل مراجعٍ لملفّ البنك وحده"
    for _model, user in fake.calls:
        assert json.loads(user)["suite_id"] == "t", "لا يُرسل إلا ملفُّ البنك نفسه"
    record = json.loads((bank / "reviews" / "deepseek-v4-flash_cloud" / "tier_a"
                         / "kimi_t_a_001.json").read_text(encoding="utf-8"))
    assert record["error"] is None and len(record["judgments"]) == 3
    assert record["family"] == "deepseek"


def test_the_brief_sent_is_the_reviewer_part_without_the_owner_note(tmp_path):
    bank = _bank(tmp_path)
    seen = []

    def spy(model, system, user, schema):
        seen.append(system)
        return json.dumps(_ok(["c1", "c2", "c3"]))
    review_bank(bank, REVIEWERS, spy, brief_path=BRIEF)
    assert seen[0].startswith("## من أنت")
    assert "للمالك" not in seen[0]


def test_a_second_run_does_not_call_again(tmp_path):
    bank = _bank(tmp_path)
    review_bank(bank, REVIEWERS, Fake({m: [_ok(["c1", "c2", "c3"])] for m in REVIEWERS}),
                brief_path=BRIEF)
    again = Fake({m: [] for m in REVIEWERS})
    counts = review_bank(bank, REVIEWERS, again, brief_path=BRIEF)
    assert counts == {"reviewed": 0, "skipped": 2, "failed": 0} and again.calls == []


# ————— لا يسقط ملفٌّ صمتًا —————

def test_a_reply_missing_an_id_is_retried_once_then_recorded_as_error(tmp_path):
    bank = _bank(tmp_path)
    partial = _ok(["c1", "c2"])
    fake = Fake({REVIEWERS[0]: [partial, partial], REVIEWERS[1]: [_ok(["c1", "c2", "c3"])]})
    counts = review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)
    assert counts["failed"] == 1
    record = json.loads((bank / "reviews" / "deepseek-v4-flash_cloud" / "tier_a"
                         / "kimi_t_a_001.json").read_text(encoding="utf-8"))
    assert record["judgments"] is None, "لا يُكتب حكمٌ ناقص"
    assert record["error"] == "judgment_ids_mismatch"
    assert len(record["attempts"]) == 2
    assert "judgment_ids_mismatch" in fake.calls[1][1], "المحاولة الثانية تحمل رمز الرفض"


def test_a_retry_that_succeeds_is_accepted(tmp_path):
    bank = _bank(tmp_path)
    fake = Fake({REVIEWERS[0]: ["ليس JSON", _ok(["c1", "c2", "c3"])],
                 REVIEWERS[1]: [_ok(["c1", "c2", "c3"])]})
    assert review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)["failed"] == 0


@pytest.mark.parametrize("judgments", [
    [_judgment("c1"), _judgment("c2"), _judgment("c3"), _judgment("c4")],   # زائد
    [_judgment("c1"), _judgment("c1"), _judgment("c2"), _judgment("c3")],   # مكرّر
    [_judgment("c1"), _judgment("c2"), _judgment("c3", reference="maybe")],  # قيمة
    [{**_judgment("c1"), "reason": " "}, _judgment("c2"), _judgment("c3")],  # سببٌ فارغ
])
def test_malformed_judgments_are_refused(tmp_path, judgments):
    bank = _bank(tmp_path)
    bad = {"judgments": judgments}
    fake = Fake({REVIEWERS[0]: [bad, bad], REVIEWERS[1]: [_ok(["c1", "c2", "c3"])]})
    assert review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)["failed"] == 1


def test_a_transport_failure_is_an_error_not_a_verdict(tmp_path):
    bank = _bank(tmp_path)
    down = AutomaticReviewError("transport_error")
    fake = Fake({REVIEWERS[0]: [down, down], REVIEWERS[1]: [_ok(["c1", "c2", "c3"])]})
    assert review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)["failed"] == 1
    summary = summarize(bank)
    assert summary["errors"] == [{"model": REVIEWERS[0], "file": "tier_a/kimi_t_a_001.json",
                                  "error": "transport_error"}]


# ————— المحجوب لا يُرسل —————

def test_a_sealed_file_is_never_sent(tmp_path):
    bank = _bank(tmp_path)
    leaked = bank / "open" / "sealed" / "kimi_x.json"
    leaked.parent.mkdir(parents=True)
    leaked.write_text((bank / "open" / "tier_a" / "kimi_t_a_001.json").read_text(
        encoding="utf-8"), encoding="utf-8")
    fake = Fake({m: [] for m in REVIEWERS})
    with pytest.raises(AutomaticReviewError) as exc:
        review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)
    assert exc.value.code == "sealed_never_reviewed_externally"
    assert fake.calls == [], "لا نداءَ واحدًا قبل الرفض"


# ————— قاعدة العائلات —————

@pytest.mark.parametrize("model,code", [
    ("qwen3.5:9b", "reviewer_is_engine_family"),
    ("kimi-k2.6:cloud", "reviewer_is_author_family"),
    ("gpt-oss:120b-cloud", "reviewer_is_developer_family"),
    ("gemini-3-pro", "reviewer_is_developer_family"),
    ("gemma4:latest", "reviewer_is_developer_family"),
    ("claude-opus", "reviewer_is_developer_family"),
    ("mystery-model:cloud", "reviewer_family_unknown"),
])
def test_forbidden_reviewer_families_are_refused(model, code):
    with pytest.raises(AutomaticReviewError) as exc:
        check_reviewers(["deepseek-v4-flash:cloud", model])
    assert exc.value.code == code


def test_two_reviewers_of_one_family_are_refused():
    with pytest.raises(AutomaticReviewError) as exc:
        check_reviewers(["mistral-large-3:675b-cloud", "ministral-3:14b"])
    assert exc.value.code == "duplicate_reviewer_family"


def test_the_engine_family_rule_follows_the_real_engine():
    """إن تغيّر المحرّكُ الافتراضيّ سقط هذا الاختبار حتى تتبعه القاعدة."""
    from providers.ollama import OllamaProvider
    default = inspect.signature(OllamaProvider.__init__).parameters["model"].default
    assert reviewer_family(default) == ENGINE_FAMILY


# ————— κ والقائمة المرفوعة للمالك —————

def test_cohen_kappa_matches_a_hand_computed_table():
    # 10 أحكام: اتفاقٌ في 7. المراجع الأول: 6 صحيح و4 خاطئ، والثاني: 5 و5.
    # التوقّع بالمصادفة = 0.6×0.5 + 0.4×0.5 = 0.5، فκ = (0.7−0.5)/(1−0.5) = 0.4
    first = ["c"] * 6 + ["i"] * 4
    second = ["c"] * 4 + ["i"] * 2 + ["i"] * 3 + ["c"]
    assert sum(a == b for a, b in zip(first, second)) == 7
    assert cohen_kappa(first, second) == 0.4


def test_kappa_is_null_when_every_verdict_is_one_category():
    assert cohen_kappa(["c"] * 5, ["c"] * 5) is None


def test_disagreements_and_flags_reach_the_owner_queue_and_agreement_does_not(tmp_path):
    bank = _bank(tmp_path)
    a = {"judgments": [_judgment("c1"), _judgment("c2"),
                       _judgment("c3", reference="incorrect")]}
    b = {"judgments": [_judgment("c1"), _judgment("c2", rubric="insufficient"),
                       _judgment("c3", reference="incorrect")]}
    review_bank(bank, REVIEWERS, Fake({REVIEWERS[0]: [a], REVIEWERS[1]: [b]}),
                brief_path=BRIEF)
    summary = summarize(bank)
    queued = {entry["id"] for entry in summary["owner_queue"]}
    assert queued == {"c2", "c3"}, "c1 متّفقٌ على صحّته فلا يُرفع"
    assert summary["pairs"][0]["items"] == 3
    assert summary["pairs"][0]["reference"]["observed_agreement"] == 1.0
    assert "llm_reviewers_not_human" in summary["measurement_limits"]


# ————— التجربة الحيّة الصغيرة —————

def test_the_smoke_probe_passes_only_when_the_planted_error_is_caught(tmp_path):
    from evaluation.external_review import SMOKE_PLANTED, smoke
    ids = ["smoke_1", SMOKE_PLANTED, "smoke_3"]
    catches = {"judgments": [_judgment("smoke_1"), _judgment(SMOKE_PLANTED, reference="incorrect"),
                             _judgment("smoke_3")]}
    report = smoke(tmp_path, REVIEWERS, Fake({m: [catches] for m in REVIEWERS}), brief_path=BRIEF)
    assert report["status"] == "passed"
    assert all(r["caught_planted_error"] for r in report["reviewers"].values())
    # مراجعٌ يوافق على كل شيء يُسقط التجربة، ولو عمل النداء سليمًا
    yes_man = _ok(ids)
    fake = Fake({REVIEWERS[0]: [catches], REVIEWERS[1]: [yes_man]})
    report = smoke(tmp_path / "again", REVIEWERS, fake, brief_path=BRIEF)
    assert report["status"] == "failed"
    assert report["reviewers"][REVIEWERS[1]]["caught_planted_error"] is False
