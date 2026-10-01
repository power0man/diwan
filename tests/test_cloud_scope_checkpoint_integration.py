"""Combining cloud creation with Hub checkpoints must not widen disclosure."""
import json

import pytest

from tests.test_cloud_workspace_scope import _app, _create, _session
from tests.test_hf_checkpoints import Remote, NS
from workspace_tools import backup, checkpoints, hf_checkpoints, storage_scope


@pytest.mark.parametrize("mode", [None, "text", "agent"])
@pytest.mark.parametrize("restored", [False, True])
def test_internal_cloud_archive_never_reaches_hub_even_with_public_grants(tmp_path, mode, restored):
    scope = _create(tmp_path)
    app = _app(scope.root, synthetic_cloud=True)
    try:
        if mode is not None:
            _session(app, mode)
    finally:
        app.close()
    archive = tmp_path / "initial.json"
    exported = backup.export_workspace(scope.root, archive)
    if restored:
        destination = tmp_path / "restored"
        backup.restore_workspace(archive, destination, exported["sha256"], tombstones_from=None)
        assert storage_scope.read_storage_scope(destination).raw == scope.raw
        archive = tmp_path / "restored.json"
        exported = backup.export_workspace(destination, archive)
    # Deliberately overbroad grants cannot override the archive's real contract.
    # All bytes here are created by this test; no owner archive or network IO.
    grants = {"archive_sha256": exported["sha256"], "files": {
        row["path"]: {"sha256": row["sha256"], "data_policy": "public"}
        for row in json.loads(archive.read_bytes())["files"]}}
    remote = Remote()
    contacted = []
    original = remote.snapshot
    def snapshot():
        contacted.append(True)
        return original()
    remote.snapshot = snapshot
    store = hf_checkpoints.HubCheckpointStore(remote, NS)
    code = "checkpoint_policy_refused" if mode is None else "checkpoint_session_policy_refused"
    with pytest.raises(checkpoints.CheckpointError, match=code):
        checkpoints.commit_checkpoint(archive, exported["sha256"], grants, store)
    assert contacted == []
    assert remote.commits == 0
