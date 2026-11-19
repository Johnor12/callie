"""Your ratings, reckoned from the games you have finished against opponents of a known one.

One for each time format, as on Lichess: untimed games, and timed ones by what Lichess calls their clock (bullet,
blitz, rapid, classical; "10+3" is rapid). A game counts towards the rating of its own format only.

Glicko (Glickman, 1999), a game at a time: a rating and how unsure it is (its deviation). A new player is 1500
give or take 350, as on Lichess, whose scale the opponents' ratings are on; each result moves the rating by more
the less sure it is, and makes it surer. The opponents are engines set to a rating, so theirs are taken as exact.
The deviation is not let below `SETTLED`: someone learning the game is a moving target, and a rating that has
stopped listening would never catch up with them.
"""

from __future__ import annotations

import math

from .allie import speed

FORMATS = ("untimed", "bullet", "blitz", "rapid", "classical")
START, UNSURE = 1500.0, 350.0  # where a rating begins, and its deviation then
SETTLED = 70.0  # the least the deviation falls to: a game between equals then moves the rating about 13 points
PROVISIONAL = 110.0  # above this deviation a rating is shown as a guess (Lichess's "?")
_Q = math.log(10) / 400


def format_of(time_control: str | None) -> str:
    """The format a game's clock ("10+3": minutes + seconds a move; None: untimed) puts it in."""
    if not time_control or time_control == "untimed":
        return "untimed"
    minutes, _, seconds = time_control.partition("+")
    return speed(float(minutes) * 60, float(seconds or 0))


def expected(rating: float, opponent: float) -> float:
    """The score a player of ``rating`` is expected to make against ``opponent``, 0 to 1."""
    return 1 / (1 + 10 ** ((opponent - rating) / 400))


def after(rating: float, deviation: float, opponent: float, score: float) -> tuple[float, float]:
    """The rating and deviation after one game: ``score`` 1 for a win, 0.5 for a draw, 0 for a loss."""
    e = expected(rating, opponent)
    precision = 1 / deviation**2 + _Q**2 * e * (1 - e)
    return rating + _Q / precision * (score - e), max(SETTLED, math.sqrt(1 / precision))


def score(game: dict) -> float | None:
    """What a finished game was worth to you, or None if it is not one a rating can be reckoned from: one still
    being played, or one against an opponent with no rating (Stockfish at full strength)."""
    if game["status"] != "finished" or game["elo"] is None or game["result"] not in ("1-0", "0-1", "1/2-1/2"):
        return None
    if game["result"] == "1/2-1/2":
        return 0.5
    return 1.0 if (game["result"] == "1-0") == (game["user_color"] == "w") else 0.0


def history(games: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """Where you stand in each format, and what each game did to it.

    ``games``: rows of the database, in any order; they are taken in the order they ended. Returns the standing
    of every format with a rated game ({"elo", "deviation", "provisional", "games"}, in the order of FORMATS)
    and, by game id, the format's rating before and after that game and the change, in whole points (the changes
    add up to the difference between the ratings shown).
    """
    now: dict[str, list[float]] = {}  # format -> [rating, deviation, games]
    changes = {}
    for g in sorted(games, key=lambda g: (g["finished_at"] or g["updated_at"], g["created_at"])):
        got = score(g)
        if got is None:
            continue
        fmt = format_of(g["time_control"])
        at = now.setdefault(fmt, [START, UNSURE, 0])
        before = at[0]
        at[0], at[1] = after(at[0], at[1], g["elo"], got)
        at[2] += 1
        changes[g["id"]] = {"format": fmt, "before": round(before), "after": round(at[0]), "change": round(at[0]) - round(before)}
    standing = {
        fmt: {"elo": round(r), "deviation": round(d), "provisional": d > PROVISIONAL, "games": n}
        for fmt in FORMATS
        for r, d, n in [now.get(fmt, (0, 0, 0))]
        if n
    }
    return standing, changes
