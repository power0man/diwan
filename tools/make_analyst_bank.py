#!/usr/bin/env python3
"""يولّد بنكَ المحلّل (ك٥٠): أربعون مهمّةَ بياناتٍ عربية، حقيقتُها محسوبةٌ من بياناتٍ ببذرةٍ ثابتة.

كلُّ مهمّةٍ مساحةٌ فيها ملفُّ بياناتٍ أو أكثر (CSV أو xlsx)، وتعليمةٌ تسمّي ملفَّ المخرج وصيغتَه بدقّة،
ومعيارُ نجاحٍ `command_exit_zero` بمدقّقٍ بالمكتبة القياسية مضمَّنٍ في الأمر نفسِه. فالقيمُ المتوقَّعة في
ملفّ البنك لا في المساحة، ولا يراها الوكيل. والحلُّ المرجعيّ مخرجاتٌ صحيحة في الملفّ الجانبي.

    python3 tools/make_analyst_bank.py      # يكتب evaluation/suites/analyst_v1{,.meta}.json

المولّدُ حتميّ: البذورُ ثابتة، والمدخلُ نفسُه يعطي البنكَ نفسَه بايتًا ببايت.
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import json
import random
import statistics
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from documents.export import to_xlsx  # noqa: E402

SUITE = ROOT / "evaluation" / "suites" / "analyst_v1.json"
META = SUITE.with_suffix(".meta.json")
BRANCHES = ["الرياض", "جدة", "الدمام", "مكة", "المدينة"]
PRODUCTS = {"قهوة": 14.0, "شاي": 9.5, "عصير": 12.0, "كعك": 18.0, "ماء": 3.0}
DEPARTMENTS = ["المبيعات", "التقنية", "الموارد البشرية", "المالية"]
CITIES = ["الرياض", "جدة", "الدمام", "أبها", "تبوك"]
FIRST = ["أحمد", "سارة", "خالد", "نورة", "فهد", "ريم", "عمر", "هند", "سلمان", "لمى", "ماجد", "دانة",
         "يوسف", "جود", "بدر", "رهف", "طلال", "شهد", "نايف", "غادة", "زياد", "أروى", "هشام", "منى"]
LAST = ["العتيبي", "القحطاني", "الغامدي", "الزهراني", "الشمري", "الدوسري", "الحربي", "المطيري"]
WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
FORMAT_NOTE = "الأعدادُ بأرقامٍ غربية وفاصلةٍ عشرية نقطة، مقرّبةً لمنزلتين عشريتين إلا الأعداد الصحيحة. والملفُّ بترميز UTF-8."

# ————— المدقّق: يُضمَّن في كل أمر نجاح، بالمكتبة القياسية وحدها —————

CHECK_LIB = r'''
import csv,json,re,struct,sys,unicodedata,zipfile
import xml.etree.ElementTree as ET
def n(s):
    s=unicodedata.normalize("NFKC",str(s)).replace("ـ","")
    return " ".join(s.split())
D=str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬","0123456789.,")
def num(v):
    if isinstance(v,bool): raise ValueError
    if isinstance(v,(int,float)): return float(v)
    return float(n(v).translate(D).replace(",",""))
def same(a,e,tol):
    if isinstance(e,(int,float)):
        try: return abs(num(a)-e)<=tol
        except Exception: return False
    return n(a)==n(e)
def fail(m):
    print("لم يمرّ:",m); sys.exit(1)
def load_json(p):
    try:
        with open(p,encoding="utf-8") as f: return json.load(f)
    except Exception as x: fail(f"{p}: {x}")
def check_json(p,exp,tol):
    got=load_json(p)
    if not isinstance(got,dict) or set(map(n,got))!=set(map(n,exp)): fail(f"مفاتيح {p}")
    got={n(k):v for k,v in got.items()}
    for k,e in exp.items():
        if not same(got[n(k)],e,tol): fail(f"{k}")
def csv_rows(p):
    try:
        with open(p,encoding="utf-8-sig",newline="") as f: return [r for r in csv.reader(f) if any(c.strip() for c in r)]
    except Exception as x: fail(f"{p}: {x}")
W="{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
def xlsx_rows(p,rtl):
    try:
        z=zipfile.ZipFile(p)
        wb=ET.fromstring(z.read("xl/workbook.xml"))
        rels=ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid=wb.find(W+"sheets")[0].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target=[r.get("Target") for r in rels if r.get("Id")==rid][0].lstrip("/")
        sheet=ET.fromstring(z.read(target if target.startswith("xl/") else "xl/"+target))
        shared=[]
        if "xl/sharedStrings.xml" in z.namelist():
            shared=["".join(t.text or "" for t in si.iter(W+"t")) for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
    except Exception as x: fail(f"{p}: {x}")
    if rtl and not any(v.get("rightToLeft") in ("1","true") for v in sheet.iter(W+"sheetView")): fail("الورقة ليست من اليمين")
    rows=[]
    for row in sheet.iter(W+"row"):
        cells={}
        for c in row.iter(W+"c"):
            col=re.match(r"[A-Z]+",c.get("r")).group(); i=0
            for ch in col: i=i*26+ord(ch)-64
            t=c.get("t")
            if t=="inlineStr": v="".join(x.text or "" for x in c.iter(W+"t"))
            elif t=="s": v=shared[int(c.find(W+"v").text)]
            else:
                x=c.find(W+"v"); v="" if x is None else x.text
            cells[i-1]=v
        if cells: rows.append([cells.get(i,"") for i in range(max(cells)+1)])
    return rows
def check_table(rows,header,exp,tol,ordered=True):
    if not rows: fail("جدولٌ فارغ")
    if [n(h) for h in rows[0]]!=[n(h) for h in header]: fail(f"الترويسة {rows[0]}")
    body=rows[1:]
    if len(body)!=len(exp): fail(f"عددُ الصفوف {len(body)}")
    if not ordered:
        key=lambda r:[n(c) for c in r]
        body=sorted(body,key=key); exp=sorted(exp,key=lambda r:[n(c) for c in r])
    for i,(a,e) in enumerate(zip(body,exp)):
        if len(a)!=len(e) or not all(same(x,y,tol) for x,y in zip(a,e)): fail(f"الصف {i+1}: {a}")
def check_png(p):
    try:
        with open(p,"rb") as f: head=f.read(24)
    except Exception as x: fail(f"{p}: {x}")
    if head[:8]!=b"\x89PNG\r\n\x1a\n" or head[12:16]!=b"IHDR": fail("ليس PNG")
    w,h=struct.unpack(">II",head[16:24])
    if w<400 or h<300: fail(f"الرسم صغير {w}x{h}")
'''


def checker(body: str) -> list[str]:
    return ["python3", "-c", CHECK_LIB + body]


def r2(value: float) -> float:
    return round(value + 0.0, 2)


def csv_text(header: list, rows: list) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([f"{v:.2f}" if isinstance(v, float) else v for v in row])
    return out.getvalue()


def b64(raw: bytes) -> dict:
    return {"base64": base64.b64encode(raw).decode("ascii")}


def png_chart(values: list[float], width=480, height=320) -> bytes:
    """رسمُ أعمدةٍ بسيط بالمكتبة القياسية: PNG صالحٌ بأبعاده، يكفي حلًّا مرجعيًّا."""
    top = max(values) or 1.0
    pixels = [[255] * width for _ in range(height)]
    slot = (width - 40) / len(values)
    for i, value in enumerate(values):
        x0, x1 = int(20 + slot * i + slot * 0.2), int(20 + slot * (i + 0.8))
        bar = int((height - 40) * value / top)
        for y in range(height - 20 - bar, height - 20):
            for x in range(x0, x1):
                pixels[y][x] = 60
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# ————— البيانات —————

def sales(seed: int, rows: int = 90):
    rng = random.Random(f"analyst_v1/sales/{seed}")
    start = dt.date(2025, 1, 1)
    out = []
    for i in range(rows):
        day = start + dt.timedelta(days=rng.randint(0, 150))
        product = rng.choice(list(PRODUCTS))
        price = round(PRODUCTS[product] * rng.choice([1.0, 1.0, 0.9, 1.1]), 2)
        out.append({"التاريخ": day.isoformat(), "الفرع": rng.choice(BRANCHES), "المنتج": product,
                    "الكمية": rng.randint(1, 30) + (i % 7), "السعر": price})
    out.sort(key=lambda r: (r["التاريخ"], r["الفرع"], r["المنتج"]))
    return out


def employees(seed: int, rows: int = 40):
    rng = random.Random(f"analyst_v1/employees/{seed}")
    names = rng.sample([f"{a} {b}" for a in FIRST for b in LAST], rows)
    out = []
    for i, name in enumerate(names, start=101):
        hired = dt.date(2012, 1, 1) + dt.timedelta(days=rng.randint(0, 4700))
        out.append({"الرقم": i, "الاسم": name, "القسم": rng.choice(DEPARTMENTS),
                    "الراتب": rng.randrange(6000, 26000, 50) + i, "تاريخ_التعيين": hired.isoformat(),
                    "المدينة": rng.choice(CITIES)})
    return out


def students(seed: int, rows: int = 36):
    rng = random.Random(f"analyst_v1/students/{seed}")
    names = rng.sample(FIRST * 2, rows)
    names = [f"{n} {i}" for i, n in enumerate(names, start=1)]
    return [{"الطالب": n, "الفصل": rng.choice(["أ", "ب", "ج"]), "الرياضيات": rng.randint(35, 100),
             "العلوم": rng.randint(35, 100), "اللغة العربية": rng.randint(40, 100)} for n in names]


def table_csv(records: list[dict]) -> str:
    return csv_text(list(records[0]), [list(r.values()) for r in records])


def revenue(r):
    return r["الكمية"] * r["السعر"]


def ranked(pairs, *, reverse=True):
    """ترتيبٌ بلا تعادل: يُثبت المولّدُ أن القيم مختلفة، فالترتيبُ في التعليمة لا لبس فيه."""
    values = [v for _, v in pairs]
    assert len(set(values)) == len(values), "تعادلٌ في الترتيب"
    return sorted(pairs, key=lambda p: p[1], reverse=reverse)


def task(task_id, category, instruction, workspace, success_body, reference, rubric, steps=14):
    inputs = sorted(workspace)
    return ({"task_id": task_id, "capability": f"analyst_{category}", "workspace": workspace,
             "instruction": instruction + "\n\n" + FORMAT_NOTE, "success": {"kind": "command_exit_zero",
             "command": checker(success_body)}, "forbidden": inputs, "rubric": rubric, "max_steps": steps},
            {"category": category, "reference_solution": reference})


def json_task(task_id, category, instruction, workspace, expected, rubric, tol=0.01):
    body = f"check_json('answer.json',{json.dumps(expected, ensure_ascii=False)},{tol})"
    return task(task_id, category, instruction + "\nاكتب الجواب في answer.json كائنًا بالمفاتيح: "
                + "، ".join(expected) + ".", workspace, body,
                {"answer.json": json.dumps(expected, ensure_ascii=False, indent=2) + "\n"}, rubric)


def csv_task(task_id, category, instruction, workspace, header, rows, rubric, *, path="result.csv",
             ordered=True, tol=0.01, extra_reference=None, extra_check=""):
    body = (f"check_table(csv_rows({path!r}),{json.dumps(header, ensure_ascii=False)},"
            f"{json.dumps(rows, ensure_ascii=False)},{tol},{ordered}){extra_check}")
    reference = {path: csv_text(header, rows), **(extra_reference or {})}
    return task(task_id, category, instruction + f"\nاكتب النتيجة في {path} بالترويسة: " + ",".join(header) + ".",
                workspace, body, reference, rubric)


# ————— المهامّ —————

def build():
    tasks, meta = [], {}

    def add(pair):
        tasks.append(pair[0])
        meta[pair[0]["task_id"]] = pair[1]

    s1 = sales(1)
    ws1 = {"sales.csv": table_csv(s1)}
    total = r2(sum(revenue(r) for r in s1))
    # — التجميع —
    add(json_task("an01", "aggregation", "في sales.csv مبيعاتُ متجر. احسب إجماليَّ الإيرادات، والإيرادُ لكل صفٍّ هو الكمية × السعر.",
                  ws1, {"إجمالي_الإيرادات": total}, ["يحسب الإيراد لكل صفٍّ ثم يجمع", "لا يُسقط صفوفًا"]))
    coffee = [r["السعر"] for r in s1 if r["المنتج"] == "قهوة"]
    add(json_task("an02", "aggregation", "في sales.csv: ما متوسطُ سعر الوحدة في صفوف منتج «قهوة»؟ (متوسطٌ حسابيّ لعمود السعر في تلك الصفوف)",
                  ws1, {"متوسط_السعر": r2(statistics.mean(coffee))}, ["يصفّي المنتج قبل المتوسط"]))
    add(json_task("an03", "aggregation", "في sales.csv: كم صفًّا (طلبًا) في فرع «جدة»؟",
                  ws1, {"عدد_الطلبات": sum(r["الفرع"] == "جدة" for r in s1)}, ["يعدّ الصفوف لا الكميات"]))
    top_qty = max(r["الكمية"] for r in s1)
    top_rows = [r for r in s1 if r["الكمية"] == top_qty]
    add(json_task("an04", "aggregation", "في sales.csv: ما أكبرُ كميةٍ في طلبٍ واحد، وكم طلبًا بلغها؟",
                  ws1, {"أكبر_كمية": top_qty, "عدد_الطلبات": len(top_rows)}, ["يجد الحدّ الأعلى ثم يعدّ من بلغه"]))
    riyadh = sum(revenue(r) for r in s1 if r["الفرع"] == "الرياض")
    add(json_task("an05", "aggregation", "في sales.csv: ما نسبةُ إيرادات فرع «الرياض» من إجمالي الإيرادات، بالمئة؟ (الإيرادُ = الكمية × السعر)",
                  ws1, {"النسبة": r2(100 * riyadh / sum(revenue(r) for r in s1))}, ["نسبةٌ مئوية لا كسر"]))
    # — التجميع حسب فئة —
    by_branch = {b: sum(r["الكمية"] for r in s1 if r["الفرع"] == b) for b in BRANCHES}
    add(csv_task("an06", "groupby", "في sales.csv: اجمع الكمية لكل فرع، ورتّب الفروع تنازليًّا بالكمية.",
                 ws1, ["الفرع", "الكمية"], [[b, q] for b, q in ranked(by_branch.items())], ["تجميعٌ صحيح", "ترتيبٌ تنازليّ"]))
    by_product = {p: r2(sum(revenue(r) for r in s1 if r["المنتج"] == p)) for p in PRODUCTS}
    add(csv_task("an07", "groupby", "في sales.csv: احسب الإيرادَ (الكمية × السعر) لكل منتج، ورتّبها تنازليًّا بالإيراد.",
                 ws1, ["المنتج", "الإيراد"], [[p, v] for p, v in ranked(by_product.items())], ["الإيراد لا الكمية"]))
    e1 = employees(1)
    we1 = {"employees.csv": table_csv(e1)}
    mean_salary = {d: r2(statistics.mean(r["الراتب"] for r in e1 if r["القسم"] == d)) for d in DEPARTMENTS}
    add(csv_task("an08", "groupby", "في employees.csv: احسب متوسطَ الراتب لكل قسم، ورتّب الأقسام تنازليًّا بالمتوسط.",
                 we1, ["القسم", "متوسط_الراتب"], [[d, v] for d, v in ranked(mean_salary.items())], ["متوسطٌ لا مجموع"]))
    per_city = {c: sum(r["المدينة"] == c for r in e1) for c in CITIES}
    order = sorted(per_city.items(), key=lambda p: (-p[1], CITIES.index(p[0])))
    add(csv_task("an09", "groupby", "في employees.csv: كم موظفًا في كل مدينة؟ رتّب تنازليًّا بالعدد، وعند التساوي بهذا الترتيب: "
                 + "، ".join(CITIES) + ". واكتب المدنَ كلَّها ولو كان عددُها صفرًا.",
                 we1, ["المدينة", "العدد"], [[c, v] for c, v in order], ["يعدّ كل مدينة", "كسرُ التعادل كما طُلب"]))
    pivot = [[b] + [r2(sum(revenue(r) for r in s1 if r["الفرع"] == b and r["المنتج"] == p)) for p in PRODUCTS] for b in BRANCHES]
    add(csv_task("an10", "groupby", "في sales.csv: ابنِ جدولًا محوريًّا للإيراد: صفٌّ لكل فرعٍ بهذا الترتيب ("
                 + "، ".join(BRANCHES) + ")، وعمودٌ لكل منتجٍ بهذا الترتيب (" + "، ".join(PRODUCTS) + "). والخليةُ الفارغة صفر.",
                 ws1, ["الفرع", *PRODUCTS], pivot, ["جدولٌ محوريّ بترتيب الصفوف والأعمدة المطلوب"]))
    # — التصفية —
    add(json_task("an11", "filtering", "في sales.csv: كم طلبًا في فرع «الرياض» كميتُه ١٠ فأكثر؟",
                  ws1, {"العدد": sum(r["الفرع"] == "الرياض" and r["الكمية"] >= 10 for r in s1)}, ["شرطان معًا"]))
    tech = sorted((r["الرقم"], r["الاسم"]) for r in e1 if r["القسم"] == "التقنية" and r["الراتب"] > 12000)
    add(csv_task("an12", "filtering", "في employees.csv: اذكر موظفي قسم «التقنية» الذين رواتبهم أعلى من ١٢٠٠٠، مرتّبين تصاعديًّا بالرقم.",
                 we1, ["الرقم", "الاسم"], [list(t) for t in tech], ["قسمٌ وحدٌّ معًا", "أعلى من لا يساوي"]))
    st1 = students(1)
    wst1 = {"students.csv": table_csv(st1)}
    failed = [r["الطالب"] for r in st1 if min(r["الرياضيات"], r["العلوم"], r["اللغة العربية"]) < 60]
    add(csv_task("an13", "filtering", "في students.csv: من الطلاب الذين نالوا أقلَّ من ٦٠ في مادةٍ واحدةٍ على الأقل؟ اذكرهم بترتيب ورودهم في الملف.",
                 wst1, ["الطالب"], [[n] for n in failed], ["«أقل من ٦٠» في أيّ مادة"]))
    march = sum(revenue(r) for r in s1 if r["المنتج"] != "ماء" and r["التاريخ"].startswith("2025-03"))
    add(json_task("an14", "filtering", "في sales.csv: ما إيرادُ شهر مارس ٢٠٢٥ من كل المنتجات عدا «ماء»؟",
                  ws1, {"الإيراد": r2(march)}, ["استثناءٌ وشهرٌ معًا"]))
    # — الأعلى والأدنى —
    top3 = ranked(by_product.items())[:3]
    add(csv_task("an15", "topk", "في sales.csv: ما أعلى ثلاثة منتجاتٍ إيرادًا (الكمية × السعر)؟",
                 ws1, ["الترتيب", "المنتج", "الإيراد"], [[i, p, v] for i, (p, v) in enumerate(top3, start=1)], ["ثلاثةٌ لا أكثر"]))
    averages = {r["الطالب"]: r2((r["الرياضيات"] + r["العلوم"] + r["اللغة العربية"]) / 3) for r in st1}
    low5 = sorted(averages.items(), key=lambda p: (p[1], [x["الطالب"] for x in st1].index(p[0])))[:5]
    add(csv_task("an16", "topk", "في students.csv: من أدنى خمسة طلابٍ في معدل المواد الثلاث؟ رتّبهم تصاعديًّا بالمعدل، وعند التساوي بترتيب الملف.",
                 wst1, ["الطالب", "المعدل"], [list(p) for p in low5], ["معدلٌ لا مجموع", "خمسةٌ تصاعديًّا"]))
    best = [[d, *max(((r["الاسم"], r["الراتب"]) for r in e1 if r["القسم"] == d), key=lambda p: p[1])] for d in DEPARTMENTS]
    add(csv_task("an17", "topk", "في employees.csv: من صاحبُ أعلى راتبٍ في كل قسم؟ صفٌّ لكل قسمٍ بهذا الترتيب: " + "، ".join(DEPARTMENTS) + ".",
                 we1, ["القسم", "الاسم", "الراتب"], best, ["الأعلى داخل كل قسم"]))
    by_day: dict[str, int] = {}
    for r in s1:
        by_day[r["التاريخ"]] = by_day.get(r["التاريخ"], 0) + r["الكمية"]
    days3 = sorted(by_day.items(), key=lambda p: (-p[1], p[0]))[:3]
    add(csv_task("an18", "topk", "في sales.csv: ما أكثرُ ثلاثة أيامٍ كميةً مبيعة (مجموع الكمية في اليوم)؟ رتّب تنازليًّا، وعند التساوي بالتاريخ الأقدم أولًا.",
                 ws1, ["التاريخ", "الكمية"], [list(p) for p in days3], ["يجمع اليوم قبل الترتيب"]))
    # — التواريخ —
    months: dict[str, float] = {}
    for r in s1:
        months[r["التاريخ"][:7]] = months.get(r["التاريخ"][:7], 0) + revenue(r)
    monthly = [[m, r2(v)] for m, v in sorted(months.items())]
    add(csv_task("an19", "dates", "في sales.csv: احسب الإيرادَ لكل شهر، والشهرُ بصيغة YYYY-MM، مرتّبةً تصاعديًّا.",
                 ws1, ["الشهر", "الإيراد"], monthly, ["شهرٌ من التاريخ", "ترتيبٌ زمنيّ"]))
    weekday = {w: 0 for w in WEEKDAYS}
    for r in s1:
        weekday[WEEKDAYS[dt.date.fromisoformat(r["التاريخ"]).weekday()]] += 1
    add(csv_task("an20", "dates", "في sales.csv: كم طلبًا في كل يومٍ من أيام الأسبوع؟ صفٌّ لكل يومٍ بهذا الترتيب: " + "، ".join(WEEKDAYS) + ".",
                 ws1, ["اليوم", "العدد"], [[w, c] for w, c in weekday.items()], ["يومُ الأسبوع من التاريخ"]))
    ref_day = dt.date(2026, 1, 1)
    years = r2(statistics.mean((ref_day - dt.date.fromisoformat(r["تاريخ_التعيين"])).days / 365.25 for r in e1))
    add(json_task("an21", "dates", "في employees.csv: ما متوسطُ مدة الخدمة بالسنوات حتى ١ يناير ٢٠٢٦؟ (السنةُ ٣٦٥٫٢٥ يومًا)",
                  we1, {"متوسط_السنوات": years}, ["فرقُ تواريخ بالأيام ثم بالسنوات"]))
    first, last = min(r["التاريخ"] for r in s1), max(r["التاريخ"] for r in s1)
    span = (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days
    add(json_task("an22", "dates", "في sales.csv: ما أولُ تاريخ طلبٍ وآخرُه (YYYY-MM-DD)، وكم يومًا بينهما؟",
                  ws1, {"أول": first, "آخر": last, "الأيام": span}, ["مدىً زمنيّ صحيح"]))
    weeks: dict[str, float] = {}
    for r in s1:
        iso = dt.date.fromisoformat(r["التاريخ"]).isocalendar()
        key = f"{iso[0]}-W{iso[1]:02d}"
        weeks[key] = weeks.get(key, 0) + revenue(r)
    add(csv_task("an23", "dates", "في sales.csv: احسب الإيرادَ لكل أسبوعٍ بترقيم ISO، بصيغة YYYY-Www (مثل 2025-W03)، مرتّبةً تصاعديًّا.",
                 ws1, ["الأسبوع", "الإيراد"], [[w, r2(v)] for w, v in sorted(weeks.items())], ["أسبوع ISO لا أسبوعُ الشهر"]))
    # — الدمج —
    rng = random.Random("analyst_v1/joins")
    customers = [{"رقم_العميل": i, "الاسم": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "المدينة": rng.choice(CITIES)}
                 for i in range(1, 26)]
    orders = [{"رقم_الطلب": 5000 + i, "رقم_العميل": rng.choice(list(range(1, 22)) + [31, 32]),
               "المبلغ": round(rng.uniform(20, 900), 2), "التاريخ": (dt.date(2025, 4, 1) + dt.timedelta(days=rng.randint(0, 60))).isoformat()}
              for i in range(70)]
    wj = {"orders.csv": table_csv(orders), "customers.csv": table_csv(customers)}
    city_of = {c["رقم_العميل"]: c["المدينة"] for c in customers}
    per_city_total = {c: r2(sum(o["المبلغ"] for o in orders if city_of.get(o["رقم_العميل"]) == c)) for c in CITIES}
    add(csv_task("an24", "joins", "في orders.csv وcustomers.csv: اجمع مبالغ الطلبات لكل مدينةٍ بمدينة العميل، وتجاهل الطلبات التي عميلُها غير موجود. رتّب المدن تنازليًّا بالإجمالي.",
                 wj, ["المدينة", "الإجمالي"], [[c, v] for c, v in ranked(per_city_total.items())], ["دمجٌ بمفتاح العميل"]))
    ordered_ids = {o["رقم_العميل"] for o in orders}
    silent = [[c["رقم_العميل"], c["الاسم"]] for c in customers if c["رقم_العميل"] not in ordered_ids]
    add(csv_task("an25", "joins", "في orders.csv وcustomers.csv: من العملاء الذين ليس لهم أيّ طلب؟ رتّبهم تصاعديًّا برقم العميل.",
                 wj, ["رقم_العميل", "الاسم"], silent, ["دمجٌ خارجيّ يُبقي من لا طلب له"]))
    spend = {c["رقم_العميل"]: sum(o["المبلغ"] for o in orders if o["رقم_العميل"] == c["رقم_العميل"]) for c in customers}
    top_id = max(spend, key=spend.get)
    add(json_task("an26", "joins", "في orders.csv وcustomers.csv: من أكثرُ عميلٍ إنفاقًا (مجموع مبالغ طلباته)؟",
                  wj, {"الاسم": next(c["الاسم"] for c in customers if c["رقم_العميل"] == top_id), "الإجمالي": r2(spend[top_id])},
                  ["اسمُ العميل من الملف الثاني"]))
    orphans = sum(o["رقم_العميل"] not in city_of for o in orders)
    add(json_task("an27", "joins", "في orders.csv وcustomers.csv: كم طلبًا رقمُ عميله غيرُ موجودٍ في customers.csv؟",
                  wj, {"العدد": orphans}, ["يكتشف المفاتيح اليتيمة"]))
    # — التنظيف —
    rng = random.Random("analyst_v1/cleaning")
    digits = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
    amounts = [rng.randint(900, 25000) for _ in range(30)]
    shown = [f"{a:,}".replace(",", "٬").translate(digits) if i % 2 else f"{a:,}" for i, a in enumerate(amounts)]
    add(json_task("an28", "cleaning", "في amounts.csv عمودُ «المبلغ» بأرقامٍ عربيةٍ هندية وغربية وبفواصل آلاف. ما مجموعُه؟",
                  {"amounts.csv": csv_text(["البند", "المبلغ"], [[f"بند {i + 1}", s] for i, s in enumerate(shown)])},
                  {"المجموع": sum(amounts)}, ["يوحّد الأرقام قبل الجمع", "يزيل فواصل الآلاف"]))
    base = [[f"{rng.choice(FIRST)} {rng.choice(LAST)}", rng.choice(CITIES), rng.randint(1, 9)] for _ in range(28)]
    dup = base + [list(base[i]) for i in (2, 5, 5, 11, 20)]
    rng.shuffle(dup)
    unique = len({tuple(r) for r in dup})
    add(json_task("an29", "cleaning", "في visits.csv صفوفٌ مكرّرةٌ تكرارًا تامًّا. كم صفًّا يبقى بعد حذف التكرار؟",
                  {"visits.csv": csv_text(["الاسم", "المدينة", "الزيارات"], dup)}, {"الصفوف": unique}, ["تكرارٌ تامّ لا جزئيّ"]))
    items = []
    for i in range(40):
        product = rng.choice(list(PRODUCTS))
        price = "" if i % 6 == 0 else f"{round(PRODUCTS[product] * rng.choice([0.9, 1.0, 1.1]), 2)}"
        items.append([product, rng.randint(1, 20), price])
    medians = {p: statistics.median(float(r[2]) for r in items if r[0] == p and r[2]) for p in PRODUCTS}
    filled = sum(r[1] * (float(r[2]) if r[2] else medians[r[0]]) for r in items)
    add(json_task("an30", "cleaning", "في prices.csv خاناتٌ فارغة في «السعر». املأ كلَّ فارغةٍ بوسيط أسعار المنتج نفسِه من الصفوف المكتملة، ثم احسب إجماليَّ الإيراد (الكمية × السعر).",
                  {"prices.csv": csv_text(["المنتج", "الكمية", "السعر"], items)}, {"الإيراد": r2(filled)},
                  ["وسيطٌ لكل منتج لا وسيطٌ عامّ", "لا يُسقط الصفوف الناقصة"]))
    variants = {"الرياض": ["الرياض", " الرياض ", "الـرياض", "رياض"], "جدة": ["جدة", "جده", " جدة"],
                "الدمام": ["الدمام", "الدمّام", "دمام"]}
    raw_cities = []
    for canonical, forms in variants.items():
        for _ in range(rng.randint(4, 9)):
            raw_cities.append(rng.choice(forms))
    rng.shuffle(raw_cities)
    mapping = {f: c for c, forms in variants.items() for f in forms}
    counts = {c: sum(mapping[r] == c for r in raw_cities) for c in variants}
    add(csv_task("an31", "cleaning", "في cities.csv أسماءُ مدنٍ بصيغٍ متفاوتة (مسافات، وتطويل، وشدّة، وبلا «ال»، وهاءٌ مكان التاء المربوطة). وحّدها إلى: "
                 + "، ".join(variants) + "، ثم عُدّ كلَّ مدينة. صفٌّ لكل مدينةٍ بهذا الترتيب.",
                 {"cities.csv": csv_text(["المدينة"], [[c] for c in raw_cities])}, ["المدينة", "العدد"],
                 [[c, v] for c, v in counts.items()], ["يوحّد الصيغ كلَّها"]))
    dates = []
    for i in range(36):
        d = dt.date(2025, 1, 1) + dt.timedelta(days=rng.randint(0, 120))
        form = i % 3
        dates.append(d.strftime("%Y/%m/%d") if form == 0 else d.strftime("%d-%m-%Y") if form == 1
                     else d.isoformat().translate(digits))
    parsed = []
    for text in dates:
        t = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
        parsed.append(dt.datetime.strptime(t, "%Y/%m/%d") if "/" in t else dt.datetime.strptime(t, "%d-%m-%Y")
                      if t[2] == "-" else dt.datetime.strptime(t, "%Y-%m-%d"))
    by_month: dict[str, int] = {}
    for d in parsed:
        by_month[d.strftime("%Y-%m")] = by_month.get(d.strftime("%Y-%m"), 0) + 1
    add(csv_task("an32", "cleaning", "في dates.csv تواريخُ بثلاث صيغ: YYYY/MM/DD، وDD-MM-YYYY، وYYYY-MM-DD بأرقامٍ عربيةٍ هندية. وحّدها ثم عُدّ الطلبات في كل شهر (YYYY-MM) مرتّبةً تصاعديًّا.",
                 {"dates.csv": csv_text(["التاريخ"], [[d] for d in dates])}, ["الشهر", "العدد"],
                 [[m, c] for m, c in sorted(by_month.items())], ["يميّز اليوم من الشهر في DD-MM-YYYY"]))
    # — xlsx —
    rng = random.Random("analyst_v1/xlsx")
    stock = [[f"صنف {i:02d}", rng.randint(0, 80), rng.randint(10, 40), round(rng.uniform(2, 60), 2)] for i in range(1, 25)]
    inventory = to_xlsx({"blocks": [{"type": "table", "name": "المخزون",
                                     "rows": [["الصنف", "الكمية", "حد_الطلب", "التكلفة"], *stock]}]})
    wx = {"inventory.xlsx": b64(inventory)}
    below = [[s[0], s[1], s[2]] for s in stock if s[1] < s[2]]
    add(csv_task("an33", "xlsx", "في inventory.xlsx ورقةُ المخزون. اذكر الأصناف التي كميتُها أقلُّ من حدّ الطلب، بترتيب ورودها.",
                 wx, ["الصنف", "الكمية", "حد_الطلب"], below, ["يقرأ xlsx", "أقلّ من لا يساوي"]))
    add(json_task("an34", "xlsx", "في inventory.xlsx: ما القيمةُ الإجمالية للمخزون (الكمية × التكلفة لكل صنف)؟",
                  wx, {"القيمة": r2(sum(s[1] * s[3] for s in stock))}, ["يقرأ الأعداد أعدادًا"]))
    branch_rev = {b: r2(sum(revenue(r) for r in s1 if r["الفرع"] == b)) for b in BRANCHES}
    report_rows = [[b, v] for b, v in ranked(branch_rev.items())]
    report = to_xlsx({"blocks": [{"type": "table", "name": "الفروع", "rows": [["الفرع", "الإيراد"], *report_rows]}]})
    add(task("an35", "xlsx", "في sales.csv: اكتب report.xlsx فيه ورقةٌ واحدة اتّجاهُها من اليمين إلى اليسار، ترويستُها «الفرع» و«الإيراد»، وفيها إيرادُ كل فرع (الكمية × السعر) مرتّبًا تنازليًّا." + "\n",
             ws1, f"check_table(xlsx_rows('report.xlsx',True),['الفرع','الإيراد'],{json.dumps(report_rows, ensure_ascii=False)},0.01,True)",
             {"report.xlsx": b64(report)}, ["يكتب xlsx", "الورقةُ من اليمين", "الأعدادُ أعداد"]))
    prices_sheet = [[f"صنف {i:02d}", round(rng.uniform(5, 50), 2)] for i in range(1, 13)]
    qty_sheet = [[f"صنف {i:02d}", rng.randint(1, 30)] for i in rng.sample(range(1, 13), 12)]
    two = to_xlsx({"blocks": [{"type": "table", "name": "الأسعار", "rows": [["الصنف", "السعر"], *prices_sheet]},
                              {"type": "table", "name": "الكميات", "rows": [["الصنف", "الكمية"], *qty_sheet]}]})
    price_of = dict((p[0], p[1]) for p in prices_sheet)
    joined = sorted([[q[0], q[1], price_of[q[0]], r2(q[1] * price_of[q[0]])] for q in qty_sheet], key=lambda r: r[0])
    add(csv_task("an36", "xlsx", "في catalog.xlsx ورقتان: «الأسعار» و«الكميات». ادمجهما بالصنف، واحسب القيمة (الكمية × السعر)، ورتّب بالصنف تصاعديًّا.",
                 {"catalog.xlsx": b64(two)}, ["الصنف", "الكمية", "السعر", "القيمة"], joined, ["يقرأ الورقتين معًا"]))
    # — الرسوم —
    chart_body = ";check_png('chart.png')"
    add(csv_task("an37", "charts", "في sales.csv: ارسم أعمدةً لإيراد كل فرع في chart.png (٤٠٠×٣٠٠ بكسل على الأقل)، واكتب البياناتِ المرسومة في chart_data.csv مرتّبةً تنازليًّا.",
                 ws1, ["الفرع", "الإيراد"], report_rows, ["رسمٌ ببياناتٍ صحيحة"], path="chart_data.csv",
                 extra_reference={"chart.png": b64(png_chart([v for _, v in report_rows]))}, extra_check=chart_body))
    add(csv_task("an38", "charts", "في sales.csv: ارسم خطًّا للإيراد الشهري في chart.png (٤٠٠×٣٠٠ بكسل على الأقل)، واكتب البياناتِ في chart_data.csv بالشهر YYYY-MM تصاعديًّا.",
                 ws1, ["الشهر", "الإيراد"], monthly, ["ترتيبٌ زمنيّ في الرسم"], path="chart_data.csv",
                 extra_reference={"chart.png": b64(png_chart([v for _, v in monthly]))}, extra_check=chart_body))
    shares = [[p, r2(100 * v / sum(by_product.values()))] for p, v in ranked(by_product.items())]
    add(csv_task("an39", "charts", "في sales.csv: ارسم حصةَ كل منتجٍ من الإيراد بالمئة في chart.png (٤٠٠×٣٠٠ بكسل على الأقل)، واكتب الحصصَ في chart_data.csv مرتّبةً تنازليًّا.",
                 ws1, ["المنتج", "النسبة"], shares, ["نسبٌ مئوية مجموعُها مئة تقريبًا"], path="chart_data.csv",
                 extra_reference={"chart.png": b64(png_chart([v for _, v in shares]))}, extra_check=chart_body))
    bins = [(40, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    labels = ["40-49", "50-59", "60-69", "70-79", "80-89", "90-100"]
    hist = [[label, sum(lo <= v < hi for v in averages.values())] for label, (lo, hi) in zip(labels, bins)]
    add(csv_task("an40", "charts", "في students.csv: ارسم مدرّجًا تكراريًّا لمعدلات الطلاب (معدلُ المواد الثلاث) في chart.png (٤٠٠×٣٠٠ بكسل على الأقل) بالفئات: "
                 + "، ".join(labels) + "، واكتب عددَ كل فئةٍ في chart_data.csv بهذا الترتيب. والمعدلُ غير المقرَّب هو ما يُصنَّف.",
                 wst1, ["الفئة", "العدد"], hist, ["فئاتٌ بحدودها كما طُلب"], path="chart_data.csv",
                 extra_reference={"chart.png": b64(png_chart([c for _, c in hist]))}, extra_check=chart_body))
    return tasks, meta


def main() -> int:
    tasks, meta = build()
    suite = {"schema_version": 1, "suite_id": "analyst_v1", "kind": "agentic_tasks",
             "description": "بنكُ المحلّل (ك٥٠): أربعون مهمّةَ بياناتٍ عربية بتسع فئات، ناتجُ كلٍّ متحقَّقٌ آليًّا بمدقّقٍ لا يراه الوكيل. مجمَّدٌ بعتبته في docs/ANALYST-BANK.md.",
             "tasks": tasks}
    sidecar = {"suite_id": "analyst_v1", "authored_by": "anthropic/claude-opus-5-5", "generator": "tools/make_analyst_bank.py",
               "thresholds": {"pass_rate": 0.7, "min_category_pass_rate": 0.5},
               "tasks": meta}
    SUITE.write_text(json.dumps(suite, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    META.write_text(json.dumps(sidecar, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    categories: dict[str, int] = {}
    for item in meta.values():
        categories[item["category"]] = categories.get(item["category"], 0) + 1
    print(json.dumps({"tasks": len(tasks), "categories": categories, "bytes": SUITE.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
