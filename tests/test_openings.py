"""The opening books: every line is legal chess, and the opponent only ever answers for its own side."""

import random

from chesstutor import openings, opponent
from chesstutor.chess import START_FEN, Position
from tests import function_tests


def test_every_line_is_legal_and_starts_from_the_start():
    for key in openings.OPENINGS:
        book = openings._book(key)  # raises on a move that cannot be played
        assert book, key


def test_the_opponent_follows_its_opening_and_transpositions_count():
    start = Position.from_fen(START_FEN)
    assert openings.book_move("sicilian-najdorf", start.push("e2e4")) == "c7c5"
    assert openings.book_move("sicilian-najdorf", start) is None  # it is Black: nothing to play for White
    assert openings.book_move("london", start) == "d2d4"
    assert openings.book_move(None, start) is None
    # 1. Nf3 Nf6 2. c4 g6 reaches a King's Indian position by another road
    pos = start
    for m in ("g1f3", "g8f6", "c2c4"):
        pos = pos.push(m)
    assert openings.book_move("kings-indian", pos) == "g7g6"
    # off the book: nothing
    assert openings.book_move("french", start.push("a2a3")) is None


def test_book_moves_are_always_legal():
    rng = random.Random(3)
    for key, (_, side, _) in openings.OPENINGS.items():
        for _ in range(20):
            pos = Position.from_fen(START_FEN)
            for _ in range(24):
                legal = pos.legal_moves()
                if not legal:
                    break
                if pos.turn == side:
                    move = openings.book_move(key, pos, rng) or rng.choice(legal)
                else:
                    move = rng.choice(legal)
                assert move in legal
                pos = pos.push(move)


def test_choices_are_grouped_by_the_opponents_side():
    c = openings.choices()
    assert {o["key"] for o in c["w"]} >= {"italian", "london"} and {o["key"] for o in c["b"]} >= {"french", "slav"}
    assert openings.name_of(None) == "Engine's choice"


def test_temperature_falls_as_the_rating_rises():
    temps = [opponent.temperature(e) for e in range(opponent.ELO_MIN, opponent.SF_MIN, 50)]
    assert temps == sorted(temps, reverse=True) and temps[0] > temps[-1] > 0


load_tests = function_tests(globals())
