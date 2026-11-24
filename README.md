# Callie

Play chess in the browser against an opponent held to a chosen Elo (Stockfish weakened, or Allie, a model that
plays the way people of that rating do), with full-strength Stockfish grading every move as you make it, a
rating of your own kept from the results, and Claude on call to explain the game with that analysis and the
engine in hand.

It is a small local web server (Python standard library only, nothing to install) meant for one person on their
own machine, reachable from their other devices over Tailscale. Allie is optional and is the one thing that is
installed: a model with an environment of its own, beside the server.

## What you get

- **Home page**: a form for a new game (your colour, the opponent (Stockfish or Allie) and its strength, which
  starts at your own rating, the opening it should steer for, and the clock), then the decks to review (your
  errors, and the heuristics), your ratings and your recent games (with the final position, result and settings, and what each did to
  your rating).
- **Game page**:
  - the board (drag a piece, or click it and then its square), with back/forward buttons, arrow keys, and a click
    on any move to walk through the game;
  - premoves: while the opponent is on its move, make yours the same way. It is marked in blue on the board and
    played the moment the opponent has moved, at no cost to your clock, if it is legal then; if it is not, or the
    opponent offers a draw with its move, it is dropped and the move is yours to make. Another premove takes its
    place, and a click anywhere on the board takes it back;
  - the advantage bar beside the board and a graph of the game's swings above the move list;
  - the move list, with the centipawns each move lost and its grade (`?!` inaccuracy, `?` mistake, `??` blunder);
    a move of yours in a yellow box has an explanation of what was wrong with it, shown by a click on it;
    once the game is over, what the engine preferred to the move on screen and the line it expected, and
    with the result what the game did to your rating and where that leaves it;
  - your clock, with a pause button, and Allie's when it is the opponent;
  - a Claude pane: a box to type to Claude, and, from your first message, a real Claude Code session in a
    terminal above it, with the lines Claude talks about listed over it, to click through on the board.
- Take-backs, resigning, and the game as PGN.
- **Review**: flashcards of your own errors, made as you play: the position before a move that lost too much, to
  find the better move in, shown again after longer and longer as you get it right, with Claude's explanation
  of why the move was bad on its back, and a box there to ask Claude about the position, in a session the card
  keeps. A fourth deck, [Heuristics](#heuristics), holds rules of thumb for finding a move, each shown by
  positions to find the move in.

## Setting it up

Needs Linux (it was built and tried on Linux only; macOS may work), Python 3.12 or newer, and
[Claude Code](https://claude.com/claude-code) logged in for the Claude pane. Everything else works without it.

```sh
git clone https://github.com/Johnor12/callie && cd callie
./callie engine install     # downloads the latest Stockfish release (about 80 MB) into the state directory
./callie allie install      # optional: Allie, the opponent that plays as people do (19 GB; see "Allie" below)
./callie start              # serves http://127.0.0.1:8750 in the background
./callie status             # what is running and what is missing
```

`./callie serve` runs it in the terminal instead, `stop` stops it, and `service install` makes it a systemd
user service that starts with the machine. The service runs no shell profile: of the shell `service install` is
run in it keeps the `PATH` (where `claude` is found) and the `CALLIE_` variables, and nothing else, so run
`service install` again when one of those changes. Settings live in `~/.config/callie/config.toml`
([config.example.toml](config.example.toml) lists every key); games, the engine and logs live in
`~/.local/share/callie`.

## The opponent

There are two, picked with each new game: Stockfish held back, described first, and [Allie](#allie).

**Strength.** A rating, and whoever the opponent is it is a rating on one scale: Allie's, which is Lichess's.
Stockfish at 1200 is set to score against Allie at 1200 as an equal would, so the number means the same
opponent to beat either way, and [your own rating](#your-rating) can be reckoned from games against both.
"Full strength" removes the limit.

Stockfish has a scale of its own, and two ways of playing below its best. From 1320 to 3190 on that scale it
limits itself (`UCI_Elo`, calibrated against the CCRL 40/4 rating list). It has no setting below 1320 (which
plays as about 1650 here), and a tutor is most useful below it, so weaker opponents are built here
(`src/callie/opponent.py`): every legal move
is scored by a 6-ply search and one is drawn with odds that fall off with how much worse than the best it is.
The fall-off (a "temperature" in centipawns) sets the strength. What plays at which rating was measured, not
guessed, by `scripts/calibrate.py`: first which temperature plays at which rating of Stockfish's scale (matches
between temperatures and Stockfish at 1320, 1500 and 1700), then what each point of Stockfish's scale is worth
on Allie's (games between Stockfish and Allie). See [Calibration](#calibration) for the numbers and their limits.

The number says how the results come out, not how the moves look. A weakened engine does not err the way a
person does: it can hang a piece in one move and find a deep tactic in the next.

**Opening.** The opponent can be asked for an opening of the colour it plays (the Sicilian or the French if you
are White; the Italian or the London if you are Black; 23 in all, in `src/callie/openings.py`). It follows the
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
the ratings that fit covers, and has no "full strength". The ratings are Lichess's, and they are the scale
Stockfish's strengths are given on too. Allie is told the ratings of both players, and is given its own for yours.

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
no GPU. `./callie allie install` puts everything under `~/.local/share/callie/allie`: a Python 3.12
environment with PyTorch (CPU) and Allie's code, its engine built from Rust (with a toolchain downloaded for the
build and removed after it, unless Rust 1.89 or newer is already there), and the weights (18 GB, from Hugging
Face). To remove it, delete that directory. The model runs in a process of its own, which is there for as long
as a browser is: it is started (about a minute to load, in the background) when a browser first has the page on
screen, whichever page of the app that is, kept while at least one does, and ended 15 minutes after the last of
them (`[allie] idle_s`). On screen is what counts: each such page tells the server so every 20 seconds, and a
tab behind another, or a phone that is locked or on another app, tells it nothing. With no browser there, a
move or a question still starts it, and it is ended 15 minutes after the last of those.
Its searches are priced for a 16-core server; on a slower or busy machine it searches less in the same think
time and plays somewhat below its rating.

## Your rating

Every finished game against an opponent with a rating moves yours: a win against a stronger opponent by a lot, a
loss to one by little. It is on the same scale as the opponents' (Lichess's, by way of Allie), and is worked out
afresh from the games on file each time it is shown (`src/callie/rating.py`), so:

- only games that are over count, whatever ended them (mate, resignation, the clock, a draw);
- a game you delete is forgotten, and a finished game you take a move back in counts again only once it ends again;
- games against Stockfish at full strength do not count: it has no rating to compare yours with.

There is a rating for each time format, as on Lichess: one for untimed games, and one for each kind of clock
(bullet, blitz, rapid, classical, by the length of a 40-move game: "10+3" is rapid). A game moves the rating of
its own format only.

The arithmetic is Glicko's, a game at a time, in the order the games ended. A rating starts at 1500 and very
unsure (a deviation of 350), so the first results move it by a hundred points or more; as games accumulate it
settles, to about 13 points for a win or loss against an equal, and no further, so that it keeps following you
as you improve. While it is still unsure (a deviation above 110, about the first ten games) it is shown with a
question mark. The home page shows each format's rating, each game's gain or loss beside its result, and starts
the opponent's strength at your rating for the clock chosen (or, with no games at that clock yet, at the rating
from the format you have played most). A game's own page shows its gain or loss too, with the rating it leaves
you at, as soon as the game is over.

Stockfish's ratings are measured against Allie at 10+3, and Allie itself plays an untimed game as people play
classical ones, so across formats the numbers are neighbours rather than the same thing; within a format they
are comparable from game to game, which is what a rating is for.

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
The session is given these tools (an MCP server, `src/callie/mcp.py`), `human_moves` only where Allie is installed:

| Tool | What it returns |
| --- | --- |
| `game` | the game as it stands: every move with its evaluation, loss, grade and the engine's preference, the [explanations](#why-the-move-was-bad) written for your errors, then the current position |
| `review` | the analysis on file for moves already played: evaluations either side of each move, the engine's choices there with the lines it expected, its main line afterwards, and the explanation written for the move if it made a card |
| `position` | any position spelled out: diagram, piece lists, legal moves |
| `analyze` | Stockfish at full strength, run now, on any position: a point in the game or a FEN, after any "what if" moves, optionally comparing chosen candidate moves |
| `show_lines` | nothing new: it puts a position and named lines from it [on your board](#lines-on-the-board), and returns them as they are listed there |
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
(`~/.local/share/callie/tutor`), so they share one Claude Code project and its memory: what Claude learns
about your play in one game is there in the next. The first session asks once whether you trust that directory.

The model is `claude-fable-5-2` unless `[claude] model` says otherwise, and `[claude] config_dir` picks which
Claude Code login the sessions use.

### Lines on the board

A line written out in notation is slow to follow. So Claude is told to put the lines its answer turns on on the
board (`show_lines`: a position, and one or more named lines from it), and to write around them. They are listed
above the terminal, wherever you talk to Claude (a game, the back of a card), each move a button:

- a click on a move puts the position after it on the board beside the pane, in place of the game or the card,
  with the move marked; a click on a line's name puts its starting position there. The board is framed in blue
  while a line is on it, and nothing on it can be moved;
- ◀ and ▶ under the list (or the arrow keys, Home and End) step through the line. With no line on the board, ▶
  starts the first one;
- ✕, Esc, or a click on a move of the game puts the game or the card back. Nothing of the game changes
  meanwhile: its clock runs as it did, and the opponent's move arrives behind the line;
- everything a conversation was shown is kept with it (its last 30), and `‹ ›` beside the title page back
  through it, so an answer you read again later still has its lines;
- on a phone, where the pane is a screen below the board, they are listed directly under the board instead (a
  game's: under the buttons that step through its moves), so that the board, the list and its buttons are on
  one screen. The list is no taller than the screen leaves under the board, and scrolls by itself past that.

Claude's moves are checked before anything is shown: a line with a move that is not legal is refused, and Claude
is told where it stops. The page still knows no chess: each move comes with the position after it.

## Reviewing your errors

Every move of yours that lost 50 centipawns or more becomes a flashcard, as soon as the engine has analysed the
positions either side of it: the position before the move, for you to find the better move in. Three decks, by
what the move lost (the [centipawn loss](#the-analysis) of the move list, not its grades, which go by winning
chances):

| Deck | Centipawns lost |
| --- | --- |
| Blunders | 200 or more |
| Mistakes | 100 to 199 |
| Inaccuracies | 50 to 99 |

A card is one position with one move played in it. The same move in the same position in another game (an
opening you keep getting wrong) makes no second card: it makes the first a new card again, active at once, and
moves it to the deck of what the move lost this time. A card outlives a take-back of its move, and is deleted
with its game: the game the move was last played in. If an earlier game still on file has the same move in the
same position, the card stays, as that game's, where it stood in its schedule. The games already on file when
the server starts get their cards then.

The home page shows each deck with its number of active cards (those to review now); a deck opens on one of
them, drawn at random from the active ones, with the opponent's last move marked on the board as in a game
(where the game is still on file, for a card made before this was kept). Move a piece to answer:

- **Wrong**: the board is marked, with where you went in red and an arrow for the right move, and beside it, on
  one line, are your move with what it lost in centipawns (against the engine's first choice; a move the card has
  no line for is searched then, for as long as a game's positions are) and the move you played in the game with
  what that lost. The card is hidden for a few minutes and then comes back. The position stays open: any other
  move you make on it is listed under that line with what it would have lost, and changes nothing of the card.
  Next moves on.
- **Right**: your move is shown with what it lost (a move as good as the engine's first choice to within 10
  centipawns is right too; the first choice is then named, at 0). You say how it was to remember, Hard, Good or
  Easy, and each button says how long it hides the card for. An answer made within 3 seconds counts as Easy
  without the question: the card stays up until Next.

The card you are on stays until you move on: load the page again, or leave the deck and come back in the same
tab, and it is there as you left it, answered or not, with the moves you tried.

Delete card, at the right of the bar at the top, takes the card on screen out of its deck for good, answered or
not: for an error that is not one to study, a move made for want of time, say. The game it is of does not make it
a card again (not at a restart, and not on another machine the history is imported on); the same move played in
that position in a later game does. What was written to explain the move stays in the game's move list.

A deck with no active card left says so, and, when a card is only minutes away, in how many: the page shows it
when it is back.

### When a card comes back

The schedule is Anki's, with Anki's default settings: the rules of its scheduler
([`rslib/src/scheduler/states`](https://github.com/ankitects/anki/tree/main/rslib/src/scheduler/states)) written
again in `src/callie/cards.py`, not its code, and tested against the values Anki's own tests expect. A wrong
answer is Anki's "Again".

- **A new card** is learned in two steps, of 1 and 10 minutes. Good moves it a step on, and after the second to
  an interval of a day; Easy takes it straight to four days; Hard repeats the step (the first after 5½ minutes);
  a wrong answer puts it back on the first, a minute away.
- **A learned card** comes back after its interval in days, and a right answer lengthens that: Hard to 1.2
  times, Good by the card's ease (2.5 to begin with), Easy by the ease and 1.3 more. An answer given late counts
  the days the card was remembered for beyond its interval. Hard lowers the ease by 0.15 and Easy raises it by
  as much, so a card that keeps being hard comes back more often than one that never is.
- **A learned card answered wrong** lapses: its ease drops by 0.2 (never below 1.3), its interval to a day, and
  it is back in 10 minutes to be learned again, from where Good returns it to that interval. So a well known
  card that is missed is shown often again, and its intervals grow more slowly than they did.
- The day a card comes back on is drawn from the days around its interval (a day either side from 3 days, two at
  a week, five at two months), so that the cards of one game do not all come back together.
- Days start at 4 in the morning: a card answered late in the evening with a day to wait is there the next morning.

Two things are not Anki's defaults. No interval is longer than 180 days (Anki's limit is a hundred years), so
that a card you know well still comes back twice a year. And a card in a step stays hidden until the step is
over, even when nothing else is left in the deck (Anki would show it up to twenty minutes early). Left out are
Anki's other scheduler, FSRS (off by default there too), its evening out of reviews across days, leeches and
daily limits.

What counts as right is the engine's first choice in the position, or another of the three lines it searched
there that it scored within 10 centipawns of the first; never the move you played in the game. A fourth move as
good as those is not known to the card and counts as wrong. All of this is fixed in `src/callie/cards.py`: there
are no settings.

### Heuristics

The fourth deck is not made of errors. It holds heuristics: short rules for finding a decent move ("to promote
a passed pawn, secure the promotion square first"), each shown by a few archetypal positions. A card of it is one
of those positions, to find the move in, like any other card; on its back, over the board's verdict, are the
heuristic and how it finds the move there. It is answered, graded and scheduled exactly as the cards of your
errors are, has no "you played" (no game made it), and Claude can be asked about it from its back: its session is
told the heuristic and the position, and reads the game the position is from when that game is on file.

The heuristics are written down in [`src/callie/heuristics.json`](src/callie/heuristics.json), and the app makes
none itself. The ones there are the author's own, and those distilled from the errors he repeats, shown mostly by
positions from his games: replace them with yours. To add one, or a position to one:

1. give it a `key` (what its cards are known by: leave it alone afterwards) and a `title`, and each position its
   `fen`, the `answer` (SAN), `why` (the back of the card) and, where one led to it, the move before (`last`, UCI);
2. `./scripts/heuristics.py` asks Stockfish for its three best lines in each new position and writes them into
   the file. They are what answers the card, as on any card: the engine's first choice, or a line within 10
   centipawns of it. The script says of each position whether its `answer` is among those; a position where it
   is not shows nothing, and the server refuses the file;
3. restart the server. It puts what the file says in the deck: a new position is a new card, active at once; a
   card whose text or lines changed keeps its place in the schedule; one whose position is gone from the file is
   deleted. A card deleted by hand stays out of the deck while its position is in the file.

### Why the move was bad

Each card gets an explanation, written by Claude in the background when the card is made (for the cards already
there, when the server starts): Claude Code is run once for the card, with no terminal, with the tools a game's
session has, and asked "Explain why this move was a blunder" (or a mistake, or inaccurate), with the position, the
move, what it lost and what the engine preferred. What it answers, some hundred words, is kept with the card and
shown:

- on the back of the card, once you have answered it;
- in the game: the move's box in the move list is yellow, and a click on it shows the explanation beside it. That
  is so while the game is still being played too, and an explanation names the better move. On a phone, where
  the list is below the board, the click also brings the board back on screen and the explanation stays below
  it; a tap elsewhere puts the explanation away, and scrolling the page does not;
- to the game's own Claude session: `game` and `review` give it with the move, as previous analysis generated
  by Claude with access to Stockfish and Allie.

The games come first (`src/callie/explain.py`):

- explanations are written one at a time, by a process at the lowest priority (`nice`), so it runs on what the
  engines of a game leave idle;
- what its tools ask is answered by a Stockfish of its own, on one thread at that same priority, searching a set
  number of positions (at most some two million) instead of a set time: kept waiting by a game, it answers
  later, not worse;
- Allie is asked only between the opponent's moves, and is not read into memory for it while a game is being
  played (one that is not over and had a move in the last ten minutes).

A card that could not be explained (Claude Code not logged in, say) is tried again after an hour. Each
explanation is one run of Claude on the account the app's sessions use.

### Asking Claude about a card

The back of a card has a box to type to Claude, under the explanation: for why another move does not work, or
where a line you had in mind goes wrong. As in a game, your first message opens a Claude Code session in a
terminal above the box, and later ones are typed into it. The session is the card's own (`src/callie/tutor.py`):

- it is told the card when it is opened: the position, your move and what it lost, the engine's choice and its
  line, the moves the card takes as right, and the explanation on its back;
- its tools are those of a game's session, and read the game the move was played in. With that game no longer on
  file, `position` and `analyze` work from the card's position. The [lines it shows](#lines-on-the-board) are
  played on the card's board;
- a message takes with it the moves you have made on the card's board since the last one, each with what it
  lost, so that "why not this?" can be asked of a move you have just tried;
- it is kept with the card. Whenever the back of the card is on screen (the card answered again, days or months
  later; the page loaded again), its terminal is there with the conversation as you left it, resumed the way a
  game's is. A card that a later game makes new again keeps it too.

Claude Code deletes the transcripts nothing was added to for 30 days (its `cleanupPeriodDays`), and a card can be
away for six months. So the server keeps a copy of each card's transcript as it grows
(`~/.local/share/callie/transcripts`), and puts it back where Claude Code looks for it before the session is
resumed. Like the games' conversations, these are not part of the history kept in git.

## The games, kept in git

The games are in a database on this machine only (`~/.local/share/callie/games.db`). To keep them in the
repository as well:

```sh
./callie history export   # the games and cards as text under history/ in the checkout
./callie history push     # that, then history/ committed on main and pushed
./callie history import   # the games and cards in history/ that the database does not hold
```

`history/games/<id>.jsonl` is one game: its row of the database, its moves and the analysis of each position, a
row to a line. `history/cards.jsonl` is the cards with where each stands in its schedule (a card you deleted keeps
its line, as removed: that is what keeps it deleted). A game that is deleted
loses its file at the next export, and its cards their lines. On another machine, or after losing the database, `git pull` and `history
import` bring the games back; a game or card the database already holds is left as it is. The conversations with
Claude are not part of it.

`push` commits `history/` and nothing else, whatever else is changed in the checkout, and pushes only when the
rest of `main` is on GitHub already: work that has not been pushed is never sent along with the games (it says
so and leaves the commit for the next push). To run it every night at 2, `crontab -e` and:

```
0 2 * * * PATH=/usr/local/bin:/usr/bin:/bin /path/to/callie/callie history push >> ~/.local/share/callie/history.log 2>&1
```

with a `PATH` that has Python 3.12 or newer, `git`, and whatever `git push` signs in with. If the repository is
public, so are the games: every move, and when it was played.

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

and open `https://my-machine.my-tailnet.ts.net:8443`. `./callie status` says which of these steps is missing.

This matters because the Claude pane is a terminal: whoever can reach the server can type into a Claude Code
session that runs as you. So the server only answers requests with a loopback `Host` or one of the `hosts`, a
same-site `Origin`, and JSON POST bodies (a web page in your browser cannot drive it), refuses anything a proxy
forwarded unless `hosts` is set, and with `tailscale_logins` set lets in only those Tailscale users. There is no
other authentication: do not put it on a port the internet can reach.

## Calibration

<!-- calibration -->
Two measurements, both made by `scripts/calibrate.py`, both on 2026-11-18 with Stockfish 19.

### The sub-1320 opponent, on Stockfish's own scale

9,090 games (`scripts/calibration.jsonl`), each temperature against its neighbours and against Stockfish at
1320, 1500 and 1700 where they were within reach of each other. One rating per temperature was then fitted to
all the results (maximum likelihood under the Elo model, Stockfish's levels held at their nominal ratings), and
where two neighbouring temperatures came out in the wrong order they were pooled. The opponent interpolates
between these. The ratings in this table are on Stockfish's own scale (`UCI_Elo`), not the one the app shows:

| Rating (Stockfish's own scale) | Temperature (centipawns) |
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

What to make of the numbers:

- A pairing of 60 to 270 games pins a rating difference to within roughly 40 to 80 points, and the lowest
  levels are reached through a chain of such pairings, so they are the least certain.
- Strength falls steeply with temperature in this range: the whole span from 1300 down to 400 is less than a
  tripling of the temperature.
- These games were played at fast time controls, where Stockfish's weakened levels pick their move at a fixed
  shallow depth and so do not get weaker.

### Stockfish's own scale on Allie's

61 games against Allie 2.1 (`scripts/calibration-allie.jsonl`, one line a game), with Allie playing as the app
has it play at 10+3: told the same things, charged its think time, resigning where it would. Eight settings of
Stockfish took part (the temperatures 245, 180, 135 and 100, and `UCI_Elo` 1320, 1550, 1800 and 2100), each
matched against Allie at about the rating the games so far made it level with, or a hundred points either side
of it. The model is two straight lines that meet at 1320 on Stockfish's own scale, where the sampler hands
over to `UCI_Elo`; their three ends were fitted to the results (maximum likelihood under the Elo model, Allie
held at its ratings):

| Stockfish's own scale | Level with Allie at | Standard error |
| --- | --- | --- |
| 450 | 588 | 126 |
| 1320 | 1648 | 78 |
| 2100 | 2125 | 109 |

So a point of Stockfish's scale below 1320 is worth about 1.2 points of Allie's and one above it about 0.6:
Stockfish's own numbers undersell it by some 140 points at the bottom and 330 at 1320, and by nothing much at
2100. Each setting's score, with what the fitted lines predict for the same games:

| Setting | Own scale | Against Allie at | Score | Predicted |
| --- | --- | --- | --- | --- |
| temperature 245 | 450 | 800 | 0 of 4 | 0.9 |
| temperature 180 | 719 | 800 to 1050 | 5 of 9 | 4.6 |
| temperature 135 | 992 | 1150 to 1350 | 5.5 of 8 | 3.8 |
| temperature 100 | 1265 | 1450 to 1750 | 5 of 8 | 3.9 |
| `UCI_Elo` 1320 | 1320 | 1600 to 1900 | 2 of 8 | 3.3 |
| `UCI_Elo` 1550 | 1550 | 1650 to 1950 | 3 of 8 | 4.4 |
| `UCI_Elo` 1800 | 1800 | 1650 to 2200 | 5 of 8 | 4.4 |
| `UCI_Elo` 2100 | 2100 | 1900 to 2300 | 4 of 8 | 3.9 |

Stockfish is offered from 530 to 2790: its settings from 400 to 3190 on its own scale, read on Allie's. What to
make of that:

- The standard errors are large: 61 games place the scale to within about 80 to 130 points at its three ends,
  and to about 60 at best between them. A Stockfish rating is good to about a hundred points either way.
- The two settings just under the join scored more than the lines predict and the two just over it less (10.5 of
  16 against a predicted 7.7, and 5 of 16 against 7.7). Eight games a setting cannot tell that from chance, but
  if it is real, the sampler near the top of its range (ratings of about 1250 to 1600) is stronger than its
  number and `UCI_Elo` near the bottom of its own (about 1650 to 1800) weaker.
- Only the stretch from 450 to 2100 on Stockfish's scale was played. Above 2100 (ratings over about 2125) the
  upper line is carried on, for more than a thousand points of Stockfish's scale; the top rating, 2790, is where
  that line puts `UCI_Elo` 3190, not something a game has shown. Allie has no rating below 800, so below that
  the lower line is carried on too, held up at its far end only by four lost games against Allie at 800.
- It is measured at 10+3 only. Allie plays differently at other clocks, and an untimed game as people play
  classical ones; Stockfish is given the same number at all of them.
- Allie's own ratings are not exact. Live on Lichess its predecessor, Allie 2.0, played some 50 to 90 points
  above the rating it was set to (about 60 in rapid) by its authors' count, and 2.1 has not been measured that
  way. Whatever that error is, Stockfish's ratings inherit it.
- A slower or busy machine makes Allie search less and play below its rating, which would make Stockfish look
  stronger than it is. These games were played one at a time, and waited whenever the app's own Allie was
  running.

To measure again (a new Stockfish release, another search depth, a new Allie):

```sh
./scripts/calibrate.py play --games 150   # temperatures on Stockfish's own scale (minutes, every core)
./scripts/calibrate.py allie --games 60   # Stockfish against Allie (about three minutes a game, one at a time)
./scripts/calibrate.py fit                # prints both fits and rewrites src/callie/levels.json
```

The games against Allie add up: `allie` can be stopped and run again, and `fit` uses every game on file. A refit
changes what a rating means for games started after it; games already on file keep the rating they were played
at.
<!-- /calibration -->

## How it is put together

| File | What it does |
| --- | --- |
| `src/callie/chess.py` | the rules, from scratch: legal moves, FEN, SAN, how a game ends (checked against published perft counts) |
| `src/callie/engine.py` | a UCI engine as a child process; what a score means (winning chances, loss, grades) |
| `src/callie/opponent.py`, `levels.json` | Stockfish's move at a rating |
| `src/callie/rating.py` | your ratings, from the games on file |
| `src/callie/cards.py` | the flashcards of your errors: the decks, what answers a card, how long it is hidden |
| `src/callie/allie.py`, `allie_worker.py` | Allie: the process that holds the model (run in Allie's own environment), and the server's end of it |
| `src/callie/openings.py` | the opening books |
| `src/callie/play.py` | games: moves, replies, the clocks, the analysis queue (which makes the cards), what the Claude tools return |
| `src/callie/store.py` | the SQLite database: games, moves, the analysis of each position, the cards, the lines Claude showed |
| `src/callie/history.py` | the games and cards as files under `history/`: written, read back, committed and pushed |
| `src/callie/tutor.py` | the Claude Code session of each game: how it is started, resumed and typed into |
| `src/callie/explain.py` | the explanation of each card: Claude run once for it, in the background, behind the games |
| `src/callie/terminals.py`, `ptyexec.py` | programs on pseudo-terminals, streamed to the page |
| `src/callie/mcp.py` | the tools a session is given (an MCP server on stdio that calls back into the server) |
| `src/callie/server.py` | the page and the JSON API |
| `src/callie/static/` | the page: `app.js` (board, clock, review, terminal, Claude's lines), `pieces.js` (the pieces, drawn here), `style.css` |

The page holds no chess rules: with each position where it is your move the server sends every legal move and
the position it leads to, and with each where it is the opponent's the moves that can be made ahead (how your
pieces move on an empty board: the server holds the one you make and plays it itself). Every action is a plain HTTP call, so the whole thing can be driven with `curl`.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

Nothing to install. Tests that need the engine are skipped without it; none starts the real Claude Code (sessions
and explanations are exercised against `tests/fake_claude.py`) or loads Allie (`tests/fake_allie.py` answers in its place).

The Python is kept to `ruff check` and `ruff format` (settings in `pyproject.toml`). `.githooks/pre-commit` refuses
a commit that is not; turn it on in a checkout with `git config core.hooksPath .githooks`.

## Licences

This project is MIT licensed. Stockfish is GPLv3 and is not included: `engine install` downloads the official
release. Allie (code and weights) is MIT and is not included either: `allie install` fetches both. `src/callie/static/vendor` holds xterm.js 6.0.0 with its fit, WebGL and unicode-graphemes addons, as
published on npm, MIT.
