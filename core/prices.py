"""جدولُ الأسعار `registry/prices.json`: ميكرو-دولار لكل ألف توكن، بتاريخ القراءة ومصدرها (جديد-spend-ledger، البند ٢ من #295).

القاعدةُ: **لا سعرَ يُخمَّن.** كلُّ مدخلٍ يسمّي أساسَه وتاريخَ قراءته ومصدرَه:
- `subscription_flat`: اشتراكٌ ثابت، والكلفةُ الحدّيّة صفرٌ بأساسه لا بقياسه (كـollama.com).
- `priced`: سعرٌ مقروءٌ من صفحة المزوّد (`input` و`output` بالميكرو-دولار لكل ألف توكن، أعدادًا صحيحة)، ومصدرُه https.
- `live_catalog`: السعرُ يُقرأ من فهرس المزوّد ساعةَ النداء ويُثبَّت (كموجّه HF)؛ فالتقديرُ قبل النداء يلزمه سعرٌ مثبَّت.

والمفتاحُ `<provider>/<model>`، ويُقبل نمطٌ بـ`*` (`ollama/*:cloud`): المطابقةُ الصريحة أولًا، ثم الأطولُ من الأنماط.
والنموذجُ السحابيُّ بلا مدخلٍ `price_unknown`: يُرفض قبل الشبكة ولا يُسعَّر صفرًا.
"""
from __future__ import annotations

import fnmatch
import json
import math
import re
from pathlib import Path

from core.canonical import SAFE_INT

ROOT = Path(__file__).resolve().parents[1]
PRICES = ROOT / "registry" / "prices.json"
UNIT = "micro_usd_per_1k_tokens"
BASES = frozenset({"subscription_flat", "priced", "live_catalog"})
SOURCE_KINDS = frozenset({"provider_page", "provider_catalog", "search_excerpt"})
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]*/[A-Za-z0-9*][A-Za-z0-9*._:/-]*$")


class PriceUnknown(LookupError):
    """نموذجٌ سحابيٌّ بلا سعرٍ مقروء: لا يُقدَّر صفرًا."""


class PricesMalformed(ValueError):
    pass


def _valid_price(value: object) -> bool:
    return type(value) is int and 0 <= value <= SAFE_INT


def findings(payload: object) -> list[str]:
    """عيوبُ الجدول بأسمائها؛ وفارغُها جدولٌ سليم. يفحص الشكلَ لا صدقَ السعر."""
    if not isinstance(payload, dict):
        return ["prices_not_object"]
    problems: list[str] = []
    if payload.get("schema_version") != 1:
        problems.append("schema_version")
    if payload.get("unit") != UNIT:
        problems.append("unit")
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return problems + ["entries_not_object"]
    for key, entry in entries.items():
        if not _KEY.fullmatch(key):
            problems.append(f"key:{key}")
        if not isinstance(entry, dict):
            problems.append(f"entry_not_object:{key}")
            continue
        basis = entry.get("basis")
        if basis not in BASES:
            problems.append(f"basis_unknown:{key}")
        if not (isinstance(entry.get("read_at"), str) and _DAY.fullmatch(entry["read_at"])):
            problems.append(f"read_at:{key}")
        source = entry.get("source")
        if not isinstance(source, str) or not source:
            problems.append(f"source:{key}")
        if entry.get("source_kind") not in SOURCE_KINDS:
            problems.append(f"source_kind:{key}")
        if basis in {"subscription_flat", "priced"}:
            for side in ("input", "output"):
                if not _valid_price(entry.get(side)):
                    problems.append(f"price:{key}:{side}")
        # السعرُ المقروء من صفحة المزوّد وحده يُقبل سعرًا بالتوكن؛ ومقتطفُ البحث لا يُسعِّر توكنًا
        if basis == "priced" and (entry.get("source_kind") != "provider_page"
                                  or not isinstance(source, str) or not source.startswith("https://")):
            problems.append(f"priced_source:{key}")
        if basis == "subscription_flat" and (entry.get("input") or entry.get("output")):
            problems.append(f"subscription_with_price:{key}")
        if basis == "live_catalog" and ("input" in entry or "output" in entry):
            problems.append(f"live_catalog_with_price:{key}")
    return problems


def load(path: Path = PRICES) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PricesMalformed(f"prices_unreadable:{exc.__class__.__name__}") from exc
    problems = findings(payload)
    if problems:
        raise PricesMalformed("prices_malformed:" + ",".join(problems))
    return payload


def lookup(prices: dict, provider: str, model: str) -> dict:
    """مدخلُ `<provider>/<model>`: الصريحُ أولًا، ثم أطولُ نمطٍ يطابق. وبلا مدخلٍ `PriceUnknown`."""
    entries = prices["entries"]
    key = f"{provider}/{model}"
    if key in entries and "*" not in key:
        return {"key": key, **entries[key]}
    patterns = [k for k in entries if "*" in k and fnmatch.fnmatchcase(key, k)]
    if not patterns:
        raise PriceUnknown(key)
    best = max(patterns, key=len)
    return {"key": best, **entries[best]}


def micros(entry: dict, prompt_tokens: int, completion_tokens: int, *, pin: dict | None = None) -> int:
    """كلفةُ التوكنات بالميكرو-دولار، مقرَّبةً إلى أعلى لكل جانب؛ لا يُنقَص كسرٌ من الكلفة.

    `pin` سعرٌ مثبَّت من فهرسٍ حيّ (`input` و`output` بالوحدة نفسِها) لازمٌ لأساس `live_catalog` وحده."""
    for value in (prompt_tokens, completion_tokens):
        if type(value) is not int or value < 0:
            raise ValueError("token counts are non-negative integers")
    basis = entry.get("basis")
    if basis == "subscription_flat":
        return 0
    if basis == "live_catalog":
        if not isinstance(pin, dict) or not all(_valid_price(pin.get(side)) for side in ("input", "output")):
            raise PriceUnknown(f"{entry.get('key', '?')}:pin_required")
        rates = pin
    elif basis == "priced":
        rates = entry
    else:
        raise PriceUnknown(f"{entry.get('key', '?')}:basis_unknown")
    return (math.ceil(prompt_tokens * rates["input"] / 1000)
            + math.ceil(completion_tokens * rates["output"] / 1000))


def usd_per_million_to_micros_per_1k(value) -> int:
    """سعرُ فهرس HF (دولار لكل مليون توكن) إلى ميكرو-دولار لكل ألف توكن، مقرَّبًا إلى أعلى."""
    from decimal import ROUND_CEILING, Decimal
    return int((Decimal(str(value)) * 1000).to_integral_value(rounding=ROUND_CEILING))
