"""Allie: a model of how people play, as the opponent and as something the tutor can ask.

Allie 2.1 (https://huggingface.co/yimingzhang/allie-2.1) predicts the move a player of a given rating makes,
given the game so far, both clocks and the time control, and how long they think on it. As the opponent it
plays its "calibrated" mode: a think time is drawn first, a search as large as that time allows scores the
moves, and the move is drawn from the human distribution tilted by those scores, so that it plays at the
strength of people of its rating at the game's time control. It also resigns and offers draws where they do.

It needs torch, its own package and 10 GB of memory, so it lives in a process of its own
(`allie_worker.py`, in the environment `callie allie install` makes), started when a browser has the page
on screen, kept for as long as one does, and ended a while after the last of them has gone (and after anything
else last asked it something). Asked with no browser there, it is started for that. This module is the server's
end of that pipe.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from queue import SimpleQueue

from .config import Config

NAME = "Allie 2.1"
REPO = "yimingzhang/allie-2.1"
# The code that plays it: Allie's calibration is fitted to the model and lives in this commit, not in the weights.
SOURCE = "https://github.com/y0mingzhang/allie"
COMMIT = "53e4de8fe256ffe8734a5ff1cb334854c4e1cdf4"
ELO_MIN, ELO_MAX = 800, 2600  # the ratings its calibrated play was fitted on
# The rating every Lichess account starts at. Allie reads a rating as its four digits, and at exactly this one it
# predicts another player than at 1499 or 1501, which agree with each other: a wilder one, taken for a beginner
# after a poor move or two (measured in a game it lost that way; presumably it has learnt the new accounts).
NEWCOMER = 1500
_WORKER = Path(__file__).with_name("allie_worker.py")


class AllieError(Exception):
    pass


def speed(base_s: float, increment_s: float) -> str:
    """The name Lichess gives a time control, which is what picks Allie's calibration: by how long a game of 40
    moves would take."""
    total = base_s + 40 * increment_s
    return "bullet" if total < 180 else "blitz" if total < 480 else "rapid" if total < 1500 else "classical"


def told(elo: int) -> int:
    """The rating Allie is given for a player rated ``elo``: that rating, but never the one new accounts have."""
    return elo + 1 if elo == NEWCOMER else elo


def weights(cfg: Config) -> Path | None:
    """The directory holding the model (config.json, model.safetensors), if it has been downloaded."""
    if cfg.allie_model:
        found = [cfg.allie_model]
    else:
        found = sorted((cfg.allie_home / "hub" / f"models--{REPO.replace('/', '--')}" / "snapshots").glob("*"))
    for d in found:
        if (d / "config.json").is_file() and (d / "model.safetensors").is_file():
            return d
    return None


def missing(cfg: Config) -> str | None:
    """Why Allie cannot run here, or None when everything it needs is in place."""
    if not cfg.allie_python.is_file():
        return f"Allie is not installed (no {cfg.allie_python}): run `callie allie install`"
    if weights(cfg) is None:
        return f"Allie's weights are missing ({cfg.allie_model or cfg.allie_home / 'hub'}): run `callie allie install`"
    return None


class Allie:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()  # starting and stopping
        self.wlock = threading.Lock()  # one writer on the pipe
        self.proc: subprocess.Popen | None = None
        self.loading: subprocess.Popen | None = None  # started, and still reading its model
        self.info: dict = {}
        self.waiting: dict[int, SimpleQueue] = {}
        self.n = 0
        self.used = time.monotonic()
        self.warming = False
        self.failed = float("-inf")  # when it last could not be started ahead of need
        self.closed = threading.Event()
        threading.Thread(target=self._idle, name="allie-idle", daemon=True).start()

    @property
    def ready(self) -> bool:
        """Loaded: a request is answered without first waiting for the model."""
        return self.proc is not None and self.proc.poll() is None

    def available(self) -> bool:
        return missing(self.cfg) is None

    # ---------- the process ----------

    def _start(self) -> None:
        why = "the server is stopping" if self.closed.is_set() else missing(self.cfg)
        if why:
            raise AllieError(why)
        argv = [
            str(self.cfg.allie_python),
            str(_WORKER),
            "--model",
            str(weights(self.cfg)),
            "--threads",
            str(self.cfg.allie_threads),
        ]
        log = open(self.cfg.home / "allie.log", "ab")
        try:
            proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1
            )
        except OSError as e:
            raise AllieError(f"cannot start Allie ({argv[0]}): {e}") from None
        finally:
            log.close()
        self.loading = proc
        if self.closed.is_set():
            proc.kill()
        try:
            line = proc.stdout.readline()  # the model is loaded, which takes a while, or it says why not
        finally:
            self.loading = None
        try:
            first = json.loads(line)
        except ValueError:
            first = {"error": "it stopped before loading"}
        if not first.get("ready"):
            proc.kill()
            raise AllieError(
                f"Allie did not start: {first.get('error', line.strip())} (see {self.cfg.home / 'allie.log'})"
            )
        self.info, self.proc = first, proc
        threading.Thread(target=self._read, args=(proc,), name="allie-answers", daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:
                try:
                    answer = json.loads(line)
                except ValueError:
                    continue
                q = self.waiting.pop(answer.get("id"), None)
                if q is not None:
                    q.put(answer)
        except ValueError:
            pass  # the pipe was closed under it: it was stopped
        for q in list(self.waiting.values()):  # it died: nobody waits for ever
            q.put({"error": f"Allie stopped (see {self.cfg.home / 'allie.log'})"})
        self.waiting.clear()

    def _stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write('{"op": "quit"}\n')
            proc.stdin.flush()
            proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait()
        for pipe in (proc.stdin, proc.stdout):
            try:
                pipe.close()
            except OSError:
                pass

    def close(self) -> None:
        self.closed.set()
        proc = self.loading
        if proc is not None:
            proc.kill()  # not waited for: left to finish loading, it would hold its 10 GB with nobody to ask it
        with self.lock:
            self._stop()

    def _idle(self) -> None:
        """Give the memory back when nobody has wanted it for a while: it loads again when somebody does."""
        while not self.closed.wait(min(30.0, self.cfg.allie_idle_s / 4)):
            with self.lock:
                if self.ready and not self.waiting and time.monotonic() - self.used > self.cfg.allie_idle_s:
                    self._stop()

    def load(self) -> dict:
        """Start it if it is not running; returns once the model is loaded."""
        with self.lock:
            if not self.ready:
                self._start()
            self.used = time.monotonic()
            return self.info

    def warm(self) -> None:
        """It is wanted in memory (a browser has the page on screen): keep it loaded, or start loading it now,
        in the background, so that its first move does not wait for that."""
        self.used = time.monotonic()
        if self.ready or self.warming or time.monotonic() - self.failed < 20:
            return
        self.warming = True

        def load() -> None:
            try:
                self.load()
            except AllieError:
                self.failed = time.monotonic()  # whoever asks it for a move is told why
            finally:
                self.warming = False

        threading.Thread(target=load, name="allie-load", daemon=True).start()

    # ---------- asking ----------

    def call(self, op: str, **args) -> dict:
        q: SimpleQueue = SimpleQueue()
        with self.lock:
            if not self.ready:
                self._start()
            self.used = time.monotonic()
            self.n += 1
            self.waiting[self.n] = q
            try:
                with self.wlock:
                    self.proc.stdin.write(json.dumps({"id": self.n, "op": op, **args}) + "\n")
                    self.proc.stdin.flush()
            except (OSError, ValueError):
                self.waiting.pop(self.n, None)
                self._stop()
                raise AllieError(f"Allie stopped (see {self.cfg.home / 'allie.log'})") from None
        answer = q.get()
        self.used = time.monotonic()
        if "error" in answer:
            raise AllieError(str(answer["error"]))
        return answer

    def decide(self, game_id: str, moves: list[str], clocks: list, white_elo: int, black_elo: int,
               base: int | None, inc: int | None, clock: float | None) -> dict:  # fmt: skip
        """The opponent's move in a game (calibrated mode) and the seconds to think on it; ``resign``: it gives
        the game up instead of playing it; ``offer_draw``: it offers a draw with it. ``clocks``: each mover's
        seconds left after each move; ``clock``: Allie's own now; ``base``, ``inc``: None when untimed."""
        return self.call(
            "decide", game=game_id, moves=moves, clocks=clocks, white_elo=told(white_elo), black_elo=told(black_elo),
            base=base, inc=inc, speed="classical" if base is None else speed(base, inc or 0), clock=clock,
        )  # fmt: skip

    def concede(self, game_id: str, moves: list[str], clocks: list, white_elo: int, black_elo: int,
                base: int | None, inc: int | None, clock: float | None, white: bool) -> bool:  # fmt: skip
        """Allie (White, or not) has just moved: does it resign on seeing the position, as a person might?"""
        return bool(self.call(
            "concede", game=game_id, moves=moves, clocks=clocks, white_elo=told(white_elo), black_elo=told(black_elo),
            base=base, inc=inc, speed="classical" if base is None else speed(base, inc or 0), clock=clock, white=white,
        )["resign"])  # fmt: skip

    def predict(self, moves: list[str], clocks: list, white_elo: int, black_elo: int, base: int | None,
                inc: int | None, top: int = 5, line: int = 0) -> dict:  # fmt: skip
        """What players of those ratings play after ``moves``: the likeliest moves with their probabilities,
        the mover's win / draw / loss chances between such players, and the likeliest continuation."""
        return self.call(
            "predict",
            moves=moves,
            clocks=clocks,
            white_elo=told(white_elo),
            black_elo=told(black_elo),
            base=base,
            inc=inc,
            top=top,
            line=line,
        )

    def forget(self, game_id: str) -> None:
        if self.ready:
            try:
                self.call("forget", game=game_id)
            except AllieError:
                pass
