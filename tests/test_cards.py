"""The cards made of your errors: which deck, what answers one, how long it is hidden, and that games make them."""

import time
from datetime import datetime

from callie import cards, opponent
from callie.cards import CardError, Cards
from callie.store import Store
from tests import function_tests, need_engine, scratch_config
from tests.test_play import done, service, until
from tests.test_server import Running

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
    assert [d["cards"] for d in c.decks(NOON)] == [0, 0, 0]
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
    assert [(d["cards"], d["active"]) for d in c.decks(NOON + 4.5 * DAY)] == [(0, 0), (1, 1), (1, 1)]
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
        # the same game on file once more, played the day before: the cards stay the later game's
        svc.store.add_game({**svc.store.game(g["id"]), "id": "older"})
        svc.store.run(
            "INSERT INTO moves (game_id, ply, uci, san, fen, source, played_at, clock_ms) "
            "SELECT 'older', ply, uci, san, fen, source, played_at - 86400, clock_ms FROM moves WHERE game_id=?",
            (g["id"],),
        )
        svc.store.run(
            "INSERT INTO evals (game_id, ply, fen, depth, value, cp, mate, lines) "
            "SELECT 'older', ply, fen, depth, value, cp, mate, lines FROM evals WHERE game_id=?",
            (g["id"],),
        )
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
        svc.delete("older")
        assert svc.store.all("SELECT * FROM cards") == []
    finally:
        done(svc)


def test_the_cards_over_http():
    s = Running()
    try:
        s.svc.cards.note(FEN, "g8f6", "Nf6", 990, LINES, NOON - 30 * DAY)
        assert [d["active"] for d in s.call("GET", "/api/cards")[1]] == [1, 0, 0]
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
            and set(done) == {"right", "active", "move", "best", "played", "explanation", "next"}
        )
        assert s.svc.store.one("SELECT state, interval FROM cards")["state"] == "review"
        assert s.call("GET", "/api/cards/blunder")[1] | {"deck": 0, "name": 0} == {
            "deck": 0,
            "name": 0,
            "active": 0,
            "card": None,
            "wait": None,
        }
        assert b'id="card-board"' in s.call("GET", "/")[1]
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


load_tests = function_tests(globals())
