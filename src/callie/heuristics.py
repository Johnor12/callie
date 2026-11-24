"""Heuristics for finding a decent move, each shown by archetypal positions: what the Heuristics deck is made of.

They are written down in `heuristics.json`, beside this file: a list of heuristics, each a ``key`` (what its
cards are told apart by: not to be changed once it has cards), a ``title`` (the heuristic, as it is said on the
back of its cards) and its ``positions``. A position is a card (`cards.py`): its ``fen``, to find the move in;
the move that led to it (``last``, as UCI; left out: none, or not known); ``why``, how the heuristic finds the
move there, which is the back of the card; the ``answer`` the text is about, as SAN; and the engine's ``lines``
there, best first, each its ``moves`` (UCI, in one string) and its ``cp`` or ``mate`` for White. The lines are
what a card is answered by, as on any card: the engine's first choice, or another line within a few centipawns
of it. `scripts/heuristics.py` writes them for the positions that have none.

Nothing here is made by the app: a heuristic is added, or reworded, by editing the file, and the server puts
what the file says in the deck when it starts (`Cards.teach`).
"""

from __future__ import annotations

import json
from pathlib import Path

from .cards import right_moves
from .chess import Position
from .engine import Line

FILE = Path(__file__).with_name("heuristics.json")


class HeuristicError(Exception):
    pass


def line(fen: str, moves: str, cp: int | None = None, mate: int | None = None) -> dict:
    """An engine line as a card holds it, from how the file says it."""
    pos, ucis, sans = Position.from_fen(fen), moves.split(), []
    for uci in ucis:
        sans.append(pos.san(uci))
        pos = pos.push(uci)
    return {"moves": ucis, "san": sans, "cp": cp, "mate": mate, "value": Line(ucis, cp, mate).value}


def load(path: Path = FILE) -> list[dict]:
    """The heuristics on file, each with its positions as `Cards.teach` takes them: "fen", "last" (None: none),
    "why", "answer" and "lines" (as a card's). One the file says wrongly (a move that is not legal, a position
    there twice, an answer its lines do not make right) is an error, with where it is."""
    out, keys, seen = [], set(), set()
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
                positions.append(
                    {"fen": fen, "last": p.get("last"), "why": p["why"], "answer": p["answer"], "lines": lines}
                )
            out.append({"key": key, "title": h["title"], "positions": positions})
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise HeuristicError(f"{path.name}, heuristic {key!r}: {e}") from None
    return out
