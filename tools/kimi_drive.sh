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

cmd_setup() {
  mkdir -p "$KIMI_WORK/examples" "$KIMI_WORK/logs" "$KIMI_WORK/prompts"
  # الملفُّ الوحيد الذي ينتقل من المستودع إلى Kimi
  cp "$DIWAN/evaluation/suites/agentic_v1.json" "$KIMI_WORK/examples/agentic_v1.json"
  echo "مجلّد Kimi جاهز: $KIMI_WORK (examples/ و logs/ و prompts/)"
}

# يجمع الرأسَ والتكليفَ والتحديث، كلٌّ من بعد أول خطٍّ فاصل فيه
cmd_bundle() {
  local out="$KIMI_WORK/prompts/prompt-$STAMP.txt"
  mkdir -p "$KIMI_WORK/prompts"
  {
    sed -n '/^---$/,$p' "$DIWAN/docs/external/KIMI-WORKSPACE-HEADER.md" | tail -n +2
    printf '\n'
    cat "$DIWAN/docs/KIMI-BENCHMARK-BRIEF.md"
    printf '\n'
    sed -n '/^---$/,$p' "$DIWAN/docs/external/KIMI-NEXT.md" | tail -n +2
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
  local log="$KIMI_WORK/logs/run-$STAMP.log"
  local errlog="$KIMI_WORK/logs/run-$STAMP.err.log"
  local rc=0
  ( cd "$KIMI_WORK" && "$kimi_bin" --prompt "$(cat "$bundle")" --output-format text )     >"$log" 2>"$errlog" || rc=$?
  chmod 600 "$log" "$errlog"
  echo "انتهى التشغيل (رمز $rc). السجلّان لا يُفتحان: $log ($(wc -c <"$log" | tr -d ' ') بايت)، و$errlog ($(wc -c <"$errlog" | tr -d ' ') بايت)."
  if [ "$rc" != 0 ]; then
    echo "تعذّر التشغيل: يشخّصه المالكُ بنفسه، فمجرى الأخطاء قد يحمل حالاتٍ محجوبة."
  fi
  echo "التالي: تحقّق من البنية بـ '$0 inspect'، واستلم بـ '$0 intake'، ثم وزّع بـ '$0 place'."
  return "$rc"
}

# أعدادٌ وأسماءُ مجلّداتٍ فقط — لا محتوى أي حالة
cmd_inspect() {
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
  local src="$KIMI_WORK/kimi-benchmark"
  [ -d "$src" ] || die "لم يسلّم Kimi بعد: لا مجلّد $src"
  local out="$KIMI_WORK/logs/intake-$STAMP.json"
  mkdir -p "$KIMI_WORK/logs"
  local rc=0
  "${PYTHON:-python3}" "$DIWAN/tools/kimi_intake.py" "$src" --out "$out" || rc=$?
  echo "تقريرُ الاستلام في $out — أعدادٌ ورموزٌ فقط، فيُقرأ."
  echo "والمهامُّ الوكيلة تُحكم في حاويةٍ زائلة: انظر رأسَ tools/kimi_intake.py."
  return "$rc"
}

# يستخرج أمرَ التوزيع من PLACE-KIMI-FILES.md ويشغّله، فلا تتفرّق النسختان
cmd_place() {
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
  *) echo "الاستعمال: $0 {setup|bundle|run [ملفّ]|inspect|intake|place}" >&2; exit 2 ;;
esac
