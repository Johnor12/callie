"""The web server (who it answers), terminals, and how a game's Claude session is started, with a fake claude."""

import json
import os
import threading
import time
import urllib.error
import urllib.request

from callie import mcp, server
from callie.terminals import Terminal
from tests import function_tests, need_engine, scratch_config


class Running:
    def __init__(self, **changes):
        self.cfg = scratch_config(**changes)
        self.httpd, self.svc, self.tutor = server.make_server(self.cfg)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = self.cfg.url

    def call(self, method, path, body=None, headers=None):
        head = {"Content-Type": "application/json"} if method == "POST" else {}
        head.update(headers or {})
        data = json.dumps(body or {}).encode() if method == "POST" else None
        req = urllib.request.Request(self.url + path, data=data, headers=head, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, json.loads(raw) if r.headers.get_content_type() == "application/json" else raw
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, json.loads(raw or b"{}") if e.headers.get_content_type() == "application/json" else raw

    def close(self):
        self.tutor.close()
        self.svc.close()
        self.httpd.shutdown()
        self.httpd.server_close()


def output(term, want, timeout=30):
    """What a terminal has written, once it contains ``want``."""
    deadline, seen = time.time() + timeout, b""
    for kind, data in term.stream(idle=0.2):
        seen += data
        if want.encode() in seen or kind == "x" or time.time() > deadline:
            break
    assert want.encode() in seen, seen[-400:]
    return seen.decode(errors="replace")


def test_who_is_answered():
    s = Running()
    try:
        assert s.call("GET", "/api/games") == (200, [])
        assert s.call("POST", "/api/here") == (200, {"ok": True})  # a page on screen, where Allie is not installed
        assert s.call("GET", "/")[0] == 200 and b"Callie" in s.call("GET", "/")[1]
        assert s.call("GET", "/static/../config.py")[0] == 404
        assert s.call("GET", "/api/games", headers={"Host": "evil.example"})[0] == 403  # DNS rebinding
        assert s.call("GET", "/api/games", headers={"Origin": "https://evil.example"})[0] == 403
        assert s.call("POST", "/api/games", headers={"Content-Type": "text/plain"})[0] == 415  # a cross-site form
        # forwarded by a proxy, with no name configured: refused
        assert s.call("GET", "/api/games", headers={"X-Forwarded-For": "100.1.2.3"})[0] == 403
        assert s.call("GET", "/api/games/abc123")[0] == 400 and s.call("GET", "/api/nope")[0] == 404
    finally:
        s.close()
    s = Running(hosts=["box.tail1234.ts.net"], logins=["me@example.com"])
    try:
        named = {"Host": "box.tail1234.ts.net:8443", "Origin": "https://box.tail1234.ts.net:8443"}
        assert s.call("GET", "/api/games", headers=named)[0] == 403  # no login
        assert s.call("GET", "/api/games", headers={**named, "Tailscale-User-Login": "other@example.com"})[0] == 403
        assert s.call("GET", "/api/games", headers={**named, "Tailscale-User-Login": "Me@example.com"})[0] == 200
        assert s.call("GET", "/api/games")[0] == 200  # this machine itself
    finally:
        s.close()


def test_a_terminal():
    cfg = scratch_config()
    t = Terminal(
        ["sh", "-c", "stty size; read x; echo got:$x"], dict(os.environ), str(cfg.home), 91, 27, cfg.home / "t.log"
    )
    assert "27 91" in output(t, "27 91")  # the program sees the window size it was given
    t.resize(100, 30)
    t.resize(100, 30)  # the same size again: a repaint, not an error
    t.write(b"hello\n")
    assert "got:hello" in output(t, "got:hello")  # a late viewer is given what was written before it came
    deadline = time.time() + 5
    while t.alive and time.time() < deadline:
        time.sleep(0.05)
    assert not t.alive and t.exit_code == 0


def test_a_claude_session_is_started_resumed_and_talked_to():
    s = Running()
    try:
        need_engine(s.cfg)
        _, g = s.call("POST", "/api/games", {"color": "w", "elo": 1320, "opening": "french", "time_control": "untimed"})
        gid = g["id"]
        assert g["claude"] == {"started": False, "analysed_ply": 0, "terminal": None}
        assert s.call("POST", f"/api/games/{gid}/claude/resume")[0] == 400  # nothing to resume yet
        assert s.call("POST", f"/api/games/{gid}/draw", {"accept": True}) == (
            400,
            {"error": "there is no draw offer to answer"},
        )
        s.call("POST", f"/api/games/{gid}/move", {"move": "e4"})
        # the button: a new session, told what the game is and what is wanted
        code, r = s.call("POST", f"/api/games/{gid}/claude/analyse", {"cols": 100, "rows": 30})
        assert code == 200
        term = s.tutor.terminal(r["terminal"])
        first = json.loads(output(term, '"prompt"').split("\n")[0])
        assert first["mode"] == "new" and first["model"] == "claude-fable-5-2" and first["mcp"]
        assert first["cwd"] == str(s.cfg.workspace)  # every game's session runs in the one directory
        assert first["prompt"].startswith("[analysis request] Game " + gid) and "French Defence" in first["prompt"]
        state = s.call("GET", f"/api/games/{gid}")[1]
        assert (
            state["claude"]["started"]
            and state["claude"]["terminal"] == term.id
            and state["claude"]["analysed_ply"] >= 1
        )
        # the box under the terminal types into it
        s.call("POST", f"/api/games/{gid}/claude/say", {"text": "why not 1. d4?"})
        assert "why not 1. d4?" in output(term, "heard:")  # pasted, then Enter
        s.call("POST", f"/api/terminals/{term.id}/input", {"data": "typed by hand\n"})
        assert "heard: typed by hand" in output(term, "heard: typed by hand")
        # closed, then reopened: the same session, resumed
        s.call("POST", f"/api/games/{gid}/claude/close")
        assert s.call("GET", f"/api/games/{gid}")[1]["claude"]["terminal"] is None
        code, r2 = s.call("POST", f"/api/games/{gid}/claude/resume")
        again = json.loads(output(s.tutor.terminal(r2["terminal"]), '"prompt"').split("\n")[0])
        assert again["mode"] == "resume" and again["session"] == first["session"] and again["prompt"] == ""
        # another game: another session, the same directory
        _, g2 = s.call("POST", "/api/games", {"color": "b", "elo": None})
        code, r3 = s.call("POST", f"/api/games/{g2['id']}/claude/say", {"text": "hello"})
        other = json.loads(output(s.tutor.terminal(r3["terminal"]), '"prompt"').split("\n")[0])
        assert other["session"] != first["session"] and other["cwd"] == first["cwd"]
        assert other["prompt"].startswith("[context] Game " + g2["id"]) and other["prompt"].endswith("hello")
        # forgotten: the next analysis is a new session
        s.call("POST", f"/api/games/{gid}/claude/reset")
        fresh = s.call("GET", f"/api/games/{gid}")[1]["claude"]
        assert fresh == {"started": False, "analysed_ply": 0, "terminal": None}
    finally:
        s.close()


def test_a_terminal_nobody_watches_is_ended_and_the_session_resumes():
    s = Running(idle_terminal_s=0.5)
    try:
        need_engine(s.cfg)
        _, g = s.call("POST", "/api/games", {"color": "w", "elo": 1320})
        _, r = s.call("POST", f"/api/games/{g['id']}/claude/analyse")
        term = s.tutor.terminal(r["terminal"])
        output(term, '"prompt"')  # watched for a moment, then left alone
        deadline = time.time() + 15
        while s.call("GET", f"/api/games/{g['id']}")[1]["claude"]["terminal"] and time.time() < deadline:
            time.sleep(0.2)
        left = s.call("GET", f"/api/games/{g['id']}")[1]["claude"]
        assert left["terminal"] is None and left["started"]
        deadline = time.time() + 10
        while term.alive and time.time() < deadline:
            time.sleep(0.1)
        assert not term.alive
        code, r2 = s.call("POST", f"/api/games/{g['id']}/claude/resume")
        assert code == 200 and r2["terminal"] != r["terminal"]
    finally:
        s.close()


def test_the_tools_claude_is_given():
    s = Running()
    try:
        need_engine(s.cfg)
        _, g = s.call("POST", "/api/games", {"color": "w", "elo": 1320})
        assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        init = mcp.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
        )
        assert init["result"]["capabilities"] == {"tools": {}} and init["result"]["protocolVersion"] == "2025-06-18"
        names = [t["name"] for t in mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
        assert names == ["game", "review", "position", "analyze"]
        os.environ["CALLIE_URL"], os.environ["CALLIE_GAME"] = s.url, g["id"]

        def tool(name, **arguments):
            msg = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
            result = mcp.handle(msg)["result"]
            return result["content"][0]["text"], result["isError"]

        assert "the student plays White" in tool("game")[0]
        assert tool("review") == ("No move has been played yet.", False)
        text, failed = tool("analyze", moves=["e4", "e5"], lines=2, seconds=0.1)
        assert not failed and "then e4 e5" in text and text.count("2. ") >= 2
        assert "Legal moves (20)" in tool("position", ply=0)[0]
        assert tool("position", fen="nonsense")[1] and tool("nope")[1]
        assert mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})["error"]["code"] == -32601
        # a session nobody talks to (it has no pane beside a board) is not offered the board, and has none
        assert "no board beside it" in tool("show_lines", lines=[{"name": "a", "moves": ["e4"]}])[0]
    finally:
        os.environ.pop("CALLIE_URL", None)
        os.environ.pop("CALLIE_GAME", None)
        s.close()


def test_claude_puts_lines_on_the_board():
    s = Running()
    try:
        need_engine(s.cfg)
        _, g = s.call("POST", "/api/games", {"color": "w", "elo": 1320, "opening": "french"})
        gid = g["id"]
        s.call("POST", f"/api/games/{gid}/move", {"move": "e4"})
        _, r = s.call("POST", f"/api/games/{gid}/claude/say", {"text": "hello"})
        term = s.tutor.terminal(r["terminal"])
        session = json.loads(output(term, '"prompt"').split("\n")[0])["session"]
        # the session's tools are told which conversation they belong to, and so are offered the board
        env = json.loads((s.cfg.home / "sessions" / f"{gid}.mcp.json").read_text())["mcpServers"]["chess"]["env"]
        assert env["CALLIE_SESSION"] == session and env["CALLIE_GAME"] == gid
        os.environ.update(CALLIE_URL=s.url, CALLIE_GAME=gid, CALLIE_SESSION=session)
        names = [t["name"] for t in mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
        assert names == ["game", "review", "position", "analyze", "show_lines"]

        def show(**arguments):
            params = {"name": "show_lines", "arguments": arguments}
            result = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params})["result"]
            return result["content"][0]["text"], result["isError"]

        assert s.call("GET", f"/api/terminals/{term.id}/lines") == (200, {"shown": []})
        watching = term.stream(idle=0.2)
        next(watching)  # a page on the terminal
        fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
        lines = [{"name": "Two knights", "moves": ["Nf6", "Ng5", "d5"]}, {"moves": ["f8c5", "c3"]}]
        text, failed = show(fen=fen, title="  Black's third move ", lines=lines)
        assert not failed and 'as "Black\'s third move", from the given position' in text
        assert "- Two knights: 3... Nf6 4. Ng5 d5" in text and "- Line 2: 3... Bc5 4. c3" in text
        deadline = time.time() + 10
        while next(watching)[0] != "lines":  # the page is told, on the terminal's stream
            assert time.time() < deadline
        watching.close()
        (board,) = s.call("GET", f"/api/terminals/{term.id}/lines")[1]["shown"]
        assert board["title"] == "Black's third move" and board["fen"] == fen
        assert [ln["name"] for ln in board["lines"]] == ["Two knights", "Line 2"]
        assert [m["san"] for m in board["lines"][0]["moves"]] == ["Nf6", "Ng5", "d5"]
        # each move with the position after it: the page plays a line with no rules of its own
        assert board["lines"][1]["moves"][0] == {
            "uci": "f8c5",
            "san": "Bc5",
            "fen": "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
        }
        # from a point in the game, after moves played on it; with no title, the position is one
        text, failed = show(ply=0, moves=["d4"], lines=[{"name": "Slav", "moves": ["d5", "c4", "c6"]}])
        assert not failed and "- Slav: 1... d5 2. c4 c6" in text
        shown = s.call("GET", f"/api/terminals/{term.id}/lines")[1]["shown"]
        assert [b["title"] for b in shown] == ["Black's third move", "The start position then d4"]
        assert shown[0]["id"] < shown[1]["id"]
        # a line that cannot be played shows nothing, and says where it stops
        text, failed = show(
            fen=fen, lines=[{"name": "ok", "moves": ["Nf6"]}, {"name": "Oops", "moves": ["Nf6", "Ke2", "Nxe4", "Kf3"]}]
        )
        assert failed and "line 2 (Oops): 'Kf3' is not legal after 3... Nf6 4. Ke2 Nxe4. Legal: " in text
        assert show(fen=fen, lines=[])[1] and show(fen=fen, lines=[{"name": "x", "moves": []}])[1]
        assert show(fen=fen, lines=[{"name": "x", "moves": ["Nf6"]}] * 9)[1]
        assert len(s.call("GET", f"/api/terminals/{term.id}/lines")[1]["shown"]) == 2
        # they are the conversation's: there when its terminal is another one, gone when it is forgotten
        s.call("POST", f"/api/games/{gid}/claude/close")
        assert s.call("GET", f"/api/terminals/{term.id}/lines")[0] == 400
        _, r2 = s.call("POST", f"/api/games/{gid}/claude/resume")
        assert len(s.call("GET", f"/api/terminals/{r2['terminal']}/lines")[1]["shown"]) == 2
        for i in range(31):  # the last 30 of a conversation are kept
            assert not show(fen=fen, title=f"n{i}", lines=[{"name": "x", "moves": ["Nf6"]}])[1]
        kept = s.call("GET", f"/api/terminals/{r2['terminal']}/lines")[1]["shown"]
        assert len(kept) == 30 and kept[0]["title"] == "n1" and kept[-1]["title"] == "n30"
        s.call("POST", f"/api/games/{gid}/claude/reset")
        assert "no board beside it" in show(fen=fen, lines=[{"name": "x", "moves": ["Nf6"]}])[0]
        _, r3 = s.call("POST", f"/api/games/{gid}/claude/say", {"text": "again"})
        new = json.loads(output(s.tutor.terminal(r3["terminal"]), '"prompt"').split("\n")[0])["session"]
        os.environ["CALLIE_SESSION"] = new
        assert not show(fen=fen, lines=[{"name": "x", "moves": ["Nf6"]}])[1]  # what the old one showed went then
        assert s.svc.store.all("SELECT session_id FROM shown") == [{"session_id": new}]
    finally:
        for name in ("CALLIE_URL", "CALLIE_GAME", "CALLIE_SESSION"):
            os.environ.pop(name, None)
        s.close()


def test_the_freeplay_board():
    s = Running()
    try:
        need_engine(s.cfg)
        # a position to move pieces in: where a game starts, or the one asked for, with every move and where it leads
        code, start = s.call("GET", "/api/freeplay")
        assert code == 200 and start["turn"] == "w" and len(start["legal"]) == 20
        e4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
        assert start["legal"]["e2e4"] == {"san": "e4", "fen": e4}
        fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
        asked = fen.replace(" ", "%20")
        there = s.call("GET", f"/api/freeplay?fen={asked}")[1]
        assert there["fen"] == fen and there["turn"] == "b" and there["legal"]["g8f6"]["san"] == "Nf6"
        assert s.call("GET", "/api/freeplay?fen=nonsense")[0] == 400
        # the engine's view of one: the evaluation, its first choice and the line it expects
        code, seen = s.call("GET", f"/api/freeplay/eval?fen={asked}&ms=100")
        assert code == 200 and seen["fen"] == fen and seen["depth"] > 0 and not seen["over"]
        assert seen["best"]["uci"] in there["legal"] and seen["line"][0] == seen["best"]["san"]
        assert isinstance(seen["cp"], int) and seen["value"] == seen["cp"] and seen["mate"] is None
        assert s.call("GET", "/api/freeplay/eval?ms=soon")[0] == 400
        mated = s.call("GET", "/api/freeplay/eval?fen=7k/6Q1/6K1/8/8/8/8/8%20b%20-%20-%200%201")[1]
        assert mated["over"] and mated["mate"] == 0 and mated["value"] > 9000 and mated["best"] is None
        stuck = s.call("GET", "/api/freeplay/eval?fen=7k/5Q2/6K1/8/8/8/8/8%20b%20-%20-%200%201")[1]
        assert stuck["over"] and stuck["cp"] == 0 and stuck["mate"] is None
        assert s.call("GET", "/api/freeplay?fen=7k/6Q1/6K1/8/8/8/8/8%20b%20-%20-%200%201")[1]["legal"] == {}
        # a position asked about ends the search of the one before it
        long = {}
        began = time.time()
        first = threading.Thread(target=lambda: long.update(seen=s.call("GET", "/api/freeplay/eval?ms=10000")))
        first.start()
        time.sleep(0.5)
        assert s.call("GET", f"/api/freeplay/eval?fen={asked}&ms=100")[1]["best"]
        first.join()
        assert time.time() - began < 6 and long["seen"][0] == 200
        # Claude beside it: nothing until it is asked, then a session of the board's own, told what is on the board
        assert s.call("POST", "/api/freeplay/claude/resume") == (200, {"terminal": None})
        assert s.call("POST", "/api/freeplay/claude/say", {"text": " "})[0] == 400 and not s.tutor.terms
        assert s.call("POST", "/api/freeplay/claude/say", {"text": "why?", "moves": ["e2e5"]})[0] == 400
        code, r = s.call("POST", "/api/freeplay/claude/say", {"text": "why e4?", "moves": ["e2e4"], "cols": 90})
        said = lambda term: json.loads(output(term, '"prompt"').split("\n")[0])  # noqa: E731
        told = said(s.tutor.terminal(r["terminal"]))
        assert code == 200 and told["mode"] == "new" and told["cwd"] == str(s.cfg.workspace) and told["mcp"]
        assert told["prompt"] == f"[board: {e4}, Black to move, after 1. e4 from the starting position]\nwhy e4?"
        assert s.svc.store.freeplay() == {"id": 1, "session_id": told["session"], "session_started": 1}
        # its tools are those that take any position, and its lines go to the pane beside the board
        env = json.loads((s.cfg.home / "sessions" / "freeplay.mcp.json").read_text())["mcpServers"]["chess"]["env"]
        assert env == {
            "CALLIE_URL": s.url,
            "CALLIE_GAME": "0",
            "CALLIE_SESSION": told["session"],
            "CALLIE_FREEPLAY": "1",
        }
        os.environ.update(env)
        tools = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        assert [t["name"] for t in tools] == ["position", "analyze", "show_lines"]
        assert all("ply" not in t["inputSchema"]["properties"] and "fen" in t["inputSchema"]["required"] for t in tools)
        text, failed = mcp.call("show_lines", {"fen": fen, "lines": [{"name": "Two knights", "moves": ["Nf6", "Ng5"]}]})
        assert not failed and "- Two knights: 3... Nf6 4. Ng5" in text
        assert s.call("GET", f"/api/terminals/{r['terminal']}/lines")[1]["shown"][0]["fen"] == fen
        assert "Legal moves (20)" in mcp.call("position", {"fen": start["fen"]})[0]
        # the page shows that terminal, and a message from another position is typed into it, with that position
        assert s.call("POST", "/api/freeplay/claude/resume")[1] == r
        s.call("POST", "/api/freeplay/claude/say", {"text": "and here?", "fen": fen, "moves": ["Nf6"]})
        heard = output(s.tutor.terminal(r["terminal"]), "and here?")
        assert "Black to move" not in heard.split("heard:")[1] and f"after 3... Nf6 from {fen}]" in heard
        # its terminal gone (the server was restarted, say): the same conversation, resumed
        s.tutor.forget("freeplay")
        code, r2 = s.call("POST", "/api/freeplay/claude/resume")
        again = said(s.tutor.terminal(r2["terminal"]))
        assert r2 != r and again["mode"] == "resume" and again["session"] == told["session"] and again["prompt"] == ""
        assert len(s.call("GET", f"/api/terminals/{r2['terminal']}/lines")[1]["shown"]) == 1
        s.tutor.forget("freeplay")
        code, r3 = s.call("POST", "/api/freeplay/claude/say", {"text": "one more", "fen": fen})
        more = said(s.tutor.terminal(r3["terminal"]))
        assert more["mode"] == "resume" and more["prompt"].endswith("which is where the board started]\none more")
        page = s.call("GET", "/")[1]
        assert (
            b'id="free-board"' in page
            and b'id="free-open"' in page
            and b'id="card-free"' in page
            and b'id="act-free"' in page
        )
    finally:
        for name in ("CALLIE_URL", "CALLIE_GAME", "CALLIE_SESSION", "CALLIE_FREEPLAY"):
            os.environ.pop(name, None)
        s.close()


load_tests = function_tests(globals())
