"""اختبارات تشخيص CI: «معلَّقٌ صامت» يصير رمزًا مسمًّى.

بتصميم ق٣٦ لا مُشغِّل مقيمًا، فالوصلة تنتظر حاويةً يبدأها المالك —
وكان الانتظار بلا أي إشارة: لا رسالة ولا رمز ولا موضع يُسأل.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ci"))

import ci_status


VALID_ID = "sha256:" + "a" * 64


@pytest.fixture
def receipt(tmp_path):
    p = tmp_path / "runner.json"
    p.write_text(json.dumps({"image_id": VALID_ID}), encoding="utf-8")
    return p


def _patch(monkeypatch, *, docker=(True, "28.0"), image=(True, "ok"),
           labels=("diwan-isolated",), waiting=0):
    monkeypatch.setattr(ci_status, "docker_ok", lambda: docker)
    monkeypatch.setattr(ci_status, "image_present", lambda _r: image)
    monkeypatch.setattr(ci_status, "github_state", lambda _repo: (
        {"labels": list(labels),
         "waiting": [{"status": "queued"}] * waiting}, ""))


def test_docker_down_is_named(monkeypatch, receipt):
    _patch(monkeypatch, docker=(False, "لا محرك"))
    assert ci_status.diagnose(receipt, "r")["code"] == "docker_unavailable"


def test_missing_image_is_named_before_github(monkeypatch, receipt):
    """الحالة التي عطّلت CI: إيصالٌ يسمّي صورةً محذوفة."""
    _patch(monkeypatch, image=(False, "غير موجودة"), waiting=1)
    r = ci_status.diagnose(receipt, "r")
    assert r["code"] == "runner_image_missing" and "مالك" in r["remedy"]


def test_waiting_without_runner_is_named(monkeypatch, receipt):
    _patch(monkeypatch, labels=(), waiting=1)
    r = ci_status.diagnose(receipt, "r")
    assert r["code"] == "no_runner_registered" and r["waiting"] == 1


def test_waiting_with_runner_is_not_a_fault(monkeypatch, receipt):
    _patch(monkeypatch, waiting=2)
    assert ci_status.diagnose(receipt, "r")["code"] == "queued_waiting"


def test_idle_is_clean(monkeypatch, receipt):
    _patch(monkeypatch)
    assert ci_status.diagnose(receipt, "r")["code"] == "idle"


def test_untagged_image_counts_as_present(tmp_path, monkeypatch):
    """الصورة بلا وسم موجودةٌ فعلًا — الفحص بالمعرّف لا بالقائمة
    الموسومة، وإلا شُخِّص عطلٌ لا وجود له."""
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"image_id": VALID_ID}), encoding="utf-8")
    monkeypatch.setattr(ci_status, "_run",
                        lambda cmd, timeout=30: (0, VALID_ID))
    ok, _ = ci_status.image_present(p)
    assert ok


def test_invalid_receipt_refused_by_name(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"image_id": "not-a-digest"}', encoding="utf-8")
    ok, detail = ci_status.image_present(p)
    assert not ok and "قانوني" in detail


def test_missing_receipt_refused_by_name(tmp_path):
    ok, detail = ci_status.image_present(tmp_path / "absent.json")
    assert not ok and "غائب" in detail
