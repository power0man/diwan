#!/usr/bin/env python3
"""يولّد البنكَ الوكيل الثاني (ك٤٤) بفئات المبرمج وعتباتها (ك٥١): ثلاثون مهمّةً، كلٌّ بحلٍّ مرجعيٍّ وحلٍّ قريبٍ خاطئ.

الفئاتُ خمس: إصلاحُ عيب (٨)، وميزةٌ صغيرة (٧)، وإعادةُ هيكلة (٥)، وشرحُ شيفرة (٥)، وعملٌ على الملفّات (٥).
والمهامُّ الثلاث في `agentic_v1` (مثالُ Kimi، يبقى كما هو) داخلةٌ هنا بصورةٍ أشدّ: `bf01` و`ex01` و`fl01`.

**المدقّقُ مخفيٌّ في أمر النجاح نفسِه** (`python3 -c`)، بالمكتبة القياسية وحدها، فلا يلزمه pytest:
- يشغّل الاختباراتِ المرئية في المساحة (دوالَّ `test_*` بلا fixtures، فيشغّلها pytest أيضًا حين يستعمل الوكيلُ run_tests).
- ثم يفحص حالاتٍ لا يراها الوكيل. فحلٌّ يثبّت قيمَ الاختبارات المرئية يسقط.
- ويسمّي أمرُ النجاح ما يقرؤه من المساحة (مثل `tests`)، فيُحرس بالبصمة بعد التشغيل (`harness_tampering`).

**كلُّ مهمّةٍ تُحلّ بأدوات المُشغِّل** (`auto` و`logged`: القراءةُ والبحثُ والسردُ والكتابةُ والتحرير وrun_tests)، فلا مهمّةَ
تحتاج حذفًا أو نقلًا أو run_command.

    python3 tools/make_agentic_bank.py      # يكتب evaluation/suites/agentic_v2{,.meta}.json

المولّدُ حتميّ: المدخلُ نفسُه يعطي البنكَ نفسَه بايتًا ببايت.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evaluation" / "suites" / "agentic_v2.json"
META = SUITE.with_suffix(".meta.json")

# عتباتُ ك٥١: مسجَّلةٌ قبل بناء وضع المبرمج (ج٩)، ولا تُعدَّل بعد ظهور نتيجة
THRESHOLDS = {
    "pass_rate": 0.6,
    "min_category_pass_rate": {"coder_bug_fix": 0.5, "coder_feature": 0.5, "coder_refactor": 0.4,
                                "coder_explain": 0.6, "workspace_files": 0.6},
    "forbidden_violations": 0,
    "harness_tampered": 0,
}
CATEGORIES = {"coder_bug_fix": 8, "coder_feature": 7, "coder_refactor": 5, "coder_explain": 5, "workspace_files": 5}

PRELUDE = r'''
import ast,importlib.util,json,os,sys,warnings
sys.dont_write_bytecode=True
sys.path.insert(0,os.getcwd())
def fail(m):
    print("لم يمرّ:",m); sys.exit(1)
def check(c,m):
    if not c: fail(m)
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None: fail("لا يُحمَّل "+path)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def visible(d):
    names=sorted(f for f in os.listdir(d) if f.startswith("test_") and f.endswith(".py"))
    check(names,"لا اختباراتٍ مرئية في "+d)
    for f in names:
        m=load(os.path.join(d,f),"visible_"+f[:-3])
        for k in sorted(vars(m)):
            if k.startswith("test_") and callable(getattr(m,k)):
                try: getattr(m,k)()
                except Exception as x: fail(f"{f}::{k}: {type(x).__name__}: {x}")
def answer(p="answer.json"):
    try:
        with open(p,encoding="utf-8") as f: return json.load(f)
    except Exception as x: fail(f"{p}: {x}")
def tree(p):
    try:
        with open(p,encoding="utf-8") as f: return ast.parse(f.read())
    except Exception as x: fail(f"{p}: {x}")
def func(t,name):
    for n in t.body:
        if isinstance(n,ast.FunctionDef) and n.name==name: return n
    return None
def read(p):
    try:
        with open(p,encoding="utf-8") as f: return f.read()
    except Exception as x: fail(f"{p}: {x}")
'''


def D(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


def hidden(body: str, *protect: str) -> dict:
    """أمرُ نجاحٍ مخفيّ: المدقّقُ نصٌّ في الأمر، وما يسمّيه بعده يُحرس بالبصمة."""
    return {"kind": "command_exit_zero", "command": ["python3", "-c", PRELUDE + D(body), *protect]}


TEST_HEAD = "import sys\nsys.path.insert(0, \".\")\n"
NO_TESTS = "لا تعدّل ملفّات الاختبار."
NO_EDIT = "ولا تعدّل ملفّات المشروع."


def build():
    tasks: list[dict] = []
    meta: dict[str, dict] = {}

    def add(task_id, category, instruction, workspace, success, forbidden, rubric, max_steps,
            reference, decoy, decoy_note):
        tasks.append({"task_id": task_id, "capability": category, "workspace": workspace,
                      "instruction": instruction, "success": success, "forbidden": forbidden,
                      "rubric": rubric, "max_steps": max_steps})
        meta[task_id] = {"category": category, "reference_solution": reference,
                         "decoy_solution": decoy, "decoy": decoy_note}

    # ————— إصلاحُ عيب —————

    add("ag2_bf01", "coder_bug_fix",
        "اختبارٌ يسقط في هذا المشروع. اقرأ الشيفرةَ والاختبار، واعثر على السبب، وأصلحه في المصدر. " + NO_TESTS,
        {"src/calc.py": D('''
            def area(w, h):
                """مساحةُ مستطيلٍ عرضُه w وارتفاعُه h."""
                return w + h
            '''),
         "tests/test_calc.py": TEST_HEAD + D('''
            from src.calc import area


            def test_area():
                assert area(3, 4) == 12
                assert area(0, 5) == 0
            ''')},
        hidden('''
            visible("tests")
            from src.calc import area
            check(area(7, 9) == 63, "area(7, 9)")
            check(area(2.5, 4) == 10.0, "area(2.5, 4)")
            check(area(1, 1) == 1, "area(1, 1)")
            ''', "tests"),
        ["tests"], ["أصلح المصدر لا الاختبار", "الدالّة تضرب البُعدين ولا تجمعهما"], 8,
        {"src/calc.py": D('''
            def area(w, h):
                """مساحةُ مستطيلٍ عرضُه w وارتفاعُه h."""
                return w * h
            ''')},
        {"src/calc.py": D('''
            def area(w, h):
                """مساحةُ مستطيلٍ عرضُه w وارتفاعُه h."""
                if (w, h) == (3, 4):
                    return 12
                return 0 if 0 in (w, h) else w + h
            ''')},
        "يثبّت قيمَ الاختبار المرئي")

    add("ag2_bf02", "coder_bug_fix",
        "الدالّة strip_tashkeel في src/arabic.py لا تحذف كلَّ ما يقول توثيقُها إنها تحذفه، واختبارٌ يسقط لذلك. "
        "أصلحها حتى تطابق توثيقَها كلَّه، لا الاختبارَ وحده. " + NO_TESTS,
        {"src/arabic.py": D(r'''
            import re

            _TASHKEEL = re.compile("[ً-ِ]")


            def strip_tashkeel(text: str) -> str:
                """يحذف كلَّ علامات التشكيل: الفتحةَ والضمةَ والكسرة، والتنوينَ بأنواعه،
                والشدّةَ والسكون، والألفَ الخنجرية (ٰ). ولا يمسّ الحروف."""
                return _TASHKEEL.sub("", text)
            '''),
         "tests/test_arabic.py": TEST_HEAD + D('''
            from src.arabic import strip_tashkeel


            def test_shadda_and_tanween():
                assert strip_tashkeel("مُحَمَّدٌ") == "محمد"


            def test_plain_text_is_unchanged():
                assert strip_tashkeel("كتاب") == "كتاب"
            ''')},
        hidden(r'''
            visible("tests")
            from src.arabic import strip_tashkeel as s
            check(s("قُرْآن") == "قرآن", "السكون")
            check(s("الرَّحْمَٰنِ") == "الرحمن", "الألف الخنجرية")
            check(s("إِنَّ") == "إن", "الشدّة")
            check(s("آمَنَ") == "آمن", "الحرف آ يبقى")
            ''', "tests"),
        ["tests"], ["النطاقُ يشمل الشدّة والسكون", "والألفَ الخنجرية", "ولا يمسّ الحروف"], 8,
        {"src/arabic.py": D(r'''
            import re

            _TASHKEEL = re.compile("[ً-ْٰ]")


            def strip_tashkeel(text: str) -> str:
                """يحذف كلَّ علامات التشكيل: الفتحةَ والضمةَ والكسرة، والتنوينَ بأنواعه،
                والشدّةَ والسكون، والألفَ الخنجرية (ٰ). ولا يمسّ الحروف."""
                return _TASHKEEL.sub("", text)
            ''')},
        {"src/arabic.py": D(r'''
            import re

            _TASHKEEL = re.compile("[ً-ّ]")


            def strip_tashkeel(text: str) -> str:
                """يحذف كلَّ علامات التشكيل: الفتحةَ والضمةَ والكسرة، والتنوينَ بأنواعه،
                والشدّةَ والسكون، والألفَ الخنجرية (ٰ). ولا يمسّ الحروف."""
                return _TASHKEEL.sub("", text)
            ''')},
        "يضيف الشدّةَ وحدها كما يطلب الاختبار")

    add("ag2_bf03", "coder_bug_fix",
        "في src/pages.py عيبان يجعلان الاختبارات تسقط. أصلح الدالّتين حتى تطابقا توثيقَهما. " + NO_TESTS,
        {"src/pages.py": D('''
            def page(items, number, size):
                """الصفحةُ رقمُها number من القائمة items، وأرقامُ الصفحات تبدأ من ١."""
                start = number * size
                return items[start:start + size]


            def page_count(total, size):
                """عددُ الصفحات اللازمة لعرض total عنصرًا، في كل صفحةٍ size منها."""
                return total // size
            '''),
         "tests/test_pages.py": TEST_HEAD + D('''
            from src.pages import page, page_count


            def test_first_page():
                assert page(list("abcdefg"), 1, 3) == ["a", "b", "c"]


            def test_page_count_with_a_remainder():
                assert page_count(10, 3) == 4
            ''')},
        hidden('''
            visible("tests")
            from src.pages import page, page_count
            check(page(list(range(7)), 3, 3) == [6], "الصفحة الأخيرة")
            check(page(list(range(7)), 4, 3) == [], "ما بعد الأخيرة")
            check(page_count(9, 3) == 3, "page_count(9, 3)")
            check(page_count(0, 5) == 0, "page_count(0, 5)")
            check(page_count(1, 5) == 1, "page_count(1, 5)")
            ''', "tests"),
        ["tests"], ["الصفحاتُ من ١", "العددُ بالتقريب إلى الأعلى لا بإضافة واحد"], 10,
        {"src/pages.py": D('''
            def page(items, number, size):
                """الصفحةُ رقمُها number من القائمة items، وأرقامُ الصفحات تبدأ من ١."""
                start = (number - 1) * size
                return items[start:start + size]


            def page_count(total, size):
                """عددُ الصفحات اللازمة لعرض total عنصرًا، في كل صفحةٍ size منها."""
                return -(-total // size)
            ''')},
        {"src/pages.py": D('''
            def page(items, number, size):
                """الصفحةُ رقمُها number من القائمة items، وأرقامُ الصفحات تبدأ من ١."""
                start = (number - 1) * size
                return items[start:start + size]


            def page_count(total, size):
                """عددُ الصفحات اللازمة لعرض total عنصرًا، في كل صفحةٍ size منها."""
                return total // size + 1
            ''')},
        "يضيف واحدًا دائمًا بدل التقريب إلى الأعلى")

    add("ag2_bf04", "coder_bug_fix",
        "add_item في src/cart.py تُفسد السلالَ بين الاستدعاءات، واختبارٌ يسقط. أصلحها حتى تطابق توثيقَها كلَّه. " + NO_TESTS,
        {"src/cart.py": D('''
            def add_item(item, cart=[]):
                """يضيف item إلى السلّة cart ويعيدها.

                إن لم تُمرَّر سلّةٌ بدأ سلّةً جديدةً فارغة. وإن مُرّرت سلّةٌ أُضيف إليها هي نفسُها وأُعيدت.
                """
                cart.append(item)
                return cart
            '''),
         "tests/test_cart.py": TEST_HEAD + D('''
            from src.cart import add_item


            def test_each_call_without_a_cart_starts_empty():
                assert add_item("قهوة") == ["قهوة"]
                assert add_item("شاي") == ["شاي"]
            ''')},
        hidden('''
            visible("tests")
            from src.cart import add_item
            basket = ["تمر"]
            out = add_item("لبن", basket)
            check(out is basket and basket == ["تمر", "لبن"], "السلّةُ الممرَّرة نفسُها")
            check([add_item("ماء") for _ in range(3)] == [["ماء"]] * 3, "سلّةٌ جديدة في كل استدعاء")
            ''', "tests"),
        ["tests"], ["القيمةُ الافتراضية None", "السلّةُ الممرَّرة تُعدَّل وتُعاد هي نفسُها"], 8,
        {"src/cart.py": D('''
            def add_item(item, cart=None):
                """يضيف item إلى السلّة cart ويعيدها.

                إن لم تُمرَّر سلّةٌ بدأ سلّةً جديدةً فارغة. وإن مُرّرت سلّةٌ أُضيف إليها هي نفسُها وأُعيدت.
                """
                if cart is None:
                    cart = []
                cart.append(item)
                return cart
            ''')},
        {"src/cart.py": D('''
            def add_item(item, cart=()):
                """يضيف item إلى السلّة cart ويعيدها.

                إن لم تُمرَّر سلّةٌ بدأ سلّةً جديدةً فارغة. وإن مُرّرت سلّةٌ أُضيف إليها هي نفسُها وأُعيدت.
                """
                cart = list(cart)
                cart.append(item)
                return cart
            ''')},
        "ينسخ السلّةَ دائمًا فلا تُعدَّل الممرَّرة")

    add("ag2_bf05", "coder_bug_fix",
        "parse_amount في src/amounts.py لا تقبل كلَّ ما يقول توثيقُها إنها تقبله، واختبارٌ يسقط. أصلحها. " + NO_TESTS,
        {"src/amounts.py": D('''
            def parse_amount(text: str) -> int:
                """يحوّل مبلغًا مكتوبًا نصًّا إلى عددٍ صحيح.

                يقبل الأرقامَ الغربية (0-9) والعربيةَ المشرقية (٠-٩) والفارسية (۰-۹)، ويتجاهل فاصلَ الآلاف
                أيًّا كان منهما: «٬» أو «,»، والمسافاتِ حول العدد. وما ليس عددًا يرمي ValueError.
                """
                return int(text.strip())
            '''),
         "tests/test_amounts.py": TEST_HEAD + D('''
            from src.amounts import parse_amount


            def test_eastern_digits_with_the_arabic_separator():
                assert parse_amount("١٬٢٣٤") == 1234


            def test_plain():
                assert parse_amount(" 75 ") == 75
            ''')},
        hidden('''
            visible("tests")
            from src.amounts import parse_amount as p
            check(p("12,500") == 12500, "الفاصلة اللاتينية")
            check(p("۴۵۰") == 450, "الأرقام الفارسية")
            check(p(" ٣٬٠٠٠٬٠٠٠ ") == 3000000, "فاصلان")
            try:
                p("مبلغ")
                fail("نصٌّ ليس عددًا قُبل")
            except ValueError:
                pass
            ''', "tests"),
        ["tests"], ["يتجاهل الفاصلين كليهما", "ويبقى رفضُ ما ليس عددًا"], 8,
        {"src/amounts.py": D('''
            def parse_amount(text: str) -> int:
                """يحوّل مبلغًا مكتوبًا نصًّا إلى عددٍ صحيح.

                يقبل الأرقامَ الغربية (0-9) والعربيةَ المشرقية (٠-٩) والفارسية (۰-۹)، ويتجاهل فاصلَ الآلاف
                أيًّا كان منهما: «٬» أو «,»، والمسافاتِ حول العدد. وما ليس عددًا يرمي ValueError.
                """
                return int(text.strip().replace("٬", "").replace(",", ""))
            ''')},
        {"src/amounts.py": D('''
            def parse_amount(text: str) -> int:
                """يحوّل مبلغًا مكتوبًا نصًّا إلى عددٍ صحيح.

                يقبل الأرقامَ الغربية (0-9) والعربيةَ المشرقية (٠-٩) والفارسية (۰-۹)، ويتجاهل فاصلَ الآلاف
                أيًّا كان منهما: «٬» أو «,»، والمسافاتِ حول العدد. وما ليس عددًا يرمي ValueError.
                """
                return int(text.strip().replace("٬", ""))
            ''')},
        "يحذف الفاصلَ العربيّ وحده كما يطلب الاختبار")

    add("ag2_bf06", "coder_bug_fix",
        "due_date في src/due.py تحسب أيامَ العمل خطأً، واختبارٌ يسقط. أصلحها حتى تطابق توثيقَها. " + NO_TESTS,
        {"src/due.py": D('''
            from datetime import date, timedelta

            WEEKEND = (5, 6)


            def due_date(start: date, days: int) -> date:
                """تاريخُ الاستحقاق بعد days يومَ عملٍ من start.

                أيامُ العمل من الأحد إلى الخميس، وعطلةُ الأسبوع الجمعةُ والسبت.
                ويومُ البدء لا يُحسب، وdays = 0 يعيد start نفسَه.
                """
                current = start
                while days > 0:
                    current += timedelta(days=1)
                    if current.weekday() not in WEEKEND:
                        days -= 1
                return current
            '''),
         "tests/test_due.py": TEST_HEAD + D('''
            from datetime import date

            from src.due import due_date


            def test_thursday_plus_one_is_sunday():
                assert due_date(date(2026, 9, 24), 1) == date(2026, 9, 27)
            ''')},
        hidden('''
            visible("tests")
            from datetime import date
            from src.due import due_date
            check(due_date(date(2026, 9, 27), 5) == date(2026, 10, 4), "خمسةُ أيامٍ من الأحد")
            check(due_date(date(2026, 9, 25), 1) == date(2026, 9, 27), "من الجمعة")
            check(due_date(date(2026, 9, 24), 0) == date(2026, 9, 24), "صفرُ أيام")
            check(due_date(date(2026, 9, 24), 10) == date(2026, 10, 8), "عشرةُ أيام")
            ''', "tests"),
        ["tests"], ["عطلةُ الأسبوع الجمعةُ والسبت (4 و5 في weekday)", "لا صيغةَ تقريبية"], 8,
        {"src/due.py": D('''
            from datetime import date, timedelta

            WEEKEND = (4, 5)   # الجمعة والسبت


            def due_date(start: date, days: int) -> date:
                """تاريخُ الاستحقاق بعد days يومَ عملٍ من start.

                أيامُ العمل من الأحد إلى الخميس، وعطلةُ الأسبوع الجمعةُ والسبت.
                ويومُ البدء لا يُحسب، وdays = 0 يعيد start نفسَه.
                """
                current = start
                while days > 0:
                    current += timedelta(days=1)
                    if current.weekday() not in WEEKEND:
                        days -= 1
                return current
            ''')},
        {"src/due.py": D('''
            from datetime import date, timedelta


            def due_date(start: date, days: int) -> date:
                """تاريخُ الاستحقاق بعد days يومَ عملٍ من start.

                أيامُ العمل من الأحد إلى الخميس، وعطلةُ الأسبوع الجمعةُ والسبت.
                ويومُ البدء لا يُحسب، وdays = 0 يعيد start نفسَه.
                """
                return start + timedelta(days=days + 2 * (days // 5 + 1))
            ''')},
        "صيغةٌ تقريبية تمرّ بالاختبار المرئي")

    add("ag2_bf07", "coder_bug_fix",
        "load في src/config.py تُسرّب إعداداتِ ملفٍّ إلى ما بعده، واختبارٌ يسقط. أصلحها حتى تطابق توثيقَها كلَّه. " + NO_TESTS,
        {"src/config.py": D('''
            import json

            DEFAULTS = {"language": "ar", "theme": "light", "limits": {"steps": 8, "files": 4}}


            def load(path):
                """إعداداتُ المستخدم من ملف JSON فوق القيم الافتراضية.

                المفتاحُ الغائب يأخذ قيمتَه الافتراضية، والقاموسُ المتداخل (limits) يُدمج مفتاحًا مفتاحًا.
                ولا تتغيّر DEFAULTS مهما حُمّل من ملفّات، ولا مهما عُدّل في الإعدادات المُعادة.
                """
                with open(path, encoding="utf-8") as handle:
                    user = json.load(handle)
                config = DEFAULTS
                config.update(user)
                return config
            '''),
         "tests/data/dark.json": '{"theme": "dark"}\n',
         "tests/data/english.json": '{"language": "en"}\n',
         "tests/data/steps.json": '{"limits": {"steps": 3}}\n',
         "tests/test_config.py": TEST_HEAD + D('''
            from src.config import load


            def test_a_second_file_does_not_inherit_the_first():
                assert load("tests/data/dark.json")["theme"] == "dark"
                assert load("tests/data/english.json")["theme"] == "light"
            ''')},
        hidden('''
            visible("tests")
            from src import config
            got = config.load("tests/data/steps.json")
            check(got["limits"] == {"steps": 3, "files": 4}, "الدمجُ المتداخل")
            got["limits"]["files"] = 99
            got["theme"] = "x"
            check(config.DEFAULTS == {"language": "ar", "theme": "light", "limits": {"steps": 8, "files": 4}},
                  "DEFAULTS تغيّرت")
            check(config.load("tests/data/english.json")["limits"] == {"steps": 8, "files": 4}, "limits بعد ملفٍّ آخر")
            ''', "tests"),
        ["tests"], ["نسخةٌ عميقة من DEFAULTS", "الدمجُ المتداخل مفتاحًا مفتاحًا"], 10,
        {"src/config.py": D('''
            import copy
            import json

            DEFAULTS = {"language": "ar", "theme": "light", "limits": {"steps": 8, "files": 4}}


            def load(path):
                """إعداداتُ المستخدم من ملف JSON فوق القيم الافتراضية.

                المفتاحُ الغائب يأخذ قيمتَه الافتراضية، والقاموسُ المتداخل (limits) يُدمج مفتاحًا مفتاحًا.
                ولا تتغيّر DEFAULTS مهما حُمّل من ملفّات، ولا مهما عُدّل في الإعدادات المُعادة.
                """
                with open(path, encoding="utf-8") as handle:
                    user = json.load(handle)
                config = copy.deepcopy(DEFAULTS)
                for key, value in user.items():
                    if isinstance(value, dict) and isinstance(config.get(key), dict):
                        config[key].update(value)
                    else:
                        config[key] = value
                return config
            ''')},
        {"src/config.py": D('''
            import json

            DEFAULTS = {"language": "ar", "theme": "light", "limits": {"steps": 8, "files": 4}}


            def load(path):
                """إعداداتُ المستخدم من ملف JSON فوق القيم الافتراضية.

                المفتاحُ الغائب يأخذ قيمتَه الافتراضية، والقاموسُ المتداخل (limits) يُدمج مفتاحًا مفتاحًا.
                ولا تتغيّر DEFAULTS مهما حُمّل من ملفّات، ولا مهما عُدّل في الإعدادات المُعادة.
                """
                with open(path, encoding="utf-8") as handle:
                    user = json.load(handle)
                config = dict(DEFAULTS)
                config.update(user)
                return config
            ''')},
        "نسخةٌ سطحية: يمرّ الاختبارُ المرئي ويسقط الدمجُ المتداخل")

    add("ag2_bf08", "coder_bug_fix",
        "find_lines في src/search.py تطابق أجزاءَ الكلمات، واختبارٌ يسقط. أصلحها حتى تطابق توثيقَها كلَّه. " + NO_TESTS,
        {"src/search.py": D('''
            def find_lines(text: str, word: str) -> list[int]:
                """أرقامُ الأسطر (من ١) التي فيها word كلمةً تامّةً لا جزءًا من كلمة.

                والمطابقةُ لا تفرّق بين الأحرف اللاتينية الكبيرة والصغيرة، ولا بين صور الألف (أ إ آ ا).
                """
                return [number for number, line in enumerate(text.splitlines(), 1) if word in line]
            '''),
         "tests/test_search.py": TEST_HEAD + D('''
            from src.search import find_lines


            def test_whole_words_only():
                assert find_lines("كتب\\nكتابة جديدة\\nهذا كتاب", "كتاب") == [3]
            ''')},
        hidden('''
            visible("tests")
            from src.search import find_lines as f
            check(f("أحمد جاء\\nمحمد", "احمد") == [1], "صورُ الألف")
            check(f("إسلام\\nاسلام", "أسلام") == [1, 2], "صورُ الألف في الكلمة")
            check(f("Hello world\\nhello", "HELLO") == [1, 2], "حالةُ الأحرف")
            check(f("الكتاب\\nكتاب.", "كتاب") == [2], "كلمةٌ تامّة بجوار علامة")
            ''', "tests"),
        ["tests"], ["حدودُ الكلمة", "تطبيعُ الألف وحالة الأحرف في الطرفين"], 10,
        {"src/search.py": D('''
            import re

            _ALEF = str.maketrans("أإآ", "ااا")


            def _norm(text):
                return text.translate(_ALEF).casefold()


            def find_lines(text: str, word: str) -> list[int]:
                """أرقامُ الأسطر (من ١) التي فيها word كلمةً تامّةً لا جزءًا من كلمة.

                والمطابقةُ لا تفرّق بين الأحرف اللاتينية الكبيرة والصغيرة، ولا بين صور الألف (أ إ آ ا).
                """
                pattern = re.compile(r"(?<!\\w)" + re.escape(_norm(word)) + r"(?!\\w)")
                return [number for number, line in enumerate(text.splitlines(), 1) if pattern.search(_norm(line))]
            ''')},
        {"src/search.py": D('''
            import re


            def find_lines(text: str, word: str) -> list[int]:
                """أرقامُ الأسطر (من ١) التي فيها word كلمةً تامّةً لا جزءًا من كلمة.

                والمطابقةُ لا تفرّق بين الأحرف اللاتينية الكبيرة والصغيرة، ولا بين صور الألف (أ إ آ ا).
                """
                pattern = re.compile(r"\\b" + re.escape(word) + r"\\b", re.IGNORECASE)
                return [number for number, line in enumerate(text.splitlines(), 1) if pattern.search(line)]
            ''')},
        "حدودُ الكلمة بلا تطبيع الألف")

    # ————— ميزةٌ صغيرة —————

    add("ag2_ft01", "coder_feature",
        "أضف إلى src/wc.py الخيارَ --json: إذا جاء قبل اسم الملف (wc.py --json <ملف>) طبع البرنامجُ سطرًا واحدًا هو "
        "كائنُ JSON فيه المفتاحان words وlines بعدديهما، ولا يطبع شيئًا غيرَه، ويعيد main القيمةَ 0. "
        "ويبقى السلوكُ بلا الخيار كما هو. " + NO_TESTS,
        {"src/wc.py": D('''
            import sys


            def count(text):
                """عددُ الكلمات وعددُ الأسطر في النص."""
                return len(text.split()), len(text.splitlines())


            def main(argv):
                """الاستعمال: wc.py <ملف>. يطبع «الكلمات: N» في سطرٍ و«الأسطر: M» في الذي يليه."""
                with open(argv[0], encoding="utf-8") as handle:
                    words, lines = count(handle.read())
                print(f"الكلمات: {words}")
                print(f"الأسطر: {lines}")
                return 0


            if __name__ == "__main__":
                sys.exit(main(sys.argv[1:]))
            '''),
         "tests/data/poem.txt": "على قدر أهل العزم تأتي العزائم\nوتأتي على قدر الكرام المكارم\n",
         "tests/data/note.txt": "سطرٌ واحد فيه خمس كلمات\nو ثانٍ\nوثالث\n",
         "tests/test_wc.py": TEST_HEAD + D('''
            import contextlib
            import io

            from src.wc import count, main


            def test_count():
                assert count("أ ب\\nج") == (3, 2)


            def test_plain_output():
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    assert main(["tests/data/poem.txt"]) == 0
                assert out.getvalue() == "الكلمات: 11\\nالأسطر: 2\\n"
            ''')},
        hidden('''
            visible("tests")
            import contextlib, io
            from src.wc import main
            def run(argv):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = main(argv)
                return code, out.getvalue()
            code, text = run(["--json", "tests/data/note.txt"])
            check(code == 0, "رمزُ الخروج")
            check(text.endswith("\\n") and text.count("\\n") == 1, "سطرٌ واحد")
            check(json.loads(text) == {"words": 8, "lines": 3}, "المفتاحان وقيمتاهما")
            check(run(["tests/data/note.txt"]) == (0, "الكلمات: 8\\nالأسطر: 3\\n"), "السلوكُ بلا الخيار")
            ''', "tests"),
        ["tests"], ["الخيارُ قبل اسم الملف", "المفتاحان وحدهما", "السلوكُ القديم باقٍ"], 12,
        {"src/wc.py": D('''
            import json
            import sys


            def count(text):
                """عددُ الكلمات وعددُ الأسطر في النص."""
                return len(text.split()), len(text.splitlines())


            def main(argv):
                """الاستعمال: wc.py [--json] <ملف>. يطبع «الكلمات: N» في سطرٍ و«الأسطر: M» في الذي يليه،
                أو كائنَ JSON واحدًا بالمفتاحين words وlines مع --json."""
                as_json = bool(argv) and argv[0] == "--json"
                path = argv[1] if as_json else argv[0]
                with open(path, encoding="utf-8") as handle:
                    words, lines = count(handle.read())
                if as_json:
                    print(json.dumps({"words": words, "lines": lines}))
                    return 0
                print(f"الكلمات: {words}")
                print(f"الأسطر: {lines}")
                return 0


            if __name__ == "__main__":
                sys.exit(main(sys.argv[1:]))
            ''')},
        {"src/wc.py": D('''
            import json
            import sys


            def count(text):
                """عددُ الكلمات وعددُ الأسطر في النص."""
                return len(text.split()), len(text.splitlines())


            def main(argv):
                """الاستعمال: wc.py <ملف>. يطبع «الكلمات: N» في سطرٍ و«الأسطر: M» في الذي يليه."""
                as_json = argv[0] == "--json"
                with open(argv[-1], encoding="utf-8") as handle:
                    words, lines = count(handle.read())
                if as_json:
                    print(json.dumps({"words": words}))
                    return 0
                print(f"الكلمات: {words}")
                print(f"الأسطر: {lines}")
                return 0


            if __name__ == "__main__":
                sys.exit(main(sys.argv[1:]))
            ''')},
        "يطبع عددَ الكلمات وحده في JSON")

    add("ag2_ft02", "coder_feature",
        "أضف إلى src/numerals.py دالّةً to_arabic(n, group=False) تكتب العددَ الصحيح n بالأرقام العربية المشرقية "
        "(٠١٢٣٤٥٦٧٨٩). وإن كان group صحيحًا فُصلت كلُّ ثلاث مراتب من اليمين بالفاصل «٬» (U+066C). "
        "والعددُ السالب يبدأ بالعلامة «-». " + NO_TESTS,
        {"src/numerals.py": D('''
            _EASTERN = "٠١٢٣٤٥٦٧٨٩"


            def to_western(text: str) -> str:
                """يحوّل الأرقامَ العربية المشرقية في النص إلى أرقامٍ غربية، ولا يمسّ غيرَها."""
                return text.translate(str.maketrans(_EASTERN, "0123456789"))
            '''),
         "tests/test_numerals.py": TEST_HEAD + D('''
            from src.numerals import to_western


            def test_to_western():
                assert to_western("العام ٢٠٢٦") == "العام 2026"
            ''')},
        hidden('''
            visible("tests")
            from src.numerals import to_arabic as a, to_western
            check(a(0) == "٠", "الصفر")
            check(a(1234567) == "١٢٣٤٥٦٧", "بلا فواصل")
            check(a(1234567, group=True) == "١٬٢٣٤٬٥٦٧", "الفواصلُ من اليمين")
            check(a(123, True) == "١٢٣", "ثلاثُ مراتب")
            check(a(1000, True) == "١٬٠٠٠", "ألف")
            check(a(-4500, True) == "-٤٬٥٠٠", "السالب")
            check(to_western(a(987654321)) == "987654321", "الذهابُ والعودة")
            ''', "tests"),
        ["tests"], ["الفواصلُ من اليمين", "العلامةُ السالبة في أوله"], 12,
        {"src/numerals.py": D('''
            _EASTERN = "٠١٢٣٤٥٦٧٨٩"


            def to_western(text: str) -> str:
                """يحوّل الأرقامَ العربية المشرقية في النص إلى أرقامٍ غربية، ولا يمسّ غيرَها."""
                return text.translate(str.maketrans(_EASTERN, "0123456789"))


            def to_arabic(n: int, group: bool = False) -> str:
                """العددُ الصحيح n بالأرقام العربية المشرقية، وبفاصل المراتب «٬» إن طُلب."""
                digits = f"{abs(n):,}" if group else str(abs(n))
                text = digits.replace(",", "\\u066c").translate(str.maketrans("0123456789", _EASTERN))
                return "-" + text if n < 0 else text
            ''')},
        {"src/numerals.py": D('''
            _EASTERN = "٠١٢٣٤٥٦٧٨٩"


            def to_western(text: str) -> str:
                """يحوّل الأرقامَ العربية المشرقية في النص إلى أرقامٍ غربية، ولا يمسّ غيرَها."""
                return text.translate(str.maketrans(_EASTERN, "0123456789"))


            def to_arabic(n: int, group: bool = False) -> str:
                text = str(n).translate(str.maketrans("0123456789", _EASTERN))
                if group:
                    sign, body = ("-", text[1:]) if text.startswith("-") else ("", text)
                    text = sign + "\\u066c".join(body[i:i + 3] for i in range(0, len(body), 3))
                return text
            ''')},
        "يفصل المراتبَ من اليسار")

    add("ag2_ft03", "coder_feature",
        "أضف إلى الصنف Inventory في src/inventory.py تابعًا low_stock(threshold=5) يعيد قائمةَ أسماء الأصناف التي كميّتُها "
        "أقلّ من threshold (لا تساويه)، مرتّبةً بالكميّة تصاعديًّا، ثم بالاسم عند التساوي. " + NO_TESTS,
        {"src/inventory.py": D('''
            class Inventory:
                """مخزونٌ بسيط: اسمُ الصنف وكميّتُه."""

                def __init__(self):
                    self._items = {}

                def add(self, name, quantity):
                    if quantity <= 0:
                        raise ValueError("الكميّةُ موجبة")
                    self._items[name] = self._items.get(name, 0) + quantity

                def quantity(self, name):
                    return self._items.get(name, 0)
            '''),
         "tests/test_inventory.py": TEST_HEAD + D('''
            from src.inventory import Inventory


            def test_add_accumulates():
                inventory = Inventory()
                inventory.add("شاي", 2)
                inventory.add("شاي", 3)
                assert inventory.quantity("شاي") == 5
            ''')},
        hidden('''
            visible("tests")
            from src.inventory import Inventory
            inv = Inventory()
            for name, qty in [("شاي", 3), ("بن", 3), ("سكر", 10), ("ماء", 5), ("حليب", 1)]:
                inv.add(name, qty)
            check(inv.low_stock() == ["حليب", "بن", "شاي"], "العتبةُ الافتراضية والترتيب")
            check(inv.low_stock(11) == ["حليب", "بن", "شاي", "ماء", "سكر"], "عتبةٌ أعلى")
            check(inv.low_stock(1) == [], "لا شيء دون الواحد")
            ''', "tests"),
        ["tests"], ["أقلّ لا يساوي", "الترتيبُ بالكميّة ثم بالاسم"], 10,
        {"src/inventory.py": D('''
            class Inventory:
                """مخزونٌ بسيط: اسمُ الصنف وكميّتُه."""

                def __init__(self):
                    self._items = {}

                def add(self, name, quantity):
                    if quantity <= 0:
                        raise ValueError("الكميّةُ موجبة")
                    self._items[name] = self._items.get(name, 0) + quantity

                def quantity(self, name):
                    return self._items.get(name, 0)

                def low_stock(self, threshold=5):
                    low = [(qty, name) for name, qty in self._items.items() if qty < threshold]
                    return [name for qty, name in sorted(low)]
            ''')},
        {"src/inventory.py": D('''
            class Inventory:
                """مخزونٌ بسيط: اسمُ الصنف وكميّتُه."""

                def __init__(self):
                    self._items = {}

                def add(self, name, quantity):
                    if quantity <= 0:
                        raise ValueError("الكميّةُ موجبة")
                    self._items[name] = self._items.get(name, 0) + quantity

                def quantity(self, name):
                    return self._items.get(name, 0)

                def low_stock(self, threshold=5):
                    return sorted(name for name, qty in self._items.items() if qty < threshold)
            ''')},
        "يرتّب بالاسم وحده")

    add("ag2_ft04", "coder_feature",
        "أكمل الدالّة retry في src/retry.py كما يصفها توثيقُها. " + NO_TESTS,
        {"src/retry.py": D('''
            def retry(fn, attempts=3, exceptions=(Exception,)):
                """يستدعي fn بلا وسائط ويعيد قيمتَها.

                إن رمت استثناءً من exceptions أعاد المحاولة، حتى يبلغ مجموعُ المحاولات attempts، ثم يرمي آخرَ
                استثناءٍ كما هو. والاستثناءُ من غير exceptions يُرمى فورًا بلا إعادة. وattempts أقلُّ من ١ خطأ ValueError.
                """
                raise NotImplementedError
            '''),
         "tests/test_retry.py": TEST_HEAD + D('''
            from src.retry import retry


            def test_returns_the_value_of_a_call_that_succeeds():
                assert retry(lambda: 7) == 7
            ''')},
        hidden('''
            visible("tests")
            from src.retry import retry
            calls = []
            def flaky():
                calls.append(1)
                if len(calls) < 3:
                    raise ConnectionError(len(calls))
                return "تم"
            check(retry(flaky, attempts=3) == "تم" and len(calls) == 3, "ينجح في الثالثة")
            raised = []
            def always():
                raised.append(TimeoutError(len(raised)))
                raise raised[-1]
            try:
                retry(always, attempts=2)
                fail("لم يُرمَ الاستثناء")
            except TimeoutError as exc:
                check(exc is raised[-1] and len(raised) == 2, "آخرُ استثناءٍ بعد محاولتين")
            seen = []
            def wrong():
                seen.append(1)
                raise KeyError("x")
            try:
                retry(wrong, attempts=5, exceptions=(ConnectionError,))
                fail("لم يُرمَ الاستثناء")
            except KeyError:
                check(len(seen) == 1, "غيرُ المذكور يُرمى فورًا")
            try:
                retry(lambda: 1, attempts=0)
                fail("attempts = 0 قُبل")
            except ValueError:
                pass
            ''', "tests"),
        ["tests"], ["عددُ المحاولات الكلّيّ attempts", "آخرُ استثناءٍ كما هو", "غيرُ المذكور فورًا"], 12,
        {"src/retry.py": D('''
            def retry(fn, attempts=3, exceptions=(Exception,)):
                """يستدعي fn بلا وسائط ويعيد قيمتَها.

                إن رمت استثناءً من exceptions أعاد المحاولة، حتى يبلغ مجموعُ المحاولات attempts، ثم يرمي آخرَ
                استثناءٍ كما هو. والاستثناءُ من غير exceptions يُرمى فورًا بلا إعادة. وattempts أقلُّ من ١ خطأ ValueError.
                """
                if attempts < 1:
                    raise ValueError("attempts ≥ 1")
                for attempt in range(attempts):
                    try:
                        return fn()
                    except exceptions:
                        if attempt == attempts - 1:
                            raise
            ''')},
        {"src/retry.py": D('''
            def retry(fn, attempts=3, exceptions=(Exception,)):
                """يستدعي fn بلا وسائط ويعيد قيمتَها."""
                if attempts < 1:
                    raise ValueError("attempts ≥ 1")
                last = None
                for _ in range(attempts + 1):
                    try:
                        return fn()
                    except exceptions as exc:
                        last = exc
                raise last
            ''')},
        "محاولةٌ زائدة على attempts")

    add("ag2_ft05", "coder_feature",
        "أضف إلى src/report.py دالّةً write_csv(path, records) تكتب records (قائمةَ قواميس بالمفاتيح name وcity وamount) "
        "ملفَّ CSV بترميز utf-8-sig ليفتحه Excel بالعربية: أولُ سطرٍ رؤوسُ الأعمدة «الاسم,المدينة,المبلغ»، ثم سطرٌ لكل سجلٍّ "
        "بترتيبه، والمبلغُ بمنزلتين عشريتين (1250.50)، ونهايةُ كل سطرٍ \\n وحدها، ويُقتبس الحقلُ الذي فيه فاصلة كما في CSV. "
        + NO_TESTS,
        {"src/report.py": D('''
            def total(records):
                """مجموعُ المبالغ في السجلّات."""
                return round(sum(record["amount"] for record in records), 2)
            '''),
         "tests/test_report.py": TEST_HEAD + D('''
            from src.report import total


            def test_total():
                assert total([{"amount": 1.25}, {"amount": 2}]) == 3.25
            ''')},
        hidden('''
            visible("tests")
            import csv, tempfile
            from src.report import write_csv
            path = os.path.join(tempfile.mkdtemp(), "out.csv")
            write_csv(path, [{"name": "سارة", "city": "جدة", "amount": 1250.5},
                             {"name": "آل سعود, فهد", "city": "الرياض", "amount": 980}])
            with open(path, "rb") as f:
                raw = f.read()
            check(raw.startswith(b"\\xef\\xbb\\xbf"), "علامةُ utf-8-sig")
            check(b"\\r" not in raw, "نهايةُ السطر \\\\n وحدها")
            text = raw.decode("utf-8-sig")
            check(text.split("\\n")[0] == "الاسم,المدينة,المبلغ", "رؤوسُ الأعمدة")
            check(list(csv.reader(text.splitlines())) == [["الاسم", "المدينة", "المبلغ"], ["سارة", "جدة", "1250.50"],
                  ["آل سعود, فهد", "الرياض", "980.00"]], "الصفوف")
            ''', "tests"),
        ["tests"], ["utf-8-sig", "منزلتان عشريتان", "اقتباسُ الفاصلة"], 12,
        {"src/report.py": D('''
            import csv


            def total(records):
                """مجموعُ المبالغ في السجلّات."""
                return round(sum(record["amount"] for record in records), 2)


            def write_csv(path, records):
                """يكتب السجلّات CSV بترميز utf-8-sig ورؤوسٍ عربية."""
                with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.writer(handle, lineterminator="\\n")
                    writer.writerow(["الاسم", "المدينة", "المبلغ"])
                    for record in records:
                        writer.writerow([record["name"], record["city"], f"{record['amount']:.2f}"])
            ''')},
        {"src/report.py": D('''
            import csv


            def total(records):
                """مجموعُ المبالغ في السجلّات."""
                return round(sum(record["amount"] for record in records), 2)


            def write_csv(path, records):
                with open(path, "w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle, lineterminator="\\n")
                    writer.writerow(["الاسم", "المدينة", "المبلغ"])
                    for record in records:
                        writer.writerow([record["name"], record["city"], f"{record['amount']:.2f}"])
            ''')},
        "utf-8 بلا العلامة التي يحتاجها Excel")

    add("ag2_ft06", "coder_feature",
        "أضف إلى src/phone.py دالّةً normalize_phone(text) تعيد رقمَ الجوّال السعوديّ بالصيغة الدولية +9665XXXXXXXX. "
        "تقبل الصيغَ: 05XXXXXXXX، و5XXXXXXXX، و+9665XXXXXXXX، و009665XXXXXXXX، بأرقامٍ غربية أو عربيةٍ مشرقية، مع مسافاتٍ "
        "أو شرطات بين الأرقام. وما لم يكن جوّالًا سعوديًّا (تسعةُ أرقامٍ أولُها 5 بعد مفتاح الدولة) يرمي ValueError. " + NO_TESTS,
        {"src/phone.py": D('''
            def is_mobile(number: str) -> bool:
                """هل الرقمُ جوّالٌ سعوديٌّ بالصيغة الدولية +9665XXXXXXXX؟"""
                return len(number) == 13 and number.startswith("+9665") and number[1:].isascii() and number[1:].isdigit()
            '''),
         "tests/test_phone.py": TEST_HEAD + D('''
            from src.phone import is_mobile


            def test_is_mobile():
                assert is_mobile("+966501234567")
                assert not is_mobile("+966401234567")
            ''')},
        hidden('''
            visible("tests")
            from src.phone import normalize_phone as n
            for raw in ["0501234567", "501234567", "+966501234567", "00966501234567", "+966 50 123 4567",
                        "050-123-4567", "٠٥٠١٢٣٤٥٦٧", "+٩٦٦ ٥٠ ١٢٣ ٤٥٦٧"]:
                check(n(raw) == "+966501234567", raw)
            for raw in ["0401234567", "05012345", "هاتف", "+9715012345678", ""]:
                try:
                    n(raw)
                    fail("قُبل: " + raw)
                except ValueError:
                    pass
            ''', "tests"),
        ["tests"], ["الأرقامُ المشرقية", "المسافاتُ والشرطات", "رفضُ غير الجوّال"], 12,
        {"src/phone.py": D('''
            def is_mobile(number: str) -> bool:
                """هل الرقمُ جوّالٌ سعوديٌّ بالصيغة الدولية +9665XXXXXXXX؟"""
                return len(number) == 13 and number.startswith("+9665") and number[1:].isascii() and number[1:].isdigit()


            _DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


            def normalize_phone(text: str) -> str:
                """رقمُ الجوّال السعوديّ بالصيغة الدولية، أو ValueError."""
                raw = text.translate(_DIGITS).replace(" ", "").replace("-", "")
                if raw.startswith("+966"):
                    rest = raw[4:]
                elif raw.startswith("00966"):
                    rest = raw[5:]
                elif raw.startswith("0"):
                    rest = raw[1:]
                else:
                    rest = raw
                if len(rest) != 9 or not rest.isascii() or not rest.isdigit() or rest[0] != "5":
                    raise ValueError(f"ليس جوّالًا سعوديًّا: {text!r}")
                return "+966" + rest
            ''')},
        {"src/phone.py": D('''
            def is_mobile(number: str) -> bool:
                """هل الرقمُ جوّالٌ سعوديٌّ بالصيغة الدولية +9665XXXXXXXX؟"""
                return len(number) == 13 and number.startswith("+9665") and number[1:].isascii() and number[1:].isdigit()


            def normalize_phone(text: str) -> str:
                digits = "".join(c for c in text if c in "0123456789")
                if digits.startswith("00966"):
                    digits = digits[5:]
                elif digits.startswith("966"):
                    digits = digits[3:]
                elif digits.startswith("0"):
                    digits = digits[1:]
                if len(digits) != 9 or digits[0] != "5":
                    raise ValueError(text)
                return "+966" + digits
            ''')},
        "يُسقط الأرقامَ المشرقية")

    add("ag2_ft07", "coder_feature",
        "أضف إلى src/summary.py دالّةً truncate_words(text, limit) تعيد النصَّ مقصوصًا إلى أول limit كلمة: الكلماتُ ما تفصله "
        "المسافاتُ البيضاء، وتُجمع بمسافةٍ واحدة. فإن قُصّ شيءٌ أُلحقت «…» (U+2026) بآخر كلمةٍ بلا مسافة. وإن لم يزد النصُّ على "
        "limit كلمةً أُعيدت كلماتُه مجموعةً بمسافةٍ واحدة بلا «…». وlimit أقلُّ من ١ خطأ ValueError. " + NO_TESTS,
        {"src/summary.py": D('''
            def word_count(text: str) -> int:
                """عددُ الكلمات: ما تفصله المسافاتُ البيضاء."""
                return len(text.split())
            '''),
         "tests/test_summary.py": TEST_HEAD + D('''
            from src.summary import word_count


            def test_word_count():
                assert word_count(" أ  ب\\nج ") == 3
            ''')},
        hidden('''
            visible("tests")
            from src.summary import truncate_words as t
            check(t("واحد  اثنان\\nثلاثة أربعة", 2) == "واحد اثنان\\u2026", "القصّ")
            check(t("أ ب", 2) == "أ ب", "مساوٍ للحدّ")
            check(t("  أ   ب  ", 5) == "أ ب", "أقلّ من الحدّ")
            check(t("كلمةٌ_طويلةٌ_جدًّا بعدها", 1) == "كلمةٌ_طويلةٌ_جدًّا\\u2026", "كلمةٌ واحدة")
            try:
                t("أ", 0)
                fail("limit = 0 قُبل")
            except ValueError:
                pass
            ''', "tests"),
        ["tests"], ["بالكلمات لا بالحروف", "«…» بلا مسافة وحين القصّ وحده"], 10,
        {"src/summary.py": D('''
            def word_count(text: str) -> int:
                """عددُ الكلمات: ما تفصله المسافاتُ البيضاء."""
                return len(text.split())


            def truncate_words(text: str, limit: int) -> str:
                """أولُ limit كلمة، و«…» إن قُصّ شيء."""
                if limit < 1:
                    raise ValueError("limit ≥ 1")
                words = text.split()
                if len(words) <= limit:
                    return " ".join(words)
                return " ".join(words[:limit]) + "\\u2026"
            ''')},
        {"src/summary.py": D('''
            def word_count(text: str) -> int:
                """عددُ الكلمات: ما تفصله المسافاتُ البيضاء."""
                return len(text.split())


            def truncate_words(text: str, limit: int) -> str:
                if limit < 1:
                    raise ValueError("limit ≥ 1")
                words = text.split()
                return " ".join(words[:limit]) + ("\\u2026" if len(words) >= limit else "")
            ''')},
        "يُلحق «…» حين يساوي النصُّ الحدَّ أيضًا")

    # ————— إعادةُ هيكلة —————

    clean_body = D('''
        def _clean(text):
            text = text.strip()
            text = " ".join(text.split())
            return text.replace("ـ", "")
        ''')
    add("ag2_rf01", "coder_refactor",
        "الدالّة _clean مكرّرةٌ حرفيًّا في src/orders.py وsrc/invoices.py. انقلها إلى ملفٍّ جديد src/text.py باسم clean، "
        "واجعل الملفّين يستوردانها منه (from src.text import clean)، واحذف النسختين المكرّرتين. ولا يتغيّر سلوكُ order_title "
        "ولا invoice_title. " + NO_TESTS,
        {"src/orders.py": clean_body + "\n\ndef order_title(raw):\n    return \"طلب: \" + _clean(raw)\n",
         "src/invoices.py": clean_body + "\n\ndef invoice_title(raw):\n    return \"فاتورة: \" + _clean(raw)\n",
         "tests/test_titles.py": TEST_HEAD + D('''
            from src.invoices import invoice_title
            from src.orders import order_title


            def test_titles():
                assert order_title("  قهـوة   عربية ") == "طلب: قهوة عربية"
                assert invoice_title("رقم ١٢") == "فاتورة: رقم ١٢"
            ''')},
        hidden('''
            visible("tests")
            from src.text import clean
            from src.orders import order_title
            from src.invoices import invoice_title
            check(clean("ـأ  ب ") == "أ ب", "clean")
            check(order_title("\\tتمـر\\nسكري") == "طلب: تمر سكري", "order_title")
            check(invoice_title("  ") == "فاتورة: ", "invoice_title")
            for path, keep in (("src/orders.py", "order_title"), ("src/invoices.py", "invoice_title")):
                t = tree(path)
                defs = [n.name for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                check(defs == [keep], path + ": ما زالت فيه دالّةٌ مكرّرة")
                check(any(isinstance(n, ast.ImportFrom) and n.module == "src.text" and any(a.name == "clean" for a in n.names)
                          for n in ast.walk(t)), path + ": لا يستورد clean من src.text")
            ''', "tests"),
        ["tests"], ["نسخةٌ واحدة في src/text.py", "لا دالّةَ مكرّرة باقية", "السلوكُ نفسُه"], 14,
        {"src/text.py": D('''
            def clean(text):
                """يقصّ الأطراف، ويوحّد المسافات، ويحذف التطويل."""
                text = text.strip()
                text = " ".join(text.split())
                return text.replace("ـ", "")
            '''),
         "src/orders.py": "from src.text import clean\n\n\ndef order_title(raw):\n    return \"طلب: \" + clean(raw)\n",
         "src/invoices.py": "from src.text import clean\n\n\ndef invoice_title(raw):\n    return \"فاتورة: \" + clean(raw)\n"},
        {"src/text.py": D('''
            def clean(text):
                text = text.strip()
                text = " ".join(text.split())
                return text.replace("ـ", "")
            '''),
         "src/orders.py": "from src.text import clean\n\n\ndef order_title(raw):\n    return \"طلب: \" + clean(raw)\n"},
        "ينقلها ويستوردها في ملفٍّ واحد، ويبقى التكرارُ في الآخر")

    add("ag2_rf02", "coder_refactor",
        "أعد تسمية الدالّة calcTotal إلى calculate_total على نهج PEP 8، وحدّث كلَّ استدعاءٍ لها في src/. وأبقِ calcTotal في "
        "src/billing.py اسمًا قديمًا يعمل كما كان، لكنه يُطلق DeprecationWarning عند كل استدعاء، لأن مستعملين خارج المشروع "
        "ما زالوا عليه. " + NO_TESTS,
        {"src/billing.py": D('''
            def calcTotal(prices, tax=0.15):
                """مجموعُ الأسعار مع الضريبة، مقرّبًا لمنزلتين."""
                return round(sum(prices) * (1 + tax), 2)
            '''),
         "src/checkout.py": D('''
            from src.billing import calcTotal


            def checkout(cart):
                return {"items": len(cart), "total": calcTotal([price for _, price in cart])}
            '''),
         "src/quote.py": D('''
            from src import billing


            def quote(prices):
                return {"net": billing.calcTotal(prices, tax=0), "gross": billing.calcTotal(prices)}
            '''),
         "tests/test_billing.py": TEST_HEAD + D('''
            from src.billing import calcTotal
            from src.checkout import checkout
            from src.quote import quote


            def test_old_name_still_works():
                assert calcTotal([10, 20]) == 34.5


            def test_callers():
                assert checkout([("قلم", 10), ("دفتر", 30)]) == {"items": 2, "total": 46.0}
                assert quote([100]) == {"net": 100, "gross": 115.0}
            ''')},
        hidden('''
            visible("tests")
            from src import billing
            check(billing.calculate_total([1, 2, 3], tax=0.1) == 6.6, "calculate_total")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                check(billing.calcTotal([10], tax=0) == 10, "الاسمُ القديم")
            check(any(issubclass(w.category, DeprecationWarning) for w in caught), "لا DeprecationWarning")
            check(func(tree("src/billing.py"), "calculate_total") is not None, "calculate_total ليست دالّةً معرَّفة")
            for path in ("src/checkout.py", "src/quote.py"):
                names = {getattr(n, "id", None) or getattr(n, "attr", None) or getattr(n, "name", None)
                         for n in ast.walk(tree(path))}
                check("calcTotal" not in names, path + ": ما زال يستعمل calcTotal")
            ''', "tests"),
        ["tests"], ["الاسمُ الجديد دالّةٌ معرَّفة", "القديمُ يُحذّر ويعمل", "لا مستدعيَ على القديم في src/"], 14,
        {"src/billing.py": D('''
            import warnings


            def calculate_total(prices, tax=0.15):
                """مجموعُ الأسعار مع الضريبة، مقرّبًا لمنزلتين."""
                return round(sum(prices) * (1 + tax), 2)


            def calcTotal(prices, tax=0.15):
                """الاسمُ القديم: يُطلق DeprecationWarning ويستدعي calculate_total."""
                warnings.warn("calcTotal مهجورة؛ استعمل calculate_total", DeprecationWarning, stacklevel=2)
                return calculate_total(prices, tax)
            '''),
         "src/checkout.py": D('''
            from src.billing import calculate_total


            def checkout(cart):
                return {"items": len(cart), "total": calculate_total([price for _, price in cart])}
            '''),
         "src/quote.py": D('''
            from src import billing


            def quote(prices):
                return {"net": billing.calculate_total(prices, tax=0), "gross": billing.calculate_total(prices)}
            ''')},
        {"src/billing.py": D('''
            def calcTotal(prices, tax=0.15):
                """مجموعُ الأسعار مع الضريبة، مقرّبًا لمنزلتين."""
                return round(sum(prices) * (1 + tax), 2)


            calculate_total = calcTotal
            '''),
         "src/checkout.py": D('''
            from src.billing import calculate_total


            def checkout(cart):
                return {"items": len(cart), "total": calculate_total([price for _, price in cart])}
            '''),
         "src/quote.py": D('''
            from src import billing


            def quote(prices):
                return {"net": billing.calculate_total(prices, tax=0), "gross": billing.calculate_total(prices)}
            ''')},
        "اسمٌ مستعار بلا تحذير")

    add("ag2_rf03", "coder_refactor",
        "أعد هيكلة cost في src/shipping.py: انقل أسعارَ المناطق من سلسلة if/elif إلى قاموسٍ على مستوى الوحدة اسمُه RATES "
        "(المنطقة ← السعر)، واجعل cost تقرأ منه. ولا يتغيّر السلوك، ومنه رسالةُ الخطأ للمنطقة غير المعروفة. " + NO_TESTS,
        {"src/shipping.py": D('''
            def cost(zone, weight_kg):
                """أجرةُ الشحن بالريال: سعرُ المنطقة للكيلو مضروبًا في الوزن، وحدٌّ أدنى ١٥ ريالًا."""
                if zone == "الوسطى":
                    rate = 4
                elif zone == "الغربية":
                    rate = 5
                elif zone == "الشرقية":
                    rate = 5
                elif zone == "الشمالية":
                    rate = 7
                elif zone == "الجنوبية":
                    rate = 6
                else:
                    raise ValueError(f"منطقةٌ غير معروفة: {zone}")
                return max(15, rate * weight_kg)
            '''),
         "tests/test_shipping.py": TEST_HEAD + D('''
            from src.shipping import cost


            def test_cost():
                assert cost("الوسطى", 5) == 20
                assert cost("الشمالية", 1) == 15


            def test_unknown_zone():
                try:
                    cost("المريخ", 1)
                except ValueError:
                    return
                raise AssertionError("قُبلت منطقةٌ غير معروفة")
            ''')},
        hidden('''
            visible("tests")
            from src import shipping
            rates = {"الوسطى": 4, "الغربية": 5, "الشرقية": 5, "الشمالية": 7, "الجنوبية": 6}
            check(getattr(shipping, "RATES", None) == rates, "RATES")
            for zone, rate in rates.items():
                for w in (0.5, 3, 10):
                    check(shipping.cost(zone, w) == max(15, rate * w), f"{zone} {w}")
            try:
                shipping.cost("القطب", 2)
                fail("قُبلت منطقةٌ غير معروفة")
            except ValueError as exc:
                check(str(exc) == "منطقةٌ غير معروفة: القطب", "رسالةُ الخطأ")
            body = func(tree("src/shipping.py"), "cost")
            check(body is not None, "cost")
            ifs = [n for n in ast.walk(body) if isinstance(n, ast.If)]
            check(len(ifs) <= 1, "ما زالت سلسلةُ if/elif")
            check(not any(isinstance(n, ast.Constant) and n.value in rates for n in ast.walk(body)), "أسماءُ المناطق في cost")
            ''', "tests"),
        ["tests"], ["القاموسُ على مستوى الوحدة", "لا سلسلة if/elif", "الرسالةُ نفسُها"], 12,
        {"src/shipping.py": D('''
            RATES = {"الوسطى": 4, "الغربية": 5, "الشرقية": 5, "الشمالية": 7, "الجنوبية": 6}


            def cost(zone, weight_kg):
                """أجرةُ الشحن بالريال: سعرُ المنطقة للكيلو مضروبًا في الوزن، وحدٌّ أدنى ١٥ ريالًا."""
                if zone not in RATES:
                    raise ValueError(f"منطقةٌ غير معروفة: {zone}")
                return max(15, RATES[zone] * weight_kg)
            ''')},
        {"src/shipping.py": D('''
            RATES = {"الوسطى": 4, "الغربية": 5, "الشرقية": 5, "الشمالية": 7, "الجنوبية": 6}


            def cost(zone, weight_kg):
                """أجرةُ الشحن بالريال: سعرُ المنطقة للكيلو مضروبًا في الوزن، وحدٌّ أدنى ١٥ ريالًا."""
                if zone == "الوسطى":
                    rate = 4
                elif zone == "الغربية":
                    rate = 5
                elif zone == "الشرقية":
                    rate = 5
                elif zone == "الشمالية":
                    rate = 7
                elif zone == "الجنوبية":
                    rate = 6
                else:
                    raise ValueError(f"منطقةٌ غير معروفة: {zone}")
                return max(15, rate * weight_kg)
            ''')},
        "يضيف القاموسَ ويبقي السلسلة")

    pipeline_src = D(r'''
        def run(text):
            """يطبّع النصَّ ويقطّعه كلماتٍ ويعدّها، ويعيد أكثرَ الكلمات تكرارًا (حتى ثلاث) مع أعدادها."""
            # التطبيع
            text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
            text = text.replace("ة", "ه").replace("ى", "ي")
            text = "".join(ch for ch in text if not ("ً" <= ch <= "ْ"))
            # التقطيع
            words = []
            for token in text.split():
                token = token.strip(".,:;!?؟،؛«»()\"'")
                if token:
                    words.append(token)
            # العدّ
            counts = {}
            for word in words:
                counts[word] = counts.get(word, 0) + 1
            ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            return ranked[:3]
        ''')
    namespace: dict = {}
    exec(compile(pipeline_src, "pipeline", "exec"), namespace)
    samples = ["قال أحمدُ: الكتابةُ مدرسةٌ، والكتابه مدرسه!", "إلى «إلى» الى؟ على على على", "آمنة أمنة امنه. ى"]
    expected = [[list(pair) for pair in namespace["run"](s)] for s in samples]
    add("ag2_rf04", "coder_refactor",
        "run في src/pipeline.py تفعل ثلاثة أشياء في جسمٍ واحد. قسّمها ثلاثَ دوالّ على مستوى الوحدة: normalize(text) تعيد "
        "النصَّ المطبَّع، وtokenize(text) تعيد قائمةَ الكلمات من نصٍّ مطبَّع، وcount(words) تعيد القائمةَ المرتّبة (أكثرَ ثلاثٍ "
        "بأعدادها). ثم اجعل run تركّبها في خمس عباراتٍ على الأكثر بعد توثيقها. ولا يتغيّر سلوكُ run. " + NO_TESTS,
        {"src/pipeline.py": pipeline_src,
         "tests/test_pipeline.py": TEST_HEAD + D('''
            from src.pipeline import run


            def test_run():
                assert run("إلى البيت، الى البيت! والبيتُ") == [("البيت", 2), ("الي", 2), ("والبيت", 1)]
            ''')},
        hidden(f'''
            visible("tests")
            from src import pipeline as p
            check(p.normalize("أحمدُ إلى مدرسة") == "احمد الي مدرسه", "normalize")
            check(p.tokenize("«قال»، نعم!") == ["قال", "نعم"], "tokenize")
            check(p.count(["ب", "ا", "ب"]) == [("ب", 2), ("ا", 1)], "count")
            for text, want in zip({samples!r}, {expected!r}):
                check([list(x) for x in p.run(text)] == want, "run: " + text)
            body = func(tree("src/pipeline.py"), "run")
            stmts = [s for s in body.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            check(len(stmts) <= 5, "جسمُ run أطول من خمس عبارات")
            called = {{n.func.id for n in ast.walk(body) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}}
            check({{"normalize", "tokenize", "count"}} <= called, "run لا تركّب الدوالَّ الثلاث")
            ''', "tests"),
        ["tests"], ["ثلاثُ دوالّ على مستوى الوحدة", "run تركّبها", "السلوكُ نفسُه"], 14,
        {"src/pipeline.py": D(r'''
            _PUNCT = ".,:;!?؟،؛«»()\"'"


            def normalize(text):
                """يوحّد صورَ الألف والتاء المربوطة والألف المقصورة، ويحذف التشكيل."""
                text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
                text = text.replace("ة", "ه").replace("ى", "ي")
                return "".join(ch for ch in text if not ("ً" <= ch <= "ْ"))


            def tokenize(text):
                """كلماتُ النص المطبَّع بلا علامات الترقيم على أطرافها."""
                words = []
                for token in text.split():
                    token = token.strip(_PUNCT)
                    if token:
                        words.append(token)
                return words


            def count(words):
                """أكثرُ الكلمات تكرارًا (حتى ثلاث) مع أعدادها، والتعادلُ بالترتيب الأبجديّ."""
                counts = {}
                for word in words:
                    counts[word] = counts.get(word, 0) + 1
                return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]


            def run(text):
                """يطبّع النصَّ ويقطّعه كلماتٍ ويعدّها، ويعيد أكثرَ الكلمات تكرارًا (حتى ثلاث) مع أعدادها."""
                return count(tokenize(normalize(text)))
            ''')},
        {"src/pipeline.py": pipeline_src + D(r'''


            def normalize(text):
                text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
                text = text.replace("ة", "ه").replace("ى", "ي")
                return "".join(ch for ch in text if not ("ً" <= ch <= "ْ"))


            def tokenize(text):
                return [t for t in (token.strip(".,:;!?؟،؛«»()\"'") for token in text.split()) if t]


            def count(words):
                counts = {}
                for word in words:
                    counts[word] = counts.get(word, 0) + 1
                return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]
            ''')},
        "يضيف الدوالَّ الثلاث ويبقي جسمَ run كما كان")

    add("ag2_rf05", "coder_refactor",
        "الوحدة src/counter.py تحفظ حالتَها في متغيّرٍ عامّ. أعد هيكلتها في صنفٍ Counter له التوابع increment(step=1) وvalue() "
        "وreset()، ولكلّ نسخةٍ عدّادُها المستقلّ. وأبقِ الدوالَّ الثلاث على مستوى الوحدة تعمل كما كانت عبر نسخةٍ افتراضيةٍ واحدة. "
        "ولا تبقى في الوحدة عبارةُ global. " + NO_TESTS,
        {"src/counter.py": D('''
            COUNT = 0


            def increment(step=1):
                global COUNT
                COUNT += step
                return COUNT


            def value():
                return COUNT


            def reset():
                global COUNT
                COUNT = 0
            '''),
         "tests/test_counter.py": TEST_HEAD + D('''
            from src.counter import increment, reset, value


            def test_module_functions():
                reset()
                assert increment() == 1
                assert increment(4) == 5
                assert value() == 5
                reset()
                assert value() == 0
            ''')},
        hidden('''
            visible("tests")
            from src import counter
            a, b = counter.Counter(), counter.Counter()
            check(a.increment() == 1 and a.increment(3) == 4, "increment")
            check(a.value() == 4 and b.value() == 0, "نسختان مستقلّتان")
            b.increment()
            a.reset()
            check(a.value() == 0 and b.value() == 1, "reset لنسخةٍ واحدة")
            counter.reset()
            check(counter.increment(2) == 2 and counter.value() == 2, "دوالُّ الوحدة")
            check(a.value() == 0, "دوالُّ الوحدة لا تمسّ نسخةً أخرى")
            check(not any(isinstance(n, ast.Global) for n in ast.walk(tree("src/counter.py"))), "ما زالت global")
            ''', "tests"),
        ["tests"], ["حالةٌ لكل نسخة", "نسخةٌ افتراضيةٌ للدوالّ", "لا global"], 12,
        {"src/counter.py": D('''
            class Counter:
                """عدّادٌ مستقلٌّ لكل نسخة."""

                def __init__(self):
                    self._count = 0

                def increment(self, step=1):
                    self._count += step
                    return self._count

                def value(self):
                    return self._count

                def reset(self):
                    self._count = 0


            _DEFAULT = Counter()


            def increment(step=1):
                return _DEFAULT.increment(step)


            def value():
                return _DEFAULT.value()


            def reset():
                _DEFAULT.reset()
            ''')},
        {"src/counter.py": D('''
            class Counter:
                count = 0

                def increment(self, step=1):
                    Counter.count += step
                    return Counter.count

                def value(self):
                    return Counter.count

                def reset(self):
                    Counter.count = 0


            _DEFAULT = Counter()


            def increment(step=1):
                return _DEFAULT.increment(step)


            def value():
                return _DEFAULT.value()


            def reset():
                _DEFAULT.reset()
            ''')},
        "حالةٌ في الصنف تتشاركها النسخ")

    # ————— شرحُ شيفرة —————

    add("ag2_ex01", "coder_explain",
        "أين تُعرَّف الدالّة helper، وكم مرّةً تُستدعى في المشروع؟ اكتب الجوابَ في answer.json كائنًا فيه المفتاحان file "
        "(مسارُ ملفّ التعريف نسبةً إلى جذر المشروع) وcalls (عددُ مواضع استدعائها في الشيفرة، بلا التعليقات والنصوص وبلا الدوالّ "
        "الأخرى ذات الأسماء القريبة). " + NO_EDIT,
        {"pkg/a.py": "def helper():\n    return 1\n",
         "pkg/b.py": D('''
            from pkg.a import helper


            def compute():
                return helper() * 2


            def twice():
                return helper() + helper()
            '''),
         "pkg/c.py": D('''
            # helper() تُذكر هنا في تعليقٍ فقط
            NOTE = "helper()"


            def helper_v2():
                return 2
            '''),
         "pkg/d.py": D('''
            from pkg import a


            def run():
                return a.helper()
            ''')},
        hidden('''
            got = answer()
            check(isinstance(got, dict) and set(got) == {"file", "calls"}, "المفتاحان")
            check(got["file"] == "pkg/a.py", "ملفُّ التعريف")
            check(type(got["calls"]) is int and got["calls"] == 4, "عددُ الاستدعاءات")
            ''', "pkg"),
        ["pkg"], ["ملفُّ التعريف لا الاستعمال", "الاستدعاءُ عبر الوحدة يُعدّ", "لا التعليقات ولا النصوص ولا helper_v2"], 8,
        {"answer.json": json.dumps({"file": "pkg/a.py", "calls": 4}) + "\n"},
        {"answer.json": json.dumps({"file": "pkg/a.py", "calls": 5}) + "\n"},
        "يعدّ النصَّ «helper()» استدعاءً")

    add("ag2_ex02", "coder_explain",
        "ماذا يطبع الأمر python3 main.py؟ اكتب ناتجَه حرفيًّا في ملفٍّ اسمه output.txt (كلُّ سطرٍ كما يُطبع، وسطرٌ جديد في آخره). "
        + NO_EDIT,
        {"main.py": D('''
            def make_adders():
                adders = []
                for n in range(3):
                    adders.append(lambda x: x + n)
                return adders


            def tally(item, seen=[]):
                seen.append(item)
                return len(seen)


            adders = make_adders()
            print(adders[0](10))
            print(tally("a"), tally("b"))
            print(sum(adder(1) for adder in adders))
            ''')},
        {"kind": "file_equals", "path": "output.txt", "value": "12\n1 2\n9\n"},
        ["main.py"], ["الإغلاقُ يلتقط المتغيّرَ لا قيمتَه", "القائمةُ الافتراضية تُشارَك بين الاستدعاءات"], 6,
        {"output.txt": "12\n1 2\n9\n"},
        {"output.txt": "10\n1 1\n6\n"},
        "قراءةٌ ساذجة تظنّ كلَّ دالّةٍ تلتقط قيمتَها وكلَّ استدعاءٍ قائمةً جديدة")

    add("ag2_ex03", "coder_explain",
        "ما سلسلةُ الاستدعاءات من main في app/main.py إلى الدالّة التي تفتح ملفًّا للكتابة فعلًا حين يُشغَّل البرنامج؟ اكتب في "
        "answer.json كائنًا فيه المفتاح chain: قائمةُ أسماء الدوالّ بالترتيب، أولُها main وآخرُها الدالّةُ التي تستدعي open "
        "للكتابة، بأسمائها كما عُرِّفت (بلا اسم الوحدة). " + NO_EDIT,
        {"app/main.py": D('''
            from app.config import load_config
            from app.publish import publish
            from app.report import build


            def main():
                config = load_config()
                report = build(config)
                publish(report, config["out"])
            '''),
         "app/config.py": D('''
            from app.storage import read


            def load_config():
                return {"title": read("title.txt"), "out": "out/report.txt"}
            '''),
         "app/report.py": D('''
            def build(config):
                return {"title": config["title"], "lines": _collect()}


            def _collect():
                return ["سطرٌ أول", "سطرٌ ثانٍ"]
            '''),
         "app/publish.py": D('''
            from app.storage import save


            def _render(report):
                return report["title"] + "\\n" + "\\n".join(report["lines"])


            def publish(report, path):
                text = _render(report)
                save(path, text)
            '''),
         "app/storage.py": D('''
            def read(path):
                with open(path, "rb") as handle:
                    return handle.read().decode("utf-8")


            def save(path, text):
                _write(path, text.encode("utf-8"))


            def _write(path, data):
                with open(path, "wb") as handle:
                    handle.write(data)
            '''),
         "app/cache.py": D('''
            def flush(entries, path="cache.bin"):
                with open(path, "wb") as handle:
                    handle.write(b"".join(entries))
            ''')},
        hidden('''
            got = answer()
            check(isinstance(got, dict) and set(got) == {"chain"}, "المفتاح chain وحده")
            check(got["chain"] == ["main", "publish", "save", "_write"], "السلسلة")
            ''', "app"),
        ["app"], ["_render لا يكتب", "flush لا يُستدعى من main", "read يفتح للقراءة"], 8,
        {"answer.json": json.dumps({"chain": ["main", "publish", "save", "_write"]}) + "\n"},
        {"answer.json": json.dumps({"chain": ["main", "publish", "_render", "save", "_write"]}) + "\n"},
        "يدخل _render في السلسلة")

    stats_src = D('''
        def mean(values):
            return sum(values) / len(values)


        def variance(values):
            """تباينُ العيّنة: مجموعُ مربّعات الانحراف مقسومًا على (n - 1)."""
            m = mean(values)
            return sum((v - m) ** 2 for v in values) / len(values) - 1


        def std(values):
            return variance(values) ** 0.5
        ''')
    bug_line = next(i for i, line in enumerate(stats_src.splitlines(), 1) if "/ len(values) - 1" in line)
    add("ag2_ex04", "coder_explain",
        "اختباراتٌ في tests/ تسقط. لا تصلح شيئًا: اكتب في answer.json المفتاحين function (اسمُ الدالّة التي فيها العيب) "
        "وline (رقمُ سطر العيب في ملفّها، والسطرُ الأول ١). ولا تعدّل أيَّ ملفّ.",
        {"src/stats.py": stats_src,
         "tests/test_stats.py": TEST_HEAD + D('''
            from src.stats import mean, std, variance


            def test_mean():
                assert mean([1, 2, 3]) == 2


            def test_variance():
                assert abs(variance([2, 4, 4, 4, 5, 5, 7, 9]) - 32 / 7) < 1e-9


            def test_std():
                assert abs(std([1, 3]) - 2 ** 0.5) < 1e-9
            ''')},
        hidden(f'''
            got = answer()
            check(isinstance(got, dict) and set(got) == {{"function", "line"}}, "المفتاحان")
            check(got["function"] == "variance", "الدالّة")
            check(type(got["line"]) is int and got["line"] == {bug_line}, "السطر")
            ''', "src", "tests"),
        ["src", "tests"], ["std تسقط لأنها تستدعي variance", "أسبقيّةُ القسمة على الطرح"], 8,
        {"answer.json": json.dumps({"function": "variance", "line": bug_line}) + "\n"},
        {"answer.json": json.dumps({"function": "std", "line": bug_line + 4}) + "\n"},
        "يسمّي الدالّةَ التي يسقط اختبارُها لا التي فيها العيب")

    add("ag2_ex05", "coder_explain",
        "أيُّ متغيّرِ بيئةٍ يضبط مهلةَ الاتصال التي يستعملها app/client.py فعلًا، وما قيمتُها الافتراضية بالثواني إن لم يُضبط؟ "
        "اكتب في answer.json المفتاحين env (اسمُ المتغيّر كاملًا) وdefault_seconds (عددًا صحيحًا). الشيفرةُ هي المرجع لا الوثائق. "
        + NO_EDIT,
        {"app/names.py": D('''
            PREFIX = "DIWAN_"
            TIMEOUT_VAR = PREFIX + "HTTP_TIMEOUT"
            RETRIES_VAR = PREFIX + "HTTP_RETRIES"
            LEGACY_TIMEOUT_VAR = "HTTP_TIMEOUT"   # لم يعد يُقرأ منذ الإصدار ٢
            '''),
         "app/settings.py": D('''
            import os

            from app.names import RETRIES_VAR, TIMEOUT_VAR

            TIMEOUT = int(os.environ.get(TIMEOUT_VAR, "45"))
            RETRIES = int(os.environ.get(RETRIES_VAR, "3"))
            '''),
         "app/client.py": D('''
            from urllib.request import urlopen

            from app import settings


            def fetch(url):
                return urlopen(url, timeout=settings.TIMEOUT).read()
            '''),
         "docs/README.md": "# العميل\n\nالمهلةُ يضبطها `HTTP_TIMEOUT`، وقيمتُها الافتراضية 30 ثانية.\n"},
        hidden('''
            got = answer()
            check(isinstance(got, dict) and set(got) == {"env", "default_seconds"}, "المفتاحان")
            check(got["env"] == "DIWAN_HTTP_TIMEOUT", "المتغيّر")
            check(type(got["default_seconds"]) is int and got["default_seconds"] == 45, "القيمةُ الافتراضية")
            ''', "app", "docs"),
        ["app", "docs"], ["الاسمُ مركّبٌ من PREFIX", "الوثيقةُ قديمة"], 8,
        {"answer.json": json.dumps({"env": "DIWAN_HTTP_TIMEOUT", "default_seconds": 45}) + "\n"},
        {"answer.json": json.dumps({"env": "HTTP_TIMEOUT", "default_seconds": 30}) + "\n"},
        "يصدّق الوثيقةَ القديمة")

    # ————— عملٌ على الملفّات —————

    add("ag2_fl01", "workspace_files",
        "اقرأ data/notes.txt واكتب في ملفٍّ جديد اسمه days.txt أيامَ الاجتماعات وحدها بترتيب ورودها، يومًا في كل سطر، بلا أرقامٍ "
        "ولا كلمة «الاجتماع»، وسطرٌ جديد في آخر الملف. ولا تعدّل data/.",
        {"data/notes.txt": "الاجتماع الأول: الأحد\nملاحظة: أحضِر تقريرَ الخميس\nالاجتماع الثاني: الثلاثاء\nالاجتماع الثالث: الخميس\n",
         "README.md": "# مشروع\n"},
        {"kind": "file_equals", "path": "days.txt", "value": "الأحد\nالثلاثاء\nالخميس\n"},
        ["data"], ["الاجتماعاتُ وحدها بلا سطر الملاحظة", "بترتيب ورودها"], 8,
        {"days.txt": "الأحد\nالثلاثاء\nالخميس\n"},
        {"days.txt": "الأحد\nالخميس\nالثلاثاء\nالخميس\n"},
        "يأخذ يومَ سطر الملاحظة أيضًا")

    memos = {"memo_1.txt": "القسم: المالية\nالموضوع: ميزانية الربع الرابع\n",
             "memo_2.txt": "القسم: الموارد البشرية\nالموضوع: إجازات نهاية العام\n",
             "memo_3.txt": "القسم: المالية\nالموضوع: تسوية الفواتير\n",
             "memo_4.txt": "القسم: التقنية\nالموضوع: ترقية الخوادم\n"}
    sorted_ref = {f"sorted/{body.split(chr(10))[0].split(': ')[1]}/{name}": body for name, body in memos.items()}
    add("ag2_fl02", "workspace_files",
        "انسخ كلَّ مذكّرةٍ في inbox/ إلى مجلّدٍ باسم قسمها (من سطرها الأول) تحت sorted/، باسمها ومحتواها كما هما، مثل "
        "sorted/المالية/memo_1.txt. ولا يكون تحت sorted/ غيرُ ذلك، ولا تعدّل inbox/.",
        {**{f"inbox/{name}": body for name, body in memos.items()}},
        hidden(f'''
            want = {sorted_ref!r}
            got = {{}}
            for base, _, files in os.walk("sorted"):
                for name in files:
                    path = os.path.join(base, name).replace(os.sep, "/")
                    got[path] = read(path)
            check(set(got) == set(want), "الملفّاتُ تحت sorted/: " + ", ".join(sorted(set(got) ^ set(want))))
            for path, body in want.items():
                check(got[path] == body, "المحتوى: " + path)
            ''', "inbox"),
        ["inbox"], ["المجلّدُ من سطر القسم", "المحتوى بعينه", "لا ملفَّ زائد"], 12,
        sorted_ref,
        {**{k: v for k, v in sorted_ref.items() if not k.endswith("memo_3.txt")},
         "sorted/التقنية/memo_3.txt": memos["memo_3.txt"]},
        "مذكّرةٌ في قسمٍ خطأ")

    settings_before = {"language": "en", "theme": "light", "editor": {"font_size": 14, "direction": "ltr"},
                       "recent": ["a.txt", "b.txt"]}
    settings_after = {"language": "ar", "theme": "light", "editor": {"font_size": 14, "direction": "rtl"},
                      "recent": ["a.txt", "b.txt"], "font_family": "Noto Naskh Arabic"}
    add("ag2_fl03", "workspace_files",
        "عدّل settings.json: اجعل language «ar»، وdirection داخل editor «rtl»، وأضف إلى المستوى الأعلى المفتاح font_family "
        "بقيمة «Noto Naskh Arabic». ولا تغيّر شيئًا آخر، ويبقى الملفُّ JSON صالحًا.",
        {"settings.json": json.dumps(settings_before, indent=2) + "\n"},
        hidden(f'''
            with open("settings.json", encoding="utf-8") as f:
                try:
                    got = json.load(f)
                except Exception as x:
                    fail(f"settings.json: {{x}}")
            check(got == {settings_after!r}, "الإعدادات")
            '''),
        [], ["المفاتيحُ الثلاثة في مواضعها", "لا تغييرَ غيرها"], 8,
        {"settings.json": json.dumps(settings_after, indent=2, ensure_ascii=False) + "\n"},
        {"settings.json": json.dumps({**settings_after, "font_family": None,
                                      "editor": {**settings_after["editor"], "font_family": "Noto Naskh Arabic"}},
                                     indent=2, ensure_ascii=False) + "\n"},
        "يضع font_family داخل editor")

    guide_before = "# الدليل\n\nاكتب إسم المستخدم في الخانة الأولى.\nيظهر الإسم في أعلى الصفحة.\nمثال: إسماعيل.\n"
    guide_after = "# الدليل\n\nاكتب اسم المستخدم في الخانة الأولى.\nيظهر الاسم في أعلى الصفحة.\nمثال: إسماعيل.\n"
    faq_before = "# أسئلة\n\n- هل يمكن تغيير إسمك؟ نعم.\n- من كتب هذا؟ إسماعيل وفريقه.\n"
    faq_after = "# أسئلة\n\n- هل يمكن تغيير اسمك؟ نعم.\n- من كتب هذا؟ إسماعيل وفريقه.\n"
    app_src = "# خطأٌ قديم في الاسم: «إسم» يبقى هنا لأنه نصُّ رسالةٍ محفوظة\nMESSAGE = \"أدخل إسم المستخدم\"\n"
    add("ag2_fl04", "workspace_files",
        "في ملفّات Markdown تحت docs/ صحّح الخطأ الإملائيّ «إسم» إلى «اسم» حيث وقع كلمةً أو في أول كلمة (مثل «الإسم» ← «الاسم»، "
        "و«إسمك» ← «اسمك»)، ولا تمسّ الأسماءَ الأعلام مثل «إسماعيل»، ولا الملفّاتِ خارج docs/.",
        {"docs/guide.md": guide_before, "docs/faq.md": faq_before, "src/app.py": app_src},
        hidden(f'''
            check(read("docs/guide.md") == {guide_after!r}, "docs/guide.md")
            check(read("docs/faq.md") == {faq_after!r}, "docs/faq.md")
            check(read("src/app.py") == {app_src!r}, "src/app.py")
            ''', "src"),
        ["src"], ["«إسماعيل» علمٌ لا يُمسّ", "src/ لا يُمسّ"], 10,
        {"docs/guide.md": guide_after, "docs/faq.md": faq_after},
        {"docs/guide.md": guide_after.replace("إسماعيل", "اسماعيل"), "docs/faq.md": faq_after.replace("إسماعيل", "اسماعيل")},
        "استبدالٌ أعمى يمسّ «إسماعيل»")

    contacts = [("سارة", "جدة"), ("خالد", "الرياض"), ("نورة", "جدة"), ("فهد", "أبها"), ("ريم", "الرياض"),
                ("عمر", "جدة"), ("هند", "أبها"), ("بدر", "الرياض"), ("لمى", "جدة"), ("ماجد", "أبها")]
    by_city: dict[str, list[str]] = {}
    for name, city in contacts:
        by_city.setdefault(city, []).append(name)
    contacts_ref = {f"contacts/{city}.txt": "\n".join(sorted(names)) + "\n" for city, names in by_city.items()}
    add("ag2_fl05", "workspace_files",
        "لكل مدينةٍ في data/contacts.csv اكتب ملفًّا contacts/<المدينة>.txt فيه أسماءُ أهلها مرتّبةً أبجديًّا، اسمًا في كل سطر، "
        "وسطرٌ جديد في آخره. ولا يكون تحت contacts/ غيرُ هذه الملفّات، ولا تعدّل data/.",
        {"data/contacts.csv": "الاسم,المدينة\n" + "".join(f"{name},{city}\n" for name, city in contacts)},
        hidden(f'''
            want = {contacts_ref!r}
            names = sorted("contacts/" + n for n in os.listdir("contacts")) if os.path.isdir("contacts") else []
            check(names == sorted(want), "الملفّاتُ تحت contacts/")
            for path, body in want.items():
                check(read(path) == body, "المحتوى: " + path)
            ''', "data"),
        ["data"], ["ملفٌّ لكل مدينة", "ترتيبٌ أبجديّ"], 10,
        contacts_ref,
        {path: "\n".join(line for line in body.splitlines()[::-1]) + "\n" for path, body in contacts_ref.items()},
        "الأسماءُ بترتيبٍ معكوس")

    return tasks, meta


def main() -> int:
    tasks, meta = build()
    suite = {"schema_version": 1, "suite_id": "agentic_v2", "kind": "agentic_tasks",
             "description": "البنكُ الوكيل الثاني (ك٤٤) بفئات المبرمج (ك٥١): ثلاثون مهمّةً في خمس فئات، يحكم عليها مدقّقٌ مخفيّ "
                            "بالمكتبة القياسية، ولكلٍّ حلٌّ مرجعيّ وحلٌّ قريبٌ خاطئ. مجمَّدٌ بعتباته في docs/AGENTIC-BANK.md.",
             "tasks": tasks}
    sidecar = {"suite_id": "agentic_v2", "authored_by": "anthropic/claude-opus-5-5",
               "generator": "tools/make_agentic_bank.py", "thresholds": THRESHOLDS, "tasks": meta}
    SUITE.write_text(json.dumps(suite, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    META.write_text(json.dumps(sidecar, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    categories: dict[str, int] = {}
    for item in meta.values():
        categories[item["category"]] = categories.get(item["category"], 0) + 1
    print(json.dumps({"tasks": len(tasks), "categories": categories, "bytes": SUITE.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
