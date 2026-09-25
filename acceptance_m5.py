#!/usr/bin/env python3
"""مُشغِّل دليل قبول م٥ — الموجّه والتيارات وعقدة اللغويات/المعرِّب (ق٢٠).

    python3 acceptance_m5.py

نصّ القبول المعتمد: استعلام يمس العقدتين يُجاب موادَّ من كلتيهما عبر
الموجّه حصرًا؛ استعلام جذر يعيد مداخل بصفحات مطابقة للمطبوع (تدقيق
يدوي 20 جذرًا — يُكتب دليلها)؛ تعريب نص بحري يمر ببوابة المسرد.

نداءات نموذج حية (سؤال حوكمة + تعريب). التيارات والسجل الرئيس تُصفَّر
لكل تشغيل (حالة تشغيلٍ لا حقيقة مدفوعة).
"""
from __future__ import annotations

import datetime
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


from core.budget import Budget
from core.corpus import CorpusCatalog
from core.knowledge import KnowledgeEvent
from core.ledger import Ledger
from core.registry import NodeRegistry
from core.router import last_run_age_s, main_ledger, route_once
from core.streams import Stream, inbox, outbox
from providers.ollama import OllamaProvider
from run_node import load_node_module, main as run_node_main

_ling_mod = load_node_module("linguistics")
LinguisticsNode = _ling_mod.LinguisticsNode
term_in_text = _ling_mod.term_in_text

CHECKS: list[tuple[str, bool, str]] = []
ROOTS_20 = ("صبر", "بحر", "سفن", "ملح", "شحن", "ربن", "غرق", "رسو",
            "قلع", "مرس", "نقل", "حمل", "عوم", "جزر", "مدد", "هدي",
            "سلم", "علم", "وسق", "قطر")


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def emit_gateway_query(to: str, question: str, domain: str, corr: str):
    outbox(ROOT, "gateway").emit(KnowledgeEvent(
        kind="query", from_node="gateway", to=to,
        correlation_id=corr, data_policy="regulated",
        payload={"question": question, "domain": domain},
        idempotency_key=f"gw-{to}-{corr}", budget_cap_micros=1))


def main() -> int:
    now_iso = datetime.datetime.now(datetime.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # ١ — سجل العقد: العقد الثلاثة مسجلة مختومة
    registry = NodeRegistry(ROOT / "registry" / "nodes.jsonl")
    names = sorted(registry.current())
    check("العقد الثلاثة (البوابة، الحوكمة، اللغويات) مسجلة مختومة",
          {"gateway", "linguistics", "maritime"}.issubset(names)
          and registry.verify(strict=True), "، ".join(names))

    # ٢ — الحوار متعدد العقد عبر الموجّه حصرًا (تصفير حالة التشغيل)
    for d in (ROOT / "streams", ROOT / "ledger"):
        shutil.rmtree(d, ignore_errors=True)
    for f in ("gateway-offsets.json", "maritime-offsets.json",
              "linguistics-offsets.json", "maritime-calls.jsonl",
              "maritime-calls.jsonl.anchor", "linguistics-calls.jsonl",
              "linguistics-calls.jsonl.anchor"):
        (ROOT / "var" / f).unlink(missing_ok=True)

    corr = "m5-golden"
    # سؤال محدد يلتصق بنص المادة — العريض التأليفي يستدرج إعلان العجز
    # من نموذجٍ بلا تفكير (سلوك أمين لا عيب، لكنه ليس غاية هذا الدليل)
    emit_gateway_query(
        "maritime",
        "ما المعاينات التي تخضع لها السفن بموجب لائحة إدارة مياه الصابورة؟",
        "maritime", corr)
    emit_gateway_query("linguistics", "صبر", "lexicon", corr)
    # ورسالة مزوّرة الهوية تُدس في صادر الحوكمة — يجب أن يردّها الموجّه
    outbox(ROOT, "maritime").emit(KnowledgeEvent(
        kind="query", from_node="linguistics", to="gateway",
        correlation_id=corr, data_policy="internal",
        payload={"question": "انتحال", "domain": "x"},
        idempotency_key="forged-1", budget_cap_micros=1))

    r1 = route_once(ROOT, now_iso)
    # ورسالة تُدس مباشرة في وارد الحوكمة متجاوزةً الموجّه — قيدُ العبور
    # شرطُ الاستهلاك فلا تُخدَم (عيب تدقيق م٥ المُصلح)
    injected_digest = inbox(ROOT, "maritime").emit(KnowledgeEvent(
        kind="query", from_node="gateway", to="maritime",
        correlation_id=corr, data_policy="regulated",
        payload={"question": "حقن مباشر", "domain": "x"},
        idempotency_key="inj-1", budget_cap_micros=1))
    sys.argv = ["run_node.py", "maritime"]; run_node_main()
    sys.argv = ["run_node.py", "linguistics"]; run_node_main()
    r2 = route_once(ROOT, now_iso)

    got = inbox(ROOT, "gateway").read_from(0)
    answers = [(ev, dg) for _, ev, dg in got if ev.kind == "answer"]
    by_sender = {ev.from_node: ev for ev, _ in answers}
    maritime_ok = ("maritime" in by_sender
                   and by_sender["maritime"].payload.get("originality")
                   == "derived")
    ling_ok = ("linguistics" in by_sender
               and by_sender["linguistics"].payload.get("originality")
               == "original"
               and "ص" in by_sender["linguistics"].payload.get("locus", ""))
    check("استعلامٌ مسّ العقدتين فأجابتا موادَّ (مشتقة ومعجمية أصلًا) عبر الموجّه",
          maritime_ok and ling_ok,
          f"سُلِّم {r1['delivered']}+{r2['delivered']}، أجوبة {len(answers)}")

    led = main_ledger(ROOT)
    recs = [e["record"] for e in led.entries()]
    transits = [r for r in recs if r["kind"] == "transit"]
    refused = [r for r in recs if r["kind"] == "transit_refused"]
    check("كل عبور مقيد بالأثرين، والمزوّر الهوية رُدّ برمزه",
          len(transits) >= 4 and led.verify_chain(strict=True)
          and any(r["code"] == "sender_mismatch" for r in refused),
          f"{len(transits)} عبورًا، {len(refused)} ردًّا")
    replies_to_injected = [
        ev for _o, ev, _d in outbox(ROOT, "maritime").read_from(0)
        if ev.in_reply_to == injected_digest]
    check("المدسوس في الوارد بلا قيد عبور لم يُخدَم",
          not replies_to_injected,
          f"{len(replies_to_injected)} جوابًا للمدسوسة")
    age = last_run_age_s(ROOT, time.time())
    check("نبض الموجّه مقيد وحارس الشيخوخة يقرؤه",
          age is not None and age < 300, f"العمر {age:.0f} ث")

    # ٣ — عشرون جذرًا: مداخل بصفحات المطبوع من معجمين فأكثر
    ling = LinguisticsNode(ROOT)
    roots_out, failed, books = [], [], set()
    for root_w in ROOTS_20:
        try:
            entries = ling.lookup(root_w, limit=4)
            page_ok = [e for e in entries if "ص" in e["item"]["locus"]]
            if not page_ok:
                failed.append(root_w)
                continue
            for e in page_ok:
                books.add(e["item"]["part"].split(" — ")[0])
            roots_out.append({
                "الجذر": root_w,
                "مداخل": [{"الكتاب": e["item"]["part"],
                           "الموضع": e["item"]["locus"],
                           "بصمة": e["item_digest"][:16],
                           "مقتطف": e["item"]["text"][:120]}
                          for e in page_ok[:3]],
            })
        except LookupError:
            failed.append(root_w)
    check("عشرون جذرًا أُجيبت مداخلَ بمواضع المطبوع من معجمين فأكثر",
          len(roots_out) == 20 and len(books) >= 2,
          f"{len(roots_out)}/20 من {len(books)} كتب"
          + (f" — أخفق: {failed[:4]}" if failed else ""))
    ev_file = ROOT / "nodes" / "linguistics" / "evidence" / "roots-20.json"
    ev_file.write_text(json.dumps(
        {"generated_by": "acceptance_m5", "roots": roots_out},
        ensure_ascii=False, indent=1), encoding="utf-8")
    check("دليل الجذور مكتوب للتدقيق اليدوي", ev_file.exists(),
          str(ev_file.relative_to(ROOT)))

    # الكلمة المجردة تصل إلى تعريفٍ ذي أداة تعريف/عطف في المصدر الفعلي.
    ballast = ling.lookup("صابورة", limit=2)
    expected = {("المعجم الوسيط — ج1", "ج1 ص506")}
    if (ROOT / "corpus" / "lexicons-local" / "_catalog.jsonl").exists():
        expected.add(("تاج العروس — ج12", "ج12 ص285"))
    found = {(p["item"]["part"], p["item"]["locus"]) for p in ballast}
    ballast_open = all(CorpusCatalog(
        ROOT / "corpus" / p["store"] / "_catalog.jsonl"
    ).find_page(ROOT, p["item_digest"]) is not None for p in ballast)
    ballast_file = ev_file.with_name("lexicon-variants.json")
    ballast_file.write_text(json.dumps({
        "generated_by": "acceptance_m5", "query": "صابورة",
        "results": [{"store": p["store"], "digest": p["item_digest"],
                     "part": p["item"]["part"], "locus": p["item"]["locus"],
                     "source_id": p["item"]["source_id"]} for p in ballast],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    check("صابورة المجردة تسترجع التعريف المعرف والمعطوف بشواهد تُفتح",
          expected <= found and ballast_open,
          "، ".join(f"{part}: {locus}" for part, locus in sorted(found)))

    # ٤ — تعريب نص بحري يمر ببوابة المسرد
    (ROOT / "var" / "m5-translate.jsonl").unlink(missing_ok=True)
    (ROOT / "var" / "m5-translate.jsonl.anchor").unlink(missing_ok=True)
    ling_llm = LinguisticsNode(ROOT, OllamaProvider("qwen3:14b"),
                               Budget(10_000_000, 100_000_000),
                               Ledger(ROOT / "var" / "m5-translate.jsonl"))
    en = ("The master of a ship shall ensure that ballast water is managed "
          "in accordance with the plan approved by the Administration, and "
          "the owner shall keep the crew informed of these duties on every "
          "international voyage.")
    try:
        tr = ling_llm.translate(en, source_id="un-treaties-cn",
                                locus="مقتطف تجريبي — أسلوب MARPOL/BWM",
                                part="نص قبول م٥")
        enforced = [ar for _, ar in tr["bindings"]]
        check("التعريب مرّ ببوابة المسرد: كل مكافئٍ عُرِّب بمصطلحه المعتمد",
              len(tr["bindings"]) >= 3
              and all(term_in_text(ar, tr["item"].text) for ar in enforced)
              and tr["item"].originality == "translated"
              and tr["item"].glossary_ref == "maritime"
              and tr["item"].lang == "en",
              f"{len(tr['bindings'])} مصطلحات ملزمة: "
              + "، ".join(enforced[:4]))
    except Exception as exc:
        check("التعريب مرّ ببوابة المسرد", False,
              f"{type(exc).__name__}: {exc}")

    print("\n— دليل قبول م٥ —\n")
    width = max(len(n) for n, _, _ in CHECKS)
    failed_n = sum(1 for _, ok, _ in CHECKS if not ok)
    for name, ok, detail in CHECKS:
        print(f"  {'✓' if ok else '✗'}  {name:<{width}}  {detail}")
    print(f"\n  {len(CHECKS) - failed_n}/{len(CHECKS)} اجتازت.\n")
    return 1 if failed_n else 0


if __name__ == "__main__":
    raise SystemExit(main())
