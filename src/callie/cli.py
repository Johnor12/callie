"""`callie`: run the server, fetch the engines, install it as a service, keep the games in git."""

from __future__ import annotations

import argparse
import hashlib
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

from . import allie
from .config import Config, ConfigError, load_config

RELEASE = "https://api.github.com/repos/official-stockfish/Stockfish/releases/latest"
RUSTUP = "https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init"
RUST_MIN = (1, 89)  # what Allie's engine is written for
TORCH = ("torch==2.10.*", "https://download.pytorch.org/whl/cpu")  # the version Allie was trained with, without CUDA
UNIT = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "systemd" / "user" / "callie.service"
# What the service is given of the environment `service install` is run in: where `claude` and the rest are found
# (Claude Code is started by the server and inherits its environment), and this program's own settings.
_CARRIED = ("PATH", "CALLIE_CONFIG", "CALLIE_HOME", "CALLIE_CLAUDE_BIN", "CALLIE_PYTHON")


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
            [sys.executable, "-m", "callie.cli", "serve"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, start_new_session=True
        )  # fmt: skip
    deadline = time.time() + 15
    while time.time() < deadline:
        info = _running(cfg)
        if info and _answers(info["url"]):
            print(f"callie: {info['url']}  (pid {info['pid']}, log {cfg.home / 'server.log'})")
            return 0
        time.sleep(0.1)
    print(f"callie: the server did not start; see {cfg.home / 'server.log'}", file=sys.stderr)
    return 1


def cmd_stop(cfg: Config, args, quiet: bool = False) -> int:
    info = _running(cfg)
    if not info:
        if not quiet:
            print("callie: not running")
        return 0
    os.kill(info["pid"], signal.SIGTERM)
    deadline = time.time() + 10
    while _running(cfg) and time.time() < deadline:
        time.sleep(0.05)
    if _running(cfg):
        os.kill(info["pid"], signal.SIGKILL)
    if not quiet:
        print(f"callie: stopped (pid {info['pid']})")
    return 0


def cmd_status(cfg: Config, args) -> int:
    info = _running(cfg)
    print(f"server: {info['url']}, pid {info['pid']}" if info else "server: not running (`callie start`)")
    print(f"engine: {cfg.engine}" + ("" if cfg.engine.exists() else "  MISSING: run `callie engine install`"))
    why = allie.missing(cfg)
    print(f"allie: {allie.weights(cfg)}, run by {cfg.allie_python}" if why is None else f"allie: not installed (`callie allie install`): {why}")
    claude = shutil.which(cfg.claude_bin)
    print(f"claude: {claude or cfg.claude_bin + '  NOT ON THE PATH'}, model {cfg.claude_model}, "
          f"account {cfg.claude_config_dir or os.environ.get('CLAUDE_CONFIG_DIR') or '~/.claude'}")  # fmt: skip
    print(f"service: {'installed (' + str(UNIT) + ')' if UNIT.exists() else 'not installed (`callie service install`)'}")
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


# ---------- Allie ----------


def _pinned(python: Path) -> bool:
    """Whether Allie's environment already holds its code and engine at the commit this version plays."""
    check = (
        "import json, sys, torch, allie_fast, allie.lichess.engine\n"
        "from importlib.metadata import distribution as d\n"
        "at = lambda n: json.loads(d(n).read_text('direct_url.json'))['vcs_info']['commit_id']\n"
        "sys.exit(not (at('allie') == at('allie-fast') == sys.argv[1]))"
    )
    return python.is_file() and subprocess.run([str(python), "-c", check, allie.COMMIT], capture_output=True).returncode == 0


def _cargo(home: Path, env: dict) -> Path | None:
    """Make a Rust toolchain of RUST_MIN or newer the first on ``env``'s PATH: the one already on it, else one
    downloaded into ``home`` (returned, so it can be removed once the engine is built) and nowhere else."""

    def version() -> tuple:
        try:
            out = subprocess.run(["cargo", "--version"], capture_output=True, text=True, env=env).stdout.split()
            return tuple(int(x) for x in out[1].split(".")[:2])
        except (OSError, IndexError, ValueError):
            return (0, 0)

    if version() >= RUST_MIN:
        return None
    rust = home / "rust"
    env.update(RUSTUP_HOME=str(rust / "rustup"), CARGO_HOME=str(rust / "cargo"))
    env["PATH"] = f"{rust / 'cargo' / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    if version() < RUST_MIN:
        print("downloading a Rust toolchain to build Allie's engine with (kept out of your own setup) ...", flush=True)
        rust.mkdir(parents=True, exist_ok=True)
        init = rust / "rustup-init"
        with urllib.request.urlopen(RUSTUP, timeout=60) as r, open(init, "wb") as f:
            shutil.copyfileobj(r, f)
        want = urllib.request.urlopen(RUSTUP + ".sha256", timeout=60).read().split()[0].decode()
        if hashlib.sha256(init.read_bytes()).hexdigest() != want:
            raise RuntimeError("the Rust installer did not download whole: run this again")
        init.chmod(0o755)
        subprocess.run([str(init), "-y", "-q", "--no-modify-path", "--profile", "minimal", "--default-toolchain", "stable"], env=env, check=True)  # fmt: skip
    return rust


def cmd_allie(cfg: Config, args) -> int:
    """Everything Allie needs, under <home>/allie: a Python environment with its code and its engine (built here,
    in Rust), and its weights."""
    if platform.system() != "Linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        print("Allie's engine, which its calibrated play needs, runs on Linux x86-64 only", file=sys.stderr)
        return 1
    home, python, env = cfg.allie_home, cfg.allie_python, dict(os.environ)
    home.mkdir(parents=True, exist_ok=True)
    try:
        if cfg.allie_python_path:
            print(f"[allie] python is set: using {python} as it is")
        elif _pinned(python):
            print(f"Allie's code and engine are already at {allie.COMMIT[:10]} in {python.parent.parent}")
        else:
            if not shutil.which("uv"):
                print("needs uv (https://docs.astral.sh/uv/) to make Allie's Python environment", file=sys.stderr)
                return 1
            # --no-config: packages come from PyPI, PyTorch and GitHub, whatever indexes this machine's uv is set up with
            uv = ["uv", "--no-config"]
            if not python.is_file():
                subprocess.run([*uv, "venv", "--python", "3.12", str(home / "venv")], env=env, check=True)
            private = _cargo(home, env)
            pip = [*uv, "pip", "install", "--python", str(python)]
            subprocess.run([*pip, TORCH[0], "--default-index", TORCH[1]], env=env, check=True)
            at = f"git+{allie.SOURCE}@{allie.COMMIT}"
            print("building Allie's engine (a few minutes) ...", flush=True)
            subprocess.run(
                [*pip, "--no-sources", "--default-index", "https://pypi.org/simple", f"allie[bot] @ {at}",
                 f"allie-fast @ {at}#subdirectory=rust/allie-fast"], env=env, check=True,
            )  # fmt: skip
            if not _pinned(python):
                print(f"Allie's code did not install into {python.parent.parent}", file=sys.stderr)
                return 1
            if private:
                shutil.rmtree(private, ignore_errors=True)  # it was only needed for the build
        if allie.weights(cfg) is None:
            if cfg.allie_model:
                print(f"[allie] model is set, but {cfg.allie_model} holds no config.json and model.safetensors", file=sys.stderr)
                return 1
            print(f"downloading {allie.REPO} from Hugging Face (18 GB) ...", flush=True)
            fetch = (
                "import sys; from huggingface_hub import snapshot_download\n"
                "snapshot_download(sys.argv[1], allow_patterns=['config.json', 'model.safetensors'])"
            )
            hub = dict(env, HF_HUB_CACHE=str(home / "hub"), HF_HUB_DISABLE_TELEMETRY="1")
            subprocess.run([str(python), "-c", fetch, allie.REPO], env=hub, check=True)
    except (subprocess.CalledProcessError, OSError, RuntimeError) as e:
        print(f"callie: installing Allie stopped: {e}", file=sys.stderr)
        return 1
    print("loading it once, to see that it runs ...", flush=True)
    engine = allie.Allie(cfg)
    try:
        info = engine.load()
    except allie.AllieError as e:
        print(f"callie: {e}", file=sys.stderr)
        return 1
    finally:
        engine.close()
    print(f"installed {allie.NAME} ({info['name']}, {info['threads']} threads, loads in {info['load_s']:.0f} s) in {home}")
    print("it is offered as an opponent from the next time the page is loaded")
    return 0


# ---------- the games, in git ----------


def cmd_history(cfg: Config, args) -> int:
    """`export`: the games and cards as text under the checkout's history/; `push`: that, committed and
    pushed (what cron runs each night); `import`: the games and cards there that the database does not hold."""
    from . import history
    from .store import Store

    checkout = Path(__file__).resolve().parents[2]
    store = Store(cfg.db_path)
    try:
        if args.action == "import":
            added = history.load(store, checkout / "history")
            print(f"callie: added {added['games']} games and {added['cards']} cards from {checkout / 'history'}")
            return 0
        games = history.export(store, checkout / "history")
        said = f"{games} games in {checkout / 'history'}"
        if args.action == "push":
            said += f": {history.push(checkout)}"
        print(f"callie: {time.strftime('%Y-%m-%d %H:%M')} {said}")
        return 0
    except history.HistoryError as e:
        print(f"callie: {time.strftime('%Y-%m-%d %H:%M')} {e}", file=sys.stderr)
        return 1


# ---------- the service ----------


def unit_text(argv: list[str], env: dict[str, str]) -> str:
    """The systemd unit that runs ``argv`` with ``env``. No shell starts it, so nothing of a shell's profile
    runs or reaches it: beyond ``env`` it has what systemd gives a user's services (HOME, USER, LANG)."""
    word = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'  # noqa: E731
    return "\n".join(
        [
            "# Written by `callie service install`; `callie service uninstall` removes it.",
            "[Unit]",
            "Description=Callie: play against a calibrated engine with Stockfish and Claude analysis",
            "StartLimitIntervalSec=0",
            "",
            "[Service]",
            *(f"Environment={word(f'{k}={v}')}" for k, v in env.items()),
            "ExecStart=" + " ".join(word(a).replace("$", "$$") for a in argv),
            "Restart=always",
            "RestartSec=2",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def cmd_service(cfg: Config, args) -> int:
    def systemctl(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run(["systemctl", "--user", *a], capture_output=True, text=True)

    if args.action == "uninstall":
        systemctl("disable", "--now", UNIT.name)
        UNIT.unlink(missing_ok=True)
        systemctl("daemon-reload")
        print(f"removed {UNIT}")
        return 0
    root = Path(__file__).resolve().parent.parent
    UNIT.parent.mkdir(parents=True, exist_ok=True)
    UNIT.write_text(unit_text([sys.executable, "-m", "callie.cli", "serve"], {"PYTHONPATH": str(root), **{k: os.environ[k] for k in _CARRIED if os.environ.get(k)}}))
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
    ap = argparse.ArgumentParser(prog="callie", description=__doc__)
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
    a = sub.add_parser("allie", help="Allie, the opponent that plays as people do (a 19 GB install)")
    a.add_argument("action", choices=["install"])
    a.set_defaults(fn=cmd_allie)
    h = sub.add_parser("history", help="the games and cards as files in the checkout, for git")
    h.add_argument("action", choices=["export", "import", "push"])
    h.set_defaults(fn=cmd_history)
    v = sub.add_parser("service", help="run the server as a systemd user service")
    v.add_argument("action", choices=["install", "uninstall"])
    v.set_defaults(fn=cmd_service)
    args = ap.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as err:
        print(f"callie: {err}", file=sys.stderr)
        return 2
    return args.fn(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
