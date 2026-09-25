"""صورةُ الحاوية ومهمّةُ فحصها (ج٦، #36): ما يُثبت هنا بلا Docker، وما يُثبت في CI.

الصورةُ تُبنى من القفل المجمَّد وحده، وبمستخدمٍ غير الجذر، وقاعدةُ الصرف داخلها. والمهمّةُ
تشغّل فحصَ الدخان بلا شبكة وتنتظر الخروج 3 بخطواتٍ مسمّاة. والخطواتُ التي تنتظرها هي التي
يُخرجها `tools/launch_check.py` فعلًا، فلا تنجرف المهمّةُ عن الأداة.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "container-smoke.yml").read_text(encoding="utf-8")
LAUNCH = (ROOT / "tools" / "launch_check.py").read_text(encoding="utf-8")


def test_the_image_installs_from_the_frozen_lock_with_a_pinned_uv():
    assert re.search(r"pip install --no-cache-dir 'uv==\d+\.\d+\.\d+'", DOCKERFILE)
    assert "uv sync --frozen" in DOCKERFILE
    assert "UV_PYTHON_DOWNLOADS=never" in DOCKERFILE


def test_the_image_runs_as_a_non_root_user_with_the_morphology_data_inside():
    assert re.search(r"^USER diwan$", DOCKERFILE, re.M)
    assert "camel_data -i morphology-db-msa-r13" in DOCKERFILE
    assert "CAMELTOOLS_DATA=/opt/camel_tools" in DOCKERFILE


def test_the_smoke_runs_offline_and_expects_the_declared_exit():
    assert "docker run --rm --network none diwan:ci python tools/launch_check.py --json" in WORKFLOW
    assert "int(sys.argv[1]) == 3 == report[\"exit_code\"]" in WORKFLOW
    assert "permissions:\n  contents: read\n" in WORKFLOW


def test_the_expected_steps_are_the_ones_launch_check_emits():
    expected = dict(re.findall(r'"(\w+)": \("(?:ok|unavailable)", (?:"(\w+)"|None)\)', WORKFLOW))
    assert set(expected) == {"runtime", "morphology", "engine", "agent_turn", "policies", "ui"}
    for step, code in expected.items():
        assert f'Step("{step}"' in LAUNCH, step
        if code:
            assert f'"{code}"' in LAUNCH, code
