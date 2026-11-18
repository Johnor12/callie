"""Measure what rating each temperature of the sub-1320 opponent plays at, and write chesstutor/levels.json.

Players are `sf:<elo>` (Stockfish limiting itself: the fixed points of the scale) and `t:<temperature>` (the move
sampler of chesstutor/opponent.py). Matches are played between players expected to be within a few hundred points
of each other, then one rating per temperature is fitted to all the results at once (maximum likelihood under the
Elo model, with the `sf:` players held at their nominal ratings).

    ./scripts/calibrate.py play --games 200          # appends results to calibration.jsonl
    ./scripts/calibrate.py fit                        # prints the ratings, writes levels.json
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chesstutor import opponent  # noqa: E402
from chesstutor.chess import START_FEN, Game  # noqa: E402
from chesstutor.config import load_config  # noqa: E402
from chesstutor.engine import Engine  # noqa: E402

RESULTS = Path(__file__).with_name("calibration.jsonl")
MAX_PLIES = 400
FLOOR = 400  # no opponent is offered below this rating
ANCHOR_MS = 30  # Stockfish picks its weakened move at a fixed shallow depth, so thinking longer changes nothing

# Temperatures, strongest first, and the anchors each is close enough to for a match to say something.
TEMPS = [8, 16, 30, 50, 80, 120, 180, 270, 400, 650, 1100, 2500]

_engines: dict[str, Engine] = {}


def _engine(path: str) -> Engine:
    if "e" not in _engines:
        _engines["e"] = Engine(path, threads=1, hash_mb=16)
    return _engines["e"]


def _move(player: str, engine: Engine, moves: list[str], rng: random.Random) -> str | None:
    kind, _, value = player.partition(":")
    if kind == "sf":
        return engine.search(START_FEN, moves, ms=ANCHOR_MS, elo=int(value)).best
    return opponent.sample(engine, START_FEN, moves, float(value), rng)


def play(args) -> tuple[str, str, float, int]:
    """One game; the score is White's."""
    white, black, seed, path = args
    rng, engine, game = random.Random(seed), _engine(path), Game()
    while game.outcome() is None and len(game.moves) < MAX_PLIES:
        game.push(_move(white if len(game.moves) % 2 == 0 else black, engine, game.moves, rng))
    result = (game.outcome() or ("1/2-1/2", "too long"))[0]
    return white, black, {"1-0": 1.0, "0-1": 0.0}.get(result, 0.5), len(game.moves)


def pairings(temps: list[int], anchors: list[int], reach: int) -> list[tuple[str, str]]:
    names = [f"t:{t}" for t in temps]
    pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1 : i + 1 + reach]]
    # Which temperatures an anchor is matched with is decided from what is already known (or all, the first time).
    known = fit(load(), quiet=True) if RESULTS.exists() else {}
    for elo in anchors:
        for n in names:
            if n not in known or abs(known[n] - elo) < 450:
                pairs.append((n, f"sf:{elo}"))
    return pairs


def load() -> list[dict]:
    return [json.loads(line) for line in RESULTS.read_text().splitlines() if line.strip()]


def fit(rows: list[dict], quiet: bool = False) -> dict[str, float]:
    """Ratings that make the results most likely. `sf:` players stay where their name says."""
    totals: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        a, b = sorted((r["a"], r["b"]))
        score = r["score"] if a == r["a"] else r["games"] - r["score"]
        t = totals.setdefault((a, b), [0.0, 0.0])
        t[0] += score
        t[1] += r["games"]
    players = sorted({p for pair in totals for p in pair})
    rating = {p: float(p[3:]) if p.startswith("sf:") else 1000.0 for p in players}
    free = [p for p in players if not p.startswith("sf:")]
    for _ in range(4000):
        for p in free:
            # Newton's step on the log-likelihood of p's games.
            grad = hess = 0.0
            for (a, b), (score, games) in totals.items():
                if p not in (a, b):
                    continue
                other = b if p == a else a
                got = score if p == a else games - score
                e = 1 / (1 + 10 ** ((rating[other] - rating[p]) / 400))
                grad += got - games * e
                hess += games * e * (1 - e)
            if hess > 1e-9:
                rating[p] += max(-50.0, min(50.0, grad / hess * 400 / math.log(10)))
    if not quiet:
        for (a, b), (score, games) in sorted(totals.items()):
            e = 1 / (1 + 10 ** ((rating[b] - rating[a]) / 400))
            print(f"  {a:>8} vs {b:<8} {score:6.1f}/{games:<5.0f} = {score / games:5.1%}   (fitted ratings predict {e:5.1%})")
    return rating


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("play")
    p.add_argument("--games", type=int, default=100, help="per pairing")
    p.add_argument("--temps", type=str, default=",".join(map(str, TEMPS)))
    p.add_argument("--anchors", type=str, default="1320,1500,1700")
    p.add_argument("--reach", type=int, default=2, help="how many weaker neighbours each temperature also plays")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    sub.add_parser("fit")
    a = ap.parse_args()
    if a.cmd == "play":
        path = str(load_config().engine)
        pairs = pairings([int(t) for t in a.temps.split(",")], [int(e) for e in a.anchors.split(",")], a.reach)
        jobs = []
        for x, y in pairs:
            for g in range(a.games):
                white, black = (x, y) if g % 2 == 0 else (y, x)
                jobs.append((white, black, random.getrandbits(48), path))
        print(f"{len(pairs)} pairings, {len(jobs)} games, {a.workers} at a time", flush=True)
        tally: dict[tuple[str, str], list[float]] = {pair: [0.0, 0, 0] for pair in pairs}
        started, done = time.time(), 0
        with mp.Pool(a.workers) as pool:
            for white, black, score, plies in pool.imap_unordered(play, jobs, chunksize=2):
                pair = (white, black) if (white, black) in tally else (black, white)
                tally[pair][0] += score if pair[0] == white else 1 - score
                tally[pair][1] += 1
                tally[pair][2] += plies
                done += 1
                if done % 500 == 0:
                    print(f"  {done}/{len(jobs)} games, {time.time() - started:.0f}s", flush=True)
        with RESULTS.open("a") as f:
            for (x, y), (score, games, plies) in tally.items():
                f.write(json.dumps({"a": x, "b": y, "score": score, "games": games, "plies": plies / max(1, games),
                                    "engine": Engine(path).version(), "depth": opponent.WEAK_DEPTH}) + "\n")  # fmt: skip
        print(f"done in {time.time() - started:.0f}s; results in {RESULTS}")
    rating = fit(load())
    temps = sorted((float(p[2:]), r) for p, r in rating.items() if p.startswith("t:"))
    print("\ntemperature -> rating")
    for t, r in temps:
        print(f"  {t:7.0f}  {r:6.0f}")
    if a.cmd == "fit":
        # What the opponent uses: the levels around and below Stockfish's own floor. A hotter temperature has
        # to play worse; where two measurements say otherwise that is noise, and they are pooled into one level
        # (isotonic regression: each pool's mean rating at its geometric-mean temperature).
        pools: list[list[float]] = []  # [sum of ratings, sum of log temperatures, count]
        for t, r in temps:
            pools.append([r, math.log(t), 1])
            while len(pools) > 1 and pools[-2][0] / pools[-2][2] <= pools[-1][0] / pools[-1][2]:
                r1, lt1, n1 = pools.pop()
                pools[-1] = [pools[-1][0] + r1, pools[-1][1] + lt1, pools[-1][2] + n1]
        levels = [
            [round(rs / n), round(math.exp(lts / n), 1)]
            for rs, lts, n in pools
            if FLOOR - 150 <= rs / n <= opponent.SF_MIN + 250
        ]
        out = {
            "comment": "rating -> temperature of the sub-1320 opponent, measured by scripts/calibrate.py",
            "measured": time.strftime("%Y-%m-%d"),
            "engine": load()[-1].get("engine", ""),
            "games": int(sum(r["games"] for r in load())),
            "min": max(FLOOR, -(-min(r for r, _ in levels) // 50) * 50),  # the weakest rating offered
            "levels": sorted(levels),
        }
        Path(opponent._LEVELS_FILE).write_text(json.dumps(out, indent=2) + "\n")
        print(f"wrote {opponent._LEVELS_FILE}: {out['levels']}, offered from {out['min']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
