"""Your ratings: the arithmetic, which games count, and what the service says of them."""

import time

from callie import opponent, rating
from callie.store import Store
from tests import function_tests, need_engine, scratch_config
from tests.test_play import done, service, until


def game(n, result, elo=1500, color="w", tc=None, status="finished", at=None):
    return {"id": f"g{n}", "created_at": n, "updated_at": n, "finished_at": n if at is None else at, "user_color": color,
            "elo": elo, "status": status, "result": result, "time_control": tc}  # fmt: skip


def test_one_game_at_a_time():
    assert rating.expected(1500, 1500) == 0.5 and 0.75 < rating.expected(1700, 1500) < 0.77
    won, sure = rating.after(1500, 350, 1500, 1)
    lost, _ = rating.after(1500, 350, 1500, 0)
    assert 1674 < won < 1676 and abs((won - 1500) + (lost - 1500)) < 1e-9 and 246 < sure < 248  # Glicko's own numbers
    assert rating.after(1500, 350, 1500, 0.5)[0] == 1500
    assert (
        rating.after(1500, 350, 1100, 1)[0] - 1500 < 50 < 1500 - rating.after(1500, 350, 1100, 0)[0]
    )  # an upset says more
    r, d = 1500.0, 350.0
    for _ in range(60):
        r, d = rating.after(r, d, 1500, 1)
        r, d = rating.after(r, d, 1500, 0)
    assert d == rating.SETTLED and 12 < rating.after(r, d, round(r), 1)[0] - r < 15  # it never stops listening


def test_formats():
    assert rating.format_of(None) == rating.format_of("untimed") == "untimed"
    assert [rating.format_of(tc) for tc in ("1+0", "3+2", "5+3", "10+3", "15+10", "30+20")] == [
        "bullet", "blitz", "blitz", "rapid", "rapid", "classical"]  # fmt: skip


def test_which_games_count_and_in_what_order():
    games = [
        game(1, "0-1"),  # lost
        game(2, "1-0", color="b"),  # lost, as Black
        game(3, "1/2-1/2", elo=1300),
        game(4, "1-0", elo=None),  # Stockfish at full strength has no rating
        game(5, None, status="active"),  # not over
        game(6, "1-0", tc="10+3"),  # another format: a rating of its own
        game(7, "1-0", elo=1100, at=2.5),  # ended between the second and the third
    ]
    standing, changes = rating.history(games)
    assert (
        list(standing) == ["untimed", "rapid"] and standing["untimed"]["games"] == 4 and standing["rapid"]["games"] == 1
    )
    assert set(changes) == {"g1", "g2", "g3", "g6", "g7"}
    assert (
        changes["g1"]["before"] == 1500
        and changes["g1"]["change"] < 0
        and changes["g2"]["before"] == changes["g1"]["after"]
    )
    assert changes["g7"]["before"] == changes["g2"]["after"] and changes["g3"]["before"] == changes["g7"]["after"]
    assert changes["g7"]["change"] > 0 and changes["g3"]["after"] == standing["untimed"]["elo"]
    assert changes["g6"] == {
        "format": "rapid",
        "before": 1500,
        "after": standing["rapid"]["elo"],
        "change": standing["rapid"]["elo"] - 1500,
    }
    assert standing["rapid"]["provisional"] and standing["rapid"]["deviation"] > 240
    total = sum(c["change"] for c in changes.values() if c["format"] == "untimed")
    assert total == standing["untimed"]["elo"] - 1500  # the changes shown add up
    assert rating.history(list(reversed(games))) == (standing, changes)  # whatever order they are handed in
    assert rating.history([]) == ({}, {}) and rating.history(games[3:5]) == ({}, {})


def test_an_older_database_is_brought_along():
    cfg = scratch_config()
    store = Store(cfg.db_path)
    store.conn.execute("PRAGMA user_version = 0")  # as before Stockfish's ratings were on Allie's scale
    store.conn.execute("ALTER TABLE games DROP COLUMN finished_at")
    for gid, engine, elo, status in (("a1", "stockfish", 1200, "finished"), ("a2", "allie", 1200, "finished"),
                                     ("a3", "stockfish", None, "active")):  # fmt: skip
        store.add_game({"id": gid, "created_at": 1.0, "updated_at": 50.0, "user_color": "w", "engine": engine, "elo": elo,
                        "start_fen": "x", "status": status, "session_id": gid})  # fmt: skip
    store.add_move("a1", 1, "e2e4", "e4", "x", "user")
    store.run("UPDATE moves SET played_at = 20")
    store.run("UPDATE games SET updated_at = 50")
    store.conn.close()
    store = Store(cfg.db_path)
    store.rescale(lambda own: own + 300.4)
    store.rescale(lambda own: own + 300.4)  # once only
    rows = {r["id"]: r for r in store.all("SELECT * FROM games")}
    assert rows["a1"]["elo"] == 1500 and rows["a2"]["elo"] == 1200 and rows["a3"]["elo"] is None
    assert rows["a1"]["finished_at"] == 20 and rows["a2"]["finished_at"] == 50 and rows["a3"]["finished_at"] is None
    store.update_game("a3", status="finished", result="0-1", reason="resignation")
    assert time.time() - store.game("a3")["finished_at"] < 5
    store.update_game("a3", status="active", result=None, reason=None)
    assert store.game("a3")["finished_at"] is None
    fresh = Store(scratch_config().db_path)  # a new database has nothing to bring along, and says so
    assert fresh.conn.execute("PRAGMA user_version").fetchone()[0] == 0
    fresh.rescale(lambda own: own)
    assert fresh.conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_the_service_keeps_score():
    mate = ["e7e5", "d8h4"]  # the fool's mate, twice over
    svc = service(script=mate + mate + ["e7e5", "e7e5"])
    try:
        assert svc.rating() == {} and svc.meta()["formats"] == {"untimed": "untimed", "10+3": "rapid"}
        lost = svc.create("w", 1320, None, None)["id"]
        for mine in ("f3", "g4"):
            n = svc.state(lost)["plies"]
            svc.move(lost, mine)
            until(svc, lost, lambda s: s["plies"] == n + 2)
        assert svc.state(lost)["result"] == "0-1"
        first = svc.rating()
        assert list(first) == ["untimed"] and first["untimed"]["games"] == 1 and first["untimed"]["elo"] < 1500
        row = svc.games()[0]
        assert (
            row["format"] == "untimed"
            and row["rating"]["before"] == 1500
            and row["rating"]["change"] == first["untimed"]["elo"] - 1500
        )
        assert svc.state(lost)["rating"] == row["rating"]  # the game's own page is told too, as soon as it is over
        # a game on the clock is rated apart; one against full strength and one that is not over are not rated
        timed = svc.create("w", 1320, None, "10+3")["id"]
        for mine in ("f3", "g4"):
            n = svc.state(timed)["plies"]
            svc.move(timed, mine)
            until(svc, timed, lambda s: s["plies"] == n + 2)
        full = svc.create("w", None, None, None)["id"]
        svc.move(full, "e4")
        until(svc, full, lambda s: s["plies"] == 2)
        svc.resign(full)
        live = svc.create("w", 1320, None, None)["id"]
        svc.move(live, "e4")
        until(svc, live, lambda s: s["plies"] == 2)
        now = svc.rating()
        assert list(now) == ["untimed", "rapid"] and now["untimed"] == first["untimed"] and now["rapid"]["games"] == 1
        by_id = {g["id"]: g["rating"] for g in svc.games()}
        assert (
            by_id[full] is None
            and by_id[live] is None
            and by_id[timed]["format"] == "rapid"
            and by_id[timed]["before"] == 1500
        )
        assert svc.state(full)["rating"] is None and svc.state(live)["rating"] is None
        # resigning the live one counts it; taking the move back reopens it, and it no longer does
        svc.resign(live)
        assert svc.rating()["untimed"]["games"] == 2 and svc.rating()["untimed"]["elo"] < first["untimed"]["elo"]
        svc.takeback(live)
        assert svc.rating()["untimed"] == first["untimed"]
        # a deleted game is as if it had never been played
        svc.delete(lost)
        assert list(svc.rating()) == ["rapid"]
        svc.delete(timed)
        assert svc.rating() == {} and all(g["rating"] is None for g in svc.games())
    finally:
        done(svc)


def test_stockfish_is_set_by_a_rating_on_allies_scale():
    cfg = scratch_config()
    need_engine(cfg)
    asked = []

    class Spy:
        def search(self, fen, moves, **how):
            asked.append(how)
            return type("R", (), {"best": "e2e4", "lines": []})()

    old = opponent.SCALE
    try:
        opponent.SCALE = [(500.0, 800.0), (1320.0, 1700.0), (2500.0, 2700.0)]
        opponent.pick(Spy(), "fen", [], 1700, 50)
        opponent.pick(Spy(), "fen", [], 2200, 50)
        opponent.pick(Spy(), "fen", [], 5000, 50)
        opponent.pick(Spy(), "fen", [], None, 50)
        opponent.pick(Spy(), "fen", [], 1250, 50)  # below the join: the sampler's shallow search of every move
        assert [a.get("elo") for a in asked] == [1320, 1910, opponent.SF_MAX, None, None]
        assert asked[4] == {"depth": opponent.WEAK_DEPTH, "multipv": 256} and "depth" not in asked[3]
    finally:
        opponent.SCALE = old


load_tests = function_tests(globals())
