"""Heuristics for finding a decent move, each shown by archetypal positions: what the Heuristics deck is made of.

They are written down in `heuristics.json`, beside this file: a list of heuristics, each a ``key`` (what its
cards are told apart by: not to be changed once it has cards), a ``title`` (the heuristic, as it is said on the
back of its cards) and its ``positions``. A position is a card (`cards.py`): its ``fen``, to find the move in;
the moves that led to it from the start of a game, where it is from one (``moves``, as SAN or UCI, in one string),
or only the last of them (``last``, as UCI; both left out: none, or not known); the moves that follow from it,
its answer first (``then``, the same way; left out: none are shown); ``why``, how the heuristic finds the
move there, which is the back of the card; the ``answer`` the text is about, as SAN; where the position is from
(``source``, for whoever reads the file: the app shows it nowhere); and the engine's ``lines`` there, best first,
each its ``moves`` (UCI, in one string) and its ``cp`` or ``mate`` for White. The lines are
what a card is answered by, as on any card: the engine's first choice, or another line within a few centipawns
of it. `scripts/heuristics.py` writes them for the positions that have none.

Nothing here is made by the app: a heuristic is added, or reworded, by editing the file, and the server puts
what the file says in the deck when it starts (`Cards.teach`).

The moves that led to a position and the ones that follow from it are one line on the back of its card, to step
through on the board from the card's position, onwards or back: listed there as the lines Claude puts on a board
are (`Service.tool_show`), and kept in the same form.

The Openers deck is written down the same way, in `openers.json`: positions early in a game that are sharp, or
often played wrongly. Each entry there is an opening, its ``title`` the opening's name, as said on the back of its
cards; ``moves`` is how the opening gets to the position, ``then`` how it goes on (a trap closing, say), and
``why`` says why the move is the one. A position is in one file only.
"""

from __future__ import annotations

import json
from pathlib import Path

from .cards import right_moves
from .chess import Position
from .engine import Line

FILE = Path(__file__).with_name("heuristics.json")
OPENERS = Path(__file__).with_name("openers.json")


class HeuristicError(Exception):
    pass


def line(fen: str, moves: str, cp: int | None = None, mate: int | None = None) -> dict:
    """An engine line as a card holds it, from how the file says it."""
    pos, ucis, sans = Position.from_fen(fen), moves.split(), []
    for uci in ucis:
        sans.append(pos.san(uci))
        pos = pos.push(uci)
    return {"moves": ucis, "san": sans, "cp": cp, "mate": mate, "value": Line(ucis, cp, mate).value}


def led(fen: str, moves: str = "", then: str = "") -> dict:
    """The line of a position: the moves that lead to it from the start of a game, and after them the ones that
    follow from it. As a position and a line from it are put on a board: its "title", the "fen" the line starts
    from (the position's own, where nothing says how it came about), and the line, each move its "uci", its
    "san" and the "fen" after it; "at" is how many of the moves lead to the position, which is where stepping
    through the line starts."""
    card = Position.from_fen(fen)
    pos = Position.from_fen() if moves else card
    start, played = pos.fen(), []

    def play(texts: str) -> None:
        nonlocal pos
        for text in texts.split():
            uci = pos.parse(text)
            played.append({"uci": uci, "san": pos.san(uci)})
            pos = pos.push(uci)
            played[-1]["fen"] = pos.fen()

    play(moves)
    if pos.key() != card.key():
        raise ValueError(f"{fen}: its moves lead to {pos.fen()}")
    at = len(played)
    play(then)
    name = "From the start" if moves else "From here"
    return {"title": "The line", "fen": start, "lines": [{"name": name, "at": at, "moves": played}]}


def load(path: Path = FILE, seen: set[str] | None = None) -> list[dict]:
    """The heuristics on file (or the openings of the other one), each with its positions as `Cards.teach` takes
    them: "fen", "last" (None: none), "why", "answer", "lines" (as a card's) and "shown" (what its card has to
    step through on the board: the moves that led to the position and those that follow, where the file has
    them). One the file says wrongly (a move that is not legal, a position there twice, an answer its lines do
    not make right, moves that lead elsewhere, or follow from another move than the answer) is an error, with
    where it is. ``seen`` is the positions of a file read before, which this one's are added to: one in both is
    an error too."""
    out, keys, seen = [], set(), set() if seen is None else seen
    for h in json.loads(path.read_text()):
        key = h.get("key")
        try:
            if not key or key in keys or not h["title"].strip() or not h["positions"]:
                raise ValueError("it needs a key of its own, a title and a position")
            keys.add(key)
            positions = []
            for p in h["positions"]:
                fen = p["fen"]
                pos = Position.from_fen(fen)
                if pos.key() in seen:
                    raise ValueError(f"{fen} is there twice")
                seen.add(pos.key())
                lines = [line(fen, **ln) for ln in p["lines"]]
                right = [pos.san(m) for m in right_moves(lines, pos.turn == "w", "")]
                if p["answer"] not in right:
                    raise ValueError(
                        f"{fen}: the lines answer it with {', '.join(right) or 'nothing'}, not {p['answer']}"
                    )
                if not p["why"].strip():
                    raise ValueError(f"{fen}: nothing says why")
                moves, then, shown, last = p.get("moves", ""), p.get("then", ""), [], p.get("last")
                if moves or then:
                    shown = [led(fen, moves, then)]
                    (ln,) = shown[0]["lines"]
                    if then and ln["moves"][ln["at"]]["san"] != p["answer"]:
                        raise ValueError(f"{fen}: what follows does not start with its answer, {p['answer']}")
                    last = last or (ln["moves"][ln["at"] - 1]["uci"] if moves else None)
                positions.append(
                    {"fen": fen, "last": last, "why": p["why"], "answer": p["answer"], "lines": lines, "shown": shown}
                )
            out.append({"key": key, "title": h["title"], "positions": positions})
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise HeuristicError(f"{path.name}, at {key!r}: {e}") from None
    return out
