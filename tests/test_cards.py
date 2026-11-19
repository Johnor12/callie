"""The cards made of your errors: which deck, what answers one, how long it is hidden, and that games make them."""

from datetime import datetime

from chesstutor import cards
from chesstutor.cards import CardError, Cards
from chesstutor.store import Store
from tests import function_tests, scratch_config
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
    return Cards(Store(scratch_config().home / "cards.db"))


def test_decks_and_how_long_a_card_is_hidden():
    assert [cards.deck_of(n) for n in (49, 50, 99, 100, 199, 200, 1000)] == [None, "inaccuracy", "inaccuracy", "mistake", "mistake", "blunder", "blunder"]
    easy, days = [], 0
    for _ in range(7):
        days = cards.hidden_for(days, "easy")
        easy.append(days)
    assert easy == [4, 10, 25, 62, 155, 180, 180]  # longer each time, up to a ceiling
    assert [cards.hidden_for(n, "hard") for n in (0, 1, 4, 25, 170, 180)] == [1, 2, 5, 30, 180, 180]
    try:
        cards.hidden_for(3, "good")
        raise AssertionError("an unknown grade was accepted")
    except CardError:
        pass
    # due at the start of a day, which is at four in the morning
    assert cards.due_in(1, at(2026, 11, 19, 21)) == at(2026, 11, 20, 4)
    assert cards.due_in(1, at(2026, 11, 20, 1)) == at(2026, 11, 20, 4)  # one in the morning is still the day before
    assert cards.due_in(4, at(2026, 11, 19, 12)) == at(2026, 11, 23, 4)
    # the engine's first choice and a line as good as it; never the move the card is about
    assert cards.right_moves(LINES, False, "g8f6") == ["g7g6", "d8e7"]
    assert cards.right_moves(LINES, False, "d8e7") == ["g7g6"]
    mates = [{"moves": ["a1a8"], "value": 9999}, {"moves": ["a1a7"], "value": 9997}, {"moves": ["a1a6"], "value": 400}]
    assert cards.right_moves(mates, True, "h2h3") == ["a1a8", "a1a7"]  # a slower mate is as good


def test_a_card_is_made_once_and_reviewed():
    c = scratch()
    c.note(FEN, "g8f6", "Nf6", 30, LINES, NOON)  # lost too little
    c.note(FEN, "g7g6", "g6", 250, LINES, NOON)  # the engine's own choice
    assert [d["cards"] for d in c.decks(NOON)] == [0, 0, 0]
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON)
    c.note(FEN, "g8h6", "Nh6", 75, LINES, NOON)
    c.note(FEN.replace(" 3 3", " 7 9"), "g8f6", "Nf6", 990, LINES, NOON)  # the same move, told of again
    assert [(d["key"], d["cards"], d["active"]) for d in c.decks(NOON)] == [("blunder", 1, 1), ("mistake", 0, 0), ("inaccuracy", 1, 1)]
    assert c.draw("mistake", now=NOON) == {"deck": "mistake", "name": "Mistakes", "active": 0, "card": None}
    drawn = c.draw("blunder", now=NOON)
    card = drawn["card"]
    assert drawn["active"] == 1 and card["fen"] == FEN and card["turn"] == "b" and card["legal"]["g7g6"]["san"] == "g6"
    assert c.draw("blunder", skip=card["id"], now=NOON)["card"]["id"] == card["id"]  # the only one: it is shown again
    try:
        c.answer(card["id"], "e2e4")
        raise AssertionError("an illegal move was accepted")
    except CardError:
        pass
    # wrong: the answer is given and the card stays active
    r = c.answer(card["id"], "Nh6", now=NOON)
    assert not r["right"] and r["move"]["san"] == "Nh6" and r["best"]["san"] == "g6" and r["best"]["line"] == ["g6", "Qf3"]
    assert r["played"] == {"uci": "g8f6", "san": "Nf6", "cpl": 990, "deck": "blunder"} and r["active"] == 1
    assert not c.answer(card["id"], "Nf6", "easy", now=NOON)["right"]  # the move of the game is wrong, however fast
    # right, but not yet said how hard: still active
    r = c.answer(card["id"], "Qe7", now=NOON)
    assert r["right"] and r["active"] == 1 and r["move"]["fen"].startswith("r1b1kbnr/ppppqppp")
    # right and easy: hidden for four days, then for ten
    assert c.answer(card["id"], "g6", "easy", now=NOON)["active"] == 0
    assert c.draw("blunder", now=NOON + 3 * DAY)["card"] is None and c.draw("blunder", now=NOON + 4 * DAY)["card"]
    c.answer(card["id"], "g6", "easy", now=NOON + 2 * DAY)  # not active: graded already, nothing changes
    assert c.draw("blunder", now=NOON + 4 * DAY)["card"]
    c.answer(card["id"], "g6", "easy", now=NOON + 4 * DAY)
    assert c.draw("blunder", now=NOON + 13 * DAY)["card"] is None and c.draw("blunder", now=NOON + 14 * DAY)["card"]
    # answered wrong when it comes back: not learned, and after that a hard right answer hides it for a day
    c.answer(card["id"], "Nh6", now=NOON + 14 * DAY)
    assert c.draw("blunder", now=NOON + 14 * DAY)["active"] == 1
    c.answer(card["id"], "g6", "hard", now=NOON + 14 * DAY)
    assert c.draw("blunder", now=NOON + 14.5 * DAY)["card"] is None and c.draw("blunder", now=NOON + 15 * DAY)["card"]
    # the same move played again in a later game: no second card, this one is not learned again
    c.answer(card["id"], "g6", "easy", now=NOON + 15 * DAY)
    c.note(FEN, "g8f6", "Nf6", 990, LINES, NOON)  # an older game, heard of late: nothing
    assert c.draw("blunder", now=NOON + 16 * DAY)["card"] is None
    c.note(FEN, "g8f6", "Nf6", 150, LINES, NOON + 16 * DAY)  # it cost less this time: the card moves deck
    assert [(d["cards"], d["active"]) for d in c.decks(NOON + 16 * DAY)] == [(0, 0), (1, 1), (1, 1)]
    # two active cards: the one just seen is not drawn again
    c.note(FEN, "a7a6", "a6", 120, LINES, NOON + 16 * DAY)
    for _ in range(20):
        assert c.draw("mistake", skip=card["id"], now=NOON + 16 * DAY)["card"]["id"] != card["id"]


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
        before = svc.store.all("SELECT * FROM cards")
        svc._cards_on_file()  # what a restart does: the same games make no more cards and unlearn none
        assert svc.store.all("SELECT * FROM cards") == before
        svc.delete(g["id"])
        assert svc.store.all("SELECT * FROM cards") == before  # a card outlives its game
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
        assert s.call("POST", f"/api/cards/{cid}/answer", {"move": "zz"})[0] == 400
        assert s.call("POST", "/api/cards/999/answer", {"move": "g6"})[0] == 400
        assert s.call("POST", f"/api/cards/{cid}/answer", {"move": "h6"})[1]["right"] is False
        assert s.call("POST", f"/api/cards/{cid}/answer", {"move": "g6", "grade": "soso"})[0] == 400
        assert s.call("POST", f"/api/cards/{cid}/answer", {"move": "g6", "grade": "easy"})[1] | {"move": 0, "best": 0, "played": 0} == {
            "right": True, "active": 0, "move": 0, "best": 0, "played": 0}  # fmt: skip
        assert s.call("GET", "/api/cards/blunder")[1]["card"] is None
        assert b'id="card-board"' in s.call("GET", "/")[1]
    finally:
        s.close()


load_tests = function_tests(globals())
