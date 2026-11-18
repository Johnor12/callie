#!/usr/bin/env python3
"""Stands in for `chesstutor/allie_worker.py` in the tests: the same lines in and out, no model.

It plays the first legal move (in UCI order) after the think time its "model" directory asks for (fake.json),
predicts every legal move equally likely, and writes down what it was asked (requests.jsonl). From the plies
fake.json names it resigns instead of moving (resign_at), offers a draw with its move (offer_at), or resigns
after its own move (concede_at).
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chesstutor.chess import START_FEN, Game  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True)
ap.add_argument("--threads", type=int, default=0)
home = Path(ap.parse_args().model)


def setting(name, default):
    try:
        return json.loads((home / "fake.json").read_text()).get(name, default)
    except (OSError, ValueError):
        return default


def answer(r: dict) -> dict:
    if r["op"] in ("ping", "forget"):
        return {}
    since = lambda name: len(r["moves"]) >= setting(name, 10**9)  # noqa: E731
    if r["op"] == "concede":
        return {"resign": since("concede_at")}
    legal = sorted(Game(START_FEN, r["moves"]).position.legal_moves())
    if r["op"] == "decide":
        time.sleep(setting("compute", 0.0))
        return {"move": legal[0], "think": setting("think", 0.0), "wdl": [0.4, 0.2, 0.4], "probability": 1 / len(legal),
                "sims": 8, "searched": 0.0, "took": 0.0, "fallback": False, "resign": since("resign_at"),
                "offer_draw": since("offer_at")}  # fmt: skip
    moves, line = list(r["moves"]), []
    for _ in range(r.get("line") or 0):
        here = sorted(Game(START_FEN, moves).position.legal_moves())
        if not here:
            break
        line.append([here[0], 1 / len(here)])
        moves.append(here[0])
    return {"moves": [[m, 1 / len(legal)] for m in legal[: r.get("top") or 5]], "legal": len(legal), "wdl": [0.5, 0.1, 0.4],
            "think_time": 7.0, "line": line}  # fmt: skip


time.sleep(setting("load", 0.0))
print(json.dumps({"ready": True, "name": "fake-allie", "threads": 1, "load_s": 0.0}), flush=True)
for raw in sys.stdin:
    r = json.loads(raw)
    if r.get("op") == "quit":
        break
    with open(home / "requests.jsonl", "a") as f:
        f.write(raw)
    try:
        print(json.dumps({"id": r["id"], "ok": True, **answer(r)}), flush=True)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"id": r["id"], "error": f"{type(e).__name__}: {e}"}), flush=True)
