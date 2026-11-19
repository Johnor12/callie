"""Games through the service, with the real engine (fast settings) and a scripted opponent where the test needs one."""

import time

from callie import opponent
from callie.engine import Engine, loss, show, verdict, win_percent
from callie.play import GameError, Service
from tests import function_tests, need_engine, scratch_config


def service(script=None):
    cfg = scratch_config()
    need_engine(cfg)
    svc = Service(cfg)
    svc.start()
    if script is not None:  # the opponent plays these moves, whatever its strength
        moves = iter(script)
        svc._old_pick = opponent.pick
        opponent.pick = lambda engine, fen, played, elo, ms, rng=None: next(moves)
    return svc


def done(svc):
    if hasattr(svc, "_old_pick"):
        opponent.pick = svc._old_pick
    svc.close()


def until(svc, game_id, test, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = svc.state(game_id)
        if test(state):
            return state
        time.sleep(0.05)
    raise AssertionError(f"timed out; last state: plies={state['plies']} status={state['status']}")


def analysed(state):
    return state["start_eval"] and all("verdict" in m for m in state["moves"])


def test_score_arithmetic():
    assert win_percent(0) == 50 and win_percent(300) > 70 and win_percent(-9990) == 0
    assert loss(35, -120, True) == 155 and loss(-120, 35, False) == 155 and loss(35, 80, True) == 0
    assert loss(2500, 1500, True) == 0  # a win that stays a win costs nothing
    assert verdict(35, -120, True, False) == "mistake" and verdict(35, 35, True, True) == "best"
    assert verdict(0, -400, True, False) == "blunder" and verdict(0, -30, True, False) == "good"
    assert show(35, None) == "+0.35" and show(None, -3) == "#-3"


def test_engine_search():
    cfg = scratch_config()
    need_engine(cfg)
    e = Engine(str(cfg.engine))
    res = e.search("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1", ms=100)
    assert res.lines[0].mate == 1 and res.best in ("f7g7", "f7f8", "f7h7")
    black = e.search("6K1/8/6k1/8/8/8/5q2/8 b - - 0 1", ms=100)  # scores are White's, whoever is to move
    assert black.lines[0].value < -9000
    three = e.search("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", ["e2e4"], ms=100, multipv=3)
    assert len(three.lines) == 3 and len({ln.moves[0] for ln in three.lines}) == 3
    only = e.search("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", ms=50, only=["a2a3"])
    assert only.best == "a2a3"
    assert e.search("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", ms=50).best is None  # stalemate
    e.close()


def test_a_game_with_a_book_opponent():
    svc = service()
    try:
        g = svc.create("w", 1320, "french", None)
        assert g["turn"] == "w" and len(g["legal"]) == 20 and g["legal"]["e2e4"]["san"] == "e4" and g["clock"] is None
        try:
            svc.move(g["id"], "e2e5")
            raise AssertionError("an illegal move was accepted")
        except GameError:
            pass
        svc.move(g["id"], "e4")  # SAN is taken too
        s = until(svc, g["id"], lambda s: s["plies"] == 2)
        assert [m["san"] for m in s["moves"]] == ["e4", "e6"] and s["moves"][1]["source"] == "book"
        s = until(svc, g["id"], analysed)
        assert s["moves"][0]["cpl"] >= 0 and s["moves"][0]["verdict"] and s["acpl"]["user"] is not None
        assert not any("best" in m for m in s["moves"])  # what the engine preferred is not shown while the game is on
        assert '[White "You"]' in s["pgn"] and "1. e4 e6 *" in s["pgn"]
        # the tools a Claude session is given
        assert "the student plays White" in svc.tool_game(g["id"]) and "Legal moves" in svc.tool_game(g["id"])
        review = svc.tool_review(g["id"], who="all")
        assert "Ply 1: 1. e4" in review and "Ply 2: 1... e6" in review and "Engine's choices" in review
        assert "Ply 2" not in svc.tool_review(g["id"])  # the student's moves only, unless asked
        assert "then d4 d5" in svc.tool_position(g["id"], moves=["d4", "d5"])
        text = svc.tool_analyze(g["id"], moves=["d4"], candidates=["d5", "c5"], seconds=0.1)
        assert text.count("\n") >= 4 and "2... d5" in text and "2... c5" in text
        try:
            svc.tool_analyze(g["id"], moves=["Ke2", "Ke2"])
            raise AssertionError("an illegal line was accepted")
        except GameError as e:
            assert "Legal:" in str(e)
        # take back: your move and the reply go, the analysis of what remains stays
        s = svc.takeback(g["id"])
        assert s["plies"] == 0 and s["start_eval"] and s["turn"] == "w"
        assert [x["plies"] for x in svc.games()] == [0]
        svc.delete(g["id"])
        assert svc.games() == []
    finally:
        done(svc)


def test_playing_black_and_being_mated():
    svc = service(script=["e2e4", "d1h5", "f1c4", "h5f7"])  # the scholar's mate
    try:
        g = svc.create("b", None, None, None)
        until(svc, g["id"], lambda s: s["plies"] == 1)
        for mine in ("e5", "Nc6", "Nf6"):
            n = svc.state(g["id"])["plies"]
            svc.move(g["id"], mine)
            until(svc, g["id"], lambda s: s["plies"] == n + 2)
        s = until(svc, g["id"], analysed)
        assert s["status"] == "finished" and s["result"] == "1-0" and s["reason"] == "checkmate" and s["legal"] == {}
        assert s["moves"][-1]["san"] == "Qxf7#" and s["moves"][-1]["eval"]["mate"] == 0 and s["moves"][-1]["eval"]["value"] > 9000
        assert s["moves"][5]["verdict"] == "blunder"  # 3... Nf6 allows mate
        assert s["moves"][5]["best"]["line"] and s["moves"][5]["best"]["uci"] != "g8f6"  # over: what the engine preferred
        try:
            svc.move(g["id"], "Ke7")
            raise AssertionError("a move was accepted after the game ended")
        except GameError:
            pass
        assert "Finished: 1-0 by checkmate" in svc.tool_game(g["id"])
    finally:
        done(svc)


def test_resigning_and_wrong_openings():
    svc = service()
    try:
        for bad in (dict(color="w", opening="london"), dict(color="x"), dict(elo=50), dict(time_control="soon")):
            try:
                svc.create(**bad)
                raise AssertionError(f"accepted {bad}")
            except GameError:
                pass
        g = svc.create("w", 600, None, None)  # below Stockfish's own floor: the sampled opponent
        svc.move(g["id"], "d4")
        until(svc, g["id"], lambda s: s["plies"] == 2)
        s = svc.resign(g["id"])
        assert s["status"] == "finished" and s["result"] == "0-1" and s["reason"] == "resignation"
    finally:
        done(svc)


def test_the_clock():
    svc = service(script=["e7e5", "b8c6", "g8f6"])
    try:
        g = svc.create("w", 1320, None, "10+3")
        gid = g["id"]
        assert g["clock"] == {"ms": 600000, "increment_ms": 3000, "running": False, "paused": False, "opponent": None}
        svc.move(gid, "e4")
        s = until(svc, gid, lambda s: s["plies"] == 2)
        assert s["clock"]["ms"] == 603000 and s["clock"]["running"]  # not charged for the first move; the increment is
        # two seconds with a page watching: charged
        svc.clocks[gid] = time.monotonic() - 2
        s = svc.state(gid)
        assert 599000 < s["clock"]["ms"] <= 601100, s["clock"]
        # a minute with no page watching: not charged
        svc.clocks[gid] = time.monotonic() - 60
        assert svc.state(gid)["clock"]["ms"] == s["clock"]["ms"]
        # paused: nothing runs, and a move starts it again
        p = svc.pause(gid, True)
        assert p["clock"]["paused"] and not p["clock"]["running"] and gid not in svc.clocks
        svc.move(gid, "Nf3")
        s = until(svc, gid, lambda s: s["plies"] == 4)
        assert not s["clock"]["paused"] and s["clock"]["running"]
        # out of time
        svc.store.run("UPDATE games SET clock_ms=400 WHERE id=?", (gid,))
        svc.clocks[gid] = time.monotonic() - 1
        s = svc.state(gid)
        assert s["status"] == "finished" and s["result"] == "0-1" and s["reason"] == "time" and s["clock"]["ms"] == 0
    finally:
        done(svc)


load_tests = function_tests(globals())
