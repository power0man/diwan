"""اختبارات تكامل التوجيه السيادي والتعقيم في خادم واجهة الويب webui/server.py."""
import json
import pytest

from acceptance_m9 import SyntheticProvider, _answer, SYNTHETIC_MODEL, SYNTHETIC_VERSION
from webui.server import LocalApp


class EchoSyntheticProvider(SyntheticProvider):
    def __init__(self):
        super().__init__([])
        self.calls = 0
        self.last_request = None

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        self.last_request = request
        last_msg = request.messages[-1].content
        return _answer(f"استجابة النموذج: {last_msg}")


@pytest.fixture
def app_sovereign(tmp_path):
    provider = EchoSyntheticProvider()
    app = LocalApp(
        tmp_path / "app",
        model=SYNTHETIC_MODEL,
        model_version=SYNTHETIC_VERSION,
        provider_factory=lambda: provider,
    )
    yield app, provider
    app.close()


def test_webui_sovereign_status_action(app_sovereign):
    app, _ = app_sovereign
    res = app.dispatch({"action": "sovereign_status"})
    assert "available_tiers" in res
    assert len(res["available_tiers"]) == 1
    tiers = [t["id"] for t in res["available_tiers"]]
    assert "local_edge" in tiers
    assert "sovereign_cloud" not in tiers
    assert "frontier_zdr" not in tiers
    assert "local_only" in res["data_policies"]


def test_webui_sovereign_local_ask_success(app_sovereign):
    import uuid
    app, provider = app_sovereign
    proj = app.dispatch({"action": "create_project", "name": "مشروع سيادي"})["id"]
    sess = app.dispatch({"action": "create_session", "project": proj, "name": "جلسة محلية"})["id"]
    turn_id = uuid.uuid4().hex

    res = app.dispatch({
        "action": "ask",
        "project": proj,
        "session": sess,
        "turn": turn_id,
        "message": "طلب سري محلي",
        "files": [],
        "tier": "local_edge",
        "data_policy": "local_only",
    })
    assert res["status"] == "complete"
    assert res["sovereign_tier"] == "local_edge"
    assert res["data_policy"] == "local_only"
    assert res["sanitized_count"] == 0
    assert provider.calls == 1


def test_webui_sovereign_policy_violation_rejected(app_sovereign):
    import uuid
    app, provider = app_sovereign
    proj = app.dispatch({"action": "create_project", "name": "مشروع سيادي"})["id"]
    sess = app.dispatch({"action": "create_session", "project": proj, "name": "جلسة محظورة"})["id"]
    turn_id = uuid.uuid4().hex

    from webui.server import UIError
    with pytest.raises(UIError) as exc_info:
        app.dispatch({
            "action": "ask",
            "project": proj,
            "session": sess,
            "turn": turn_id,
            "message": "بيانات محظورة الخروج",
            "files": [],
            "tier": "frontier_zdr",
            "data_policy": "local_only",
        })
    assert exc_info.value.code == "tier_unavailable"
    assert provider.calls == 0  # حظر فوري بلا استدعاء للمزود


def test_webui_unwired_frontier_tier_is_not_a_claimed_local_execution(app_sovereign):
    import uuid
    app, provider = app_sovereign
    proj = app.dispatch({"action": "create_project", "name": "مشروع تعقيم"})["id"]
    sess = app.dispatch({"action": "create_session", "project": proj, "name": "جلسة طليعية"})["id"]
    turn_id = uuid.uuid4().hex

    from webui.server import UIError
    with pytest.raises(UIError, match="tier_unavailable"):
        app.dispatch({
        "action": "ask",
        "project": proj,
        "session": sess,
        "turn": turn_id,
        "message": "المراجع هويته 1092837465 وهاتفه 0509998877 يطلب الخدمة.",
        "files": [],
        "tier": "frontier_zdr",
        "data_policy": "public",
    })
    assert provider.calls == 0 and provider.last_request is None
