#!/usr/bin/env python3
"""دليل م١٦ المحلي لفحوص المكونات والـfixtures وحلقة الفعل المحكومة.

البوابات الحتمية للمرحلة م١٦:
١. فحص مكونات DALUB وعلامات المرجع؛ لا نداء نموذج أو شهادة جودة.
٢. تطابق تصنيفات برمجية على مدخلات مصطنعة؛ لا حكم قانوني على نصوص حقيقية.
٣. محرك حلقة الفعل المحكومة (GovernedActionLoop) وفرض درجات الإذن الثلاث (auto, logged, owner).
٤. رفض الأوامر بلا إذن أو منفذ حاوية موثوق؛ إعلان البيئة وحده لا يكفي.
٥. تكامل الأدوات التشغيلية الحقيقية مع سجل وكيل ديوان العام (ToolRegistry).
٦. وجود ق٤٦ في السجل التاريخي، وليس اعتماد مضمونه أو جاهزية إطلاق حاليًا.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dataclasses import replace
from agent.actions import ActionStore
from agent.journal import Journal
from agent.registry import ToolContext, ToolRegistry
from core.action_loop import ActionControl, GovernedActionLoop
from core.contracts import ToolCall, ToolSpec
from core.corpus import (
    RIGHTS_HERITAGE_PUBLIC_DOMAIN,
    RIGHTS_RESTRICTED_COMMENTARY,
    RIGHTS_STATUTORY_PUBLIC_DOMAIN,
    classify_copyright_status,
)
from core.ledger import Ledger
from core.tools_registry import create_default_action_loop, export_agent_tools
from tools.evaluate_dalub import evaluate_suite


def run_acceptance_m16(tmp_dir: Path) -> dict:
    # ١. فحوص البنك المحلي: مكونات فعلية وعلامات مرجعية، بلا نماذج.
    suite_path = ROOT / "evaluation" / "suites" / "dalub_v1.json"
    if not suite_path.exists():
        raise FileNotFoundError(f"Suite missing: {suite_path}")
    report = evaluate_suite(suite_path)
    assert report["status"] == "passed", "فحوص بنك DALUB لم تجتز"
    assert report["total_cases"] >= 200, f"عدد قضايا DALUB أقل من 200: {report['total_cases']}"
    assert report["overall_accuracy"] == 1.0, f"نسبة فحوص البنك ليست كاملة: {report['overall_accuracy']}"
    for pillar in ("morphology", "syntax", "semantics", "attribution", "quarantine"):
        p_res = report["pillars"][pillar]
        assert p_res["passed"] == p_res["total"], f"فشل في محور {pillar}"

    # ٢. نختبر مخرجات مصنّف برمجي فقط؛ لا تطبيق قانون أو تقرير حقوق نص بعينه.
    assert classify_copyright_status("regulation") == RIGHTS_STATUTORY_PUBLIC_DOMAIN
    assert classify_copyright_status("law") == RIGHTS_STATUTORY_PUBLIC_DOMAIN
    assert classify_copyright_status("judgment") == RIGHTS_STATUTORY_PUBLIC_DOMAIN
    assert classify_copyright_status("decision") == RIGHTS_STATUTORY_PUBLIC_DOMAIN
    assert classify_copyright_status("treaty") == RIGHTS_STATUTORY_PUBLIC_DOMAIN
    assert classify_copyright_status("literature", author_death_year=1300, current_year=2026) == RIGHTS_HERITAGE_PUBLIC_DOMAIN
    assert classify_copyright_status("literature", author_death_year=1300, current_year=2026, has_commercial_commentary=True) == RIGHTS_RESTRICTED_COMMENTARY

    # ٣. فحص حلقة الفعل المحكومة ودرجات الإذن الثلاث والرجوع بطلب تحكم
    ledger = Ledger(tmp_dir / "m16_ledger.jsonl")
    loop = create_default_action_loop(ledger=ledger)

    # auto
    res_auto = loop.dispatch_call(ToolCall("c-auto", "analyze_arabic_morphology", {"word": "المستكشفون"}))
    assert res_auto.success is True
    assert res_auto.consent_grade == "auto"
    assert res_auto.output["root"] == "كشف"

    # logged مع إيصال دائم ودفتر رجوع
    ws = tmp_dir / "workspace"
    ws.mkdir(exist_ok=True)
    doc_path = ws / "test.txt"
    control = ActionControl(ActionStore(tmp_dir / "actions", ws),
        ToolContext(ws, Journal(ws), frozenset({"auto", "logged"})), "m16", "write")
    res_logged = loop.dispatch_call(
        ToolCall("c-log", "write_workspace_document", {"path": "test.txt", "content": "م١٦ السيادي"}),
        control=control
    )
    assert res_logged.success is True
    assert res_logged.consent_grade == "logged"
    assert doc_path.read_text("utf-8") == "م١٦ السيادي"
    # تراجع
    assert loop.rollback(res_logged, control=control, request_id="undo-write") is True
    assert not doc_path.exists()

    # ٤. فحص رفض الأوامر عند غياب الإذن أو منفذ التنفيذ الموثوق.
    call_owner = ToolCall("c-owner", "execute_isolated_command", {"command": "echo test"})
    # بلا موافقة المالك
    res_no_owner = loop.dispatch_call(call_owner, owner_approved=False)
    assert res_no_owner.success is False
    assert res_no_owner.error == "action_control_required"

    # Owner decision is a durable bound receipt, not the old boolean.
    effects = []
    owner_spec = ToolSpec("owner_fixture", "Synthetic owner effect", {}, consent="owner")
    owner_loop = GovernedActionLoop({owner_spec.name: owner_spec},
        {owner_spec.name: lambda args, ctx: effects.append("approved") or {"done": True}})
    owner_control = replace(control, turn_id="owner-fixture")
    owner_call = ToolCall("c-owner-fixture", owner_spec.name, {})
    pending = owner_loop.dispatch_call(owner_call, control=owner_control, owner_approved=True)
    assert pending.pending_owner and effects == []
    control.store.decide(pending.action_id, pending.call_digest, pending.revision, approve=True)
    completed = owner_loop.dispatch_call(owner_call, control=owner_control, replayed=True)
    assert completed.success and completed.consent_grade == "owner"
    assert owner_loop.dispatch_call(owner_call, control=owner_control, replayed=True) == completed
    assert effects == ["approved"]

    # موافقة المالك وإعلان البيئة لا ينشئان منفذ حاوية موثوقًا.
    old_host = os.environ.pop("DIWAN_DISPOSABLE_HOST", None)
    try:
        res_no_host = loop.dispatch_call(call_owner, owner_approved=True,
                                           control=replace(control, turn_id="command"))
        assert res_no_host.success is False
        assert "execution_backend_unavailable" in res_no_host.error

        # تبقى مغلقة حتى مع الإعلان القديم؛ لا shell على المضيف.
        os.environ["DIWAN_DISPOSABLE_HOST"] = "m16-ephemeral-sandbox"
        res_host = loop.dispatch_call(call_owner, owner_approved=True,
                                           control=replace(control, turn_id="command"))
        assert res_host.success is False
        assert "execution_backend_unavailable" in res_host.error
    finally:
        if old_host is not None:
            os.environ["DIWAN_DISPOSABLE_HOST"] = old_host
        else:
            os.environ.pop("DIWAN_DISPOSABLE_HOST", None)

    # ٥. فحص تصدير الأدوات وتكاملها مع ToolRegistry
    agent_tools = export_agent_tools()
    assert len(agent_tools) == 6
    agent_ctx = ToolContext(root=ws, journal=Journal(ws), allowed_consents=frozenset({"auto", "logged"}))
    reg = ToolRegistry(*agent_tools)
    res_reg = reg.invoke(ToolCall("c-reg", "analyze_arabic_morphology", {"word": "المستكشفون"}), agent_ctx)
    assert res_reg["status"] == "ok"
    assert "كشف" in res_reg["content"]

    # ٦. وجود نص ق٤٦ تاريخيًا؛ وجوده ليس شهادة حالية بصحة ادعاءاته.
    decisions_text = (ROOT / "docs/DECISIONS.md").read_text(encoding="utf-8")
    assert "## ق٤٦ — اعتماد حلقة الفعل المحكومة ومعيار DALUB الوطني والملكية العامة النظامية بموجب المادة 4" in decisions_text

    return {
        "status": "passed",
        "checks": {
            # الأسماء القديمة للتوافق مع المستهلكين، وحدودها أدناه.
            "dalub_benchmark_perfect": True,
            "statutory_public_domain_article_4": True,
            "governed_action_loop_three_tiers": True,
            "fail_closed_sandbox_enforced": True,
            "agent_registry_tools_integrated": True,
            "decision_q46_codified": True,
        },
        "scope": "component_acceptance_and_fixture_self_checks",
        "model_calls": 0,
        "product_readiness": "not_assessed",
        "certified": False,
        "legal_determination": False,
        "check_scopes": {
            "dalub_benchmark_perfect": "component_and_reference_self_checks",
            "statutory_public_domain_article_4": "software_classification_fixtures_only",
            "governed_action_loop_three_tiers": "shared_receipts_permissions_and_journal_rollback",
            "fail_closed_sandbox_enforced": "unconfigured_execution_refusal_only",
            "agent_registry_tools_integrated": "registry_export_and_morphology_invocation",
            "decision_q46_codified": "historical_text_presence_only",
        },
        "dalub": report,
    }


def main() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        result = run_acceptance_m16(Path(td).resolve())
        dalub = result["dalub"]
        print("\n— دليل م١٦ لفحوص برمجية محلية —\n")
        print(f"  ✓  فحوص بنك DALUB: {dalub['total_passed']}/{dalub['total_cases']}؛ مكونات ومرجع ذاتي، بلا نموذج")
        print("     النحو يقرأ علامات المرجع والدلالة تعد الكلمات؛ ليست نتيجة فهم لغوي")
        print("  ✓  تطابق التصنيفات البرمجية مع fixtures؛ لا حكم على حقوق نصوص حقيقية")
        print("  ✓  حلقة الفعل تستخدم إيصالات الإذن المشتركة والرجوع عبر Journal")
        print("  ✓  رفض الأوامر بلا الإذن أو المنفذ الموثوق؛ هذا لا يختبر حاوية حية")
        print("  ✓  تصدير أدوات السجل واستدعاء مكوّن الصرف (ToolRegistry)")
        print("  ✓  وجود نص ق٤٦ التاريخي؛ ليس اعتمادًا حاليًا لمضمونه")
        print("\n  6/6 فحوص محلية اجتازت؛ لا شهادة، وجاهزية المنتج غير مقاسة.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
