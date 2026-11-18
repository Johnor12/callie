"""`python ptyexec.py PROGRAM ARGS...`: become PROGRAM with the terminal on stdin as controlling terminal.

Started by `terminals.Terminal` as the leader of a new session whose stdio is a pseudo-terminal. A session leader
has to claim its terminal before the kernel will send it window-size and hang-up signals.
"""

import fcntl
import os
import sys
import termios

if __name__ == "__main__":
    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError:
        pass
    try:
        os.execvp(sys.argv[1], sys.argv[1:])
    except OSError as e:
        sys.stderr.write(f"chess-tutor: cannot run {sys.argv[1]}: {e}\r\n")
        sys.exit(127)
