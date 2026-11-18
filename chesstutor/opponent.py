"""How the opponent picks a move at a given strength.

From 1320 up, Stockfish limits itself (`UCI_Elo`: its own calibration, anchored to the CCRL 40/4 rating list).
It has no setting below 1320, so weaker opponents are made here: every legal move is scored by a shallow search
and one is drawn with odds that fall off with how much worse than the best it is. How fast they fall off (the
"temperature", in centipawns) sets the strength, and `LEVELS` says which temperature plays at which rating:
measured by `scripts/calibrate.py` in games against Stockfish at 1320, 1500 and 1700 and between neighbours.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from .engine import Engine

SF_MIN, SF_MAX = 1320, 3190  # what Stockfish's own UCI_Elo takes
WEAK_DEPTH = 6  # how deep each move is looked at before the draw
_LEVELS_FILE = Path(__file__).with_name("levels.json")


_DATA = json.loads(_LEVELS_FILE.read_text())
LEVELS = sorted((int(e), float(t)) for e, t in _DATA["levels"])  # (rating, temperature), weakest first
ELO_MIN = int(_DATA.get("min", LEVELS[0][0]))  # the weakest opponent offered


def temperature(elo: int) -> float:
    """The temperature that plays at ``elo``: between two measured levels, a straight line in log-temperature."""
    if elo <= LEVELS[0][0]:
        return LEVELS[0][1]
    for (e0, t0), (e1, t1) in zip(LEVELS, LEVELS[1:]):
        if elo <= e1:
            w = (elo - e0) / (e1 - e0)
            return math.exp(math.log(t0) + w * (math.log(t1) - math.log(t0)))
    return LEVELS[-1][1]


def sample(engine: Engine, fen: str, moves: list[str], temp: float, rng: random.Random) -> str | None:
    """Score every legal move, then draw one: a move ``temp`` centipawns worse than the best is 1/e as likely."""
    res = engine.search(fen, moves, depth=WEAK_DEPTH, multipv=256)
    if not res.lines:
        return res.best
    black = (fen.split()[1] == "b") != (len(moves) % 2 == 1)
    scored = [(ln.moves[0], -ln.value if black else ln.value) for ln in res.lines if ln.moves]
    top = max(v for _, v in scored)
    weights = [math.exp(max(-60.0, (v - top) / temp)) for _, v in scored]
    return rng.choices([m for m, _ in scored], weights)[0]


def pick(engine: Engine, fen: str, moves: list[str], elo: int | None, ms: int, rng: random.Random | None = None) -> str | None:
    """The opponent's move at ``elo`` (None: full strength)."""
    if elo is None or elo > SF_MAX:
        return engine.search(fen, moves, ms=ms).best
    if elo >= SF_MIN:
        return engine.search(fen, moves, ms=ms, elo=elo).best
    return sample(engine, fen, moves, temperature(elo), rng or random.Random())
