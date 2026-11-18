# Chess Tutor

Play chess in the browser against an opponent held to a chosen Elo (Stockfish weakened, or Allie, a model that
plays the way people of that rating do), with full-strength Stockfish grading every move as you make it, and
Claude on call to explain the game with that analysis and the engine in hand.

It is a small local web server (Python standard library only, nothing to install) meant for one person on their
own machine, reachable from their other devices over Tailscale. Allie is optional and is the one thing that is
installed: a model with an environment of its own, beside the server.

## What you get

- **Home page**: your recent games (with the final position, result and settings) and a form for a new one: your
  colour, the opponent (Stockfish or Allie) and its strength, the opening it should steer for, and the clock.
- **Game page**:
  - the board (drag a piece, or click it and then its square), with back/forward buttons, arrow keys, and a click
    on any move to walk through the game;
  - the advantage bar beside the board and a graph of the game's swings above the move list;
  - the move list, with the centipawns each move lost and its grade (`?!` inaccuracy, `?` mistake, `??` blunder);
    once the game is over, what the engine preferred to the move on screen and the line it expected;
  - your clock, with a pause button, and Allie's when it is the opponent;
  - a Claude pane: a box to type to Claude, and, from your first message, a real Claude Code session in a
    terminal above it.
- Take-backs, resigning, and the game as PGN.

## Setting it up

Needs Linux (it was built and tried on Linux only; macOS may work), Python 3.12 or newer, and
[Claude Code](https://claude.com/claude-code) logged in for the Claude pane. Everything else works without it.

```sh
git clone https://github.com/Johnor12/chess-tutor && cd chess-tutor
./chess-tutor engine install     # downloads the latest Stockfish release (about 80 MB) into the state directory
./chess-tutor allie install      # optional: Allie, the opponent that plays as people do (19 GB; see "Allie" below)
./chess-tutor start              # serves http://127.0.0.1:8750 in the background
./chess-tutor status             # what is running and what is missing
```

`./chess-tutor serve` runs it in the terminal instead, `stop` stops it, and `service install` makes it a systemd
user service that starts with the machine. Settings live in `~/.config/chess-tutor/config.toml`
([config.example.toml](config.example.toml) lists every key); games, the engine and logs live in
`~/.local/share/chess-tutor`.

## The opponent

There are two, picked with each new game: Stockfish held back, described first, and [Allie](#allie).

**Strength.** From 1320 to 3190 the opponent is Stockfish limiting itself (`UCI_Elo`), on the scale Stockfish
calibrated against the CCRL 40/4 rating list. "Full strength" removes the limit.

Stockfish has no setting below 1320, and a tutor is most useful below it, so weaker opponents are built here
(`chesstutor/opponent.py`): every legal move is scored by a 6-ply search and one is drawn with odds that fall off
with how much worse than the best it is. The fall-off (a "temperature" in centipawns) sets the strength. Which
temperature plays at which rating was measured, not guessed: `scripts/calibrate.py` plays matches between
temperatures and Stockfish at 1320, 1500 and 1700 and fits one rating per temperature to all the results. See
[Calibration](#calibration) for the numbers and their limits.

These are ratings on Stockfish's engine scale, not chess.com or Lichess ratings, which come from other pools of
players. And a weakened engine does not err the way a person does: it can hang a piece in one move and find a
deep tactic in the next.

**Opening.** The opponent can be asked for an opening of the colour it plays (the Sicilian or the French if you
are White; the Italian or the London if you are Black; 23 in all, in `chesstutor/openings.py`). It follows the
book for as long as your moves allow, transpositions included, then plays at its strength. Book moves are marked
in the move list.

**Clock.** "10+3" gives you ten minutes plus three seconds a move; "Untimed" gives you none. Against Stockfish
only you are timed. The clock runs while it is your move and the game's page is open and visible: close the tab,
switch away, or press Pause (to talk to Claude, say), and it stops. Run out and the game is lost on time.

### Allie

[Allie 2.1](https://huggingface.co/yimingzhang/allie-2.1) is a model trained on human games to predict the move
a player of a given rating makes, given the game so far, both clocks and the time control, and how long they
think on it. It is the opponent to pick for one that errs the way people do.

**Strength.** It plays its *calibrated* mode, as its own Lichess bot does (the code is Allie's, at a pinned
commit): for each move a think time is drawn from the model, a search as large as that time allows scores the
moves, and the move is drawn from the human distribution tilted by those scores. That is fitted so that at rating
R it plays moves of the quality people rated R play at the game's time control. It is offered from 800 to 2600,
the ratings that fit covers, and has no "full strength". The ratings are Lichess's, so the same number does not
mean the same thing as for Stockfish. Allie is told the ratings of both players, and is given its own for yours.

**Clock.** Allie plays to the same clock as you: "10+3" gives each side ten minutes and three seconds a move, and
its clock is on the page above the board. It is stopped by exactly what stops yours. It runs while it is Allie's
move and the game's page is open and visible; with the tab closed or hidden, or the game paused, it stands still
and Allie's move waits with it, to be made once the rest of its think time has run. As for you, its first move
is not timed, and it loses the game if it runs out (it keeps a reserve and rarely does). What is charged for a
move is the think time it drew, which the search fits inside. Time the model takes to load is not charged.

In an untimed game it has no clock either: it is told there is no time control, draws a think time all the same
to size its search, and moves as soon as the search is done.

**Resigning and draws.** Allie gives a game up, and offers a draw, where people of its rating do (the behaviour
is Allie's own, fitted on human games): it may resign on its move instead of playing, or just after a move of
its own, never before move 10 and seldom before the game is as good as lost; a draw offer comes with a move, and
is rare. An offer shows beside its name with Accept and Decline, and playing on declines it. You cannot offer
it one.

**What it takes.** Linux on x86-64, [uv](https://docs.astral.sh/uv/), 19 GB of disk and 9 GB of free memory;
no GPU. `./chess-tutor allie install` puts everything under `~/.local/share/chess-tutor/allie`: a Python 3.12
environment with PyTorch (CPU) and Allie's code, its engine built from Rust (with a toolchain downloaded for the
build and removed after it, unless Rust 1.89 or newer is already there), and the weights (18 GB, from Hugging
Face). To remove it, delete that directory. The model runs in a process of its own, started when a game or a
question first needs it (about a minute to load) and ended after half an hour without either (`[allie] idle_s`).
Its searches are priced for a 16-core server; on a slower or busy machine it searches less in the same think
time and plays somewhat below its rating.

## The analysis

**The engine, always.** Every position of every game is analysed once by Stockfish at full strength (1.5 seconds
on half the machine's threads, three lines), in the background, as the position is reached. From that:

- **Centipawn loss** of a move is the evaluation before it minus the evaluation after it, from the mover's side,
  with both capped at ten pawns (a win that stays a win costs nothing). The engine's own first choice costs 0.
- **Grades** follow the winning chances given away (the curve and thresholds Lichess uses): 5 points of winning
  chance is an inaccuracy, 10 a mistake, 15 a blunder.
- The **advantage bar** shows White's winning chances in the position on screen; the graph shows them over the
  game, with your mistakes and blunders marked.
- What the engine preferred to a move (a green arrow on the board, and its line under it, while the move is on
  screen) is shown only once the game is over. While it is on you see what each move cost and its grade, not
  what should have been played.

**Claude, when you ask.** Nothing is sent to Claude as you play. The pane is a box to type in: your first
message opens a Claude Code session above it, told what the game is and what is expected of it, and later ones
are typed into that session (which is resumed if it had been closed for lack of use).
The session is given these tools (an MCP server, `chesstutor/mcp.py`), the last only where Allie is installed:

| Tool | What it returns |
| --- | --- |
| `game` | the game as it stands: every move with its evaluation, loss, grade and the engine's preference, then the current position |
| `review` | the analysis on file for moves already played: evaluations either side of each move, the engine's choices there with the lines it expected, and its main line afterwards |
| `position` | any position spelled out: diagram, piece lists, legal moves |
| `analyze` | Stockfish at full strength, run now, on any position: a point in the game or a FEN, after any "what if" moves, optionally comparing chosen candidate moves |
| `human_moves` | Allie's prediction of what people play at a point in the game, after any "what if" moves: the most played moves of players of a chosen rating with how often each is played, how such games end from there, and the likeliest continuation |

Claude is told to check lines with the engine rather than trust its own reading of the board, and not to give
away the position you are playing unless you ask. Whoever the opponent is, every evaluation, grade and best move
is Stockfish's at full strength: `human_moves` is there for what people play (the replies to expect at your
level, whether a mistake is a common one), and says how likely a move is, never how good.

Each game has its own session, and it is shown whenever the game is: come back to a game (from the list, after a
reload, in another tab or browser) and its terminal is there as you left it. A terminal nobody has watched for
`[claude] idle_terminal_s` (15 minutes) is ended, and all of them end when the server restarts; the page then
resumes the session in a new one (`claude --resume`, which redraws the conversation and asks the model nothing).
One you end yourself (`/exit`) stays closed until you send a message or come back to the game.

All of them run in one directory
(`~/.local/share/chess-tutor/tutor`), so they share one Claude Code project and its memory: what Claude learns
about your play in one game is there in the next. The first session asks once whether you trust that directory.

The model is `claude-fable-5-2` unless `[claude] model` says otherwise, and `[claude] config_dir` picks which
Claude Code login the sessions use.

## From another device, over Tailscale

The server listens on 127.0.0.1 only. `tailscale serve` takes HTTPS for the machine's tailnet name and hands the
requests to it:

```sh
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8750
```

Then tell the server that name, and who may use it, in the config:

```toml
[server]
hosts = ["my-machine.my-tailnet.ts.net"]
tailscale_logins = ["me@example.com"]
```

and open `https://my-machine.my-tailnet.ts.net:8443`. `./chess-tutor status` says which of these steps is missing.

This matters because the Claude pane is a terminal: whoever can reach the server can type into a Claude Code
session that runs as you. So the server only answers requests with a loopback `Host` or one of the `hosts`, a
same-site `Origin`, and JSON POST bodies (a web page in your browser cannot drive it), refuses anything a proxy
forwarded unless `hosts` is set, and with `tailscale_logins` set lets in only those Tailscale users. There is no
other authentication: do not put it on a port the internet can reach.

## Calibration

<!-- calibration -->
The sub-1320 opponent was measured on 2026-11-18 with Stockfish 19: 9,090 games
(`scripts/calibration.jsonl`), each temperature against its neighbours and against Stockfish at 1320, 1500 and
1700 where they were within reach of each other. One rating per temperature was then fitted to all the results
(maximum likelihood under the Elo model, Stockfish's levels held at their nominal ratings), and where two
neighbouring temperatures came out in the wrong order they were pooled. The opponent interpolates between these:

| Rating | Temperature (centipawns) |
| --- | --- |
| 1414 | 80 |
| 1380 | 90 |
| 1265 | 100 |
| 1191 | 110 |
| 1071 | 120 |
| 992 | 135 |
| 913 | 150 |
| 789 | 165 |
| 719 | 180 |
| 605 | 200 |
| 542 | 220 |
| 450 | 245 |
| 344 | 270 |

Opponents are offered from 400 up. What to make of the numbers:

- A pairing of 60 to 270 games pins a rating difference to within roughly 40 to 80 points, and the lowest
  levels are reached through a chain of such pairings, so they are the least certain.
- Strength falls steeply with temperature in this range: the whole span from 1300 down to 400 is less than a
  tripling of the temperature.
- The scale is Stockfish's (`UCI_Elo`), measured at fast time controls, where its weakened levels pick their
  move at a fixed shallow depth and so do not get weaker.

To measure again (a new Stockfish release, another search depth): `./scripts/calibrate.py play --games 150`,
then `./scripts/calibrate.py fit`, which rewrites `chesstutor/levels.json`.
<!-- /calibration -->

## How it is put together

| File | What it does |
| --- | --- |
| `chesstutor/chess.py` | the rules, from scratch: legal moves, FEN, SAN, how a game ends (checked against published perft counts) |
| `chesstutor/engine.py` | a UCI engine as a child process; what a score means (winning chances, loss, grades) |
| `chesstutor/opponent.py`, `levels.json` | Stockfish's move at a rating |
| `chesstutor/allie.py`, `allie_worker.py` | Allie: the process that holds the model (run in Allie's own environment), and the server's end of it |
| `chesstutor/openings.py` | the opening books |
| `chesstutor/play.py` | games: moves, replies, the clocks, the analysis queue, what the Claude tools return |
| `chesstutor/store.py` | the SQLite database: games, moves, the analysis of each position |
| `chesstutor/tutor.py` | the Claude Code session of each game: how it is started, resumed and typed into |
| `chesstutor/terminals.py`, `ptyexec.py` | programs on pseudo-terminals, streamed to the page |
| `chesstutor/mcp.py` | the tools a session is given (an MCP server on stdio that calls back into the server) |
| `chesstutor/server.py` | the page and the JSON API |
| `chesstutor/static/` | the page: `app.js` (board, clock, terminal), `pieces.js` (the pieces, drawn here), `style.css` |

The page holds no chess rules: with each position where it is your move the server sends every legal move and
the position it leads to. Every action is a plain HTTP call, so the whole thing can be driven with `curl`.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

Nothing to install. Tests that need the engine are skipped without it; none starts the real Claude Code (sessions
are exercised against `tests/fake_claude.py`) or loads Allie (`tests/fake_allie.py` answers in its place).

## Licences

This project is MIT licensed. Stockfish is GPLv3 and is not included: `engine install` downloads the official
release. Allie (code and weights) is MIT and is not included either: `allie install` fetches both. `chesstutor/static/vendor` holds xterm.js 6.0.0 with its fit, WebGL and unicode-graphemes addons, as
published on npm, MIT.
