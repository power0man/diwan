"""محوِّلاتُ الوكلاء: عقدٌ واحد يقود وكلاءَ CLI مختلفين بوضعهم غير التفاعلي، ويعلن قدراتِ كلٍّ وعزلَه وتوافرَه."""
from __future__ import annotations


def registry() -> dict:
    """المحوِّلات المتاحة بأسمائها؛ تُبنى عند الطلب لأن استيرادها يقرأ مسارات الثنائيات."""
    from .claude import ClaudeAdapter
    from .codex import CodexAdapter
    return {"claude": ClaudeAdapter(), "codex": CodexAdapter()}
