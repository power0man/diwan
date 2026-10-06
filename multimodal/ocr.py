"""أداة استخراج النص من الصور والمستندات (غ٨ #48): ocr_image.

تستخرج النصوص من الصور (PNG وJPEG) ومستندات PDF عبر المحرّك المتاح محليًا
(LocalMediaProvider/Ollama، ثم Tesseract المثبَّت في صورة الحاوية، ثم EasyOCR)، وتحجر النصوص المستخرجة
عبر core.quoted.quarantine حمايةً من أي حقن للأوامر (Prompt Injection)،
وتتيح حفظ النص المستخرج في مساحة العمل عبر دفتر الرجوع (Journal).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.registry import Tool, ToolContext, ToolRefused
from core.contracts import ToolSpec
from core.quoted import quarantine
from multimodal.codec import (
    MediaError,
    pack_media,
    read_selected,
)

OCR_PROMPT = (
    "انسخ كلَّ النصِّ الظاهر في هذه الصورة حرفًا بحرف بخطّه الأصلي، بما في ذلك الحروف والأرقام "
    "والأسماء والرموز، بترتيب قراءته وأسطره، بلا مقدّمةٍ ولا شرحٍ ولا ترجمة. اكتب النصَّ وحده."
)

OCR_IMAGE_SPEC = ToolSpec(
    name="ocr_image",
    description="يستخرج النص من صورة (PNG/JPEG) أو صفحة مستند (PDF) داخل مساحة العمل، ويحجر أي أوامر مدسوسة، مع خيار حفظ النص في ملف.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "مسار ملف الصورة أو مستند PDF داخل مساحة العمل",
            },
            "page": {
                "type": "integer",
                "description": "رقم الصفحة في حال كان الملف PDF (يبدأ من 1، الافتراضي 1)",
            },
            "output_path": {
                "type": "string",
                "description": "مسار اختياري لحفظ النص المستخرج داخله عبر دفتر الرجوع",
            },
        },
        "required": ["path"],
    },
    # تكتب ملفًّا في مساحة العمل حين يُعطى output_path، فدرجتُها درجةُ أدوات الكتابة (ملاحظة Codex على #311)
    consent="logged",
    reversible=True,
)

# Tesseract بإعداداته الافتراضية وبيانات العربية، كما قيس في غ٨ (#92)؛ ومهلتُه مهلةُ مُشغِّل القياس
TESSERACT_TIMEOUT_S = 120
# ما يلزم تثبيتُه حين لا يتاح محرّك: صورةُ الحاوية تحمل Tesseract وpoppler (docs/INSTALL.md)
OCR_INSTALL_HINT = ("لا محرّكَ OCR متاح: ثبّت tesseract-ocr مع tesseract-ocr-ara (وpoppler-utils لصفحات PDF)، "
                    "أو اضبط DIWAN_MEDIA_MODEL وDIWAN_MEDIA_DIGEST لنموذج رؤيةٍ محلي")


def _inside_workspace(context: ToolContext, relative: str, *, writing: bool = False) -> Path:
    from agent.builtin_tools import _inside
    return _inside(context, relative, writing=writing)


def _extract_text_via_easyocr(image_bytes: bytes) -> str:
    import tempfile
    try:
        import easyocr
    except ImportError:
        raise ToolRefused("ocr_engine_unavailable", "easyocr غير متوفر في البيئة الحالية") from None

    model_dir = Path.home() / ".EasyOCR" / "model"
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        tmp.write(image_bytes)
        tmp.flush()
        try:
            reader = easyocr.Reader(["ar"], gpu=False, verbose=False,
                                    download_enabled=False,
                                    model_storage_directory=str(model_dir))
            lines = reader.readtext(tmp.name, detail=0, paragraph=True)
            return "\n".join(lines).strip()
        except ToolRefused:
            raise
        except Exception:
            raise ToolRefused("ocr_engine_unavailable", "أوزان easyocr غير متوفرة محليًا") from None


def _extract_text_via_tesseract(image_bytes: bytes, suffix: str = ".png", executable: str | None = None,
                                language: str = "ara") -> str:
    """Tesseract على ملفٍّ مؤقّت بإعداداته الافتراضية (لا --psm ولا --oem)، كما في `evaluation/ocr_runner.py`."""
    import shutil
    import subprocess
    import tempfile

    executable = executable or shutil.which("tesseract")
    if not executable:
        raise ToolRefused("ocr_engine_unavailable", "tesseract غائب؛ ثبّته مع بيانات العربية (tesseract-ocr-ara)")
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(image_bytes)
        tmp.flush()
        try:
            done = subprocess.run([executable, tmp.name, "-", "-l", language], capture_output=True, text=True,
                                  timeout=TESSERACT_TIMEOUT_S, check=False)
        except (OSError, subprocess.SubprocessError):
            raise ToolRefused("ocr_engine_unavailable", "تعذّر تشغيل tesseract") from None
    if done.returncode != 0:
        raise ToolRefused("ocr_engine_unavailable",
                          f"أخفق tesseract (رمز الخروج {done.returncode})؛ بياناتُ «{language}» غائبةٌ أو الصورة غير مقروءة")
    return done.stdout.strip()


def _get_media_provider(model: str | None = None, version: str | None = None, base_url: str | None = None):
    """استرجاع مزوّد الوسائط المحلي المضبوط في البيئة."""
    import os
    from providers.local_media import LocalMediaProvider
    from providers.base import ProviderError

    target_model = model or os.environ.get("DIWAN_MEDIA_MODEL")
    target_version = version or os.environ.get("DIWAN_MEDIA_DIGEST")
    target_url = base_url or os.environ.get("DIWAN_OLLAMA_URL", "http://127.0.0.1:11434")

    if not target_model or not target_version:
        return None
    try:
        return LocalMediaProvider(target_model, target_version, base_url=target_url)
    except ProviderError:
        return None


def _extract_text_via_media_provider(image_dict: dict, provider=None, model: str | None = None, base_url: str | None = None) -> str:
    from core.contracts import Message, Request
    from core.validate import validated
    from multimodal.codec import MEDIA_SYSTEM, encode_request
    from providers.base import ProviderError
    from providers.local_media import LocalMediaProvider

    target_provider = provider
    if target_provider is None:
        target_provider = _get_media_provider(model=model, base_url=base_url)

    if target_provider is None:
        raise ToolRefused("ocr_engine_unavailable", "مزوّد الوسائط المحلي غير مهيّأ")

    try:
        user_text = encode_request(OCR_PROMPT, (image_dict,))
        messages = (Message("system", MEDIA_SYSTEM), Message("user", user_text))
        req = validated(Request(messages, target_provider.model, target_provider.model_version,
                               800, 30, "local_only", "ocr_turn"))
        response = target_provider.complete(req)
        return response.content.strip()
    except ProviderError as exc:
        raise ToolRefused(exc.code, exc.reason) from None
    except Exception:
        raise ToolRefused("ocr_engine_unavailable", "تعذر استخراج النص عبر مزود الوسائط") from None


def perform_ocr(image_dict: dict, engine: str = "auto", provider=None) -> str:
    """استخراج النص من الوسيط المجهز عبر المحرك المحدد أو التبديل التلقائي."""
    import base64
    raw_bytes = base64.b64decode(image_dict["data_base64"])
    suffix = ".jpg" if image_dict.get("mime") == "image/jpeg" else ".png"
    if engine == "easyocr":
        return _extract_text_via_easyocr(raw_bytes)
    elif engine == "tesseract":
        return _extract_text_via_tesseract(raw_bytes, suffix)
    elif engine in ("media_provider", "ollama"):
        return _extract_text_via_media_provider(image_dict, provider=provider)
    else:
        # auto: مزوّد الوسائط إن ضُبط، ثم Tesseract (في صورة الحاوية)، ثم EasyOCR؛ فإن غابت كلُّها سُمّي ما يُثبَّت
        # (ملاحظة Codex على #311)
        try:
            return _extract_text_via_media_provider(image_dict, provider=provider)
        except Exception:
            pass
        try:
            return _extract_text_via_tesseract(raw_bytes, suffix)
        except ToolRefused:
            pass
        try:
            return _extract_text_via_easyocr(raw_bytes)
        except ToolRefused:
            raise ToolRefused("ocr_engine_unavailable", OCR_INSTALL_HINT) from None


def ocr_image_handler(arguments: dict, context: ToolContext) -> dict:
    path_arg = arguments.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        raise ToolRefused("argument_invalid", "الوسيط «path» مطلوبٌ من نوع str")

    page_arg = arguments.get("page", 1)
    if not isinstance(page_arg, int) or page_arg < 1:
        raise ToolRefused("argument_invalid", "الوسيط «page» يجب أن يكون عددًا صحيحًا موجباً (>= 1)")

    output_path_arg = arguments.get("output_path")
    if output_path_arg is not None and (not isinstance(output_path_arg, str) or not output_path_arg.strip()):
        raise ToolRefused("argument_invalid", "الوسيط «output_path» يجب أن يكون نصًا صالحًا عند تحديده")

    resolved_path = _inside_workspace(context, path_arg, writing=False)
    if resolved_path.is_symlink():
        raise ToolRefused("path_symlink", "لا يُقرأ عبر رابط رمزيّ")
    if not resolved_path.is_file():
        raise ToolRefused("file_not_found", f"لا ملفَّ عند {path_arg}")

    try:
        media_doc = read_selected(resolved_path, page=page_arg)
    except MediaError as exc:
        raise ToolRefused(exc.code, exc.reason) from None

    engine_pref = arguments.get("engine", "auto")
    provider = None
    factory = getattr(context, "media_provider_factory", None)
    if callable(factory):
        provider = factory()

    extracted_raw = perform_ocr(media_doc, engine=engine_pref, provider=provider)

    # فرض الحجر الإلزامي على النص المستخرج لحماية حلقة الوكيل من التوجيه الخفي
    held = quarantine(extracted_raw)
    quarantined_findings = [
        {"code": f.code, "start": f.start, "end": f.end, "excerpt": f.excerpt}
        for f in held.findings
    ]

    action_id = None
    if output_path_arg:
        _inside_workspace(context, output_path_arg, writing=True)
        action = context.journal.write_file(output_path_arg, held.text)
        action_id = action.action_id

    summary = f"تم استخراج {len(held.text)} محرفًا من {path_arg}."
    if held.findings:
        summary += f" [تحذير: تم حجر {len(held.findings)} مقطعًا مشبوهًا]."
    if action_id:
        summary += f" حُفظ الناتج في {output_path_arg} (إجراء: {action_id})."

    return {
        "content": summary,
        "text": held.text,
        "quarantined": quarantined_findings,
        "clean": held.clean,
        "action_id": action_id,
        "media_sha256": media_doc["sha256"],
    }


OCR_IMAGE = Tool(OCR_IMAGE_SPEC, ocr_image_handler)
