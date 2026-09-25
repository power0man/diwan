"""سجل الأدوات التشغيلية الحقيقية لمنظومة ديوان (م١٦ وما بعدها).

توفير الأدوات الأساسية وفق درجات الإذن الثلاث:
  1. search_regulations (auto): استرجاع الأنظمة واللوائح الرسمية.
  2. analyze_arabic_morphology (auto): التحليل الصرفي وتفكيك الجذور.
  3. write_workspace_document (logged / reversible): كتابة وتعديل الوثائق مع التراجع التلقائي.
  4. execute_isolated_command (owner): تنفيذ أوامر الشل داخل المضيف الزائل بإذن المالك (ق٤٤).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent.journal import Journal, JournalRefused
from agent.registry import ToolContext
from core.action_loop import GovernedActionLoop
from core.canonical import PayloadRejected
from core.contracts import ToolSpec
from core.execution import execute_candidate
from core.ledger import Ledger
from core.linguistics.roots import root_verdict
from projections.morphology import analyze
from workspace_tools.files import WorkspaceError, _relative
from tools.rebuild_index import search as fts_search


def _search_regulations_handler(args: dict, context: dict | None = None) -> list[dict]:
    query = args.get("query", "").strip()
    if not query:
        raise PayloadRejected("tool.search_regulations", "query_empty", "الاستعلام فارغ")
    limit = int(args.get("limit", 5))
    mode = args.get("mode", "hybrid")
    if mode == "fts":
        results = fts_search(query, limit=limit, corpus="maritime")
        return [
            {
                "doc_id": r["doc_id"],
                "part": r["part"],
                "locus": r["locus"],
                "item_digest": r["item_digest"],
            }
            for r in results
        ]
    from core.hybrid_retrieval import hybrid_search
    results = hybrid_search(query, limit=limit, corpus="maritime")
    return [
        {
            "doc_id": r["doc_id"],
            "part": r["part"],
            "locus": r["locus"],
            "item_digest": r["item_digest"],
            "text": r.get("text", ""),
            "score": r.get("score", 0),
        }
        for r in results
    ]


def _analyze_morphology_handler(args: dict, context: dict | None = None) -> dict:
    word = args.get("word", "").strip()
    if not word:
        raise PayloadRejected("tool.analyze_arabic_morphology", "word_empty", "الكلمة فارغة")
    res = analyze(word)
    # الجذرُ من المصدر المقيس (غ١): CAMeL باتّفاق تحليلاته حين يحضر، وإلا القالبيُّ مسمًّى
    verdict = root_verdict(word)
    return {
        "word": res.word,
        "normalized": res.normalized,
        "root": verdict.root,
        "root_source": verdict.source,
        "root_reason": verdict.reason,
        "root_candidates": list(verdict.candidates),
        "template_root": res.root,
        "pattern": res.pattern,
        "stem": res.stem,
        "prefixes": list(res.prefixes),
        "suffixes": list(res.suffixes),
        "weak": res.weak,
        "reason": res.reason,
    }


def _workspace_journal(context: ToolContext | dict | None) -> Journal:
    """سياق الوكيل هو الأصل؛ قاموس الجذر القديم حدّ توافق صريح فقط."""
    if isinstance(context, ToolContext):
        roots = [context.root]
        journal = context.journal
        supplied_journal = True
    elif isinstance(context, dict):
        roots = [context[key] for key in ("workspace_root", "root") if key in context]
        journal = context.get("journal")
        supplied_journal = "journal" in context
    else:
        roots, journal, supplied_journal = [], None, False

    if not roots or any(root is None for root in roots):
        raise PayloadRejected("tool.write_workspace_document", "workspace_context_missing",
                              "جذر مساحة العمل مطلوب صراحةً قبل الكتابة")
    resolved = []
    for root in roots:
        try:
            path = Path(root)
            if not path.is_absolute():
                raise ValueError("absolute workspace root required")
            resolved.append(path.resolve())
        except (TypeError, ValueError, OSError, RuntimeError) as exc:
            raise PayloadRejected("tool.write_workspace_document", "workspace_context_invalid",
                                  "جذر مساحة العمل مسار مطلق صالح") from exc
    if any(root != resolved[0] for root in resolved[1:]):
        raise PayloadRejected("tool.write_workspace_document", "workspace_context_mismatch",
                              "جذرا السياق لا يشيران إلى مساحة العمل نفسها")
    root = resolved[0]
    if not supplied_journal:
        # توافق مع مستدعي GovernedActionLoop القديم الذي يعلن الجذر وحده.
        journal = Journal(root)
    if not isinstance(journal, Journal):
        raise PayloadRejected("tool.write_workspace_document", "workspace_journal_missing",
                              "دفتر الرجوع مطلوب في سياق الوكيل")
    if journal.root != root:
        raise PayloadRejected("tool.write_workspace_document", "workspace_context_mismatch",
                              "دفتر الرجوع لا يتبع مساحة العمل المعلنة")
    return journal


def _write_document_handler(args: dict, context: ToolContext | dict | None = None) -> dict:
    """يكتب عبر دفتر الرجوع — فـ`reversible=True` تصير صادقةً لا مُعلَنة.

    **عيبان مُعادا الإنتاج في فحص ٢١:١٣ غرينتش (٢٢ سبتمبر) أوجبا هذا:**

    ١. الأداةُ تُعلن `reversible=True` و«مع التراجع التلقائي» في وصفها،
       وكانت تكتب بـ`write_text` بلا لقطةٍ ولا إيداع. فالمسارُ الإنتاجيّ
       (`webui/server.py`) لا يمرّر `snapshot_fn`، ولا يمرّرها إلا اختبارٌ
       و`acceptance_m16`. فكان الناتج: `success=True`, `reversible=True`,
       `snapshot=None`, و`rollback()` يُرجع False، والأصلُ ضائع — وهي
       تعمل بدرجة `logged` أي بلا حضور المالك.

    ٢. حظرُ الهروب كان `str(target).startswith(str(root))` بلا فاصلِ مسار،
       فمساحةُ `…/ws` تقبل الكتابةَ في `…/ws-evil/`. **ووقع الأثرُ فعلًا**:
       الملفُّ كُتب خارج المساحة ثم رُفع `ValueError` من `relative_to`
       بعده — خطأٌ يلي الأثر لا يمنعه.

    والعلاجُ إعادةُ استعمالٍ لا اختراع: `agent.journal.Journal` يُودِع قبل
    الأثر، ويحرس المسار بـ`_relative`، ويُرجع `action_id` للرجوع.
    """
    rel_path = args.get("path", "").strip()
    content = args.get("content", "")
    if not rel_path:
        raise PayloadRejected("tool.write_workspace_document", "path_empty", "المسار مطلوب")
    if not isinstance(content, str):
        raise PayloadRejected("tool.write_workspace_document", "content_invalid",
                              "محتوى نصّيّ مطلوب")

    journal = _workspace_journal(context)
    try:
        relative = _relative(rel_path, writing=True)
        # resolve يتبع أيضًا سلفًا رمزيًّا حين لم يُنشأ المجلد الابن بعد.
        # فحارس الدفتر وحده كان يفحص الأب الموجود فقط في هذه الحالة.
        target = (journal.root / relative).resolve()
        if not target.is_relative_to(journal.root):
            raise PayloadRejected("tool.write_workspace_document", "path_escapes_root",
                                  "المسار يخرج من مساحة العمل")
        action = journal.write_file(rel_path, content)
    except (JournalRefused, WorkspaceError) as exc:
        # الرفضُ يُسمّى ويسبق الأثر — لا يليه
        raise PayloadRejected("tool.write_workspace_document", exc.code, exc.reason) from exc
    return {
        "status": "written",
        "path": action.path,
        "bytes": len(content.encode("utf-8")),
        "action_id": action.action_id,
        "reverts_to": "غير موجود" if action.before_sha256 is None
                     else action.before_sha256[:12],
    }


def _execute_isolated_command_handler(args: dict, context: ToolContext | dict | None = None) -> dict:
    command = args.get("command", "")
    if not isinstance(command, str) or not command.strip():
        raise PayloadRejected("tool.execute_isolated_command", "command_empty", "الأمر فارغ")
    root = context.root if isinstance(context, ToolContext) else (
        context.get("workspace_root", context.get("root")) if isinstance(context, dict) else None)
    res = execute_candidate(("/bin/sh", "-c", command.strip()), root, timeout_s=15)
    return {
        "exit_code": res.exit_code,
        "stdout": res.stdout[:2000],
        "stderr": res.stderr[:2000],
        "boundary": res.boundary,
        "output_truncated": res.output_truncated or len(res.stdout) > 2000 or len(res.stderr) > 2000,
    }


def _evaluate_governance_handler(args: dict, context: dict | None = None) -> dict:
    answer = args.get("answer", "").strip()
    if not answer:
        raise PayloadRejected("tool.evaluate_governance", "answer_empty", "نص الجواب مطلوب")
    pages = args.get("pages", {})
    from core.semantic_governance import evaluate_semantic_governance
    res = evaluate_semantic_governance(answer, pages)
    return {
        "all_passed": res.all_passed,
        "refusal_required": res.refusal_required,
        "average_semantic_bp": res.average_semantic_bp,
        "unsupported_claims": list(res.unsupported_claims),
        "diagnostic_suggestions": list(res.diagnostic_suggestions),
        "bindings_count": len(res.bindings),
    }


def _check_mlx_hardware_handler(args: dict, context: dict | None = None) -> dict:
    from providers.mlx_provider import MLXProvider
    import sys
    return {
        "metal_available": MLXProvider.is_hardware_accelerated(),
        "platform": sys.platform,
        "provider_name": "mlx",
        "is_local": True,
        "cost_micros": 0,
    }


def default_tools_registry() -> tuple[dict[str, ToolSpec], dict[str, Callable]]:
    """سجل الأدوات الافتراضية المعتمدة لنظام ديوان."""
    tools = {
        "search_regulations": ToolSpec(
            name="search_regulations",
            description="استرجاع الأنظمة واللوائح الرسمية من مخزن المعرفة",
            parameters={"query": "str", "limit": "int"},
            consent="auto",
            reversible=False,
        ),
        "analyze_arabic_morphology": ToolSpec(
            name="analyze_arabic_morphology",
            description="التحليل الصرفي العربي واستخراج الجذور والأوزان",
            parameters={"word": "str"},
            consent="auto",
            reversible=False,
        ),
        "evaluate_governance": ToolSpec(
            name="evaluate_governance",
            description="فحص وتقييم الجواب بالحوكمة التوليدية المعززة بالاستلزام الدلالي والاشتقاق الحسابي",
            parameters={"answer": "str", "pages": "dict"},
            consent="auto",
            reversible=False,
        ),
        "check_mlx_hardware": ToolSpec(
            name="check_mlx_hardware",
            description="فحص توفر محرك Apple MLX وتسريع عتاد Metal على Apple Silicon",
            parameters={},
            consent="auto",
            reversible=False,
        ),
        "write_workspace_document": ToolSpec(
            name="write_workspace_document",
            description="كتابة أو تعديل وثيقة داخل مساحة العمل مع التراجع التلقائي",
            parameters={"path": "str", "content": "str"},
            consent="logged",
            reversible=True,
        ),
        "execute_isolated_command": ToolSpec(
            name="execute_isolated_command",
            description="تنفيذ أمر نظام داخل المضيف الزائل المعزول بإذن المالك (ق٤٤)",
            parameters={"command": "str"},
            consent="owner",
            reversible=False,
        ),
    }

    handlers = {
        "search_regulations": _search_regulations_handler,
        "analyze_arabic_morphology": _analyze_morphology_handler,
        "evaluate_governance": _evaluate_governance_handler,
        "check_mlx_hardware": _check_mlx_hardware_handler,
        "write_workspace_document": _write_document_handler,
        "execute_isolated_command": _execute_isolated_command_handler,
    }

    return tools, handlers


def create_default_action_loop(ledger: Ledger | None = None) -> GovernedActionLoop:
    """إنشاء حلقة فعل محكومة محملة بالأدوات التشغيلية الافتراضية."""
    tools, handlers = default_tools_registry()
    return GovernedActionLoop(tools=tools, handlers=handlers, ledger=ledger)


def export_agent_tools() -> tuple:
    """تصدير الأدوات التشغيلية لتكون متوافقة مع سجل وكيل ديوان (agent.registry.ToolRegistry)."""
    from agent.registry import Tool
    tools, handlers = default_tools_registry()
    agent_tools = []
    for name, spec in tools.items():
        handler = handlers[name]

        def _make_runner(h):
            def _runner(arguments: dict, context) -> dict:
                out = h(arguments, context)
                result = {"content": out}
                # Keep the structured content compatible while allowing the
                # durable store to bind the actual Journal reversal receipt.
                if isinstance(out, dict) and isinstance(out.get("action_id"), str):
                    result["action_id"] = out["action_id"]
                return result
            return _runner

        agent_tools.append(Tool(spec, _make_runner(handler)))
    return tuple(agent_tools)
