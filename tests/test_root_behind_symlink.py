from pathlib import Path

import pytest

from workspace_tools.files import WorkspaceError
from webui.server import LocalApp


MODEL_VERSION = "0" * 64


def _app(root: Path) -> LocalApp:
    return LocalApp(root, model="synthetic", model_version=MODEL_VERSION,
                    provider_factory=lambda: None)


def test_local_app_accepts_root_behind_a_symlinked_ancestor(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)

    app = _app(alias / "ui")
    try:
        assert app.root == actual / "ui"
        assert app.root.is_dir()
    finally:
        app.close()


def test_local_app_rejects_a_symlink_inside_the_canonical_root(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    app = _app(alias / "ui")
    try:
        (app.root / "projects").symlink_to(outside, target_is_directory=True)
        with pytest.raises(WorkspaceError, match="unsafe_path"):
            app.dispatch({"action": "create_project", "name": "اختبار"})
        assert not list(outside.iterdir())
    finally:
        app.close()
