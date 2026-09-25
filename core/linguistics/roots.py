"""استخراجُ الجذر بمصدرٍ مسمًّى: CAMeL Tools حين يحضر، والمحلّلُ القالبي عند غيابه (غ١).

قِيس في ٢٥ سبتمبر ٢٠٢٦ على مئة كلمةِ متنٍ حقيقية بجذورها الذهبية
(`evaluation/suites/morphology_roots_v1.json`، والدليل
`docs/probe/g1-camel-vs-morphology-20260925.json`):

| المصدر | إصابة | خطأٌ بثقة |
|---|---|---|
| المحلّل القالبي `core.linguistics.morphology` | ٦١/١٠٠ | ٣٥ |
| CAMeL باتّفاق التحليلات (يرفض عند اللبس) | ٨٣/١٠٠ | ٢ |
| CAMeL بأوّل تحليل | ٨٩/١٠٠ | ٩ |

فاعتُمد CAMeL **باتّفاق التحليلات**: إن اتّفقت تحليلاتُه كلُّها على جذرٍ واحد أُعطي،
وإلا رُفض برمز `ambiguous` مع المرشَّحين — فالذي يرفض حين لا يعلم أفضلُ من الذي
يخمّن (٨٣ بخطأين خيرٌ من ٨٩ بتسعة). والمحلّلُ القالبي يبقى طريقًا احتياطيًّا **مسمًّى**
(`source = "template"`) حين لا يُركَّب CAMeL أو قاعدتُه؛ ورقمُه المقيس يُنشر معه.

CAMeL يُستورد عند أوّل نداءٍ لا عند استيراد الحزمة، فتبقى `core.linguistics` محضةً بلا
اعتمادٍ خارجيّ. تركيبُه: `uv sync --extra morphology` ثم
`camel_data -i morphology-db-msa-r13`. وجذرُ CAMeL يُعاد بحروفه («ك.ت.ب» ← «كتب»)،
والحرفُ المعتلّ أو الهمزة يبقى «#» كما تكتبه قاعدةُ r13 — حدٌّ معلَن لا يُخفى بتخمين.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from core.linguistics.morphology import analyze

NON_ROOTS = frozenset({"NOAN", "FOREIGN", "DIGIT", "PUNC", "NTWS", ""})
CAMEL_DB = "calima-msa-r13"


@dataclass(frozen=True)
class RootVerdict:
    """جذرٌ أو رفضٌ مسمًّى: `root is None` والسببُ في `reason`، والمرشَّحون عند اللبس."""
    word: str
    root: str | None
    source: str                 # "camel" | "template"
    reason: str | None
    candidates: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def camel_analyzer():
    """محلّلُ CAMeL إن حضر هو وقاعدتُه، وإلا None — ويُحسم مرّةً واحدة."""
    try:
        from camel_tools.morphology.analyzer import Analyzer
        from camel_tools.morphology.database import MorphologyDB
    except ImportError:
        return None
    try:
        return Analyzer(MorphologyDB.builtin_db(CAMEL_DB))
    except Exception:                          # قاعدةٌ غير منزَّلة أو معطوبة
        return None


def camel_available() -> bool:
    return camel_analyzer() is not None


def _letters(root: str) -> str:
    return root.replace(".", "")


def camel_verdict(word: str, analyzer=None) -> RootVerdict:
    """اتّفاقُ التحليلات جذرٌ، واختلافُها رفضٌ بالمرشَّحين، وغيابُها رفضٌ باسمه."""
    analyzer = camel_analyzer() if analyzer is None else analyzer
    analyses = analyzer.analyze(word)
    roots = [a.get("root") for a in analyses]
    real = sorted({_letters(r) for r in roots if isinstance(r, str) and r not in NON_ROOTS})
    if not analyses:
        return RootVerdict(word, None, "camel", "no_analysis")
    if not real:
        return RootVerdict(word, None, "camel", "no_root")
    if len(real) == 1:
        return RootVerdict(word, real[0], "camel", None, tuple(real))
    return RootVerdict(word, None, "camel", "ambiguous", tuple(real))


def template_verdict(word: str) -> RootVerdict:
    analysis = analyze(word)
    return RootVerdict(word, analysis.root, "template", analysis.reason,
                       () if analysis.root is None else (analysis.root,))


def root_verdict(word: str, *, prefer: str = "camel") -> RootVerdict:
    """الجذرُ من المصدر المفضَّل إن حضر، وإلا من الاحتياطيّ المسمّى."""
    if prefer == "camel" and camel_available():
        return camel_verdict(word)
    return template_verdict(word)
