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
    # uv بإصدارٍ محدّد وببصمات عجلاته من ملفّ متطلّباتٍ يُفحص بـ--require-hashes (#285)
    assert re.search(r"'uv==\d+\.\d+\.\d+ \\", DOCKERFILE)
    assert "pip install --no-cache-dir --require-hashes -r /tmp/uv-requirements.txt" in DOCKERFILE
    assert "uv sync --frozen" in DOCKERFILE
    assert "UV_PYTHON_DOWNLOADS=never" in DOCKERFILE


def test_the_image_runs_as_a_non_root_user_with_the_morphology_data_inside():
    assert re.search(r"^USER diwan$", DOCKERFILE, re.M)
    assert "camel_data -i morphology-db-msa-r13" in DOCKERFILE
    assert "CAMELTOOLS_DATA=/opt/camel_tools" in DOCKERFILE


def test_the_smoke_runs_offline_and_expects_the_declared_exit():
    assert "docker run --rm --network none diwan:ci diwan check --json" in WORKFLOW
    assert "int(sys.argv[1]) == 3 == report[\"exit_code\"]" in WORKFLOW
    assert "permissions:\n  contents: read\n" in WORKFLOW


def test_the_expected_steps_are_the_ones_launch_check_emits():
    expected = dict(re.findall(r'"(\w+)": \("(?:ok|unavailable)", (?:"(\w+)"|None)\)', WORKFLOW))
    assert set(expected) == {"runtime", "morphology", "engine", "agent_turn", "policies", "ui"}
    for step, code in expected.items():
        assert f'Step("{step}"' in LAUNCH, step
        if code:
            assert f'"{code}"' in LAUNCH, code


def test_container_state_is_a_volume_and_desktop_does_not_require_host_networking():
    assert re.search(r'^VOLUME \["/app/var"\]$', DOCKERFILE, re.M)
    assert "DIWAN_DATA_HOME=/app/var" in DOCKERFILE
    assert "--network host" not in DOCKERFILE
    assert 'CMD ["diwan", "check"]' in DOCKERFILE


def test_ci_always_runs_python_tests_and_checks_replacement_and_tool_install():
    assert "paths:" not in WORKFLOW  # a required check cannot be path-filtered
    assert "python -m pytest" in WORKFLOW
    assert "fetch-depth: 0" in WORKFLOW
    assert "ci/container_state.py write" in WORKFLOW
    assert "ci/container_state.py read" in WORKFLOW
    assert "uv tool install" in WORKFLOW


def test_restored_private_stores_are_excluded_from_the_build_context():
    from tools.export_public import EXCLUDED_PATHS
    ignored = set((ROOT / ".dockerignore").read_text().splitlines())
    assert set(EXCLUDED_PATHS) <= ignored
    assert {"keys/*", "!keys/anchor-ed25519.pub", "!keys/anchor-policy.json", "var/", ".env"} <= ignored


def test_local_worker_secrets_and_build_artifacts_are_excluded_from_the_build_context():
    """`.dev.vars` ملفُّ أسرار wrangler المحليّ (مثل HF_TOKEN)، والـgitignore لا يُخرجه من سياق البناء، و`COPY . .` ينسخ
    السياقَ كلَّه إلى طبقةٍ في الصورة (تدقيقٌ لاحقٌ لـ9441c44)."""
    ignored = set((ROOT / ".dockerignore").read_text().splitlines())
    local = set((ROOT / "deployment/cloudflare-hf/.gitignore").read_text().splitlines())
    assert {".dev.vars", ".wrangler/", "node_modules/"} <= local
    assert {"**/.dev.vars", "**/.dev.vars.*", "**/.wrangler/", "**/node_modules/"} <= ignored


def test_the_offline_smoke_expects_the_ui_step_without_an_engine():
    """خطوةُ الواجهة تشغّل serve_ui.py فعلًا ببصمة المحرّك (#175)، والحاويةُ تُشغَّل بلا شبكة فلا محرّك: الخطوةُ تُسمّي بصمتَها
    البديلة `ui_ready_without_engine`. فانتظارُ `ui_ready` هنا يُسقط الفحصَ على عطبٍ لا وجود له، وانتظارُ غيرِ «ok» يُخفي عطبًا."""
    assert '"engine": ("unavailable", "engine_unreachable")' in WORKFLOW
    assert '"ui": ("ok", "ui_ready_without_engine")' in WORKFLOW
