# OpenCode عاملًا محليًّا (جديد-opencode-worker)

**الغرض:** أن تُنجَز الأعمالُ الآلية المتكرّرة بلا توكنٍ سحابيّ: OpenCode بنموذجٍ محلّيّ في Ollama على الماك،
بمعرّفه المسجَّل `openai/gpt-oss-20b` (`registry/agents.json`، ق٦٢-٣).

## ١ — ما يعمله، وما لا يعمله

| يعمله | لا يعمله |
|---|---|
| `pytest` كاملًا وتسجيلُ الناتج في وصف الطلب | لا يكتب شيفرةً ولا اختبارًا ولا وثيقةً يدويّة |
| `tools/check_docs.py --write` ثم `--check` (أرقامُ الوثائق المولَّدة) | لا يمسّ `AGENTS.md` ولا `Dockerfile` ولا `~/diwan-sealed` |
| `tools/rebuild_index.py rebuild` حيث المخزنُ موضوع | لا يقرأ ملفّات المالك ولا يرسلها إلى أيّ مكان |
| إيداعٌ بذيل `Diwan-Agent: openai/gpt-oss-20b` على فرع `opencode/<التاريخ>` | لا يدمج، ولا يدفع إلى `main`، ولا `push --force` |

**المراجعة:** من عائلةٍ أخرى، anthropic أو google، **لا openai** (الخطة §٤، جديد-opencode-worker ٥).
فطلبُه مسودّةٌ يراجعها Claude، ولا يُطلب فيه `@codex review`.

## ٢ — الإعداد (مرّةً على الماك)

```bash
ollama pull gpt-oss:20b      # Apache-2.0، نحو 13GB
```

وOpenCode مركَّب (MIT). وإعدادُه للعامل **ملفٌّ منفصل خارج المستودع**، يُمرَّر بـ`OPENCODE_CONFIG`،
فلا يُمسّ إعدادُ المالك العامّ (`~/.config/opencode/opencode.json`):

```json
{
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": { "gpt-oss:20b": { "name": "gpt-oss 20b" } }
    }
  },
  "model": "ollama/gpt-oss:20b"
}
```

## ٣ — التشغيل

**قبله:** لا يعمل مع المحرّك (qwen3.5:9b) ولا الواجهة ولا Kimi ولا الاستئصال على 24GB. فيُشغَّل في نافذةٍ
فارغة من تقويم الماك (`docs/OWNER-TIME.md`)، وبعد `ollama ps` خالٍ.

```bash
git switch -c opencode/$(date +%Y%m%d) origin/main
OPENCODE_CONFIG=<مسارُ الإعداد أعلاه> opencode run --standalone -m ollama/gpt-oss:20b \
  "شغّل pytest كاملًا، ثم check_docs --write ثم --check، ثم rebuild_index rebuild. أودِع ما تغيّر وحده بذيل Diwan-Agent: openai/gpt-oss-20b."
```

ثم يفتح Claude طلبًا مسودّةً بالفرع، ويراجعه.

## ٤ — الحدود

- **النموذجُ المحلّيُّ لا يُوثق في الحكم:** العاملُ ينفّذ أوامر ثابتة ويودِع ناتجها، والمراجعةُ تتحقّق أن
  الإيداع ناتجُ الأوامر وحدها (أرقامُ الوثائق المولَّدة).
- **`rebuild_index` يحتاج المخزنَ القانونيّ موضوعًا** (`tools/place_private_stores.py`)، وهو من ملفّات المالك،
  فلا يُبنى في نسخةٍ بلا مخزن. والفهرسُ متجاهَلٌ في git، فلا يدخل الطلب.
- **الذيلُ إقرارٌ لا مصادقة** (`tools/agent_attribution.py`، `self_declared_not_authenticated`).

## ٥ — أوّلُ تشغيل (٢٩ سبتمبر ٢٠٢٦، الماك، gpt-oss:20b)

| الأمر | ما حدث |
|---|---|
| `pytest` كاملًا | مهلةُ أداة الصَّدَفة في OpenCode ‏١٢٠ ثانية، فسقطت المحاولتان الأوليان بالمهلة، وتمّت الثالثة. السقوطُ الوحيد الظاهر `test_context_budget` (رابطُ `/private/var` في macOS، يسقط على main أيضًا) |
| `check_docs --write` ثم `--check` | `verified`، ولا فرق يُودَع |
| `rebuild_index rebuild` | رُفض: فهرسُ المخزن بلا مرساة في نسخةٍ بلا مخزن، كما في §٤ |
| الإيداع | لا شيء تغيّر، فلم يُودِع، وهذا صحيح |

**ما يُتعلَّم:**
- **تقريرُ النموذج لا يُعتمد:** كتب «رمزُ الخروج ٠» لـpytest، وهو رمزُ `tail` في الأنبوب لا رمزُ pytest. فالمراجعةُ تقرأ ناتج الأوامر لا ملخّصَ العامل.
- **الإضافاتُ العامّة تُحمَّل مع الإعداد المنفصل:** `OPENCODE_CONFIG` يُضاف إلى إعداد المالك العامّ ولا يحلّ محلّه، فأنشأت إضافةُ فهرسةٍ فيه `.opencode/` داخل النسخة. والتضمينُ محلّيّ (Ollama)، فلم يخرج شيء، والمجلّدُ متجاهَلٌ الآن في git.
- **المهلة:** يُطلب pytest بمهلةٍ صريحةٍ أطول، أو يُقسَّم.
