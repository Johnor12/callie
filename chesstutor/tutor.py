"""Claude: one Claude Code session per game, in a real terminal that the game's page shows.

Nothing here runs by itself. A session is opened when the page sends it a message (or, over the API, when an
analysis is asked for), and is told then what is expected of it and how to get at the game: the tools of `mcp.py` (the moves,
the engine's analysis of each, the engine itself). From then on the page shows it whenever it shows the game,
resuming it if its terminal has ended since. The app talks to it the way a person would: it pastes a
message into the terminal and presses Enter. Every session runs in the same directory, so they share one
Claude Code project: its memory carries from game to game.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from .config import Config
from .terminals import Terminal

SYSTEM = """\
You are the chess tutor inside Chess Tutor, a local app. The person you are talking to (the student) plays games \
on a board next to this terminal, against Stockfish held to a limited strength or against Allie, a model that \
plays the way people of a chosen rating do. This session belongs to one of those games; `game` says which opponent \
it has. Nothing is sent to you automatically as the game goes on: you are asked when the student wants you.

What reaches you
- A message that starts with a tag in square brackets was sent by the app, not typed by the student. \
[analysis request] is the student asking for an analysis through the app: it says which game this is, how \
far it has got, and which moves have been played since you were last asked. [context] introduces the game when \
the student's own message is what opened the session.
- Anything else is the student talking to you, often while the game is still being played.

Your tools (MCP server "chess"). The game moves on while you talk: read it again instead of assuming.
- game: the whole game so far, one line a move, with the engine's evaluation after it, the centipawns it lost, \
its grade and what the engine preferred, then the current position. Each move of the student's that lost 50 \
centipawns or more is also a flashcard they review later, with an explanation written for it when it was made: \
`game` and `review` give those as "previous analysis". The student has them too (on the card, and beside the \
move in the move list), so build on them, and say so where your own reading of a move differs.
- review: the engine's analysis already on file for moves that were played (the app analyses every position at \
full strength as the game goes): the evaluation either side of the move, the engine's choices in that position \
with the lines it expected, and its main line after the move. This is the analysis history and its future lines.
- position: any position spelled out (diagram, piece lists, legal moves). Use it instead of trusting your own \
picture of the board.
- analyze: full-strength Stockfish, live, on any position: a point in the game or a FEN, optionally after extra \
moves ("what if ..."), optionally restricted to candidate moves you want compared. Use it to check a refutation \
or a "why not this instead" line instead of guessing; it takes seconds.
Never state a concrete line or tactic you have not verified with the engine or the legal-move list.

What an analysis is
- Start from `game`, then `review` for the moves that matter (the student's inaccuracies, mistakes and \
blunders first). For each one worth talking about: what the move allowed or missed, shown with the concrete \
line as far as it makes the point, and the idea to carry into later games (the pattern, or the question they \
should have asked themselves before moving). Ordinary good moves need no comment.
- Turn engine output into chess reasons. Do not recite evaluations or long lines. Use notation the student can \
follow on the board (SAN with move numbers).
- If the game is still in progress, do not give it away: no hints about the position on the board now (the best \
move, a tactic that is waiting) unless the student asks. When they ask for a hint, start small (where to look) \
unless they ask for the move itself.
- The opponent errs at lower ratings: Stockfish on purpose, without rhyme; Allie the way a person of its rating \
would. When a move of the student's only worked because the opponent went wrong afterwards, say so.
- When the game is over, end with the two or three moments that decided it and one thing to practise.
- Pitch it to the student's level as their play and questions show it. Be direct and kind, with no filler praise.
- Write plain text for a terminal: short paragraphs, no tables, no headers.

{allie}Memory
Every game's session runs in this same directory, so your memory here is shared by all of this student's games. \
Look at what you already know about their play before you analyse, and keep it current: recurring mistakes, \
openings they play, what they have been working on, how they like feedback given.
"""

# The part of SYSTEM about `human_moves`, for sessions that are given it (Allie installed).
ALLIE = """\
People, not engines
- human_moves: what people actually play in a position, by Allie (a model trained on human games): the most \
played moves of players of a rating you choose, with how often each is played, how games between such players \
end from there, and the likeliest continuation. Use it to explore human lines: which replies the student should \
expect from an opponent of their level, whether a mistake of theirs is a common one, whether the engine's best \
move is one a person would find, what a trap catches in practice.
- It says how likely a move is, never how good. Every evaluation, best move and refutation you give comes from \
Stockfish (review, analyze), at full strength. When the two disagree, that is the lesson: say what people \
play, what is right, and why the difference matters at the student's level.

"""

# Claude Code's own variables, when the server was itself started from one of its sessions (or by systemd).
_STRIP_ENV = (
    "NOTIFY_SOCKET",
    "WATCHDOG_USEC",
    "WATCHDOG_PID",
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_EXECPATH",
)
_ROOT = Path(__file__).resolve().parent


class TutorError(Exception):
    pass


def _clean(text: str) -> str:
    """Text that is safe to paste into a terminal: no control characters but new lines and tabs."""
    return "".join(c for c in text.replace("\r\n", "\n").replace("\r", "\n") if c in "\n\t" or ord(c) >= 32).strip()


def environment(cfg: Config) -> dict:
    """The environment a `claude` of the app's is started in: the server's own, less what would tie it to the
    session the server was started from, with the account the app's sessions run in."""
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
    if cfg.claude_config_dir:
        env["CLAUDE_CONFIG_DIR"] = str(cfg.claude_config_dir)
    return env


class Tutor:
    def __init__(self, cfg: Config, service):
        self.cfg, self.svc = cfg, service
        self.lock = threading.RLock()
        self.terms: dict[str, Terminal] = {}  # by game
        self.by_id: dict[str, Terminal] = {}
        self.dir = cfg.home / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._sweep, name="claude-sweep", daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        for t in list(self.by_id.values()):
            threading.Thread(target=t.stop, daemon=True).start()

    # ---------- the session ----------

    def _files(self, game_id: str) -> tuple[Path, Path]:
        """The Claude Code settings and MCP configuration of one game's session."""
        settings = {
            "permissions": {"allow": ["mcp__chess"]},  # its own tools never ask
            "tui": "fullscreen",
            "wheelScrollAccelerationEnabled": False,  # the page sends one wheel event per line (see app.js)
        }
        env = {"CHESSTUTOR_URL": self.cfg.url, "CHESSTUTOR_GAME": game_id}
        if self.svc.allie.available():
            env["CHESSTUTOR_ALLIE"] = "1"  # the session is offered `human_moves`
        mcp = {"mcpServers": {"chess": {"command": sys.executable, "args": [str(_ROOT / "mcp.py")], "env": env}}}
        s, m = self.dir / f"{game_id}.settings.json", self.dir / f"{game_id}.mcp.json"
        s.write_text(json.dumps(settings, indent=2))
        m.write_text(json.dumps(mcp, indent=2))
        return s, m

    def _has_transcript(self, session_id: str) -> bool:
        base = self.cfg.claude_config_dir or Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
        return any((base / "projects").glob(f"*/{session_id}.jsonl"))

    def _request(self, row: dict, plies: int) -> str:
        """What a request for an analysis sends."""
        since = row["analysed_ply"]
        text = f"[analysis request] {self.svc.brief(row['id'])} "
        if plies == 0:
            return text + "Say in a sentence or two what you can do for the student during this game, then wait."
        if since and since < plies:
            text += f"You were last asked at ply {since}: analyse the student's play in plies {since + 1} to {plies}"
        elif since:
            text += "Nothing has been played since you were last asked: look again at the game as it stands"
        else:
            text += "Analyse the student's play so far"
        return text + ", using your tools: what went well, what went wrong and why, and what to take from it."

    def _open(self, game_id: str, cols: int, rows: int, parts: list[str]) -> Terminal:
        """Start the game's session in a terminal (resuming it if it has run before), with ``parts`` as its prompt."""
        row = self.svc.store.game(game_id)
        if row is None:
            raise TutorError(f"no game {game_id}")
        started = bool(row["session_started"]) and self._has_transcript(row["session_id"])
        session_id = row["session_id"]
        if not started and self._has_transcript(session_id):
            started = True
        if not started and row["session_started"]:
            session_id = str(uuid.uuid4())  # its transcript is gone: a session id cannot be used twice
            self.svc.store.run("UPDATE games SET session_id=? WHERE id=?", (session_id, game_id))
        settings, mcp = self._files(game_id)
        argv = [self.cfg.claude_bin, *(["--resume", session_id] if started else ["--session-id", session_id])]
        argv += ["--model", self.cfg.claude_model]
        if self.cfg.claude_effort:
            argv += ["--effort", self.cfg.claude_effort]
        argv += ["--settings", str(settings), "--mcp-config", str(mcp), "--strict-mcp-config"]
        system = SYSTEM.replace("{allie}", ALLIE if self.svc.allie.available() else "")
        argv += ["--append-system-prompt", system, *self.cfg.claude_args]
        if parts:
            argv += ["--", _clean("\n\n".join(parts))]
        env = environment(self.cfg)
        env.update(CLAUDE_CODE_SCROLL_SPEED="1", TERM="xterm-256color", COLORTERM="truecolor")
        try:
            t = Terminal(argv, env, str(self.cfg.workspace), cols, rows, self.cfg.home / "claude.log")
        except OSError as e:
            raise TutorError(f"cannot start Claude Code ({self.cfg.claude_bin}): {e}") from None
        self.terms[game_id] = self.by_id[t.id] = t
        if parts:
            self.svc.store.run("UPDATE games SET session_started=1 WHERE id=?", (game_id,))
        return t

    def _live(self, game_id: str) -> Terminal | None:
        t = self.terms.get(game_id)
        return t if t is not None and t.alive else None

    def analyse(self, game_id: str, cols: int = 110, rows: int = 32) -> dict:
        """Open the game's session if need be, and ask it for an analysis of the game as it stands."""
        with self.lock:
            row = self.svc.store.game(game_id)
            if row is None:
                raise TutorError(f"no game {game_id}")
            plies = len(self.svc.store.moves(game_id))
            text = self._request(row, plies)
            t = self._live(game_id)
            if t is None:
                t = self._open(game_id, cols, rows, [text])
            else:
                self._type(t, text)
            self.svc.store.run("UPDATE games SET analysed_ply=? WHERE id=?", (plies, game_id))
        self.svc._bump(game_id)
        return {"terminal": t.id}

    def say(self, game_id: str, text: str, cols: int = 110, rows: int = 32) -> dict:
        """A message from the student (the page's box): typed into the game's session, which it opens, or resumes,
        if it is not running. Claude Code queues it if it is mid-answer."""
        text = _clean(text)
        if not text:
            raise TutorError("nothing to send")
        with self.lock:
            t = self._live(game_id)
            if t is not None:
                self._type(t, text)
                return {"terminal": t.id}
            row = self.svc.store.game(game_id)
            if row is None:
                raise TutorError(f"no game {game_id}")
            fresh = not (row["session_started"] and self._has_transcript(row["session_id"]))
            parts = ([f"[context] {self.svc.brief(game_id)}"] if fresh else []) + [text]
            t = self._open(game_id, cols, rows, parts)
        self.svc._bump(game_id)
        return {"terminal": t.id}

    def resume(self, game_id: str, cols: int = 110, rows: int = 32) -> dict:
        """Show the game's session again, as it was left, without asking it anything."""
        with self.lock:
            t = self._live(game_id)
            if t is None:
                row = self.svc.store.game(game_id)
                if row is None or not (row["session_started"] and self._has_transcript(row["session_id"])):
                    raise TutorError("this game has no Claude session yet: start an analysis")
                t = self._open(game_id, cols, rows, [])
        self.svc._bump(game_id)
        return {"terminal": t.id}

    def status(self, game_id: str) -> dict:
        t = self._live(game_id)
        return {"terminal": t.id if t else None}

    def terminal(self, term_id: str) -> Terminal:
        t = self.by_id.get(term_id)
        if t is None:
            raise TutorError(f"no terminal {term_id}")
        return t

    def reset(self, game_id: str) -> dict:
        """End the game's session and forget it: the next analysis starts a new one."""
        with self.lock:
            self._drop(game_id)
            self.svc.store.run(
                "UPDATE games SET session_id=?, session_started=0, analysed_ply=0 WHERE id=?", (str(uuid.uuid4()), game_id)
            )
        self.svc._bump(game_id)
        return {"ok": True}

    def close_terminal(self, game_id: str) -> dict:
        """End the terminal; the session stays and can be resumed."""
        with self.lock:
            self._drop(game_id)
        self.svc._bump(game_id)
        return {"ok": True}

    def forget(self, game_id: str) -> None:
        with self.lock:
            self._drop(game_id)
            for name in (f"{game_id}.settings.json", f"{game_id}.mcp.json"):
                (self.dir / name).unlink(missing_ok=True)

    def _drop(self, game_id: str) -> None:
        t = self.terms.pop(game_id, None)
        if t is not None:
            self.by_id.pop(t.id, None)
            threading.Thread(target=t.stop, daemon=True).start()

    def _type(self, t: Terminal, text: str) -> None:
        """Paste a message and press Enter. As a bracketed paste its new lines stay inside the one prompt."""
        t.write(b"\x1b[200~" + _clean(text).encode() + b"\x1b[201~")
        threading.Timer(0.4, t.write, args=(b"\r",)).start()

    def _sweep(self) -> None:
        """Forget terminals that have ended, and end those nobody has watched for a while (they resume)."""
        while not self.stop.wait(3):
            with self.lock:
                for game_id, t in list(self.terms.items()):
                    if not t.alive:
                        self.terms.pop(game_id, None)
                        self.by_id.pop(t.id, None)
                        self.svc._bump(game_id)
                    elif not t.viewers and time.time() - t.last_viewed > self.cfg.idle_terminal_s:
                        self._drop(game_id)
                        self.svc._bump(game_id)
