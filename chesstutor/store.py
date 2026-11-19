"""The database: games, their moves, the engine's analysis of each position, and the cards made of your errors.
SQLite, one file."""

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
CREATE TABLE IF NOT EXISTS cards (      -- an error of yours to review: one for each position and the move played in it
  id INTEGER PRIMARY KEY,
  position TEXT NOT NULL,               -- what makes it the same position in any game: the FEN less its move counters
  uci TEXT NOT NULL,                    -- the move you played
  san TEXT NOT NULL,
  fen TEXT NOT NULL,                    -- the position before it, in the game it was last played in
  cpl INTEGER NOT NULL,                 -- what it lost there, in centipawns: which deck the card is in
  lines TEXT NOT NULL,                  -- JSON: the engine's lines in the position, as in evals
  erred_at REAL NOT NULL,               -- when you last played it
  state TEXT NOT NULL DEFAULT 'new',    -- where it is in its schedule (cards.py): 'new', 'learning', 'review', 'relearning'
  steps INTEGER NOT NULL DEFAULT 0,     -- while it is being learned: the steps it has yet to pass
  interval INTEGER NOT NULL DEFAULT 0,  -- once learned: the days between two reviews of it
  ease REAL NOT NULL DEFAULT 2.5,       -- what a good answer multiplies the interval by
  due REAL NOT NULL,                    -- from when it is to be reviewed
  reviewed_at REAL,                     -- when it was last answered
  explanation TEXT,                     -- why the move was bad, as Claude wrote it; NULL: not written yet
  UNIQUE (position, uci)
);
"""
# Columns added since the first release: a database made before them gets them here.
ADDED = {
    "games": {"engine": "TEXT NOT NULL DEFAULT 'stockfish'", "opp_clock_ms": "INTEGER", "draw_offer": "INTEGER",
              "finished_at": "REAL"},
    "moves": {"clock_ms": "INTEGER"},
    "cards": {"explanation": "TEXT", "state": "TEXT NOT NULL DEFAULT 'new'", "steps": "INTEGER NOT NULL DEFAULT 0",
              "ease": "REAL NOT NULL DEFAULT 2.5"},
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
            for r in self.conn.execute("SELECT id, elo FROM games WHERE engine = 'stockfish' AND elo IS NOT NULL").fetchall():
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
            for table, col in (("evals", "game_id"), ("moves", "game_id"), ("games", "id")):
                self.conn.execute(f"DELETE FROM {table} WHERE {col}=?", (game_id,))

    # ---------- moves ----------

    def moves(self, game_id: str) -> list[dict]:
        return self.all("SELECT * FROM moves WHERE game_id=? ORDER BY ply", (game_id,))

    def add_move(self, game_id: str, ply: int, uci: str, san: str, fen: str, source: str, clock_ms: int | None = None) -> None:
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
