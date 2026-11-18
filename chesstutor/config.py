"""Settings: a TOML file (~/.config/chess-tutor/config.toml) over defaults, and where state is kept.

CHESSTUTOR_CONFIG and CHESSTUTOR_HOME point somewhere else (tests, a scratch server).
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HOST_RE = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    home: Path
    host: str = "127.0.0.1"
    port: int = 8750
    hosts: list[str] = field(default_factory=list)  # other names it is reached by, through a proxy on this machine
    logins: list[str] = field(default_factory=list)  # Tailscale logins let in under those names (empty: the tailnet)
    # the engine
    engine_path: Path | None = None
    threads: int = 0  # for analysis; 0: half the machine's logical processors
    hash_mb: int = 512
    review_ms: int = 1500  # how long each position of a game is analysed
    opponent_ms: int = 1000  # how long the opponent thinks
    # Claude Code
    claude_bin: str = "claude"
    claude_model: str = "claude-fable-5-2"
    claude_effort: str = ""  # empty: Claude Code's own default
    claude_config_dir: Path | None = None  # CLAUDE_CONFIG_DIR of the account the tutor runs in (None: the default)
    claude_args: list[str] = field(default_factory=list)
    idle_terminal_s: float = 900.0  # a tutor terminal nobody watches is ended after this long; it resumes when reopened
    source: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.home / "games.db"

    @property
    def engine(self) -> Path:
        return self.engine_path or self.home / "engine" / "stockfish"

    @property
    def workspace(self) -> Path:
        """The directory every tutor session runs in (one trust prompt, one place for its transcripts)."""
        return self.home / "tutor"

    @property
    def analysis_threads(self) -> int:
        return self.threads or max(1, (os.cpu_count() or 2) // 2)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _expand(p: str) -> Path:
    return Path(os.path.realpath(os.path.expanduser(p)))


def default_config_path() -> Path:
    return Path(os.environ.get("CHESSTUTOR_CONFIG", "~/.config/chess-tutor/config.toml")).expanduser()


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    data: dict = tomllib.loads(path.read_text()) if path.exists() else {}
    server, engine, claude = data.get("server", {}), data.get("engine", {}), data.get("claude", {})
    cfg = Config(
        home=_expand(os.environ.get("CHESSTUTOR_HOME", data.get("home", "~/.local/share/chess-tutor"))),
        host=str(server.get("host", "127.0.0.1")),
        port=int(server.get("port", 8750)),
        hosts=[str(h).lower() for h in server.get("hosts", [])],
        logins=[str(u).lower() for u in server.get("tailscale_logins", [])],
        engine_path=_expand(engine["path"]) if engine.get("path") else None,
        threads=int(engine.get("threads", 0)),
        hash_mb=int(engine.get("hash_mb", 512)),
        review_ms=int(engine.get("review_ms", 1500)),
        opponent_ms=int(engine.get("opponent_ms", 1000)),
        claude_bin=os.environ.get("CHESSTUTOR_CLAUDE_BIN", str(claude.get("bin", "claude"))),
        claude_model=str(claude.get("model", "claude-fable-5-2")),
        claude_effort=str(claude.get("effort", "")),
        claude_config_dir=_expand(claude["config_dir"]) if claude.get("config_dir") else None,
        claude_args=[str(a) for a in claude.get("extra_args", [])],
        idle_terminal_s=float(claude.get("idle_terminal_s", 900)),
        source=path if path.exists() else None,
    )
    for h in cfg.hosts:
        if not HOST_RE.fullmatch(h):
            raise ConfigError(f"[server] hosts in {path}: {h!r} is not a host name (no scheme, port or path)")
    if not 50 <= cfg.review_ms <= 60000 or not 50 <= cfg.opponent_ms <= 60000:
        raise ConfigError(f"[engine] in {path}: review_ms and opponent_ms are milliseconds, 50 to 60000")
    cfg.home.mkdir(parents=True, exist_ok=True)
    cfg.workspace.mkdir(parents=True, exist_ok=True)
    return cfg
