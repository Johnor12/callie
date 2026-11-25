"""The cards made of your errors: which deck, what answers one, how long it is hidden, and that games make them.
And the cards of the heuristics, and of the openings: that their file makes them, and that they are answered as
the others are."""

import json
import time
from datetime import datetime

from callie import cards, heuristics, history, opponent
from callie.cards import CardError, Cards
from callie.store import Store
from tests import function_tests, need_engine, scratch_config
from tests.test_play import done, service, until
from tests.test_server import Running, output

# After 1. e4 e5 2. Qh5 Nc6 3. Bc4, Black to move: g6 and Qe7 hold, Nh6 less well, and Nf6 is mated.
FEN = "r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 3 3"
LINES = [
    {"moves": ["g7g6", "h5f3"], "san": ["g6", "Qf3"], "cp": 20, "mate": None, "value": 20},
    {"moves": ["d8e7"], "san": ["Qe7"], "cp": 26, "mate": None, "value": 26},
    {"moves": ["g8h6"], "san": ["Nh6"], "cp": 95, "mate": None, "value": 95},
]
NOON = datetime(2026, 11, 19, 12).timestamp()
DAY = 86400


def at(*when):
    return datetime(*when).timestamp()


def scratch():
    c = Cards(Store(scratch_config().home / "cards.db"))
    c.luck = lambda: None  # no day drawn from around an interval: the interval itself
    return c


def card(state="new", steps=0, interval=0, ease=2.5, due=NOON):
    return {"state": state, "steps": steps, "interval": interval, "ease": ease, "due": due}


def after(c, rating, now=NOON, luck=None):
    """A card's schedule after an answer, with its due time as seconds (in a step) or days (learned) from ``now``."""
    new = cards.rate(c, rating, now, luck)
    wait = round(new.pop("due") - now) if new["state"] != "review" else cards.day_of(new["due"]) - cards.day_of(now)
    return (new["state"], new["steps"], new["interval"], round(new["ease"], 2), wait)


def test_decks_and_what_answers_a_card():
    assert [cards.deck_of(n) for n in (49, 50, 99, 100, 199, 200, 1000)] == [
        None,
        "inaccuracy",
        "inaccuracy",
        "mistake",
        "mistake",
        "blunder",
        "blunder",
    ]
    # due at the start of a day, which is at four in the morning
    assert cards.due_in(1, at(2026, 11, 19, 21)) == at(2026, 11, 20, 4)
    assert cards.due_in(1, at(2026, 11, 20, 1)) == at(2026, 11, 20, 4)  # one in the morning is still the day before
    assert cards.due_in(4, at(2026, 11, 19, 12)) == at(2026, 11, 23, 4)
    assert (
        cards.day_of(at(2026, 11, 20, 3)) == cards.day_of(at(2026, 11, 19, 5)) == cards.day_of(at(2026, 11, 20, 5)) - 1
    )
    # the engine's first choice and a line as good as it; never the move the card is about
    assert cards.right_moves(LINES, False, "g8f6") == ["g7g6", "d8e7"]
    assert cards.right_moves(LINES, False, "d8e7") == ["g7g6"]
    mates = [{"moves": ["a1a8"], "value": 9999}, {"moves": ["a1a7"], "value": 9997}, {"moves": ["a1a6"], "value": 400}]
    assert cards.right_moves(mates, True, "h2h3") == ["a1a8", "a1a7"]  # a slower mate is as good


def test_the_schedule_is_ankis():
    # a new card: steps of 1 and 10 minutes, then a day; easy goes straight to four days
    assert after(card(), "again") == ("learning", 2, 0, 2.5, 60)
    assert after(card(), "hard") == ("learning", 2, 0, 2.5, 330)  # between the two steps
    assert after(card(), "good") == ("learning", 1, 0, 2.5, 600)
    assert after(card(), "easy") == ("review", 0, 4, 2.5, 4)
    assert after(card("learning", 2), "good") == ("learning", 1, 0, 2.5, 600)
    assert after(card("learning", 1), "again") == ("learning", 2, 0, 2.5, 60)
    assert after(card("learning", 1), "hard") == ("learning", 1, 0, 2.5, 600)  # the same step again
    assert after(card("learning", 1), "good") == ("review", 0, 1, 2.5, 1)
    assert after(card("learning", 1), "easy") == ("review", 0, 4, 2.5, 4)
    # a learned card: hard by 1.2 and a lower ease, good by the ease, easy by the ease and 1.3 more and a higher ease
    known = card("review", 0, 10, 2.5)
    assert after(known, "hard") == ("review", 0, 12, 2.35, 12)
    assert after(known, "good") == ("review", 0, 25, 2.5, 25)
    assert after(known, "easy") == ("review", 0, 33, 2.65, 33)
    short = card("review", 0, 1, 2.5)  # each longer than the one before, whatever the arithmetic says
    assert [after(short, r)[2] for r in ("hard", "good", "easy")] == [2, 3, 4]
    assert after(short, "easy")[3] == 2.65  # as Anki's own test has it: 4 days, ease 2650
    assert after(card("review", 0, 10, 1.35), "hard")[3] == 1.3  # the ease has a floor
    # answered late, a right answer counts the days it was remembered for: half of them for good, all for easy
    late = card("review", 0, 10, 2.5, due=NOON - 8 * DAY)
    assert [after(late, r)[2] for r in ("hard", "good", "easy")] == [12, 35, 59]
    # and none is longer than the longest
    assert [after(card("review", 0, 170, 2.5), r)[2] for r in ("hard", "good", "easy")] == [180, 180, 180]
    # a lapse: the ease drops, the interval is a day, and it is back in ten minutes to be learned again
    assert after(known, "again") == ("relearning", 1, 1, 2.3, 600)
    lapsed = card("relearning", 1, 1, 2.3)
    assert after(lapsed, "again") == ("relearning", 1, 1, 2.3, 600)  # no further drop
    assert after(lapsed, "hard") == ("relearning", 1, 1, 2.3, 900)
    assert after(lapsed, "good") == ("review", 0, 1, 2.3, 1)
    assert after(lapsed, "easy") == ("review", 0, 2, 2.3, 2)
    try:
        cards.rate(known, "soso", NOON)
        raise AssertionError("an unknown rating was accepted")
    except CardError:
        pass
    # the day drawn from around an interval (Anki's own expected values: lowest, middle, highest)
    for interval, least, most, want in (
        (1.0, 1, 1000, (1, 1, 1)), (2.49, 1, 1000, (2, 2, 2)), (2.5, 1, 1000, (2, 3, 4)), (7.0, 1, 1000, (5, 7, 9)),
        (17.0, 1, 1000, (14, 17, 20)), (37.0, 1, 1000, (33, 37, 41)), (2.0, 3, 1000, (3, 4, 4)), (2.0, 3, 3, (3, 3, 3)),
        (19.9, 3, 1000, (17, 20, 23)), (100.0, 101, 1000, (101, 105, 108)), (100.0, 1, 99, (92, 96, 99)), (100.0, 97, 103, (97, 100, 103)),
    ):  # fmt: skip
        assert tuple(cards.days(interval, least, luck, most) for luck in (0.0, 0.5, 0.99)) == want, interval
    assert [cards.days(1.5, 1, None, 100), cards.days(0.1, 1, None, 100), cards.days(101, 1, None, 100)] == [2, 1, 100]
    assert {after(card(), "easy", luck=luck)[2] for luck in (0.0, 0.4, 0.8)} == {3, 4, 5}
    assert after(card(), "good", luck=0.99)[4] == 600 + 148  # a step is stretched by up to a quarter
    assert after(card("review", 0, 10), "again", luck=0.99)[4] == 600 + 148  # and by five minutes at most
    assert cards.waits(card(), NOON) == {"hard": "6m", "good": "10m", "easy": "4d"}
    assert cards.waits(known, NOON) == {"hard": "12d", "good": "25d", "easy": "33d"}


def test_a_card_is_made_once_and_reviewed():
    c = scratch()
    c.note(FEN, "g8f6", "Nf6", 30, LINES, NOON)  # lost too little
    c.note(FEN, "g7g6", "g6", 250, LINES, NOON)  # the engine's own choice
    assert [d["cards"] for d in c.decks(NOON)] == [0, 0, 0, 0, 0]
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON)
    c.note(FEN, "g8h6", "Nh6", 75, LINES, NOON, "f1c4")
    assert (
        c.draw("blunder", now=NOON)["card"]["last"] is None and c.draw("inaccuracy", now=NOON)["card"]["last"] == "f1c4"
    )
    c.note(
        FEN, "g8f6", "Nf6", 990, LINES, NOON, "f1c4"
    )  # told again, now with the move that led there: a card from before that was kept
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON, "d3c4")  # known already: left as it is
    assert c.draw("blunder", now=NOON)["card"]["last"] == "f1c4"
    c.note(FEN.replace(" 3 3", " 7 9"), "g8f6", "Nf6", 990, LINES, NOON)  # the same move, told of again
    assert [(d["key"], d["cards"], d["active"]) for d in c.decks(NOON)] == [
        ("blunder", 1, 1),
        ("mistake", 0, 0),
        ("inaccuracy", 1, 1),
        ("heuristic", 0, 0),
        ("opener", 0, 0),
    ]
    assert c.draw("mistake", now=NOON) == {
        "deck": "mistake",
        "name": "Mistakes",
        "active": 0,
        "card": None,
        "wait": None,
    }
    drawn = c.draw("blunder", now=NOON)
    one = drawn["card"]
    cid = one["id"]
    assert drawn["active"] == 1 and one["fen"] == FEN and one["turn"] == "b" and one["legal"]["g7g6"]["san"] == "g6"
    assert c.draw("blunder", skip=cid, now=NOON)["card"]["id"] == cid  # the only one: it is shown again
    for bad in (dict(move="e2e4"), dict(move="g6", grade="soso"), dict(move="g6", grade="again")):
        try:
            c.answer(cid, **bad)
            raise AssertionError(f"accepted {bad}")
        except CardError:
            pass
    # wrong: the answer is given, and the card is hidden for a minute
    r = c.answer(cid, "Nh6", now=NOON)
    assert (
        not r["right"] and r["move"]["san"] == "Nh6" and r["best"]["san"] == "g6" and r["best"]["line"] == ["g6", "Qf3"]
    )
    assert (
        r["played"] == {"uci": "g8f6", "san": "Nf6", "cpl": 990, "deck": "blunder"}
        and r["active"] == 0
        and r["next"] is None
    )
    # what a move loses: the game's as the card has it, one of the engine's lines by that search, another by a new one
    asked = []
    worth = lambda fen, uci: asked.append((fen, uci)) or 520  # noqa: E731
    assert [c.lost(cid, m, worth) for m in ("Nf6", "Nh6", "g6", "Qe7")] == [990, 75, 0, 6] and not asked
    assert c.lost(cid, "a6", worth) == 500 and asked == [(FEN, "a7a6")]
    assert (
        c.lost(cid, "a6", lambda fen, uci: -40) == 0
    )  # better than the engine's choice, by a later search: nothing lost
    gone = c.draw("blunder", now=NOON + 30)
    assert gone["card"] is None and gone["wait"] == 30  # nothing to review, and how long until there is
    assert c.draw("blunder", now=NOON + 60)["card"]["id"] == cid
    assert not c.answer(cid, "Nf6", "easy", now=NOON + 60)["right"]  # the move of the game is wrong, however fast
    # right, but not yet said how hard: still active, and told what each answer would do
    r = c.answer(cid, "Qe7", now=NOON + 120)
    assert r["right"] and r["active"] == 1 and r["move"]["fen"].startswith("r1b1kbnr/ppppqppp")
    assert r["next"] == {"hard": "6m", "good": "10m", "easy": "4d"}
    # good: ten minutes, then a day, then three
    assert c.answer(cid, "g6", "good", now=NOON + 120)["active"] == 0
    assert c.draw("blunder", now=NOON + 600)["card"] is None and c.draw("blunder", now=NOON + 720)["card"]
    c.answer(cid, "g6", "good", now=NOON + 720)
    assert c.draw("blunder", now=NOON + 720)["wait"] is None  # learned: it is not a matter of minutes
    assert c.draw("blunder", now=NOON + 0.5 * DAY)["card"] is None and c.draw("blunder", now=NOON + DAY)["card"]
    c.answer(cid, "g6", "easy", now=NOON + 0.5 * DAY)  # not active: rated already, nothing changes
    assert c.store.one("SELECT state, interval FROM cards WHERE id=?", (cid,)) == {"state": "review", "interval": 1}
    c.answer(cid, "g6", "good", now=NOON + DAY)
    assert c.draw("blunder", now=NOON + 3 * DAY)["card"] is None and c.draw("blunder", now=NOON + 4 * DAY)["card"]
    # answered wrong when it comes back: a lapse, back in ten minutes and then tomorrow, with a lower ease
    c.answer(cid, "Nh6", now=NOON + 4 * DAY)
    assert c.draw("blunder", now=NOON + 4 * DAY)["wait"] == 600
    c.answer(cid, "g6", "good", now=NOON + 4 * DAY + 600)
    assert c.store.one("SELECT state, interval, ease FROM cards WHERE id=?", (cid,)) == {
        "state": "review",
        "interval": 1,
        "ease": 2.3,
    }
    assert c.draw("blunder", now=NOON + 4.5 * DAY)["card"] is None and c.draw("blunder", now=NOON + 5 * DAY)["card"]
    # the same move played again in a later game: no second card, this one is new again
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON)  # an older game, heard of late: nothing
    assert c.draw("blunder", now=NOON + 4.5 * DAY)["card"] is None
    c.note(FEN, "g8f6", "Nf6", 150, LINES, NOON + 4.5 * DAY, "d3c4")  # it cost less this time: the card moves deck
    assert (
        c.draw("mistake", now=NOON + 4.5 * DAY)["card"]["last"] == "d3c4"
    )  # and is of that game: reached by another move
    assert [(d["cards"], d["active"]) for d in c.decks(NOON + 4.5 * DAY)][:3] == [(0, 0), (1, 1), (1, 1)]
    assert c.store.one("SELECT state, interval FROM cards WHERE id=?", (cid,)) == {"state": "new", "interval": 0}
    # two active cards: the one just seen is not drawn again, and of several any may come
    c.note(FEN, "a7a6", "a6", 120, LINES, NOON + 4.5 * DAY)
    c.note(FEN, "b7b6", "b6", 130, LINES, NOON + 4.5 * DAY)
    seen = {c.draw("mistake", skip=cid, now=NOON + 5 * DAY)["card"]["id"] for _ in range(60)}
    assert len(seen) == 2 and cid not in seen
    assert len({c.draw("mistake", now=NOON + 5 * DAY)["card"]["id"] for _ in range(60)}) == 3


def test_a_card_is_dropped_with_its_game():
    c = scratch()
    games = lambda: {r["uci"]: r["game_id"] for r in c.store.all("SELECT uci, game_id FROM cards")}  # noqa: E731
    c.note(FEN, "g8h6", "Nh6", 75, LINES, NOON)  # a card from before its game was kept
    c.note(FEN, "g8h6", "Nh6", 75, LINES, NOON, game="aa11")  # told of it again: learned
    c.note(FEN, "g8h6", "Nh6", 75, LINES, NOON, game="cc33")  # known already: left as it is
    c.note(FEN, "a7a6", "a6", 120, LINES, NOON, game="aa11")
    c.note(FEN, "a7a6", "a6", 120, LINES, NOON + DAY, game="bb22")  # played again in a later game: that game's now
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON, game="aa11")
    c.note(FEN, "b7b6", "b6", 130, LINES, NOON)  # of no game that is known: never dropped
    assert games() == {"g8h6": "aa11", "a7a6": "bb22", "g8f6": "aa11", "b7b6": None}
    last = c.store.one("SELECT * FROM cards WHERE uci='g8f6'")
    c.store.run("UPDATE cards SET state='review', interval=9, explanation='why' WHERE id=?", (last["id"],))
    gone = c.drop("aa11")
    assert sorted(r["uci"] for r in gone) == ["g8f6", "g8h6"] and games() == {"a7a6": "bb22", "b7b6": None}
    assert c.drop("aa11") == [] and c.drop("zz99") == []
    # a card made after it may take its number: what was being written for the one that went is not kept for it
    c.note(FEN, "h7h6", "h6", 300, LINES, NOON, game="bb22")
    c.explain(last, "of another card")
    assert c.explained() == {}
    # made again of another game, a dropped card is where it was
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON - DAY, game="dd44")
    c.resume(next(r for r in gone if r["uci"] == "g8f6"))
    c.resume(next(r for r in gone if r["uci"] == "g8h6"))  # not made again: nothing
    again = c.store.one("SELECT * FROM cards WHERE uci='g8f6'")
    assert (again["game_id"], again["erred_at"], again["state"], again["interval"]) == ("dd44", NOON - DAY, "review", 9)
    assert c.explained() == {(again["position"], "g8f6"): "why"} and "g8h6" not in games()
    c.explain(again, "why not")
    assert c.explained() == {(again["position"], "g8f6"): "why not"}


def test_a_card_that_is_removed_stays_out_of_its_deck():
    c = scratch()
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON, game="aa11")
    c.note(FEN, "a7a6", "a6", 120, LINES, NOON - 60, game="aa11")
    cid = c.draw("blunder", now=NOON)["card"]["id"]
    c.answer(cid, "Nh6", now=NOON)  # wrong: in a step, a minute away
    c.remove(cid)
    assert [(d["cards"], d["active"]) for d in c.decks(NOON + DAY)][:3] == [(0, 0), (1, 1), (0, 0)]
    assert c.draw("blunder", now=NOON + 30) == {
        "deck": "blunder",
        "name": "Blunders",
        "active": 0,
        "card": None,
        "wait": None,
    }
    assert c.draw("blunder", now=NOON + DAY)["card"] is None and c.fen(cid) is None
    assert c.unexplained()["uci"] == "a7a6"  # the latest played is the one removed: nothing is written for it
    for gone in (
        lambda: c.answer(cid, "g6"),
        lambda: c.lost(cid, "g6", lambda fen, uci: 0),
        lambda: c.remove(cid),
        lambda: c.remove(999),
    ):
        try:
            gone()
            raise AssertionError("a card that is not there was found")
        except CardError:
            pass
    c.explain(c.store.one("SELECT * FROM cards WHERE id=?", (cid,)), "why")  # one being written as it went: kept
    assert list(c.explained().values()) == ["why"]
    # its game tells of it again (a restart does), and so does an older one: it stays out
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON, game="aa11")
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON - DAY, game="bb22")
    assert c.fen(cid) is None
    # its game is deleted and the older one has the move too: that game's now, and out still
    gone = c.drop("aa11")
    assert sorted(r["uci"] for r in gone) == ["a7a6", "g8f6"]
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON - DAY, game="bb22")
    c.resume(next(r for r in gone if r["uci"] == "g8f6"))
    assert c.store.one("SELECT game_id, state FROM cards") == {"game_id": "bb22", "state": "removed"}
    assert [d["cards"] for d in c.decks(NOON)][:3] == [0, 0, 0]
    # played again in a later game: a new card
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON + DAY, game="cc33")
    assert [(d["cards"], d["active"]) for d in c.decks(NOON + DAY)][:3] == [(1, 1), (0, 0), (0, 0)]
    again = c.store.one("SELECT game_id, state, steps, explanation FROM cards")
    assert again == {"game_id": "cc33", "state": "new", "steps": 0, "explanation": "why"}
    assert c.answer(c.draw("blunder", now=NOON + DAY)["card"]["id"], "g6", now=NOON + DAY)["next"]["good"] == "10m"


def test_cards_from_before_they_had_a_state():
    home = scratch_config().home
    store = Store(home / "old.db")
    Cards(store).note(FEN, "g8f6", "Nf6", 990, LINES, NOON)
    store.run("UPDATE cards SET interval=4")  # as the first version left a card it had hidden for four days
    assert Store(home / "old.db").one("SELECT state, steps, ease FROM cards") == {
        "state": "review",
        "steps": 0,
        "ease": 2.5,
    }


def test_a_game_makes_cards_and_the_page_reviews_them():
    svc = service(script=["e2e4", "d1h5", "f1c4", "h5f7"])  # the scholar's mate
    try:
        g = svc.create("b", None, None, None)
        until(svc, g["id"], lambda s: s["plies"] == 1)
        for mine in ("e5", "Nc6", "Nf6"):
            n = svc.state(g["id"])["plies"]
            svc.move(g["id"], mine)
            until(svc, g["id"], lambda s: s["plies"] == n + 2)
        until(svc, g["id"], lambda s: s["start_eval"] and all("verdict" in m for m in s["moves"]))
        card = svc.cards.draw("blunder")["card"]  # 3... Nf6, made as soon as the engine had seen it
        assert card["fen"] == FEN and svc.cards.answer(card["id"], "Nf6")["played"]["san"] == "Nf6"
        assert card["last"] == "f1c4"  # 3. Bc4, the opponent's move before it
        assert (
            svc.card_loss(card["id"], "a6")["cpl"] > 800
        )  # no line of the card's: the engine is asked, and it is mate here too
        assert (
            svc._worth("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1", "f7g7") == 10000
            and svc._worth("7k/5Q2/5K2/8/8/8/8/8 w - - 0 1", "f6g6") == 0
        )
        before = svc.store.all("SELECT * FROM cards")
        svc._cards_on_file()  # what a restart does: the same games make no more cards and none new again
        assert svc.store.all("SELECT * FROM cards") == before
        assert before and {c["game_id"] for c in before} == {g["id"]}

        def the_day_before(game_id, as_id):  # the same game on file once more
            svc.store.add_game({**svc.store.game(game_id), "id": as_id})
            svc.store.run(
                "INSERT INTO moves (game_id, ply, uci, san, fen, source, played_at, clock_ms) "
                "SELECT ?, ply, uci, san, fen, source, played_at - 86400, clock_ms FROM moves WHERE game_id=?",
                (as_id, game_id),
            )
            svc.store.run(
                "INSERT INTO evals (game_id, ply, fen, depth, value, cp, mate, lines) "
                "SELECT ?, ply, fen, depth, value, cp, mate, lines FROM evals WHERE game_id=?",
                (as_id, game_id),
            )

        the_day_before(g["id"], "older")  # the cards stay the later game's
        svc._cards_on_file()
        assert svc.store.all("SELECT * FROM cards") == before
        # a game's cards are deleted with it; those the older game has too are its cards now, where they stood
        svc.store.run("UPDATE cards SET state='review', interval=4, explanation='why' WHERE id=?", (card["id"],))
        svc.delete(g["id"])
        left = svc.store.all("SELECT * FROM cards")
        assert len(left) == len(before) and {c["game_id"] for c in left} == {"older"}
        nf6 = next(c for c in left if c["uci"] == "g8f6")
        was = next(c for c in before if c["uci"] == "g8f6")
        assert (nf6["state"], nf6["interval"], nf6["explanation"]) == ("review", 4, "why")
        assert nf6["erred_at"] == was["erred_at"] - 86400 and nf6["due"] == was["due"]
        # a card that was removed is not made again by the games it is of, and goes on to an older game as that
        the_day_before("older", "oldest")
        svc.cards.remove(nf6["id"])
        svc._cards_on_file()
        assert svc.store.all("SELECT * FROM cards") == [nf6 | {"state": "removed"}]
        assert svc.cards.draw("blunder")["card"] is None
        svc.delete("older")
        assert [(c["game_id"], c["state"]) for c in svc.store.all("SELECT * FROM cards")] == [("oldest", "removed")]
        svc.delete("oldest")
        assert svc.store.all("SELECT * FROM cards") == []
    finally:
        done(svc)


def test_the_cards_over_http():
    s = Running()
    try:
        s.svc.cards.note(FEN, "g8f6", "Nf6", 990, LINES, NOON - 30 * DAY)
        assert [d["active"] for d in s.call("GET", "/api/cards")[1]] == [1, 0, 0, 0, 0]
        assert s.call("GET", "/api/cards/nonsense")[0] == 404
        _, drawn = s.call("GET", "/api/cards/blunder")
        cid = drawn["card"]["id"]
        assert drawn["name"] == "Blunders" and s.call("GET", f"/api/cards/blunder?skip={cid}")[1]["card"]["id"] == cid
        # what a page asks of a card it kept from before: there, and that position still
        assert s.call("GET", f"/api/cards/{cid}")[1] == {"fen": FEN} and s.call("GET", "/api/cards/999")[1] == {
            "fen": None
        }
        assert s.call("POST", f"/api/cards/{cid}/answer", {"move": "zz"})[0] == 400
        assert s.call("POST", "/api/cards/999/answer", {"move": "g6"})[0] == 400
        assert s.call("POST", f"/api/cards/{cid}/answer", {"move": "g6", "grade": "soso"})[0] == 400
        assert s.call("POST", f"/api/cards/{cid}/answer", {"move": "g6"})[1]["next"]["good"] == "10m"
        wrong = s.call("POST", f"/api/cards/{cid}/answer", {"move": "h6"})[1]
        assert wrong["right"] is False and wrong["active"] == 0  # hidden for a minute
        assert s.call("GET", f"/api/cards/{cid}/loss?move=Nh6")[1] == {"cpl": 75}
        assert (
            s.call("GET", f"/api/cards/{cid}/loss?move=zz")[0] == 400
            and s.call("GET", "/api/cards/999/loss?move=g6")[0] == 400
        )
        gone = s.call("GET", "/api/cards/blunder")[1]
        assert gone["card"] is None and 50 < gone["wait"] <= 75
        s.svc.store.run("UPDATE cards SET due=0")  # the minute is over
        done = s.call("POST", f"/api/cards/{cid}/answer", {"move": "g6", "grade": "easy"})[1]
        assert (
            done["right"]
            and done["active"] == 0
            and set(done)
            == {"right", "active", "move", "best", "played", "heuristic", "opener", "shown", "explanation", "next"}
            and done["heuristic"] is None
            and done["opener"] is None
            and done["shown"] == []
        )
        assert s.svc.store.one("SELECT state, interval FROM cards")["state"] == "review"
        assert s.call("GET", "/api/cards/blunder")[1] | {"deck": 0, "name": 0} == {
            "deck": 0,
            "name": 0,
            "active": 0,
            "card": None,
            "wait": None,
        }
        # taken out of its deck: gone for the page, and not to be taken out twice
        assert s.call("POST", f"/api/cards/{cid}/delete") == (200, {"ok": True})
        assert s.call("GET", f"/api/cards/{cid}")[1] == {"fen": None}
        assert [d["cards"] for d in s.call("GET", "/api/cards")[1]] == [0, 0, 0, 0, 0]
        assert s.call("POST", f"/api/cards/{cid}/delete")[0] == 400
        assert s.call("POST", f"/api/cards/{cid}/answer", {"move": "g6"})[0] == 400
        page = s.call("GET", "/")[1]
        assert b'id="card-board"' in page and b'id="card-delete"' in page
    finally:
        s.close()


def explained(s, card_id, timeout=30):
    """A card's explanation, once the (fake) Claude has written it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = s.svc.store.one("SELECT explanation FROM cards WHERE id=?", (card_id,))["explanation"]
        if text:
            return text
        time.sleep(0.05)
    raise AssertionError("no explanation was written")


def test_a_card_is_explained_in_the_background():
    s = Running()
    old = opponent.pick
    try:
        need_engine(s.cfg)
        # a card whose game is not on file: Claude is asked all the same, with no game for its tools to read
        s.svc.cards.note(FEN, "g8h6", "Nh6", 75, LINES, NOON - 30 * DAY)
        cid = s.svc.store.one("SELECT id FROM cards")["id"]
        assert explained(s, cid) == "why: Explain why this move was inaccurate. (game 0, quiet 1)"
        assert s.call("POST", f"/api/cards/{cid}/answer", {"move": "g6"})[1]["explanation"].startswith("why: Explain")
        # a game: its blunder is explained, and the explanation is on the move, in the page's state and in Claude's tools
        moves = iter(["e2e4", "d1h5", "f1c4", "h5f7"])
        opponent.pick = lambda engine, fen, played, elo, ms, rng=None: next(moves)
        gid = s.call("POST", "/api/games", {"color": "b", "elo": None})[1]["id"]
        until(s.svc, gid, lambda g: g["plies"] == 1)
        for mine in ("e5", "Nc6", "Nf6"):
            n = s.svc.state(gid)["plies"]
            s.call("POST", f"/api/games/{gid}/move", {"move": mine})
            until(s.svc, gid, lambda g: g["plies"] == n + 2)
        until(s.svc, gid, lambda g: all("verdict" in m for m in g["moves"]))
        card = s.svc.store.one("SELECT * FROM cards WHERE uci='g8f6'")
        why = explained(s, card["id"])
        assert why == f"why: Explain why this move was a blunder. (game {gid}, quiet 1)"
        asked = s.svc.card_request(card)[1]
        assert "playing Black, played 3... Nf6 in this position: " + FEN in asked and "It is ply 6 of the game" in asked
        state = until(s.svc, gid, lambda g: g["moves"][5].get("explanation"))
        s.svc.explained()
        assert s.svc.state(gid)["version"] != state["version"]  # a page waiting on the game is told of a new one
        assert [m.get("explanation") for m in state["moves"]] == [None] * 5 + [why, None]

        def tool(name, **b):
            return s.call("POST", f"/api/games/{gid}/tool", {"name": name, "arguments": b.pop("arguments", {}), **b})[
                1
            ]["text"]

        by = "generated by Claude with access to Stockfish"
        assert f"Previous analysis of the student's moves below, {by}:\n3... Nf6: {why}" in tool("game")
        assert f"Previous analysis of the move, {by}: {why}" in tool("review", arguments={"from_ply": 6})
        assert "Previous analysis" not in tool("review", arguments={"to_ply": 5})
        # asked from the background, the engine is the one that gives way: a search of a set size, however long was asked for
        text = tool("analyze", arguments={"ply": 5, "seconds": 60, "lines": 2}, quiet=True)
        assert (
            "Black to move" in text
            and text.count("3... ") >= 2
            and s.svc.quiet.nice
            and s.svc.quiet.proc
            and not s.svc.explore.proc
        )
        assert s.svc.in_play() is False  # the game is over
    finally:
        opponent.pick = old
        s.close()


def test_a_card_has_a_claude_session_of_its_own():
    s = Running()
    try:
        s.svc.cards.note(FEN, "g8f6", "Nf6", 990, LINES, NOON - 30 * DAY)
        cid = s.svc.store.one("SELECT id FROM cards")["id"]
        explained(s, cid)  # written in the background first: what is put on the card here is not written over
        s.svc.cards.explain(s.svc.store.one("SELECT * FROM cards"), "Qxf7 is mate.")
        said = lambda term: json.loads(output(term, '"prompt"').split("\n")[0])  # noqa: E731
        # never asked about: nothing to show on its back, and nothing is started to find that out
        assert s.call("POST", f"/api/cards/{cid}/claude/resume") == (200, {"terminal": None})
        assert s.call("POST", "/api/cards/999/claude/resume")[0] == 400
        assert s.call("POST", f"/api/cards/{cid}/claude/say", {"text": "  "})[0] == 400 and not s.tutor.terms
        # a message from its back: a session of the card's, told the card, and which moves were tried on its board
        tried = [{"move": "g8h6", "cpl": 75}, {"move": "d8e7"}, {"move": "a1a8", "cpl": 1}, "nonsense"]
        code, r = s.call("POST", f"/api/cards/{cid}/claude/say", {"text": "why not this?", "tried": tried, "cols": 90})
        first = said(s.tutor.terminal(r["terminal"]))
        assert code == 200 and first["mode"] == "new" and first["cwd"] == str(s.cfg.workspace) and first["mcp"]
        told = first["prompt"]
        assert told.startswith("[card] The student is on the back of a card of theirs, of a move that was a blunder")
        assert "played 3... Nf6 in this position: " + FEN in told and "The card takes as right: g6, Qe7." in told
        assert "Qxf7 is mate." in told and "no longer on file" in told
        assert told.endswith("[tried on the card: Nh6 (loses 75), Qe7]\nwhy not this?")
        tools = json.loads((s.cfg.home / "sessions" / f"card-{cid}.mcp.json").read_text())["mcpServers"]["chess"]
        assert tools["env"]["CALLIE_GAME"] == "0"  # the game is not on file: the tools have the FEN to work from
        # lines for the card's board: asked for by its session, they are in the pane on the back of the card
        said_so = {"name": "show_lines", "session": tools["env"]["CALLIE_SESSION"]}
        shown = {"fen": FEN, "lines": [{"name": "The move played", "moves": ["Nf6", "Qxf7"]}]}
        code, told = s.call("POST", "/api/games/0/tool", {**said_so, "arguments": shown})
        assert code == 200 and "- The move played: 3... Nf6 4. Qxf7#" in told["text"]
        (board,) = s.call("GET", f"/api/terminals/{r['terminal']}/lines")[1]["shown"]
        assert board["fen"] == FEN and board["lines"][0]["moves"][1]["san"] == "Qxf7#"
        row = s.svc.store.one("SELECT session_id, session_started FROM cards")
        assert row == {"session_id": first["session"], "session_started": 1}
        # the back of the card shows that terminal, and a second message is typed into it
        assert s.call("POST", f"/api/cards/{cid}/claude/resume")[1] == r
        s.call("POST", f"/api/cards/{cid}/claude/say", {"text": "and Qe7?", "tried": []})
        assert "and Qe7?" in output(s.tutor.terminal(r["terminal"]), "heard:")  # pasted, then Enter
        # its terminal gone (the server was restarted, say) and its transcript deleted by Claude Code for its age:
        # the card has the conversation still, from the copy that was kept, and goes on with it
        s.tutor.forget_card(cid)
        transcript = s.cfg.claude_config_dir / "projects" / "-fake" / f"{first['session']}.jsonl"
        assert (s.cfg.home / "transcripts" / "-fake" / transcript.name).read_text() == transcript.read_text()
        transcript.unlink()
        code, r2 = s.call("POST", f"/api/cards/{cid}/claude/resume")
        again = said(s.tutor.terminal(r2["terminal"]))
        assert r2 != r and again["mode"] == "resume" and again["session"] == first["session"] and again["prompt"] == ""
        s.tutor.forget_card(cid)
        code, r3 = s.call("POST", f"/api/cards/{cid}/claude/say", {"text": "one more"})
        more = said(s.tutor.terminal(r3["terminal"]))
        assert more["mode"] == "resume" and more["prompt"] == "one more"  # it knows the card already
        # none of it goes into the history kept in git, and a card taken out of its deck is not asked about
        assert "session" not in "".join(history._rows(s.svc.store, "cards"))
        s.call("POST", f"/api/cards/{cid}/delete")
        assert s.call("POST", f"/api/cards/{cid}/claude/say", {"text": "hello"})[0] == 400
    finally:
        s.close()


# A heuristic as its file has it, with the position above and one of a rook ending, and the same as `load` gives it.
ENDING = "6r1/8/5k2/8/2P5/2K5/8/7R w - - 0 1"
ON_FILE = [
    {
        "key": "f7",
        "title": "Count what guards f7.",
        "positions": [
            {
                "fen": FEN,
                "last": "f1c4",
                "answer": "g6",
                "why": "Queen and bishop both aim at f7: g6 shuts the queen out.",
                "lines": [{"moves": "g7g6 h5f3", "cp": 20}, {"moves": "d8e7", "cp": 26}, {"moves": "g8h6", "cp": 95}],
            },
            {"fen": ENDING, "answer": "Re1", "why": "Re1 cuts the king off.", "lines": [{"moves": "h1e1", "mate": 30}]},
        ],
    }
]


def on_file(home, said=ON_FILE, name="heuristics.json"):
    path = home / name
    path.write_text(json.dumps(said))
    return path


def test_the_heuristics_on_file():
    hs = heuristics.load()  # the ones that come with the app: every move legal, every answer one its lines make right
    assert len(hs) >= 8 and all(len(h["title"]) > 20 and h["positions"] for h in hs)
    for h in hs:
        for p in h["positions"]:
            assert p["answer"].rstrip("+#") in p["why"], (h["key"], p["fen"])  # the back of a card is about its answer
            assert p["lines"][0]["san"] and len(p["why"]) < 900
    home = scratch_config().home
    (h,) = heuristics.load(on_file(home))
    one, two = h["positions"]
    assert one["lines"][0] == LINES[0] and one["last"] == "f1c4" and two["last"] is None
    assert one["shown"] == [] and two["shown"] == []  # neither says how it came about, or goes on
    wrong = lambda **change: [{**ON_FILE[0], "positions": [{**ON_FILE[0]["positions"][0], **change}]}]  # noqa: E731
    (after,) = heuristics.load(on_file(home, wrong(then="g6 Qf3")))[0]["positions"][0]["shown"]  # only how it goes on
    assert (after["fen"], after["lines"][0]["name"], after["lines"][0]["at"]) == (FEN, "From here", 0)
    assert [m["san"] for m in after["lines"][0]["moves"]] == ["g6", "Qf3"]
    assert two["lines"] == [{"moves": ["h1e1"], "san": ["Re1"], "cp": None, "mate": 30, "value": 9970}]
    for said in (
        wrong(answer="Nh6"),  # not what its lines answer it with
        wrong(why=" "),
        wrong(lines=[{"moves": "g7g5 h5h8", "cp": 20}]),  # not a legal line
        wrong(fen="nonsense"),
        wrong(moves="e4 e5 Qh5 Nc6"),  # moves that lead to another position
        wrong(moves="e4 e5 Qh6"),  # or are not legal
        wrong(then="Qe7 Nf3"),  # what follows starts with another move than the answer
        wrong(then="g6 Qxf7"),  # or is not legal
        [ON_FILE[0], {**ON_FILE[0], "title": "Again."}],  # the same key twice
        [ON_FILE[0], {**ON_FILE[0], "key": "other"}],  # the same positions twice
        [{"key": "none", "title": "No positions.", "positions": []}],
    ):
        try:
            heuristics.load(on_file(home, said))
            raise AssertionError(f"read {said}")
        except heuristics.HeuristicError as e:
            assert "heuristics.json, at " in str(e)


def test_a_heuristic_has_cards():
    c = scratch()
    hs = heuristics.load(on_file(scratch_config().home))
    assert c.teach("heuristic", hs, NOON) == [] and c.teach("heuristic", hs, NOON + 5) == []  # twice: the same two
    assert [(d["key"], d["cards"], d["active"]) for d in c.decks(NOON)][3] == ("heuristic", 2, 2)
    assert c.draw("heuristic", now=NOON)["name"] == "Heuristics"
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON, game="aaaa")  # an error in the same position is a card of its own
    c.explain(c.store.one("SELECT * FROM cards WHERE uci='g8f6'"), "Qxf7 is mate.")
    assert [(d["cards"], d["active"]) for d in c.decks(NOON)] == [(1, 1), (0, 0), (0, 0), (2, 2), (0, 0)]
    assert c.explained() == {
        (c.store.one("SELECT position FROM cards WHERE uci='g8f6'")["position"], "g8f6"): "Qxf7 is mate."
    }
    assert c.unexplained() is None  # the back of a heuristic's card is written already
    opening, ending = (
        c.store.one("SELECT id FROM cards WHERE fen=? AND heuristic='f7'", (f,))["id"] for f in (FEN, ENDING)
    )
    drawn = c.draw("heuristic", skip=ending, now=NOON)["card"]
    assert drawn["id"] == opening and drawn["last"] == "f1c4" and drawn["turn"] == "b"
    # answered as any card is: wrong, it is back in a minute; right, it is rated
    bad = c.answer(opening, "Nf6", now=NOON)
    assert bad["right"] is False and bad["played"] is None and bad["heuristic"] == "Count what guards f7."
    assert bad["explanation"].startswith("Queen and bishop") and bad["best"]["san"] == "g6" and bad["active"] == 1
    assert c.lost(opening, "Nh6", lambda fen, uci: 1 / 0) == 75 and c.lost(opening, "Qe7", lambda fen, uci: 1 / 0) == 6
    good = c.answer(opening, "Qe7", now=NOON + 61)  # as good as the first line
    assert good["right"] and good["next"]["good"] == "10m"
    c.answer(ending, "Re1", "easy", now=NOON)
    assert c.store.one("SELECT state, interval FROM cards WHERE id=?", (ending,)) == {"state": "review", "interval": 4}
    assert c.draw("heuristic", now=NOON + 30) == {
        "deck": "heuristic",
        "name": "Heuristics",
        "active": 0,
        "card": None,
        "wait": 30.0,
    }
    # the file says something else: a card that is there stands where it stood, and says what the file says now;
    # one whose position is gone from it goes; a new position is a new card
    other = FEN.replace("3 3", "9 12")  # the same position, later in another game
    again = [
        {
            "key": "f7-again",
            "title": "Count f7 twice.",
            "positions": [{**ON_FILE[0]["positions"][0], "fen": other, "why": "g6, or Qe7."}],
        },
        {
            "key": "new",
            "title": "A new one.",
            "positions": [{**ON_FILE[0]["positions"][1], "fen": ENDING.replace("7R", "6R1")}],
        },
    ]
    again[1]["positions"][0].update(answer="Rf1+", lines=[{"moves": "g1f1", "cp": 300}])
    assert c.teach("heuristic", heuristics.load(on_file(scratch_config().home, again)), NOON + 90) == [ending]
    kept = c.store.one("SELECT * FROM cards WHERE id=?", (opening,))
    assert (kept["state"], kept["fen"], kept["heuristic"], kept["explanation"]) == (
        "learning",
        other,
        "f7-again",
        "g6, or Qe7.",
    )
    assert c.answer(opening, "g6", now=NOON + 90)["heuristic"] == "Count f7 twice."
    assert [(d["cards"], d["active"]) for d in c.decks(NOON + 90)][3] == (2, 2)
    # taken out of the deck, it stays out whatever the file says; and a game that is deleted takes none with it
    new = c.draw("heuristic", skip=opening, now=NOON + 90)["card"]["id"]
    c.remove(new)
    assert c.teach("heuristic", heuristics.load(on_file(scratch_config().home, again)), NOON + 95) == []
    assert [d["cards"] for d in c.decks(NOON + 95)] == [1, 0, 0, 1, 0]
    assert [r["uci"] for r in c.drop("aaaa")] == ["g8f6"]
    assert [d["cards"] for d in c.decks(NOON + 95)] == [0, 0, 0, 1, 0]
    assert c.teach("heuristic", [], NOON + 99) == sorted([opening, new]) and c.store.all("SELECT id FROM cards") == []


def test_the_heuristics_over_http_and_claude_on_the_back_of_one():
    s = Running(heuristics_path=on_file(scratch_config().home))
    try:
        assert [(d["key"], d["active"]) for d in s.call("GET", "/api/cards")[1]][3] == ("heuristic", 2)
        cid = s.svc.store.one("SELECT id FROM cards WHERE fen=?", (FEN,))["id"]
        drawn = s.call("GET", f"/api/cards/heuristic?skip={cid + 1 if cid == 1 else 1}")[1]
        assert drawn["name"] == "Heuristics" and drawn["card"]["id"] == cid and drawn["card"]["last"] == "f1c4"
        back = s.call("POST", f"/api/cards/{cid}/answer", {"move": "g6"})[1]
        assert back["right"] and back["played"] is None and back["heuristic"] == "Count what guards f7."
        assert b'id="card-heuristic"' in s.call("GET", "/")[1]
        # asked about from its back: its session is told the heuristic and the position, not of an error
        r = s.call(
            "POST", f"/api/cards/{cid}/claude/say", {"text": "why not Nh6?", "tried": [{"move": "g8h6", "cpl": 75}]}
        )[1]
        told = json.loads(output(s.tutor.terminal(r["terminal"]), '"prompt"').split("\n")[0])["prompt"]
        assert told.startswith("[card] The student is on the back of a card of the Heuristics deck")
        assert "The heuristic: Count what guards f7." in told and "to find the move: " + FEN in told
        assert "The engine plays g6 (" in told and "The card takes as right: g6, Qe7." in told
        assert (
            "how it finds the move here: Queen and bishop both aim at f7" in told
            and "It is from no game on file" in told
        )
        assert told.endswith("[tried on the card: Nh6 (loses 75)]\nwhy not Nh6?")
        assert not s.svc.explainer.failed and s.svc.cards.unexplained() is None  # nothing is written for it
    finally:
        s.close()


# An opening as its file has it: the same position, as one of the Openers deck, with the moves that lead to it
# and the ones that follow from it.
OPENER = [
    {
        "key": "wayward-queen",
        "title": "Wayward Queen Attack",
        "positions": [
            {
                **{k: v for k, v in ON_FILE[0]["positions"][0].items() if k != "last"},
                "moves": "e4 e5 Qh5 Nc6 Bc4",
                "then": "g6 Qf3 Nf6",
                "why": "Mate on f7 is threatened: g6 shuts the queen out.",
            }
        ],
    }
]


def test_the_openers_on_file():
    seen: set[str] = set()
    theirs = sum(len(h["positions"]) for h in heuristics.load(seen=seen))
    openers = heuristics.load(heuristics.OPENERS, seen)  # with the app, and none of them a position of a heuristic
    assert 10 <= len(seen) - theirs == sum(len(o["positions"]) for o in openers) <= 20
    for o in openers:
        assert "1." not in o["title"], o["key"]  # the opening's name: the moves that lead there are a line
        for p in o["positions"]:
            assert p["answer"].rstrip("+#") in p["why"] and len(p["why"]) < 900, (o["key"], p["fen"])
            assert int(p["fen"].split()[5]) <= 12, (o["key"], p["fen"])  # early in a game
            (board,) = p["shown"]  # one line: from the start of a game to the position, and on from its answer
            (line,) = board["lines"]
            led, then = line["moves"][: line["at"]], line["moves"][line["at"] :]
            assert board["fen"] == cards.Position.from_fen().fen() and led[-1]["uci"] == p["last"]
            assert led[-1]["fen"].split()[:4] == p["fen"].split()[:4], (o["key"], p["fen"])
            assert then[0]["san"] == p["answer"] and len(then) >= 4, (o["key"], p["fen"])
    # a position that is in both files is an error
    home, seen = scratch_config().home, set()
    heuristics.load(on_file(home), seen)
    try:
        heuristics.load(on_file(home, OPENER, "openers.json"), seen)
        raise AssertionError("read a position of both files")
    except heuristics.HeuristicError as e:
        assert "openers.json, at 'wayward-queen'" in str(e) and "is there twice" in str(e)


def test_an_opener_has_a_card():
    c = scratch()
    home = scratch_config().home
    assert c.teach("opener", heuristics.load(on_file(home, OPENER, "openers.json")), NOON) == []
    assert [(d["key"], d["name"], d["cards"], d["active"]) for d in c.decks(NOON)][3:] == [
        ("heuristic", "Heuristics", 0, 0),
        ("opener", "Openers", 1, 1),
    ]
    drawn = c.draw("opener", now=NOON)
    assert drawn["name"] == "Openers" and drawn["card"]["last"] == "f1c4" and c.unexplained() is None
    cid = drawn["card"]["id"]
    back = c.answer(cid, "g6", now=NOON)
    assert back["right"] and back["played"] is None and back["heuristic"] is None
    assert back["opener"] == "Wayward Queen Attack" and back["explanation"].startswith("Mate on f7")
    # the moves that led to it and the ones that follow are a line on its back, from the start of a game, which
    # says where in it the card's position is
    (board,) = back["shown"]
    (line,) = board["lines"]
    assert (board["title"], board["fen"].split()[0]) == ("The line", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")
    assert [m["san"] for m in line["moves"]] == ["e4", "e5", "Qh5", "Nc6", "Bc4", "g6", "Qf3", "Nf6"]
    assert line["at"] == 5 and line["moves"][4] == {"uci": "f1c4", "san": "Bc4", "fen": FEN}
    # its position said by the other file instead: the same card, where it stood, in the other deck
    c.answer(cid, "g6", "easy", now=NOON)
    hs = heuristics.load(on_file(home))
    assert c.teach("heuristic", hs, NOON + 5) == [] and c.teach("opener", [], NOON + 5) == []
    moved = c.store.one("SELECT * FROM cards WHERE id=?", (cid,))
    assert (moved["heuristic"], moved["opener"], moved["state"], moved["interval"]) == ("f7", None, "review", 4)
    assert c.answer(cid, "g6", now=NOON + 5)["shown"] == []  # and no longer has the line
    assert [d["cards"] for d in c.decks(NOON + 5)] == [0, 0, 0, 2, 0]
    try:
        c.teach("blunder", [], NOON)
        raise AssertionError("taught a deck of errors")
    except CardError:
        pass


def test_the_openers_over_http_and_claude_on_the_back_of_one():
    home = scratch_config().home
    s = Running(openers_path=on_file(home, OPENER, "openers.json"))
    try:
        assert [(d["key"], d["active"]) for d in s.call("GET", "/api/cards")[1]][3:] == [
            ("heuristic", 0),
            ("opener", 1),
        ]
        drawn = s.call("GET", "/api/cards/opener")[1]
        cid = drawn["card"]["id"]
        assert drawn["name"] == "Openers" and drawn["card"]["fen"] == FEN
        back = s.call("POST", f"/api/cards/{cid}/answer", {"move": "Nf6"})[1]
        assert not back["right"] and back["opener"].startswith("Wayward Queen Attack") and back["heuristic"] is None
        r = s.call("POST", f"/api/cards/{cid}/claude/say", {"text": "why not Qe7?"})[1]
        told = json.loads(output(s.tutor.terminal(r["terminal"]), '"prompt"').split("\n")[0])["prompt"]
        assert told.startswith("[card] The student is on the back of a card of the Openers deck")
        assert (
            "The opening: Wayward Queen Attack\n" in told and back["shown"][0]["lines"][0]["name"] == "From the start"
        )
        assert "The moves that led to the position: 1. e4 e5 2. Qh5 Nc6 3. Bc4\n" in told
        assert "How it goes on from there: 3... g6 4. Qf3 Nf6\n" in told
        assert 'on the back of the card as one line to step through ("From the start").' in told
        assert "under the opening, is why the move is the one: Mate on f7" in told and "why not Qe7?" in told
        assert not s.svc.explainer.failed and s.svc.cards.unexplained() is None  # nothing is written for it
    finally:
        s.close()


load_tests = function_tests(globals())
