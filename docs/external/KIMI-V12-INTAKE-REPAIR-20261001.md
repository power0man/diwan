# تكليف Kimi: إصلاح استلام v1.2 المفتوح — ١ أكتوبر ٢٠٢٦

> نتيجة آلية بعد الجولة الأولى، وليست نتيجة محرّك ديوان: `docs/probe/k6-intake-repair-open-20261001.json`.
> يُرسل ما بعد الخط كما هو، مسبوقًا بما بعد خط `KIMI-WORKSPACE-HEADER.md`، ثم التكليف الأصلي `KIMI-BENCHMARK-BRIEF.md` كاملًا، ثم ما بعد خط `KIMI-NEXT.md`.
> المؤلف يصلح تسليمه المفتوح؛ القائد نقل الرموز والمعرّفات ولم يؤلّف حلولًا أو فحوصًا.

---

أصلح تسليمك القائم في `kimi-benchmark/` حتى يمرّ الاستلام المفتوح كاملًا (`passed=true`): بنك القياس المفتوح وبنكا التطوير معًا. احتفظ بالإصلاحات الصحيحة في الجولة الماضية، ولا تستبدل تسليمك بنسخة أقدم من `current/open/`.

المرجع الحاكم هو **التكليف الأصلي وتحديث v1.2 السابقان في هذه الحزمة**. هذه قائمة أخطاء من المدقّقات ومعايير النجاح التي ألّفتها أنت، وليست معايير جديدة من القائد. وهي توسّع إصلاح الجولة السابقة إلى المتبقي في التسليم كله، فلا يبقى قيد الاقتصار على البنود الثلاثة القديمة.

الحدود نفسها: اعمل في هذا المجلّد وحده، ولا تقرأ مستودع ديوان أو شيفرته أو وثائقه التقنية. لا تغيّر المدقّقات أو وسيلة الاستلام، ولا تطلبها. لا تقرأ سجلّات الجولات، ولا تُنشئ `sealed/` أو بيانًا، ولا تمسّ `current/`. التسليم **مفتوح وحده**؛ ولا حذف لحالة أو مهمة أو ملف جانبي أو إفراغ لحقوله.

نتيجة الاستلام: ١٩٠٢ حالة مفتوحة، و١٤٠ مهمة مفتوحة، و٣٠ مهمة تطوير. سقطت المهام الـ١٧٠ كلها قبل الحل، ونجح ١٦٨ حلًا مرجعيًا من ١٧٠. الحلول الـ١٧٠ كلها خرائط ملفات الآن؛ حافظ على ذلك. مراجع المفتوح الـ١٤٠ ناجحة جميعها، والفشلان الباقيان في بنك التطوير.

## ١ — ١٥٠ حالة بلا checks

الرمز `cases_without_checks`. طبّق الجزء ١ من تحديث v1.2 كما ورد: فحص واحد على الأقل لكل حالة، يسقط الخطأ ويختبر قيود السؤال نفسه؛ لا تكتفِ بالـrubric، ولا تغيّر معنى السؤال لتسهيل الفحص. إن احتاج معيار جوابٍ صيغة ثابتة فأعلنها في السؤال. ولا تُضعف منع سرد الخيارات أو قلب القطبية في بقية البنك.

المعرّفات التالية من تسليمك المفتوح، مجمّعة بحسب الملف:

- `open/tier_b/kimi_arabic_b_001.json` — 12 حالة:

  `fsem_b_0007`, `fsem_b_0032`, `fsem_b_0033`, `rhet_b_0035`, `rhet_b_0031`, `rhet_b_0034`, `rhet_b_0033`, `rhet_b_0036`, `rhet_b_0042`, `rhet_b_0027`, `rhet_b_0032`, `rhet_b_0038`

- `open/tier_b/kimi_assist_b_001.json` — 25 حالة:

  `edit_b_0038`, `edit_b_0028`, `edit_b_0018`, `edit_b_0020`, `edit_b_0043`, `edit_b_0035`, `edit_b_0016`, `edit_b_0010`, `edit_b_0015`, `edit_b_0021`, `edit_b_0036`, `edit_b_0025`, `edit_b_0040`, `offw_b_0008`, `offw_b_0018`, `offw_b_0035`, `offw_b_0015`, `offw_b_0019`, `offw_b_0036`, `offw_b_0004`, `offw_b_0039`, `offw_b_0023`, `offw_b_0040`, `offw_b_0032`, `offw_b_0003`

- `open/tier_b/kimi_assist_b_002.json` — 36 حالة:

  `anal_b_0005`, `anal_b_0009`, `anal_b_0013`, `anal_b_0003`, `anal_b_0025`, `anal_b_0014`, `anal_b_0037`, `anal_b_0030`, `anal_b_0041`, `anal_b_0021`, `anal_b_0004`, `anal_b_0011`, `anal_b_0015`, `anal_b_0033`, `anal_b_0010`, `anal_b_0029`, `anal_b_0032`, `anal_b_0012`, `anal_b_0002`, `anal_b_0006`, `anal_b_0036`, `anal_b_0018`, `anal_b_0001`, `anal_b_0039`, `anal_b_0019`, `anal_b_0034`, `anal_b_0024`, `anal_b_0031`, `summ_b_0027`, `summ_b_0007`, `summ_b_0022`, `summ_b_0028`, `summ_b_0036`, `summ_b_0010`, `summ_b_0017`, `summ_b_0025`

- `open/tier_b/kimi_assist_b_003.json` — 20 حالة:

  `rewr_b_0034`, `rewr_b_0030`, `rewr_b_0029`, `rewr_b_0040`, `rewr_b_0031`, `rewr_b_0032`, `rewr_b_0036`, `rewr_b_0039`, `rewr_b_0042`, `rewr_b_0028`, `rewr_b_0038`, `rewr_b_0007`, `rewr_b_0021`, `rewr_b_0022`, `rewr_b_0002`, `rewr_b_0033`, `rewr_b_0024`, `rewr_b_0016`, `techw_b_0028`, `techw_b_0011`

- `open/tier_b/kimi_assist_b_004.json` — 57 حالة:

  `argu_b_0004`, `argu_b_0023`, `argu_b_0013`, `argu_b_0027`, `argu_b_0009`, `argu_b_0039`, `argu_b_0019`, `argu_b_0010`, `argu_b_0041`, `argu_b_0020`, `argu_b_0015`, `argu_b_0011`, `argu_b_0007`, `argu_b_0032`, `argu_b_0008`, `argu_b_0005`, `argu_b_0021`, `argu_b_0018`, `argu_b_0024`, `argu_b_0033`, `argu_b_0030`, `argu_b_0035`, `argu_b_0016`, `argu_b_0012`, `argu_b_0031`, `argu_b_0002`, `argu_b_0003`, `argu_b_0014`, `argu_b_0026`, `argu_b_0028`, `regs_b_0039`, `regs_b_0012`, `regs_b_0003`, `regs_b_0035`, `regs_b_0041`, `regs_b_0027`, `regs_b_0013`, `regs_b_0042`, `regs_b_0014`, `regs_b_0037`, `regs_b_0009`, `regs_b_0006`, `regs_b_0015`, `regs_b_0038`, `regs_b_0017`, `regs_b_0024`, `regs_b_0036`, `regs_b_0023`, `regs_b_0026`, `regs_b_0022`, `regs_b_0025`, `regs_b_0008`, `regs_b_0034`, `regs_b_0020`, `regs_b_0016`, `regs_b_0010`, `regs_b_0002`

## ٢ — ٢٦ معرّف مهمة مكرر بين الملفات

الرمز `task_id_not_unique_across_bank`. هذه ٢٦ هوية مكررة (٢٦ ظهورًا زائدًا)، لا ٢٦ مهمة تحذف. اجعل الهوية فريدة في البنك كله وفق تحديث v1.2، وحدّث مفاتيح الملف الجانبي المطابقة مع الحفاظ على المحتوى. لا تغيّر معرّفًا غير مكرر ولا تحذف أي مهمة.

| المعرّف المكرر | الملفات التي تحمله |
|---|---|
| `agentic_0002` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0005` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0006` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0010` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0011` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0012` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0013` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0014` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0016` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0019` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0022` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0023` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0024` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0031` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0032` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0033` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0035` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0036` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0037` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0038` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0041` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0042` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0044` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0045` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0046` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |
| `agentic_0050` | `open/tier_d/kimi_agentic_001.json`، `open/tier_d/kimi_agentic_002.json` |

## ٣ — عشر مهام تطوير بقواعد forbidden لا تطابق workspace

الرمز `forbidden_rule_unmatchable` في `agentic_v3.json`. القاعدة مسار ملف معلن في `workspace` أو مجلّد يضم مسارًا معلنًا؛ ليست وصفًا لفظيًا أو مسارًا لا يوجد في المساحة الابتدائية. أصلح القواعد غير المطابقة مع بقاء ملفات الاختبار ومعيار النجاح محمية كما يوجب الجزء ٣ من تحديث v1.2. لا تعالج الخطأ بإفراغ forbidden أو بإضعاف الاختبارات.

`ag3v3_dt_01`, `ag3v3_dt_02`, `ag3v3_dt_03`, `ag3v3_dt_04`, `ag3v3_dt_05`, `ag3v3_ex_01`, `ag3v3_ex_02`, `ag3v3_ex_03`, `ag3v3_ex_04`, `ag3v3_ex_05`

## ٤ — حلان مرجعيان لا يجتازان معيار نجاحهما

الموضع `agentic_v3.json` والحلول في `agentic_v3.meta.json`. التشخيص التنفيذي في حاوية فيها Python 3.12 وpytest 9.1.1 أعاد:

| المهمة | رمز الحكم | رمز الخروج | ملفات حكم عُبث بها |
|---|---|---|---|
| `ag3v3_rf_01` | `command_nonzero` | 1 | 0 |
| `ag3v3_rf_02` | `command_nonzero` | 1 | 0 |

راجع كل مهمة ومعيارها وحلها المرجعي. أصلح ما هو معطوب، ولا تُضعف المعيار ليمرّ الحل. طبّق الحل على نسخة من المساحة الابتدائية ثم شغّل معيار النجاح الأصلي؛ يجب أن يفشل قبل الحل وينجح بعده دون مسّ ملف اختبار أو حكم. أبقِ `reference_solution` خريطة «مسار ← محتوى الملف الكامل»؛ لا وصفًا نثريًا.

## ٥ — شروط انتهاء هذه الجولة

- يبقى المفتوح كاملًا بملفاته وحالاته وملفاته الجانبية، بلا `sealed/` ولا بيان.
- لا حالة بلا فحص، ولا معرّف مهمة مكرر بين ملفات البنك، ولا قاعدة forbidden غير مطابقة.
- تبقى حالات العربية العامة الـ١٥٠ على القدرات التسع بفحوصها، ومهام التطوير الثلاثون بمعاييرها وحلولها وملفات الحكم المحمية.
- تفشل كل مهمة من الـ١٧٠ قبل الحل وتنجح بحلها المرجعي، والحل الخاطئ إن وجد يفشل. لا تغيير للمدقّقات أو حدود الاستلام.
- افحص تسليمك كله وفق التكليف الأصلي وتحديث v1.2، لا الملفات المسماة وحدها، وأصلح أي خطأ من النوع نفسه. القائد يعيد المدقّقات والحكم المعزول بعد التسليم، ولن يوزع البنك قبل `passed=true`.
- حدّث `REPORT.md` بما أصلحته في هذه الجولة. رسالتك الأخيرة **أعداد فقط**: عدد الحالات المضافة لها فحوص، والمعرّفات المصححة، وقواعد المنع المصححة، والمراجع الناجحة، والفاشلة قبل الحل؛ وهل أُنشئ محجوب أو بيان (المنتظر: لا).
