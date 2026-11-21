"""The web server: the page and a JSON API, standard library only.

Every action of the page is a plain HTTP call, so it can be driven with curl
(`-H 'Content-Type: application/json'` on POSTs; `curl -N` on a terminal's stream).

The API moves pieces, but it also starts Claude Code with the user's permissions and types into its terminals, so
web pages must not be able to reach it: requests are only served for a loopback Host (which blocks DNS
rebinding), a same-site Origin, and JSON POST bodies (which blocks cross-site forms). From another machine it is
reached through a proxy on this one (`tailscale serve`), under the names in `[server] hosts` and, when
`[server] tailscale_logins` is set, only for those logins.
"""

from __future__ import annotations

import base64
import gzip
import json
import mimetypes
import os
import re
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl

from .cards import CardError
from .config import Config
from .engine import EngineError
from .explain import Explainer
from .play import GameError, Service
from .tutor import Tutor, TutorError

STATIC = Path(__file__).resolve().parent / "static"
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
_ID = r"([0-9a-f]+)"
_TEXT = ("text/", "application/json", "application/javascript", "image/svg+xml")


def _size(b: dict) -> tuple[int, int]:
    return int(b.get("cols") or 110), int(b.get("rows") or 32)


def _tool(svc: Service, game_id: str, b: dict) -> dict:
    args = b.get("arguments") or {}
    name = b.get("name")
    where = {k: args.get(k) for k in ("ply", "fen", "moves")}
    quiet = bool(b.get("quiet"))  # asked from the background (an explanation being written): it gives way to the games
    if name == "game":
        return {"text": svc.tool_game(game_id)}
    if name == "review":
        return {
            "text": svc.tool_review(game_id, args.get("from_ply"), args.get("to_ply"), args.get("who") or "student")
        }
    if name == "position":
        return {"text": svc.tool_position(game_id, **where)}
    if name == "analyze":
        extra = {k: args[k] for k in ("seconds", "lines", "candidates") if args.get(k) is not None}
        return {"text": svc.tool_analyze(game_id, **where, **extra, quiet=quiet)}
    if name == "human_moves":
        if args.get("fen"):
            raise GameError(
                "human_moves reads a game from its first move: give a ply of this game and moves to play on from it, not a FEN"
            )
        extra = {k: args[k] for k in ("elo", "top", "line") if args.get(k) is not None}
        return {"text": svc.tool_humans(game_id, args.get("ply"), args.get("moves"), **extra, quiet=quiet)}
    raise GameError(f"no tool {name!r}")


def _routes(svc: Service, tutor: Tutor):
    ok = {"ok": True}
    since = lambda q: int(q["since"]) if q.get("since", "").lstrip("-").isdigit() else None  # noqa: E731
    return [
        (
            "GET",
            r"/api/health",
            lambda m, b, q: {"ok": True, "games": len(svc.store.all("SELECT id FROM games LIMIT 1"))},
        ),
        ("GET", r"/api/meta", lambda m, b, q: svc.meta()),
        ("POST", r"/api/here", lambda m, b, q: svc.here()),  # a page on screen (it is what keeps Allie in memory)
        ("GET", r"/api/games", lambda m, b, q: svc.games()),
        ("GET", r"/api/rating", lambda m, b, q: svc.rating()),
        (
            "POST",
            r"/api/games",
            lambda m, b, q: svc.create(
                b.get("color", "w"), b.get("elo"), b.get("opening"), b.get("time_control"), b.get("engine")
            ),
        ),
        ("GET", rf"/api/games/{_ID}", lambda m, b, q: svc.state(m[1], since(q))),
        ("POST", rf"/api/games/{_ID}/move", lambda m, b, q: svc.move(m[1], b.get("move", ""))),
        # a move made ahead, played when the opponent has moved ({"move": null} takes it back)
        ("POST", rf"/api/games/{_ID}/premove", lambda m, b, q: svc.premove(m[1], b.get("move"))),
        ("POST", rf"/api/games/{_ID}/resign", lambda m, b, q: svc.resign(m[1])),
        ("POST", rf"/api/games/{_ID}/takeback", lambda m, b, q: svc.takeback(m[1])),
        ("POST", rf"/api/games/{_ID}/draw", lambda m, b, q: svc.draw(m[1], bool(b.get("accept")))),
        ("POST", rf"/api/games/{_ID}/pause", lambda m, b, q: svc.pause(m[1], bool(b.get("paused", True)))),
        ("POST", rf"/api/games/{_ID}/delete", lambda m, b, q: svc.delete(m[1])),
        # the cards of your errors: an active one of a deck, whether one a page kept is still there, your move
        # on one, what a move on it loses, and one taken out of its deck
        ("GET", r"/api/cards", lambda m, b, q: svc.cards.decks()),
        (
            "GET",
            r"/api/cards/(blunder|mistake|inaccuracy)",
            lambda m, b, q: svc.cards.draw(m[1], int(q["skip"]) if q.get("skip", "").isdigit() else None),
        ),
        ("GET", r"/api/cards/(\d+)", lambda m, b, q: {"fen": svc.cards.fen(int(m[1]))}),
        (
            "POST",
            r"/api/cards/(\d+)/answer",
            lambda m, b, q: svc.cards.answer(int(m[1]), b.get("move", ""), b.get("grade")),
        ),
        ("GET", r"/api/cards/(\d+)/loss", lambda m, b, q: svc.card_loss(int(m[1]), q.get("move", ""))),
        ("POST", r"/api/cards/(\d+)/delete", lambda m, b, q: svc.cards.remove(int(m[1])) or ok),
        # Claude: nothing starts unless one of these is called
        ("POST", rf"/api/games/{_ID}/claude/analyse", lambda m, b, q: tutor.analyse(m[1], *_size(b))),
        ("POST", rf"/api/games/{_ID}/claude/say", lambda m, b, q: tutor.say(m[1], str(b.get("text", "")), *_size(b))),
        ("POST", rf"/api/games/{_ID}/claude/resume", lambda m, b, q: tutor.resume(m[1], *_size(b))),
        ("POST", rf"/api/games/{_ID}/claude/close", lambda m, b, q: tutor.close_terminal(m[1])),
        ("POST", rf"/api/games/{_ID}/claude/reset", lambda m, b, q: tutor.reset(m[1])),
        (
            "POST",
            rf"/api/terminals/{_ID}/input",
            lambda m, b, q: tutor.terminal(m[1]).write(str(b.get("data", "")).encode()) or ok,
        ),
        ("POST", rf"/api/terminals/{_ID}/resize", lambda m, b, q: tutor.terminal(m[1]).resize(*_size(b)) or ok),
        # what a Claude session's tools call (src/callie/mcp.py)
        ("POST", rf"/api/games/{_ID}/tool", lambda m, b, q: _tool(svc, m[1], b)),
    ]


def make_handler(svc: Service, tutor: Tutor, bind_host: str):
    routes = [(meth, re.compile(pat + r"$"), fn) for meth, pat, fn in _routes(svc, tutor)]
    stream = re.compile(rf"/api/terminals/{_ID}/stream$")
    names, logins = set(svc.cfg.hosts), set(svc.cfg.logins)

    def bare(host: str) -> str:
        """A host without its port."""
        return host.rsplit(":", 1)[0] if ":" in host and not host.endswith("]") else host

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet: the page waits on the server all the time
            pass

        def _send(self, status: int, body: bytes, ctype: str, etag: str | None = None) -> None:
            packed = len(body) > 1024 and ctype.startswith(_TEXT) and "gzip" in self.headers.get("Accept-Encoding", "")
            if packed:
                body = gzip.compress(body, 5)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if packed:
                self.send_header("Content-Encoding", "gzip")
            # A file of the page is kept by the browser and asked about every time; an answer of the API never is.
            self.send_header("Cache-Control", "no-cache" if etag else "no-store")
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the page stopped waiting

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj, default=str).encode(), "application/json")

        def _refusal(self, method: str) -> tuple[int, str] | None:
            port = self.server.server_address[1]
            ours = {f"{h}:{port}" for h in (*LOOPBACK, bind_host)}
            host = (self.headers.get("Host") or "").lower()
            named = bare(host) in names
            if host not in ours and not named:
                return 403, "unexpected Host header"
            origin = self.headers.get("Origin")
            if origin:
                site = origin.partition("://")[2].lower()
                if site not in ours and bare(site) not in names:
                    return 403, "cross-origin requests are not allowed"
            # Under another name, or forwarded, it came from another machine through a proxy on this one
            # (`tailscale serve`), which says who sent it and drops what the sender claimed.
            if named or "X-Forwarded-For" in self.headers:
                who = (self.headers.get("Tailscale-User-Login") or "").lower()
                if not names:
                    return 403, "forwarded requests are not served: name the proxy's host in [server] hosts"
                if logins and who not in logins:
                    return 403, f"{who or 'a request with no Tailscale login'} is not in [server] tailscale_logins"
            if method == "POST" and self.headers.get_content_type() != "application/json":
                return 415, "POST bodies must be Content-Type: application/json"
            return None

        def _dispatch(self, method: str) -> None:
            refusal = self._refusal(method)
            if refusal is not None:
                self.close_connection = True  # the request body was not read
                return self._json(refusal[0], {"error": refusal[1]})
            path, _, query = self.path.partition("?")
            if method == "GET" and not path.startswith("/api/"):
                return self._static(path)
            m = stream.match(path)
            if m and method == "GET":
                return self._stream(m[1])
            body = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._json(400, {"error": "invalid JSON body"})
                if not isinstance(body, dict):
                    return self._json(400, {"error": "the JSON body must be an object"})
            for meth, rx, fn in routes:
                m = rx.match(path)
                if m and meth == method:
                    try:
                        return self._json(200, fn(m, body, dict(parse_qsl(query))))
                    except (GameError, CardError, TutorError, EngineError) as e:
                        return self._json(400, {"error": str(e)})
                    except Exception as e:  # noqa: BLE001
                        return self._json(500, {"error": f"{type(e).__name__}: {e}"})
            self._json(404, {"error": f"no route {method} {path}"})

        def _stream(self, term_id: str) -> None:
            """Server-sent events: the terminal's output as base64 `data:` lines, then an `exit` event."""
            try:
                frames = tutor.terminal(term_id).stream()
            except TutorError as e:
                return self._json(404, {"error": str(e)})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                for kind, payload in frames:
                    if kind == "o":
                        self.wfile.write(b"data: " + base64.b64encode(payload) + b"\n\n")
                    elif kind == "x":
                        self.wfile.write(b"event: exit\ndata: {}\n\n")
                    else:
                        self.wfile.write(b": idle\n\n")  # also how a closed browser tab is noticed
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            finally:
                frames.close()

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("/", "") else path.lstrip("/").removeprefix("static/")
            f = (STATIC / rel).resolve()
            if not f.is_relative_to(STATIC) or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            st = f.stat()
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
            if self.headers.get("If-None-Match") == etag:  # the browser has this very file
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream", etag)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler


def make_server(
    cfg: Config, host: str | None = None, port: int | None = None
) -> tuple[ThreadingHTTPServer, Service, Tutor]:
    host, port = host or cfg.host, cfg.port if port is None else port
    svc = Service(cfg)
    tutor = Tutor(cfg, svc)
    svc.tutor = tutor
    svc.explainer = Explainer(cfg, svc)
    httpd = ThreadingHTTPServer((host, port), make_handler(svc, tutor, host))
    httpd.daemon_threads = True
    cfg.host, cfg.port = host, httpd.server_address[1]  # what Claude's tools are told to call
    svc.start()
    tutor.start()
    svc.explainer.start()
    return httpd, svc, tutor


def _interrupt(*_):
    raise KeyboardInterrupt


def serve(cfg: Config, host: str | None = None, port: int | None = None) -> None:
    """Run the server in this process until it is interrupted."""
    httpd, svc, tutor = make_server(cfg, host, port)
    pidfile = cfg.home / "server.pid"
    pidfile.write_text(json.dumps({"pid": os.getpid(), "url": cfg.url}))
    signal.signal(signal.SIGTERM, _interrupt)  # leave the way Ctrl+C does
    also = "".join(f", also answers to {h}" for h in cfg.hosts)
    print(f"callie: {cfg.url}{also}  (state: {cfg.home})", flush=True)
    if not cfg.engine.exists():
        print(f"callie: no engine at {cfg.engine}: run `callie engine install`", file=sys.stderr, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        tutor.close()
        svc.close()
        httpd.server_close()
        try:
            if json.loads(pidfile.read_text()).get("pid") == os.getpid():
                pidfile.unlink()
        except (OSError, ValueError):
            pass
