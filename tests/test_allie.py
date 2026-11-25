"""Allie as the opponent and as a tool, with `fake_allie.py` in place of the model: its clock, what stops it,
what it is told of a game, and what Claude is given."""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from callie import allie, mcp
from callie.play import GameError, Service
from callie.store import Store
from tests import function_tests, need_engine, scratch_config


def service(idle_s=None, **fake):
    """A service whose Allie is the fake one, set to think (and load, and compute) for as long as ``fake`` says."""
    cfg = scratch_config(**({} if idle_s is None else {"allie_idle_s": idle_s}))
    need_engine(cfg)
    model = cfg.home / "fake-allie"
    model.mkdir()
    for name in ("config.json", "model.safetensors"):
        (model / name).write_text("{}")
    (model / "fake.json").write_text(json.dumps(fake))
    cfg.allie_python_path, cfg.allie_model = Path(sys.executable), model
    svc = Service(cfg)
    svc._old_worker, allie._WORKER = allie._WORKER, Path(__file__).with_name("fake_allie.py")
    svc.start()
    return svc


def done(svc):
    allie._WORKER = svc._old_worker
    svc.close()


def asked(svc, op):
    lines = (svc.cfg.allie_model / "requests.jsonl").read_text().splitlines()
    return [r for r in map(json.loads, lines) if r["op"] == op]


def watch(svc, game_id, test, timeout=20):
    """Look at the game as an open page does, until ``test`` passes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = svc.state(game_id)
        if test(state):
            return state
        time.sleep(0.05)
    raise AssertionError(
        f"timed out; last state: plies={state['plies']} status={state['status']} clock={state['clock']}"
    )


def test_allie_is_an_engine_only_where_it_is_installed():
    cfg = scratch_config()
    need_engine(cfg)
    svc = Service(cfg)
    try:
        engines = {e["key"]: e for e in svc.meta()["engines"]}
        assert (
            engines["stockfish"]["available"]
            and not engines["allie"]["available"]
            and "allie install" in engines["allie"]["missing"]
        )
        for bad in (dict(engine="allie", elo=1500), dict(engine="lc0")):
            try:
                svc.create(**bad)
                raise AssertionError(f"accepted {bad}")
            except GameError:
                pass
        try:
            g = svc.create("w", 1320)
            svc.tool_humans(g["id"])
            raise AssertionError("asked an Allie that is not there")
        except GameError as e:
            assert "allie install" in str(e)
    finally:
        svc.close()
    assert (
        allie.speed(600, 3) == "rapid"
        and allie.speed(180, 0) == "blitz"
        and allie.speed(60, 0) == "bullet"
        and allie.speed(1800, 20) == "classical"
    )


def test_allie_plays_to_the_same_clock():
    svc = service(think=2.5)
    try:
        engines = {e["key"]: e for e in svc.meta()["engines"]}
        assert (
            engines["allie"]["available"]
            and engines["allie"]["elo"]["min"] == allie.ELO_MIN
            and not engines["allie"]["full"]
        )
        for bad in (
            dict(elo=None),
            dict(elo=allie.ELO_MIN - 10),
            dict(elo=1500, time_control="2.5+0"),
            dict(elo=1500, time_control="5+0.5"),
        ):
            try:
                svc.create(engine="allie", **bad)
                raise AssertionError(f"accepted {bad}")
            except GameError:
                pass
        g = svc.create("w", 1500, None, "10+3", "allie")
        gid = g["id"]
        assert g["engine"] == "allie" and g["opponent"] == "Allie 1500"
        assert g["clock"]["opponent"] == {"ms": 600000, "running": False} and '"Allie 2.1 (1500 Elo)"' in g["pgn"]
        # each side's first move is not timed: Allie's comes with no page on the game, its think time waited out
        svc.move(gid, "e4")
        t = time.time()
        while len(svc.store.moves(gid)) < 2 and time.time() - t < 10:
            time.sleep(0.05)
        assert 2.3 < time.time() - t < 6 and svc.store.moves(gid)[1]["uci"] == "a7a5"
        s = svc.state(gid)
        assert s["clock"]["ms"] == 603000 and s["clock"]["opponent"]["ms"] == 603000  # the increment, nothing charged
        first = asked(svc, "decide")[0]
        # a game at 1500 is one at 1500 everywhere but in what Allie is told: to it that is a new account's rating
        assert (first["white_elo"], first["black_elo"], first["base"], first["inc"], first["speed"]) == (
            1501,
            1501,
            600,
            3,
            "rapid",
        )
        assert first["moves"] == ["e2e4"] and first["clocks"] == [603.0] and first["clock"] == 600.0
        # from its second move its clock runs, but only while a page is on the game: left alone, it does not move
        svc.move(gid, "Nf3")
        time.sleep(3.5)
        assert len(svc.store.moves(gid)) == 3
        waited = svc.store.game(gid)["opp_clock_ms"]
        assert 603000 - 1.5 * 1000 - 300 <= waited < 603000, waited  # charged for as long as a page had just been there
        # watched again, it thinks out the rest and moves: charged its think time, given its increment
        s = watch(svc, gid, lambda s: s["clock"]["opponent"]["running"])
        assert s["thinking"] and not s["clock"]["running"]
        s = watch(svc, gid, lambda s: s["plies"] == 4)
        assert 603000 - 2500 + 3000 - 400 <= s["clock"]["opponent"]["ms"] <= 603000 - 2500 + 3000, s["clock"]
        assert not s["clock"]["opponent"]["running"] and s["clock"]["running"]
        second = asked(svc, "decide")[1]
        assert second["clocks"][:2] == [603.0, 603.0] and len(second["clocks"]) == 3 and second["clock"] == 603.0
        assert svc.store.moves(gid)[3]["clock_ms"] == s["clock"]["opponent"]["ms"]
        assert "both sides have a clock (10+3)" in svc.brief(gid) and "a model of human play" in svc.brief(gid)
        assert "the opponent 10:0" in svc.tool_game(gid)
    finally:
        done(svc)


def test_a_pause_stops_allie_too():
    svc = service(think=0.8)
    try:
        g = svc.create("b", 1200, None, "10+3", "allie")
        gid = g["id"]
        watch(svc, gid, lambda s: s["plies"] == 1)
        svc.move(gid, "e5")
        watch(svc, gid, lambda s: s["plies"] == 3)
        svc.move(gid, "Nc6")
        p = svc.pause(gid, True)
        assert p["clock"]["paused"] and p["plies"] == 4
        left = svc.store.game(gid)["opp_clock_ms"]
        t = time.time()
        while time.time() - t < 2:  # a page is there all along, and still nothing moves
            s = svc.state(gid)
            assert s["plies"] == 4 and not s["clock"]["opponent"]["running"]
            time.sleep(0.05)
        assert svc.store.game(gid)["opp_clock_ms"] == left
        svc.pause(gid, False)
        s = watch(svc, gid, lambda s: s["plies"] == 5)
        assert left - 1300 + 3000 <= s["clock"]["opponent"]["ms"] <= left + 3000
    finally:
        done(svc)


def test_allie_can_lose_on_time():
    svc = service(think=30)
    try:
        g = svc.create("w", 1500, "french", "10+3", "allie")
        gid = g["id"]
        svc.move(gid, "e4")
        s = watch(svc, gid, lambda s: s["plies"] == 2)  # from its book: no think
        assert s["moves"][1]["source"] == "book" and s["clock"]["opponent"]["ms"] == 603000
        svc.move(gid, "d4")
        s = watch(svc, gid, lambda s: s["plies"] == 4)  # a book move again, now on its clock: half a second of it
        assert 603000 - 900 + 3000 <= s["clock"]["opponent"]["ms"] <= 603000 - 500 + 3000, s["clock"]
        svc.store.run("UPDATE games SET opening=NULL, opp_clock_ms=700 WHERE id=?", (gid,))
        svc.move(gid, "Nc3")
        s = watch(svc, gid, lambda s: s["status"] == "finished")
        assert s["result"] == "1-0" and s["reason"] == "time" and s["clock"]["opponent"]["ms"] == 0 and s["plies"] == 5
        try:
            svc.move(gid, "Nf3")
            raise AssertionError("a move was accepted after the game ended")
        except GameError as e:
            assert "the opponent ran out of time" in str(e)
    finally:
        done(svc)


def test_untimed_and_what_claude_is_given():
    svc = service(think=9, load=0.6)
    try:
        g = svc.create("w", 1100, None, None, "allie")
        gid = g["id"]
        assert g["clock"] is None
        svc.move(gid, "e4")
        seen = svc.state(gid)
        assert seen["thinking"] and seen["loading"]  # the model is still being read
        watch(svc, gid, lambda s: s["plies"] == 2, timeout=5)  # no clock: it moves when it has decided
        untimed = asked(svc, "decide")[0]
        assert untimed["base"] is None and untimed["clock"] is None and untimed["clocks"] == [None]
        text = svc.tool_humans(gid, moves=["Nf3"], top=3, line=2)
        assert "then Nf3" in text and "Black to move. What players rated 1100 play here" in text
        assert " 1. a4" in text and "(3 of " in text and "win 50%, draw 10%, loss 40%" in text
        assert (
            "Likeliest continuation, each side's most played move: 2... a4 (" in text
            and "check any line with analyze" in text
        )
        told = asked(svc, "predict")[0]
        assert told["moves"] == ["e2e4", "a7a5", "g1f3"] and (told["white_elo"], told["black_elo"]) == (1100, 1100)
        assert "players rated 2000" in svc.tool_humans(gid, ply=1, elo=2000)
        for bad in (dict(elo=50), dict(moves=["Ke2", "Ke2", "Ke2"])):
            try:
                svc.tool_humans(gid, **bad)
                raise AssertionError(f"accepted {bad}")
            except GameError:
                pass
        # the tool is offered to a session only where Allie is
        os.environ["CALLIE_ALLIE"] = "1"
        names = [t["name"] for t in mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
        assert names == ["game", "review", "position", "analyze", "human_moves"]
    finally:
        os.environ.pop("CALLIE_ALLIE", None)
        done(svc)


def test_allie_is_there_for_as_long_as_a_browser_is():
    svc = service(idle_s=1.0, load=0.2)

    def until(test, timeout=10, here=False):
        deadline = time.time() + timeout
        while not test() and time.time() < deadline:
            if here:
                svc.here()
            time.sleep(0.02)
        return test()

    try:
        time.sleep(0.5)
        assert not svc.allie.ready and svc.allie.loading is None  # the server has started, and no browser has come
        svc.here()  # one has the page on screen: no game, and nobody has asked it anything
        assert until(lambda: svc.allie.ready, here=True)
        first = svc.allie.proc
        gid = svc.create("b", 1200, None, None, "allie")["id"]
        seen = watch(svc, gid, lambda s: s["plies"] == 1)
        assert not seen["loading"] and svc.allie.proc is first  # the one that was loaded plays
        # still there: kept well past the time it is ended after, with nothing asked of it
        until(lambda: not svc.allie.ready, timeout=2.5, here=True)
        assert svc.allie.ready and svc.allie.proc is first and first.poll() is None
        # gone: it is ended, and the next browser to come starts another
        assert until(lambda: first.poll() is not None) and not svc.allie.ready
        svc.here()
        assert until(lambda: svc.allie.ready) and svc.allie.proc is not first
    finally:
        done(svc)


def test_one_allie_for_every_game_and_none_left_loading():
    svc = service(load=1.0)
    try:
        # two games against it, both asking for its first move while it loads: one process, loaded once
        ids = [svc.create("b", 1200, None, None, "allie")["id"] for _ in range(2)]
        for gid in ids:
            watch(svc, gid, lambda s: s["plies"] == 1)
        assert len({r["game"] for r in asked(svc, "decide")}) == 2
        first = svc.allie.proc
        assert first.poll() is None and (svc.cfg.home / "allie.log").exists()
        svc.allie.warm()
        assert svc.allie.proc is first
    finally:
        done(svc)
    assert first.poll() is not None
    # stopped while the model is being read: the process is ended there, not left to finish loading
    svc = service(load=30.0)
    try:
        svc.allie.warm()
        deadline = time.time() + 10
        while svc.allie.loading is None and time.time() < deadline:
            time.sleep(0.02)
        loading = svc.allie.loading
        assert loading is not None
    finally:
        t = time.time()
        done(svc)
    loading.wait(timeout=5)
    assert time.time() - t < 5 and not svc.allie.ready


def test_allie_offers_draws():
    svc = service(offer_at=3)  # with every move from its second
    try:
        gid = svc.create("w", 1500, None, None, "allie")["id"]
        svc.move(gid, "e4")
        s = watch(svc, gid, lambda s: s["plies"] == 2)
        assert not s["draw_offer"] and "offered a draw" not in svc.tool_game(gid)
        try:
            svc.draw(gid, True)
            raise AssertionError("a draw nobody offered was agreed")
        except GameError:
            pass
        svc.move(gid, "d4")
        s = watch(svc, gid, lambda s: s["plies"] == 4)
        assert s["draw_offer"] and not s["thinking"] and "offered a draw" in svc.tool_game(gid)
        s = svc.draw(gid, False)  # declined: the game goes on
        assert not s["draw_offer"] and s["status"] == "active" and s["legal"]
        s = svc.move(gid, "Nf3")
        assert not s["draw_offer"]
        svc.premove(gid, "b1c3")  # a move made ahead is not played over an offer, which is yours to answer
        s = watch(svc, gid, lambda s: s["plies"] >= 6)
        assert s["plies"] == 6 and s["draw_offer"] and s["premove"] is None
        svc.move(gid, "Nc3")  # playing on declines it too
        assert svc.store.game(gid)["draw_offer"] is None
        assert watch(svc, gid, lambda s: s["plies"] == 8)["draw_offer"]
        s = svc.draw(gid, True)
        assert (
            s["status"] == "finished"
            and s["result"] == "1/2-1/2"
            and s["reason"] == "agreement"
            and not s["draw_offer"]
        )
        assert "1/2-1/2" in s["pgn"] and "Finished: 1/2-1/2 by agreement" in svc.tool_game(gid)
        s = svc.takeback(gid)  # the move the offer answered goes, and the offer with it
        assert s["status"] == "active" and s["plies"] == 6 and not s["draw_offer"]
        g2 = svc.create("w", 1320)  # Stockfish never offers one
        try:
            svc.draw(g2["id"], True)
            raise AssertionError("a draw was agreed with Stockfish")
        except GameError:
            pass
    finally:
        done(svc)


def test_allie_resigns():
    svc = service(resign_at=3)  # instead of making its second move
    try:
        gid = svc.create("w", 1500, None, "10+3", "allie")["id"]
        svc.move(gid, "e4")
        watch(svc, gid, lambda s: s["plies"] == 2)
        svc.move(gid, "d4")
        s = watch(svc, gid, lambda s: s["status"] == "finished" and not s["thinking"])
        assert (
            s["result"] == "1-0"
            and s["reason"] == "resignation"
            and s["plies"] == 3
            and not s["clock"]["opponent"]["running"]
        )
        s = svc.takeback(gid)
        assert s["status"] == "active" and s["plies"] == 2 and s["turn"] == "w"
    finally:
        done(svc)
    svc = service(concede_at=4)  # on seeing the position after its second move
    try:
        gid = svc.create("w", 1500, None, None, "allie")["id"]
        svc.move(gid, "e4")
        s = watch(svc, gid, lambda s: s["plies"] == 2)
        time.sleep(0.3)
        assert svc.state(gid)["status"] == "active" and asked(svc, "concede")[0]["white"] is False
        svc.move(gid, "d4")
        s = watch(svc, gid, lambda s: s["status"] == "finished" and not s["thinking"])
        assert s["result"] == "1-0" and s["reason"] == "resignation" and s["plies"] == 4
        assert len(asked(svc, "concede")[-1]["moves"]) == 4
    finally:
        done(svc)
    svc = service(concede_at=1)  # playing White: after its first move
    try:
        gid = svc.create("b", 1500, None, None, "allie")["id"]
        s = watch(svc, gid, lambda s: s["status"] == "finished" and not s["thinking"])
        assert s["result"] == "0-1" and s["plies"] == 1 and asked(svc, "concede")[0]["white"] is True
    finally:
        done(svc)


def test_an_older_database_gains_the_new_columns():
    cfg = scratch_config()
    db = sqlite3.connect(cfg.db_path)
    db.executescript(
        "CREATE TABLE games (id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL, user_color TEXT NOT NULL,"
        " elo INTEGER, opening TEXT, start_fen TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', result TEXT, reason TEXT,"
        " time_control TEXT, clock_ms INTEGER, increment_ms INTEGER NOT NULL DEFAULT 0, paused INTEGER NOT NULL DEFAULT 0,"
        " session_id TEXT NOT NULL, session_started INTEGER NOT NULL DEFAULT 0, analysed_ply INTEGER NOT NULL DEFAULT 0);"
        "CREATE TABLE moves (game_id TEXT NOT NULL, ply INTEGER NOT NULL, uci TEXT NOT NULL, san TEXT NOT NULL, fen TEXT NOT NULL,"
        " source TEXT NOT NULL, played_at REAL NOT NULL, PRIMARY KEY (game_id, ply));"
        "INSERT INTO games (id, created_at, updated_at, user_color, elo, start_fen, session_id) VALUES ('abc', 1, 1, 'w', 1320, 'x', 's');"
    )
    db.commit()
    db.close()
    store = Store(cfg.db_path)
    old = store.game("abc")
    assert old["engine"] == "stockfish" and old["opp_clock_ms"] is None
    store.add_move("abc", 1, "e2e4", "e4", "fen", "user", 603000)
    assert store.moves("abc")[0]["clock_ms"] == 603000
    Store(cfg.db_path)  # and again: nothing left to add


load_tests = function_tests(globals())
