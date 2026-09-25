#!/bin/bash
# تنفيذ المهمتين ٢ و٣ من docs/MAC-AGENT-PROMPT.md بأمرٍ واحد على ماك المالك.
# القياس يُدفع دائمًا؛ وتسجيل المُشغِّل لا يمضي إلا بعد موافقةٍ صريحة تُكتب هنا،
# لأنّ معناه: ما يُدفع إلى power0man/diwan يصير قابلًا للتنفيذ على هذا الجهاز.
set -euo pipefail

REPO="power0man/diwan-private"   # المشغّلُ المعزول يخصّ الخاص (ق٥٢)
say() { printf '\n== %s\n' "$1"; }

cd "$(dirname "$0")/.."

say "المهمة ٢ — قياس العتاد ودفع النتيجة"
python3 tools/mac_probe.py docs/probe
git add docs/probe
if git diff --cached --quiet; then
  echo "لا جديد في القياس — لن يُنشأ commit."
else
  git commit -m "قياس: فحص إمكانات الماك (تشغيل محلي بأمر المالك)"
  git push origin main
  echo "دُفع القياس إلى main."
fi

say "المهمة ٣ — مُشغِّل Actions ذاتيّ لمستودع diwan وحده"
echo "معنى هذه الخطوة: كلُّ ما يُدفع إلى $REPO يصير قابلًا للتنفيذ على هذا الجهاز."
echo "الدافعون اليوم: أنت وجلستُك السحابية فقط. للمتابعة اكتب: نعم"
read -r REPLY
if [ "$REPLY" != "نعم" ] && [ "$REPLY" != "yes" ]; then
  echo "أُلغيت المهمة ٣ بقرارك. المهمة ٢ تمّت."
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh غير موجود. ثبّته ثم استوثق وأعد التشغيل:"
  echo "  brew install gh && gh auth login"
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "gh غير مستوثق. نفّذ: gh auth login ثم أعد تشغيل السكربت."
  exit 1
fi

RUNNER_DIR="$HOME/actions-runner-diwan"
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

if [ -f .runner ]; then
  echo "مُشغِّل مسجَّل هنا من قبل — سيُكتفى بتشغيله."
else
  ARCH="$(uname -m)"
  if [ "$ARCH" = "x86_64" ]; then GH_ARCH="x64"; else GH_ARCH="arm64"; fi
  DL="$(gh api "repos/$REPO/actions/runners/downloads" \
        -q ".[] | select(.os==\"osx\" and .architecture==\"$GH_ARCH\") | .download_url")"
  curl -fL -o runner.tar.gz "$DL"
  tar xzf runner.tar.gz && rm runner.tar.gz
  # رمز التسجيل يُستهلك في أمره ولا يُكتب في ملف (الحدّ ٣ من البرومت).
  TOKEN="$(gh api -X POST "repos/$REPO/actions/runners/registration-token" -q .token)"
  ./config.sh --url "https://github.com/$REPO" --token "$TOKEN" \
              --name mac-diwan --labels "self-hosted,macos,$GH_ARCH" --unattended
fi

./svc.sh install 2>/dev/null || true
./svc.sh start 2>/dev/null || ./svc.sh status

say "تحقّق"
gh api "repos/$REPO/actions/runners" -q '.runners[] | "\(.name): \(.status)"'
gh workflow run mac-probe -R "$REPO" \
  || echo "تعذّر تشغيل mac-probe الآن — الجلسة السحابية ستشغّله عند فحصها."

echo
echo "انتهى. جلستك السحابية سترى النتائج في فحصها الدوري؛"
echo "وإن أردتَ أسرع فأخبرها: «سُجِّل المُشغِّل»."
