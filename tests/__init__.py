"""Tests are plain functions with asserts; `python -m unittest discover -s tests -t .` runs them, nothing installed.

Those that need the Stockfish binary (`callie engine install`) are skipped without it. None starts the real
Claude Code: sessions are tried against `fake_claude.py`.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))  # the package, as `./callie` runs it


def function_tests(namespace: dict):
    """A module's `load_tests`: each of its test_ functions as a unittest case."""

    def load_tests(loader, tests, pattern):
        suite = unittest.TestSuite()
        for name, fn in sorted(namespace.items()):
            if name.startswith("test_") and callable(fn):
                suite.addTest(unittest.FunctionTestCase(fn, description=f"{namespace['__name__']}.{name}"))
        return suite

    return load_tests


def scratch_config(**changes):
    """A configuration whose state lives in a new temporary directory, with a fast engine."""
    from callie.config import load_config

    real = load_config()  # where the engine is on this machine
    home = Path(tempfile.mkdtemp(prefix="callie-test-"))
    old = {k: os.environ.get(k) for k in ("CALLIE_HOME", "CALLIE_CONFIG")}
    os.environ["CALLIE_HOME"], os.environ["CALLIE_CONFIG"] = str(home), str(home / "none.toml")
    try:
        cfg = load_config()
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    cfg.engine_path = real.engine
    cfg.threads, cfg.hash_mb, cfg.review_ms, cfg.opponent_ms, cfg.port = 1, 16, 60, 50, 0
    cfg.claude_bin = str(Path(__file__).with_name("fake_claude.py"))
    cfg.claude_config_dir = home / "claude"
    for k, v in changes.items():
        setattr(cfg, k, v)
    return cfg


def need_engine(cfg) -> None:
    if not cfg.engine.exists():
        raise unittest.SkipTest(f"no engine at {cfg.engine}: run `callie engine install`")
