#!/usr/bin/env bash
# قيادةُ Kimi المحلّي ليؤلّف بنوك القياس. الشرحُ في docs/external/KIMI-DRIVER.md.
#
# ثلاث قواعد يفرضها هذا الملفّ بدل أن يتذكّرها القائد:
#   ١ — لا يُرسَل إلى Kimi إلا نصٌّ ثابت من المستودع، يُجمَع هنا ولا يُؤلَّف.
#   ٢ — Kimi يعمل في $KIMI_WORK وحده، ولا يُعطى مسار المستودع أبدًا.
#   ٣ — مخرجات Kimi تذهب إلى سجلٍّ لا يُطبع، فما يدخل سياقَ القائد قد يصل إلى ديوان.
set -euo pipefail

DIWAN="${DIWAN:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
KIMI_WORK="${KIMI_WORK:-$HOME/kimi-work}"
STAMP="$(date +%Y%m%d-%H%M%S)"

die() { echo "توقّفت: $*" >&2; exit 1; }

# مجلّد Kimi خارج مجلّد ديوان، وإلا صار المستودع في متناوله
case "$KIMI_WORK" in
  "$HOME/diwan-work"|"$HOME/diwan-work"/*) die "مجلّد Kimi داخل diwan-work: $KIMI_WORK" ;;
esac

# المزوّد: kimi (حسابُ Kimi Code، الافتراضيّ)، أو ollama (kimi-k2.6:cloud عبر Ollama المحلي)، أو hf
# (moonshotai/Kimi-K2.6 عبر Hugging Face Inference Providers). النموذجُ نفسُه من مزوّدٍ آخر حين تنفد
# حصّةُ Kimi Code. والبديلُ يُعرَّف بمتغيّرات KIMI_MODEL_* في بيئة Kimi وحدها، فلا يُمسّ config.toml
# ولا يُكتب سرٌّ في ملفّ.
KIMI_BACKEND="${KIMI_BACKEND:-kimi}"
case "$KIMI_BACKEND" in
  kimi|ollama|hf) ;;
  *) die "KIMI_BACKEND مجهول: $KIMI_BACKEND (kimi أو ollama أو hf)" ;;
esac
OLLAMA_KIMI_MODEL="kimi-k2.6:cloud"
HF_KIMI_MODEL="moonshotai/Kimi-K2.6"

# يطبع اسمَ النموذج الذي سيؤلّف، ولا يطبع مفتاحًا
backend_model() {
  case "$KIMI_BACKEND" in
    kimi)   echo "kimi-code (default_model في config.toml)" ;;
    ollama) echo "$OLLAMA_KIMI_MODEL" ;;
    hf)     echo "$HF_KIMI_MODEL" ;;
  esac
}

# شرطُ المزوّد قبل التشغيل، فيُرى الرفضُ لا يُدفن في سجلّ الأخطاء
backend_ready() {
  if [ "$KIMI_BACKEND" = hf ]; then
    # نداءاتُ Kimi Code إلى موجّه HF مدفوعةٌ وتخرج من عمليته هو، فلا يحجزها `Budget` ولا يقيّدها سجلُّ `core.run`؛ فلا تُشغَّل
    # حتى يُعدّ إنفاقُها (ملاحظة Codex على #352، والمتابعة #366). و`ollama` بديلٌ باشتراكٍ ثابت
    die "KIMI_BACKEND=hf مرفوضٌ حتى يمرّ إنفاقُه بسجلّ core.run (#366): نداءاتُه مدفوعةٌ ولا تُحجز ولا تُقيَّد — استعمل KIMI_BACKEND=ollama"
    [ -s "${HF_TOKEN_PATH:-$HOME/.cache/huggingface/token}" ] \
      || die "لا توكن hf في ${HF_TOKEN_PATH:-$HOME/.cache/huggingface/token} — hf auth login بيد المالك"
  fi
}

# يشغّل ما بعده ببيئة المزوّد المختار؛ المفتاحُ يُقرأ وقتَ التشغيل إلى بيئة العملية وحدها
with_backend() {
  case "$KIMI_BACKEND" in
    kimi) "$@" ;;
    ollama)
      KIMI_MODEL_NAME="$OLLAMA_KIMI_MODEL" KIMI_MODEL_PROVIDER_TYPE=openai \
      KIMI_MODEL_BASE_URL="${OLLAMA_OPENAI_URL:-http://localhost:11434/v1}" KIMI_MODEL_API_KEY=ollama \
      KIMI_MODEL_CAPABILITIES="tool_use,thinking" "$@" ;;
    hf)
      local token_file="${HF_TOKEN_PATH:-$HOME/.cache/huggingface/token}"
      KIMI_MODEL_NAME="$HF_KIMI_MODEL" KIMI_MODEL_PROVIDER_TYPE=openai \
      KIMI_MODEL_BASE_URL="${HF_ROUTER_URL:-https://router.huggingface.co/v1}" \
      KIMI_MODEL_API_KEY="$(tr -d '[:space:]' <"$token_file")" \
      KIMI_MODEL_CAPABILITIES="tool_use,thinking" "$@" ;;
  esac
}

cmd_setup() {
  mkdir -p "$KIMI_WORK/examples" "$KIMI_WORK/logs" "$KIMI_WORK/prompts"
  cp "$DIWAN/evaluation/suites/agentic_v1.json" "$KIMI_WORK/examples/agentic_v1.json"
  # والشطرُ المفتوح من البنك الحاليّ إلى current/open (قرار المالك، ٢٦ سبتمبر): دورةُ v1.2
  # للمفتوح وحده، فلا يصل Kimi محجوبٌ ولا بيانُه — فـKimi نموذجٌ سحابيّ، والمحجوبُ لا يُرسل.
  local cur="$KIMI_WORK/current"
  # current/ كلُّه لا open/ وحده: current/ قائمٌ فيه sealed/ أو غيرُه يبقى بجانب المفتوح في متناول Kimi
  [ ! -e "$cur" ] || die "$cur موجود من قبل، ولم أغيّر شيئًا — انقله جانبًا بيدك إن أردت دورةً جديدة"
  mkdir -p "$cur"
  cp -R "$DIWAN/evaluation/banks/kimi_v1/open" "$cur/open"
  cmd_gameable
  echo "مجلّد Kimi جاهز: $KIMI_WORK (examples/ و logs/ و prompts/ و current/)"
  echo "  current/open: $(find "$cur/open" -name '*.json' ! -name '*.meta.json' | wc -l | tr -d ' ') ملفًّا مفتوحًا، ولا محجوب"
}

# قائمةُ ما يردّه الاستلامُ في المفتوح القائم (ك١٧): كلُّ حالةٍ يمرّرها جوابٌ ثابت، بالجواب الذي مرّرها. تُكتب في
# current/GAMEABLE.json فيقرؤها Kimi قبل أن يسلّم، ولا يرى شيفرةَ المسبار؛ وتُعاد كتابتُها ولو كان current/ قائمًا
cmd_gameable() {
  local cur="$KIMI_WORK/current"
  [ -d "$cur/open" ] || die "لا $cur/open — شغّل setup أولًا"
  "${PYTHON:-python3}" "$DIWAN/tools/kimi_intake.py" "$cur/open" --list-gameable --out "$cur/GAMEABLE.json"
  echo "  current/GAMEABLE.json: $("${PYTHON:-python3}" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["gameable"], "يمرّرها جوابٌ ثابت، و", d["needs_sandbox"], "لا يحكم فيها إلا الحاوية")' "$cur/GAMEABLE.json")"
}

# يجمع الرأسَ والتكليفَ والتحديث، كلٌّ من بعد أول خطٍّ فاصل فيه
# والتكليفُ يُختار بـKIMI_TASK: next (الافتراضيّ، KIMI-NEXT.md) أو memory (KIMI-MEMORY-BANK.md)، ولكلٍّ رأسُه: رأسُ
# v1.2 يسمّي kimi-benchmark/ موضعًا للتسليم، ورأسُ الذاكرة يسمّي kimi-memory/ وحده (ملاحظة Codex على #129).
cmd_bundle() {
  local out="$KIMI_WORK/prompts/prompt-$STAMP.txt"
  local task="${KIMI_TASK:-next}" assignment header
  case "$task" in
    next) header="$DIWAN/docs/external/KIMI-WORKSPACE-HEADER.md"; assignment="$DIWAN/docs/external/KIMI-NEXT.md" ;;
    memory) header="$DIWAN/docs/external/KIMI-MEMORY-HEADER.md"; assignment="$DIWAN/docs/external/KIMI-MEMORY-BANK.md" ;;
    *) die "KIMI_TASK: next أو memory، لا $task" ;;
  esac
  local brief="$DIWAN/docs/KIMI-BENCHMARK-BRIEF.md"
  # وتكليفُ الذاكرة يأخذ من التكليف العامّ §٠ و§١ وحدهما (مَن أنت، وقواعدُ الاستقلال) كما هما: ما بعدهما عقدُ بنك
  # v1.2 وتسليمُه في open/ وsealed/ وkimi-benchmark/، فيعارض تسليمَ الذاكرة في kimi-memory/ (ملاحظة Codex على #129)
  if [ "$task" = memory ]; then
    grep -q '^## ٠ — ' "$brief" && grep -q '^## ٢ — ' "$brief" \
      || die "لم يُعثر على §٠ و§٢ في ${brief}؛ فلا يُقتطع منه شيءٌ بتخمين"
  fi
  mkdir -p "$KIMI_WORK/prompts"
  {
    sed -n '/^---$/,$p' "$header" | tail -n +2
    printf '\n'
    if [ "$task" = memory ]; then
      awk '/^## ٢ — /{exit} /^## ٠ — /{on=1} on' "$brief"
    else
      cat "$brief"
    fi
    printf '\n'
    sed -n '/^---$/,$p' "$assignment" | tail -n +2
  } > "$out"
  chmod 600 "$out"
  echo "$out"
}

cmd_run() {
  # المركّب الافتراضي خارج PATH حتى تُعاد قراءة ملفّ الصَّدَفة، فيُسأل عنه مباشرةً
  local kimi_bin
  if command -v kimi >/dev/null 2>&1; then kimi_bin=kimi
  elif [ -x "$HOME/.kimi-code/bin/kimi" ]; then kimi_bin="$HOME/.kimi-code/bin/kimi"
  else die "لا توجد أداة kimi على هذا الجهاز. تركيبُها قرارُ المالك — انظر docs/external/KIMI-DRIVER.md §الحالة."
  fi
  local bundle="${1:-}"
  [ -n "$bundle" ] || bundle="$(cmd_bundle)"
  [ -s "$bundle" ] || die "حزمةُ الطلب فارغة: $bundle"
  # السجلّان كلاهما لا يُفتحان: Kimi يكتب تفكيرَه في مجرى الأخطاء، وفيه أسماءُ حالاتٍ
  # محجوبة وفحوصُها (قيس في ٢٦ سبتمبر ٢٠٢٦). فلا يُطبع منهما إلا الرمزُ والحجم.
  # تسليمُ الذاكرة في مجلّدٍ خاصٍّ به، لا في kimi-benchmark/ الذي يحمل تسليمَ v1.2؛ ولا يُكتب فوق تسليمٍ قائم
  # (ملاحظة Codex على #129)
  if [ "${KIMI_TASK:-next}" = memory ]; then
    local f
    for f in "$KIMI_WORK/kimi-memory/memory_kimi_v1.json" "$KIMI_WORK/kimi-memory/REPORT.md"; do
      [ ! -e "$f" ] || die "تسليمُ ذاكرةٍ قائم: $f — انقله أو أرشفه قبل تشغيلٍ جديد"
    done
    mkdir -p "$KIMI_WORK/kimi-memory"
  fi
  backend_ready
  mkdir -p "$KIMI_WORK/logs"
  local log="$KIMI_WORK/logs/run-$STAMP.log"
  local errlog="$KIMI_WORK/logs/run-$STAMP.err.log"
  local rc=0
  ( cd "$KIMI_WORK" && with_backend "$kimi_bin" --prompt "$(cat "$bundle")" --output-format text ) \
    >"$log" 2>"$errlog" || rc=$?
  chmod 600 "$log" "$errlog"
  echo "المزوّد: ${KIMI_BACKEND}، والنموذج: $(backend_model)"
  echo "انتهى التشغيل (رمز $rc). السجلّان لا يُفتحان: $log ($(wc -c <"$log" | tr -d ' ') بايت)، و$errlog ($(wc -c <"$errlog" | tr -d ' ') بايت)."
  if [ "$rc" != 0 ]; then
    echo "تعذّر التشغيل: يشخّصه المالكُ بنفسه، فمجرى الأخطاء قد يحمل حالاتٍ محجوبة."
  fi
  # تسليمُ الذاكرة ملفٌّ واحد لا شجرةُ open/sealed، فلا يمرّ بالاستلام والتوزيع (ملاحظة Codex على #129)
  if [ "${KIMI_TASK:-next}" = memory ]; then
    echo "التالي: التسليمُ $KIMI_WORK/kimi-memory/memory_kimi_v1.json يُفحص ويُشغَّل بأداةٍ واحدة تفحصه بالمدقّق وبأعداد التكليف قبل أي نداء:"
    echo "  python3 tools/evaluate_memory.py --suite $KIMI_WORK/kimi-memory/memory_kimi_v1.json --model <النموذج> --agent <معرّفك> --out docs/probe/memory-kimi-<التاريخ>.json"
    echo "ولا inspect ولا intake ولا place: تلك لبنك v1.2."
  else
    echo "التالي: تحقّق من البنية بـ '$0 inspect'، واستلم بـ '$0 intake'، ثم وزّع بـ '$0 place'."
  fi
  return "$rc"
}

# أعدادٌ وأسماءُ مجلّداتٍ فقط — لا محتوى أي حالة
cmd_inspect() {
  [ "${KIMI_TASK:-next}" = next ] || die "هذا الأمر لبنك v1.2؛ تسليمُ الذاكرة يُفحص بـ tools/evaluate_memory.py"
  local src="$KIMI_WORK/kimi-benchmark"
  [ -d "$src" ] || die "لم يسلّم Kimi بعد: لا مجلّد $src"
  echo "التسليم في $src:"
  for p in open sealed REPORT.md disputed.json sealed/MANIFEST.json; do
    if [ -e "$src/$p" ]; then echo "  ✓ $p"; else echo "  ✗ $p ناقص"; fi
  done
  echo "  ملفّاتٌ مفتوحة: $({ find "$src/open" -name '*.json' ! -name '*.meta.json' 2>/dev/null || true; } | wc -l | tr -d ' ')"
  echo "  ملفّاتٌ جانبية:  $({ find "$src/open" -name '*.meta.json' 2>/dev/null || true; } | wc -l | tr -d ' ')"
  echo "  ملفّاتٌ محجوبة: $({ find "$src/sealed" -name '*.json' ! -name MANIFEST.json 2>/dev/null || true; } | wc -l | tr -d ' ')"
}

# الاستلامُ قبل التوزيع (ك٦): المدقّقاتُ الحقيقية وشروطُ التكليف، والتقريرُ أعدادٌ ورموزٌ بلا محتوى
cmd_intake() {
  [ "${KIMI_TASK:-next}" = next ] || die "هذا الأمر لبنك v1.2؛ تسليمُ الذاكرة يُفحص بـ tools/evaluate_memory.py"
  local src="$KIMI_WORK/kimi-benchmark"
  [ -d "$src" ] || die "لم يسلّم Kimi بعد: لا مجلّد $src"
  local out="$KIMI_WORK/logs/intake-$STAMP.json"
  mkdir -p "$KIMI_WORK/logs"
  local rc=0
  local mode=(); [ "${OPEN_ONLY:-0}" != 1 ] || mode=(--open-only)
  # حالاتُ python_sandbox لا يحكم فيها إلا الحاوية، والرقمُ العام يرفض استلامًا بلاها (needs_sandbox)؛
  # فإيصالُ الحاوية يُمرَّر من SANDBOX_RECEIPT (ومساحتُها من SANDBOX_WORKSPACE) لا بخيارٍ يُنسى
  if [ -n "${SANDBOX_RECEIPT:-}" ]; then
    mode+=(--sandbox-probes --sandbox-receipt "$SANDBOX_RECEIPT")
    [ -z "${SANDBOX_WORKSPACE:-}" ] || mode+=(--sandbox-workspace "$SANDBOX_WORKSPACE")
  else
    echo "تنبيه: بلا SANDBOX_RECEIPT تُعدّ حالاتُ الحاوية needs_sandbox، فلا يشهد هذا الاستلامُ للرقم العام." >&2
  fi
  "${PYTHON:-python3}" "$DIWAN/tools/kimi_intake.py" "$src" ${mode[@]+"${mode[@]}"} --out "$out" || rc=$?
  echo "تقريرُ الاستلام في $out — أعدادٌ ورموزٌ فقط، فيُقرأ."
  echo "والمهامُّ الوكيلة تُحكم في حاويةٍ زائلة: انظر رأسَ tools/kimi_intake.py."
  return "$rc"
}

# يستخرج أمرَ التوزيع من PLACE-KIMI-FILES.md ويشغّله، فلا تتفرّق النسختان
cmd_place() {
  [ "${KIMI_TASK:-next}" = next ] || die "هذا الأمر لبنك v1.2؛ تسليمُ الذاكرة يُفحص بـ tools/evaluate_memory.py"
  local doc="$DIWAN/docs/external/PLACE-KIMI-FILES.md"
  local block; block="$(awk '/^```bash$/{f=1;next} /^```$/{f=0} f' "$doc")"
  [ -n "$block" ] || die "لم أجد أمرَ التوزيع في $doc"
  # المستودعُ هو الذي فيه هذا الملفّ، لا الافتراضيُّ في الوثيقة
  SRC="$KIMI_WORK/kimi-benchmark" DIWAN="$DIWAN" bash -c "$block"
}

case "${1:-}" in
  setup)   cmd_setup ;;
  bundle)  cmd_bundle ;;
  run)     shift; cmd_run "${1:-}" ;;
  inspect) cmd_inspect ;;
  intake)  cmd_intake ;;
  place)   cmd_place ;;
  gameable) cmd_gameable ;;
  *) echo "الاستعمال: $0 {setup|bundle|run [ملفّ]|inspect|intake|place|gameable}" >&2; exit 2 ;;
esac
