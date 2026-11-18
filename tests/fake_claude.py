#!/usr/bin/env python3
"""Stands in for `claude` in the tests: says how it was started, keeps a transcript where the real one would, and
echoes what is typed into it."""

import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
prompt = args[args.index("--") + 1] if "--" in args else ""
session = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
transcript = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-fake" / f"{session}.jsonl"
if "--resume" in args and not transcript.exists():
    sys.exit("No conversation found with that session ID")
print(json.dumps({"mode": "resume" if "--resume" in args else "new", "session": session, "model": args[args.index("--model") + 1],
                  "cwd": os.getcwd(), "mcp": Path(args[args.index("--mcp-config") + 1]).exists(), "prompt": prompt}), flush=True)  # fmt: skip
if prompt:
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps({"prompt": prompt}) + "\n")
for line in sys.stdin:
    print(f"heard: {line.strip()}", flush=True)
    if line.strip().endswith("quit"):
        break
