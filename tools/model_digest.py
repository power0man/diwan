"""حلُّ بصمة نموذج Ollama وتثبيتُها طوال تشغيلٍ مقيس.

وسمُ النموذج قابلٌ لإعادة التوجيه إلى أوزانٍ أخرى. لذلك يحلُّ مشغّلُ القياس
البصمةَ من ``/api/tags`` قبل التشغيل، ويرفض البصمةَ المتوقعة المخالفة، ثم
يعيد الحلَّ بعد التشغيل قبل أن يكتب دليلًا.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Callable
import urllib.request

BASE_URL = "http://127.0.0.1:11434"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ModelDigestError(RuntimeError):
    """رفضٌ مسمّى لهويّة نموذجٍ لا تصلح لإسناد دليل قياس."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def resolve_model_digest(model: str, *, base_url: str | None = None, timeout: float = 10.0) -> str | None:
    """أعد بصمة ``model`` كما يعرضها Ollama، أو ``None`` إن لم تُحلّ.

    لا يقرأ النداءُ إعدادات الوكيل من البيئة؛ فالمضيف المحلي المعلن هو حدُّه
    الافتراضي، ويُمرَّر عنوانٌ آخر صراحةً للاختبار فقط.
    """
    request = urllib.request.Request((base_url or BASE_URL).rstrip("/") + "/api/tags")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            models = json.loads(response.read().decode("utf-8"))["models"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    wanted = model if ":" in model else model + ":latest"
    for item in models if isinstance(models, list) else ():
        if isinstance(item, dict) and item.get("name") == wanted:
            digest = item.get("digest")
            return digest if isinstance(digest, str) and digest else None
    return None


def pin_model_digest(model: str, expected: str | None = None, *,
                     resolver: Callable[[str], str | None] | None = None) -> str:
    """حلُّ البصمة قبل القياس، وارفض الغيابَ أو مخالفةَ المتوقع باسمٍ ثابت."""
    resolved = (resolver or resolve_model_digest)(model)
    if resolved is None:
        raise ModelDigestError("model_digest_unresolved")
    if expected is not None and expected != resolved:
        raise ModelDigestError("model_version_mismatch")
    return resolved


def verify_model_digest(model: str, pinned: str, *,
                        resolver: Callable[[str], str | None] | None = None) -> None:
    """أعد الحلَّ بعد القياس، ولا تسمح بكتابة دليلٍ إن غابت البصمة أو تغيّرت."""
    resolved = (resolver or resolve_model_digest)(model)
    if resolved is None:
        raise ModelDigestError("model_digest_unresolved")
    if resolved != pinned:
        raise ModelDigestError("model_digest_drifted")


QUARANTINE_DIR = "drift-quarantine"


def quarantine_runs_since(run_root: Path, started: float) -> list[str]:
    """انقل كلَّ مجلّد تشغيلٍ كُتب فيه شيءٌ منذ `started` إلى `<run_root>/drift-quarantine/` (ملاحظة Codex على #290).

    معرّفُ التشغيلة مشتقٌّ من إعدادها وفيه البصمة، فوسمٌ تغيّر أثناء القياس ثم عاد إلى البصمة المثبّتة يُعيد في الاستدعاء
    التالي عرضَ دفترٍ فيه أجوبةٌ بعد الانحراف، ويمرّ التحقّقُ اللاحق. فالرفضُ لا يكفي: تُنقل التشغيلةُ فلا تُعاد. والحدُّ
    محافظ: كلُّ تشغيلةٍ لمس ملفًّا فيها أحدٌ منذ البدء تُنقل، ولو كانت إعادةَ عرضٍ أو من عمليةٍ أخرى، فتُعاد من أوّلها.
    """
    root = Path(run_root)
    if not root.is_dir() or root.is_symlink():
        return []
    target = root / QUARANTINE_DIR
    moved = []
    for run_dir in sorted(p for p in root.iterdir() if p.name != QUARANTINE_DIR and p.is_dir() and not p.is_symlink()):
        files = [f for f in run_dir.rglob("*") if f.is_file() and not f.is_symlink()]
        if run_dir.stat().st_mtime >= started or any(f.stat().st_mtime >= started for f in files):
            target.mkdir(exist_ok=True)
            os.replace(run_dir, target / f"{run_dir.name}-{time.time_ns()}")
            moved.append(run_dir.name)
    return moved
