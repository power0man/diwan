"""فحوص تكامل الأدوات السيادية عبر واجهة الويب HTTP (Track B).

التحقق من:
1. استعراض قائمة الأدوات السيادية المسجلة ومواصفاتها.
2. استرجاع الأنظمة واللوائح الرسمية والتحقق من بارامتر الحد والفرز.
3. التحليل الصرفي واشتقاق الجذور والأوزان عبر الواجهة.
4. فرض الحدود الأمنية: CSRF، نوع المحتوى، الحظر عند المدخلات الباطلة.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from acceptance_m9 import SYNTHETIC_MODEL, SYNTHETIC_VERSION
from tests.test_webui_http import Running
from tests.private_stores import needs_corpus


@pytest.fixture
def running_server(tmp_path: Path):
    runner = Running(tmp_path)
    try:
        yield runner
    finally:
        runner.close()


def test_sovereign_tools_listing(running_server: Running):
    res = running_server.api("sovereign_tools")
    assert res["status"] == "ok"
    assert "tools" in res
    tools = {t["name"]: t for t in res["tools"]}
    assert "search_regulations" in tools
    assert "analyze_arabic_morphology" in tools
    assert "write_workspace_document" in tools
    assert "execute_isolated_command" in tools

    assert tools["search_regulations"]["consent"] == "auto"
    assert tools["execute_isolated_command"]["consent"] == "owner"
    assert tools["write_workspace_document"]["reversible"] is True


@needs_corpus
def test_search_regulations_via_webui(running_server: Running):
    # بحث بحد مخصص
    res = running_server.api("search_regulations", query="الصابورة", limit=2)
    assert res["status"] == "ok"
    assert res["query"] == "الصابورة"
    assert isinstance(res["results"], list)
    assert 1 <= len(res["results"]) <= 2
    first = res["results"][0]
    assert "doc_id" in first
    assert "part" in first
    assert "locus" in first

    # بحث بالحد الافتراضي
    res_default = running_server.api("search_regulations", query="الموانئ")
    assert res_default["status"] == "ok"
    assert isinstance(res_default["results"], list)


def test_search_regulations_validation(running_server: Running):
    # استعلام فارغ
    status, payload, _ = running_server.request({"action": "search_regulations", "query": ""})
    assert status == 409
    assert payload.get("error_code") == "query_empty"

    # استعلام مسافات فقط
    status, payload, _ = running_server.request({"action": "search_regulations", "query": "   "})
    assert status == 409
    assert payload.get("error_code") == "query_empty"

    # حد غير صالح
    status, payload, _ = running_server.request({"action": "search_regulations", "query": "بحر", "limit": 0})
    assert status == 409
    assert payload.get("error_code") == "limit_invalid"


def test_analyze_morphology_via_webui(running_server: Running):
    res = running_server.api("analyze_morphology", word="المستكشفون")
    assert res["status"] == "ok"
    analysis = res["analysis"]
    assert analysis["root"] == "كشف"
    assert analysis["pattern"] == "مستفعل"
    assert "ال" in analysis["prefixes"]
    assert "ون" in analysis["suffixes"]

    # الاسم البديل
    res_alt = running_server.api("analyze_arabic_morphology", word="المكتوبة")
    assert res_alt["status"] == "ok"
    assert res_alt["analysis"]["root"] == "كتب"
    assert res_alt["analysis"]["pattern"] == "مفعول"


def test_analyze_morphology_validation(running_server: Running):
    status, payload, _ = running_server.request({"action": "analyze_morphology", "word": ""})
    assert status == 409
    assert payload.get("error_code") == "word_invalid"

    # حقول غير متوقعة ترفض الطلب
    status, payload, _ = running_server.request({"action": "analyze_morphology", "word": "كتب", "extra": 123})
    assert status == 409
    assert payload.get("error_code") == "request_invalid"


def test_sovereign_tools_security_csrf(running_server: Running):
    headers = running_server.headers()
    headers["X-Diwan-CSRF"] = "invalid_token_000000000000000000000000"
    status, payload, _ = running_server.request({"action": "sovereign_tools"}, headers=headers)
    assert status == 403
    assert payload.get("error_code") == "http_refused"


def test_mlx_status_via_webui(running_server: Running):
    res = running_server.api("mlx_status")
    assert res["status"] == "ok"
    assert "metal_available" in res
    assert res["is_local"] is True
    assert res["cost_micros"] == 0


def test_evaluate_governance_via_webui(running_server: Running):
    pages = {
        "1": {
            "text": "تفرض غرامة مالية قدرها 200 ريال عند التأخير، مع رسوم 50 ريالاً للفحص الفني، ويجب معاينة السفينة قبل الإبحار.",
            "part": "لائحة السلامة",
            "locus": "المادة 5",
        }
    }
    answer = "### تفاصيل الرسوم [ش1]. يجب تفتيش الباخرة قبل الإبحار [ش1]. ويبلغ إجمالي المستحقات 250 ريالاً [ش1]."
    res = running_server.api("evaluate_governance", answer=answer, pages=pages)
    assert res["status"] == "ok"
    assert res["all_passed"] is True
    assert res["average_semantic_bp"] >= 6000


def test_webui_mlx_provider_app(tmp_path):
    from webui.server import LocalApp
    from providers.mlx_provider import MLXProvider
    from core.contracts import Response, Usage

    class MockMLX(MLXProvider):
        def complete(self, request):
            return Response(
                content="أهلاً بك في ديوان المدعوم بمحرك MLX",
                usage=Usage(input_tokens=10, output_tokens=12),
                stop_reason="complete",
                cost_micros=0,
                provider="mlx:test",
                model_version="mlx-local-v1",
            )

    app = LocalApp(
        tmp_path / "ui_mlx",
        model="mlx-community/Qwen2.5-7B-Instruct-4bit",
        model_version="mlx-local-v1",
        provider_factory=lambda: MockMLX("mlx-community/Qwen2.5-7B-Instruct-4bit"),
    )
    try:
        import uuid
        p_res = app.dispatch({"action": "create_project", "name": "مشروع تجربة MLX"})
        pid = p_res["id"]
        c_res = app.dispatch({"action": "create_session", "project": pid, "name": "جلسة MLX"})
        sid = c_res["id"]
        tid = uuid.uuid4().hex
        t_res = app.dispatch({"action": "ask", "project": pid, "session": sid, "turn": tid, "message": "مرحباً", "files": []})
        assert t_res["status"] == "complete"
        assert "MLX" in t_res["content"]
    finally:
        app.close()

