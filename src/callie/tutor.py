"""Claude: one Claude Code session per game, in a real terminal that the game's page shows, one per card
that was asked about, on the back of the card, and one for the freeplay board.

Nothing here runs by itself. A session is opened when the page sends it a message (or, over the API, when an
analysis is asked for), and is told then what is expected of it and how to get at the game: the tools of `mcp.py` (the moves,
the engine's analysis of each, the engine itself, and the student's board, to put lines on). From then on the page shows it whenever it shows the game,
resuming it if its terminal has ended since. The app talks to it the way a person would: it pastes a
message into the terminal and presses Enter. Every session runs in the same directory, so they share one
Claude Code project: its memory carries from game to game.

A card's session is the same thing, about one card: opened by a message from the back of the card, with tools
that read the game the card's move was played in, and shown again whenever the back of the card is. A card comes
back for months and Claude Code deletes a conversation nobody has used for a few weeks, so the app keeps a copy
of a card's and puts it back when it is missing (`_keep`).

The freeplay board has one session, whatever position is on it. No game is behind that board: each message
from beside it says what is on it, and the session's tools are those that take any position.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from .config import Config
from .terminals import Terminal

SYSTEM = """\
You are the chess tutor inside Callie, a local app. The person you are talking to (the student) plays games \
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
{show}Never state a concrete line or tactic you have not verified with the engine or the legal-move list.

What an analysis is
- Start from `game`, then `review` for the moves that matter (the student's inaccuracies, mistakes and \
blunders first). For each one worth talking about: what the move allowed or missed, shown with the concrete \
line as far as it makes the point, and the idea to carry into later games (the pattern, or the question they \
should have asked themselves before moving). Ordinary good moves need no comment.
- Turn engine output into chess reasons. Do not recite evaluations or long lines. Use notation the student can \
follow on the board (SAN with move numbers), and put the lines you give on the board with `show_lines`.
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

# What a card's session is told instead: it is asked about one position, from the back of its card.
CARD = """\
You are the chess tutor inside Callie, a local app. The person you are talking to (the student) plays games \
against Stockfish held to a limited strength, or against Allie, a model that plays the way people of a chosen \
rating do. Every move of theirs that loses 50 centipawns or more becomes a flashcard: the position before the \
move, in which they later try to find the better one. This session belongs to one of those cards. The student \
has answered it and is looking at its back, beside this terminal: the engine's move, what their own lost, and an \
explanation written for the card when it was made. They ask you about it from there, most often why another move \
in the position, or a line that follows from one, is not as good. The card comes back, after minutes at first and \
months in the end, and this conversation is shown with it each time: what you wrote before is read again.

There is one more deck, Heuristics: rules of thumb for finding a decent move, the student's own and ones drawn \
from their repeated errors, each with positions that show it, most of them from the student's games. A card of \
it is such a position, to find the move in, and on its back are the heuristic and how it finds the move there. \
When this session belongs to one of those, the [card] message says so: talk about the position through the \
heuristic, and say where the heuristic stops being a good guide.

And one of openings, Openers: positions from the first moves of a game that are sharp, or that players of the \
student's strength often get wrong, in the openings they play (1. d4 as White; 1... e5 and 1... d5 as Black). A \
card of it is such a position, to find the move in, and on its back are the opening and why the move is the one. \
When this session belongs to one of those, the [card] message says so: talk about the moves that lead to the \
position and the ones that follow from it, and what the student should remember of it at the board.

What reaches you
- A message that starts with [card] was sent by the app when the session was opened: the card, the game its move \
was played in, and what is written on its back.
- A message of the student's may begin with a line the app adds, [tried on the card: ...]: the moves they have \
made on the card's board since you last heard from them, in order, each with the centipawns it loses against the \
engine's first choice where the app knows it. "This move" or "that" in their question is most likely the last of them.
- Anything else is the student talking to you.

Your tools (MCP server "chess"). They read the game the card's move was played in.
- analyze: full-strength Stockfish, live, on any position: a point in the game or a FEN, optionally after extra \
moves ("what if ..."), optionally restricted to candidate moves you want compared. It is how "why not this move" \
is answered: play the move on the card's position and see what the engine does to it. It takes seconds.
- position: any position spelled out (diagram, piece lists, legal moves). Use it instead of trusting your own \
picture of the board.
- review: the engine's analysis on file for moves that were played in the game: the evaluation either side of \
the move, the engine's choices in that position with the lines it expected, and its main line after the move.
- game: the whole game, one line a move, with the engine's evaluation, the centipawns each move lost, its grade, \
and the explanations written for the student's other errors.
{show}Never state a concrete line or tactic you have not verified with the engine or the legal-move list.

How to answer
- Answer what was asked, in a few sentences: what the move allows or misses, shown with the concrete line as far \
as it makes the point, and how that compares with the move the card asks for. A refutation is a line, not a number.
- Turn engine output into chess reasons. Do not recite evaluations or long lines. Use notation the student can \
follow on the board (SAN with move numbers), and put the lines you give on the board with `show_lines`.
- The explanation on the back of the card is something the student has just read: build on it, and say so where \
your own reading of the position differs.
- If the game is still in progress, do not give away the position on its board now unless the student asks.
- Pitch it to the student's level as their play and questions show it. Be direct and kind, with no filler praise.
- Write plain text for a terminal: short paragraphs, no tables, no headers.

{allie}Memory
Every session of the app runs in this same directory, so your memory here is shared with the sessions of the \
student's games and of their other cards. Look at what you already know about their play, and keep it current: \
recurring mistakes, openings they play, what they have been working on, how they like feedback given.
"""

# What the freeplay board's session is told: it is asked about whatever position is on that board.
FREE = """\
You are the chess tutor inside Callie, a local app. The person you are talking to (the student) plays games \
against Stockfish held to a limited strength, or against Allie, a model that plays the way people of a chosen \
rating do, and reviews flashcards made of the moves that cost them most. This session belongs to neither a game \
nor a card: it is the one of the app's freeplay board, beside this terminal. On that board the student moves the \
pieces of both sides as they like, from the starting position or from the position of one of their cards, to try \
moves and see what follows. It shows them Stockfish's evaluation of the position on it and an arrow for the \
engine's first choice. They ask you about what they see there: why the engine wants a move, what is wrong with \
another, what a side is playing for. The board has this one conversation, whatever is on it: what came earlier \
in it may be about another position altogether.

What reaches you
- Every message of the student's begins with a line the app adds, [board: ...]: the position on the board when \
they sent it, as a FEN, and the moves that were made on the board to reach it. The board changes between \
messages: go by the last one. "This move" or "here" in their question is about that position, and most likely \
about the last move made to reach it.
- What follows that line is the student talking to you.

Your tools (MCP server "chess"). No game is behind this board: each takes a position as a FEN, the one of the \
[board] line or any other, optionally after extra moves.
- analyze: full-strength Stockfish, live, on a position, optionally after extra moves ("what if ..."), \
optionally restricted to candidate moves you want compared. It is how "why this move" and "why not that one" are \
answered: play the move and see what the engine does to it. It takes seconds.
- position: a position spelled out (diagram, piece lists, legal moves). Use it instead of trusting your own \
picture of the board.
{show}Never state a concrete line or tactic you have not verified with the engine or the legal-move list.

How to answer
- Answer what was asked, in a few sentences: what a move allows or misses, shown with the concrete line as far \
as it makes the point. A refutation is a line, not a number.
- Turn engine output into chess reasons. Do not recite evaluations or long lines. Use notation the student can \
follow on the board (SAN with move numbers), and put the lines you give on the board with `show_lines`.
- The student sees the engine's evaluation and its first choice already: say what is behind them, and where a \
move a person would sooner find is nearly as good.
- Pitch it to the student's level as their play and questions show it. Be direct and kind, with no filler praise.
- Write plain text for a terminal: short paragraphs, no tables, no headers.

Memory
Every session of the app runs in this same directory, so your memory here is shared with the sessions of the \
student's games and of their cards. Look at what you already know about their play, and keep it current: \
recurring mistakes, openings they play, what they have been working on, how they like feedback given.
"""
FREE_KEY = "freeplay"  # what the freeplay board's session goes by where a game's goes by the game's id
NO_GAME = "0"  # the game its tools are given: none

# The part of each about `show_lines`: the board beside the terminal is a game's, a card's or the freeplay one,
# and Claude's lines are played on it.
SHOW = """\
- show_lines: puts a position and lines from it on the student's board, beside this terminal. The lines are \
listed there under the names you give them, each move a button, and the student plays through them on the board \
while reading your answer. A line written in notation is hard for them to follow, and one they can step through \
is not: so whenever what you say turns on a concrete line (what a move allowed, the refutation, what should have \
been played and how it goes on), show it, once you have verified it, and before you write about it. One call for \
each position you discuss, with every line from that position in it: the move that was played beside the better \
one, say. Then write around what is on the board: still give the line in your text, call it by the name it has \
there, and say what to watch for as it is played. What you showed earlier stays there for them to go back to.
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


def _card_key(card_id: int) -> str:
    """What a card's session goes by where a game's goes by the game's id (which is hexadecimal: never this)."""
    return f"card-{card_id}"


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
        self.terms: dict[str, Terminal] = {}  # by game, by card (`_card_key`), or FREE_KEY
        self.by_id: dict[str, Terminal] = {}
        self.kept: dict[str, str] = {}  # by card: the session in its terminal, whose transcript a copy is kept of
        self.dir = cfg.home / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.transcripts = cfg.home / "transcripts"  # the copies, laid out as Claude Code's own `projects` is
        self.stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._sweep, name="claude-sweep", daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        with self.lock:
            for session_id in self.kept.values():
                self._keep(session_id)
        for t in list(self.by_id.values()):
            threading.Thread(target=t.stop, daemon=True).start()

    # ---------- the session ----------

    def _files(self, key: str, game_id: str, session_id: str) -> tuple[Path, Path]:
        """The Claude Code settings and MCP configuration of one session (a game's, a card's, or the freeplay
        board's: ``key``), whose tools read ``game_id`` and put lines on the board beside the conversation
        ``session_id``."""
        settings = {
            "permissions": {"allow": ["mcp__chess"]},  # its own tools never ask
            "tui": "fullscreen",
            "wheelScrollAccelerationEnabled": False,  # the page sends one wheel event per line (see app.js)
        }
        env = {"CALLIE_URL": self.cfg.url, "CALLIE_GAME": game_id, "CALLIE_SESSION": session_id}  # `show_lines`
        if key == FREE_KEY:
            env["CALLIE_FREEPLAY"] = "1"  # the session is offered only the tools that take any position
        elif self.svc.allie.available():
            env["CALLIE_ALLIE"] = "1"  # the session is offered `human_moves`
        mcp = {"mcpServers": {"chess": {"command": sys.executable, "args": [str(_ROOT / "mcp.py")], "env": env}}}
        s, m = self.dir / f"{key}.settings.json", self.dir / f"{key}.mcp.json"
        s.write_text(json.dumps(settings, indent=2))
        m.write_text(json.dumps(mcp, indent=2))
        return s, m

    def _projects(self) -> Path:
        """Where Claude Code keeps its transcripts: a directory for each directory it has run in."""
        base = self.cfg.claude_config_dir or Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
        return base / "projects"

    def _has_transcript(self, session_id: str) -> bool:
        return any(self._projects().glob(f"*/{session_id}.jsonl"))

    def _keep(self, session_id: str) -> None:
        """A card's conversation is kept for as long as the card is: a copy of its transcript is made here as
        it grows, and put back where Claude Code looks for it when it is no longer there (Claude Code deletes
        the transcripts nothing was added to for its `cleanupPeriodDays`, 30 unless set)."""
        try:
            theirs = next(self._projects().glob(f"*/{session_id}.jsonl"), None)
            ours = next(self.transcripts.glob(f"*/{session_id}.jsonl"), None)
            if theirs is not None:
                ours = self.transcripts / theirs.parent.name / theirs.name
                if not ours.exists() or ours.stat().st_mtime_ns < theirs.stat().st_mtime_ns:
                    ours.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(theirs, ours)
            elif ours is not None:
                back = self._projects() / ours.parent.name / ours.name
                back.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ours, back)  # written now: not one to delete for its age
        except OSError as e:
            print(f"callie: the transcript of session {session_id} was not kept: {e}", file=sys.stderr, flush=True)

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
        return self._start(game_id, "games", row, game_id, SYSTEM, cols, rows, parts)

    def _start(
        self, key: str, table: str, row: dict, game_id: str, system: str, cols: int, rows: int, parts: list[str]
    ) -> Terminal:
        """Start a session in a terminal (resuming it if it has run before), with ``parts`` as its prompt: the
        one that ``row`` of ``table`` ("games", "cards" or "freeplay") keeps, whose tools read ``game_id``."""
        session_id = row["session_id"]
        if session_id and table == "cards":
            self._keep(session_id)
        started = bool(session_id) and self._has_transcript(session_id)
        if not started and (row["session_started"] or not session_id):
            # it has none yet (a card), or its transcript is gone: a session id cannot be used twice
            session_id = str(uuid.uuid4())
            self.svc.store.run(f"UPDATE {table} SET session_id=? WHERE id=?", (session_id, row["id"]))
        settings, mcp = self._files(key, game_id, session_id)
        argv = [self.cfg.claude_bin, *(["--resume", session_id] if started else ["--session-id", session_id])]
        argv += ["--model", self.cfg.claude_model]
        if self.cfg.claude_effort:
            argv += ["--effort", self.cfg.claude_effort]
        argv += ["--settings", str(settings), "--mcp-config", str(mcp), "--strict-mcp-config"]
        system = system.replace("{show}", SHOW).replace("{allie}", ALLIE if self.svc.allie.available() else "")
        argv += ["--append-system-prompt", system, *self.cfg.claude_args]
        if parts:
            argv += ["--", _clean("\n\n".join(parts))]
        env = environment(self.cfg)
        env.update(CLAUDE_CODE_SCROLL_SPEED="1", TERM="xterm-256color", COLORTERM="truecolor")
        try:
            t = Terminal(argv, env, str(self.cfg.workspace), cols, rows, self.cfg.home / "claude.log")
        except OSError as e:
            raise TutorError(f"cannot start Claude Code ({self.cfg.claude_bin}): {e}") from None
        self.terms[key] = self.by_id[t.id] = t
        t.session = session_id  # the conversation it shows: what Claude puts on the board there is its viewers' to see
        if table == "cards":
            self.kept[key] = session_id
        if parts:
            self.svc.store.run(f"UPDATE {table} SET session_started=1 WHERE id=?", (row["id"],))
        return t

    def _live(self, key: str) -> Terminal | None:
        t = self.terms.get(key)
        return t if t is not None and t.alive else None

    def _changed(self, key: str) -> None:
        """A session's terminal came or went: the pages on its game are told (a card has no page waiting on it,
        nor has the freeplay board)."""
        if not key.startswith("card-") and key != FREE_KEY:
            self.svc._bump(key)

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

    # ---------- a card's session ----------

    def _card(self, card_id: int) -> dict:
        row = self.svc.store.one("SELECT * FROM cards WHERE id=? AND state != 'removed'", (card_id,))
        if row is None:
            raise TutorError(f"no card {card_id}")
        return row

    def _begun(self, card: dict) -> bool:
        """The card has a conversation to go on with: one was started, and its transcript, or the copy, is there."""
        session_id = card["session_id"]
        if not (card["session_started"] and session_id):
            return False
        self._keep(session_id)
        return self._has_transcript(session_id)

    def say_card(self, card_id: int, text: str, tried=None, cols: int = 110, rows: int = 32) -> dict:
        """A message from the back of a card: typed into the card's session, which it opens, or resumes, if it
        is not running. ``tried`` is the moves made on the card's board that the session has not been told of."""
        text = _clean(text)
        if not text:
            raise TutorError("nothing to send")
        key = _card_key(card_id)
        with self.lock:
            card = self._card(card_id)
            text = self.svc.card_tried(card, tried) + text
            t = self._live(key)
            if t is not None:
                self._type(t, text)
            else:
                game_id, about = self.svc.card_context(card)
                parts = ([] if self._begun(card) else [f"[card] {about}"]) + [text]
                t = self._start(key, "cards", card, game_id, CARD, cols, rows, parts)
        return {"terminal": t.id}

    def resume_card(self, card_id: int, cols: int = 110, rows: int = 32) -> dict:
        """The card's session as it was left, without asking it anything: what the back of the card shows.
        "terminal" is None for a card that was never asked about."""
        key = _card_key(card_id)
        with self.lock:
            t = self._live(key)
            if t is None:
                card = self._card(card_id)
                if not self._begun(card):
                    return {"terminal": None}
                t = self._start(key, "cards", card, self.svc.card_context(card)[0], CARD, cols, rows, [])
        return {"terminal": t.id}

    # ---------- the freeplay board's session ----------

    def _free_begun(self, row: dict) -> bool:
        """The freeplay board has a conversation to go on with."""
        return bool(row["session_started"] and row["session_id"]) and self._has_transcript(row["session_id"])

    def say_free(self, text: str, fen=None, moves=None, cols: int = 110, rows: int = 32) -> dict:
        """A message from beside the freeplay board: typed into the board's session, which it opens, or
        resumes, if it is not running. Before it goes what is on the board: the position it started from
        (``fen``; None: where a game starts) and the ``moves`` made on it since."""
        text = _clean(text)
        if not text:
            raise TutorError("nothing to send")
        with self.lock:
            text = self.svc.free_board(fen, moves) + text
            t = self._live(FREE_KEY)
            if t is not None:
                self._type(t, text)
            else:
                t = self._start(FREE_KEY, "freeplay", self.svc.store.freeplay(), NO_GAME, FREE, cols, rows, [text])
        return {"terminal": t.id}

    def resume_free(self, cols: int = 110, rows: int = 32) -> dict:
        """The freeplay board's session as it was left, without asking it anything: what the page shows beside
        the board. "terminal" is None when nothing was ever asked there (or its transcript is gone)."""
        with self.lock:
            t = self._live(FREE_KEY)
            if t is None:
                row = self.svc.store.freeplay()
                if not self._free_begun(row):
                    return {"terminal": None}
                t = self._start(FREE_KEY, "freeplay", row, NO_GAME, FREE, cols, rows, [])
        return {"terminal": t.id}

    def forget_card(self, card_id: int) -> None:
        """The card is gone (with its game): its terminal ends, and no later card of that number is taken for it."""
        self.forget(_card_key(card_id))

    def terminal(self, term_id: str) -> Terminal:
        t = self.by_id.get(term_id)
        if t is None:
            raise TutorError(f"no terminal {term_id}")
        return t

    def lines(self, term_id: str) -> dict:
        """What Claude put on the board in the conversation a terminal shows: each a position and lines from it,
        the oldest first."""
        return {"shown": self.svc.store.shown(self.terminal(term_id).session)}

    def shown(self, session_id: str) -> None:
        """There is more of that: the pages on the conversation's terminal are told, and ask for it."""
        for t in list(self.by_id.values()):
            if t.session == session_id:
                t.tell("lines")

    def reset(self, game_id: str) -> dict:
        """End the game's session and forget it: the next analysis starts a new one."""
        with self.lock:
            self._drop(game_id)
            self.svc.store.run(
                "UPDATE games SET session_id=?, session_started=0, analysed_ply=0 WHERE id=?",
                (str(uuid.uuid4()), game_id),
            )
        self.svc._bump(game_id)
        return {"ok": True}

    def close_terminal(self, game_id: str) -> dict:
        """End the terminal; the session stays and can be resumed."""
        with self.lock:
            self._drop(game_id)
        self.svc._bump(game_id)
        return {"ok": True}

    def forget(self, key: str) -> None:
        with self.lock:
            self._drop(key)
            for name in (f"{key}.settings.json", f"{key}.mcp.json"):
                (self.dir / name).unlink(missing_ok=True)

    def _drop(self, key: str) -> None:
        t = self.terms.pop(key, None)
        if key in self.kept:
            self._keep(self.kept.pop(key))
        if t is not None:
            self.by_id.pop(t.id, None)
            threading.Thread(target=t.stop, daemon=True).start()

    def _type(self, t: Terminal, text: str) -> None:
        """Paste a message and press Enter. As a bracketed paste its new lines stay inside the one prompt."""
        t.write(b"\x1b[200~" + _clean(text).encode() + b"\x1b[201~")
        threading.Timer(0.4, t.write, args=(b"\r",)).start()

    def _sweep(self) -> None:
        """Forget terminals that have ended, and end those nobody has watched for a while (they resume). A
        card's conversation is copied as it grows."""
        while not self.stop.wait(3):
            with self.lock:
                for key, t in list(self.terms.items()):
                    if key in self.kept:
                        self._keep(self.kept[key])
                    if not t.alive:
                        self.terms.pop(key, None)
                        self.by_id.pop(t.id, None)
                        self.kept.pop(key, None)
                        self._changed(key)
                    elif not t.viewers and time.time() - t.last_viewed > self.cfg.idle_terminal_s:
                        self._drop(key)
                        self._changed(key)
