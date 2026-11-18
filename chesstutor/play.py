"""Games: making moves, the opponent's replies, your clock, and the engine's analysis of every position.

Three engine processes: `review` analyses each position of a game once, at full strength; `explore` answers the
tutor's own questions; `opp` plays the opponent at its chosen strength. Every change to a game bumps its version,
which is what a page waiting on `state(since=...)` wakes up on.
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

from . import openings, opponent
from .chess import START_FEN, Game, Position, describe
from .config import Config
from .engine import MATE_CP, Engine, EngineError, loss, show, verdict, win_percent
from .store import Store

TIME_CONTROLS = ("untimed", "10+3")  # minutes + seconds added per move; any "M+S" is accepted
AWAY_S = 3.0  # a gap this long between two looks at a clock means no page was open: it is not charged
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
        self.lock = threading.RLock()  # one change to a game at a time
        self.changed = threading.Condition()
        self.versions: dict[str, int] = {}
        self.epochs: dict[str, int] = {}  # bumped by every move and take-back: a reply computed for an older one is dropped
        self.thinking: set[str] = set()
        self.clocks: dict[str, float] = {}  # games whose clock is running, and when it was last charged
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
        for e in (self.review, self.explore, self.opp):
            e.close()

    # ---------- reading ----------

    def meta(self) -> dict:
        try:
            engine = self.review.version()
        except EngineError as e:
            engine = f"missing ({e})"
        return {
            "engine": engine,
            "elo": {"min": opponent.ELO_MIN, "max": opponent.SF_MAX, "native": opponent.SF_MIN, "default": 1200},
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

    def summary(self, row: dict, plies: int | None = None, fen: str | None = None) -> dict:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "user_color": row["user_color"],
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
            start = evals.get(0)
            acpl = {}
            for who in ("user", "opponent"):
                losses = [m["cpl"] for m in annotated if "cpl" in m and (m["source"] == "user") == (who == "user")]
                acpl[who] = round(sum(losses) / len(losses)) if losses else None
            out = self.summary(row, len(moves), pos.fen())
            out.update(
                version=self.versions.setdefault(game_id, int(time.time() * 1000) % 10**9),
                start_fen=row["start_fen"],
                start_eval={k: start[k] for k in ("cp", "mate", "value", "depth")} if start else None,
                moves=annotated,
                turn=pos.turn,
                check=pos.in_check(),
                legal=self._legal(row, game) if users_turn else {},
                thinking=game_id in self.thinking,
                acpl=acpl,
                pgn=self._pgn(row, moves),
                clock=None
                if row["clock_ms"] is None
                else {
                    "ms": row["clock_ms"],
                    "increment_ms": row["increment_ms"],
                    "running": game_id in self.clocks,
                    "paused": bool(row["paused"]),
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
        them = f"Stockfish ({row['elo']} Elo)" if row["elo"] else "Stockfish"
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

    def create(self, color: str = "w", elo: int | None = 1200, opening: str | None = None, time_control: str | None = None) -> dict:
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
                raise GameError("elo must be a number, or null for full strength") from None
            if not opponent.ELO_MIN <= elo <= opponent.SF_MAX:
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
                "elo": elo,
                "opening": opening or None,
                "start_fen": START_FEN,
                "time_control": time_control if clock_ms else None,
                "clock_ms": clock_ms,
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
                raise GameError("the game is over" + (": you ran out of time" if row["reason"] == "time" else ""))
            if game.position.turn != row["user_color"]:
                raise GameError("it is not your move")
            try:
                uci = game.position.parse(str(text))
            except ValueError:
                raise GameError(f"{text!r} is not a legal move here") from None
            if row["clock_ms"] is not None:
                self.clocks.pop(game_id, None)
                self.store.run(
                    "UPDATE games SET clock_ms=clock_ms+increment_ms, paused=0 WHERE id=?", (game_id,)
                )
            self._apply(row, moves, game, uci, "user")
        return self.state(game_id)

    # ---------- your clock ----------

    def _tick(self, game_id: str) -> None:
        """Charge your clock for the time since it was last looked at, and say whether it runs from here.

        It runs while it is your move, the game is on, you have not paused it, and you have made your first move.
        It is looked at every second by any page that is waiting on the game, so when no page is, nothing is
        charged: a gap longer than AWAY_S counts as time away. Called with the lock held.
        """
        row = self.store.game(game_id)
        if row is None or row["clock_ms"] is None:
            return
        now = time.monotonic()
        at = self.clocks.pop(game_id, None)
        if row["status"] != "active":
            return
        left = row["clock_ms"]
        if at is not None and now - at <= AWAY_S:
            left = max(0, left - int((now - at) * 1000))
            self.store.run("UPDATE games SET clock_ms=? WHERE id=?", (left, game_id))
        if left <= 0:
            result = "0-1" if row["user_color"] == "w" else "1-0"
            self.store.update_game(game_id, status="finished", result=result, reason="time")
            self.epochs[game_id] = self.epochs.get(game_id, 0) + 1
            self._bump(game_id)
            return
        moves = self.store.moves(game_id)
        mine = self._game(row, moves).position.turn == row["user_color"]
        if mine and not row["paused"] and any(m["source"] == "user" for m in moves):
            self.clocks[game_id] = now

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

    def _apply(self, row: dict, moves: list[dict], game: Game, uci: str, source: str) -> None:
        """Play a move that is known to be legal, and end the game if it does. Called with the lock held."""
        san = game.position.san(uci)
        fen = game.position.push(uci).fen()
        ply = len(moves) + 1
        self.store.add_move(row["id"], ply, uci, san, fen, source)
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

    def takeback(self, game_id: str) -> dict:
        """Undo your last move (and the reply to it, if there was one)."""
        with self.lock:
            row = self._row(game_id)
            moves = self.store.moves(game_id)
            mine = [m["ply"] for m in moves if m["source"] == "user"]
            if not mine:
                raise GameError("you have not made a move to take back")
            keep = mine[-1] - 1
            self.store.truncate(game_id, keep)
            if row["reason"] == "time":
                raise GameError("the game was lost on time: there is no move to take back")
            self.store.update_game(
                game_id, status="active", result=None, reason=None, analysed_ply=min(row["analysed_ply"], keep)
            )
            self.epochs[game_id] = self.epochs.get(game_id, 0) + 1
            self.thinking.discard(game_id)
            self.clocks.pop(game_id, None)
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
            self._chess.pop(game_id, None)
            self._bump(game_id)
        return {"ok": True}

    # ---------- the opponent ----------

    def _keep_going(self, row: dict, moves: list[dict], evals: dict[int, dict], game: Game) -> None:
        """Whatever a game is owed and nobody is doing (after a restart, say): the opponent's move, missing analysis."""
        gid = row["id"]
        if row["status"] == "active" and game.position.turn != row["user_color"] and gid not in self.thinking:
            self.thinking.add(gid)
            epoch = self.epochs.setdefault(gid, 0)
            threading.Thread(target=self._reply, args=(gid, epoch), name=f"opponent-{gid}", daemon=True).start()
        for ply in range(len(moves) + 1):
            if ply not in evals:
                self._enqueue(gid, ply)

    def _reply(self, game_id: str, epoch: int) -> None:
        started = time.time()
        try:
            row = self._row(game_id)
            moves = self.store.moves(game_id)
            game = self._game(row, moves)
            ucis = [m["uci"] for m in moves]
            source = "book"
            move = openings.book_move(row["opening"], game.position, self.rng)
            if move is None:
                source = "engine"
                move = opponent.pick(self.opp, row["start_fen"], ucis, row["elo"], self.cfg.opponent_ms, self.rng)
            time.sleep(max(0.0, 0.5 - (time.time() - started)))  # a reply the instant you let go of the piece is jarring
            with self.lock:
                if self.epochs.get(game_id, 0) != epoch or move is None:
                    return  # taken back, resigned or deleted while it was thinking
                row = self._row(game_id)
                if row["status"] == "active":
                    self._apply(row, moves, game, move, source)
        except Exception:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
        finally:
            with self.lock:
                if self.epochs.get(game_id, 0) in (epoch, epoch + 1):
                    self.thinking.discard(game_id)
            self._bump(game_id)

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
        strength = f"limited to about {row['elo']} Elo" if row["elo"] else "at full strength"
        clock = f"the student has a clock ({row['time_control']}), the opponent has none" if row["time_control"] else "no clock"
        if row["status"] == "finished":
            where = f"It is over: {row['result']} by {row['reason']}, after {len(moves)} half-moves."
        elif not moves:
            where = "No move has been played yet."
        else:
            where = f"It is in progress, {len(moves)} half-moves in."
        return (
            f"Game {row['id']}: the student plays {color} against Stockfish {strength}; opening the opponent was "
            f"asked to steer for: {openings.name_of(row['opening'])}; {clock}. {where}"
        )

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
        strength = f"limited to about {row['elo']} Elo" if row["elo"] else "at full strength"
        out = [
            f"Game {row['id']}: the student plays {color} against Stockfish {strength}.",
            f"Opening the opponent was asked to steer for: {openings.name_of(row['opening'])}.",
        ]
        pos = game.position
        if row["status"] == "finished":
            out.append(f"Finished: {row['result']} by {row['reason']}.")
        else:
            out.append(f"In progress: {'the student' if pos.turn == row['user_color'] else 'the opponent'} is to move.")
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
