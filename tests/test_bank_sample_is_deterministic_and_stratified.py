"""العيّنةُ تُصغِّر الكلفة ولا تُغيِّر ما يُقاس.

قياسُ محرّكين على عيّنتين مختلفتين ليس مقارنة. فإن تغيّرت العيّنةُ بين
تشغيلةٍ وأخرى — بترتيب الملفّات، أو بحالة المفسِّر، أو بلا سبب — صار فرقُ
الدرجتين فرقَ الحالات لا فرقَ المحرّكين، ولا يظهر ذلك في أي رقم.

ولو أُخذت الحالاتُ بلا طبقات لاختلّت نسبُ القدرات، فيُقاس محرّكٌ على برمجةٍ
أكثر وآخرُ على امتناعٍ أكثر، والدرجتان تُقارَنان كأنهما على الشيء نفسه.

فهذه الاختبارات تشدّ ثلاثة: الحتميّة، وحفظَ النسب، وصلاحيةَ ما يخرج للمدقّق.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "sample_bank.py"


def _bank(root: Path) -> Path:
    """بنكٌ مصطنعٌ بطبقتين وقدراتٍ متفاوتة الحجم."""
    # القدرةُ عربيّةٌ عمدًا — يقبلها العقد، وتختبر توليدَ معرّف الحزمة.
    # والمعرّفُ ASCII لأن العقد يشترطه، وهو هويّةُ الحالة فلا تُبدَّل.
    plan = {("tier_a", "برمجة", "prog"): 60, ("tier_a", "منطق", "logic"): 30,
            ("tier_b", "كتابة", "write"): 10}
    for (tier, cap, slug), n in plan.items():
        d = root / tier
        d.mkdir(parents=True, exist_ok=True)
        cases = [{
            "case_id": f"{slug}_{i:03}", "capability": cap, "reference": "مرجع",
            "critical": i % 20 == 0, "rubric": ["معيار"],
            "messages": [{"role": "user", "content": "سؤال"}],
            "checks": [{"kind": "contains", "value": "مرجع"}],
        } for i in range(n)]
        (d / f"{slug}.json").write_text(json.dumps({
            "schema_version": 1, "suite_id": f"src_{tier}_{slug}",
            "split": "development", "description": "عيّنةٌ للاختبار.",
            "cases": cases}, ensure_ascii=False), encoding="utf-8")
    return root


def _run(bank: Path, out: Path, *, target: int = 20, salt: str = "k2"):
    done = subprocess.run(
        [sys.executable, str(TOOL), str(bank), "--target", str(target),
         "--salt", salt, "--out", str(out)],
        capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _ids(out: Path) -> list[str]:
    ids = []
    for p in sorted(out.glob("*.json")):
        ids += [c["case_id"] for c in json.loads(p.read_text(encoding="utf-8"))["cases"]]
    return sorted(ids)


def test_the_same_salt_gives_the_very_same_cases(tmp_path):
    bank = _bank(tmp_path / "bank")
    _run(bank, tmp_path / "a")
    _run(bank, tmp_path / "b")
    assert _ids(tmp_path / "a") == _ids(tmp_path / "b")


def test_a_different_salt_gives_different_cases(tmp_path):
    """لولا هذا لكان «الحتميّ» ثابتًا لأنه يأخذ الأوائل دائمًا."""
    bank = _bank(tmp_path / "bank")
    _run(bank, tmp_path / "a")
    _run(bank, tmp_path / "b", salt="اخرى")
    assert _ids(tmp_path / "a") != _ids(tmp_path / "b")


def test_the_sample_is_not_the_head_of_each_file(tmp_path):
    """أخذُ الأوائل يقيس ما صادف أن كُتب أولًا، لا البنك."""
    bank = _bank(tmp_path / "bank")
    _run(bank, tmp_path / "a", target=20)
    picked = {i for i in _ids(tmp_path / "a") if i.startswith("prog_")}
    head = {f"prog_{i:03}" for i in range(len(picked))}
    assert picked != head


def test_capability_proportions_are_preserved(tmp_path):
    bank = _bank(tmp_path / "bank")
    _run(bank, tmp_path / "a", target=50)
    got = Counter(i.split("_")[0] for i in _ids(tmp_path / "a"))
    total = sum(got.values())
    # البنك: برمجة ٦٠٪، منطق ٣٠٪، كتابة ١٠٪
    for cap, want in (("prog", 0.60), ("logic", 0.30), ("write", 0.10)):
        assert abs(got[cap] / total - want) < 0.08, (cap, got[cap], total)


def test_every_critical_case_of_a_stratum_is_kept_first(tmp_path):
    """الحرِجُ وُضع ليكشف الإخفاق، فإسقاطُه يرفع الدرجة بلا عمل."""
    bank = _bank(tmp_path / "bank")
    _run(bank, tmp_path / "a", target=50)
    picked = set(_ids(tmp_path / "a"))
    criticals = {f"prog_{i:03}" for i in range(60) if i % 20 == 0}
    assert criticals <= picked


def test_what_comes_out_passes_the_real_validator(tmp_path):
    """العيّنةُ تُقاس بالمُشغِّل نفسِه، فحزمةٌ لا تمرّ المدقِّق لا تُقاس أصلًا."""
    from evaluation.capabilities import validate_suite
    bank = _bank(tmp_path / "bank")
    _run(bank, tmp_path / "a", target=30)
    files = list((tmp_path / "a").glob("*.json"))
    assert files
    for p in files:
        suite = json.loads(p.read_text(encoding="utf-8"))
        validate_suite(suite)
        assert len(suite["cases"]) <= 100


@pytest.mark.parametrize("target", [5, 50])
def test_the_sample_never_exceeds_the_bank(tmp_path, target):
    bank = _bank(tmp_path / "bank")
    report = _run(bank, tmp_path / "a", target=target)
    assert report["sample_cases"] <= report["bank_cases"]


def _dense_bank(root: Path) -> Path:
    """طبقةٌ حرِجُها أكثفُ من نصيبها التناسبيّ — وهي موضعُ العطب.

    العيّنةُ السابقة كان حرِجُها ٣ من ٦٠ مقابل نصيبٍ ٣٠، فلا يظهر الاقتطاع
    أبدًا مهما انكسر الضمان. وفي البنك الحقيقي طبقةٌ كلُّ حالاتها حرِجة.
    """
    for tier, slug, n, crit_every in (("tier_a", "dense", 30, 1), ("tier_a", "sparse", 170, 20)):
        d = root / tier
        d.mkdir(parents=True, exist_ok=True)
        cases = [{
            "case_id": f"{slug}_{i:03}", "capability": slug, "reference": "مرجع",
            "critical": i % crit_every == 0, "rubric": ["معيار"],
            "messages": [{"role": "user", "content": "سؤال"}],
            "checks": [{"kind": "contains", "value": "مرجع"}],
        } for i in range(n)]
        (d / f"{slug}.json").write_text(json.dumps({
            "schema_version": 1, "suite_id": f"src_{tier}_{slug}",
            "split": "development", "description": "عيّنةٌ للاختبار.",
            "cases": cases}, ensure_ascii=False), encoding="utf-8")
    return root


def test_no_critical_case_is_dropped_when_criticals_outnumber_the_share(tmp_path):
    """جوهرُ الضمان: الحرِجُ كلُّه يدخل ولو فاق النصيب.

    الطبقة «dense» ٣٠ حالةً كلُّها حرِجة، ونصيبُها التناسبيّ عند هدف ٢٠ نحو ٣.
    فلو قُصّ الحرِجُ عند النصيب لسقطت ٢٧ حالةً وُضعت لكشف الإخفاق — وارتفعت
    الدرجةُ بلا عمل، بلا رقمٍ يكشف ذلك.
    """
    bank = _dense_bank(tmp_path / "bank")
    report = _run(bank, tmp_path / "a", target=20)
    picked = set(_ids(tmp_path / "a"))
    dense_criticals = {f"dense_{i:03}" for i in range(30)}
    assert dense_criticals <= picked, sorted(dense_criticals - picked)[:5]
    assert report["critical_dropped"] == 0


def test_the_output_publishes_the_critical_guarantee(tmp_path):
    """ضمانٌ لا يُنشر رقمُه يُقرأ من الوثيقة ولا شيء ينقضه لو انكسر."""
    bank = _dense_bank(tmp_path / "bank")
    report = _run(bank, tmp_path / "a", target=20)
    for key in ("bank_critical", "sample_critical", "critical_dropped"):
        assert key in report, key
    assert report["sample_critical"] + report["critical_dropped"] == report["bank_critical"]
