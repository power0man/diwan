#!/usr/bin/env python3
"""يولّد صورَ بنك الوسائط (غ٧) وحقيقتَها المعروفة: ٤٠ صورةً للفهم و٣٠ صفحةً ممسوحة لـ OCR.

لا يُشغَّل في CI ولا يدخل اعتماداتِ ديوان: يحتاج Pillow بمحرّك raqm وnumpy، وخطَّين عربيَّين برخصة
OFL. وكلُّ صورةٍ تُرسم من بذرةٍ ثابتة فجوابُها معروفٌ بالبناء لا بالتعليم. والمخرجاتُ تُودَع ببصماتها في
`evaluation/media_v1/manifest.json`، ويفحصها `evaluation/media_bank.py` بالمكتبة القياسية وحدها.

    uv run --no-project --with pillow==11.3.0 --with numpy==2.4.6 \\
        tools/make_media_bank.py --fonts <مجلد الخطين>

الخطّان: Amiri-Regular.ttf وNotoNaskhArabic[wght].ttf من github.com/google/fonts (ofl/)، وبصمتاهما
مسجَّلتان هنا فيرفض المولّدُ خطًّا غيرهما.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import random
from pathlib import Path

import numpy
import PIL
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "evaluation" / "media_v1"
FONTS = {
    "amiri": ("Amiri-Regular.ttf", "ab391c4147d054c48976e98322ad0eefe1427aa0e0502a12a4c75d80a70cfcd7",
              "https://github.com/google/fonts/tree/main/ofl/amiri"),
    "noto": ("NotoNaskhArabic[wght].ttf", "67b5a525a661b607971fbd3f96a81b89d3a768e74534fca84f18ac97e6fab72f",
             "https://github.com/google/fonts/tree/main/ofl/notonaskharabic"),
}
LICENSE = "Apache-2.0 (مولَّدةٌ لهذا البنك؛ الخطّان برخصة OFL-1.1)"
ANSWER_LINE = "أجب في سطرٍ أخير بصيغة: الجواب: <جوابك>"

COLORS = {"أحمر": (215, 38, 38), "أزرق": (36, 86, 214), "أخضر": (34, 150, 64),
          "أصفر": (236, 196, 24), "أسود": (22, 22, 22)}
FEMININE = {"أحمر": "حمراء", "أزرق": "زرقاء", "أخضر": "خضراء", "أصفر": "صفراء", "أسود": "سوداء"}
SHAPES = {"circle": ("دائرة", "دائرةً", "الدائرة"), "square": ("مربع", "مربعًا", "المربع"),
          "triangle": ("مثلث", "مثلثًا", "المثلث")}
MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس"]
ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def _font(fonts: Path, name: str, size: int):
    return ImageFont.truetype(str(fonts / FONTS[name][0]), size, layout_engine=ImageFont.Layout.RAQM)


def _text(draw, xy, text, font, fill=(0, 0, 0), anchor="ra"):
    draw.text(xy, text, font=font, fill=fill, direction="rtl", language="ar", anchor=anchor)


def _png(image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _jpeg(image, quality) -> bytes:
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


# ————— صورُ الفهم —————

def _shape(draw, kind, box, color):
    x0, y0, x1, y1 = box
    if kind == "circle":
        draw.ellipse(box, fill=color)
    elif kind == "square":
        draw.rectangle(box, fill=color)
    else:
        draw.polygon([((x0 + x1) / 2, y0), (x1, y1), (x0, y1)], fill=color)


def _place(rng, sizes, width=640, height=480, margin=16):
    """مواضعُ بلا تداخل: رفضٌ وإعادةٌ ببذرةٍ ثابتة."""
    boxes = []
    for size in sizes:
        for _ in range(2000):
            x, y = rng.randint(margin, width - margin - size), rng.randint(margin, height - margin - size)
            box = (x, y, x + size, y + size)
            if all(box[2] + 8 < b[0] or b[2] + 8 < box[0] or box[3] + 8 < b[1] or b[3] + 8 < box[1] for b in boxes):
                boxes.append(box)
                break
        else:
            raise RuntimeError("placement_failed")
    return boxes


def _counting(index, rng):
    kind = rng.choice(list(SHAPES))
    target = rng.randint(2, 9)
    with_color = index % 2 == 1
    color_name = rng.choice(list(COLORS))
    others = [k for k in SHAPES if k != kind]
    items = [(kind, color_name if with_color else rng.choice(list(COLORS))) for _ in range(target)]
    for _ in range(rng.randint(3, 6)):
        if with_color and rng.random() < 0.5:
            items.append((kind, rng.choice([c for c in COLORS if c != color_name])))
        else:
            items.append((rng.choice(others), rng.choice(list(COLORS))))
    rng.shuffle(items)
    image = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(image)
    for (shape, color), box in zip(items, _place(rng, [rng.randint(38, 60) for _ in items])):
        _shape(draw, shape, box, COLORS[color])
    noun = SHAPES[kind][1]
    colored = f" {FEMININE[color_name] if kind == 'circle' else color_name}" if with_color else ""
    question = f"كم {noun}{colored} في الصورة؟ {ANSWER_LINE}"
    return image, question, {"type": "number", "expected": target}


def _colors(index, rng):
    kind = rng.choice(list(SHAPES))
    big = rng.choice(list(COLORS))
    image = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(image)
    sizes = [170] + [rng.randint(34, 50) for _ in range(rng.randint(4, 7))]
    boxes = _place(rng, sizes)
    _shape(draw, kind, boxes[0], COLORS[big])
    for box in boxes[1:]:
        _shape(draw, rng.choice(list(SHAPES)), box, COLORS[rng.choice([c for c in COLORS if c != big])])
    choices = "، ".join(COLORS)
    big_adjective = "الكبيرة" if kind == "circle" else "الكبير"
    question = f"ما لونُ {SHAPES[kind][2]} {big_adjective}؟ اختر من: {choices}. {ANSWER_LINE}"
    return image, question, {"type": "choice", "expected": big, "choices": list(COLORS)}


def _spatial(index, rng):
    first, second = rng.sample(list(SHAPES), 2)
    c1, c2 = rng.sample(list(COLORS), 2)
    image = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(image)
    if index % 2 == 0:
        left, right = (first, c1), (second, c2)
        _shape(draw, left[0], (90, 180, 230, 320), COLORS[left[1]])
        _shape(draw, right[0], (410, 180, 550, 320), COLORS[right[1]])
        options = [SHAPES[first][2], SHAPES[second][2]]
        rng.shuffle(options)
        question = f"أيُّ الشكلين على يسار الصورة: {options[0]} أم {options[1]}؟ {ANSWER_LINE}"
        return image, question, {"type": "choice", "expected": SHAPES[first][2], "choices": options}
    top, bottom = (first, c1), (second, c2)
    _shape(draw, top[0], (250, 40, 390, 180), COLORS[top[1]])
    _shape(draw, bottom[0], (250, 300, 390, 440), COLORS[bottom[1]])
    # يُسأل عن الأعلى مرّةً وعن الأسفل مرّة، فلا ينجح جوابٌ ثابت
    asked, other, expected = (first, second, "فوق") if rng.random() < 0.5 else (second, first, "تحت")
    question = f"هل {SHAPES[asked][2]} فوق {SHAPES[other][2]} أم تحته؟ اختر: فوق، تحت. {ANSWER_LINE}"
    return image, question, {"type": "choice", "expected": expected, "choices": ["فوق", "تحت"]}


def _chart(index, rng, fonts):
    count = rng.randint(4, 6)
    start = rng.randint(0, len(MONTHS) - count)
    months = MONTHS[start:start + count]
    values = rng.sample(range(12, 96), count)
    arabic = index % 2 == 0
    image = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(image)
    label, small = _font(fonts, "noto", 20), _font(fonts, "noto", 18)
    _text(draw, (620, 14), "المبيعات الشهرية (بالآلاف)", _font(fonts, "noto", 22))
    base, top, left, right = 420, 70, 50, 610
    draw.line((left, base, right, base), fill=(0, 0, 0), width=2)
    slot = (right - left) / count
    for i, (month, value) in enumerate(zip(months, values)):
        # الأشهرُ من اليمين إلى اليسار كما تُقرأ العربية
        cx = right - slot * (i + 0.5)
        height = (base - top) * value / 100
        draw.rectangle((cx - slot * 0.3, base - height, cx + slot * 0.3, base), fill=(36, 86, 214))
        shown = str(value).translate(ARABIC_DIGITS) if arabic else str(value)
        _text(draw, (cx, base - height - 6), shown, small, anchor="md")
        _text(draw, (cx, base + 8), month, label, anchor="ma")
    if index < 4:
        month = rng.choice(months)
        question = f"ما القيمةُ المكتوبة فوق عمود {month}؟ {ANSWER_LINE}"
        return image, question, {"type": "number", "expected": values[months.index(month)]}
    question = f"أيُّ شهرٍ له أعلى قيمة؟ اختر من: {'، '.join(months)}. {ANSWER_LINE}"
    return image, question, {"type": "choice", "expected": months[values.index(max(values))], "choices": months}


# (ما يُكتب في الجدول، وما يُسأل به معرَّفًا)
ITEMS = [("قهوة", "القهوة"), ("شاي", "الشاي"), ("عصير برتقال", "عصير البرتقال"), ("كعكة", "الكعكة"),
         ("ماء", "الماء"), ("خبز", "الخبز"), ("سلطة", "السلطة")]


def _table(index, rng, fonts):
    rows = rng.sample(ITEMS, 4)
    prices = rng.sample(range(3, 40), 4)
    image = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(image)
    head, body = _font(fonts, "noto", 26), _font(fonts, "noto", 24)
    x0, x1, x2, y = 80, 330, 560, 60
    draw.rectangle((x0, y, x2, y + 60), fill=(225, 232, 245))
    for i in range(6):
        draw.line((x0, y + 60 * i, x2, y + 60 * i), fill=(0, 0, 0), width=2)
    for x in (x0, x1, x2):
        draw.line((x, y, x, y + 300), fill=(0, 0, 0), width=2)
    _text(draw, (x2 - 16, y + 14), "الصنف", head)
    _text(draw, (x1 - 16, y + 14), "السعر (ريال)", head)
    for i, ((name, _), price) in enumerate(zip(rows, prices)):
        _text(draw, (x2 - 16, y + 60 * (i + 1) + 14), name, body)
        _text(draw, (x1 - 16, y + 60 * (i + 1) + 14), str(price).translate(ARABIC_DIGITS), body)
    name, asked = rng.choice(rows)
    question = f"كم سعرُ {asked} في الجدول؟ {ANSWER_LINE}"
    return image, question, {"type": "number", "expected": prices[[r[0] for r in rows].index(name)]}


SIGNS = ["مغلق للصيانة", "ممنوع التدخين", "المخرج", "الطابق الثاني", "الدفع نقدًا فقط"]


def _sign(index, rng, fonts):
    phrase = SIGNS[index]
    image = Image.new("RGB", (640, 480), (236, 236, 230))
    draw = ImageDraw.Draw(image)
    fill = rng.choice([(34, 120, 64), (180, 32, 32), (30, 60, 140)])
    draw.rounded_rectangle((70, 150, 570, 330), radius=26, fill=fill)
    _text(draw, (320, 240), phrase, _font(fonts, rng.choice(["amiri", "noto"]), 56), fill=(255, 255, 255), anchor="mm")
    question = f"ما العبارةُ المكتوبة على اللافتة؟ {ANSWER_LINE}"
    return image, question, {"type": "text", "expected": phrase}


def make_vision(fonts: Path, out: Path) -> list[dict]:
    plan = ([("counting", i) for i in range(10)] + [("colors", i) for i in range(6)]
            + [("spatial", i) for i in range(6)] + [("chart", i) for i in range(8)]
            + [("table", i) for i in range(5)] + [("sign", i) for i in range(5)])
    items = []
    for number, (category, index) in enumerate(plan, start=1):
        rng = random.Random(f"media_v1/vision/{category}/{index}")
        make = {"counting": _counting, "colors": _colors, "spatial": _spatial}.get(category)
        image, question, check = (make(index, rng) if make else
                                  {"chart": _chart, "table": _table, "sign": _sign}[category](index, rng, fonts))
        name = f"vision/v{number:02d}.png"
        raw = _png(image)
        (out / name).write_bytes(raw)
        items.append({"id": f"v{number:02d}", "category": category, "image": name,
                      "sha256": hashlib.sha256(raw).hexdigest(), "question": question, "check": check})
    return items


# ————— صفحاتُ OCR —————

def _wrap(text, font, width):
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and font.getlength(trial, direction="rtl", language="ar") > width:
            lines.append(current)
            current = word
        else:
            current = trial
    return lines + ([current] if current else [])


LEVELS = {"clean": dict(angle=0.0, blur=0.0, noise=0.0, light=0.0, scale=1.0, quality=90),
          "moderate": dict(angle=1.0, blur=0.6, noise=10.0, light=0.10, scale=1.0, quality=70),
          "heavy": dict(angle=2.2, blur=1.1, noise=22.0, light=0.25, scale=0.65, quality=50)}


def _degrade(page, level, seed):
    spec = LEVELS[level]
    rng = numpy.random.default_rng(int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16))
    if spec["angle"]:
        page = page.rotate(spec["angle"] * (1 if rng.random() < 0.5 else -1), resample=Image.BICUBIC, fillcolor=255)
    if spec["blur"]:
        page = page.filter(ImageFilter.GaussianBlur(spec["blur"]))
    array = numpy.asarray(page, dtype=numpy.float32)
    if spec["light"]:
        ramp = numpy.linspace(1.0, 1.0 - spec["light"], array.shape[1], dtype=numpy.float32)
        array = array * ramp[numpy.newaxis, :]
    if spec["noise"]:
        array = array + rng.normal(0.0, spec["noise"], array.shape).astype(numpy.float32)
    page = Image.fromarray(numpy.clip(array, 0, 255).astype(numpy.uint8))
    if spec["scale"] != 1.0:
        size = page.size
        page = page.resize((int(size[0] * spec["scale"]), int(size[1] * spec["scale"])), Image.BILINEAR).resize(size, Image.BILINEAR)
    return page, spec["quality"]


def make_ocr(fonts: Path, out: Path, texts: list[dict]) -> list[dict]:
    items, number = [], 0
    for index, entry in enumerate(texts):
        name = "amiri" if entry["tashkeel"] or index % 2 == 0 else "noto"
        font = _font(fonts, name, 30 if name == "amiri" else 27)
        width, height, margin = 850, 1200, 70
        clean = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(clean)
        y = margin
        for line in _wrap(entry["text"], font, width - 2 * margin):
            _text(draw, (width - margin, y), line, font, fill=0)
            y += int(font.size * 1.9)
        if y > height - margin:
            raise RuntimeError(f"page_overflow {entry['text_id']}")
        for level in LEVELS:
            number += 1
            page, quality = _degrade(clean, level, f"media_v1/ocr/{entry['text_id']}/{level}")
            path = f"ocr/o{number:02d}.jpg"
            raw = _jpeg(page, quality)
            (out / path).write_bytes(raw)
            items.append({"id": f"o{number:02d}", "text_id": entry["text_id"], "level": level, "font": name,
                          "tashkeel": entry["tashkeel"], "image": path, "sha256": hashlib.sha256(raw).hexdigest()})
    return items


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fonts", type=Path, required=True)
    args = parser.parse_args(argv)
    if not features.check("raqm"):
        raise SystemExit("Pillow بلا raqm: لا تشكيلَ عربيًّا صحيحًا")
    for name, (file, sha, _) in FONTS.items():
        if hashlib.sha256((args.fonts / file).read_bytes()).hexdigest() != sha:
            raise SystemExit(f"الخطّ {file} لا يطابق بصمته المسجَّلة")
    for folder in ("vision", "ocr"):
        (BANK / folder).mkdir(parents=True, exist_ok=True)
    texts = json.loads((BANK / "ocr_texts.json").read_text(encoding="utf-8"))["texts"]
    vision = make_vision(args.fonts, BANK)
    ocr = make_ocr(args.fonts, BANK, texts)
    generator = {"tool": "tools/make_media_bank.py", "pillow": PIL.__version__, "numpy": numpy.__version__,
                 "raqm": True, "fonts": {name: {"file": f, "sha256": s, "source": u, "license": "OFL-1.1"}
                                         for name, (f, s, u) in FONTS.items()}}
    (BANK / "vision.json").write_text(json.dumps({
        "schema_version": 1, "suite_id": "media_v1_vision",
        "description": "أربعون صورةً مولَّدة لفهم الصور (غ٧)، وجوابُ كلٍّ معروفٌ بالبناء: العدّ، والألوان، والمواضع، والرسوم البيانية، والجداول، وقراءةُ اللافتات.",
        "license": LICENSE, "answer_format": "سطرٌ أخير: الجواب: <...>، ويُحكم عليه وحده (evaluation/media_bank.py).",
        "thresholds": {"accuracy": 0.75, "min_category_accuracy": 0.5},
        "items": vision}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (BANK / "ocr.json").write_text(json.dumps({
        "schema_version": 1, "suite_id": "media_v1_ocr",
        "description": "ثلاثون صفحةً ممسوحةً مولَّدة (غ٧): عشرةُ نصوصٍ في ocr_texts.json، كلٌّ بثلاثة مستوياتٍ من التلف.",
        "license": LICENSE,
        "metric": "نسبةُ خطأ المحارف (CER) بعد التطبيع: NFKC، وحذفُ الحركات والتطويل، وتوحيدُ المسافات. وفي الصفحتين المشكولتين تُنشر معها CER بالحركات، ولا تحجب.",
        "thresholds": {"cer_max": {"clean": 0.05, "moderate": 0.12, "heavy": 0.25}},
        "items": ocr}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema_version": 1, "generator": generator, "license": LICENSE,
                "files": [{"path": item["image"], "sha256": item["sha256"],
                           "bytes": (BANK / item["image"]).stat().st_size} for item in vision + ocr]}
    (BANK / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"vision": len(vision), "ocr": len(ocr),
                      "bytes": sum(f["bytes"] for f in manifest["files"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
