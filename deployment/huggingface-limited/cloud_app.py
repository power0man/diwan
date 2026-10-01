"""Deployment-only session defaults for the slower free CPU host."""
import sys
sys.path.insert(0, "/app")
from functools import partial
import webui.server as server
from tools.serve_ui import main

# Existing persisted sessions supply their own explicit deadline and override
# this default. Both constructors record 300s in their normal manifests; the
# provider enforces its existing 300s cap. No product source is modified.
server.AgentSession = partial(server.AgentSession, deadline_s=300)
server.ChatSession = partial(server.ChatSession, deadline_s=300)
raise SystemExit(main())
