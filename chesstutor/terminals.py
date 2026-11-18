"""Programs on pseudo-terminals, shown in the page: what lets a real Claude Code session live in a browser tab.

The server holds the terminal's master side. A thread reads what the program writes and hands it to every viewer;
what a viewer types is written back. A viewer that arrives late is given the output so far (what was written
outside the alternate screen) and the terminal modes the program has set, and the page then asks for a repaint.
"""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import queue
import re
import secrets
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

REPLAY_BYTES = 1 << 20  # recent output kept for a viewer that attaches later
_MODE = re.compile(rb"\x1b\[\?([0-9;]*)([hl])")  # a DEC private mode set or reset
_CUT = re.compile(rb"\x1b(\[(\?[0-9;]*)?)?\Z")  # the start of one, cut off by the end of a read
_ALT_SCREEN = (47, 1047, 1049)
_PER_FRAME = (2026,)  # synchronized output: on for the length of one frame, never a state to hand on


class Terminal:
    def __init__(self, argv: list[str], env: dict[str, str], cwd: str, cols: int, rows: int, log: Path):
        self.id = secrets.token_hex(6)
        self.cols, self.rows = max(10, int(cols)), max(2, int(rows))
        self.started = time.time()
        self.lock = threading.Lock()
        self.viewers: list[queue.Queue] = []
        self.last_viewed = time.time()
        self.replay = bytearray()
        self.modes: dict[int, bool] = {}
        self.alt = False
        self._cut = b""
        self.exit_code: int | None = None
        self.master, slave = pty.openpty()
        self._size()
        # The child starts in a session of its own with the terminal as its stdio; `ptyexec` then makes it the
        # session's controlling terminal (so the program gets window-size signals) and becomes the program.
        launcher = [sys.executable, str(Path(__file__).with_name("ptyexec.py")), *argv]
        with open(log, "ab") as errors:
            try:
                self.proc = subprocess.Popen(
                    launcher, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env, start_new_session=True
                )
            except OSError as e:
                errors.write(f"cannot start {argv[0]}: {e}\n".encode())
                os.close(slave)
                os.close(self.master)
                raise
        os.close(slave)
        threading.Thread(target=self._read, name=f"terminal-{self.id}", daemon=True).start()

    # ---------- the program's side ----------

    def _size(self) -> None:
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))

    def _read(self) -> None:
        while True:
            try:
                data = os.read(self.master, 65536)
            except OSError as e:
                if e.errno == errno.EINTR:
                    continue
                data = b""  # EIO: the program closed its side
            if not data:
                break
            with self.lock:
                self._track(data)
                for q in self.viewers:
                    q.put(("o", data))
        try:
            self.exit_code = self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.exit_code = self.proc.wait()
        with self.lock:
            for q in self.viewers:
                q.put(("x", b""))
        try:
            os.close(self.master)
        except OSError:
            pass

    def _track(self, data: bytes) -> None:
        """Follow the modes the program sets; keep for a later viewer what it writes outside the alternate screen."""
        data = self._cut + data
        cut = _CUT.search(data)
        self._cut = data[cut.start() :] if cut else b""
        end = cut.start() if cut else len(data)
        pos = 0
        for m in _MODE.finditer(data, 0, end):
            if not self.alt:
                self.replay += data[pos : m.end()]
            pos = m.end()
            for n in m[1].split(b";"):
                if n.isdigit():
                    self.modes[int(n)] = m[2] == b"h"
            self.alt = any(self.modes.get(n) for n in _ALT_SCREEN)
        if not self.alt:
            self.replay += data[pos:end]
        if len(self.replay) > REPLAY_BYTES:
            del self.replay[: len(self.replay) - REPLAY_BYTES]

    def _state(self) -> bytes:
        """What brings a new viewer's terminal to where the program's is: the output kept, then its modes."""
        order = sorted(self.modes, key=lambda n: n not in _ALT_SCREEN)
        modes = b"".join(b"\x1b[?%d%s" % (n, b"h" if self.modes[n] else b"l") for n in order if n not in _PER_FRAME)
        return bytes(self.replay) + modes

    # ---------- the viewers' side ----------

    @property
    def alive(self) -> bool:
        return self.exit_code is None and self.proc.poll() is None

    def write(self, data: bytes) -> None:
        if not self.alive:
            return
        try:
            while data:
                n = os.write(self.master, data[:4096])
                data = data[n:]
        except OSError:
            pass

    def resize(self, cols: int, rows: int) -> None:
        """Set the window size. Asked for the size it already has, the program is made to draw its whole screen
        again: a full-screen program only does that when its window really changes, so it is made a row shorter
        and put back."""
        cols, rows = max(10, int(cols)), max(2, int(rows))
        if not self.alive:
            return
        try:
            if (cols, rows) == (self.cols, self.rows):
                self.rows = max(2, rows - 1)
                self._size()
                time.sleep(0.05)
            self.cols, self.rows = cols, rows
            self._size()
        except OSError:
            pass

    def stream(self, idle: float = 15.0):
        """Yield ("o", bytes) as the program writes, ("idle", b"") when it is quiet, ("x", b"") once it has ended."""
        q: queue.Queue = queue.Queue()
        with self.lock:
            q.put(("o", self._state()))
            if not self.alive:
                q.put(("x", b""))
            self.viewers.append(q)
        try:
            while True:
                self.last_viewed = time.time()
                try:
                    kind, data = q.get(timeout=idle)
                except queue.Empty:
                    yield "idle", b""
                    continue
                yield kind, data
                if kind == "x":
                    return
        finally:
            with self.lock:
                self.viewers.remove(q)
            self.last_viewed = time.time()

    def stop(self) -> None:
        """Hang up, as closing a terminal window does; kill it if it has not gone within a few seconds."""
        if not self.alive:
            return
        for sig in (signal.SIGHUP, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            deadline = time.time() + 4
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    return
                time.sleep(0.05)
