# الواجهاتُ المجانية للمراجعة الخارجية: GitHub Models وموجّه Hugging Face

> قرار المالك في ٢٨ سبتمبر ٢٠٢٦ (#168): «سبق أن طلبتك تشغل free llm شغلله ووزع مهام قدر المستطاع على كل المتاح».
> يمدّ ق٦٠ (ollama.com وحدها) إلى نقطتين أخريين، ويبقى ق٥٠ كما هو. الدليلُ الحيّ الأول في
> `docs/probe/free-api-inventory-20260928.json`.

## لماذا

شبكةُ جلسات Claude السحابية تحجب واجهاتِ النماذج كلَّها، فكانت المراجعةُ الخارجية (ك٥، ك١١) لا تعمل إلا من الماك
بمفتاح Ollama. أمّا مشغّلاتُ GitHub Actions وصناديقُ Hugging Face فشبكتُها مفتوحة. فصار في `tools/external_review.py`
طريقان جديدان بجانب Ollama، بالأداة نفسِها وبالقواعد نفسِها:

| الواجهة | النقطة | المفتاح (من البيئة وحدها) | أين تعمل | الكلفة |
|---|---|---|---|---|
| `--backend github-models` | `models.github.ai` | `GITHUB_TOKEN` | Actions: رمزُ المهمّة نفسُه بصلاحية `models: read`، بلا سرٍّ جديد | مجّانية ضمن حدود الطبقة المجانية (عددُ الطلبات وحجمُ المدخل لكل نموذج) |
| `--backend hf-router` | `router.huggingface.co` | `HF_TOKEN` | صندوقُ HF برمز المالك (`--forward-hf-token`) | رصيدُ PRO الشهريّ ثم سقفُ HFD2 (‎$20)؛ وكلُّ إنفاقٍ يُسجَّل في `docs/probe/` |

## كيف تُشغَّل

- **تلقائيًّا:** كلُّ طلب دمجٍ يمسّ أداةَ المراجعة يشغّل `.github/workflows/free-llm-review.yml`: جردَ الفهرس، ثم تجربةَ
  الخطأ المزروع (٧×٨=٥٤، نمط ك٧) بمراجعَين من عائلتين مسموحتين، ثم على أفضل نموذجٍ من كل عائلةٍ مسموحة.
- **يدويًّا من صفحة Actions:** `free-llm-review` ← Run workflow ← `mode: smoke` أو `mode: bank` (الشطرُ المفتوح من
  `evaluation/banks/kimi_v1` وحده).
- **من سطر الأوامر** حيث الشبكةُ مفتوحة:

```sh
GITHUB_TOKEN=… python3 tools/external_review.py --backend github-models --list-catalog catalog.json
GITHUB_TOKEN=… python3 tools/external_review.py --backend github-models --smoke smoke.json
HF_TOKEN=…     python3 tools/external_review.py --backend hf-router --smoke smoke.json --every-family
GITHUB_TOKEN=… python3 tools/external_review.py evaluation/banks/kimi_v1 --backend github-models \
    --reviewer deepseek/DeepSeek-V3-0324 --reviewer mistral-ai/mistral-medium-2505 --fallback meta/Llama-3.3-70B-Instruct
```

بلا `--reviewer` تختار الأداةُ من الفهرس بترتيب العائلات المفضَّلة: DeepSeek ثم Mistral ثم Meta Llama ثم Cohere ثم AI21
ثم Microsoft Phi.

## ما يُفرض في الشيفرة

- **النقاطُ ثلاثٌ لا غير:** `ollama.com` و`models.github.ai` و`router.huggingface.co`، بـhttps وبلا منفذٍ ولا هوية في
  الرابط؛ والتحويلُ مرفوض فلا يخرج المفتاحُ إلى مضيفٍ آخر. وما سواها `endpoint_not_allowed`.
- **المفتاحُ** لا خيارَ له في سطر الأوامر، ولا يُطبع، ولا يُكتب في تقرير، ولا يدخل نصَّ خطأ؛ وغيابُه `key_missing`.
- **العائلةُ من الناشر بجدولٍ صريح** ويجب أن يقولها الاسمُ أيضًا (وإلا `family_mismatch` أو `reviewer_family_unknown`)،
  والناشرُ المجهول `publisher_unknown`. ولا يراجع Qwen (المحرّك) ولا Moonshot/Kimi (المؤلّف) ولا OpenAI وGoogle
  وAnthropic (المطوّرون) — ولا المقطَّرُ منها، فكلُّ مقطعٍ في الاسم يدخل السلالة. ولا مراجعان تتقاطع سلالتاهما.
- **نفادُ الحصّة** (HTTP 429 أو 402) رمزُه `quota_exhausted`، فيُستبدل بالمراجع مرشّحٌ من عائلةٍ أخرى مسموحة، وإلا
  `quota_exhausted_no_fallback`. وحدُّ المدخل (413) `request_too_large`.
- **الردُّ الفارغ أو المبتور** (`reply_empty`، `reply_incomplete`) يُعاد مرّةً ثم يُسجَّل برمزه.
- **المحجوبُ لا يُرسل**، والبنكُ على هذه الواجهات من `evaluation/banks/` وحدها، فلا تُرسل ملفّاتُ المالك.

## الحدود

- هويةُ النموذج معرّفُه في فهرس المزوّد، لا بصمةُ أوزانٍ متحقَّقٌ منها؛ والعائلةُ مستنتجةٌ من الناشر والاسم.
- الطبقةُ المجانية في GitHub Models تحدّ حجمَ المدخل للطلب (نحو ٨٠٠٠ رمز بحسب وثائق GitHub، ولم يُقَس هنا): فتجربةُ
  الخطأ المزروع تمرّ، أمّا ملفّاتُ بنك kimi_v1 الكبيرة فقد تُردّ `request_too_large` — والمراجعةُ الكاملة للبنك طريقُها Ollama على الماك أو موجّهُ HF.
- ثلاثُ حالاتٍ مصطنعة تُثبت الطريقَ لا جودةَ المراجع.
