"""The tutor's tools: a Model Context Protocol server on stdio that Claude Code starts with each game's session.

It holds no chess of its own. Each tool call is passed to the running Callie server (CALLIE_URL) for the
game this session belongs to (CALLIE_GAME), which owns the game and the engines. `human_moves` is offered
only where Allie is installed (CALLIE_ALLIE). A session that works in the background (CALLIE_QUIET: one
that explains a card) says so with each call, and the server answers it with what the games can spare. Standard library only, and run as a file, so it needs nothing
from the environment Claude Code starts it in.
"""

import json
import os
import sys
import urllib.error
import urllib.request

_POSITION = {
    "ply": {
        "type": "integer",
        "description": "A point in the game: the position after this many half-moves (0 is the start, 1 after "
        "White's first move). Omit for the position on the board now.",
    },
    "fen": {"type": "string", "description": "Any position as a FEN, instead of a point in the game."},
    "moves": {
        "type": "array",
        "items": {"type": "string"},
        "description": 'Moves to play on that position first, in order, in SAN ("Nf3", "exd5", "O-O") or UCI '
        '("g1f3"): for "what if" lines.',
    },
}

TOOLS = [
    {
        "name": "game",
        "description": "The game this session is about, as it stands now: who plays what, every move so far (one "
        "line each) with the engine's evaluation after it, the centipawns it lost, its grade and what the engine "
        "preferred, the explanations already written for the student's errors, then the current position spelled out.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "review",
        "description": "The engine analysis already on file for moves that were played (every position of the "
        "game is analysed at full strength as it is reached): the evaluation before and after each move, its "
        "cost and grade, the engine's choices in that position with the lines it expected, and its main line "
        "after the move. Defaults to all of the student's moves.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_ply": {"type": "integer", "description": "First half-move to include (1 is White's first move)."},
                "to_ply": {"type": "integer", "description": "Last half-move to include."},
                "who": {
                    "type": "string",
                    "enum": ["student", "all"],
                    "description": "Whose moves: the student's (default) or both sides'.",
                },
            },
        },
    },
    {
        "name": "position",
        "description": "A position spelled out: diagram, each side's pieces, material, side to move, castling "
        "rights, and every legal move. Use it before describing a position or proposing a move.",
        "inputSchema": {"type": "object", "properties": _POSITION},
    },
    {
        "name": "analyze",
        "description": "Full-strength Stockfish, run now, on a position: its best lines with evaluations (for White; "
        "#n is mate). Use `moves` to look at what follows a move that was or could have been played, and "
        "`candidates` to compare specific moves against each other.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_POSITION,
                "candidates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only consider these first moves (SAN or UCI), one line for each.",
                },
                "lines": {"type": "integer", "description": "How many best lines to return (1 to 8, default 3)."},
                "seconds": {"type": "number", "description": "How long to search (default 2, at most 20)."},
            },
        },
    },
]

HUMAN_MOVES = {
    "name": "human_moves",
    "description": "What people actually play in a position, predicted by Allie 2.1, a model trained on human "
    "games: the most played moves of players of a given rating with how often each is played, how games between "
    "such players end from there (win / draw / loss), and optionally the likeliest continuation. Use it to see "
    "which moves and replies are natural at the student's level, which mistakes are common, and what an opponent "
    "of some rating would probably answer. It says how likely a move is, never how good: evaluations and best "
    "moves come from `analyze` and `review` (Stockfish) only. It reads a game from its first move, so it takes a "
    "point in this game and moves played on from it, not a FEN.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "ply": _POSITION["ply"],
            "moves": _POSITION["moves"],
            "elo": {
                "type": "integer",
                "description": "The rating of both players (Lichess scale, 400 to 3200). Default: the opponent's "
                "rating when the opponent is Allie, else 1500.",
            },
            "top": {"type": "integer", "description": "How many of the most played moves to list (1 to 15, default 5)."},
            "line": {
                "type": "integer",
                "description": "Also follow each side's most played move for this many half-moves (up to 16, default 0).",
            },
        },
    },
}


def tools() -> list[dict]:
    return TOOLS + ([HUMAN_MOVES] if os.environ.get("CALLIE_ALLIE") == "1" else [])


def call(name: str, arguments: dict) -> tuple[str, bool]:
    url = f"{os.environ.get('CALLIE_URL', 'http://127.0.0.1:8750')}/api/games/{os.environ.get('CALLIE_GAME', '')}/tool"
    body = json.dumps({"name": name, "arguments": arguments or {}, "quiet": os.environ.get("CALLIE_QUIET") == "1"}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:  # Allie, asked cold, loads for a minute first
            return json.loads(r.read())["text"], False
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read()).get("error", str(e)), True
        except ValueError:
            return str(e), True
    except (OSError, ValueError, KeyError) as e:
        return f"the Callie server did not answer: {e}", True


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None  # a notification: nothing to answer
    if method == "initialize":
        result = {
            "protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "chess", "version": "0.1.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tools()}
    elif method == "tools/call":
        params = msg.get("params") or {}
        text, failed = call(params.get("name", ""), params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": failed}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            answer = handle(json.loads(line))
        except ValueError:
            continue
        if answer is not None:
            sys.stdout.write(json.dumps(answer) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
