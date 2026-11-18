"""Games: making moves, the opponent's replies, the clocks, and the engine's analysis of every position.

Three Stockfish processes: `review` analyses each position of a game once, at full strength; `explore` answers
the tutor's own questions; `opp` plays the opponent at its chosen strength. The opponent can instead be Allie
(`allie.py`), a model of human play in a process of its own, which then has a clock like yours, and which
resigns and offers draws as people do. Whoever plays,
the analysis is Stockfish's. Every change to a game bumps its version, which is what a page waiting on
`state(since=...)` wakes up on.
"""

from __future__ import annotations

import random
import secrets
import sys
import threading
import time
import traceback
import uuid
from collections import deque

from . import allie, openings, opponent
from .allie import Allie, AllieError
from .chess import START_FEN, Game, Position, describe
from .config import Config
from .engine import MATE_CP, Engine, EngineError, loss, show, verdict, win_percent
from .store import Store

TIME_CONTROLS = ("untimed", "10+3")  # minutes + seconds added per move; any "M+S" is accepted
ENGINES = ("stockfish", "allie")
AWAY_S = 3.0  # a gap this long between two looks at a clock means no page was open: it is not charged
FRESH_S = 1.5  # the opponent's own clock is charged between a page's looks only this long after the last one
MIN_REPLY_S = 0.5  # a reply the instant you let go of the piece is jarring
RETRY_S = 20.0  # after the opponent failed to produce a move, how long before it is asked again
PV_PLIES = 10  # how much of an engine line is kept and shown
_RULE_DRAWS = ("insufficient material", "fifty-move rule", "threefold repetition")


class GameError(Exception):
    pass


def _numbered(fullmove: int, turn: str, sans: list[str]) -> str:
    """SAN moves as text with move numbers, starting from a position's counters: "12... h6 13. Nf3"."""
    out = []
    for i, san in enumerate(sans):
        if turn == "w":
            out.append(f"{fullmove}.")
        elif i == 0:
            out.append(f"{fullmove}...")
        out.append(san)
        if turn == "b":
            fullmove += 1
        turn = "b" if turn == "w" else "w"
    return " ".join(out)


def _line_sans(pos: Position, moves: list[str]) -> list[str]:
    """An engine line as SAN; cut where it stops being legal (the tail of a line can be stale)."""
    out = []
    for m in moves:
        try:
            out.append(pos.san(m))
            pos = pos.push(m)
        except ValueError:
            break
    return out


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = Store(cfg.db_path)
        path = str(cfg.engine)
        self.review = Engine(path, threads=cfg.analysis_threads, hash_mb=cfg.hash_mb)
        self.explore = Engine(path, threads=cfg.analysis_threads, hash_mb=cfg.hash_mb)
        self.opp = Engine(path, threads=2, hash_mb=64)
        self.allie = Allie(cfg)
        self.lock = threading.RLock()  # one change to a game at a time
        self.changed = threading.Condition()
        self.versions: dict[str, int] = {}
        self.epochs: dict[str, int] = {}  # bumped by every move and take-back: a reply computed for an older one is dropped
        self.thinking: set[str] = set()
        self.clocks: dict[str, float] = {}  # games whose clock is running, and when it was last charged
        self.looked: dict[str, float] = {}  # when a page last looked at a game that has a clock
        self.replies: dict[str, dict] = {}  # the opponent's move in the making: what its clock was charged for it
        self.trouble: dict[str, tuple[float, str]] = {}  # the opponent could not move: when, and why
        self.queue: deque[tuple[str, int]] = deque()
        self.queued: set[tuple[str, int]] = set()
        self.work = threading.Event()
        self.stop = threading.Event()
        self.tutor = None  # set by the server: chesstutor.tutor.Tutor
        self._chess: dict[str, tuple[tuple[str, ...], Game, dict | None]] = {}
        self.rng = random.Random()

    def start(self) -> None:
        threading.Thread(target=self._analyse_loop, name="analysis", daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        self.work.set()
        for e in (self.review, self.explore, self.opp, self.allie):
            e.close()

    # ---------- reading ----------

    def meta(self) -> dict:
        try:
            engine = self.review.version()
        except EngineError as e:
            engine = f"missing ({e})"
        why = allie.missing(self.cfg)
        return {
            "engine": engine,
            "engines": [
                {
                    "key": "stockfish",
                    "name": "Stockfish",
                    "elo": {"min": opponent.ELO_MIN, "max": opponent.SF_MAX, "native": opponent.SF_MIN, "default": 1200},
                    "full": True,  # it can also play with nothing held back
                    "available": True,
                },
                {
                    "key": "allie",
                    "name": allie.NAME,
                    "elo": {"min": allie.ELO_MIN, "max": allie.ELO_MAX, "default": 1200},
                    "full": False,
                    "available": why is None,
                    "missing": why,
                },
            ],
            "openings": openings.choices(),
            "time_controls": list(TIME_CONTROLS),
            "model": self.cfg.claude_model,
        }

    def _row(self, game_id: str) -> dict:
        row = self.store.game(game_id)
        if row is None:
            raise GameError(f"no game {game_id}")
        return row

    def _game(self, row: dict, moves: list[dict]) -> Game:
        """The game replayed (kept from one call to the next: only new moves are played onto it)."""
        ucis = tuple(m["uci"] for m in moves)
        cached = self._chess.get(row["id"])
        if cached and cached[0] == ucis:
            return cached[1]
        if cached and cached[0] == ucis[: len(cached[0])]:
            game = cached[1]
            for m in ucis[len(cached[0]) :]:
                game.push(m)
        else:
            game = Game(row["start_fen"], ucis)
        self._chess[row["id"]] = (ucis, game, None)
        return game

    def _legal(self, row: dict, game: Game) -> dict:
        """Every legal move of the current position with its SAN and the position it leads to: the page moves
        pieces from this, with no rules of its own."""
        ucis, g, legal = self._chess[row["id"]]
        if legal is None:
            pos = game.position
            legal = {m: {"san": pos.san(m), "fen": pos.push(m).fen()} for m in pos.legal_moves()}
            self._chess[row["id"]] = (ucis, g, legal)
        return legal

    def _evals(self, row: dict, moves: list[dict]) -> dict[int, dict]:
        """The analysis on file, less any row left over from a position that was taken back."""
        fens = [row["start_fen"]] + [m["fen"] for m in moves]
        return {p: e for p, e in self.store.evals(row["id"]).items() if p < len(fens) and e["fen"] == fens[p]}

    @staticmethod
    def _grade(move: dict, before: dict, after: dict, white_moved: bool) -> dict:
        best = before["lines"][0] if before["lines"] and before["lines"][0]["moves"] else None
        was_best = bool(best) and best["moves"][0] == move["uci"]
        out = {
            "cpl": 0 if was_best else loss(before["value"], after["value"], white_moved),
            "verdict": verdict(before["value"], after["value"], white_moved, was_best),
        }
        if best:
            out["best"] = {
                "uci": best["moves"][0],
                "san": best["san"][0] if best["san"] else best["moves"][0],
                "line": best["san"],  # what the engine expected to follow
                "cp": best["cp"],
                "mate": best["mate"],
            }
        return out

    def _annotated(self, row: dict, moves: list[dict], evals: dict[int, dict], game: Game) -> list[dict]:
        out = []
        for m in moves:
            ply = m["ply"]
            e = {"ply": ply, "uci": m["uci"], "san": m["san"], "fen": m["fen"], "source": m["source"]}
            after, before = evals.get(ply), evals.get(ply - 1)
            if after:
                e["eval"] = {"cp": after["cp"], "mate": after["mate"], "value": after["value"], "depth": after["depth"]}
            if after and before:
                e.update(self._grade(m, before, after, game.positions[ply - 1].turn == "w"))
            out.append(e)
        return out

    @staticmethod
    def _opponent(row: dict, long: bool = False) -> str:
        """Who the opponent is: "Stockfish 1320" on the page, "Stockfish (1320 Elo)" in a PGN."""
        if row["engine"] == "allie":
            return f"{allie.NAME} ({row['elo']} Elo)" if long else f"Allie {row['elo']}"
        if not row["elo"]:
            return "Stockfish" if long else "Stockfish, full strength"
        return f"Stockfish ({row['elo']} Elo)" if long else f"Stockfish {row['elo']}"

    def summary(self, row: dict, plies: int | None = None, fen: str | None = None) -> dict:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "user_color": row["user_color"],
            "engine": row["engine"],
            "opponent": self._opponent(row),
            "elo": row["elo"],
            "opening": {"key": row["opening"], "name": openings.name_of(row["opening"])},
            "time_control": row["time_control"] or "untimed",
            "status": row["status"],
            "result": row["result"],
            "reason": row["reason"],
            "plies": plies,
            "fen": fen or row["start_fen"],
        }

    def games(self) -> list[dict]:
        last = {
            r["game_id"]: r["fen"]
            for r in self.store.all(
                "SELECT game_id, fen FROM moves m WHERE ply = (SELECT max(ply) FROM moves WHERE game_id = m.game_id)"
            )
        }
        return [self.summary(r, r["plies"], last.get(r["id"])) for r in self.store.games()]

    def state(self, game_id: str, since: int | None = None, wait: float = 25.0) -> dict:
        """Everything the page shows of a game. With ``since``, waits until it has changed from that version."""
        deadline = time.time() + wait
        while since is not None:
            with self.lock:
                self._tick(game_id)  # a page waiting here is a page on the game: its clock runs
            with self.changed:
                if self.versions.get(game_id, 0) != since or time.time() >= deadline or self.stop.is_set():
                    break
                self.changed.wait(max(0.0, min(1.0, deadline - time.time())))
        with self.lock:
            self._tick(game_id)
            row = self._row(game_id)
            moves = self.store.moves(game_id)
            game = self._game(row, moves)
            evals = self._evals(row, moves)
            self._keep_going(row, moves, evals, game)
            pos = game.position
            users_turn = row["status"] == "active" and pos.turn == row["user_color"]
            annotated = self._annotated(row, moves, evals, game)
            if row["status"] == "active":
                for m in annotated:  # what the engine would have played is for after the game (and for Claude)
                    m.pop("best", None)
            start = evals.get(0)
            acpl = {}
            for who in ("user", "opponent"):
                losses = [m["cpl"] for m in annotated if "cpl" in m and (m["source"] == "user") == (who == "user")]
                acpl[who] = round(sum(losses) / len(losses)) if losses else None
            out = self.summary(row, len(moves), pos.fen())
            running = game_id in self.clocks
            trouble = self.trouble.get(game_id)
            out.update(
                version=self.versions.setdefault(game_id, int(time.time() * 1000) % 10**9),
                start_fen=row["start_fen"],
                start_eval={k: start[k] for k in ("cp", "mate", "value", "depth")} if start else None,
                moves=annotated,
                turn=pos.turn,
                check=pos.in_check(),
                legal=self._legal(row, game) if users_turn else {},
                thinking=game_id in self.thinking,
                draw_offer=users_turn and row["draw_offer"] == len(moves),  # the opponent's, yours to answer
                # Allie's model is being read into memory before it can think (about a minute, when it was not loaded)
                loading=self.replies.get(game_id, {}).get("loading", False),
                trouble=trouble[1] if trouble and row["status"] == "active" and not users_turn else None,
                acpl=acpl,
                pgn=self._pgn(row, moves),
                clock=None
                if row["clock_ms"] is None
                else {
                    "ms": row["clock_ms"],
                    "increment_ms": row["increment_ms"],
                    "running": running and users_turn,
                    "paused": bool(row["paused"]),
                    "opponent": None
                    if row["opp_clock_ms"] is None
                    else {"ms": row["opp_clock_ms"], "running": running and not users_turn},
                },
                claude={
                    "started": bool(row["session_started"]),
                    "analysed_ply": row["analysed_ply"],
                    **(self.tutor.status(game_id) if self.tutor else {}),
                },
            )
            return out

    def _pgn(self, row: dict, moves: list[dict]) -> str:
        start = Position.from_fen(row["start_fen"])
        you = "You"
        them = self._opponent(row, long=True)
        tags = {
            "Event": "Chess Tutor game",
            "Date": time.strftime("%Y.%m.%d", time.localtime(row["created_at"])),
            "White": you if row["user_color"] == "w" else them,
            "Black": them if row["user_color"] == "w" else you,
            "Result": row["result"] or "*",
        }
        if row["start_fen"] != START_FEN:
            tags.update(SetUp="1", FEN=row["start_fen"])
        text = _numbered(start.fullmove, start.turn, [m["san"] for m in moves])
        return "\n".join(f'[{k} "{v}"]' for k, v in tags.items()) + f"\n\n{text} {row['result'] or '*'}".rstrip() + "\n"

    # ---------- changing ----------

    def _bump(self, game_id: str) -> None:
        with self.changed:
            self.versions[game_id] = self.versions.get(game_id, int(time.time() * 1000) % 10**9) + 1
            self.changed.notify_all()

    def create(self, color: str = "w", elo: int | None = 1200, opening: str | None = None,
               time_control: str | None = None, engine: str | None = None) -> dict:  # fmt: skip
        engine = engine or "stockfish"
        if engine not in ENGINES:
            raise GameError(f"engine must be one of {', '.join(ENGINES)}")
        clock_ms = increment_ms = None
        if time_control and time_control != "untimed":
            try:
                minutes, _, seconds = str(time_control).partition("+")
                clock_ms, increment_ms = int(float(minutes) * 60000), int(float(seconds or 0) * 1000)
            except ValueError:
                raise GameError(f'time control must be "untimed" or minutes+seconds like "10+3", not {time_control!r}') from None
            if clock_ms <= 0 or increment_ms < 0:
                raise GameError("a time control needs more than no time")
        if color == "random":
            color = self.rng.choice("wb")
        if color not in ("w", "b"):
            raise GameError("color must be w, b or random")
        if elo is not None:
            try:
                elo = int(elo)
            except (TypeError, ValueError):
                raise GameError("elo must be a number" + ("" if engine == "allie" else ", or null for full strength")) from None
        if engine == "allie":
            why = allie.missing(self.cfg)
            if why:
                raise GameError(why)
            if elo is None or not allie.ELO_MIN <= elo <= allie.ELO_MAX:
                raise GameError(f"Allie plays as a player rated {allie.ELO_MIN} to {allie.ELO_MAX}: elo must be in that range")
            # Allie reads the time control as one of the settings its games were played at
            if clock_ms and not (clock_ms in (15000, 30000, 45000, 90000) or clock_ms % 60000 == 0 and clock_ms <= 10800000):
                raise GameError("against Allie the clock starts at a whole number of minutes (up to 180), or 15, 30, 45 or 90 seconds")
            if clock_ms and (increment_ms % 1000 or increment_ms > 180000):
                raise GameError("against Allie the increment is a whole number of seconds, up to 180")
        elif elo is not None and not opponent.ELO_MIN <= elo <= opponent.SF_MAX:
            raise GameError(f"elo must be between {opponent.ELO_MIN} and {opponent.SF_MAX}, or null for full strength")
        if opening:
            if opening not in openings.OPENINGS:
                raise GameError(f"no opening {opening!r}")
            if openings.OPENINGS[opening][1] == color:
                raise GameError(f"{openings.name_of(opening)} is an opening for {'White' if color == 'w' else 'Black'}: "
                                "the opponent can only play it with the other colour")  # fmt: skip
        game_id = secrets.token_hex(4)
        now = time.time()
        self.store.add_game(
            {
                "id": game_id,
                "created_at": now,
                "updated_at": now,
                "user_color": color,
                "engine": engine,
                "elo": elo,
                "opening": opening or None,
                "start_fen": START_FEN,
                "time_control": time_control if clock_ms else None,
                "clock_ms": clock_ms,
                "opp_clock_ms": clock_ms if engine == "allie" else None,  # Allie plays to the same clock as you
                "increment_ms": increment_ms or 0,
                "session_id": str(uuid.uuid4()),
            }
        )
        self._enqueue(game_id, 0)
        return self.state(game_id)

    def move(self, game_id: str, text: str) -> dict:
        with self.lock:
            self._tick(game_id)  # the time this move took; it may have run out
            row = self._row(game_id)
            moves = self.store.moves(game_id)
            game = self._game(row, moves)
            if row["status"] != "active":
                lost = (row["result"] == "0-1") == (row["user_color"] == "w")
                on_time = f": {'you' if lost else 'the opponent'} ran out of time" if row["reason"] == "time" else ""
                raise GameError("the game is over" + on_time)
            if game.position.turn != row["user_color"]:
                raise GameError("it is not your move")
            try:
                uci = game.position.parse(str(text))
            except ValueError:
                raise GameError(f"{text!r} is not a legal move here") from None
            left = None
            if row["clock_ms"] is not None:
                self.clocks.pop(game_id, None)
                left = row["clock_ms"] + row["increment_ms"]
                self.store.run("UPDATE games SET clock_ms=?, paused=0 WHERE id=?", (left, game_id))
            if row["draw_offer"] is not None:
                self.store.run("UPDATE games SET draw_offer=NULL WHERE id=?", (game_id,))  # playing on declines it
            self._apply(row, moves, game, uci, "user", left)
        return self.state(game_id)

    # ---------- the clocks ----------

    def _tick(self, game_id: str, look: bool = True) -> None:
        """Charge the clock of the side to move for the time since it was last charged, and say whether it runs
        from here.

        Yours runs while it is your move, the game is on, you have not paused it, and you have made your first
        move. The opponent's, when it has one (Allie), runs by the same rules on its own moves, from the moment it
        is at work on one (not while its model is being loaded). A page waiting on the game looks at the clock
        every second (``look``), so when no page is, nothing is charged: a gap longer than AWAY_S counts as time
        away. The opponent's reply asks too, to move on time, but it is not a page: between a page's looks it is
        charged only while the last of them is FRESH_S old at most. Called with the lock held.
        """
        row = self.store.game(game_id)
        if row is None or row["clock_ms"] is None:
            return
        now = time.monotonic()
        at = self.clocks.pop(game_id, None)
        seen = self.looked.get(game_id)
        if look:
            self.looked[game_id] = now
        if row["status"] != "active":
            return
        moves = self.store.moves(game_id)
        mine = self._game(row, moves).position.turn == row["user_color"]
        if not mine and row["opp_clock_ms"] is None:
            return  # an opponent with no clock
        column = "clock_ms" if mine else "opp_clock_ms"
        left = row[column]
        watched = look or (seen is not None and now - seen <= FRESH_S)
        if at is not None and watched and now - at <= AWAY_S:
            spent = int((now - at) * 1000)
            now = at + spent / 1000  # the fraction of a millisecond left over is charged next time
            left = max(0, left - spent)
            self.store.run(f"UPDATE games SET {column}=? WHERE id=?", (left, game_id))
            if not mine and game_id in self.replies:
                self.replies[game_id]["spent"] += spent
        if left <= 0:
            result = "0-1" if (row["user_color"] == "w") == mine else "1-0"
            self.store.update_game(game_id, status="finished", result=result, reason="time")
            self.epochs[game_id] = self.epochs.get(game_id, 0) + 1
            self._bump(game_id)
            return
        if mine:
            started = any(m["source"] == "user" for m in moves)
        else:
            started = self._opp_started(moves) and self.replies.get(game_id, {}).get("clocked", False)
        if started and not row["paused"] and (look or watched):
            self.clocks[game_id] = now

    @staticmethod
    def _opp_started(moves: list[dict]) -> bool:
        """The opponent has made its first move: like yours, its clock runs from the move after."""
        return any(m["source"] != "user" for m in moves)

    def pause(self, game_id: str, paused: bool) -> dict:
        with self.lock:
            self._tick(game_id)
            row = self._row(game_id)
            if row["clock_ms"] is None:
                raise GameError("this game has no clock")
            self.store.run("UPDATE games SET paused=? WHERE id=?", (1 if paused else 0, game_id))
            self.clocks.pop(game_id, None)
            self._tick(game_id)
            self._bump(game_id)
        return self.state(game_id)

    def _apply(self, row: dict, moves: list[dict], game: Game, uci: str, source: str, clock_ms: int | None = None) -> None:
        """Play a move that is known to be legal, and end the game if it does. ``clock_ms``: what the move left on
        its mover's clock. Called with the lock held."""
        san = game.position.san(uci)
        fen = game.position.push(uci).fen()
        ply = len(moves) + 1
        self.store.add_move(row["id"], ply, uci, san, fen, source, clock_ms)
        moves = self.store.moves(row["id"])
        game = self._game(row, moves)
        self.epochs[row["id"]] = self.epochs.get(row["id"], 0) + 1
        over = game.outcome()
        if over:
            self.store.update_game(row["id"], status="finished", result=over[0], reason=over[1])
        self._enqueue(row["id"], ply)
        self._bump(row["id"])

    def resign(self, game_id: str) -> dict:
        with self.lock:
            row = self._row(game_id)
            if row["status"] != "active":
                raise GameError("the game is over")
            result = "0-1" if row["user_color"] == "w" else "1-0"
            self.store.update_game(game_id, status="finished", result=result, reason="resignation")
            self.epochs[game_id] = self.epochs.get(game_id, 0) + 1
            self._bump(game_id)
        return self.state(game_id)

    def draw(self, game_id: str, accept: bool) -> dict:
        """Answer the draw the opponent offered with its last move: take it, or play on."""
        with self.lock:
            self._tick(game_id)
            row = self._row(game_id)
            if row["status"] != "active" or row["draw_offer"] is None or row["draw_offer"] != len(self.store.moves(game_id)):
                raise GameError("there is no draw offer to answer")
            if accept:
                self.clocks.pop(game_id, None)
                self.store.update_game(game_id, status="finished", result="1/2-1/2", reason="agreement", draw_offer=None)
                self.epochs[game_id] = self.epochs.get(game_id, 0) + 1
            else:
                self.store.run("UPDATE games SET draw_offer=NULL WHERE id=?", (game_id,))
            self._bump(game_id)
        return self.state(game_id)

    def takeback(self, game_id: str) -> dict:
        """Undo your last move (and the reply to it, if there was one)."""
        with self.lock:
            row = self._row(game_id)
            moves = self.store.moves(game_id)
            mine = [m["ply"] for m in moves if m["source"] == "user"]
            if not mine:
                raise GameError("you have not made a move to take back")
            if row["reason"] == "time":
                raise GameError("the game ended on time: there is no move to take back")
            keep = mine[-1] - 1
            self.store.truncate(game_id, keep)
            self.store.update_game(
                game_id, status="active", result=None, reason=None, analysed_ply=min(row["analysed_ply"], keep), draw_offer=None
            )
            self.epochs[game_id] = self.epochs.get(game_id, 0) + 1
            self.thinking.discard(game_id)
            self.clocks.pop(game_id, None)
            self.replies.pop(game_id, None)
            self.trouble.pop(game_id, None)
            self._chess.pop(game_id, None)
            self._bump(game_id)
        return self.state(game_id)

    def delete(self, game_id: str) -> dict:
        with self.lock:
            self._row(game_id)
            if self.tutor:
                self.tutor.forget(game_id)
            self.store.delete_game(game_id)
            self.epochs[game_id] = self.epochs.get(game_id, 0) + 1
            for kept in (self._chess, self.clocks, self.looked, self.replies, self.trouble):
                kept.pop(game_id, None)
            self._bump(game_id)
        self.allie.forget(game_id)
        return {"ok": True}

    # ---------- the opponent ----------

    def _keep_going(self, row: dict, moves: list[dict], evals: dict[int, dict], game: Game) -> None:
        """Whatever a game is owed and nobody is doing (after a restart, say): the opponent's move, missing analysis."""
        gid = row["id"]
        failed = self.trouble.get(gid)
        waiting = failed is not None and time.monotonic() - failed[0] < RETRY_S
        if row["status"] == "active" and game.position.turn != row["user_color"] and gid not in self.thinking and not waiting:
            self.thinking.add(gid)
            epoch = self.epochs.setdefault(gid, 0)
            threading.Thread(target=self._reply, args=(gid, epoch), name=f"opponent-{gid}", daemon=True).start()
        if row["status"] == "active" and row["engine"] == "allie":
            self.allie.warm()  # someone is on a game against it
        for ply in range(len(moves) + 1):
            if ply not in evals:
                self._enqueue(gid, ply)

    def _allie_game(self, row: dict, moves: list[dict]) -> dict:
        """A game as Allie is told it: both ratings, the time control, and each mover's clock after each move.

        Allie is not told who it plays, so the other side is given its own rating. A side with no clock of its
        own (yours before this was recorded, Stockfish's always) reads as one that stays full.
        """
        base = inc = None
        if row["clock_ms"] is not None:
            minutes, _, seconds = row["time_control"].partition("+")
            base, inc = round(float(minutes) * 60), round(float(seconds or 0))
        timed = base is not None
        return {
            "moves": [m["uci"] for m in moves],
            "clocks": [m["clock_ms"] / 1000 if timed and m["clock_ms"] is not None else None for m in moves],
            "white_elo": row["elo"],
            "black_elo": row["elo"],
            "base": base,
            "inc": inc,
        }

    def _reply(self, game_id: str, epoch: int) -> None:
        started = time.monotonic()
        # milliseconds its clock was charged for this move; whether it may run; whether its model is still being read
        reply = {"spent": 0, "clocked": False, "loading": False}
        try:
            row = self._row(game_id)
            moves = self.store.moves(game_id)
            game = self._game(row, moves)
            self.replies[game_id] = reply
            source, think, offer, asked = "book", MIN_REPLY_S, False, False
            move = openings.book_move(row["opening"], game.position, self.rng)
            if move is None:
                source = "engine"
            if move is None and row["engine"] == "allie":
                if not self.allie.ready:
                    reply["loading"] = True
                    self._bump(game_id)
                self.allie.load()  # not on its clock
                reply["loading"] = False
                self._clocked(game_id, epoch, reply)
                row = self._row(game_id)
                left = None if row["opp_clock_ms"] is None else row["opp_clock_ms"] / 1000
                found = self.allie.decide(game_id, **self._allie_game(row, moves), clock=left)
                if found.get("resign"):
                    self._gives_up(game_id, epoch)  # instead of moving
                else:
                    move, offer, asked = found["move"], bool(found.get("offer_draw")), True
                if left is not None:
                    # Its think time, the search that went into the move included, is what its clock is charged.
                    think = max(found["think"], min(MIN_REPLY_S, left / 20))
            elif move is None:
                ucis = [m["uci"] for m in moves]
                move = opponent.pick(self.opp, row["start_fen"], ucis, row["elo"], self.cfg.opponent_ms, self.rng)
            else:
                self._clocked(game_id, epoch, reply)
            played = move is not None and self._deliver(game_id, epoch, reply, started, move, source, think, offer)
            self.trouble.pop(game_id, None)
            if played and asked:
                self._second_thoughts(game_id, epoch + 1)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            self.trouble[game_id] = (time.monotonic(), str(e) if isinstance(e, (AllieError, EngineError)) else f"{type(e).__name__}: {e}")
        finally:
            with self.lock:
                if self.epochs.get(game_id, 0) in (epoch, epoch + 1):
                    self.thinking.discard(game_id)
                if self.replies.get(game_id) is reply:
                    del self.replies[game_id]
            self._bump(game_id)

    def _clocked(self, game_id: str, epoch: int, reply: dict) -> None:
        """The opponent is at work on its move: from here its clock, if it has one, runs."""
        with self.lock:
            if self.epochs.get(game_id, 0) != epoch:
                return
            reply["clocked"] = True
            self._tick(game_id, look=False)
        self._bump(game_id)

    def _gives_up(self, game_id: str, epoch: int) -> None:
        """The opponent resigns, unless the game has moved on since it made up its mind."""
        with self.lock:
            row = self.store.game(game_id)
            if row is None or self.epochs.get(game_id, 0) != epoch or row["status"] != "active":
                return
            self.clocks.pop(game_id, None)
            result = "1-0" if row["user_color"] == "w" else "0-1"
            self.store.update_game(game_id, status="finished", result=result, reason="resignation", draw_offer=None)
            self.epochs[game_id] = epoch + 1
            self._bump(game_id)

    def _second_thoughts(self, game_id: str, epoch: int) -> None:
        """Allie has just moved: it may resign on seeing the position it has reached, as a person might."""
        with self.lock:
            row = self.store.game(game_id)
            if row is None or self.epochs.get(game_id, 0) != epoch or row["status"] != "active":
                return
            moves = self.store.moves(game_id)
        left = None if row["opp_clock_ms"] is None else row["opp_clock_ms"] / 1000
        try:
            if self.allie.concede(game_id, **self._allie_game(row, moves), clock=left, white=row["user_color"] == "b"):
                self._gives_up(game_id, epoch)
        except AllieError:
            traceback.print_exc(file=sys.stderr)  # its move stands; the next one says what is wrong, if anything is

    def _deliver(self, game_id: str, epoch: int, reply: dict, started: float, move: str, source: str, think: float,
                 offer: bool = False) -> bool:  # fmt: skip
        """Play the opponent's move once it has thought ``think`` seconds on it, with a draw offer if it makes
        one; False if the game moved on without it.

        With a clock of its own that is time on that clock, which stops when yours would: no page on the game,
        or the game paused. The move then waits with it. Without one (or on its first move, which like yours is
        not timed) it is time since the reply was started.
        """
        while not self.stop.is_set():
            with self.lock:
                if self.epochs.get(game_id, 0) != epoch:
                    return False  # taken back, resigned or deleted while it was thinking
                row = self._row(game_id)
                moves = self.store.moves(game_id)
                if row["opp_clock_ms"] is not None and self._opp_started(moves):
                    self._tick(game_id, look=False)
                    row = self._row(game_id)
                    left, running = think - reply["spent"] / 1000, game_id in self.clocks
                else:
                    left, running = think - (time.monotonic() - started), True
                if row["status"] != "active":
                    return False  # out of time
                if left <= 0:
                    after = None
                    if row["opp_clock_ms"] is not None:
                        self.clocks.pop(game_id, None)
                        after = row["opp_clock_ms"] + row["increment_ms"]
                        self.store.run("UPDATE games SET opp_clock_ms=? WHERE id=?", (after, game_id))
                    self._apply(row, moves, self._game(row, moves), move, source, after)
                    self.thinking.discard(game_id)  # it is your move from here, whatever this thread still does
                    if offer and self._row(game_id)["status"] == "active":
                        self.store.run("UPDATE games SET draw_offer=? WHERE id=?", (len(moves) + 1, game_id))
                    return True
            time.sleep(max(0.005, min(left, 0.25)) if running else 0.25)
        return False

    # ---------- analysis ----------

    def _enqueue(self, game_id: str, ply: int) -> None:
        if (game_id, ply) not in self.queued:
            self.queued.add((game_id, ply))
            self.queue.append((game_id, ply))
            self.work.set()

    def _analyse_loop(self) -> None:
        while not self.stop.is_set():
            if not self.queue:
                self.work.clear()
                self.work.wait(5)
                continue
            game_id, ply = self.queue.popleft()
            try:
                self._analyse(game_id, ply)
            except GameError:
                pass  # the game was deleted
            except Exception:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                time.sleep(1)
            finally:
                self.queued.discard((game_id, ply))

    def _analyse(self, game_id: str, ply: int) -> None:
        with self.lock:
            row = self._row(game_id)
            moves = self.store.moves(game_id)
            if ply > len(moves):
                return
            if ply in self._evals(row, moves):
                return
            game = self._game(row, moves)
            pos = game.positions[ply]
            ucis = [m["uci"] for m in moves[:ply]]
            last = ply == len(moves)
        fen = pos.fen()
        lines: list[dict] = []
        depth, cp, mate = 0, None, None
        if not pos.legal_moves():
            if pos.in_check():
                mate, value = 0, (-MATE_CP if pos.turn == "w" else MATE_CP)
            else:
                cp, value = 0, 0
        elif last and row["status"] == "finished" and row["reason"] in _RULE_DRAWS:
            cp, value = 0, 0
        else:
            res = self.review.search(row["start_fen"], ucis, ms=self.cfg.review_ms, multipv=3)
            if not res.lines:
                return
            for ln in res.lines:
                cut = ln.moves[:PV_PLIES]
                lines.append({"moves": cut, "san": _line_sans(pos, cut), "cp": ln.cp, "mate": ln.mate, "value": ln.value})
            depth, cp, mate, value = res.depth, res.lines[0].cp, res.lines[0].mate, res.lines[0].value
        with self.lock:
            row = self._row(game_id)
            now = self.store.moves(game_id)
            current = row["start_fen"] if ply == 0 else (now[ply - 1]["fen"] if ply <= len(now) else None)
            if current != fen:
                return  # taken back while it was being analysed
            self.store.put_eval(game_id, ply, fen, depth, value, cp, mate, lines)
        self._bump(game_id)

    # ---------- what Claude is given ----------

    def brief(self, game_id: str) -> str:
        """The game in a paragraph: what a Claude session is told when it is opened on it."""
        row = self._row(game_id)
        moves = self.store.moves(game_id)
        color = "White" if row["user_color"] == "w" else "Black"
        if not row["time_control"]:
            clock = "no clock"
        elif row["opp_clock_ms"] is None:
            clock = f"the student has a clock ({row['time_control']}), the opponent has none"
        else:
            clock = f"both sides have a clock ({row['time_control']})"
        if row["status"] == "finished":
            where = f"It is over: {row['result']} by {row['reason']}, after {len(moves)} half-moves."
        elif not moves:
            where = "No move has been played yet."
        else:
            where = f"It is in progress, {len(moves)} half-moves in."
        return (
            f"Game {row['id']}: the student plays {color} against {self._versus(row)}; opening the opponent was "
            f"asked to steer for: {openings.name_of(row['opening'])}; {clock}. {where}"
        )

    @staticmethod
    def _versus(row: dict) -> str:
        """The opponent as Claude is told of it."""
        if row["engine"] == "allie":
            return (
                f"{allie.NAME}, a model of human play (not a weakened engine), playing as a player rated "
                f"{row['elo']} (Lichess scale) would"
            )
        return "Stockfish " + (f"limited to about {row['elo']} Elo" if row["elo"] else "at full strength")

    def _review_text(self, row: dict, game: Game, moves: list[dict], evals: dict, m: dict, grade: dict) -> str:
        ply = m["ply"]
        before, after = evals[ply - 1], evals[ply]
        pos, nxt = game.positions[ply - 1], game.positions[ply]
        white = pos.turn == "w"
        side = "White" if white else "Black"
        chance = lambda e: win_percent(e["value"]) if white else 100 - win_percent(e["value"])  # noqa: E731
        out = [
            f"Ply {ply}: {_numbered(pos.fullmove, pos.turn, [m['san']])}, played by "
            f"{'the student' if m['source'] == 'user' else 'the opponent' + (' from its opening book' if m['source'] == 'book' else '')} ({side}).",
            f"Position before it: {pos.fen()}",
            f"Engine at depth {max(before['depth'], after['depth'])}: {show(before['cp'], before['mate'])} before, "
            f"{show(after['cp'], after['mate'])} after (for White). Cost: {grade['cpl']} centipawns; {side}'s winning "
            f"chances {chance(before):.0f}% -> {chance(after):.0f}%. Grade: {grade['verdict']}.",
        ]
        if grade["verdict"] == "best":
            out.append("It was the engine's first choice.")
        choices = [
            f"{ln['san'][0]} ({show(ln['cp'], ln['mate'])}): {_numbered(pos.fullmove, pos.turn, ln['san'])}"
            for ln in before["lines"]
            if ln["san"]
        ]
        if choices:
            out.append("Engine's choices in that position: " + "; ".join(choices))
        if after["lines"] and after["lines"][0]["san"]:
            line = _numbered(nxt.fullmove, nxt.turn, after["lines"][0]["san"])
            out.append(f"Engine's main line after {m['san']}: {line}")
        if ply < len(moves):
            out.append(f"Played next: {_numbered(nxt.fullmove, nxt.turn, [moves[ply]['san']])}.")
        elif row["status"] == "finished":
            out.append(f"That is where the game ended: {row['result']} by {row['reason']}.")
        return "\n".join(out)

    # ---------- the tutor's tools ----------

    def _resolve(self, game_id: str, ply, fen, extra) -> tuple[str, list[str], Position, str]:
        """The position a tool call is about: a FEN, or a point in the game, then ``extra`` moves played on it."""
        if fen:
            try:
                pos = Position.from_fen(str(fen))
            except ValueError as e:
                raise GameError(str(e)) from None
            base, played, label = pos.fen(), [], "the given position"
        else:
            row = self._row(game_id)
            moves = self.store.moves(game_id)
            game = self._game(row, moves)
            n = len(moves) if ply is None else int(ply)
            if not 0 <= n <= len(moves):
                raise GameError(f"ply must be 0 to {len(moves)} (the game has {len(moves)} half-moves so far)")
            pos, base, played = game.positions[n], row["start_fen"], [m["uci"] for m in moves[:n]]
            label = "the start position" if n == 0 else f"the position after ply {n} ({_numbered(game.positions[n - 1].fullmove, game.positions[n - 1].turn, [moves[n - 1]['san']])})"
        added = []
        for text in extra or []:
            try:
                uci = pos.parse(str(text))
            except ValueError:
                legal = " ".join(sorted(pos.san(m) for m in pos.legal_moves()))
                raise GameError(f"{text!r} is not legal after {' '.join(added) or 'no extra moves'}. Legal: {legal}") from None
            added.append(pos.san(uci))
            played.append(uci)
            pos = pos.push(uci)
        if added:
            label += " then " + " ".join(added)
        return base, played, pos, label

    def tool_position(self, game_id: str, ply=None, fen=None, moves=None) -> str:
        _, _, pos, label = self._resolve(game_id, ply, fen, moves)
        return f"{label[0].upper()}{label[1:]}:\n{describe(pos)}"

    def tool_analyze(self, game_id: str, ply=None, fen=None, moves=None, seconds=2.0, lines=3, candidates=None) -> str:
        base, played, pos, label = self._resolve(game_id, ply, fen, moves)
        if not pos.legal_moves():
            return f"{label[0].upper()}{label[1:]} ({pos.fen()}): no legal moves, it is {'checkmate' if pos.in_check() else 'stalemate'}."
        only = None
        if candidates:
            try:
                only = [pos.parse(str(c)) for c in candidates]
            except ValueError as e:
                raise GameError(str(e)) from None
        ms = int(max(0.1, min(float(seconds or 2.0), 20.0)) * 1000)
        n = len(only) if only else max(1, min(int(lines or 3), 8))
        res = self.explore.search(base, played, ms=ms, multipv=n, only=only)
        side = "White" if pos.turn == "w" else "Black"
        out = [f"{label[0].upper()}{label[1:]}: {pos.fen()}", f"{side} to move. {self.explore.name}, depth {res.depth}, {res.ms / 1000:.1f} s."]
        for i, ln in enumerate(res.lines, 1):
            sans = _line_sans(pos, ln.moves[:14])
            out.append(f"{i}. {show(ln.cp, ln.mate):>6}  {_numbered(pos.fullmove, pos.turn, sans)}")
        out.append("Scores are for White (positive: White is better); #n is a forced mate in n, #-n a mate for Black.")
        return "\n".join(out)

    def tool_humans(self, game_id: str, ply=None, moves=None, elo=None, top=5, line=0) -> str:
        """What people play in a position, by Allie: the likeliest moves of players of a rating. Not an evaluation."""
        why = allie.missing(self.cfg)
        if why:
            raise GameError(why)
        row = self._row(game_id)
        if elo is None:
            elo = row["elo"] if row["engine"] == "allie" else 1500
        try:
            elo, top, line = int(elo), max(1, min(int(top or 5), 15)), max(0, min(int(line or 0), 16))
        except (TypeError, ValueError):
            raise GameError("elo, top and line are whole numbers") from None
        if not 400 <= elo <= 3200:
            raise GameError("elo must be between 400 and 3200")
        _, played, pos, label = self._resolve(game_id, ply, None, moves)
        head = f"{label[0].upper()}{label[1:]}: {pos.fen()}"
        if not pos.legal_moves():
            return f"{head}\nNo legal moves: it is {'checkmate' if pos.in_check() else 'stalemate'}."
        # The game as Allie reads it, with the clocks of the moves that were played and none for the "what if" ones.
        known = self.store.moves(game_id)[: len(played) - len(moves or [])]
        told = self._allie_game({**row, "elo": elo}, known)
        told["moves"], told["clocks"] = played, told["clocks"] + [None] * (len(played) - len(known))
        try:
            found = self.allie.predict(**told, top=top, line=line)
        except AllieError as e:
            raise GameError(str(e)) from None
        side = "White" if pos.turn == "w" else "Black"
        tc = f", at {row['time_control']}" if row["time_control"] else ""
        out = [head, f"{side} to move. What players rated {elo} play here{tc}, as {allie.NAME} predicts it:"]
        for i, (uci, p) in enumerate(found["moves"], 1):
            out.append(f"{i:>2}. {pos.san(uci):<7} {100 * p:4.0f}%")
        covered = sum(p for _, p in found["moves"])
        if found["legal"] > len(found["moves"]):
            out.append(f"({len(found['moves'])} of {found['legal']} legal moves, {100 * covered:.0f}% of what is played.)")
        w, d, lost = found["wdl"]
        out.append(
            f"How such a game ends from here, for {side}: win {100 * w:.0f}%, draw {100 * d:.0f}%, loss {100 * lost:.0f}%."
            + (f" Typical think on this move: {found['think_time']:.0f} s." if found.get("think_time") is not None and row["time_control"] else "")
        )
        if found["line"]:
            sans, at = [], pos
            for uci, p in found["line"]:
                sans.append(f"{at.san(uci)} ({100 * p:.0f}%)")
                at = at.push(uci)
            out.append("Likeliest continuation, each side's most played move: " + _numbered(pos.fullmove, pos.turn, sans))
        out.append("These are predictions of what people play, mistakes included, not judgements of the moves: check any line with analyze.")
        return "\n".join(out)

    def tool_review(self, game_id: str, from_ply=None, to_ply=None, who: str = "student") -> str:
        """The analysis on file for moves already played: evaluations either side of each, and the engine's lines."""
        with self.lock:
            row = self._row(game_id)
            moves = self.store.moves(game_id)
            game = self._game(row, moves)
            evals = self._evals(row, moves)
        lo = 1 if from_ply is None else int(from_ply)
        hi = len(moves) if to_ply is None else int(to_ply)
        out, waiting = [], 0
        for m in moves:
            ply = m["ply"]
            if not lo <= ply <= hi or (who != "all" and m["source"] != "user"):
                continue
            if ply not in evals or ply - 1 not in evals:
                waiting += 1
                continue
            grade = self._grade(m, evals[ply - 1], evals[ply], game.positions[ply - 1].turn == "w")
            out.append(self._review_text(row, game, moves, evals, m, grade))
        if waiting:
            out.append(f"({waiting} move(s) in that range are still being analysed by the engine: ask again in a few seconds.)")
        if not out:
            return "No moves in that range." if moves else "No move has been played yet."
        out.append("Evaluations are for White (positive: White is better); #n is a forced mate in n, #-n a mate for Black.")
        return "\n\n".join(out)

    def tool_game(self, game_id: str) -> str:
        with self.lock:
            row = self._row(game_id)
            moves = self.store.moves(game_id)
            game = self._game(row, moves)
            evals = self._evals(row, moves)
            annotated = self._annotated(row, moves, evals, game)
        color = "White" if row["user_color"] == "w" else "Black"
        out = [
            f"Game {row['id']}: the student plays {color} against {self._versus(row)}.",
            f"Opening the opponent was asked to steer for: {openings.name_of(row['opening'])}.",
        ]
        if row["clock_ms"] is not None:
            mmss = lambda ms: f"{ms // 60000}:{ms // 1000 % 60:02d}"  # noqa: E731
            theirs = "" if row["opp_clock_ms"] is None else f", the opponent {mmss(row['opp_clock_ms'])}"
            out.append(f"Clocks ({row['time_control']}): the student has {mmss(row['clock_ms'])} left{theirs}.")
        pos = game.position
        if row["status"] == "finished":
            out.append(f"Finished: {row['result']} by {row['reason']}.")
        else:
            out.append(f"In progress: {'the student' if pos.turn == row['user_color'] else 'the opponent'} is to move.")
            if row["draw_offer"] == len(moves) and pos.turn == row["user_color"]:
                out.append("The opponent offered a draw with its last move: the student can take it or play on.")
        if annotated:
            out.append("")
            out.append("ply  move        by        eval(White)  loss  grade       engine preferred")
        for m in annotated:
            p = game.positions[m["ply"] - 1]
            who = "student" if m["source"] == "user" else "opponent" + ("*" if m["source"] == "book" else "")
            ev = show(m["eval"]["cp"], m["eval"]["mate"]) if "eval" in m else "..."
            pref = m["best"]["san"] if m.get("best") and m["best"]["uci"] != m["uci"] else ""
            out.append(
                f"{m['ply']:>3}  {_numbered(p.fullmove, p.turn, [m['san']]):<11} {who:<9} {ev:>10}  "
                f"{m.get('cpl', '...')!s:>4}  {m.get('verdict', '...'):<11} {pref}"
            )
        if any(m["source"] == "book" for m in annotated):
            out.append("(* a move from the opponent's opening book)")
        out += ["", "Current position:", describe(pos)]
        return "\n".join(out)
