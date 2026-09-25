#!/usr/bin/env python3
"""Retired under q49: generated opinions cannot impersonate a human review."""
import sys


def main():
    print("legacy_human_review_retired: use tools/review_automatically.py on frozen outputs; "
          "the historical generator did not call Gemini or perform human review.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
