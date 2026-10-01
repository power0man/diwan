"""A synthetic conversation written and read by two different containers (J6)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from diwan.cli import data_root, runtime_root

sys.path.insert(0, str(runtime_root()))

from core.contracts import Response, Usage
from webui.server import LocalApp


class SyntheticProvider:
    name = "container_smoke"
    is_local = True

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        return Response("جواب محفوظ بين حاويتين", Usage(1, 1), "complete", 0,
                        provider=self.name, model_version="synthetic-v1")


def main(mode: str) -> None:
    state = data_root()
    app = LocalApp(state / "daily-ui", model="synthetic", model_version="synthetic-v1",
                   provider_factory=SyntheticProvider)
    try:
        if mode == "write":
            project = app.dispatch({"action": "create_project", "name": "مشروع دخان"})["id"]
            session = app.dispatch({"action": "create_session", "project": project, "name": "محادثة دخان"})["id"]
            ctx = {"project": project, "session": session}
            answer = app.dispatch({"action": "ask", **ctx, "turn": "a" * 32,
                                   "message": "احفظ جوابًا تجريبيًا", "files": []})
            (state / "smoke-context.json").write_text(json.dumps({**ctx, "content": answer["content"]}), encoding="utf-8")
        elif mode == "read":
            ctx = json.loads((state / "smoke-context.json").read_text(encoding="utf-8"))
            content = ctx.pop("content")
            history = app.dispatch({"action": "history", **ctx, "before": None})
            assert len(history["turns"]) == 1
            assert history["turns"][0]["content"] == content == "جواب محفوظ بين حاويتين"
        else:
            raise ValueError("write_or_read_required")
        print(f"conversation persistence: {mode} ok (synthetic provider, no model call)")
    finally:
        app.close()


if __name__ == "__main__":
    main(sys.argv[1])
