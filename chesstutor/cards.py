"""Flashcards of your own errors: the position before a move that lost too much, to find the better move in.

Three decks, by what the move lost. A card is one position with one move played in it: the same move in the same
position in another game makes no second card, it marks the first as not learned. A card is active (to be
reviewed) from the moment it is made, and again each time the days it was hidden for have passed: more of them
after every right answer, as in Anki, with a ceiling so that even a well known card comes back.
"""

from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timedelta
from datetime import time as clock

from .chess import Position
from .engine import loss
from .store import Store

DECKS = (("blunder", "Blunders", 200), ("mistake", "Mistakes", 100), ("inaccuracy", "Inaccuracies", 50))  # centipawns lost, at least
NEAR = 10  # another of the engine's lines within this many centipawns of its first is a right answer too
FIRST = {"hard": 1, "easy": 4}  # days a card is hidden for after its first right answer
GROWTH = {"hard": 1.2, "easy": 2.5}  # and how much longer after each one that follows
LONGEST = 180  # days: however well it is known, a card comes back this often
DAY_STARTS = 4  # o'clock: a card due on a day is active from then, so one answered late at night is there in the morning


class CardError(Exception):
    pass


def deck_of(cpl: int) -> str | None:
    """The deck a move that lost ``cpl`` centipawns belongs in; None: it lost too little for a card."""
    return next((key for key, _, least in DECKS if cpl >= least), None)


def hidden_for(interval: int, grade: str) -> int:
    """Days a card is hidden for after a right answer, given the days it was hidden for before it (0: it was
    not learned)."""
    if grade not in GROWTH:
        raise CardError(f"grade must be one of {', '.join(GROWTH)}")
    days = max(interval + 1, round(interval * GROWTH[grade])) if interval else FIRST[grade]
    return min(days, LONGEST)


def due_in(days: int, now: float) -> float:
    """When a card hidden for ``days`` from ``now`` is active again: the start of that day."""
    day = (datetime.fromtimestamp(now) - timedelta(hours=DAY_STARTS)).date() + timedelta(days=days)
    return datetime.combine(day, clock(DAY_STARTS)).timestamp()


def right_moves(lines: list[dict], white: bool, played: str) -> list[str]:
    """The moves that answer a card: the engine's first choice in the position and any other line of its
    search that is as good to within NEAR, less the move the card is about."""
    first = [ln["moves"][0] for ln in lines if ln["moves"]]
    values = [ln["value"] for ln in lines if ln["moves"]]
    return [m for m, v in zip(first, values) if m != played and (m == first[0] or loss(values[0], v, white) <= NEAR)]


class Cards:
    def __init__(self, store: Store):
        self.store = store
        self.rng = random.Random()
        self.made = threading.Event()  # set when a card is made: what writes their explanations waits on it

    def note(self, fen: str, uci: str, san: str, cpl: int, lines: list[dict], at: float) -> None:
        """A move of yours, played at ``at`` in the position ``fen``, with what it lost and the engine's lines
        there: a card if it lost enough, or, where there is one for it already, that card not learned again.
        Told of the same move twice (or of an older game after a newer one), it does nothing the second time.
        """
        if deck_of(cpl) is None or not lines or not lines[0]["moves"] or lines[0]["moves"][0] == uci:
            return
        position = Position.from_fen(fen).key()
        with self.store.lock:
            have = self.store.one("SELECT id, erred_at FROM cards WHERE position=? AND uci=?", (position, uci))
            if have is None:
                self.store.run(
                    "INSERT INTO cards (position, uci, san, fen, cpl, lines, erred_at, due) VALUES (?,?,?,?,?,?,?,?)",
                    (position, uci, san, fen, cpl, json.dumps(lines), at, at),
                )
                self.made.set()
            elif have["erred_at"] < at:
                self.store.run(
                    "UPDATE cards SET san=?, fen=?, cpl=?, lines=?, erred_at=?, interval=0, due=? WHERE id=?",
                    (san, fen, cpl, json.dumps(lines), at, at, have["id"]),
                )

    def _active(self, deck: str, now: float) -> list[dict]:
        if deck not in [key for key, _, _ in DECKS]:
            raise CardError(f"no deck {deck!r}")
        return [r for r in self.store.all("SELECT * FROM cards WHERE due <= ?", (now,)) if deck_of(r["cpl"]) == deck]

    def explained(self) -> dict[tuple[str, str], str]:
        """The explanation of each move that has one, by its position (a `Position.key`) and the move."""
        rows = self.store.all("SELECT position, uci, explanation FROM cards WHERE explanation IS NOT NULL")
        return {(r["position"], r["uci"]): r["explanation"] for r in rows}

    def unexplained(self, skip: tuple[int, ...] = ()) -> dict | None:
        """The card whose explanation is to be written next: of those without one, the latest played."""
        rows = self.store.all("SELECT * FROM cards WHERE explanation IS NULL ORDER BY erred_at DESC")
        return next((r for r in rows if r["id"] not in skip), None)

    def explain(self, card_id: int, text: str) -> None:
        self.store.run("UPDATE cards SET explanation=? WHERE id=?", (text, card_id))

    def decks(self, now: float | None = None) -> list[dict]:
        """Each deck with how many cards it holds and how many of them are active."""
        now = time.time() if now is None else now
        rows = self.store.all("SELECT cpl, due FROM cards")
        return [
            {
                "key": key,
                "name": name,
                "cards": sum(deck_of(r["cpl"]) == key for r in rows),
                "active": sum(deck_of(r["cpl"]) == key and r["due"] <= now for r in rows),
            }
            for key, name, _ in DECKS
        ]

    def draw(self, deck: str, skip: int | None = None, now: float | None = None) -> dict:
        """An active card of a deck, at random (not ``skip``, the one just seen, while there is another), with
        every legal move of its position as a game's state has them. "card" is None when none is active."""
        now = time.time() if now is None else now
        rows = self._active(deck, now)
        out = {"deck": deck, "name": next(name for key, name, _ in DECKS if key == deck), "active": len(rows), "card": None}
        if rows:
            row = self.rng.choice([r for r in rows if r["id"] != skip] or rows)
            pos = Position.from_fen(row["fen"])
            legal = {m: {"san": pos.san(m), "fen": pos.push(m).fen()} for m in pos.legal_moves()}
            out["card"] = {"id": row["id"], "fen": row["fen"], "turn": pos.turn, "legal": legal}
        return out

    def answer(self, card_id: int, move: str, grade: str | None = None, now: float | None = None) -> dict:
        """Your move on a card. Wrong: the card is not learned, and stays active. Right, and with a ``grade``
        ("hard" or "easy", how it was to remember): it is hidden, for longer than the last time. Either way
        the answer is given: the engine's choice and its line, the move the card is about, and why that was
        bad, if it has been written."""
        now = time.time() if now is None else now
        with self.store.lock:
            row = self.store.one("SELECT * FROM cards WHERE id=?", (card_id,))
            if row is None:
                raise CardError(f"no card {card_id}")
            pos = Position.from_fen(row["fen"])
            try:
                uci = pos.parse(str(move))
            except ValueError:
                raise CardError(f"{move!r} is not a legal move here") from None
            lines = json.loads(row["lines"])
            right = uci in right_moves(lines, pos.turn == "w", row["uci"])
            if not right:
                self.store.run("UPDATE cards SET interval=0, due=?, reviewed_at=? WHERE id=?", (now, now, card_id))
            elif grade is not None and row["due"] <= now:  # a card that is not active has been graded already
                days = hidden_for(row["interval"], grade)
                self.store.run("UPDATE cards SET interval=?, due=?, reviewed_at=? WHERE id=?", (days, due_in(days, now), now, card_id))
            deck, best = deck_of(row["cpl"]), lines[0]
            return {
                "right": right,
                "move": {"uci": uci, "san": pos.san(uci), "fen": pos.push(uci).fen()},
                "best": {"uci": best["moves"][0], "san": best["san"][0], "line": best["san"], "cp": best["cp"], "mate": best["mate"]},
                "played": {"uci": row["uci"], "san": row["san"], "cpl": row["cpl"], "deck": deck},
                "explanation": row["explanation"],
                "active": next(d["active"] for d in self.decks(now) if d["key"] == deck),
            }
