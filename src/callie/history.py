"""The games on file and the cards made of them, as text in the checkout (`history/`), where git can keep them.

A file for each game (`games/<id>.jsonl`: its row of the database, then its moves and the analysis of its
positions, a row to a line) and one for the cards (`cards.jsonl`). `export` writes them as the database has them
now, `load` puts into a database the games and cards it does not hold, `push` commits and pushes them.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .store import Store

GAME = (("games", "id"), ("moves", "game_id"), ("evals", "game_id"))  # a game's tables, and the column that names it
ORDER = {"games": "id", "moves": "ply", "evals": "ply", "cards": "position, uci"}


class HistoryError(Exception):
    pass


def _rows(store: Store, table: str, where: str = "", args: tuple = ()) -> list[str]:
    out = []
    for row in store.all(f"SELECT * FROM {table} {where} ORDER BY {ORDER[table]}", args):
        if table == "cards":
            # of this database only: its number (a card is its position and move), and its conversation with Claude
            for mine in ("id", "session_id", "session_started"):
                del row[mine]
        if "lines" in row:
            row["lines"] = json.loads(row["lines"])  # JSON in the database: written as what it holds
        out.append(json.dumps([table, row], ensure_ascii=False))
    return out


def _write(path: Path, lines: list[str]) -> None:
    text = "".join(line + "\n" for line in lines)
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def export(store: Store, root: Path) -> int:
    """Write every game and the cards under ``root``, and remove the files of games that are no longer on
    file. Returns how many games there are."""
    (root / "games").mkdir(parents=True, exist_ok=True)
    kept = set()
    for g in store.all("SELECT id FROM games"):
        with store.lock:  # a game as it stands at one moment
            lines = [line for table, key in GAME for line in _rows(store, table, f"WHERE {key}=?", (g["id"],))]
        kept.add(f"{g['id']}.jsonl")
        _write(root / "games" / f"{g['id']}.jsonl", lines)
    for f in (root / "games").glob("*.jsonl"):
        if f.name not in kept:
            f.unlink()
    _write(root / "cards.jsonl", _rows(store, "cards"))
    return len(kept)


def load(store: Store, root: Path) -> dict[str, int]:
    """Put the games and cards under ``root`` that the database does not hold into it (a game it holds is left
    as it is, whatever its file says; so is a card). Returns how many of each were added."""
    added = {"games": 0, "cards": 0}
    files = [*sorted((root / "games").glob("*.jsonl")), root / "cards.jsonl"]
    with store.lock:
        columns = {t: {r["name"] for r in store.all(f"PRAGMA table_info({t})")} for t in ORDER}
        store.conn.execute("BEGIN")
        try:
            for f in files:
                for line in f.read_text().splitlines() if f.exists() else []:
                    table, row = json.loads(line)
                    if table not in columns or not set(row) <= columns[table]:
                        raise HistoryError(f"{f}: not a row of this database: {line[:80]}")
                    if table == "games" and store.one("SELECT id FROM games WHERE id=?", (row["id"],)):
                        break  # on file already: its moves stay as they are
                    values = [json.dumps(v) if isinstance(v, (list, dict)) else v for v in row.values()]
                    done = store.conn.execute(
                        f"INSERT OR IGNORE INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", values
                    )
                    if table in added:
                        added[table] += done.rowcount
            store.conn.execute("COMMIT")
        except BaseException:
            store.conn.execute("ROLLBACK")
            raise
    return added


def push(checkout: Path, remote: str = "origin", branch: str = "main") -> str:
    """Commit what `export` wrote under the checkout's `history/`, and nothing else, and push it. Says what
    it did. It pushes only when everything else on the branch is on the remote already: other work is never
    sent along with the games."""

    def git(*args: str, ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
        done = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True)
        if done.returncode not in ok:
            raise HistoryError(f"git {' '.join(args)}: {(done.stderr or done.stdout).strip()}")
        return done

    on = git("symbolic-ref", "--short", "HEAD").stdout.strip()
    if on != branch:
        raise HistoryError(f"the checkout is on {on}, not {branch}: nothing committed")
    git("add", "-A", "--", "history")
    said = "nothing new"
    if git("diff", "--cached", "--quiet", "--", "history", ok=(0, 1)).returncode:
        git("commit", "-q", "-m", f"Game history, {time.strftime('%Y-%m-%d')}", "--", "history")
        said = "committed"
    git("fetch", "-q", remote, branch)
    theirs = f"{remote}/{branch}"
    if not git("rev-list", f"{theirs}..HEAD").stdout.strip():
        return f"{said}; nothing to push"
    if git("diff", "--quiet", theirs, "HEAD", "--", ".", ":(exclude)history", ok=(0, 1)).returncode:
        raise HistoryError(
            f"{said}, not pushed: {branch} and {theirs} differ in more than history/ (push or pull that first)"
        )
    git("push", "-q", remote, branch)
    return f"{said}; pushed to {theirs}"
