"""فصل بيئة التطوير عن بيئة القبول.

والفصل الحقيقي **بالبيئة**: بياناتُ القبول تسكن خارج شجرة المصدر، وتُقرأ
في تشغيلٍ منفصل. وهذا الملف يفرض الشقّ الساكن منه — ألّا تستورد النواة
ولا المزوّدون طبقةَ التقييم — وهو **شرطٌ ضروري لا كافٍ**: لا يمنع قراءةً
مباشرة للملفات ولا استيرادًا ديناميكيًّا، ولذلك لا يُقدَّم عزلًا بذاته.
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ["core", "providers"]


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


@pytest.mark.parametrize("pkg", SHIPPED)
def test_shipped_code_does_not_import_evaluation(pkg):
    for f in (ROOT / pkg).rglob("*.py"):
        for mod in _imports(f):
            assert not mod.startswith("evaluation"), \
                f"{f.relative_to(ROOT)} يستورد {mod} — النواة لا ترى طبقة التقييم"


def test_holdout_directory_is_not_tracked():
    """بيانات القبول خارج شجرة المصدر — ويُسأل git لا يُبحث عن نصّ.

    فوجودُ السلسلة في .gitignore يمرّ ولو كانت معطَّلةً بتعليق.
    """
    import subprocess
    probe = "evaluation/holdout/cases.jsonl"
    r = subprocess.run(["git", "check-ignore", "-q", probe],
                       cwd=ROOT, capture_output=True)
    if r.returncode == 128:                      # لا مستودع git بعد
        lines = [l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
        assert "evaluation/holdout/" in lines, "القاعدة غائبة أو معطَّلة بتعليق"
    else:
        assert r.returncode == 0, f"git لا يتجاهل {probe}"


def _suite(n=6, **kw):
    from evaluation.disclosure import Case, EvaluationSession
    kw.setdefault("max_attempts", 3)
    return EvaluationSession(
        cases=tuple(Case(f"c{i}", f"س{i}", "الجواب المرجعي", "معيار") for i in range(n)), **kw)


def test_reference_answers_never_reach_visible_tasks():
    s = _suite()
    blob = repr(s.visible_tasks())
    assert "الجواب المرجعي" not in blob and "معيار" not in blob


def test_per_case_disclosure_is_blocked():
    from evaluation.disclosure import DisclosureRefused
    s = _suite()
    s.begin_attempt()
    for i in range(5):
        s.record(f"c{i}", True)
    with pytest.raises(DisclosureRefused):
        s.per_case_results()
    assert s.aggregate()["n"] == 5


def test_revealed_case_moves_out_of_the_holdout():
    from evaluation.disclosure import DisclosureRefused
    s = _suite()
    s.begin_attempt()
    for i in range(6):
        s.record(f"c{i}", i % 2 == 0)
    assert s.holdout_size == 6
    s.reveal("c0")
    assert s.holdout_size == 5
    assert all(t["case_id"] != "c0" for t in s.visible_tasks())
    with pytest.raises(DisclosureRefused):
        s.record("c0", True)
    assert s.aggregate()["revealed_cases"] == 1
