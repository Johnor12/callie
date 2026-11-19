"""The games and cards as files for git: written, read back into another database, committed and pushed."""

import shutil
import subprocess
import time
import unittest

from callie import history
from callie.cards import Cards
from callie.store import Store
from tests import function_tests, scratch_config
from tests.test_cards import FEN, LINES


def filled(home):
    """A database with one game of two moves, analysed, and one card."""
    store = Store(home / "games.db")
    now = time.time()
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    store.add_game({"id": "ab12", "created_at": now, "updated_at": now, "user_color": "w", "elo": 1200, "start_fen": start, "session_id": "s"})
    store.add_move("ab12", 1, "e2e4", "e4", "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1", "user")
    store.add_move("ab12", 2, "e7e5", "e5", "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2", "engine")
    store.put_eval("ab12", 0, start, 20, 30, 30, None, LINES)
    Cards(store).note(FEN, "g8f6", "Nf6", 990, LINES, now)
    return store


def everything(store):
    cards = [{k: v for k, v in r.items() if k != "id"} for r in store.all("SELECT * FROM cards")]
    return [store.all(f"SELECT * FROM {t} ORDER BY 1, 2") for t in ("games", "moves", "evals")] + [cards]


def test_written_and_read_back():
    home = scratch_config().home
    store, root = filled(home), home / "history"
    assert history.export(store, root) == 1
    game = (root / "games" / "ab12.jsonl").read_text().splitlines()
    assert [line.split(",")[0] for line in game] == ['["games"', '["moves"', '["moves"', '["evals"']
    assert '"lines": [{"moves": ["g7g6", "h5f3"]' in (root / "cards.jsonl").read_text()  # as what it is, not as a string
    stamp = (root / "cards.jsonl").stat().st_mtime_ns
    history.export(store, root)
    assert (root / "cards.jsonl").stat().st_mtime_ns == stamp  # nothing changed: nothing written
    # into an empty database: all of it; a second time: nothing
    other = Store(home / "other.db")
    assert history.load(other, root) == {"games": 1, "cards": 1} and everything(other) == everything(store)
    assert history.load(other, root) == {"games": 0, "cards": 0} and everything(other) == everything(store)
    # a game the database holds is left as it is, whatever the file says
    other.truncate("ab12", 1)
    assert history.load(other, root) == {"games": 0, "cards": 0} and len(other.moves("ab12")) == 1
    (root / "games" / "bad.jsonl").write_text('["games", {"id": "zz", "nonsense": 1}]\n')
    try:
        history.load(other, root)
        raise AssertionError("a row with an unknown column was accepted")
    except history.HistoryError:
        pass
    # a game that was deleted loses its file
    store.delete_game("ab12")
    assert history.export(store, root) == 0 and list((root / "games").iterdir()) == []


def test_committed_and_pushed():
    if not shutil.which("git"):
        raise unittest.SkipTest("no git")
    home = scratch_config().home
    work, bare = home / "checkout", home / "remote.git"

    def git(*args, at=work):
        return subprocess.run(["git", "-C", str(at), *args], capture_output=True, text=True, check=True).stdout.strip()

    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        git("config", k, v)
    (work / "code.py").write_text("1\n")
    git("add", "."), git("commit", "-q", "-m", "first"), git("remote", "add", "origin", str(bare)), git("push", "-q", "origin", "main")
    store = filled(home)
    history.export(store, work / "history")
    (work / "code.py").write_text("2\n")  # work in progress, not committed: it stays out of it
    assert history.push(work) == "committed; pushed to origin/main"
    assert git("rev-parse", "main", at=bare) == git("rev-parse", "HEAD") and git("status", "--short") == "M code.py"
    assert git("show", "--stat", "--format=%s", "HEAD").splitlines()[0].startswith("Game history, 20")
    assert set(git("show", "--name-only", "--format=", "HEAD").splitlines()) == {"history/cards.jsonl", "history/games/ab12.jsonl"}
    assert history.push(work) == "nothing new; nothing to push"
    # a commit of something else that is not on the remote yet is not sent along
    git("commit", "-q", "-am", "second")
    store.delete_game("ab12")
    history.export(store, work / "history")
    try:
        history.push(work)
        raise AssertionError("other work was pushed with the games")
    except history.HistoryError as e:
        assert "committed, not pushed" in str(e)
    assert git("log", "--format=%s", "main", at=bare) == "Game history, " + time.strftime("%Y-%m-%d") + "\nfirst"
    git("push", "-q", "origin", "main")
    assert history.push(work) == "nothing new; nothing to push" and not (work / "history" / "games" / "ab12.jsonl").exists()
    git("checkout", "-q", "-b", "side")
    try:
        history.push(work)
        raise AssertionError("committed on another branch")
    except history.HistoryError:
        pass


load_tests = function_tests(globals())
