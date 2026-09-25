"""بنكُ الذاكرة المحكومة مجمَّدٌ بعتبته قبل البناء (ك٤٨، #33).

بصمةُ البنك مسجّلةٌ في `docs/MEMORY-DESIGN.md`، فتعديلُه يظهر في المراجعة تعديلًا للتصميم.
والمدقّقُ يردّ البنكَ الذي يغيّر العتبة، أو ينسى بلا فحص بقايا ولا إيصال، أو يقيس العزلَ
بمشروعٍ واحد، أو يقيس الحجرَ بلا طلبه.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from core.canonical import PayloadRejected
from evaluation.memory_bank import THRESHOLDS, validate_memory_bank

ROOT = Path(__file__).resolve().parents[1]
BANK_PATH = ROOT / "evaluation" / "suites" / "memory_v1.json"
BANK = json.loads(BANK_PATH.read_text(encoding="utf-8"))
DESIGN = (ROOT / "docs" / "MEMORY-DESIGN.md").read_text(encoding="utf-8")


def test_the_bank_is_valid_and_covers_every_guarantee():
    validate_memory_bank(BANK)
    counts = Counter(s["category"] for s in BANK["scenarios"])
    assert counts == {"forget": 8, "isolation": 8, "consent": 6, "backup": 4, "injection": 4}


def test_the_bank_is_frozen_by_the_digest_recorded_in_the_design():
    recorded = re.search(r"`memory_v1\.json` sha256: `([0-9a-f]{64})`", DESIGN).group(1)
    assert hashlib.sha256(BANK_PATH.read_bytes()).hexdigest() == recorded


def test_the_thresholds_are_the_pre_registered_ones():
    assert BANK["thresholds"] == THRESHOLDS == {"forget_rate": 1.0, "leakage": 0,
                                                "consent_violations": 0, "injection_unquarantined": 0}
    for name in THRESHOLDS:
        assert f"`{name}`" in DESIGN


def _scenario(scenario_id):
    bank = copy.deepcopy(BANK)
    return bank, next(s for s in bank["scenarios"] if s["id"] == scenario_id)


def _refused(bank, code):
    with pytest.raises(PayloadRejected) as err:
        validate_memory_bank(bank)
    assert err.value.code == code


def test_a_changed_threshold_is_refused():
    bank = copy.deepcopy(BANK)
    bank["thresholds"]["forget_rate"] = 0.95
    _refused(bank, "thresholds_changed")


def test_forgetting_without_a_residue_check_is_refused():
    bank, scenario = _scenario("forget_001")
    scenario["steps"] = [s for s in scenario["steps"] if s.get("expect") != "residue"]
    _refused(bank, "residue_unchecked")


def test_forgetting_without_a_receipt_check_is_refused():
    bank, scenario = _scenario("forget_001")
    scenario["steps"] = [s for s in scenario["steps"] if s.get("expect") != "receipt"]
    _refused(bank, "receipt_unchecked")


def test_isolation_measured_on_one_project_is_refused():
    bank, scenario = _scenario("isolation_001")
    for step in scenario["steps"]:
        step["project"] = "A"
    _refused(bank, "isolation_single_project")


def test_an_injection_scenario_must_ask_for_quarantine():
    bank, scenario = _scenario("injection_001")
    for step in scenario["steps"]:
        step.pop("quarantined", None)
    _refused(bank, "quarantine_unchecked")


def test_a_reference_is_defined_before_use_in_its_own_project():
    bank, scenario = _scenario("forget_001")
    scenario["steps"][1]["project"] = "B"
    _refused(bank, "ref_unknown")
