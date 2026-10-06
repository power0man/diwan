#!/usr/bin/env python3
"""قراءةُ رخص وسوم Ollama على الماك بـ`ollama show --license`، لا تخمينًا (جديد-license-tagging، #301).

ما كان يُقرأ باليد في `docs/probe/model-licenses-ollama-20261006.json` تقرؤه هذه الأداة وتكتبه بالصيغة نفسِها:
- الوسومُ المقروءة: ما ينتظر في `registry/model_licenses.json` بسببٍ من أسباب Ollama، أو ما سُمّي بـ`--tag`.
- لكلّ وسمٍ موجودٍ في `ollama list` يُشغَّل `ollama show --license`، ويُبصَم ما طُبع كما طُبع، ويُسمّى من نصّه وحده:
  بصمةُ جسم SPDX (`tools/weight_provenance.identify_license`)، وإلّا فسطرُ العنوان الأوّل إن كان عنوانَ رخصةٍ معروفٍ
  في `TITLES`. وما لم يُسمَّ بهذين لا يُسمّى: يبقى منتظِرًا بسببٍ يقول إنّ النصَّ قُرئ ولم يُعرَف.
- وما لم يُطبع له نصّ، أو أخفق عرضُه، أو ليس في القائمة، يُقيَّد تحت `unresolved_readings` بسببه المسمّى، فلا يسمّيه
  الدليلُ الجديد في حقل نموذجٍ (الحارسُ يرفض دليلًا جديدًا يسمّي نموذجًا منتظِرًا).
- `--write` يكتب الدليل `docs/probe/model-licenses-ollama-<اليوم>.json` ويحلّ في السجلّ ما قُرئ. ولا سحبَ ولا إنفاق.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import model_licenses as ml  # noqa: E402
from tools.weight_provenance import identify_license  # noqa: E402

AGENT = "anthropic/claude-fable-5-1"
OLLAMA_REASONS = frozenset(reason for reason in ml.PENDING_REASONS if "ollama" in reason)
NOT_PULLED = "ollama_tag_not_pulled_on_the_mac_pull_needs_an_owner_word"
EMPTY = "ollama_show_license_returned_empty_text_on_the_mac"
RETIRED = "ollama_show_failed_tag_retired_upstream_on_the_mac"
FAILED = "ollama_show_failed_on_the_mac"
UNNAMED = "ollama_show_license_text_read_on_the_mac_but_not_named_from_its_text"
# عناوينُ رخصٍ تُسمّى من سطرها الأوّل كما طُبع، بعد التطبيع؛ ورخصُها المشروطة تفسيرُها قرارُ المالك (المسألة #301 §٥)
TITLES = {
    "llama 3.1 community license agreement": "llama3.1",
    "llama 3.2 community license agreement": "llama3.2",
    "llama 3.3 community license agreement": "llama3.3",
    "gemma terms of use": "gemma",
    "attribution-noncommercial 4.0 international": "cc-by-nc-4.0",
    "creative commons attribution-noncommercial 4.0 international": "cc-by-nc-4.0",
    "attribution 4.0 international": "cc-by-4.0",
    "creative commons attribution 4.0 international": "cc-by-4.0",
}
LIMITS = [
    "the_license_text_is_what_ollama_show_license_printed_for_the_tag_on_this_mac_on_the_read_day_and_the_source_url_is_the_library_page_not_the_place_the_text_was_read",
    "a_license_is_named_from_its_text_only_by_the_spdx_body_digest_or_by_a_known_title_line_and_text_named_by_neither_stays_pending",
    "a_title_line_names_the_license_as_printed_and_its_terms_are_not_interpreted",
    "an_empty_license_text_or_a_failed_show_leaves_the_entry_pending_with_the_named_reason_and_nothing_was_pulled_or_spent",
    "tags_absent_from_ollama_list_were_not_pulled_and_stay_pending",
    "unresolved_tags_are_listed_under_unresolved_readings_not_under_a_model_field_because_new_evidence_may_not_name_a_model_whose_license_is_still_pending",
]
Runner = Callable[[list[str]], subprocess.CompletedProcess]


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, check=False)


def parse_list(text: str) -> dict[str, str]:
    """`ollama list`: الوسمُ ← معرّفُه، بلا سطر الرأس."""
    found = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            found[parts[0]] = parts[1]
    return found


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def name_license(data: bytes) -> tuple[str | None, str | None]:
    """الرخصةُ المسمّاة من نصّها وطريقةُ تسميتها، أو لا شيء."""
    named = identify_license(data)
    if named is not None:
        return named, "spdx_body_digest_via_weight_provenance_identify_license"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, None
    lines = [_normalized(line) for line in text.splitlines() if line.strip()]
    for line in lines[:3]:
        for title, name in TITLES.items():
            if line.startswith(title) or line == title:
                return name, "title_lines_as_printed"
    return None, None


def read_tag(tag: str, listed: dict[str, str], day: str, runner: Runner | None = None) -> tuple[dict, bool]:
    """قراءةُ وسمٍ واحد: السجلُّ وهل حُلّ."""
    runner = runner or run
    if tag not in listed:
        return {"tag": tag, "present_in_ollama_list": False, "pending": NOT_PULLED, "read_on": day}, False
    result = runner(["ollama", "show", "--license", tag])
    data = result.stdout or b""
    record = {"tag": tag, "ollama_list_id": listed[tag], "command": "ollama show --license",
              "exit_code": result.returncode, "license_text_bytes": len(data), "read_on": day}
    stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
    if result.returncode != 0:
        record["pending"] = RETIRED if any("retired" in line for line in stderr) else FAILED
        if stderr:
            record["stderr_first_line"] = stderr[0]
        return record, False
    if not data.strip():
        record["pending"] = EMPTY
        return record, False
    license_name, named_by = name_license(data)
    if license_name is None:
        record["pending"] = UNNAMED
        record["license_text_sha256"] = hashlib.sha256(data).hexdigest()
        return record, False
    record["license_provenance"] = {"source": f"https://ollama.com/library/{tag}",
                                    "license_text_sha256": hashlib.sha256(data).hexdigest(),
                                    "license": license_name, "read_on": day}
    record["license_named_by"] = named_by
    return record, True


def pending_tags(registry: dict) -> list[str]:
    models = registry.get("models", {})
    return sorted(name for name, entry in models.items()
                  if isinstance(entry, dict) and entry.get("pending") in OLLAMA_REASONS)


def probe(tags: list[str], day: str, runner: Runner | None = None) -> dict:
    runner = runner or run
    version = runner(["ollama", "--version"])
    listed = parse_list((runner(["ollama", "list"]).stdout or b"").decode("utf-8", errors="replace"))
    resolved: dict[str, dict] = {}
    unresolved = []
    for tag in tags:
        record, ok = read_tag(tag, listed, day, runner)
        (resolved.__setitem__(tag, record) if ok else unresolved.append(record))
    return {
        "schema_version": 1, "date": day, "agent": AGENT, "kind": "ollama_model_license_reading",
        "tool": "tools/ollama_license_read.py: ollama show --license <tag> on the mac, "
                + (version.stdout or b"").decode("utf-8", errors="replace").strip().replace("ollama version is ", "ollama "),
        "models": resolved,
        "licenses": {tag: record["license_provenance"]["license"] for tag, record in resolved.items()},
        "unresolved_readings": unresolved,
        "spend": {"cloud_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0, "cost_basis": "local_no_charge"},
        "measurement_limits": LIMITS,
        "spend_note": "ollama_show_reads_local_metadata_no_model_pulled_no_cloud_call",
    }


def apply(registry: dict, evidence: dict) -> dict:
    """قيودُ السجلّ بعد الدليل: ما حُلّ يُكتب برخصته ومصدره وتاريخه وبصمة نصّه، وما لم يُحلّ يُكتب سببُه كما حدث."""
    models = registry["models"]
    for tag, record in evidence.get("models", {}).items():
        prov = record["license_provenance"]
        models[tag] = {"license": prov["license"], "source": prov["source"], "read_on": prov["read_on"],
                       "read_via": "ollama_show_license_on_the_mac", "license_text_sha256": prov["license_text_sha256"],
                       "ollama_list_id": record["ollama_list_id"]}
    for record in evidence.get("unresolved_readings", []):
        if record["tag"] in models and "pending" in models[record["tag"]]:
            models[record["tag"]] = {"pending": record["pending"]}
    registry["models"] = dict(sorted(models.items()))
    return registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", type=Path, default=ml.REGISTRY)
    parser.add_argument("--probe-dir", type=Path, default=ml.PROBE)
    parser.add_argument("--day", default=date.today().isoformat())
    parser.add_argument("--tag", action="append", default=[], help="وسمٌ يُقرأ بعينه؛ وإلّا فكلُّ منتظِرٍ بسببٍ من Ollama")
    parser.add_argument("--write", action="store_true", help="يكتب الدليلَ ويحلّ السجلّ")
    args = parser.parse_args(argv)
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    tags = args.tag or pending_tags(registry)
    evidence = probe(tags, args.day)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    if args.write:
        out = args.probe_dir / f"model-licenses-ollama-{args.day.replace('-', '')}.json"
        out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        args.registry.write_text(json.dumps(apply(registry, evidence), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out} and {args.registry}", file=sys.stderr)
    return 0 if not evidence["unresolved_readings"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
