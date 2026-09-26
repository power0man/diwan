# توزيع ملفّات Kimi على أماكنها

**من يشغّله:** أنت في الطرفية، أو ذكاء ديوان المحلّي (Claude). **لا تعطه Kimi إن أمكن.** فتشغيله
يعرّف Kimi بمكان مستودع ديوان على الجهاز، وKimi المحلّي يستطيع قراءة ما يعرف مكانه. واستقلاله،
أي أنه لم يرَ شيفرة ديوان ولا بنوكه، هو كل قيمته. فالأفضل أن تنتهي مهمّته عند إنتاج مجلّد
`kimi-benchmark/` في مجلّده هو.

**وإن كان Kimi هو من يشغّله**، فأعطه هذا السطر قبل الأمر:

> نفّذ الأمر التالي من داخل مجلّد `kimi-benchmark` كما هو، ولا تعدّله. ولا تفتح ولا تقرأ ولا
> تسرد أي ملفٍّ داخل `~/diwan-work`، فاستقلالك عن شيفرة ديوان شرطُ عملك. وحين ينتهي، أعد إليّ
> ما طبعه الأمر فقط.

## ما يفعله الأمر
1. يتأكّد أن مجلّد Kimi كامل، وأن مستودع ديوان موجود.
2. **يرفض الكتابة فوق نسخةٍ سابقة** من الشطر المفتوح أو من مجلّد المحجوب.
3. **يطابق البيان المختوم مع الملفّات المحجوبة قبل أي نسخ:** بصمةً وعددًا لكل ملف، ويتأكّد أنه لا
   يوجد ملفٌّ محجوب خارج البيان. يطبع ✓ أو ✗ مع الأعداد فقط، ولا يعرض أي محتوى. فإن لم يطابق،
   توقّف دون أن ينسخ شيئًا.
4. **ينسخ كل شيءٍ إلى مكانه:**

| من مجلّد Kimi | إلى |
|---|---|
| `open/` | `~/diwan-work/diwan-public/evaluation/banks/kimi_v1/open/` |
| `REPORT.md` و`disputed.json` | `~/diwan-work/diwan-public/evaluation/banks/kimi_v1/` (يحلّان محلّ نسختَي v1، وهما محفوظتان في git) |
| `sealed/MANIFEST.json` | `~/diwan-work/diwan-public/evaluation/banks/kimi_v1/sealed/` |
| `sealed/` كلّه | `~/diwan-sealed/kimi_v1/`، خارج ديوان، ولا يقرؤه غيرك |
| `arabic_general_v3_1.json` و`arabic_general_v3_2.json` و`agentic_v3.json` و`agentic_v3.meta.json` (منذ v1.2، إن وُجدت) | `~/diwan-work/diwan-public/evaluation/suites/`، وهي مفتوحةٌ كلُّها فلا تمرّ بالبيان |

5. يتأكّد أنه لا يوجد ملفٌّ محجوب داخل ديوان عدا البيان.
6. **لا يُودع ولا يدفع.** ذلك عمل ذكاء ديوان المحلّي، بعد أن ينفّذ ك١ (الفحص) وك٥ (المراجعة
   الخارجية).

## الأمر

انسخ الكتلة كلّها والصقها في الطرفية، وأنت داخل مجلّد `kimi-benchmark`. تعمل في zsh وbash، وتجري في عمليةٍ مستقلّة، فإن توقّفت لا تُغلق نافذة الطرفية:

```bash
bash <<'KIMI'
# توزيع ملفّات Kimi على أماكنها. يُشغَّل من داخل مجلّد kimi-benchmark.
# لا يقرأ شيئًا من مستودع ديوان، ولا يعرض محتوى أي ملفٍّ محجوب، ولا يُودع ولا يدفع.
set -euo pipefail
SRC="${SRC:-$PWD}"
DIWAN="${DIWAN:-$HOME/diwan-work/diwan-public}"
SEALED_DST="${SEALED_DST:-$HOME/diwan-sealed/kimi_v1}"
BANK="$DIWAN/evaluation/banks/kimi_v1"

# ١ — المصدر كامل، والمستودع موجود
for p in open sealed/MANIFEST.json REPORT.md disputed.json; do
  [ -e "$SRC/$p" ] || { echo "توقّفت: ينقص المصدرَ $p"; exit 1; }
done
[ -d "$DIWAN/.git" ] || { echo "توقّفت: لا مستودع ديوان في $DIWAN"; exit 1; }
# النسخةُ العامة وحدها: diwan-private على الماك في ~/diwan-work/diwan
case "$(git -C "$DIWAN" remote get-url origin)" in
  *power0man/diwan|*power0man/diwan.git) ;;
  *) echo "توقّفت: $DIWAN ليس نسخةَ power0man/diwan العامة"; exit 1 ;;
esac

# ٢ — لا كتابة فوق ما ليس محفوظًا في git. والتحديثُ (UPDATE=1) يشترط القائمَ ولا يمحوه:
#      المفتوحُ محفوظٌ في git، والمحجوبُ يُنقل إلى نسخةٍ مؤرَّخة بجانبه قبل النسخ.
if [ "${UPDATE:-0}" = 1 ]; then
  [ -e "$BANK/open" ] && [ -e "$SEALED_DST" ] || { echo "توقّفت: UPDATE=1 ولا بنكَ قائمًا يُحدَّث، ولم أغيّر شيئًا"; exit 1; }
  [ -z "$(git -C "$DIWAN" status --porcelain -- "$BANK")" ] || { echo "توقّفت: في $BANK تغييراتٌ غيرُ مودَعة، ولم أغيّر شيئًا"; exit 1; }
else
  [ ! -e "$BANK/open" ] || { echo "توقّفت: $BANK/open موجود من قبل، ولم أغيّر شيئًا (للتحديث: UPDATE=1)"; exit 1; }
  [ ! -e "$SEALED_DST" ] || { echo "توقّفت: $SEALED_DST موجود من قبل، ولم أغيّر شيئًا (للتحديث: UPDATE=1)"; exit 1; }
fi
# بنكا التطوير (منذ v1.2) مفتوحان كلُّهما، ويُنسخان إن سلّمهما Kimi
DEV="arabic_general_v3_1.json arabic_general_v3_2.json agentic_v3.json agentic_v3.meta.json"
for f in $DEV; do
  [ ! -e "$SRC/$f" ] || [ ! -e "$DIWAN/evaluation/suites/$f" ] || {
    echo "توقّفت: $DIWAN/evaluation/suites/$f موجود من قبل، ولم أغيّر شيئًا"; exit 1; }
done

# ٣ — البيان يطابق الملفّات المحجوبة قبل أي نسخ (أعدادٌ وعلامات فقط، بلا محتوى)
( cd "$SRC" && python3 - <<'PY'
import hashlib, json, pathlib, sys
manifest = json.load(open("sealed/MANIFEST.json", encoding="utf-8"))
files = manifest["files"]
# البيانُ يُكتب بشكلين: قائمةً فيها path (v1)، أو قاموسًا مفتاحُه المسار (v1.1).
# والمشروطُ في التكليف محتواه لا شكلُه، فيُقبل الشكلان وتُفحص البصمةُ والعدد في الحالين.
entries = ([(k, v) for k, v in files.items()] if isinstance(files, dict)
           else [(e["path"], e) for e in files])
bad = 0
for path, meta in entries:
    target = pathlib.Path(path)
    if not target.is_file():
        print("✗", path, "مفقود"); bad += 1; continue
    raw = target.read_bytes()
    ok = hashlib.sha256(raw).hexdigest() == meta["sha256"]
    # الملفُّ الجانبي وصفٌ لا حالات، فلا يُطالَب بعدد
    declared = meta.get("count", meta.get("cases"))
    if declared is not None and meta.get("kind") not in ("sidecar", "meta"):
        try:
            count = len(json.loads(raw).get("cases") or json.loads(raw).get("tasks") or [])
        except Exception:
            count = None
        ok = ok and count == declared
        print("✓" if ok else "✗", path, count, "من", declared)
    else:
        print("✓" if ok else "✗", path, "(بصمةً)")
    bad += not ok
listed = {path for path, _ in entries}
extra = [p for p in pathlib.Path("sealed").rglob("*.json")
         if p.name != "MANIFEST.json" and p.as_posix() not in listed]
if extra:
    print("✗ ملفّاتٌ محجوبة غير مذكورة في البيان:", len(extra))
    bad += 1
sys.exit(1 if bad else 0)
PY
) || { echo "توقّفت: البيان لا يطابق الملفّات المحجوبة، ولم أنسخ شيئًا"; exit 1; }

# ٤ — النسخ
if [ "${UPDATE:-0}" = 1 ]; then
  BACKUP="$SEALED_DST.before-$(date +%Y%m%d-%H%M%S)"
  mv "$SEALED_DST" "$BACKUP" && echo "المحجوبُ السابق في $BACKUP"
  rm -rf "$BANK/open"
fi
mkdir -p "$BANK/sealed" "$(dirname "$SEALED_DST")"
cp -R "$SRC/open" "$BANK/open"
cp "$SRC/REPORT.md" "$SRC/disputed.json" "$BANK/"
cp "$SRC/sealed/MANIFEST.json" "$BANK/sealed/MANIFEST.json"
cp -R "$SRC/sealed" "$SEALED_DST"
chmod -R go-rwx "$SEALED_DST"
for f in $DEV; do
  [ ! -e "$SRC/$f" ] || { mkdir -p "$DIWAN/evaluation/suites"
    cp "$SRC/$f" "$DIWAN/evaluation/suites/$f"; echo "✓ بنك تطوير: $f"; }
done

# ٥ — لا محجوب داخل ديوان عدا البيان
leak=$(find "$BANK" -path '*/sealed/*' -type f ! -name MANIFEST.json | wc -l | tr -d ' ')
[ "$leak" = "0" ] || { echo "تحذير: $leak ملفًّا محجوبًا داخل ديوان — لا تُودع شيئًا"; exit 1; }
opened=$(find "$BANK/open" -name '*.json' ! -name '*.meta.json' | wc -l | tr -d ' ')
echo "تمّ: $opened ملفًّا مفتوحًا في $BANK/open، والمحجوب في $SEALED_DST"
echo "التالي: أخبر ذكاء ديوان المحلّي أن ملفّات Kimi في مكانها، لينفّذ ك١ ثم ك٥ ويدفع."
KIMI
```

**إن توقّف الأمر،** فالرسالة تقول السبب، ولم يُنسخ شيء. أشهر الأسباب:
- **«البيان لا يطابق»:** أعده إلى Kimi ليصدر بيانًا يطابق ملفّاته (فقرة «والمحجوب» في الجزء ١ من `KIMI-NEXT.md`).
- **«موجود من قبل»:** نسخةٌ سابقة في المكان نفسه. انقلها أنت ثم أعد التشغيل، فالأمر لا يحذف شيئًا.

