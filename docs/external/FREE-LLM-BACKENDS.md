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

## القياسُ الأول (٢٨ سبتمبر ٢٠٢٦)

- **موجّهُ HF يعمل:** DeepSeek-V3-0324 وLlama-3.3-70B وCohere command-a وPhi-4 التقطت الخطأَ المزروع كلُّها بلا إنذارٍ
  كاذب ومن محاولةٍ واحدة. ولا Mistral ولا AI21 في فهرسه يومئذٍ.
- **GitHub Models غيرُ متاح:** `models.github.ai` يردّ HTTP 200 و`OK` نصًّا عاديًّا (٤ بايتات) على الفهرس وعلى كل نداء، من
  Actions برمز المهمّة ومن غيرها بلا رمز، ولا تحويلَ. فالمهمّةُ تسمّيه `unavailable` ولا تُسقط الطلب؛ والتحقّقُ من نقطة
  GitHub Models الحالية خطوةُ المالك.

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
- **حالةُ HTTP مسمّاة** (`unauthorized` 401، `forbidden` 403، `not_found` 404)، والردُّ الذي ليس JSON `response_not_json`؛
  ويُسجَّل لكل نموذجٍ (وللفهرس) آخرُ شكلٍ فاشل (`last_failure_shapes`): الحالةُ ونوعُ المحتوى والحجمُ وأسماءُ المفاتيح، ومن
  جسم الخطأ `error.code` و`error.type` إن كانا معرّفين قصيرين — لا رسالةٌ ولا نصُّ نموذجٍ ولا مفتاح.
- **الواجهةُ غيرُ المتاحة ليست إخفاقَ مراجع:** إن لم يُجب نموذجٌ واحد وكانت الأخطاءُ كلُّها رفضَ خدمة (`response_not_json`
  أو `unauthorized` أو `forbidden`) فالحالةُ `unavailable` برمز `service_unavailable` وخروجٌ بـ3، وتجعلها المهمّةُ في Actions
  تنبيهًا لا يُسقط الطلب. وفهرسٌ لا يُقرأ لا يوقف التجربة: تختار من القائمة المفضَّلة وتقول ذلك (`candidates_from`).
- **المحجوبُ لا يُرسل**، والبنكُ على هذه الواجهات من `evaluation/banks/` وحدها، فلا تُرسل ملفّاتُ المالك. وقبل أيّ نسخٍ أو نداء
  تُمشى `open/` بلا اتّباع روابط: رابطٌ رمزيّ أو ملفٌّ غيرُ عاديّ `open_split_link_refused`، وملفٌّ تطابق بصمتُه بصمةً في بيان
  المحجوب (ولو باسمٍ آخر) `sealed_digest_refused`؛ والنسخُ إلى مجلّد التشغيل لا يتبع رابطًا، ويُطابَق كلُّ منسوخٍ ببصمة مصدره.
- **حدودُ القياس** نفسُها (ومنها حدودُ الواجهات المجانية) في التجربة وفي نتيجة البنك وفي خلاصته المحفوظة.
- **المراجعُ المستبدَل** تُنقل سجلّاتُه إلى `reviews/superseded/<النموذج>/` (ومعها `SUPERSEDED.json` باسم بديله)، ويراجع بديلُه
  البنكَ كلَّه؛ فالنجاحُ والاتفاقُ وκ وقائمةُ المالك من المجموعة الأخيرة وحدها، ونفادُ حصّته يُروى في `superseded`.
- **مراجعةُ البنك في Actions تُكتب في مجلّدِ تشغيلٍ نظيف** (`--run-id`: `evaluation/banks/kimi_v1/runs/<run_id>-<attempt>/`، فيه
  نسخةُ الشطر المفتوح ومراجعاتٌ تبدأ فارغة وRUN.json بحالة التشغيل)، ويحمل كلُّ سجلٍّ وخلاصةٍ معرّفَه؛ فلا تُقرأ الخلاصةُ المتتبَّعة
  ولا تُرفع. وتُحفظ مراجعاتُه أثرًا اسمُه `free-llm-review-kimi_v1-<run_id>-<attempt>` لثلاثين يومًا، ولو أخفقت المراجعة، بعد فحصٍ
  (`--check-artifact --run-id`) أنها سجلّاتُ JSON لشطرٍ مفتوح من هذا التشغيل بلا مسارٍ محجوب ولا نمطِ رمزٍ ولا قيمةِ الرمز
  (`artifact_stale_summary`/`artifact_stale_record` لما سواه)؛ فإن خرجت قبل الخلاصة رُفع RUN.json برمز رفضها وحده.
- **الأعدادُ من المجموعة الأخيرة:** `reviewed` و`skipped` و`failed` لمراجعيها وحدهم، ومجاميعُ المحاولات كلِّها في `attempts`.

## الحدود

- هويةُ النموذج معرّفُه في فهرس المزوّد، لا بصمةُ أوزانٍ متحقَّقٌ منها؛ والعائلةُ مستنتجةٌ من الناشر والاسم.
- الطبقةُ المجانية في GitHub Models تحدّ حجمَ المدخل للطلب (نحو ٨٠٠٠ رمز بحسب وثائق GitHub، ولم يُقَس هنا): فتجربةُ
  الخطأ المزروع تمرّ، أمّا ملفّاتُ بنك kimi_v1 الكبيرة فقد تُردّ `request_too_large` — والمراجعةُ الكاملة للبنك طريقُها Ollama على الماك أو موجّهُ HF.
- ثلاثُ حالاتٍ مصطنعة تُثبت الطريقَ لا جودةَ المراجع.
