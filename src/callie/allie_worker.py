"""Allie in a process of its own: the model loaded once, asked for moves over JSON lines on stdin and stdout.

Run as a file by the Python of Allie's environment (`callie allie install`), never imported by the server:
it needs torch and the `allie` package, which the server does not. `src/callie/allie.py` is the other end.

One request a line, each answered by one line with the same `id`, in whatever order they finish:
  decide   the opponent's move in a game, Allie's calibrated mode: the move and how long to think on it, or
           that it resigns instead; whether it offers a draw with the move
  concede  after its own move: does it resign on seeing the position it has reached?
  predict  what players of a rating play in a position: the policy's probabilities, with no search
  forget   drop a game's cache
  ping
The first line written says the model is loaded ({"ready": true, ...}) or why it is not ({"error": ...}).
"""

import argparse
import json
import os
import sys
import threading
import time
import traceback
from collections import OrderedDict
from dataclasses import replace

GAMES = 8  # games whose caches are kept (each a few tens of MB)
# Run as a file, this directory leads the import path, and its allie.py is not the `allie` meant here.
sys.path[:] = [p for p in sys.path if os.path.realpath(p or ".") != os.path.dirname(os.path.realpath(__file__))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="a directory with config.json and model.safetensors")
    ap.add_argument("--threads", type=int, default=0)
    args = ap.parse_args()

    # Answers go to the pipe this was started with; anything a library prints goes to the log instead.
    out = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    wlock = threading.Lock()

    def say(obj: dict) -> None:
        with wlock:
            out.write(json.dumps(obj) + "\n")
            out.flush()

    def log(text: str) -> None:
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} allie: {text}", file=sys.stderr, flush=True)

    try:
        started = time.monotonic()
        import torch
        from allie.lichess import behaviour, calibration
        from allie.lichess.api import Allie
        from allie.lichess.engine import Engine, Game, Play
        from allie.lichess.model import Model
        from allie.lichess.treers import KL

        behaviour.check()  # what its resignations and draw offers are fitted on
        if args.threads:
            torch.set_num_threads(args.threads)
        # int8 weights on the Rust engine: the only setting Allie's calibrated searches run on
        model = Model(args.model, "cpu", torch.bfloat16, None, True, "rust", args.threads or None)
        torch.set_num_threads(1)  # the engine's own pool does the work; torch's threads would fight it
        engine = Engine(model)
        search = KL(calibration.GROW, calibration.READ, views=calibration.VIEWS)
        policy = Allie(model)
        name = model.config.get("name", "allie")
        if name != "allie-2.1":
            log(f"{name} is playing the calibration fitted for allie-2.1")
        info = {"ready": True, "name": name, "threads": model.fast.threads, "load_s": round(time.monotonic() - started, 1)}
    except BaseException as e:  # noqa: BLE001
        traceback.print_exc()
        say({"error": f"{type(e).__name__}: {e}"})
        return 1
    log(f"loaded {info}")
    say(info)

    # Calibrated mode as Allie's bot plays it, resigning and offering draws as people of its rating do, less what
    # belongs to Lichess: no lag to take off the think time (the clock here is charged exactly what is waited).
    play = Play(mode="calibrated", think_time=True, resign=True, draws=True, lag=0.0)
    games: OrderedDict[str, tuple[tuple, Game, threading.Lock]] = OrderedDict()
    glock, plock = threading.Lock(), threading.Lock()

    def game_of(r: dict):
        key = (r["white_elo"], r["black_elo"], r.get("base"), r.get("inc"), r.get("speed"))
        with glock:
            have = games.get(r["game"])
            if have is None or have[0] != key:
                g = Game(engine, r["white_elo"], r["black_elo"], r.get("base"), r.get("inc"), r.get("speed") or "classical")
                have = (key, g, threading.Lock())
            games[r["game"]] = have
            games.move_to_end(r["game"])
            while len(games) > GAMES:
                games.popitem(last=False)
        return have[1], have[2]

    def decide(r: dict) -> dict:
        g, lock = game_of(r)
        with lock:  # a game is one line of play: a second request for it (after a take-back) waits its turn
            g.update(list(r["moves"]))
            g.clocks[:] = [None if c is None else int(c) for c in r["clocks"]]
            fallback = False
            t = time.monotonic()
            try:
                d = g.decide(play, search, r.get("clock"))
            except BaseException:  # noqa: BLE001  a Rust panic too: the policy plays, loudly
                traceback.print_exc()
                fallback = True
                d = g.decide(replace(play, mode="human", temperature=1.0), None, r.get("clock"))
            took = time.monotonic() - t
        log(
            f"game {r['game']} ply {len(r['moves'])}: {d.move} p={d.probability:.2f} wdl={d.wdl[0]:.2f}/{d.wdl[1]:.2f}/{d.wdl[2]:.2f} "
            f"think {d.think:.1f} s, search {d.sims} leaves in {d.searched:.2f} s"
            f"{' stopped' if d.stopped else ' cut' if d.cut else ''}, decided in {took:.2f} s{' (policy fallback)' if fallback else ''}"
            f"{'; resigning' if d.resign else '; offering a draw' if d.offer_draw else ''}"
        )
        return {
            "move": d.move,
            "think": float(d.think),
            "wdl": [float(x) for x in d.wdl],
            "probability": float(d.probability),
            "sims": int(d.sims),
            "searched": float(d.searched),
            "took": took,
            "fallback": fallback,
            "resign": bool(d.resign),  # instead of playing the move
            "offer_draw": bool(d.offer_draw),  # with the move
        }

    def concede(r: dict) -> dict:
        g, lock = game_of(r)
        with lock:
            g.update(list(r["moves"]))
            g.clocks[:] = [None if c is None else int(c) for c in r["clocks"]]
            resign = bool(g.concede(play, r.get("clock"), bool(r["white"])))
        if resign:
            log(f"game {r['game']} ply {len(r['moves'])}: resigning after its move")
        return {"resign": resign}

    def predict(r: dict) -> dict:
        moves, clocks = list(r["moves"]), list(r.get("clocks") or [None] * len(r["moves"]))
        tc = None if r.get("base") is None else (r["base"], r.get("inc") or 0)
        ask = lambda: policy.analyze(moves, r["white_elo"], r["black_elo"], tc, clocks)  # noqa: E731
        with plock:
            first = ask()
            line = []
            for _ in range(int(r.get("line") or 0)):  # each side's most likely move, one after another
                try:
                    a = ask() if line else first
                except ValueError:  # the line ended the game
                    break
                move, p = next(iter(a["moves"].items()))
                line.append([move, p])
                moves.append(move)
                clocks.append(None)
        return {
            "moves": [[m, p] for m, p in list(first["moves"].items())[: int(r.get("top") or 5)]],
            "legal": len(first["moves"]),
            "wdl": list(first["wdl"]),
            "think_time": first["think_time"],
            "line": line,
        }

    def forget(r: dict) -> dict:
        with glock:
            games.pop(r["game"], None)
        return {}

    ops = {"decide": decide, "concede": concede, "predict": predict, "forget": forget, "ping": lambda r: dict(info)}

    def serve(r: dict) -> None:
        try:
            say({"id": r.get("id"), "ok": True, **ops[r["op"]](r)})
        except BaseException as e:  # noqa: BLE001
            traceback.print_exc()
            say({"id": r.get("id"), "error": f"{type(e).__name__}: {e}"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("op") == "quit":
            break
        threading.Thread(target=serve, args=(r,), daemon=True).start()
    engine.close()
    return 0


if __name__ == "__main__":
    code = main()
    sys.stderr.flush()
    os._exit(code)  # the engine's thread pool has no reason to be waited for
