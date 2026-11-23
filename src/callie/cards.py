"""Flashcards of your own errors: the position before a move that lost too much, to find the better move in.

Three decks, by what the move lost. A card is one position with one move played in it: the same move in the same
position in another game makes no second card, it makes the first a new card again, and a card of that game: a
card is deleted with the game its move was last played in.

A card can be removed by hand (the error is not one to study: a move made for want of time, say). It is out of
its deck for good, but its row stays, in the state "removed": that is what keeps the game it is of from making
it again. The same move played in that position in a later game does make it a card again.

When a card is shown is Anki's scheduling, with Anki's default settings: the rules of its scheduler
(https://github.com/ankitects/anki, rslib/src/scheduler/states, read at commit 0302650) written again here, not
its code. A wrong answer is Anki's "Again"; a right one is rated Hard, Good or Easy.

- A new card is learned in steps of 1 and 10 minutes: Good moves it a step on, and from the last to a day's
  interval; Easy takes it straight to four days; Again puts it back on the first step, a minute away.
- A learned card comes back after its interval in days, which each right answer lengthens: Hard by 1.2, Good by
  the card's ease (2.5 to begin with), Easy by the ease and 1.3 more, each counted from when the card was
  answered if that was late. Hard lowers the ease by 0.15 and Easy raises it by as much.
- A learned card answered wrong lapses: its ease drops by 0.2 (never below 1.3), its interval to a day, and it
  is learned again: back in 10 minutes, and from there Good returns it to its interval.
- The day a card comes back on is drawn from a few days around its interval, wider for longer ones, so that
  cards made in one game do not all come back together.

Two things are not Anki's defaults: no interval is longer than LONGEST (Anki: a hundred years), so that a well
known card still comes back; and a card in a step is hidden until the step is over even when nothing else is
left to review (Anki would show it up to twenty minutes early).
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import time as clock

from .chess import Position
from .engine import loss
from .store import Store

DECKS = (
    ("blunder", "Blunders", 200),
    ("mistake", "Mistakes", 100),
    ("inaccuracy", "Inaccuracies", 50),
)  # centipawns lost, at least
NEAR = 10  # another of the engine's lines within this many centipawns of its first is a right answer too
RATINGS = ("again", "hard", "good", "easy")
LEARN_STEPS = (60, 600)  # seconds: the steps of a new card
RELEARN_STEPS = (600,)  # and of one that lapsed
GRADUATING = {"good": 1, "easy": 4}  # days: the first interval of a card that leaves the steps
EASE, EASE_LEAST = 2.5, 1.3  # what Good multiplies an interval by: to begin with, and at the least
EASE_CHANGE = {"again": -0.2, "hard": -0.15, "good": 0.0, "easy": 0.15}
HARD_FACTOR, EASY_BONUS = 1.2, 1.3
LAPSE_FACTOR, LAPSE_LEAST = 0.0, 1  # what is left of the interval of a card that lapsed, and the least it is, in days
FUZZ = (
    (2.5, 7.0, 0.15),
    (7.0, 20.0, 0.1),
    (20.0, float("inf"), 0.05),
)  # from, to, days of spread for each day of interval in between
LONGEST = 180  # days: however well it is known, a card comes back this often
DAY_STARTS = (
    4  # o'clock: a card due on a day is active from then, so one answered late at night is there in the morning
)
DAY = 86400


class CardError(Exception):
    pass


def deck_of(cpl: int) -> str | None:
    """The deck a move that lost ``cpl`` centipawns belongs in; None: it lost too little for a card."""
    return next((key for key, _, least in DECKS if cpl >= least), None)


def day_of(when: float) -> int:
    """The number of the day a moment falls on (days start at DAY_STARTS)."""
    return (datetime.fromtimestamp(when) - timedelta(hours=DAY_STARTS)).date().toordinal()


def due_in(days: int, now: float) -> float:
    """When a card hidden for ``days`` from ``now`` is active again: the start of that day."""
    day = (datetime.fromtimestamp(now) - timedelta(hours=DAY_STARTS)).date() + timedelta(days=days)
    return datetime.combine(day, clock(DAY_STARTS)).timestamp()


def _round(x: float) -> int:
    return int(x + 0.5)  # halves go up, as in Anki


def days(interval: float, least: int = 1, luck: float | None = None, most: int = LONGEST) -> int:
    """An interval as it is given to a card: whole days, from ``least`` to ``most``. With ``luck`` (0 to 1)
    it is drawn from the days around it: none for less than 2.5 days, then a day either side, and more
    for each further day (FUZZ)."""
    most = max(most, 1)
    least = min(max(least, 1), most)
    if luck is None:
        return min(max(_round(interval), least), most)
    interval = min(max(interval, least), most)
    spread = 0.0 if interval < 2.5 else 1.0 + sum(by * max(0.0, min(interval, end) - start) for start, end, by in FUZZ)
    low, high = (min(max(_round(x), least), most) for x in (interval - spread, interval + spread))
    if high == low and 2 < high < most:
        high += 1
    return int(low + luck * (1 + high - low))


def rate(card: dict, rating: str, now: float, luck: float | None = None) -> dict:
    """What an answer does to a card's schedule: its ``state`` ("new", "learning", "review", "relearning"),
    the ``steps`` it has yet to pass, its ``interval`` in days, its ``ease`` and when it is ``due``, after
    being rated "again", "hard", "good" or "easy" at ``now``. ``luck`` (0 to 1; None: none) picks the day
    from around an interval, and stretches a step by up to a quarter (five minutes at most)."""
    if rating not in RATINGS:
        raise CardError(f"a right answer is rated one of {', '.join(RATINGS[1:])}")
    interval, ease = card["interval"], card["ease"]

    def learned(interval: int, ease: float) -> dict:
        return {"state": "review", "steps": 0, "interval": interval, "ease": ease, "due": due_in(interval, now)}

    def step(state: str, steps: int, delay: int, interval: int, ease: float) -> dict:
        delay += int((luck or 0.0) * min(delay * 0.25, 300))
        return {"state": state, "steps": steps, "interval": interval, "ease": ease, "due": now + delay}

    if card["state"] == "review":
        if rating == "again":  # a lapse
            left = days(max(interval, 1) * LAPSE_FACTOR, LAPSE_LEAST, luck)
            return step(
                "relearning", len(RELEARN_STEPS), RELEARN_STEPS[0], left, max(EASE_LEAST, ease + EASE_CHANGE["again"])
            )
        late = max(0, day_of(now) - day_of(card["due"]))
        base = max(interval, 1)
        hard = days(base * HARD_FACTOR, interval + 1, luck)
        good = days((base + late / 2) * ease, hard + 1, luck)
        easy = days((base + late) * ease * EASY_BONUS, good + 1, luck)
        return learned({"hard": hard, "good": good, "easy": easy}[rating], max(EASE_LEAST, ease + EASE_CHANGE[rating]))

    again = card["state"] == "relearning"  # learning what was known before; otherwise for the first time
    state, steps = ("relearning", RELEARN_STEPS) if again else ("learning", LEARN_STEPS)
    left = len(steps) if card["state"] == "new" else card["steps"]
    at = max(0, min(len(steps) - left, len(steps) - 1))  # the step it is on
    if rating == "again":
        return step(
            state, len(steps), steps[0], days(max(interval, 1) * LAPSE_FACTOR, LAPSE_LEAST, luck) if again else 0, ease
        )
    if rating == "hard":  # the same step once more; the first one a little longer, so that it is not "again"
        delay = (
            steps[at]
            if at
            else (steps[0] + steps[1]) // 2
            if len(steps) > 1
            else min(steps[0] * 3 // 2, steps[0] + DAY)
        )
        return step(state, len(steps) - at, delay, interval, ease)
    if rating == "good" and at + 1 < len(steps):
        return step(state, len(steps) - at - 1, steps[at + 1], interval, ease)
    if again:  # back to the interval the lapse left it
        return learned(min(interval + (rating == "easy"), LONGEST), ease)
    return learned(days(GRADUATING[rating], 1, luck), EASE)


def waits(card: dict, now: float) -> dict[str, str]:
    """How long each rating of a right answer would hide a card for, as written on its button: "10m", "4d"."""
    out = {}
    for rating in RATINGS[1:]:
        after = rate(card, rating, now)
        out[rating] = (
            f"{after['interval']}d" if after["state"] == "review" else f"{max(1, _round((after['due'] - now) / 60))}m"
        )
    return out


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
        self.luck = self.rng.random  # what picks a day from around an interval (None from it: the interval itself)
        self.made = threading.Event()  # set when a card is made: what writes their explanations waits on it

    def note(
        self,
        fen: str,
        uci: str,
        san: str,
        cpl: int,
        lines: list[dict],
        at: float,
        prev: str | None = None,
        game: str | None = None,
    ) -> None:
        """A move of yours, played at ``at`` in the position ``fen``, with what it lost and the engine's lines
        there: a card if it lost enough, or, where there is one for it already, that card new again.
        ``prev`` is the move that led to the position (None: none did, or it is not known), ``game`` the game
        it was played in (None: not known). Told of the same move twice (or of an older game after a newer
        one), it does nothing the second time, but learn ``prev`` and ``game`` where the card had none.
        """
        if deck_of(cpl) is None or not lines or not lines[0]["moves"] or lines[0]["moves"][0] == uci:
            return
        position = Position.from_fen(fen).key()
        with self.store.lock:
            have = self.store.one(
                "SELECT id, erred_at, prev, game_id FROM cards WHERE position=? AND uci=?", (position, uci)
            )  # one that was removed too: of the game it was removed in, it stays so
            if have is None:
                self.store.run(
                    "INSERT INTO cards (position, uci, san, fen, prev, cpl, lines, erred_at, due, game_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (position, uci, san, fen, prev, cpl, json.dumps(lines), at, at, game),
                )
                self.made.set()
            elif have["erred_at"] < at:
                self.store.run(
                    "UPDATE cards SET san=?, fen=?, prev=?, cpl=?, lines=?, erred_at=?, state='new', steps=0, interval=0, "
                    "due=?, game_id=? WHERE id=?",
                    (san, fen, prev, cpl, json.dumps(lines), at, at, game, have["id"]),
                )
            elif have["erred_at"] == at and (
                (prev and have["prev"] is None) or (game and have["game_id"] is None)
            ):  # a card from before they had these
                self.store.run(
                    "UPDATE cards SET prev=coalesce(prev, ?), game_id=coalesce(game_id, ?) WHERE id=?",
                    (prev, game, have["id"]),
                )

    def drop(self, game_id: str) -> list[dict]:
        """Remove the cards of a game (it was deleted): those whose move was last played in it. Returns them
        as they were."""
        with self.store.lock:
            rows = self.store.all("SELECT * FROM cards WHERE game_id=?", (game_id,))
            self.store.run("DELETE FROM cards WHERE game_id=?", (game_id,))
        return rows

    def resume(self, card: dict) -> None:
        """A card that was dropped and has been made again, of another game with the same move in the same
        position: it stands where it stood in its schedule (removed, if it was), and keeps its explanation and
        its conversation with Claude."""
        kept = (
            "state",
            "steps",
            "interval",
            "ease",
            "due",
            "reviewed_at",
            "explanation",
            "session_id",
            "session_started",
        )
        self.store.run(
            f"UPDATE cards SET {', '.join(f'{k}=?' for k in kept)} WHERE position=? AND uci=?",
            (*(card[k] for k in kept), card["position"], card["uci"]),
        )

    def remove(self, card_id: int) -> None:
        """Take a card out of its deck for good: it is not one to study. Its game does not make it again; the
        same move in that position in a later game does."""
        with self.store.lock:
            if self.fen(card_id) is None:
                raise CardError(f"no card {card_id}")
            self.store.run("UPDATE cards SET state='removed' WHERE id=?", (card_id,))

    def _active(self, deck: str, now: float) -> list[dict]:
        if deck not in [key for key, _, _ in DECKS]:
            raise CardError(f"no deck {deck!r}")
        rows = self.store.all("SELECT * FROM cards WHERE due <= ? AND state != 'removed'", (now,))
        return [r for r in rows if deck_of(r["cpl"]) == deck]

    def fen(self, card_id: int) -> str | None:
        """The position of a card as it is now (None: there is no such card, or it was removed): what a page
        that kept one from earlier tells by whether it is still there, and still as it was."""
        row = self.store.one("SELECT fen FROM cards WHERE id=? AND state != 'removed'", (card_id,))
        return row and row["fen"]

    def explained(self) -> dict[tuple[str, str], str]:
        """The explanation of each move that has one, by its position (a `Position.key`) and the move."""
        rows = self.store.all("SELECT position, uci, explanation FROM cards WHERE explanation IS NOT NULL")
        return {(r["position"], r["uci"]): r["explanation"] for r in rows}

    def unexplained(self, skip: tuple[int, ...] = ()) -> dict | None:
        """The card whose explanation is to be written next: of those without one, the latest played. None
        is written for a card that was removed."""
        rows = self.store.all(
            "SELECT * FROM cards WHERE explanation IS NULL AND state != 'removed' ORDER BY erred_at DESC"
        )
        return next((r for r in rows if r["id"] not in skip), None)

    def explain(self, card: dict, text: str) -> None:
        """Keep what was written for a card, if it is still there: the number of one deleted with its game
        meanwhile may be another card's by now."""
        self.store.run(
            "UPDATE cards SET explanation=? WHERE id=? AND position=? AND uci=?",
            (text, card["id"], card["position"], card["uci"]),
        )

    def decks(self, now: float | None = None) -> list[dict]:
        """Each deck with how many cards it holds and how many of them are active."""
        now = time.time() if now is None else now
        rows = self.store.all("SELECT cpl, due FROM cards WHERE state != 'removed'")
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
        every legal move of its position as a game's state has them, and the move that led to it ("last": None
        when none did, or it is not known). "card" is None when none is active;
        "wait" is then the seconds until one that is in a step comes back (None: none is)."""
        now = time.time() if now is None else now
        rows = self._active(deck, now)
        out = {
            "deck": deck,
            "name": next(name for key, name, _ in DECKS if key == deck),
            "active": len(rows),
            "card": None,
            "wait": None,
        }
        if not rows:
            stepping = self.store.all(
                "SELECT cpl, due FROM cards WHERE state IN ('learning', 'relearning') AND due > ?", (now,)
            )
            out["wait"] = min((r["due"] - now for r in stepping if deck_of(r["cpl"]) == deck), default=None)
        if rows:
            row = self.rng.choice([r for r in rows if r["id"] != skip] or rows)
            pos = Position.from_fen(row["fen"])
            legal = {m: {"san": pos.san(m), "fen": pos.push(m).fen()} for m in pos.legal_moves()}
            out["card"] = {"id": row["id"], "fen": row["fen"], "turn": pos.turn, "legal": legal, "last": row["prev"]}
        return out

    def _move(self, card_id: int, move: str) -> tuple[dict, Position, str]:
        """A card, its position, and a move in it (as UCI, given as that or as SAN)."""
        row = self.store.one("SELECT * FROM cards WHERE id=? AND state != 'removed'", (card_id,))
        if row is None:
            raise CardError(f"no card {card_id}")
        pos = Position.from_fen(row["fen"])
        try:
            return row, pos, pos.parse(str(move))
        except ValueError:
            raise CardError(f"{move!r} is not a legal move here") from None

    def lost(self, card_id: int, move: str, worth: Callable[[str, str], int]) -> int:
        """Centipawns a move on a card gives away, against the engine's first choice there. The move of the
        game lost what the card says, and a move the engine gave a line to what that search made of it; of any
        other, ``worth`` is asked: what a position (a FEN) is worth to White after a move in it."""
        row, pos, uci = self._move(card_id, move)
        if uci == row["uci"]:
            return row["cpl"]
        lines = [ln for ln in json.loads(row["lines"]) if ln["moves"]]
        known = next((ln["value"] for ln in lines if ln["moves"][0] == uci), None)
        return loss(lines[0]["value"], worth(row["fen"], uci) if known is None else known, pos.turn == "w")

    def answer(self, card_id: int, move: str, grade: str | None = None, now: float | None = None) -> dict:
        """Your move on a card. Wrong: the card is rated "again". Right, and with a ``grade`` ("hard", "good"
        or "easy", how it was to remember): it is rated that. Right with none: nothing changes, and "next"
        says how long each grade would hide it for. Either way the answer is given: the engine's choice and its
        line, the move the card is about, and why that was bad, if it has been written."""
        now = time.time() if now is None else now
        if grade is not None and grade not in RATINGS[1:]:
            raise CardError(f"grade must be one of {', '.join(RATINGS[1:])}")
        with self.store.lock:
            row, pos, uci = self._move(card_id, move)
            lines = json.loads(row["lines"])
            right = uci in right_moves(lines, pos.turn == "w", row["uci"])
            rating = grade if right else "again"
            if rating == "again" or (rating and row["due"] <= now):  # right on a card that is not active: rated already
                new = rate(row, rating, now, self.luck())
                self.store.run(
                    "UPDATE cards SET state=?, steps=?, interval=?, ease=?, due=?, reviewed_at=? WHERE id=?",
                    (new["state"], new["steps"], new["interval"], new["ease"], new["due"], now, card_id),
                )
            deck, best = deck_of(row["cpl"]), lines[0]
            return {
                "right": right,
                "move": {"uci": uci, "san": pos.san(uci), "fen": pos.push(uci).fen()},
                "best": {
                    "uci": best["moves"][0],
                    "san": best["san"][0],
                    "line": best["san"],
                    "cp": best["cp"],
                    "mate": best["mate"],
                },
                "played": {"uci": row["uci"], "san": row["san"], "cpl": row["cpl"], "deck": deck},
                "explanation": row["explanation"],
                "next": waits(row, now) if right and grade is None and row["due"] <= now else None,
                "active": next(d["active"] for d in self.decks(now) if d["key"] == deck),
            }
