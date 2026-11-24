"""A UCI chess engine as a child process, and the arithmetic that turns its scores into feedback.

One `Engine` is one process with its own hash table. A search is a blocking call: callers on other threads wait
their turn. Scores are always from White's point of view once they leave this module.
"""

from __future__ import annotations

import math
import subprocess
import threading
import time
from dataclasses import dataclass, field

MATE_CP = 10000  # a forced mate as centipawns, for arithmetic and sorting: mate in n is worth MATE_CP - n
CPL_CAP = 1000  # evaluations beyond +-10 pawns are equally won: the loss of a move is measured inside this


class EngineError(Exception):
    pass


@dataclass
class Line:
    """One principal variation of a search."""

    moves: list[str]  # UCI
    cp: int | None  # centipawns for White, None when it is a forced mate
    mate: int | None  # moves until mate, positive when White mates
    depth: int = 0

    @property
    def value(self) -> int:
        """A single number for White: centipawns, with mates beyond any material count."""
        if self.mate is not None:
            return (MATE_CP - abs(self.mate)) * (1 if self.mate > 0 else -1)
        return self.cp or 0


@dataclass
class Result:
    best: str | None  # the move the engine would play (None when there is none: mate or stalemate)
    lines: list[Line] = field(default_factory=list)  # best first
    depth: int = 0
    nodes: int = 0
    ms: int = 0


class Engine:
    def __init__(self, path: str, threads: int = 1, hash_mb: int = 64, nice: bool = False):
        self.path, self.threads, self.hash_mb = path, threads, hash_mb
        self.nice = nice  # run at the lowest priority: it works only on what the other processes leave idle
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.name = ""
        self.busy = False  # a search is under way
        self._options: dict[str, str] = {}

    # ---------- the process ----------

    def _start(self) -> None:
        try:
            self.proc = subprocess.Popen(
                ["nice", "-n", "19", self.path] if self.nice else [self.path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            raise EngineError(
                f"cannot run the chess engine at {self.path}: {e} (run `callie engine install`)"
            ) from None
        self._options = {}
        self._send("uci")
        for line in self._until("uciok"):
            if line.startswith("id name "):
                self.name = line[8:]
        self._set("Threads", self.threads)
        self._set("Hash", self.hash_mb)
        self._send("isready")
        self._until("readyok")

    def _send(self, text: str) -> None:
        try:
            self.proc.stdin.write(text + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            raise EngineError("the chess engine stopped") from None

    def _until(self, word: str) -> list[str]:
        seen = []
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise EngineError("the chess engine stopped")
            line = line.strip()
            seen.append(line)
            if line == word or line.startswith(word + " "):
                return seen

    def _set(self, name: str, value) -> None:
        value = str(value).lower() if isinstance(value, bool) else str(value)
        if self._options.get(name) != value:
            self._send(f"setoption name {name} value {value}")
            self._options[name] = value

    def close(self) -> None:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                try:
                    self._send("quit")
                    self.proc.wait(timeout=2)
                except (EngineError, subprocess.TimeoutExpired):
                    self.proc.kill()
            self.proc = None

    def stop(self) -> None:
        """End the search under way, if there is one: it answers at once, with what it has found so far (which
        may be nothing). For an engine asked about one position after another, where a new question makes the
        last one moot. Not behind the lock: that is the search's."""
        proc = self.proc
        if self.busy and proc is not None:
            try:
                proc.stdin.write("stop\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                pass  # it has stopped by itself

    def version(self) -> str:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            return self.name

    # ---------- searching ----------

    def search(
        self,
        fen: str,
        moves: list[str] | tuple[str, ...] = (),
        *,
        ms: int | None = None,
        depth: int | None = None,
        nodes: int | None = None,
        multipv: int = 1,
        only: list[str] | None = None,
        elo: int | None = None,
    ) -> Result:
        """Search the position reached from ``fen`` by ``moves``, for ``ms`` milliseconds, to ``depth``, or over
        ``nodes`` positions (as much work however long it takes: for an engine that may be kept waiting).
        ``only`` restricts it to those first moves; ``elo`` has the engine play at its own reduced strength
        (its best move is then deliberately not the best).
        """
        with self.lock:
            for attempt in (1, 2):
                try:
                    if self.proc is None or self.proc.poll() is not None:
                        self._start()
                    return self._search(fen, moves, ms, depth, nodes, multipv, only, elo)
                except EngineError:
                    if self.proc:
                        self.proc.kill()
                    self.proc = None
                    if attempt == 2:
                        raise
        raise EngineError("unreachable")

    def _search(self, fen, moves, ms, depth, nodes, multipv, only, elo) -> Result:
        self._set("MultiPV", max(1, min(int(multipv), 256)))
        self._set("UCI_LimitStrength", elo is not None)
        if elo is not None:
            self._set("UCI_Elo", int(elo))
        self._send(f"position fen {fen}" + (" moves " + " ".join(moves) if moves else ""))
        go = "go"
        if depth is not None:
            go += f" depth {int(depth)}"
        if nodes is not None:
            go += f" nodes {int(nodes)}"
        if ms is not None or (depth is None and nodes is None):
            go += f" movetime {int(ms or 1000)}"
        if only:
            go += " searchmoves " + " ".join(only)
        black = (fen.split()[1] == "b") != (len(moves) % 2 == 1)
        sign = -1 if black else 1
        found: dict[int, Line] = {}
        nodes = took = 0
        started = time.monotonic()
        self._send(go)
        self.busy = True
        while True:
            line = self.proc.stdout.readline()
            if not line:
                self.busy = False
                raise EngineError("the chess engine stopped")
            words = line.split()
            if not words:
                continue
            if words[0] == "bestmove":
                self.busy = False
                best = words[1] if len(words) > 1 and words[1] != "(none)" else None
                break
            if words[0] != "info" or "score" not in words:
                continue
            idx = int(words[words.index("multipv") + 1]) if "multipv" in words else 1
            # A bound is only half an answer: kept just until a proper score for that line arrives.
            if ("upperbound" in words or "lowerbound" in words) and idx in found:
                continue
            at = words.index("score")
            kind, value = words[at + 1], int(words[at + 2])
            pv = words[words.index("pv") + 1 :] if "pv" in words else []
            if not pv and idx in found:
                continue
            found[idx] = Line(
                moves=pv,
                cp=sign * value if kind == "cp" else None,
                mate=sign * value if kind == "mate" else None,
                depth=int(words[words.index("depth") + 1]) if "depth" in words else 0,
            )
            if "nodes" in words:
                nodes = int(words[words.index("nodes") + 1])
        took = int((time.monotonic() - started) * 1000)
        lines = [found[i] for i in sorted(found)]
        return Result(best=best, lines=lines, depth=max((ln.depth for ln in lines), default=0), nodes=nodes, ms=took)


# ---------- what a score means ----------


def win_percent(value: int) -> float:
    """White's expected score, in percent, for an evaluation (the curve lichess fitted to its games)."""
    if abs(value) >= MATE_CP - 1000:
        return 100.0 if value > 0 else 0.0
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * max(-CPL_CAP, min(CPL_CAP, value)))) - 1)


def show(cp: int | None, mate: int | None) -> str:
    """An evaluation as it is written on a board: "+0.35", "-1.20", "#3", "#-2"."""
    if mate is not None:
        return f"#{mate}"
    return f"{(cp or 0) / 100:+.2f}"


def loss(before: int, after: int, white_moved: bool) -> int:
    """Centipawns a move gave away: the evaluation before it against the one after, from the mover's side and
    inside +-CPL_CAP (a win that stays a win costs nothing)."""
    side = 1 if white_moved else -1
    clamp = lambda v: max(-CPL_CAP, min(CPL_CAP, v))  # noqa: E731
    return max(0, clamp(side * before) - clamp(side * after))


def verdict(before: int, after: int, white_moved: bool, was_best: bool) -> str:
    """How a move is graded, by the winning chances it gave away (lichess's thresholds)."""
    side = 1 if white_moved else -1
    drop = side * (win_percent(before) - win_percent(after))
    if was_best:
        return "best"
    if drop >= 15:
        return "blunder"
    if drop >= 10:
        return "mistake"
    if drop >= 5:
        return "inaccuracy"
    return "good"
