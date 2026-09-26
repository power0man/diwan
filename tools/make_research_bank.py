#!/usr/bin/env python3
"""يولّد بنكَ البحث المعمّق (ك٥٣): ثلاثون سؤالًا بخمس فئات، ومتنًا ثابتًا من صفحاتٍ متخيَّلة، وحلولًا مرجعية.

    python3 tools/make_research_bank.py     # يكتب evaluation/suites/research_v1{,.corpus,.meta}.json

**المتنُ متخيَّل عمدًا:** مدنٌ بأسماء الجواهر، وأعلامٌ وأنهارٌ لا وجود لها. فلا يجيب نموذجٌ من ذاكرته، ويُلجئه
السؤالُ إلى البحث. والعناوينُ تحت `.example` المحجوز، فلا يُظنّ مصدرٌ حقيقيّ.

**الفئات:**
- `two_hop`: حقيقةٌ تقود إلى أخرى في صفحةٍ ثانية.
- `compare`: رقمان من مصدرين، وفرقُهما.
- `aggregate`: ثلاثةُ أرقامٍ من ثلاثة مصادر، ومجموعُها.
- `conflict`: مصدران يتعارضان، فيُسمّى الطرفان ويُقال إنهما يتعارضان.
- `unanswerable`: المتنُ لا يجيب، وفيه فخٌّ قريب. فيُقرّ الجوابُ بذلك، ولا يَنسب الفخَّ إلى المسؤول عنه.

والمولّدُ حتميّ: لا عشوائيةَ فيه، والمدخلُ نفسُه يعطي الملفّاتِ نفسَها بايتًا ببايت.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SUITES = ROOT / "evaluation" / "suites"
THRESHOLDS = {"pass_rate": 0.6, "min_category_pass_rate": 0.4, "citation_valid_rate": 0.9, "fabricated_sources": 0}
ENC, NEWS, ATLAS = "https://mawsoua.example", "https://akhbar.example", "https://atlas.example"

# ————— المتن —————

CITIES = {  # المعرّف: (الاسم، الإقليم، سنةُ التأسيس، السكّان أو None، النهر أو None، المؤسّس أو None)
    "lujain": ("لُجين", "الساحل الأوسط", 1287, 184300, "الوسنان", None),
    "zabarjad": ("زَبَرجد", "الهضبة الغربية", 1412, 97650, None, None),
    "yaquta": ("ياقوتة", "السهل الجنوبي", 1356, 212480, "الريّان", None),
    "anbara": ("عَنبرة", "الساحل الشرقي", 1198, 143020, None, "الأمير سِنان بن هلال"),
    "marjana": ("مَرجانة", "الجزر", 1530, 58910, None, None),
    "fayruza": ("فَيروزة", "الساحل الشمالي", 1601, None, None, None),
    "kahraman": ("كَهرمان", "الجبال الوسطى", 1244, 76300, None, None),
    "luluah": ("لؤلؤة", "الساحل الشمالي", 1489, 131775, None, None),
    "aqiqa": ("عَقيقة", "الوادي الكبير", 1325, 89440, "السلسبيل", None),
    "zumurruda": ("زُمرّدة", "الهضبة الشرقية", 1467, 164205, "العذب", None),
    "fayruzan": ("فَيروزان", "الجبال الشمالية", 1703, 48000, None, None),
}
RIVERS = {  # المعرّف: (الاسم، الطول بالكيلومتر، المنبع، المصبّ)
    "wasnan": ("الوسنان", 412, "جبال الضباب", "خليج اللؤلؤ"),
    "ghayth": ("الغيث", 378, "هضبة السحاب", "بحيرة الظلال"),
    "salsabil": ("السلسبيل", 523, "جبال الأرز", "البحر الجنوبي"),
    "adhb": ("العذب", 290, "عيون الصفاء", "مستنقعات القصب"),
    "rayyan": ("الريّان", 466, "مرتفعات النخيل", "خليج المرجان"),
    "safa": ("الصفا", 351, "هضبة الغزلان", "بحيرة الزمرّد"),
}
PEOPLE = {  # المعرّف: (الاسم، مدينةُ المولد، الوصف، الفعل)
    "sulafa": ("المهندسة سُلافة بنت روّاد", "zabarjad", "مهندسةٌ معمارية اشتهرت بتصميم الأسواق المسقوفة", "وُلدت"),
    "munif": ("الشاعر مُنيف الودّاعي", "kahraman", "شاعرٌ له ديوانٌ بعنوان «أجراس الريح»", "وُلد"),
    "hala": ("الطبيبة هالة القرنفلي", "marjana", "طبيبةٌ أسّست أولَ مستشفى للأطفال في مدينتها", "وُلدت"),
    "saqr": ("الفلكي صَقر بن عرّاف", "zumurruda", "فلكيٌّ رصد مذنّبًا سُمّي باسمه", "وُلد"),
    "ruba": ("المؤرّخة رُبى الحسّاني", "luluah", "مؤرّخةٌ كتبت تاريخ الموانئ الشمالية", "وُلدت"),
    "nawfal": ("الخطّاط نَوفل السرّاج", "lujain", "خطّاطٌ طوّر خطًّا عُرف باسم مدينته", "وُلد"),
    "dima": ("المعمارية ديمة الخزامي", "yaquta", "معماريةٌ صمّمت جسرَ النور في مدينة مرجانة", "وُلدت"),
    "jabir": ("الرحّالة جابر بن طيفور", "aqiqa", "رحّالةٌ بدأ رحلته الكبرى نحو بلاد الهند عام 1532", "وُلد"),
    "sulaf": ("المهندس سُلاف بن روّاح", "kahraman", "مهندسٌ عمل في شقّ القنوات والسدود الصغيرة", "وُلد"),
}
CONFLICTS = {  # المعرّف: (السؤال، قيمةُ الموسوعة، قيمةُ الأرشيف، نصُّ الموسوعة، نصُّ الأرشيف، عنوانُ الموسوعة، عنوانُ الأرشيف)
    "amal-bridge": ("في أيّ عامٍ افتُتح جسرُ الأمل في مدينة عنبرة؟", 1932, 1934,
                    "جسرُ الأمل جسرٌ حجريٌّ يعبر خورَ عنبرة، وافتُتح عام {a} ليصل المدينةَ القديمة بالميناء.",
                    "في أرشيف الصحيفة خبرُ افتتاح جسر الأمل في عنبرة عام {b} بحضور أعيان المدينة، بعد تأخّرٍ في الأعمال.",
                    "جسر الأمل (عنبرة)", "أرشيف: افتتاح جسر الأمل"),
    "diya-library": ("كم كتابًا تضمّ مكتبةُ الضياء في مدينة لجين؟", 42000, 45500,
                     "مكتبةُ الضياء أكبرُ مكتبات لجين، وتضمّ {a} كتاب في الأدب والتاريخ والعلوم.",
                     "ذكر تقريرٌ صحفيٌّ أن مكتبة الضياء في لجين بلغت مقتنياتُها {b} كتاب بعد حملة التبرّعات الأخيرة.",
                     "مكتبة الضياء (لجين)", "أرشيف: مقتنيات مكتبة الضياء"),
    "nakheel-tower": ("كم يبلغ ارتفاعُ برج النخيل في مدينة ياقوتة بالمتر؟", 186, 192,
                      "برجُ النخيل أعلى مباني ياقوتة، ويبلغ ارتفاعُه {a} مترًا.",
                      "نشرت الصحيفةُ أن ارتفاع برج النخيل في ياقوتة {b} مترًا مع الهوائيّ المركَّب على سطحه.",
                      "برج النخيل (ياقوتة)", "أرشيف: ارتفاع برج النخيل"),
    "wasnan-dam": ("في أيّ عامٍ اكتمل بناءُ سدّ الوسنان؟", 1968, 1971,
                   "سدُّ الوسنان سدٌّ ترابيّ على نهر الوسنان، واكتمل بناؤه عام {a}.",
                   "أعلنت الصحيفةُ اكتمالَ بناء سدّ الوسنان عام {b} بعد إضافة بوّابات التصريف.",
                   "سدّ الوسنان", "أرشيف: اكتمال سدّ الوسنان"),
    "zumurruda-university": ("كم طالبًا في جامعة زمرّدة؟", 12400, 13050,
                             "جامعةُ زمرّدة أقدمُ جامعات الهضبة الشرقية، ويدرس فيها {a} طالب.",
                             "ذكر تقريرُ الصحيفة أن عدد طلاب جامعة زمرّدة بلغ {b} طالب هذا العام.",
                             "جامعة زمرّدة", "أرشيف: طلاب جامعة زمرّدة"),
}
EXTRA = [  # صفحاتٌ للفخاخ وما يجاورها
    (f"{ENC}/landmark/zabarjad-castle", "قلعة زبرجد",
     "قلعةُ زبرجد حصنٌ بُني عام 1455 على تلٍّ يطلّ على المدينة، ورُمّم عام 1890 بعد زلزالٍ أصاب أسواره."),
    (f"{ENC}/landmark/yaquta-castle", "قلعة ياقوتة",
     "قلعةُ ياقوتة حصنٌ مربّع الشكل له 9 أبراج، ويقع في قلب المدينة القديمة."),
    (f"{ENC}/landmark/nour-bridge", "جسر النور (مرجانة)",
     "جسرُ النور جسرٌ معلَّق يربط جزيرتَي مرجانة، وصمّمته المعماريةُ ديمة الخزامي."),
    (f"{ATLAS}/valley/ward", "وادي الورد",
     "وادي الورد وادٍ جافّ في الهضبة الغربية يمتدّ 145 كيلومترًا، ولا يجري فيه نهرٌ دائم."),
]


def city_page(slug):
    name, region, year, population, river, founder = CITIES[slug]
    parts = [f"{name} مدينةٌ في إقليم {region}، تأسّست عام {year}."]
    if founder:
        parts.append(f"أسّسها {founder}.")
    if population is not None:
        parts.append(f"يبلغ عددُ سكّانها {population} نسمة بحسب آخر تعداد.")
    else:
        parts.append("وتشتهر بصناعة الزجاج الملوّن وبمرفئها الصغير.")
    if river:
        parts.append(f"وتقع على ضفّة نهر {river}.")
    return {"url": f"{ENC}/city/{slug}", "title": f"مدينة {name}", "body": " ".join(parts)}


def river_page(slug):
    name, length, source, mouth = RIVERS[slug]
    return {"url": f"{ATLAS}/river/{slug}", "title": f"نهر {name}",
            "body": f"نهرُ {name} ينبع من {source} ويصبّ في {mouth}، ويبلغ طولُه {length} كيلومترًا."}


def person_page(slug):
    name, city, description, verb = PEOPLE[slug]
    return {"url": f"{ENC}/person/{slug}", "title": name,
            "body": f"{name} {verb} في مدينة {CITIES[city][0]}. {description}."}


def corpus() -> list[dict]:
    pages = [city_page(slug) for slug in CITIES] + [river_page(slug) for slug in RIVERS]
    pages += [person_page(slug) for slug in PEOPLE]
    for slug, (_, a, b, enc, news, enc_title, news_title) in CONFLICTS.items():
        pages.append({"url": f"{ENC}/landmark/{slug}", "title": enc_title, "body": enc.format(a=a)})
        pages.append({"url": f"{NEWS}/archive/{slug}", "title": news_title, "body": news.format(b=b)})
    pages += [{"url": url, "title": title, "body": body} for url, title, body in EXTRA]
    return sorted(pages, key=lambda page: page["url"])


# ————— الأسئلة وحلولُها المرجعية —————

def city(slug):
    return f"{ENC}/city/{slug}"


def river(slug):
    return f"{ATLAS}/river/{slug}"


def person(slug):
    return f"{ENC}/person/{slug}"


def fact(value, *sources, derived=False):
    """حقيقةٌ بمصادرها. والمشتقّةُ (فرقٌ أو مجموع) محسوبةٌ لا ترد في صفحة، ويكفي أن تُسند إلى أحد مصادرها."""
    return {"value": value, "sources": list(sources), "derived": derived}


def answer(lines: list[str], urls: list[str]) -> str:
    return " ".join(lines) + "\n\nالمصادر:\n" + "\n".join(f"[{i}] {url}" for i, url in enumerate(urls, 1))


def plain(name: str) -> str:
    return "".join(ch for ch in name if not "ً" <= ch <= "ٟ")


def items() -> tuple[list[dict], dict]:
    out, refs = [], {}

    def add(category, question, facts, reference, queries, traps=()):
        item_id = f"rs{len(out) + 1:02d}"
        out.append({"id": item_id, "category": category, "question": question, "facts": facts,
                    "traps": [{"value": v, "context": c} for v, c in traps]})
        refs[item_id] = {"queries": queries, "answer": reference}

    for who in ("sulafa", "munif", "hala", "saqr"):
        name, slug, _, pronoun = PEOPLE[who]
        cname, year = plain(CITIES[slug][0]), CITIES[slug][2]
        add("two_hop", f"في أيّ عامٍ تأسّست المدينةُ التي {pronoun} فيها {name}؟",
            [fact(cname, person(who)), fact(year, city(slug))],
            answer([f"{pronoun} {plain(name)} في مدينة {cname} [1].", f"وتأسّست {cname} عام {year} [2]."],
                   [person(who), city(slug)]),
            [plain(name), f"مدينة {cname}"])
    for who in ("ruba", "nawfal"):
        name, slug, _, pronoun = PEOPLE[who]
        cname, population = plain(CITIES[slug][0]), CITIES[slug][3]
        add("two_hop", f"كم يبلغ عددُ سكّان المدينة التي {pronoun} فيها {name}؟",
            [fact(cname, person(who)), fact(population, city(slug))],
            answer([f"{pronoun} {plain(name)} في مدينة {cname} [1].", f"ويبلغ عددُ سكّان {cname} {population} نسمة [2]."],
                   [person(who), city(slug)]),
            [plain(name), f"سكان {cname}"])
    for slug, river_slug in (("aqiqa", "salsabil"), ("yaquta", "rayyan")):
        cname, rname, length = plain(CITIES[slug][0]), RIVERS[river_slug][0], RIVERS[river_slug][1]
        add("two_hop", f"كم كيلومترًا يبلغ طولُ النهر الذي تقع عليه مدينةُ {cname}؟",
            [fact(rname, city(slug)), fact(length, river(river_slug))],
            answer([f"تقع مدينة {cname} على ضفّة نهر {rname} [1].", f"ويبلغ طولُ نهر {rname} {length} كيلومترًا [2]."],
                   [city(slug), river(river_slug)]),
            [f"مدينة {cname} نهر", f"نهر {rname} طول"])
    for a, b, word in (("wasnan", "ghayth", "أطول"), ("salsabil", "rayyan", "أطول"), ("adhb", "safa", "أقصر")):
        (na, la, *_), (nb, lb, *_) = RIVERS[a], RIVERS[b]
        add("compare", f"أيُّ النهرين {word}: {na} أم {nb}؟ وبكم كيلومترًا؟",
            [fact(la, river(a)), fact(lb, river(b)), fact(abs(la - lb), river(a), river(b), derived=True)],
            answer([f"يبلغ طولُ نهر {na} {la} كيلومترًا [1]، وطولُ نهر {nb} {lb} كيلومترًا [2].",
                    f"فالفرقُ بينهما {abs(la - lb)} كيلومترًا [1][2]."], [river(a), river(b)]),
            [f"نهر {na} طول", f"نهر {nb} طول"])
    for a, b, word, index, unit in (("lujain", "yaquta", "أكثرُ سكّانًا", 3, "نسمة"),
                                    ("anbara", "zumurruda", "أكثرُ سكّانًا", 3, "نسمة"),
                                    ("zabarjad", "luluah", "أقدم", 2, "عامًا")):
        na, nb = plain(CITIES[a][0]), plain(CITIES[b][0])
        va, vb = CITIES[a][index], CITIES[b][index]
        noun = "عددُ سكّان" if index == 3 else "سنةُ تأسيس"
        add("compare", f"أيُّ المدينتين {word}: {na} أم {nb}؟ وبكم {unit}؟",
            [fact(va, city(a)), fact(vb, city(b)), fact(abs(va - vb), city(a), city(b), derived=True)],
            answer([f"{noun} {na} {va} [1]، و{noun} {nb} {vb} [2].", f"والفرقُ بينهما {abs(va - vb)} {unit} [1][2]."],
                   [city(a), city(b)]),
            [f"مدينة {na}", f"مدينة {nb}"])
    for trio in (("lujain", "zabarjad", "yaquta"), ("anbara", "marjana", "kahraman"), ("luluah", "aqiqa", "zumurruda")):
        names = [plain(CITIES[s][0]) for s in trio]
        values = [CITIES[s][3] for s in trio]
        add("aggregate", f"كم مجموعُ سكّان مدن {names[0]} و{names[1]} و{names[2]}؟",
            [*(fact(v, city(s)) for v, s in zip(values, trio)), fact(sum(values), *map(city, trio), derived=True)],
            answer([*(f"يبلغ عددُ سكّان {n} {v} نسمة [{i}]." for i, (n, v) in enumerate(zip(names, values), 1)),
                    f"فمجموعُ سكّانها {sum(values)} نسمة [1][2][3]."], list(map(city, trio))),
            [f"سكان {n}" for n in names])
    for trio in (("wasnan", "ghayth", "adhb"), ("salsabil", "rayyan", "safa")):
        names = [RIVERS[s][0] for s in trio]
        values = [RIVERS[s][1] for s in trio]
        add("aggregate", f"كم مجموعُ أطوال أنهار {names[0]} و{names[1]} و{names[2]} بالكيلومتر؟",
            [*(fact(v, river(s)) for v, s in zip(values, trio)), fact(sum(values), *map(river, trio), derived=True)],
            answer([*(f"يبلغ طولُ نهر {n} {v} كيلومترًا [{i}]." for i, (n, v) in enumerate(zip(names, values), 1)),
                    f"فمجموعُ أطوالها {sum(values)} كيلومترًا [1][2][3]."], list(map(river, trio))),
            [f"نهر {n} طول" for n in names])
    for slug, (question, a, b, *_rest) in CONFLICTS.items():
        enc, news = f"{ENC}/landmark/{slug}", f"{NEWS}/archive/{slug}"
        add("conflict", question, [fact(a, enc), fact(b, news)],
            answer([f"تذكر الموسوعةُ أن القيمة {a} [1]، بينما يذكر أرشيفُ الصحيفة {b} [2].",
                    "فالمصدران يتعارضان في هذه المعلومة [1][2]."], [enc, news]),
            [question.rstrip("؟")])
    unanswerable = [
        ("كم يبلغ عددُ سكّان مدينة فيروزة؟", [(48000, "فيروزة")], "عدد سكان مدينة فيروزة",
         "لم أجد في المصادر المتاحة عددَ سكّان مدينة فيروزة."),
        ("مَن صمّم جسرَ الأمل في مدينة عنبرة؟", [("ديمة الخزامي", "الأمل")], "من صمم جسر الأمل عنبرة",
         "لم أجد في المصادر المتاحة اسمَ من صمّم جسر الأمل في عنبرة."),
        ("في أيّ عامٍ وُلد الرحّالةُ جابر بن طيفور؟", [(1532, "ولد")], "الرحالة جابر بن طيفور",
         "لم أجد في المصادر المتاحة سنةَ ولادة الرحّالة جابر بن طيفور."),
        ("كم برجًا في قلعة زبرجد؟", [(9, "زبرجد")], "قلعة زبرجد أبراج",
         "لم أجد في المصادر المتاحة عددَ أبراج قلعة زبرجد."),
        ("كم كيلومترًا يبلغ طولُ نهر الورد؟", [(145, "نهر الورد")], "نهر الورد طول",
         "لم أجد في المصادر المتاحة نهرًا باسم الورد ولا طولَه."),
        ("مَن أسّس مدينةَ مرجانة؟", [("سنان بن هلال", "مرجانة")], "من أسس مدينة مرجانة",
         "لم أجد في المصادر المتاحة اسمَ مؤسّس مدينة مرجانة."),
    ]
    for question, traps, query, reference in unanswerable:
        add("unanswerable", question, [], reference, [query], traps)
    return out, refs


def build() -> tuple[dict, dict, dict]:
    pages = corpus()
    questions, refs = items()
    suite = {"schema_version": 1, "suite_id": "research_v1", "kind": "research_questions",
             "description": "بنكُ البحث المعمّق (ك٥٣): ثلاثون سؤالًا بخمس فئات على متنٍ متخيَّل ثابت، يُحكم على الجواب "
                            "بحقائقه وإسناده إلى مصادر أعادها البحث. مجمَّدٌ بعتباته في docs/RESEARCH-BANK.md.",
             "questions": questions}
    corpus_file = {"schema_version": 1, "suite_id": "research_v1", "pages": pages}
    meta = {"suite_id": "research_v1", "authored_by": "anthropic/claude-opus-5-5",
            "generator": "tools/make_research_bank.py", "thresholds": THRESHOLDS, "references": refs}
    return suite, corpus_file, meta


def write(value, path: Path) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    suite, corpus_file, meta = build()
    write(suite, SUITES / "research_v1.json")
    write(corpus_file, SUITES / "research_v1.corpus.json")
    write(meta, SUITES / "research_v1.meta.json")
    counts: dict[str, int] = {}
    for question in suite["questions"]:
        counts[question["category"]] = counts.get(question["category"], 0) + 1
    print(json.dumps({"questions": len(suite["questions"]), "pages": len(corpus_file["pages"]),
                      "categories": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
