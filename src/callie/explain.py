"""Why a move was bad: an explanation written by Claude for each card, in the background.

A card is made the moment the engine has seen an error (`cards.py`). Here one thread takes the cards that have
no explanation yet, the latest first, and for each runs Claude Code once, without a terminal (`claude -p`), with
the tools a game's session has (`mcp.py`) and the question "Explain why this move was a blunder". What it answers
is kept with the card: shown on its back, beside the move in the game's move list, and given to the game's own
Claude session with the moves.

The games come first. One explanation is written at a time, by a process at the lowest priority; what its tools
ask of the server is marked as asked from the background, and is answered by an engine of its own on one thread
at that priority, with searches of a small set size, and by Allie only between its moves (`play.py`).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from .config import Config
from .tutor import _clean, environment

SYSTEM = """\
You write the back of a flashcard for Callie, a local app in which a student plays games against Stockfish \
held to a limited strength, or against Allie, a model that plays the way people of a chosen rating do. Every move \
of the student's that loses too much becomes a card: the position before the move, in which they will later try \
to find the better one. What you write is shown to them after they have answered, and beside the move in the \
game's move list. Nobody reads along as you work and nobody can answer you: ask no questions.

Your answer is the explanation and nothing else: no greeting, no heading, no markdown, no list. Plain text, 60 to \
110 words, to the student ("you"). Say what the move allowed or missed, show it with the concrete line as far \
as it makes the point (SAN with move numbers), name the better move and why it works, and end with the idea to \
carry into later games: the pattern, or the question to ask before moving. Give chess reasons, not evaluations.

Your tools (MCP server "chess"):
- review: the engine's full-strength analysis on file for a move of the game: the evaluation either side of it, \
the engine's choices in the position with the lines it expected, and its main line after the move. Start there.
- position: a position spelled out (diagram, piece lists, legal moves). Use it instead of your own picture of \
the board.
- analyze: Stockfish, run now, on a point of the game or a FEN, after any "what if" moves, optionally comparing \
candidate moves: for the refutation or the "why not this" that the analysis on file does not show.
- game: the whole game, if what came before matters.
{allie}Never state a line or a tactic you have not checked against `review`, `analyze` or the legal moves.

You work in the background, on a machine whose processors belong first to the games being played. Keep it \
light: a handful of tool calls. `analyze` is here a small search on one thread (at most 3 seconds' worth of \
work), and it can be slow to answer while a game is on.
"""
ALLIE = """\
- human_moves: what people play in a position of the game, by Allie: the most played moves of players of a \
rating, and how often. For whether the mistake is a common one at the student's level, or what an opponent would \
likely have answered. It says how likely a move is, never how good. It may not be there to ask while a game is \
being played: do without it then.
"""
TIMEOUT_S = 900  # a tool call waits its turn behind the games: it can take a while
AGAIN_S = 3600  # a card whose explanation could not be written is tried again after this


class Explainer:
    def __init__(self, cfg: Config, service):
        self.cfg, self.svc = cfg, service
        self.dir = cfg.home / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.stop = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.failed: dict[int, float] = {}  # cards that could not be explained, and when

    def start(self) -> None:
        threading.Thread(target=self._loop, name="claude-explain", daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        self.svc.cards.made.set()
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def _loop(self) -> None:
        failures = 0
        while not self.stop.is_set():
            self.svc.cards.made.clear()
            now = time.monotonic()
            card = self.svc.cards.unexplained(tuple(c for c, at in self.failed.items() if now - at < AGAIN_S))
            if card is None:
                self.svc.cards.made.wait(60)  # a card was made, or it is time to look at those that failed
                continue
            try:
                self.svc.cards.explain(card, self.write(card))
                self.svc.explained()
                failures = 0
            except Exception as e:  # noqa: BLE001
                if self.stop.is_set():
                    return
                if not isinstance(e, (OSError, RuntimeError, subprocess.TimeoutExpired)):
                    traceback.print_exc(file=sys.stderr)
                print(f"callie: no explanation for card {card['id']} ({card['san']}): {e}", file=sys.stderr, flush=True)
                self.failed[card["id"]] = time.monotonic()
                failures += 1
                self.stop.wait(min(3600, 30 * 2**failures))  # Claude Code missing or logged out fails every time

    def _files(self, game_id: str) -> tuple[Path, Path]:
        env = {"CALLIE_URL": self.cfg.url, "CALLIE_GAME": game_id, "CALLIE_QUIET": "1"}
        if self.svc.allie.available():
            env["CALLIE_ALLIE"] = "1"
        mcp = {
            "mcpServers": {
                "chess": {"command": sys.executable, "args": [str(Path(__file__).with_name("mcp.py"))], "env": env}
            }
        }
        s, m = self.dir / "explain.settings.json", self.dir / "explain.mcp.json"
        s.write_text(json.dumps({"permissions": {"allow": ["mcp__chess"]}}, indent=2))  # its own tools never ask
        m.write_text(json.dumps(mcp, indent=2))
        return s, m

    def write(self, card: dict) -> str:
        """Run Claude on one card and return what it wrote."""
        game_id, prompt = self.svc.card_request(card)
        settings, mcp = self._files(game_id)
        argv = [
            "nice",
            "-n",
            "19",
            self.cfg.claude_bin,
            "-p",
            "--output-format",
            "json",
            "--model",
            self.cfg.claude_model,
        ]
        if self.cfg.claude_effort:
            argv += ["--effort", self.cfg.claude_effort]
        # No tools but the game's, and no session kept: it is one question, asked once.
        argv += [
            "--settings",
            str(settings),
            "--mcp-config",
            str(mcp),
            "--strict-mcp-config",
            "--tools",
            "",
            "--no-session-persistence",
        ]
        argv += ["--system-prompt", SYSTEM.replace("{allie}", ALLIE if self.svc.allie.available() else "")]
        argv += [*self.cfg.claude_args, "--", _clean(prompt)]
        with open(self.cfg.home / "claude.log", "ab") as log:
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log,
                env=environment(self.cfg),
                cwd=str(self.cfg.workspace),
            )
        try:
            out, _ = self.proc.communicate(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.communicate()
            raise
        finally:
            code, self.proc = self.proc.returncode, None
        try:
            answer = json.loads(out)
        except ValueError:
            answer = {}
        text = _clean(str(answer.get("result") or ""))
        if code or answer.get("is_error") or not text:
            raise RuntimeError(
                f"Claude Code ({self.cfg.claude_bin}) gave no answer: {text or out.decode(errors='replace').strip()[-300:] or f'exit {code}'}"
            )
        return text
