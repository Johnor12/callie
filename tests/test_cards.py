"""The cards made of your errors: which deck, what answers one, how long it is hidden, and that games make them."""

import time
from datetime import datetime

from chesstutor import cards, opponent
from chesstutor.cards import CardError, Cards
from chesstutor.store import Store
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
        done = s.call("POST", f"/api/cards/{cid}/answer", {"move": "g6", "grade": "easy"})[1]
        assert done["right"] and done["active"] == 0 and set(done) == {"right", "active", "move", "best", "played", "explanation"}
        assert s.call("GET", "/api/cards/blunder")[1]["card"] is None
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
        tool = lambda name, **b: s.call("POST", f"/api/games/{gid}/tool", {"name": name, "arguments": b.pop("arguments", {}), **b})[1]["text"]  # noqa: E731
        by = "generated by Claude with access to Stockfish"
        assert f"Previous analysis of the student's moves below, {by}:\n3... Nf6: {why}" in tool("game")
        assert f"Previous analysis of the move, {by}: {why}" in tool("review", arguments={"from_ply": 6})
        assert "Previous analysis" not in tool("review", arguments={"to_ply": 5})
        # asked from the background, the engine is the one that gives way: a search of a set size, however long was asked for
        text = tool("analyze", arguments={"ply": 5, "seconds": 60, "lines": 2}, quiet=True)
        assert "Black to move" in text and text.count("3... ") >= 2 and s.svc.quiet.nice and s.svc.quiet.proc and not s.svc.explore.proc
        assert s.svc.in_play() is False  # the game is over
    finally:
        opponent.pick = old
        s.close()


load_tests = function_tests(globals())
