# مهمة تنفيذية على ماك المالك — مستودع `power0man/diwan`

> **هذا برومت مستقلّ بذاته.** أنت وكيلُ ذكاءٍ له طرفية وgit على جهاز ماك يملكه
> hussain (حساب GitHub: `power0man`). لا سياقَ لك غير هذا الملف، وكل ما تحتاجه فيه.

## الغرض في سطرين

مستودع `diwan` نواةُ مشروعٍ شخصيّ بُنيت في جلسةٍ سحابية لا تصل إلى هذا الجهاز.
مهمّتك أربع: تحقّقٌ محليّ، وقياسُ عتادٍ، وتسجيلُ مُشغِّل GitHub Actions ذاتيّ
لهذا المستودع **وحده**، وتقرير.

## حدودك — اقرأها قبل أيّ أمر

1. **نطاقك مستودع `power0man/diwan` وحده.** لا تلمس أيّ مستودعٍ آخر على هذا
   الجهاز أو الحساب (`lmai-*`، `kitaba-*`، `Github_Tutorial`) ولا أيّ LaunchAgent
   قائم ولا أيّ عامل Cloudflare.
2. **لا حذف.** لا `rm -rf` على شيءٍ لم تُنشئه أنت في هذه المهمة. ولا `push --force`.
3. **لا أسرار في المستودع.** لا تنسخ رمزًا أو مفتاحًا إلى ملفٍّ يُدفع. رموز
   التسجيل تُستهلك في أوامرها وتُنسى.
4. **لا `sudo`.** كل الخطوات أدناه تعمل بمستخدم المالك العاديّ. إن ظننت أنك
   تحتاجه فتوقّف واسأل المالك.
5. **لا تعدّل `verify.yml` ولا أيّ ملفٍّ في المستودع** — عملُك يضيف ملفات قياسٍ
   فقط تحت `docs/probe/`. تعديل الشيفرة والـworkflows شأنُ جلسةٍ أخرى.
6. **عند أيّ غموضٍ أو فشلٍ غير مذكور هنا: توقّف واعرض على المالك.** لا تخمّن.

## المهمة ١ — الاستنساخ والتحقّق المحليّ

```sh
mkdir -p ~/diwan-work && cd ~/diwan-work
git clone https://github.com/power0man/diwan && cd diwan
python3 acceptance.py                    # بلا اعتماديات — المتوقَّع: 8/8 اجتازت
python3 -m venv .venv
.venv/bin/pip install -q pytest
.venv/bin/python -m pytest tests/ -q     # المتوقَّع: 50 passed
```

- **لماذا `.venv`:** بايثون Homebrew بيئةٌ مُدارة (PEP 668) — التثبيت المباشر
  فيها يفشل أو يلوّث النظام. والبيئة الافتراضية في `.gitignore` فلا تُدفع.
- إن طلب الاستنساخ استيثاقًا: استعمل `gh auth login` (سيحتاج حضور المالك) أو
  مدير بيانات اعتماد macOS القائم. **لا تُدخل كلمة سرّ في ملف.**
- إن فشل اختبارٌ: **توقّف**، والصق المخرَج كاملًا في تقريرك. لا تحاول الإصلاح.
- إن كان `python3` أقدم من ‎3.11: سجّل الإصدار في التقرير وتوقّف عن المهمة ١
  وواصل الباقي.

## المهمة ٢ — قياس العتاد ودفع النتيجة

السكربت موجودٌ في المستودع، ولا يُنزّل شيئًا ولا يُشغّل نموذجًا ولا يقرأ سرًّا:

```sh
cd ~/diwan-work/diwan
python3 tools/mac_probe.py docs/probe
git add docs/probe
git commit -m "قياس: فحص إمكانات الماك (تشغيلٌ محليّ بوكيل المالك)"
git push origin main
```

ثم **اسأل المالك سؤالًا واحدًا** لا يقيسه أمر: «عند إجازة أداةٍ في هرمس، هل
تعرض الواجهة وصف الأداة كاملًا أم اسمها فقط؟» — واكتب جوابه حرفيًّا في
`docs/probe/hermes-ui.md` وادفعه بالطريقة نفسها.

## المهمة ٣ — تسجيل مُشغِّل Actions ذاتيّ لهذا المستودع وحده

**معنى هذه الخطوة، قله للمالك قبل تنفيذها وانتظر موافقته:** المُشغِّل يجعل ما
يُدفع إلى `power0man/diwan` قابلًا للتنفيذ على هذا الجهاز. الدافعون اليوم:
المالك وجلسته السحابية فقط.

الطريق الأول — إن كان `gh` موجودًا ومستوثقًا:

```sh
mkdir -p ~/actions-runner-diwan && cd ~/actions-runner-diwan
DL=$(gh api repos/power0man/diwan-private/actions/runners/downloads \
     -q '.[] | select(.os=="osx" and .architecture=="arm64") | .download_url')
curl -o runner.tar.gz -L "$DL" && tar xzf runner.tar.gz
TOKEN=$(gh api -X POST repos/power0man/diwan-private/actions/runners/registration-token -q .token)
./config.sh --url https://github.com/power0man/diwan-private --token "$TOKEN" \
            --name mac-diwan --labels self-hosted,macos,arm64 --unattended
./svc.sh install && ./svc.sh start
```

الطريق الثاني — بلا `gh`: افتح للمالك
`https://github.com/power0man/diwan-private/settings/actions/runners/new`
(macOS / ARM64) ونفّذ الأوامر التي تعرضها الصفحة حرفيًّا، ثم
`./svc.sh install && ./svc.sh start`.

**تحقّق:**

```sh
gh api repos/power0man/diwan-private/actions/runners -q '.runners[] | "\(.name): \(.status)"'
# المتوقَّع: mac-diwan: online
gh workflow run mac-probe -R power0man/diwan-private   # تشغيل فحصٍ عبر المُشغِّل نفسه
gh run watch -R power0man/diwan-private --exit-status
```

نجاحُ `mac-probe` عبر المُشغِّل يعني أنّ القناة كلّها تعمل: دفعٌ من السحابة ←
تنفيذٌ على هذا الجهاز ← نتيجةٌ تُدفع من الجهاز.

## المهمة ٤ — التقرير

اعرض على المالك، نصًّا لا وصفًا:

1. نتيجة الاختبارات ودليل القبول (المهمة ١) كما طُبعت.
2. مسار ملف القياس المدفوع، وخلاصة سطره الأخير (المهمة ٢).
3. حالة المُشغِّل (`online` أو ما ظهر)، ونتيجة تشغيل `mac-probe` عبره.
4. **كل ما خالف المتوقَّع**، بلا تجميل — الفشل الموصوف بدقّة أنفع من نجاحٍ مزعوم.

وقل له في آخر التقرير: «جلستك السحابية سترى النتائج في المستودع عند فحصها
الدوري؛ وإن أردتَ أسرع فأخبرها: سُجِّل المُشغِّل».
