"""The web server (who it answers), terminals, and how a game's Claude session is started, with a fake claude."""

import json
import os
import threading
import time
import urllib.error
import urllib.request

from chesstutor import mcp, server
from chesstutor.terminals import Terminal
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
        assert s.call("GET", "/")[0] == 200 and b"Chess Tutor" in s.call("GET", "/")[1]
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
    t = Terminal(["sh", "-c", "stty size; read x; echo got:$x"], dict(os.environ), str(cfg.home), 91, 27, cfg.home / "t.log")
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
        assert state["claude"]["started"] and state["claude"]["terminal"] == term.id and state["claude"]["analysed_ply"] >= 1
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
        init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        assert init["result"]["capabilities"] == {"tools": {}} and init["result"]["protocolVersion"] == "2025-06-18"
        names = [t["name"] for t in mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
        assert names == ["game", "review", "position", "analyze"]
        os.environ["CHESSTUTOR_URL"], os.environ["CHESSTUTOR_GAME"] = s.url, g["id"]

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
    finally:
        os.environ.pop("CHESSTUTOR_URL", None)
        os.environ.pop("CHESSTUTOR_GAME", None)
        s.close()


load_tests = function_tests(globals())
