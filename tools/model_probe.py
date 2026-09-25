"""قياس النماذج الحية محليًّا — الشقّ المقيس من فحص إمكانات الماك.

يقيس عبر Ollama المحلي (127.0.0.1:11434) بلا اعتماديات خارج المكتبة
القياسية: (١) كمون الطلب القصير، (٢) سرعة التوليد توكن/ثانية من عدّادات
Ollama نفسها لا من تقدير، (٣) «السياق المفيد فعلًا»: إبرة (حقيقة مميزة)
تُدفن في مطلع سياقٍ متزايد الطول ويُسأل عنها في آخره — النجاح استرجاعها
لا مجرد قبول الطول. النتيجة JSON تحت docs/probe/.

حدود معلنة: العدّ بالكلمات تقريب لطول التوكنات؛ والنماذج السحابية
(-cloud) تُستبعد — القياس محلي بطبيعته.
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

BASE = "http://127.0.0.1:11434"
NEEDLE = "رقم بروتوكول المرساة البحرية التجريبي هو 7391-دال"
QUESTION = "ما رقم بروتوكول المرساة البحرية التجريبي المذكور في مطلع النص؟ أجب بالرقم فقط."
FILLER = (
    "تتناول هذه الفقرة أحكامًا عامة في تنظيم الملاحة البحرية وإجراءات "
    "التفتيش والسلامة وإدارة الموانئ وتداول البضائع والتزامات الناقل "
    "والشاحن في عقود النقل البحري الدولية. "
)


ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(model: str, prompt: str, max_out: int = 64, timeout: int = 900,
         num_ctx: int | None = None) -> dict:
    options: dict = {"num_predict": max_out, "temperature": 0}
    if num_ctx:  # بلا ضبطٍ صريح يتضخم تخصيص KV إلى نافذة النموذج كاملة
        options["num_ctx"] = num_ctx
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "10s",
        "think": False,  # نماذج التفكير تستهلك حصة التوليد قبل الجواب
        "options": options,
    }).encode()
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(f"{BASE}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with _LOCAL_OPENER.open(req, timeout=timeout) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError:  # نموذج لا يعرف حقل think — أعد بدونه
        payload = json.loads(body)
        payload.pop("think", None)
        req = urllib.request.Request(f"{BASE}/api/generate",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with _LOCAL_OPENER.open(req, timeout=timeout) as r:
            out = json.loads(r.read())
    out["_wall_s"] = round(time.monotonic() - t0, 2)
    return out


def probe_model(model: str) -> dict:
    result: dict = {"model": model}

    lat = []
    for _ in range(3):
        r = call(model, "اذكر كلمة واحدة فقط: نعم أم لا؟", max_out=8)
        lat.append(r["_wall_s"])
    result["short_latency_s"] = {"runs": lat, "min": min(lat)}

    r = call(model, "اكتب فقرة من مئة كلمة عن أهمية توثيق القرارات.", max_out=256)
    if r.get("eval_count") and r.get("eval_duration"):
        result["gen_tokens_per_s"] = round(r["eval_count"] / (r["eval_duration"] / 1e9), 1)
    if r.get("prompt_eval_count") and r.get("prompt_eval_duration"):
        result["prompt_tokens_per_s"] = round(
            r["prompt_eval_count"] / (r["prompt_eval_duration"] / 1e9), 1)

    result["needle"] = []
    # نافذة ثابتة 40960 يدعمها الثلاثة، والأهداف دونها بهامش — وإلا بتر
    # Ollama مقدّمةَ النص فضاعت الإبرة بالبتر لا بالنسيان (عيب قياسٍ وقعنا
    # فيه ووثّقناه: أعداد توكنات متطابقة بين نماذج مختلفة = نصف num_ctx)
    for approx_tokens in (2_000, 8_000, 16_000, 24_000):
        words = int(approx_tokens / 2)  # عربي: ~توكنان للكلمة (قيس فعليًّا)
        haystack = NEEDLE + "\n" + (FILLER * (words // len(FILLER.split()) + 1))
        prompt = haystack + "\n\n" + QUESTION
        try:
            r = call(model, prompt, max_out=256, num_ctx=40_960)
            norm = r.get("response", "").translate(ARABIC_DIGITS)
            found = "7391" in norm
            result["needle"].append({
                "approx_tokens": approx_tokens,
                "prompt_tokens_reported": r.get("prompt_eval_count"),
                "retrieved": found,
                "wall_s": r["_wall_s"],
            })
        except Exception as exc:  # فشل الطول جزء من القياس لا عطل فيه
            result["needle"].append({
                "approx_tokens": approx_tokens,
                "error": f"{type(exc).__name__}: {exc}",
            })
            break
    return result


def main() -> None:
    models = sys.argv[2:] or ["qwen3:14b", "gemma4:latest", "llama3.1:8b"]
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "docs/probe"
    report = {
        "probed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note_ar": "قياس حي: كمون وسرعة توليد وسياق مفيد (استرجاع إبرة) — التقريب بالكلمات معلن",
        "models": [],
    }
    for m in models:
        print(f"— قياس {m} ...", flush=True)
        try:
            report["models"].append(probe_model(m))
        except Exception as exc:
            report["models"].append({"model": m, "error": f"{type(exc).__name__}: {exc}"})
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    path = f"{out_dir}/models-{stamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"كُتب: {path}")


if __name__ == "__main__":
    main()
