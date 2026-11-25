#!/usr/bin/env python3
"""Give the positions of src/callie/heuristics.json the engine's lines: what their cards are answered by.

A heuristic is added by hand: its key and title, and for each position its "fen", the moves that led to it
("moves", from the start of a game) or the last of them ("last", UCI), if there were any, the "answer" (SAN) and
"why". This asks Stockfish, at full strength, for its three best lines in every position that has none yet,
writes them into the file, and says of each position whether its answer is one the lines make right (the first,
or another within a few centipawns): a position where it is not shows nothing, and the app refuses the file.

A position where more than three moves are as good is given more lines (`--lines`), so that each of them is a
right answer; searched again, it is given as many as it has.

    ./scripts/heuristics.py                # the positions without lines, five seconds each
    ./scripts/heuristics.py --all          # every position again
    ./scripts/heuristics.py --seconds 20
    ./scripts/heuristics.py --lines 5      # five lines for the positions without any
    ./scripts/heuristics.py --file src/callie/openers.json    # the Openers deck's file, which is written the same way
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callie import heuristics  # noqa: E402
from callie.cards import right_moves  # noqa: E402
from callie.chess import Position  # noqa: E402
from callie.config import load_config  # noqa: E402
from callie.engine import Engine  # noqa: E402

PLIES = 10  # how much of a line is kept, as for a game's positions


def dump(hs: list[dict]) -> str:
    """The file as it is kept: one thing to a line, an engine line to a line."""
    out = []
    for h in hs:
        positions = []
        for p in h["positions"]:
            fields = [
                f"      {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in p.items() if k != "lines"
            ]
            if "lines" in p:
                lines = ",\n".join(f"        {json.dumps(ln)}" for ln in p["lines"])
                fields.append(f'      "lines": [\n{lines}\n      ]')
            positions.append("    {\n" + ",\n".join(fields) + "\n    }")
        head = [f"  {json.dumps(k)}: {json.dumps(h[k], ensure_ascii=False)}" for k in h if k != "positions"]
        out.append("{\n" + ",\n".join([*head, '  "positions": [\n' + ",\n".join(positions) + "\n  ]"]) + "\n}")
    return "[\n" + ",\n".join(out) + "\n]\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true", help="every position, not only those without lines")
    ap.add_argument("--seconds", type=float, default=5.0, help="how long each position is searched")
    ap.add_argument("--lines", type=int, default=3, help="how many lines a position is given")
    ap.add_argument("--file", type=Path, default=heuristics.FILE)
    args = ap.parse_args()
    cfg = load_config()
    engine = Engine(str(cfg.engine), threads=cfg.analysis_threads, hash_mb=cfg.hash_mb)
    hs, wrong = json.loads(args.file.read_text()), 0
    try:
        for h in hs:
            for p in h["positions"]:
                pos = Position.from_fen(p["fen"])
                if args.all or not p.get("lines"):
                    found, first = [], set()
                    many = max(args.lines, len(p.get("lines") or []))
                    for ln in engine.search(p["fen"], ms=int(args.seconds * 1000), multipv=many).lines:
                        if ln.moves and ln.moves[0] not in first:  # a line is now and then given twice
                            first.add(ln.moves[0])
                            found.append(ln)
                    p["lines"] = [
                        {
                            "moves": " ".join(ln.moves[:PLIES]),
                            **({"cp": ln.cp} if ln.mate is None else {"mate": ln.mate}),
                        }
                        for ln in found
                    ]
                    args.file.write_text(dump(hs))
                lines = [heuristics.line(p["fen"], **ln) for ln in p["lines"]]
                right = [pos.san(m) for m in right_moves(lines, pos.turn == "w", "")]
                ok = p.get("answer") in right
                wrong += not ok
                print(f"{'ok ' if ok else 'NOT'} {h['key']}: {p.get('answer')} in {p['fen']}"
                      + ("" if ok else " (the lines: " + ", ".join(f"{ln['san'][0]} {ln['value']}" for ln in lines) + ")"))  # fmt: skip
    finally:
        engine.close()
    args.file.write_text(dump(hs))
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
