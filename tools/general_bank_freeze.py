#!/usr/bin/env python3
"""ك٤٣ (#28): بنكُ العربية العامة v3، مئةٌ وخمسون حالةً على القدرات التسع، مجمَّدٌ ببصمته قبل أيّ قياس.

البنكُ ملفّا Kimi `evaluation/suites/arabic_general_v3_1.json` و`arabic_general_v3_2.json` كما سُلّما بايتًا ببايت
(`docs/external/KIMI-NEXT.md` §٢)، وهذه الأداةُ تصفه بالأعداد والبصمات لا بالمحتوى، وتحكم عليه:

- `validate_suite` الحقيقيّ على كلّ ملفّ، والمعرّفاتُ فريدةٌ في الملفّين معًا.
- ≥١٥٠ حالة، وكلُّ قدرةٍ من التسع حاضرةٌ بعشر حالاتٍ فأكثر، ولكلّ حالةٍ فحصٌ واحد على الأقل.
- لا حالةَ يمرّرها جوابٌ ثابت على فحوصها الخارجة عن الحاوية (`tools/kimi_intake.gameable_probe`)؛ وما لا يحكم فيه إلا
  فحصُ الحاوية يُعدّ `needs_sandbox` لا سليمًا.
- مستقلٌّ عن بنك القياس وعن بنكَي v1 وv2: لا معرّفَ مشترك ولا رسالةَ أولى مشتركة.

والتجميد: `--write` يكتب الدليلَ `docs/probe/k43-general-150-<date>.json` ببصمة كلّ ملفّ، و`--check` يعيد الحسابَ ويسمّي
ما تغيّر؛ فتغييرُ حالةٍ واحدة يغيّر البصمةَ ويحمرّ الحارس `tests/test_arabic_general_v3.py` حتى يُكتب دليلٌ جديد يعلن ذلك.

الحدود: الأداةُ لا تحكم على صحّة المرجع ولا على معنى الفحص، ولا تشغّل فحوصَ الحاوية، ولا تقيس محرّكًا.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.capabilities import PayloadRejected, validate_suite  # noqa: E402
from tools.kimi_intake import GENERAL_CAPABILITIES, GENERAL_MIN_CASES, gameable_probe  # noqa: E402

SUITES = ("arabic_general_v3_1.json", "arabic_general_v3_2.json")
SUITES_DIR = ROOT / "evaluation" / "suites"
MEASUREMENT_BANK = ROOT / "evaluation" / "banks" / "kimi_v1" / "open"
EARLIER_SUITES = ("arabic_general_v1.json", "arabic_general_v2.json")
PROBE_DIR = ROOT / "docs" / "probe"
PROBE_PREFIX = "k43-general-150-"
MIN_CASES = GENERAL_MIN_CASES
MIN_PER_CAPABILITY = 10
KIND = "k43_general_bank_freeze"
LIMITS = [
    "the_freeze_binds_the_bytes_of_the_two_suite_files_not_the_truth_of_any_reference_answer_or_the_meaning_of_any_check",
    "fixed_answer_probes_run_only_the_checks_outside_the_container_a_case_whose_other_checks_a_fixed_answer_passes_is_counted_needs_sandbox_not_judged",
    "independence_is_checked_by_case_id_and_first_user_message_only_a_paraphrase_of_a_measurement_bank_case_is_not_detected",
    "regulation_share_is_a_keyword_count_over_the_case_messages_not_a_reading_of_the_cases",
    "no_engine_was_run_the_bank_is_frozen_before_measurement_and_no_pass_rate_is_claimed_here",
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _first_user(case: dict) -> str:
    messages = case.get("messages") or []
    first = messages[0] if messages and isinstance(messages[0], dict) else {}
    return (first.get("content") or "").strip()


def _earlier_cases(data) -> list[dict] | None:
    """حالاتُ بنكٍ سابق إن صلح شكلُها لاستخراج المعرّف والرسالة الأولى، وإلا `None`.

    لا يُرشَّح عضوٌ مشوَّه صامتًا: `cases` غائبةٌ أو ليست قائمة، أو عضوٌ ليس كائنًا بمعرّفٍ نصّيّ ورسالةٍ أولى نصّية،
    يجعل البنكَ كلَّه غيرَ مقروء؛ فما لا يُقرأ لا يشهد بالاستقلال.
    """
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list):
        return None
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str) or not case["case_id"]:
            return None
        messages = case.get("messages")
        if (not isinstance(messages, list) or not messages or not isinstance(messages[0], dict)
                or not isinstance(messages[0].get("content"), str)):
            return None
    return cases


def _bank_identity(bank: Path) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    firsts: set[str] = set()
    if not bank.is_dir():
        return ids, firsts
    for path in sorted(bank.rglob("*.json")):
        if path.name.endswith(".meta.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for case in (data.get("cases") or []) if isinstance(data, dict) else []:
            if isinstance(case, dict):
                ids.add(str(case.get("case_id")))
                firsts.add(_first_user(case))
    return ids, firsts


def profile(suites_dir: Path | None = None, *, measurement_bank: Path = MEASUREMENT_BANK) -> dict:
    """وصفُ البنك بالأعداد والبصمات، وما خالف الشروطَ في `findings`. لا محتوى حالةٍ فيه."""
    suites_dir = SUITES_DIR if suites_dir is None else suites_dir
    files, cases, findings = [], [], []
    for name in SUITES:
        path = suites_dir / name
        if not path.is_file():
            findings.append(f"file_missing:{name}")
            continue
        raw = path.read_bytes()
        entry = {"path": f"evaluation/suites/{name}", "sha256": _sha256(raw), "bytes": len(raw), "cases": 0}
        try:
            suite = validate_suite(json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            findings.append(f"suite_unreadable:{name}")
            files.append(entry)
            continue
        except PayloadRejected as exc:
            findings.append(f"suite_invalid:{name}:{exc.code}")
            files.append(entry)
            continue
        entry["cases"] = len(suite["cases"])
        files.append(entry)
        cases.extend(suite["cases"])
    ids = Counter(case["case_id"] for case in cases)
    findings += [f"case_id_duplicate:{case_id}" for case_id, n in sorted(ids.items()) if n > 1]
    capabilities = Counter(case["capability"] for case in cases)
    for capability in sorted(GENERAL_CAPABILITIES):
        if capabilities.get(capability, 0) < MIN_PER_CAPABILITY:
            findings.append(f"capability_under_minimum:{capability}:{capabilities.get(capability, 0)}")
    findings += [f"capability_unknown:{capability}" for capability in sorted(set(capabilities) - GENERAL_CAPABILITIES)]
    if len(cases) < MIN_CASES:
        findings.append(f"too_few_cases:{len(cases)}")
    findings += [f"case_without_checks:{case['case_id']}" for case in cases if not case["checks"]]
    probes: Counter = Counter()
    for case in cases:
        result = gameable_probe(case)
        probes["not_gameable" if result is None else result] += 1
        if result not in (None, "needs_sandbox"):
            findings.append(f"gameable_by_fixed_answer:{case['case_id']}:{result}")
    bank_ids, bank_firsts = _bank_identity(measurement_bank)
    overlap = {"measurement_bank_cases": len(bank_ids),
               "case_id_overlap": sum(case["case_id"] in bank_ids for case in cases),
               "first_message_overlap": sum(_first_user(case) in bank_firsts for case in cases)}
    findings += [f"overlaps_measurement_bank:{case['case_id']}" for case in cases
                 if case["case_id"] in bank_ids or _first_user(case) in bank_firsts]
    # بنكا v1 وv2 كبنك القياس: المعرّفُ وحده أو الرسالةُ الأولى وحدها تكفي لتسمية الحالة، فلا يُجمَّد بنكٌ يكرّرهما.
    for name in EARLIER_SUITES:
        earlier = suites_dir / name
        if earlier.is_file():
            try:
                data = json.loads(earlier.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            earlier_cases = _earlier_cases(data)
            if earlier_cases is None:
                findings.append(f"earlier_suite_unreadable:{name}")
                continue
            e_ids = {str(c.get("case_id")) for c in earlier_cases}
            e_firsts = {_first_user(c) for c in earlier_cases}
            overlap[name] = {"case_id_overlap": sum(c["case_id"] in e_ids for c in cases),
                             "first_message_overlap": sum(_first_user(c) in e_firsts for c in cases)}
            findings += [f"overlaps_earlier_suite:{name}:{case['case_id']}" for case in cases
                         if case["case_id"] in e_ids or _first_user(case) in e_firsts]
    regulation = sum(any(word in json.dumps(case["messages"], ensure_ascii=False)
                         for word in ("نظام", "لائحة", "المادة", "تشريع", "قانون")) for case in cases)
    return {
        "files": files,
        "bank_sha256": _sha256("\n".join(f["sha256"] for f in files).encode("utf-8")),
        "cases": len(cases),
        "capabilities": dict(sorted(capabilities.items())),
        "check_kinds": dict(sorted(Counter(check["kind"] for case in cases for check in case["checks"]).items())),
        "checks_per_case_min": min((len(case["checks"]) for case in cases), default=0),
        "critical_cases": sum(bool(case["critical"]) for case in cases),
        "multi_turn_cases": sum(len(case["messages"]) > 1 for case in cases),
        "fixed_answer_probes": dict(sorted(probes.items())),
        "independence": overlap,
        "regulation_keyword_cases": regulation,
        "findings": findings,
    }


def freeze_findings(current: dict, frozen: dict) -> list[str]:
    """ما تغيّر بين البنك الحاليّ والدليل المجمَّد: ملفٌّ زال، أو بصمةٌ تغيّرت، أو عددٌ تغيّر."""
    problems = []
    frozen_files = {f["path"]: f for f in frozen.get("files", []) if isinstance(f, dict)}
    current_files = {f["path"]: f for f in current.get("files", [])}
    for path, entry in sorted(frozen_files.items()):
        actual = current_files.get(path)
        if actual is None:
            problems.append(f"frozen_file_missing:{path}")
        elif actual["sha256"] != entry.get("sha256"):
            problems.append(f"digest_changed:{path}")
    for path in sorted(set(current_files) - set(frozen_files)):
        problems.append(f"file_not_frozen:{path}")
    if frozen.get("bank_sha256") != current.get("bank_sha256"):
        problems.append("bank_digest_changed")
    if frozen.get("cases") != current.get("cases"):
        problems.append("case_count_changed")
    return problems


def latest_probe(probe_dir: Path = PROBE_DIR) -> Path | None:
    candidates = sorted(probe_dir.glob(PROBE_PREFIX + "*.json"))
    return candidates[-1] if candidates else None


def evidence(day: str, agent: str, current: dict) -> dict:
    return {
        "schema_version": 1,
        "kind": KIND,
        "task": "ك٤٣ (#28)",
        "agent": agent,
        "date": day,
        "status": "frozen_not_measured",
        "source": {
            "author": "Kimi (moonshot), v1.2 delivery round 3 of 2026-10-06, development part 2 of docs/external/KIMI-NEXT.md",
            "delivery_outside_the_repository": "~/kimi-work/kimi-benchmark/",
            "intake_evidence": "docs/probe/kimi-v12-intake-round3-20261006.json",
            "placed_byte_for_byte": True,
        },
        "spend": {"cloud_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0,
                  "cost_basis": "local_no_charge"},
        **{key: value for key, value in current.items() if key != "findings"},
        "findings": current["findings"],
        "measurement": {
            "status": "not_measured",
            "next": "tools/evaluate_capabilities.py --suite evaluation/suites/arabic_general_v3_1.json (then _2) "
                    "on the Mac with the frozen default engine; pass rate with Wilson 95% per capability",
        },
        "measurement_limits": LIMITS,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="يكتب دليلَ التجميد؛ يلزمه --date و--agent")
    parser.add_argument("--check", action="store_true", help="يقارن البنكَ الحاليّ بآخر دليلِ تجميد")
    parser.add_argument("--date", help="يومُ التجميد YYYY-MM-DD")
    parser.add_argument("--agent", help="معرّفُ العميل المسجَّل")
    parser.add_argument("--probe", type=Path, help="دليلٌ بعينه بدل الأحدث")
    args = parser.parse_args(argv)
    current = profile()
    report = {"findings": current["findings"], "cases": current["cases"], "bank_sha256": current["bank_sha256"]}
    if args.write:
        if not args.date or not args.agent:
            parser.error("--write يلزمه --date و--agent")
        # اسمُ الدليل بالتاريخ المضغوط كسائر أدلّة المهامّ (`k17-gameable-sample-20261005.json`)
        target = PROBE_DIR / f"{PROBE_PREFIX}{args.date.replace('-', '')}.json"
        target.write_text(json.dumps(evidence(args.date, args.agent, current), ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        report["written"] = str(target.relative_to(ROOT))
    if args.check or not args.write:
        probe = args.probe or latest_probe()
        if probe is None:
            report["freeze"] = ["no_freeze_evidence"]
        else:
            frozen = json.loads(probe.read_text(encoding="utf-8"))
            report["freeze"] = freeze_findings(current, frozen)
            report["probe"] = str(probe.relative_to(ROOT)) if probe.is_relative_to(ROOT) else str(probe)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if report["findings"] or report.get("freeze") else 0


if __name__ == "__main__":
    raise SystemExit(main())
