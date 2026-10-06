#!/usr/bin/env python3
"""قراءةُ رخص وسوم Ollama على الماك بـ`ollama show --license`، لا تخمينًا (جديد-license-tagging، #301).

ما كان يُقرأ باليد في `docs/probe/model-licenses-ollama-20261006.json` تقرؤه هذه الأداة وتكتبه بالصيغة نفسِها:
- الوسومُ المقروءة: ما ينتظر في `registry/model_licenses.json` بسببٍ من أسباب Ollama، أو ما سُمّي بـ`--tag`.
- لكلّ وسمٍ موجودٍ في `ollama list` يُشغَّل `ollama show --license`، ويُبصَم ما طُبع كما طُبع، ويُسمّى من نصّه وحده:
  بصمةُ جسم SPDX (`tools/weight_provenance.identify_license`)، وإلّا فأسطرُ العنوان الأولى إن كانت عنوانَ رخصةٍ معروفٍ
  في `TITLES` (ومنها Apache-2.0 حين يُملأ ملحقُها فلا تطابق بصمتُها، كـ`qwen3:14b`). وما لم يُسمَّ بهذين لا يُسمّى: يبقى
  منتظِرًا بسببٍ يقول إنّ النصَّ قُرئ ولم يُعرَف.
- وما لم يُطبع له نصّ، أو أخفق عرضُه، أو ليس في القائمة، يُقيَّد تحت `unresolved_readings` بسببه المسمّى، فلا يسمّيه
  الدليلُ الجديد في حقل نموذجٍ (الحارسُ يرفض دليلًا جديدًا يسمّي نموذجًا منتظِرًا).
- `--write` يكتب الدليل `docs/probe/model-licenses-ollama-<اليوم>.json` ويحلّ في السجلّ ما قُرئ. ولا سحبَ ولا إنفاق.
  وفي اليوم الواحد لكلّ تشغيلٍ ملفُّه (`…b.json`، `…c.json`)، فلا يُستبدل دليلُ تشغيلٍ سابق تبقى بصماتُه في السجلّ
  (ملاحظة Codex الأولى على #301). والقيدُ المحلول يُحدَّث حقلًا حقلًا فيبقى ما لا تقرؤه الأداة، كالأوزان (ملاحظتُه الثانية).
- وإن أخفقت `ollama list` (الخادمُ لا يجيب، أو الأمرُ غائب) فلم يُقرأ شيء: لا يُكتب دليلٌ ولا يُمسّ السجلّ، فالقائمةُ الفارغة
  بالإخفاق ليست شاهدًا على أنّ وسمًا لم يُسحب (ملاحظتُه الثالثة)، والرمزُ 3.
- ووسمٌ محلولٌ ببصمة نصٍّ في السجلّ يُقرأ ثانيةً فيُطبع نصٌّ آخر، ولو بتنسيقه، لا تُستبدل بصمتُه: فالدليلُ الذي حلّه بالبصمة
  الأولى باقٍ ويخالفه السجلُّ الجديد (`license_provenance_conflicts`). فتُقيَّد القراءةُ تحت `unresolved_readings` بالبصمتين
  وسببٍ يقول إنّ استبدالَها كلمةُ المالك، ويبقى القيدُ كما كان (ملاحظتُه الخامسة).
- وقبل الكتابة يُفحص السجلُّ بعد الحلّ مع كلِّ أدلّة `docs/probe` والدليلِ الجديد بحارس `tools/model_licenses.py`: فما أحدث
  مخالفةً لم تكن لا يُكتب، وتُطبع مخالفاتُه، والرمزُ 4. فالأداةُ لا تكتب حالةً يرفضها الحارس (ملاحظتُه الخامسة).
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
LIST_FAILED = "ollama_list_failed_on_the_mac_nothing_was_read_and_nothing_was_written"
TEXT_CHANGED = "ollama_show_license_text_differs_from_the_registered_text_on_the_mac_replacing_it_needs_an_owner_word"
# ما يكتبه قيدٌ محلول من قراءةٍ على الماك؛ وما سواه في القيد السابق (كالأوزان) يبقى كما كان
READ_FIELDS = ("license", "source", "read_on", "read_via", "license_text_sha256", "ollama_list_id")
# عناوينُ رخصٍ تُسمّى من سطرها الأوّل كما طُبع، بعد التطبيع؛ ورخصُها المشروطة تفسيرُها قرارُ المالك (المسألة #301 §٥)
TITLES = {
    # عنوانُ Apache سطران كما يُطبع («Apache License» ثم «Version 2.0, January 2004»)، ويُحتاج إليه حين يُملأ سطرُ حقوق
    # النشر في الملحق فلا تطابق بصمةُ SPDX؛ وهي حالةُ `qwen3:14b` في دليل ٦ أكتوبر (ملاحظة Codex الرابعة على #301)
    "apache license version 2.0, january 2004": "apache-2.0",
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
    "a_license_is_named_from_its_text_only_by_the_spdx_body_digest_or_by_known_title_lines_among_its_first_three_and_text_named_by_neither_stays_pending",
    "a_title_line_names_the_license_as_printed_and_its_terms_are_not_interpreted",
    "an_empty_license_text_or_a_failed_show_leaves_the_entry_pending_with_the_named_reason_and_nothing_was_pulled_or_spent",
    "tags_absent_from_ollama_list_were_not_pulled_and_stay_pending",
    "unresolved_tags_are_listed_under_unresolved_readings_not_under_a_model_field_because_new_evidence_may_not_name_a_model_whose_license_is_still_pending",
    "each_run_writes_its_own_file_so_a_same_day_rerun_does_not_replace_the_evidence_an_earlier_run_resolved_the_registry_against",
    "a_failed_ollama_list_reads_no_tag_and_writes_nothing_because_an_empty_list_from_a_failure_is_not_evidence_that_a_tag_was_not_pulled",
    "a_resolved_tag_whose_text_now_differs_from_its_registered_digest_is_not_rewritten_because_the_evidence_that_resolved_it_would_then_conflict_and_replacing_it_is_the_owners_word",
    "nothing_is_written_that_the_license_guard_would_refuse_over_the_registry_and_every_probe_file_together",
]
Runner = Callable[[list[str]], subprocess.CompletedProcess]


def run(command: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, check=False)
    except OSError as error:  # الأمرُ غائب أو لا يُنفَّذ: إخفاقٌ مسمًّى لا انفجار
        return subprocess.CompletedProcess(command, 127, b"", f"{type(error).__name__}: {error}".encode())


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
    # العنوانُ يبدأ في أحد الأسطر الثلاثة الأولى غير الفارغة، وقد يمتدّ على سطرين (Apache)، فتُقابَل الأسطرُ المتتالية مجموعةً
    for first in range(min(3, len(lines))):
        head = " ".join(lines[first:first + 3])
        for title, name in TITLES.items():
            if head.startswith(title):
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


def registered_texts(registry: dict) -> dict[str, str]:
    """بصمةُ نصّ الرخصة لكلّ قيدٍ محلولٍ يحملها: ما يُقارَن به نصٌّ يُقرأ ثانيةً."""
    models = registry.get("models", {})
    return {name: entry["license_text_sha256"] for name, entry in models.items()
            if isinstance(entry, dict) and isinstance(entry.get("license_text_sha256"), str)}


def probe(tags: list[str], day: str, runner: Runner | None = None, registered: dict[str, str] | None = None) -> dict:
    """الدليلُ: ما حُلّ تحت `models`، وما لم يُحلّ تحت `unresolved_readings`. و`registered` بصماتُ النصوص المقيَّدة
    (`registered_texts`): فما قُرئ لوسمٍ منها بنصٍّ آخر لا يُحلّ ثانيةً بل يُقيَّد بالبصمتين (ملاحظة Codex الخامسة على #301)."""
    runner = runner or run
    registered = registered or {}
    version = runner(["ollama", "--version"])
    evidence = {
        "schema_version": 1, "date": day, "agent": AGENT, "kind": "ollama_model_license_reading",
        "tool": "tools/ollama_license_read.py: ollama show --license <tag> on the mac, "
                + (version.stdout or b"").decode("utf-8", errors="replace").strip().replace("ollama version is ", "ollama "),
        "models": {}, "licenses": {}, "unresolved_readings": [],
        "spend": {"cloud_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0, "cost_basis": "local_no_charge"},
        "measurement_limits": LIMITS,
        "spend_note": "ollama_show_reads_local_metadata_no_model_pulled_no_cloud_call",
    }
    listing = runner(["ollama", "list"])
    if listing.returncode != 0:
        # قائمةٌ أخفقت ليست قائمةً فارغة: لا يُقرأ وسم، ولا يُنسب إلى وسمٍ أنّه لم يُسحب (ملاحظة Codex الثالثة على #301)
        stderr = (listing.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        evidence["ollama_list_failed"] = {"exit_code": listing.returncode, "stderr_first_line": stderr[0] if stderr else "",
                                          "read_on": day}
        evidence["unresolved_readings"] = [{"tag": tag, "not_read": LIST_FAILED, "read_on": day} for tag in tags]
        return evidence
    listed = parse_list((listing.stdout or b"").decode("utf-8", errors="replace"))
    for tag in tags:
        record, ok = read_tag(tag, listed, day, runner)
        if ok and tag in registered and record["license_provenance"]["license_text_sha256"] != registered[tag]:
            # نصٌّ آخر لوسمٍ محلولٍ ببصمةٍ: لو استُبدلت لخالف السجلُّ الدليلَ الذي حلّه، فتُقيَّد القراءةُ ولا تُطبَّق
            prov = record.pop("license_provenance")
            record.pop("license_named_by", None)
            # والبصمةُ المقيَّدة تحت مفتاحٍ لا ينتهي بـ`_sha256` حتى لا يقرأها الحارسُ بصمةَ أثرٍ بلا مالك
            record.update({"not_applied": TEXT_CHANGED, "license_text_sha256": prov["license_text_sha256"],
                           "registered_license_text_digest": registered[tag], "license_named_from_text": prov["license"]})
            ok = False
        if ok:
            evidence["models"][tag] = record
            evidence["licenses"][tag] = record["license_provenance"]["license"]
        else:
            evidence["unresolved_readings"].append(record)
    return evidence


def apply(registry: dict, evidence: dict) -> dict:
    """قيودُ السجلّ بعد الدليل: ما حُلّ يُكتب برخصته ومصدره وتاريخه وبصمة نصّه، وما لم يُحلّ يُكتب سببُه كما حدث.
    والقيدُ المحلول من قبل يُحدَّث في حقول القراءة وحدها، فما لا تقرؤه الأداة فيه (كالأوزان ومصادرها) يبقى
    (ملاحظة Codex الثانية على #301)؛ وما لم يُقرأ أصلًا (`not_read`) لا يمسّ قيدَه."""
    models = registry["models"]
    for tag, record in evidence.get("models", {}).items():
        prov = record["license_provenance"]
        read = {"license": prov["license"], "source": prov["source"], "read_on": prov["read_on"],
                "read_via": "ollama_show_license_on_the_mac", "license_text_sha256": prov["license_text_sha256"],
                "ollama_list_id": record["ollama_list_id"]}
        previous = models.get(tag)
        kept = {k: v for k, v in previous.items() if k not in READ_FIELDS and k != "pending"} \
            if isinstance(previous, dict) else {}
        models[tag] = {**read, **kept}
    for record in evidence.get("unresolved_readings", []):
        if "pending" in record and record["tag"] in models and "pending" in models[record["tag"]]:
            models[record["tag"]] = {"pending": record["pending"]}
    registry["models"] = dict(sorted(models.items()))
    return registry


def introduced_findings(before: dict, after: dict, probe_dir: Path, out: Path, evidence: dict, engine: str | None) -> list[str]:
    """ما يُحدثه الحلُّ من مخالفاتٍ في الحارس على السجلّ وكلِّ أدلّة `docs/probe` والدليلِ الجديد معًا، ولم يكن قبله؛ فالأداةُ
    لا تكتب حالةً يرفضها الحارس، كسجلٍّ حُدِّثت بصمتُه وبقي دليلٌ حلّه بغيرها (ملاحظة Codex الخامسة على #301)."""
    existing = ml.load_evidence(probe_dir) if probe_dir.is_dir() else {}
    was = set(ml.findings(before, existing, engine))
    now = set(ml.findings(after, {**existing, out.name: evidence}, engine))
    return sorted(now - was)


def evidence_path(probe_dir: Path, day: str) -> Path:
    """ملفٌّ لكلّ تشغيل: الأوّلُ في اليوم بلا لاحقة، ثمّ `b`، `c`… كما تُسمّى أدلّةُ اليوم الواحد في `docs/probe`؛
    فلا يُستبدل دليلٌ سابق بقيت بصماتُه في السجلّ (ملاحظة Codex الأولى على #301)."""
    stem = f"model-licenses-ollama-{day.replace('-', '')}"
    candidates = [probe_dir / f"{stem}.json"] + [probe_dir / f"{stem}{suffix}.json" for suffix in "bcdefghijklmnopqrstuvwxyz"]
    for candidate in candidates:
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"{stem}: every suffix through z is taken in {probe_dir}")


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
    evidence = probe(tags, args.day, registered=registered_texts(registry))
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    if "ollama_list_failed" in evidence:
        failed = evidence["ollama_list_failed"]
        print(f"ollama list failed (exit {failed['exit_code']}): {failed['stderr_first_line']}; nothing read, nothing written",
              file=sys.stderr)
        return 3
    for record in evidence["unresolved_readings"]:
        if record.get("not_applied") == TEXT_CHANGED:
            print(f"{record['tag']}: license text differs from the registered digest; the entry was kept and the reading "
                  f"recorded, replacing it is the owner's word", file=sys.stderr)
    if args.write:
        out = evidence_path(args.probe_dir, args.day)
        applied = apply(json.loads(json.dumps(registry)), evidence)
        introduced = introduced_findings(registry, applied, args.probe_dir, out, evidence, ml.default_engine())
        if introduced:
            print("the license guard would refuse what this run resolves; nothing written:\n  " + "\n  ".join(introduced),
                  file=sys.stderr)
            return 4
        out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        args.registry.write_text(json.dumps(applied, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out} and {args.registry}", file=sys.stderr)
    return 0 if not evidence["unresolved_readings"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
