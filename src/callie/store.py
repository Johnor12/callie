"""The database: games, their moves, the engine's analysis of each position, the cards made of your errors and
of the heuristics' and the openings' positions, the lines of your own tried on a board, and the lines Claude put
on the board in its conversations. SQLite, one file."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  user_color TEXT NOT NULL,            -- 'w' or 'b'
  engine TEXT NOT NULL DEFAULT 'stockfish',  -- who the opponent is: 'stockfish' or 'allie'
  elo INTEGER,                          -- the opponent's rating (Allie's scale, whoever plays); NULL: full strength
  opening TEXT,                         -- key of the opening the opponent steers for; NULL: its own choice
  start_fen TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',  -- 'active' or 'finished'
  finished_at REAL,                     -- when it ended; NULL while it is being played
  result TEXT,                          -- '1-0', '0-1', '1/2-1/2'
  reason TEXT,                          -- checkmate, resignation, stalemate, ...
  time_control TEXT,                    -- '10+3' (minutes + seconds a move); NULL: untimed
  clock_ms INTEGER,                     -- what is left on your clock
  opp_clock_ms INTEGER,                 -- what is left on the opponent's; NULL: it has none (Stockfish never has)
  increment_ms INTEGER NOT NULL DEFAULT 0,
  paused INTEGER NOT NULL DEFAULT 0,    -- you stopped the clock (yours, and the opponent's if it has one)
  draw_offer INTEGER,                   -- the ply of the opponent's move that came with a draw offer; NULL: none stands
  session_id TEXT NOT NULL,             -- the Claude Code session that analyses this game
  session_started INTEGER NOT NULL DEFAULT 0,  -- it was given its first prompt, so it can be resumed
  analysed_ply INTEGER NOT NULL DEFAULT 0      -- how far the game was when Claude was last asked to analyse it
);
CREATE TABLE IF NOT EXISTS moves (
  game_id TEXT NOT NULL,
  ply INTEGER NOT NULL,                 -- 1 is White's first move
  uci TEXT NOT NULL,
  san TEXT NOT NULL,
  fen TEXT NOT NULL,                    -- the position after the move
  source TEXT NOT NULL,                 -- 'user', 'engine' or 'book'
  played_at REAL NOT NULL,
  clock_ms INTEGER,                     -- the mover's clock after the move, increment included; NULL: it has none
  PRIMARY KEY (game_id, ply)
);
CREATE TABLE IF NOT EXISTS evals (      -- the position after `ply` moves (0: the start), at full strength
  game_id TEXT NOT NULL,
  ply INTEGER NOT NULL,
  fen TEXT NOT NULL,                    -- what was analysed: a take-back makes rows for other positions stale
  depth INTEGER NOT NULL,
  value INTEGER NOT NULL,               -- for White, in centipawns; mates are beyond +-9000
  cp INTEGER,
  mate INTEGER,
  lines TEXT NOT NULL,                  -- JSON: [{"moves": [uci...], "cp": n, "mate": n}], best first
  PRIMARY KEY (game_id, ply)
);
CREATE TABLE IF NOT EXISTS cards (      -- an error of yours to review: one for each position and the move played in it;
                                        -- or a position that shows a heuristic, or one of an opening (cards.py):
                                        -- one for each position
  id INTEGER PRIMARY KEY,
  position TEXT NOT NULL,               -- what makes it the same position in any game: the FEN less its move counters
  uci TEXT NOT NULL,                    -- the move you played; '': none, the card is a heuristic's or an opener's
  san TEXT NOT NULL,
  fen TEXT NOT NULL,                    -- the position before it, in the game it was last played in
  prev TEXT,                            -- the move that led to that position there (the opponent's); NULL: none, or not known
  cpl INTEGER NOT NULL,                 -- what it lost there, in centipawns: which deck the card is in
  lines TEXT NOT NULL,                  -- JSON: the engine's lines in the position, as in evals
  erred_at REAL NOT NULL,               -- when you last played it
  state TEXT NOT NULL DEFAULT 'new',    -- where it is in its schedule (cards.py): 'new', 'learning', 'review', 'relearning';
                                        -- or 'removed': taken out of its deck by you, and kept so that it is not made again
  steps INTEGER NOT NULL DEFAULT 0,     -- while it is being learned: the steps it has yet to pass
  interval INTEGER NOT NULL DEFAULT 0,  -- once learned: the days between two reviews of it
  ease REAL NOT NULL DEFAULT 2.5,       -- what a good answer multiplies the interval by
  due REAL NOT NULL,                    -- from when it is to be reviewed
  reviewed_at REAL,                     -- when it was last answered
  explanation TEXT,                     -- why the move was bad, as Claude wrote it; NULL: not written yet
  heuristic TEXT,                       -- the heuristic its position shows (its key in heuristics.json), whose deck it is
                                        -- in, with how the heuristic finds the move as its explanation; NULL: not one
  opener TEXT,                          -- the opening its position is of (its key in openers.json), whose deck it is in,
                                        -- with why the move is the one as its explanation; NULL: not one
  game_id TEXT,                         -- the game it was last played in, which it is deleted with; NULL: not known
  session_id TEXT,                      -- the Claude Code session asked about it from its back; NULL: none yet
  session_started INTEGER NOT NULL DEFAULT 0,  -- it was given its first prompt, so it can be resumed
  UNIQUE (position, uci)
);
CREATE TABLE IF NOT EXISTS shown (      -- what Claude put on the board in a conversation: a position and lines from it
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,             -- the Claude Code session it was shown in: a game's or a card's
  shown_at REAL NOT NULL,
  board TEXT NOT NULL                   -- JSON: {"title", "fen", "lines": [{"name", "moves": [{"uci", "san", "fen"}]}]}
);
CREATE TABLE IF NOT EXISTS explored (   -- a line of your own, tried on a board that is not being played: the back of a
                                        -- card, a game that is over
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  board TEXT NOT NULL,                  -- whose board: a game's id, or "card-" and a card's
  tree TEXT NOT NULL,                   -- what it grew from: "own" (a position of the board's own), or "s" and the id
                                        -- of what Claude showed there
  fen TEXT NOT NULL,                    -- the position that starts from
  moves TEXT NOT NULL,                  -- JSON: [{"uci", "san", "fen"}], from that position: where it follows a line
                                        -- that was there before it leaves it, those moves too
  explored_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS explored_board ON explored (board);
CREATE TABLE IF NOT EXISTS looked (     -- the engine's analysis of a position of a line that is listed on a board (a
                                        -- card's own, one Claude showed, one you tried): made once, in the background
  fen TEXT PRIMARY KEY,
  eval TEXT NOT NULL,                   -- JSON: {"cp", "mate", "value", "depth", "best": {"uci", "san"}, "line": [SAN]}
  looked_at REAL NOT NULL
);
"""
# Columns added since the first release: a database made before them gets them here.
ADDED = {
    "games": {
        "engine": "TEXT NOT NULL DEFAULT 'stockfish'",
        "opp_clock_ms": "INTEGER",
        "draw_offer": "INTEGER",
        "finished_at": "REAL",
    },
    "moves": {"clock_ms": "INTEGER"},
    "cards": {
        "prev": "TEXT",
        "explanation": "TEXT",
        "state": "TEXT NOT NULL DEFAULT 'new'",
        "steps": "INTEGER NOT NULL DEFAULT 0",
        "ease": "REAL NOT NULL DEFAULT 2.5",
        "game_id": "TEXT",
        "session_id": "TEXT",
        "session_started": "INTEGER NOT NULL DEFAULT 0",
        "heuristic": "TEXT",
        "opener": "TEXT",
    },
}


class Store:
    """One connection shared by the server's threads, behind a lock: writes are small and rare."""

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self.conn.execute("DROP TABLE IF EXISTS freeplay")  # the session of a board the app no longer has
            for table, columns in ADDED.items():
                have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                for name, kind in columns.items():
                    if name not in have:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
            # Cards from before they had a state: one with an interval had been learned.
            self.conn.execute("UPDATE cards SET state = 'review' WHERE state = 'new' AND interval > 0")
            # Games that ended before that was recorded: at their last move, near enough.
            self.conn.execute(
                "UPDATE games SET finished_at = coalesce((SELECT max(played_at) FROM moves WHERE game_id = games.id), "
                "updated_at) WHERE status = 'finished' AND finished_at IS NULL"
            )

    def rescale(self, rated) -> None:
        """Once, in a database from before Stockfish's ratings were given on Allie's scale: say the strength of
        its games there (``rated``: a rating of Stockfish's own scale on Allie's), as every later game does."""
        with self.lock:
            if self.conn.execute("PRAGMA user_version").fetchone()[0] >= 1:
                return
            for r in self.conn.execute(
                "SELECT id, elo FROM games WHERE engine = 'stockfish' AND elo IS NOT NULL"
            ).fetchall():
                self.conn.execute("UPDATE games SET elo = ? WHERE id = ?", (round(rated(r["elo"])), r["id"]))
            self.conn.execute("PRAGMA user_version = 1")

    def run(self, sql: str, args: tuple = ()) -> None:
        with self.lock:
            self.conn.execute(sql, args)

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self.all(sql, args)
        return rows[0] if rows else None

    # ---------- games ----------

    def add_game(self, game: dict) -> None:
        cols = ", ".join(game)
        self.run(f"INSERT INTO games ({cols}) VALUES ({', '.join('?' * len(game))})", tuple(game.values()))

    def game(self, game_id: str) -> dict | None:
        return self.one("SELECT * FROM games WHERE id=?", (game_id,))

    def games(self, limit: int = 50) -> list[dict]:
        return self.all(
            "SELECT g.*, (SELECT count(*) FROM moves m WHERE m.game_id=g.id) AS plies FROM games g "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )

    def rated_games(self) -> list[dict]:
        """Every finished game: what a rating is reckoned from."""
        return self.all(
            "SELECT id, created_at, updated_at, finished_at, user_color, elo, status, result, time_control FROM games "
            "WHERE status = 'finished'"
        )

    def update_game(self, game_id: str, **fields) -> None:
        fields["updated_at"] = time.time()
        if "status" in fields:  # ending a game says when; a take-back that reopens it unsays it
            fields["finished_at"] = fields["updated_at"] if fields["status"] == "finished" else None
        sets = ", ".join(f"{k}=?" for k in fields)
        self.run(f"UPDATE games SET {sets} WHERE id=?", (*fields.values(), game_id))

    def delete_game(self, game_id: str) -> None:
        with self.lock:
            for table, col in (("evals", "game_id"), ("moves", "game_id"), ("games", "id"), ("explored", "board")):
                self.conn.execute(f"DELETE FROM {table} WHERE {col}=?", (game_id,))

    # ---------- moves ----------

    def moves(self, game_id: str) -> list[dict]:
        return self.all("SELECT * FROM moves WHERE game_id=? ORDER BY ply", (game_id,))

    def add_move(
        self, game_id: str, ply: int, uci: str, san: str, fen: str, source: str, clock_ms: int | None = None
    ) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO moves (game_id, ply, uci, san, fen, source, played_at, clock_ms) VALUES (?,?,?,?,?,?,?,?)",
                (game_id, ply, uci, san, fen, source, time.time(), clock_ms),
            )
            self.conn.execute("UPDATE games SET updated_at=? WHERE id=?", (time.time(), game_id))

    def truncate(self, game_id: str, plies: int) -> None:
        """Keep the first ``plies`` moves (a take-back) and the analysis of the positions that remain."""
        with self.lock:
            self.conn.execute("DELETE FROM moves WHERE game_id=? AND ply>?", (game_id, plies))
            self.conn.execute("DELETE FROM evals WHERE game_id=? AND ply>?", (game_id, plies))

    # ---------- analysis ----------

    def evals(self, game_id: str) -> dict[int, dict]:
        out = {}
        for r in self.all("SELECT * FROM evals WHERE game_id=?", (game_id,)):
            r["lines"] = json.loads(r["lines"])
            out[r["ply"]] = r
        return out

    def put_eval(self, game_id: str, ply: int, fen: str, depth: int, value: int, cp, mate, lines: list[dict]) -> None:
        self.run(
            "INSERT OR REPLACE INTO evals (game_id, ply, fen, depth, value, cp, mate, lines) VALUES (?,?,?,?,?,?,?,?)",
            (game_id, ply, fen, depth, value, cp, mate, json.dumps(lines)),
        )

    # ---------- what Claude put on the board ----------

    def show(self, session_id: str, board: dict, keep: int = 30) -> None:
        """Keep a position and its lines for the conversation they were shown in: the last ``keep`` of each
        conversation are, and none of a conversation that is gone (its game deleted, its session forgotten)."""
        with self.lock:
            self.conn.execute(
                "INSERT INTO shown (session_id, shown_at, board) VALUES (?,?,?)",
                (session_id, time.time(), json.dumps(board)),
            )
            self.conn.execute(
                "DELETE FROM shown WHERE session_id=? AND id NOT IN "
                "(SELECT id FROM shown WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                (session_id, session_id, keep),
            )
            self.conn.execute(
                "DELETE FROM shown WHERE session_id NOT IN (SELECT session_id FROM games) "
                "AND session_id NOT IN (SELECT session_id FROM cards WHERE session_id IS NOT NULL)"
            )
            # and what you tried from a line of one that is no longer kept goes with it
            self.conn.execute(
                "DELETE FROM explored WHERE tree != 'own' AND CAST(substr(tree, 2) AS INTEGER) NOT IN (SELECT id FROM shown)"
            )

    def shown(self, session_id: str) -> list[dict]:
        """What a conversation was shown, the oldest first: each with the `id` the page knows it by."""
        rows = self.all("SELECT id, board FROM shown WHERE session_id=? ORDER BY id", (session_id,))
        return [{"id": r["id"], **json.loads(r["board"])} for r in rows]

    def conversation(self, session_id) -> bool:
        """It is the Claude Code session of a game or of a card."""
        if not isinstance(session_id, str) or not session_id:
            return False
        found = self.one(
            "SELECT 1 AS there FROM games WHERE session_id=? UNION ALL SELECT 1 FROM cards WHERE session_id=?",
            (session_id, session_id),
        )
        return found is not None

    # ---------- the lines of your own ----------

    def explored(self, board: str) -> list[dict]:
        """What you tried on a board, the oldest first: each the tree it grew from, where that starts, and its
        moves from there."""
        rows = self.all("SELECT tree, fen, moves FROM explored WHERE board=? ORDER BY id", (board,))
        return [{"tree": r["tree"], "fen": r["fen"], "moves": json.loads(r["moves"])} for r in rows]

    def explore(self, board: str, tree: str, fen: str, moves: list[dict], keep: int = 200) -> None:
        """Keep a line you tried on a board. One that goes on from a line kept before takes its place, where
        that stood among the others; one that is the start of a line kept before adds nothing. The last ``keep``
        of a board are kept."""
        ucis = [m["uci"] for m in moves]
        with self.lock:
            rows = self.all("SELECT id, moves FROM explored WHERE board=? AND tree=? AND fen=?", (board, tree, fen))
            for r in rows:
                r["ucis"] = [m["uci"] for m in json.loads(r["moves"])]
            if any(r["ucis"][: len(ucis)] == ucis for r in rows):
                return
            start = next((r for r in rows if ucis[: len(r["ucis"])] == r["ucis"]), None)
            if start:
                self.conn.execute(
                    "UPDATE explored SET moves=?, explored_at=? WHERE id=?",
                    (json.dumps(moves), time.time(), start["id"]),
                )
                return
            self.conn.execute(
                "INSERT INTO explored (board, tree, fen, moves, explored_at) VALUES (?,?,?,?,?)",
                (board, tree, fen, json.dumps(moves), time.time()),
            )
            self.conn.execute(
                "DELETE FROM explored WHERE board=? AND id NOT IN (SELECT id FROM explored WHERE board=? ORDER BY id DESC LIMIT ?)",
                (board, board, keep),
            )

    def lines_kept(self) -> list[dict]:
        """Every position with lines from it that is kept for a board, Claude's and yours: each its "fen" and
        the "moves" of one line from it."""
        out = self.all("SELECT fen, moves FROM explored")
        for r in out:
            r["moves"] = json.loads(r["moves"])
        for r in self.all("SELECT board FROM shown"):
            board = json.loads(r["board"])
            out += [{"fen": board["fen"], "moves": line["moves"]} for line in board["lines"]]
        return out

    # ---------- the engine's analysis of their positions ----------

    def looked(self, fens: list[str] | None = None) -> dict[str, dict]:
        """The analysis on file of those positions (None: of every one that has any), by FEN."""
        if fens is None:
            rows = self.all("SELECT fen, eval FROM looked")
        else:
            rows = []
            for i in range(0, len(fens), 500):
                some = fens[i : i + 500]
                rows += self.all(
                    f"SELECT fen, eval FROM looked WHERE fen IN ({', '.join('?' * len(some))})", tuple(some)
                )
        return {r["fen"]: json.loads(r["eval"]) for r in rows}

    def look(self, fen: str, found: dict) -> None:
        self.run(
            "INSERT OR REPLACE INTO looked (fen, eval, looked_at) VALUES (?,?,?)", (fen, json.dumps(found), time.time())
        )

    def unexplore(self, board: str) -> None:
        """The board is gone (a card that was deleted with its game): so is what was tried on it."""
        self.run("DELETE FROM explored WHERE board=?", (board,))
