"""`chess-tutor`: run the server, fetch the engine, install it as a service."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from .config import Config, ConfigError, load_config

RELEASE = "https://api.github.com/repos/official-stockfish/Stockfish/releases/latest"
UNIT = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "systemd" / "user" / "chess-tutor.service"
_CARRIED = ("CHESSTUTOR_CONFIG", "CHESSTUTOR_HOME", "CHESSTUTOR_CLAUDE_BIN", "CHESSTUTOR_PYTHON")


# ---------- the server ----------


def _running(cfg: Config) -> dict | None:
    try:
        info = json.loads((cfg.home / "server.pid").read_text())
        os.kill(info["pid"], 0)
        return info
    except (OSError, ValueError, KeyError):
        return None


def _answers(url: str) -> bool:
    try:
        return urllib.request.urlopen(f"{url}/api/health", timeout=2).status == 200
    except OSError:
        return False


def cmd_serve(cfg: Config, args) -> int:
    from . import server

    server.serve(cfg, args.host, args.port)
    return 0


def cmd_start(cfg: Config, args) -> int:
    """Start the server detached from this terminal (its output goes to <home>/server.log)."""
    cmd_stop(cfg, args, quiet=True)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent))
    with open(cfg.home / "server.log", "ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "chesstutor.cli", "serve"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, start_new_session=True
        )  # fmt: skip
    deadline = time.time() + 15
    while time.time() < deadline:
        info = _running(cfg)
        if info and _answers(info["url"]):
            print(f"chess-tutor: {info['url']}  (pid {info['pid']}, log {cfg.home / 'server.log'})")
            return 0
        time.sleep(0.1)
    print(f"chess-tutor: the server did not start; see {cfg.home / 'server.log'}", file=sys.stderr)
    return 1


def cmd_stop(cfg: Config, args, quiet: bool = False) -> int:
    info = _running(cfg)
    if not info:
        if not quiet:
            print("chess-tutor: not running")
        return 0
    os.kill(info["pid"], signal.SIGTERM)
    deadline = time.time() + 10
    while _running(cfg) and time.time() < deadline:
        time.sleep(0.05)
    if _running(cfg):
        os.kill(info["pid"], signal.SIGKILL)
    if not quiet:
        print(f"chess-tutor: stopped (pid {info['pid']})")
    return 0


def cmd_status(cfg: Config, args) -> int:
    info = _running(cfg)
    print(f"server: {info['url']}, pid {info['pid']}" if info else "server: not running (`chess-tutor start`)")
    print(f"engine: {cfg.engine}" + ("" if cfg.engine.exists() else "  MISSING: run `chess-tutor engine install`"))
    claude = shutil.which(cfg.claude_bin)
    print(f"claude: {claude or cfg.claude_bin + '  NOT ON THE PATH'}, model {cfg.claude_model}, "
          f"account {cfg.claude_config_dir or os.environ.get('CLAUDE_CONFIG_DIR') or '~/.claude'}")  # fmt: skip
    print(f"service: {'installed (' + str(UNIT) + ')' if UNIT.exists() else 'not installed (`chess-tutor service install`)'}")
    print(f"config: {cfg.source or 'defaults (no config file)'}; state: {cfg.home}")
    for line in _tailnet(cfg):
        print(line)
    return 0 if info else 1


def _tailnet(cfg: Config) -> list[str]:
    """What stands between the server and another machine of the tailnet, checked one by one."""

    def run(*cmd: str) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    try:
        me = json.loads(run("tailscale", "status", "--json"))["Self"]["DNSName"].rstrip(".").lower()
    except (ValueError, KeyError):
        return ["tailscale: not running, or not installed"]
    out = [f"tailscale: this machine is {me}"]
    if me not in cfg.hosts:
        out.append(f'  not let in: add hosts = ["{me}"] under [server] in the config, then restart the server')
    try:
        web = json.loads(run("tailscale", "serve", "status", "--json") or "{}").get("Web") or {}
    except ValueError:
        web = {}
    served = [
        name for name, w in web.items() for h in (w.get("Handlers") or {}).values() if str(h.get("Proxy", "")).endswith(f":{cfg.port}")
    ]  # fmt: skip
    if served:
        out += [f"  served at https://{name.removesuffix(':443')}" for name in served]
    else:
        out.append(f"  not served: run `sudo tailscale serve --bg --https=8443 http://127.0.0.1:{cfg.port}`")
    return out


# ---------- the engine ----------


def cmd_engine(cfg: Config, args) -> int:
    """Download the latest Stockfish release for this machine into the state directory."""
    system = {"Linux": "linux", "Darwin": "macos"}.get(platform.system())
    arch = {"x86_64": "x86-64", "amd64": "x86-64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if not system or not arch:
        print(f"no Stockfish build known for {platform.system()} {platform.machine()}: put one at {cfg.engine}", file=sys.stderr)
        return 1
    release = json.load(urllib.request.urlopen(RELEASE, timeout=30))
    want = "stockfish-macos-universal" if system == "macos" else f"stockfish-{system}-{arch}"
    asset = next((a for a in release["assets"] if a["name"].startswith(want) and a["name"].endswith(".tar.gz")), None)
    if asset is None:
        print(f"release {release['tag_name']} has no {want}*.tar.gz: put a Stockfish binary at {cfg.engine}", file=sys.stderr)
        return 1
    print(f"downloading {asset['name']} ({asset['size'] >> 20} MB) of {release['name']} ...", flush=True)
    with tempfile.TemporaryDirectory(dir=cfg.home) as tmp:
        archive = Path(tmp) / "engine.tar.gz"
        with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as r, open(archive, "wb") as f:
            shutil.copyfileobj(r, f)
        unpacked = Path(tmp) / "unpacked"
        with tarfile.open(archive) as tar:
            tar.extractall(unpacked, filter="data")
        binaries = [p for p in unpacked.rglob("stockfish*") if p.is_file() and p.suffix == "" or p.name.startswith(want) and p.is_file()]
        binary = max(binaries, key=lambda p: p.stat().st_size, default=None)
        if binary is None:
            print("the archive holds no stockfish binary", file=sys.stderr)
            return 1
        cfg.engine.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(binary, cfg.engine)
        cfg.engine.chmod(0o755)
    from .engine import Engine

    print(f"installed {Engine(str(cfg.engine)).version()} at {cfg.engine}")
    return 0


# ---------- the service ----------


def cmd_service(cfg: Config, args) -> int:
    def systemctl(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run(["systemctl", "--user", *a], capture_output=True, text=True)

    if args.action == "uninstall":
        systemctl("disable", "--now", UNIT.name)
        UNIT.unlink(missing_ok=True)
        systemctl("daemon-reload")
        print(f"removed {UNIT}")
        return 0
    # Claude Code is started by the server and inherits its environment, so the unit runs it through a login
    # shell: the same PATH and variables as when it is started by hand from a new terminal.
    root = Path(__file__).resolve().parent.parent
    run = shlex.join([sys.executable, "-m", "chesstutor.cli", "serve"])
    shell = os.environ.get("SHELL") or "/bin/bash"
    env = {"PYTHONPATH": str(root), **{k: os.environ[k] for k in _CARRIED if os.environ.get(k)}}
    UNIT.parent.mkdir(parents=True, exist_ok=True)
    UNIT.write_text(
        "\n".join(
            [
                "# Written by `chess-tutor service install`; `chess-tutor service uninstall` removes it.",
                "[Unit]",
                "Description=Chess Tutor: play against a calibrated engine with Stockfish and Claude analysis",
                "StartLimitIntervalSec=0",
                "",
                "[Service]",
                *(f"Environment={k}={v}" for k, v in env.items()),
                f"ExecStart={shell} -lic 'exec {run}'",
                "Restart=always",
                "RestartSec=2",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        )
    )
    cmd_stop(cfg, args, quiet=True)  # one started by hand holds the port
    for a in (("daemon-reload",), ("enable", UNIT.name), ("restart", UNIT.name)):
        r = systemctl(*a)
        if r.returncode:
            print(f"systemctl --user {' '.join(a)}: {r.stderr.strip()}", file=sys.stderr)
            return 1
    # Lingering: the user's services start at boot, before any login, and are not stopped at logout.
    if subprocess.run(["loginctl", "enable-linger"], capture_output=True).returncode:
        print(f"could not turn lingering on; run `sudo loginctl enable-linger {os.environ.get('USER', '')}`")
    deadline = time.time() + 20
    while time.time() < deadline and not _answers(cfg.url):
        time.sleep(0.2)
    if not _answers(cfg.url):
        print(f"{UNIT.name} did not start: journalctl --user -u {UNIT.name}", file=sys.stderr)
        return 1
    print(f"installed {UNIT}: started at boot and restarted if it dies. Serving {cfg.url}")
    print(f"log: journalctl --user -u {UNIT.name}; stop: systemctl --user stop {UNIT.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="chess-tutor", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the server in this terminal")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_serve)
    sub.add_parser("start", help="start (or restart) the server in the background").set_defaults(fn=cmd_start)
    sub.add_parser("stop", help="stop the background server").set_defaults(fn=cmd_stop)
    sub.add_parser("status", help="what is running, and what is missing").set_defaults(fn=cmd_status)
    e = sub.add_parser("engine", help="the chess engine")
    e.add_argument("action", choices=["install"])
    e.set_defaults(fn=cmd_engine)
    v = sub.add_parser("service", help="run the server as a systemd user service")
    v.add_argument("action", choices=["install", "uninstall"])
    v.set_defaults(fn=cmd_service)
    args = ap.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as err:
        print(f"chess-tutor: {err}", file=sys.stderr)
        return 2
    return args.fn(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
