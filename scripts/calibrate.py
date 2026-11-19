"""Measure what rating each setting of the Stockfish opponent plays at, and write chesstutor/levels.json.

Two measurements, one after the other:

1. Stockfish's own scale. Players are `sf:<elo>` (Stockfish limiting itself, `UCI_Elo`: the fixed points) and
   `t:<temperature>` (the move sampler of chesstutor/opponent.py). Matches are played between players expected to
   be within a few hundred points of each other, then one rating per temperature is fitted to all the results at
   once (maximum likelihood under the Elo model, with the `sf:` players held at their nominal ratings).

2. Allie's scale, which is the one the app speaks: the same players against `allie:<rating>` (Allie playing as
   the app has it play, at 10+3), each against the rating it is about level with. From the results, the rating on
   Allie's scale of every point of Stockfish's own: a line below `UCI_Elo`'s floor, another above it, meeting there.

    ./scripts/calibrate.py play --games 200          # 1: appends results to calibration.jsonl
    ./scripts/calibrate.py allie --games 60          # 2: appends each game to calibration-allie.jsonl
    ./scripts/calibrate.py fit                        # prints the ratings, writes levels.json

Allie's games are slow (its searches are what its strength is made of: about three minutes a game, one game at a
time) and can be stopped and added to at any time. While `scripts/calibration.pause` exists, or the app's own
Allie is running, they wait: two of it do not fit in memory, and a busy machine makes it play below its rating.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import random
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chesstutor import allie as allie_mod  # noqa: E402
from chesstutor import opponent  # noqa: E402
from chesstutor.allie import Allie, AllieError  # noqa: E402
from chesstutor.chess import START_FEN, Game  # noqa: E402
from chesstutor.config import Config, load_config  # noqa: E402
from chesstutor.engine import Engine  # noqa: E402
from chesstutor.play import MIN_REPLY_S  # noqa: E402

RESULTS = Path(__file__).with_name("calibration.jsonl")
ALLIE_RESULTS = Path(__file__).with_name("calibration-allie.jsonl")
PAUSE = Path(__file__).with_name("calibration.pause")  # while this file exists, games against Allie wait
MAX_PLIES = 400
FLOOR = 400  # no opponent is offered below this rating
ANCHOR_MS = 30  # Stockfish picks its weakened move at a fixed shallow depth, so thinking longer changes nothing

# Temperatures, strongest first, and the anchors each is close enough to for a match to say something.
TEMPS = [8, 16, 30, 50, 80, 120, 180, 270, 400, 650, 1100, 2500]

# Against Allie: the settings of Stockfish that are tried, weakest first, and the clock Allie plays to. They are
# the ones a learner meets: where a game costs minutes, none is spent on telling 2400 from 2600 (above the last
# of these the line through them is carried on).
VERSUS = ["t:245", "t:180", "t:135", "t:100", "sf:1320", "sf:1550", "sf:1800", "sf:2100"]
BASE_S, INC_S = 600, 3
FIRST_GUESS = 300  # how far above Stockfish's own scale Allie's is taken to be until ten games say otherwise
PRIOR_SD = 600.0  # the two scales are taken to agree, this loosely: it only keeps a fit to a few games finite
Q = math.log(10) / 400

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


def load(path: Path = RESULTS) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


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


def pooled(rating: dict[str, float]) -> list[list[float]]:
    """What the opponent uses of a fit: [rating, temperature] of the levels around and below Stockfish's own
    floor, weakest first. A hotter temperature has to play worse; where two measurements say otherwise that is
    noise, and they are pooled into one level (isotonic regression: each pool's mean rating at its
    geometric-mean temperature)."""
    pools: list[list[float]] = []  # [sum of ratings, sum of log temperatures, count]
    for t, r in sorted((float(p[2:]), r) for p, r in rating.items() if p.startswith("t:")):
        pools.append([r, math.log(t), 1])
        while len(pools) > 1 and pools[-2][0] / pools[-2][2] <= pools[-1][0] / pools[-1][2]:
            r1, lt1, n1 = pools.pop()
            pools[-1] = [pools[-1][0] + r1, pools[-1][1] + lt1, pools[-1][2] + n1]
    return sorted(
        [round(rs / n), round(math.exp(lts / n), 1)] for rs, lts, n in pools if FLOOR - 150 <= rs / n <= opponent.SF_MIN + 250
    )


# ---------- against Allie ----------


def own(player: str, levels: list) -> float:
    """Where a setting of Stockfish stands on Stockfish's own scale: `sf:` by its name, `t:` by ``levels``
    (between two of them, a straight line in log-temperature, as the opponent reads them)."""
    kind, _, value = player.partition(":")
    if kind == "sf":
        return float(value)
    lt, hot = math.log(float(value)), sorted((math.log(t), r) for r, t in levels)
    for (t0, r0), (t1, r1) in zip(hot, hot[1:]):
        if lt <= t1 or (t1, r1) == hot[-1]:
            return r0 + (lt - t0) / (t1 - t0) * (r1 - r0)
    return hot[0][1]


def _weights(knots: list[float], s: float) -> list[float]:
    """A point of a line through values at ``knots`` (and on past its ends) as a mix of those values."""
    i = max(0, min(len(knots) - 2, sum(k <= s for k in knots) - 1))
    w = [0.0] * len(knots)
    w[i + 1] = (s - knots[i]) / (knots[i + 1] - knots[i])
    w[i] = 1 - w[i + 1]
    return w


def _inverse(m: list[list[float]]) -> list[list[float]]:
    n = len(m)
    a = [row[:] + [float(i == j) for j in range(n)] for i, row in enumerate(m)]
    for i in range(n):
        pivot = max(range(i, n), key=lambda r: abs(a[r][i]))
        a[i], a[pivot] = a[pivot], a[i]
        a[i] = [x / a[i][i] for x in a[i]]
        for r in range(n):
            if r != i:
                a[r] = [x - a[r][i] * y for x, y in zip(a[r], a[i])]
    return [row[n:] for row in a]


class Scale:
    """Stockfish's own scale read on Allie's: the rating at which Allie is level with each point of it.

    Two straight lines that meet where Stockfish's own limit begins (`SF_MIN`): the sampler below it and
    `UCI_Elo` above it are different ways of playing badly, and need not stretch alike against a human's play.
    Their three ends are fitted to the games (maximum likelihood under the Elo model, Allie held at its ratings).
    """

    def __init__(self, games: list[tuple[float, float, float, float]]):
        """``games``: (Stockfish's own rating, Allie's rating, Stockfish's score, games)."""
        self.games = games
        at = [s for s, *_ in games] or [opponent.SF_MIN - 800, opponent.SF_MIN + 1000]
        self.knots = [min(at), float(opponent.SF_MIN), max(at)]
        if self.knots[0] >= opponent.SF_MIN - 50 or self.knots[2] <= opponent.SF_MIN + 50:
            self.knots = [min(at), max(max(at), min(at) + 100)]  # all on one side of the join: one line
        n = len(self.knots)
        self.values = self.knots[:]
        for _ in range(200):
            grad = [-(v - k) / PRIOR_SD**2 for v, k in zip(self.values, self.knots)]
            hess = [[float(i == j) / PRIOR_SD**2 for j in range(n)] for i in range(n)]  # of minus the log-likelihood
            for s, r, score, count in games:
                w = _weights(self.knots, s)
                e = 1 / (1 + 10 ** ((r - sum(x * v for x, v in zip(w, self.values))) / 400))
                for i in range(n):
                    grad[i] += Q * (score - count * e) * w[i]
                    for j in range(n):
                        hess[i][j] += Q * Q * count * e * (1 - e) * w[i] * w[j]
            self.cov = _inverse(hess)
            step = [max(-200.0, min(200.0, sum(c * g for c, g in zip(row, grad)))) for row in self.cov]
            self.values = [v + d for v, d in zip(self.values, step)]
            if max(abs(d) for d in step) < 0.01:
                break

    def __call__(self, s: float) -> float:
        return sum(w * v for w, v in zip(_weights(self.knots, s), self.values))

    def error(self, s: float) -> float:
        """The standard error of the rating read at ``s``."""
        w = _weights(self.knots, s)
        return math.sqrt(sum(w[i] * self.cov[i][j] * w[j] for i in range(len(w)) for j in range(len(w))))


def versus(rows: list[dict], levels: list) -> list[tuple[float, float, float, float]]:
    """The games against Allie as Scale reads them."""
    return [(own(r["a"], levels), float(r["b"].partition(":")[2]), r["score"], r["games"]) for r in rows]


class Table:
    """The Allie the games are played against: one process, loaded once, and let go of while it must wait."""

    def __init__(self, cfg: Config):
        self.cfg, self.allie = cfg, Allie(cfg)

    def _other(self) -> bool:
        """Another Allie is running on this machine (the app's own, for a game of yours)."""
        mine = self.allie.proc.pid if self.allie.proc else None
        for f in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                if b"allie_worker.py" in f.read_bytes() and int(f.parent.name) != mine:
                    return True
            except OSError:
                pass
        return False

    def wait(self) -> None:
        """Between two moves: stand still while asked to (the clocks of these games are not real ones)."""
        told = False
        while (other := self._other()) or PAUSE.exists():
            if not told:
                print(f"  waiting: {'the app is using Allie' if other else PAUSE.name + ' exists'}", flush=True)
                told = True
            if other and self.allie.ready:  # its memory is needed: this one loads again afterwards
                self.close()
                self.allie = Allie(self.cfg)
            time.sleep(3)

    def close(self) -> None:
        self.allie.close()


def allie_game(table: Table, engine: Engine, sf: str, rating: int, allie_white: bool, rng: random.Random) -> dict:
    """One game of a Stockfish setting against Allie at ``rating``, as the app plays a 10+3 game against it:
    Allie is told the same things, charged its think time, and resigns as it does there. Nothing is waited
    out: a search takes what it takes and the clocks are arithmetic. Stockfish's clock, which Allie is shown,
    is charged what Allie last took. Draw offers are declined."""
    game, clocks, gid = Game(), [], f"cal-{secrets.token_hex(4)}"
    left = {"allie": float(BASE_S), "sf": float(BASE_S)}
    told = {"white_elo": rating, "black_elo": rating, "base": BASE_S, "inc": INC_S}
    last, end, leaves, searching, started = 1.0, None, 0, 0.0, time.time()
    while end is None and game.outcome() is None and len(game.moves) < MAX_PLIES:
        table.wait()
        first = len(game.moves) < 2  # as in the app, nobody's first move is timed
        if (len(game.moves) % 2 == 0) == allie_white:
            d = table.allie.decide(gid, list(game.moves), list(clocks), clock=left["allie"], **told)
            leaves, searching = leaves + d["sims"], searching + d["searched"]
            think = 0.0 if first else max(d["think"], min(MIN_REPLY_S, left["allie"] / 20))
            if d["resign"] or left["allie"] - think <= 0:
                end = "resignation" if d["resign"] else "time"
                break
            left["allie"] += INC_S - think
            last = d["think"]
            game.push(d["move"])
            clocks.append(left["allie"])
            if game.outcome() is None and table.allie.concede(
                gid, list(game.moves), list(clocks), clock=left["allie"], white=allie_white, **told
            ):
                end = "resignation"
        else:
            move = _move(sf, engine, game.moves, rng)
            left["sf"] += INC_S - (0.0 if first else min(last, max(0.0, left["sf"] - 10)))
            game.push(move)
            clocks.append(left["sf"])
    table.allie.forget(gid)
    result, reason = game.outcome() or ("1/2-1/2", "too long")
    if end:  # Allie gave up or ran out of time
        result, reason = ("0-1" if allie_white else "1-0"), end
    score = {"1-0": 0.0 if allie_white else 1.0, "0-1": 1.0 if allie_white else 0.0}.get(result, 0.5)
    return {
        "a": sf, "b": f"allie:{rating}", "score": score, "games": 1, "white": "b" if allie_white else "a",
        "result": result, "reason": reason, "plies": len(game.moves), "clock": f"{BASE_S // 60}+{INC_S}",
        "leaves": leaves, "search_s": round(searching, 1), "seconds": round(time.time() - started),
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }  # fmt: skip


def next_game(rows: list[dict], levels: list, rng: random.Random) -> tuple[str, int, bool]:
    """Who plays next: the setting with the fewest games, against Allie at about the rating the games so far
    make it level with (that is where a game says the most), and with the colour it has had less often."""
    scale = Scale(versus(rows, levels))
    level = (lambda s: scale(s)) if len(rows) >= 10 else (lambda s: s + FIRST_GUESS)
    played = {p: [r for r in rows if r["a"] == p] for p in VERSUS}
    within = [p for p in VERSUS if allie_mod.ELO_MIN - 150 <= level(own(p, levels)) <= allie_mod.ELO_MAX + 150] or VERSUS
    fewest = min(len(played[p]) for p in within)
    sf = rng.choice([p for p in within if len(played[p]) == fewest])
    rating = round((level(own(sf, levels)) + rng.choice((-100, 0, 100))) / 50) * 50
    rating = max(allie_mod.ELO_MIN, min(allie_mod.ELO_MAX, rating))
    whites = sum(r["white"] == "a" for r in played[sf])
    return sf, rating, whites * 2 > len(played[sf]) or (whites * 2 == len(played[sf]) and rng.random() < 0.5)


def play_allie(games: int) -> None:
    cfg = load_config()
    why = allie_mod.missing(cfg)
    if why:
        sys.exit(why)
    engine, rng, table = Engine(str(cfg.engine), threads=1, hash_mb=16), random.Random(), Table(cfg)
    names = {"engine": engine.version(), "depth": opponent.WEAK_DEPTH}
    failed = 0
    try:
        for n in range(1, games + 1):
            rows = load(ALLIE_RESULTS)
            sf, rating, allie_white = next_game(rows, opponent.LEVELS, rng)
            try:
                table.wait()
                row = allie_game(table, engine, sf, rating, allie_white, rng)
                failed = 0
            except AllieError as e:
                failed += 1
                print(f"  game dropped: {e}", flush=True)
                if failed >= 3:
                    sys.exit("Allie keeps failing")
                table.close()
                table = Table(cfg)
                continue
            with ALLIE_RESULTS.open("a") as f:
                f.write(json.dumps({**row, **names, "allie": table.allie.info.get("name", "")}) + "\n")
            print(
                f"{n}/{games}  {sf} ({'Black' if allie_white else 'White'}) vs allie:{rating}: {row['result']} by {row['reason']}, "
                f"{row['plies']} plies, {row['seconds']} s",
                flush=True,
            )
    finally:
        table.close()
        engine.close()


def fit_allie(levels: list, quiet: bool = False) -> Scale | None:
    rows = load(ALLIE_RESULTS)
    if not rows:
        return None
    scale = Scale(versus(rows, levels))
    if quiet:
        return scale
    print(f"\nagainst Allie, {len(rows)} games (Stockfish's score)")
    for p in sorted({r["a"] for r in rows}, key=lambda p: own(p, levels)):
        mine = [r for r in rows if r["a"] == p]
        s = own(p, levels)
        expected = sum(1 / (1 + 10 ** ((float(r["b"][6:]) - scale(s)) / 400)) for r in mine)
        against = sorted({int(r["b"][6:]) for r in mine})
        print(
            f"  {p:>8} (own scale {s:5.0f}) vs allie:{against[0]}-{against[-1]}: {sum(r['score'] for r in mine):5.1f}/{len(mine):<3} "
            f"(the fit predicts {expected:5.1f});  level with Allie at {scale(s):5.0f} +- {scale.error(s):.0f}"
        )
    print("Stockfish's own scale -> Allie's")
    for s in scale.knots:
        print(f"  {s:6.0f}  {scale(s):6.0f} +- {scale.error(s):.0f}")
    return scale


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("play")
    p.add_argument("--games", type=int, default=100, help="per pairing")
    p.add_argument("--temps", type=str, default=",".join(map(str, TEMPS)))
    p.add_argument("--anchors", type=str, default="1320,1500,1700")
    p.add_argument("--reach", type=int, default=2, help="how many weaker neighbours each temperature also plays")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    p = sub.add_parser("allie")
    p.add_argument("--games", type=int, default=60, help="how many more to play, one after another")
    sub.add_parser("fit")
    a = ap.parse_args()
    if a.cmd == "allie":
        play_allie(a.games)
        fit_allie(opponent.LEVELS)
        return 0
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
    print("\ntemperature -> rating (Stockfish's own scale)")
    for t, r in temps:
        print(f"  {t:7.0f}  {r:6.0f}")
    if a.cmd == "fit":
        levels = pooled(rating)
        scale = fit_allie(levels)
        if scale and any(a >= b for a, b in zip(scale.values, scale.values[1:])):
            sys.exit("against Allie a stronger setting came out no stronger: more games are needed before this is used")
        rated = scale or (lambda s: s)  # not measured against Allie: Stockfish's own scale stands
        weakest = max(FLOOR, -(-min(r for r, _ in levels) // 50) * 50)  # on Stockfish's own scale
        out = {
            "comment": "levels: rating on Stockfish's own scale -> temperature of the sub-1320 opponent; "
            "scale: Stockfish's own scale -> Allie's, which ratings are given in; both measured by scripts/calibrate.py",
            "measured": time.strftime("%Y-%m-%d"),
            "engine": load()[-1].get("engine", ""),
            "games": int(sum(r["games"] for r in load())),
            "allie": {"model": allie_mod.NAME, "games": len(load(ALLIE_RESULTS)), "clock": f"{BASE_S // 60}+{INC_S}"},
            "min": -(-round(rated(weakest)) // 10) * 10,  # the weakest rating offered, and the strongest
            "max": round(rated(opponent.SF_MAX)) // 10 * 10,
            "levels": levels,
            "scale": [[round(s), round(scale(s))] for s in scale.knots] if scale else [],
        }
        Path(opponent._LEVELS_FILE).write_text(json.dumps(out, indent=2) + "\n")
        print(f"wrote {opponent._LEVELS_FILE}: {out['levels']}, scale {out['scale']}, offered from {out['min']} to {out['max']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
