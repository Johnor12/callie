"""How the opponent picks a move at a given strength.

A strength is a rating on Allie's scale (Lichess's), the one scale the app speaks, so that Stockfish at 1200 and
Allie at 1200 are the same opponent to beat and your own rating can be reckoned from games against either.
Stockfish's own scale is another: `SCALE` reads one in the other, measured by `scripts/calibrate.py` in games
between Stockfish and Allie.

On its own scale, from 1320 up, Stockfish limits itself (`UCI_Elo`: its own calibration, anchored to the CCRL
40/4 rating list). It has no setting below 1320, so weaker opponents are made here: every legal move is scored by
a shallow search and one is drawn with odds that fall off with how much worse than the best it is. How fast they
fall off (the "temperature", in centipawns) sets the strength, and `LEVELS` says which temperature plays at which
rating of Stockfish's scale: measured in games against Stockfish at 1320, 1500 and 1700 and between neighbours.
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
LEVELS = sorted((int(e), float(t)) for e, t in _DATA["levels"])  # (Stockfish's rating, temperature), weakest first
# (Stockfish's rating, the rating on Allie's scale that plays level with it); empty: not measured, the same number
SCALE = sorted((float(s), float(r)) for s, r in _DATA.get("scale") or [])
ELO_MIN = int(_DATA.get("min", LEVELS[0][0]))  # the weakest opponent offered, and the strongest
ELO_MAX = int(_DATA.get("max", SF_MAX))


def _along(points: list[tuple[float, float]], x: float) -> float:
    """The line through ``points`` at ``x``; past either end, its last stretch carried on."""
    if len(points) < 2:
        return x
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1 or (x1, y1) == points[-1]:
            return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return x


def rated(own: float) -> float:
    """The rating (Allie's scale) of Stockfish playing at ``own`` on its own."""
    return _along(SCALE, own)


def own(elo: float) -> float:
    """Where on Stockfish's own scale it plays as a player rated ``elo`` does."""
    return _along([(r, s) for s, r in SCALE], elo)


def temperature(own_elo: float) -> float:
    """The temperature that plays at ``own_elo`` of Stockfish's scale: between two measured levels, a straight
    line in log-temperature."""
    if own_elo <= LEVELS[0][0]:
        return LEVELS[0][1]
    for (e0, t0), (e1, t1) in zip(LEVELS, LEVELS[1:]):
        if own_elo <= e1:
            w = (own_elo - e0) / (e1 - e0)
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
    if elo is None:
        return engine.search(fen, moves, ms=ms).best
    at = own(elo)
    if at >= SF_MIN:
        return engine.search(fen, moves, ms=ms, elo=min(SF_MAX, round(at))).best
    return sample(engine, fen, moves, temperature(at), rng or random.Random())
