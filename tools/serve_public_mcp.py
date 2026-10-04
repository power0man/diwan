#!/usr/bin/env python3
"""Explicit local stdio MCP for a closed public snapshot, never owner workspaces."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conversation.mcp_public import PublicMCPBridge, mcp_server


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--public-root", required=True)
    p.add_argument("--state-root", required=True)
    p.add_argument("--manifest", required=True)
    args = p.parse_args(argv)
    try:
        manifest = json.loads(Path(args.manifest).read_text())
        bridge = PublicMCPBridge(args.public_root, args.state_root, manifest)
        mcp_server(bridge).run(transport="stdio")
    except Exception:
        print("public_mcp_bootstrap_refused", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
