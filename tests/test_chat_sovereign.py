"""اختبارات تكامل التوجيه السيادي والتعقيم الحتمي في أداة الحوار chat.py."""
import json
from io import StringIO
import pytest

from core.contracts import Response, Usage
from tools import chat


class MockEchoProvider:
    is_local = True
    name = "mock_echo"
    model = "mock_echo"

    def __init__(self, *args):
        self.calls = 0
        self.last_request = None

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        self.last_request = request
        last_message = request.messages[-1].content
        # النموذج يعيد تكرار ما وصله تماماً لاختبار التعقيم وعكسه
        return Response(f"تم الاستلام: {last_message}", Usage(10, 10), "complete", 0)


@pytest.fixture
def sovereign_setup(tmp_path, monkeypatch):
    provider = MockEchoProvider()
    monkeypatch.setattr(chat, "CHAT_ROOT", tmp_path / "chat")
    monkeypatch.setattr(chat, "LocalChatProvider", lambda *a: provider)
    base_args = ["--session", "sovereign-session", "--model", "fixture", "--model-version", "a" * 64]
    return base_args, provider


def test_chat_cli_sovereign_local_allowed(sovereign_setup, capsys):
    base_args, provider = sovereign_setup
    cmd = base_args + [
        "--tier", "local_edge",
        "--data-policy", "local_only",
        "ask", "--turn", "t1", "--message", "بيانات محلية سرية"
    ]
    assert chat.main(cmd) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "complete"
    assert res["sovereign_tier"] == "local_edge"
    assert res["data_policy"] == "local_only"
    assert "بيانات محلية سرية" in res["content"]


def test_chat_cli_sovereign_policy_violation_fails_closed(sovereign_setup, capsys):
    base_args, provider = sovereign_setup
    # محاولة إرسال بيانات local_only إلى المستوى الطليعي frontier_zdr يجب أن تُمنع فوراً
    cmd = base_args + [
        "--tier", "frontier_zdr",
        "--data-policy", "local_only",
        "ask", "--turn", "t2", "--message", "بيانات محظور خروجها"
    ]
    assert chat.main(cmd) == 2
    res = json.loads(capsys.readouterr().out)
    assert res["error_code"] == "policy_violation_local_only"
    assert provider.calls == 0  # المزود لم يُستدعَ أصلاً (إخفاق مغلق)


def test_chat_cli_unwired_tier_is_refused_not_run_locally_under_its_label(sovereign_setup, capsys):
    """لا ناقلَ سحابيٌّ موصول (غ٥): كان المستوى الطليعيُّ يُنفَّذ على المزوّد المحليّ ويُوسم frontier_zdr.

    فصار يُرفض بـtier_unavailable قبل أيّ نداء، كما في الواجهة. والتعقيمُ وعكسُه مختبَران في موجّه السياسات
    (tests/test_sovereign_router.py)، وحدودُ المعقِّم معلنةٌ في tests/test_pii_sanitizer_declared_limits.py.
    """
    base_args, provider = sovereign_setup
    for tier in ("frontier_zdr", "sovereign_cloud"):
        cmd = base_args + [
            "--tier", tier,
            "--data-policy", "public",
            "ask", "--turn", "t3-" + tier,
            "--message", "المواطن رقم هويته 1082736451 ورقم هاتفه 0501112233 يطلب المساعدة."
        ]
        assert chat.main(cmd) == 2
        res = json.loads(capsys.readouterr().out)
        assert res["error_code"] == "tier_unavailable"
    assert provider.calls == 0 and provider.last_request is None


def test_chat_cli_interactive_refuses_unwired_tier_too(sovereign_setup, capsys, monkeypatch):
    base_args, provider = sovereign_setup
    lines = iter(["نصٌّ عامّ"])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None
    monkeypatch.setattr("builtins.input", fake_input)
    assert chat.main(base_args + ["--tier", "frontier_zdr", "--data-policy", "public", "chat"]) == 0
    assert "tier_unavailable" in capsys.readouterr().err
    assert provider.calls == 0
