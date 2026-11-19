"""The openings the opponent can be asked to steer for.

An opening is a handful of main lines. The opponent only controls its own moves, so a book is a map from a
position (found however the game got there, so transpositions count) to the moves the opponent plays in it. Once
the game leaves the book, the engine takes over at the chosen strength.
"""

from __future__ import annotations

import random

from .chess import START_FEN, Position

# key: (name, the side the OPPONENT plays, lines in SAN from the start position)
OPENINGS: dict[str, tuple[str, str, list[str]]] = {
    # ----- the opponent is White (you play Black) -----
    "italian": (
        "Italian Game",
        "w",
        [
            "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6 O-O O-O",
            "e4 e5 Nf3 Nc6 Bc4 Nf6 d3 Bc5 c3 d6 O-O",
            "e4 e5 Nf3 Nc6 Bc4 Nf6 d3 Be7 O-O O-O Re1",
            "e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4 d4",
            "e4 e5 Nf3 d6 d4 exd4 Nxd4",
            "e4 c5 Nf3",
            "e4 e6 d4 d5 Nc3",
            "e4 c6 d4 d5 Nc3",
            "e4 d5 exd5 Qxd5 Nc3",
            "e4 d6 d4 Nf6 Nc3",
        ],
    ),
    "ruy-lopez": (
        "Ruy Lopez",
        "w",
        [
            "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 c3 O-O h3",
            "e4 e5 Nf3 Nc6 Bb5 Nf6 O-O Nxe4 d4 Nd6 Bxc6 dxc6 dxe5 Nf5 Qxd8+",
            "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 d6 c3 Bd7 d4",
            "e4 e5 Nf3 Nc6 Bb5 d6 d4",
            "e4 e5 Nf3 Nc6 Bb5 Bc5 c3",
            "e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4 d4",
            "e4 e5 Nf3 d6 d4 exd4 Nxd4",
            "e4 c5 Nf3",
            "e4 e6 d4 d5 Nc3",
            "e4 c6 d4 d5 Nc3",
            "e4 d5 exd5 Qxd5 Nc3",
            "e4 d6 d4 Nf6 Nc3",
        ],
    ),
    "scotch": (
        "Scotch Game",
        "w",
        [
            "e4 e5 Nf3 Nc6 d4 exd4 Nxd4 Nf6 Nxc6 bxc6 e5 Qe7 Qe2 Nd5 c4",
            "e4 e5 Nf3 Nc6 d4 exd4 Nxd4 Bc5 Be3 Qf6 c3 Nge7 Bc4",
            "e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4 d4",
            "e4 e5 Nf3 d6 d4 exd4 Nxd4",
            "e4 c5 Nf3",
            "e4 e6 d4 d5 Nc3",
            "e4 c6 d4 d5 Nc3",
            "e4 d5 exd5 Qxd5 Nc3",
        ],
    ),
    "kings-gambit": (
        "King's Gambit",
        "w",
        [
            "e4 e5 f4 exf4 Nf3 g5 h4 g4 Ne5",
            "e4 e5 f4 exf4 Nf3 d6 d4 g5 h4",
            "e4 e5 f4 exf4 Nf3 d5 exd5 Nf6 Bb5+",
            "e4 e5 f4 Bc5 Nf3 d6 c3",
            "e4 e5 f4 d5 exd5 e4 d3",
            "e4 c5 Nf3",
            "e4 e6 d4 d5 Nc3",
            "e4 c6 d4 d5 Nc3",
        ],
    ),
    "vienna": (
        "Vienna Game",
        "w",
        [
            "e4 e5 Nc3 Nf6 f4 d5 fxe5 Nxe4 Nf3",
            "e4 e5 Nc3 Nc6 Bc4 Nf6 d3 Bc5 f4",
            "e4 e5 Nc3 Nf6 Bc4 Nxe4 Qh5 Nd6 Bb3",
            "e4 e5 Nc3 Bc5 Nf3 d6 d4",
            "e4 c5 Nc3",
            "e4 e6 d4 d5 Nc3",
            "e4 c6 d4 d5 Nc3",
        ],
    ),
    "queens-gambit": (
        "Queen's Gambit",
        "w",
        [
            "d4 d5 c4 e6 Nc3 Nf6 Bg5 Be7 e3 O-O Nf3 h6 Bh4",
            "d4 d5 c4 c6 Nf3 Nf6 Nc3 dxc4 a4 Bf5 e3 e6 Bxc4",
            "d4 d5 c4 c6 Nf3 Nf6 Nc3 e6 e3 Nbd7 Bd3",
            "d4 d5 c4 dxc4 Nf3 Nf6 e3 e6 Bxc4 c5 O-O",
            "d4 Nf6 c4 e6 Nc3 d5 Bg5",
            "d4 Nf6 c4 e6 Nc3 Bb4 e3 O-O Bd3",
            "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3 O-O Be2",
            "d4 Nf6 c4 g6 Nc3 d5 cxd5 Nxd5 e4",
            "d4 e6 c4 d5 Nc3",
            "d4 f5 c4 Nf6 g3",
            "d4 d6 c4",
            "d4 c5 d5",
        ],
    ),
    "london": (
        "London System",
        "w",
        [
            "d4 d5 Bf4 Nf6 e3 e6 Nf3 c5 c3 Nc6 Nbd2 Bd6 Bg3 O-O Bd3",
            "d4 d5 Bf4 Nf6 e3 c5 c3 Nc6 Nd2 e6 Ngf3",
            "d4 d5 Bf4 c5 e3 Nc6 c3 Nf6 Nd2",
            "d4 Nf6 Bf4 g6 e3 Bg7 Nf3 O-O Be2 d6 h3",
            "d4 Nf6 Bf4 e6 e3 c5 c3 d5 Nd2",
            "d4 Nf6 Bf4 d5 e3",
            "d4 e6 Bf4",
            "d4 g6 Bf4",
            "d4 f5 Bf4",
            "d4 d6 Bf4",
            "d4 c5 d5",
        ],
    ),
    "english": (
        "English Opening",
        "w",
        [
            "c4 e5 Nc3 Nf6 g3 d5 cxd5 Nxd5 Bg2 Nb6 Nf3 Nc6 O-O",
            "c4 e5 Nc3 Nc6 g3 g6 Bg2 Bg7 d3 d6",
            "c4 c5 Nc3 Nc6 g3 g6 Bg2 Bg7 Nf3",
            "c4 Nf6 Nc3 e6 e4 d5 e5",
            "c4 Nf6 Nc3 g6 g3 Bg7 Bg2 O-O e4",
            "c4 e6 Nc3 d5 d4",
            "c4 c6 e4 d5 exd5 cxd5 d4",
            "c4 d5 cxd5",
            "c4 g6 Nc3",
            "c4 f5 Nc3",
        ],
    ),
    "catalan": (
        "Catalan Opening",
        "w",
        [
            "d4 Nf6 c4 e6 g3 d5 Bg2 Be7 Nf3 O-O O-O dxc4 Qc2 a6 Qxc4",
            "d4 Nf6 c4 e6 g3 d5 Bg2 dxc4 Nf3 c5 O-O",
            "d4 d5 c4 e6 Nf3 Nf6 g3 Be7 Bg2 O-O O-O",
            "d4 Nf6 c4 g6 g3 Bg7 Bg2 O-O Nf3",
            "d4 d5 c4 c6 Nf3 Nf6 g3",
            "d4 d5 c4 dxc4 Nf3 Nf6 g3",
            "d4 Nf6 c4 c5 d5",
        ],
    ),
    "reti-kia": (
        "Réti / King's Indian Attack",
        "w",
        [
            "Nf3 d5 g3 Nf6 Bg2 e6 O-O Be7 d3 O-O Nbd2 c5 e4",
            "Nf3 d5 g3 c5 Bg2 Nc6 O-O e5 d3",
            "Nf3 Nf6 g3 g6 Bg2 Bg7 O-O O-O d3",
            "Nf3 c5 g3 Nc6 Bg2",
            "Nf3 d5 g3 Bg4 Bg2",
            "Nf3 e6 g3",
            "Nf3 f5 g3",
            "Nf3 d6 g3",
            "Nf3 Nc6 g3",
        ],
    ),
    # ----- the opponent is Black (you play White) -----
    "e5-classical": (
        "Open Game (1...e5)",
        "b",
        [
            "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 c3 O-O",
            "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6 O-O O-O",
            "e4 e5 Nf3 Nc6 Bc4 Bc5 b4 Bxb4 c3 Ba5 d4 exd4",
            "e4 e5 Nf3 Nc6 d4 exd4 Nxd4 Nf6 Nxc6 bxc6 e5 Qe7",
            "e4 e5 Nf3 Nc6 Nc3 Nf6 Bb5 Bb4 O-O O-O",
            "e4 e5 Nf3 Nc6 Bc4 Bc5 d3 Nf6",
            "e4 e5 Nf3 Nc6 d4 exd4 Bc4 Nf6",
            "e4 e5 Nf3 Nc6 c3 Nf6 d4 Nxe4",
            "e4 e5 f4 exf4 Nf3 g5 h4 g4",
            "e4 e5 Nc3 Nf6 f4 d5 fxe5 Nxe4",
            "e4 e5 Bc4 Nf6 d3 c6",
            "e4 e5 d4 exd4 Qxd4 Nc6",
            "e4 e5 Nf3 Nc6 Bb5 a6 Bxc6 dxc6 O-O f6",
            "e4 e5 d4 exd4 c3 d5",
        ],
    ),
    "petrov": (
        "Petrov's Defence",
        "b",
        [
            "e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4 d4 d5 Bd3 Nc6 O-O Be7",
            "e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4 Nc3 Nxc3 dxc3 Be7",
            "e4 e5 Nf3 Nf6 d4 Nxe4 Bd3 d5 Nxe5 Nd7",
            "e4 e5 Nf3 Nf6 Nc3 Nc6",
            "e4 e5 Nf3 Nf6 Bc4 Nxe4",
            "e4 e5 Nf3 Nf6 d3 Nc6",
            "e4 e5 Nc3 Nf6",
            "e4 e5 Bc4 Nf6",
            "e4 e5 f4 exf4",
            "e4 e5 d4 exd4 Qxd4 Nc6",
        ],
    ),
    "sicilian-najdorf": (
        "Sicilian Defence, Najdorf",
        "b",
        [
            "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3 e5 Nb3 Be6 f3 Be7",
            "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Bg5 e6 f4 Be7 Qf3 Qc7",
            "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be2 e5 Nb3 Be7",
            "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Bc4 e6",
            "e4 c5 Nf3 d6 Bb5+ Bd7 Bxd7+ Qxd7",
            "e4 c5 Nf3 d6 c3 Nf6",
            "e4 c5 Nf3 d6 Bc4 Nf6",
            "e4 c5 Nf3 d6 Nc3 Nf6",
            "e4 c5 Nc3 Nc6 g3 g6 Bg2 Bg7",
            "e4 c5 c3 Nf6 e5 Nd5 d4 cxd4",
            "e4 c5 d4 cxd4 c3 dxc3 Nxc3 Nc6",
            "e4 c5 f4 d5",
            "e4 c5 Bc4 e6",
            "e4 c5 Nc3 Nc6 f4 g6",
            "e4 c5 Nf3 d6 d4 cxd4 Qxd4 Nc6",
        ],
    ),
    "sicilian-dragon": (
        "Sicilian Defence, Dragon",
        "b",
        [
            "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 g6 Be3 Bg7 f3 O-O Qd2 Nc6",
            "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 g6 Be2 Bg7 O-O O-O",
            "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 g6 Bc4 Bg7",
            "e4 c5 Nf3 d6 Bb5+ Bd7 Bxd7+ Qxd7",
            "e4 c5 Nf3 d6 c3 Nf6",
            "e4 c5 Nf3 d6 Nc3 Nf6",
            "e4 c5 Nf3 d6 Bc4 Nf6",
            "e4 c5 Nc3 Nc6 g3 g6 Bg2 Bg7",
            "e4 c5 c3 Nf6 e5 Nd5 d4 cxd4",
            "e4 c5 d4 cxd4 c3 dxc3 Nxc3 Nc6",
            "e4 c5 f4 d5",
            "e4 c5 Bc4 e6",
            "e4 c5 Nc3 Nc6 f4 g6",
        ],
    ),
    "french": (
        "French Defence",
        "b",
        [
            "e4 e6 d4 d5 Nc3 Nf6 Bg5 Be7 e5 Nfd7 Bxe7 Qxe7",
            "e4 e6 d4 d5 Nc3 Bb4 e5 c5 a3 Bxc3+ bxc3 Ne7",
            "e4 e6 d4 d5 Nc3 Nf6 e5 Nfd7 f4 c5 Nf3 Nc6",
            "e4 e6 d4 d5 e5 c5 c3 Nc6 Nf3 Qb6",
            "e4 e6 d4 d5 Nd2 Nf6 e5 Nfd7 Bd3 c5",
            "e4 e6 d4 d5 exd5 exd5 Nf3 Nf6",
            "e4 e6 d4 d5 exd5 exd5 Bd3 Bd6",
            "e4 e6 Nf3 d5",
            "e4 e6 d3 d5 Nd2 Nf6",
            "e4 e6 Nc3 d5",
            "e4 e6 Qe2 c5",
            "e4 e6 d4 d5 Bd3 dxe4 Bxe4 Nf6",
        ],
    ),
    "caro-kann": (
        "Caro-Kann Defence",
        "b",
        [
            "e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5 Ng3 Bg6 h4 h6 Nf3 Nd7",
            "e4 c6 d4 d5 e5 Bf5 Nf3 e6 Be2 c5",
            "e4 c6 d4 d5 exd5 cxd5 c4 Nf6 Nc3 e6",
            "e4 c6 d4 d5 exd5 cxd5 Bd3 Nc6 c3 Nf6",
            "e4 c6 d4 d5 Nd2 dxe4 Nxe4 Bf5",
            "e4 c6 Nc3 d5 Nf3 Bg4",
            "e4 c6 Nf3 d5",
            "e4 c6 d4 d5 f3 dxe4 fxe4 e5",
            "e4 c6 c4 d5",
            "e4 c6 d3 d5",
            "e4 c6 Bc4 d5",
        ],
    ),
    "scandinavian": (
        "Scandinavian Defence",
        "b",
        [
            "e4 d5 exd5 Qxd5 Nc3 Qa5 d4 Nf6 Nf3 c6 Bc4 Bf5",
            "e4 d5 exd5 Qxd5 Nf3 Nf6 d4 Bg4",
            "e4 d5 exd5 Qxd5 d4 Nf6",
            "e4 d5 e5 c5",
            "e4 d5 Nc3 d4",
            "e4 d5 d3 dxe4",
            "e4 d5 exd5 Qxd5 Nc3 Qa5 Nf3 Nf6",
            "e4 d5 exd5 Qxd5 Nc3 Qa5 Bc4 Nf6",
        ],
    ),
    "pirc": (
        "Pirc Defence",
        "b",
        [
            "e4 d6 d4 Nf6 Nc3 g6 Nf3 Bg7 Be2 O-O O-O c6",
            "e4 d6 d4 Nf6 Nc3 g6 f4 Bg7 Nf3 O-O",
            "e4 d6 d4 Nf6 Nc3 g6 Be3 Bg7 Qd2 c6",
            "e4 d6 d4 Nf6 Bd3 e5",
            "e4 d6 d4 Nf6 f3 e5",
            "e4 d6 Nf3 Nf6",
            "e4 d6 Nc3 Nf6",
            "e4 d6 f4 Nf6",
            "e4 d6 Bc4 Nf6",
        ],
    ),
    "qgd": (
        "Queen's Gambit Declined",
        "b",
        [
            "d4 d5 c4 e6 Nc3 Nf6 Bg5 Be7 e3 O-O Nf3 h6 Bh4 b6",
            "d4 d5 c4 e6 Nc3 Nf6 Nf3 Be7 Bf4 O-O e3 c5",
            "d4 d5 c4 e6 Nc3 Nf6 cxd5 exd5 Bg5 c6 e3 Be7",
            "d4 d5 c4 e6 Nf3 Nf6 g3 Be7 Bg2 O-O",
            "d4 d5 Nf3 Nf6 c4 e6",
            "d4 d5 Bf4 Nf6 e3 c5 c3 Nc6",
            "d4 d5 Nf3 Nf6 Bf4 c5 e3 Nc6",
            "d4 d5 e3 Nf6",
            "d4 d5 Nc3 Nf6 Bg5 Nbd7",
            "d4 d5 Bg5 h6",
            "d4 d5 e4 dxe4 Nc3 Nf6",
        ],
    ),
    "slav": (
        "Slav Defence",
        "b",
        [
            "d4 d5 c4 c6 Nf3 Nf6 Nc3 dxc4 a4 Bf5 e3 e6 Bxc4 Bb4",
            "d4 d5 c4 c6 Nf3 Nf6 e3 Bf5 Nc3 e6",
            "d4 d5 c4 c6 Nc3 Nf6 e3 e6 Nf3 Nbd7",
            "d4 d5 c4 c6 cxd5 cxd5 Nc3 Nf6 Nf3 Nc6",
            "d4 d5 Nf3 Nf6 c4 c6",
            "d4 d5 Bf4 c5 e3 Nc6",
            "d4 d5 Nf3 Nf6 Bf4 c5",
            "d4 d5 e3 Nf6",
            "d4 d5 Nc3 Nf6",
            "d4 d5 Bg5 h6",
        ],
    ),
    "kings-indian": (
        "King's Indian Defence",
        "b",
        [
            "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3 O-O Be2 e5 O-O Nc6 d5 Ne7",
            "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 f3 O-O Be3 e5",
            "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 f4 O-O Nf3 c5",
            "d4 Nf6 c4 g6 g3 Bg7 Bg2 O-O Nf3 d6",
            "d4 Nf6 Nf3 g6 c4 Bg7 Nc3 O-O",
            "d4 Nf6 Nf3 g6 Bf4 Bg7 e3 O-O",
            "d4 Nf6 Bf4 g6 e3 Bg7 Nf3 O-O",
            "d4 Nf6 Bg5 g6",
            "d4 Nf6 Nc3 g6",
            "d4 Nf6 e3 g6",
            "c4 Nf6 Nc3 g6",
            "Nf3 Nf6 c4 g6",
            "Nf3 Nf6 g3 g6",
        ],
    ),
    "nimzo-indian": (
        "Nimzo-Indian / Queen's Indian",
        "b",
        [
            "d4 Nf6 c4 e6 Nc3 Bb4 e3 O-O Bd3 d5 Nf3 c5 O-O Nc6",
            "d4 Nf6 c4 e6 Nc3 Bb4 Qc2 O-O a3 Bxc3+ Qxc3 b6",
            "d4 Nf6 c4 e6 Nc3 Bb4 Nf3 b6",
            "d4 Nf6 c4 e6 Nf3 b6 g3 Ba6 b3 Bb4+",
            "d4 Nf6 c4 e6 g3 d5 Bg2 Be7",
            "d4 Nf6 Nf3 e6 c4 b6",
            "d4 Nf6 Nf3 e6 Bf4 c5",
            "d4 Nf6 Bf4 e6 e3 c5",
            "d4 Nf6 Bg5 e6",
            "d4 Nf6 Nc3 d5",
            "d4 Nf6 e3 e6",
        ],
    ),
    "dutch": (
        "Dutch Defence",
        "b",
        [
            "d4 f5 g3 Nf6 Bg2 e6 Nf3 Be7 O-O O-O c4 d6",
            "d4 f5 c4 Nf6 Nc3 e6 Nf3 Bb4",
            "d4 f5 Nf3 Nf6 g3 g6 Bg2 Bg7 O-O O-O",
            "d4 f5 Bg5 g6",
            "d4 f5 e4 fxe4 Nc3 Nf6",
            "d4 f5 Nc3 Nf6 Bg5 d5",
            "d4 f5 Bf4 Nf6",
            "c4 f5",
            "Nf3 f5",
        ],
    ),
}


def choices() -> dict[str, list[dict]]:
    """What the new-game form offers, by the side the OPPONENT plays."""
    out: dict[str, list[dict]] = {"w": [], "b": []}
    for key, (name, side, _) in OPENINGS.items():
        out[side].append({"key": key, "name": name})
    return out


def name_of(key: str | None) -> str:
    return OPENINGS[key][0] if key in OPENINGS else "Engine's choice"


_BOOKS: dict[str, dict[str, set[str]]] = {}


def _book(key: str) -> dict[str, set[str]]:
    """Position (as its repetition key) -> the opponent's book moves there."""
    if key not in _BOOKS:
        _, side, lines = OPENINGS[key]
        book: dict[str, set[str]] = {}
        for line in lines:
            pos = Position.from_fen(START_FEN)
            for san in line.split():
                move = pos.parse(san)  # a typo in a line above fails here, and in the tests
                if pos.turn == side:
                    book.setdefault(pos.key(), set()).add(move)
                pos = pos.push(move)
        _BOOKS[key] = book
    return _BOOKS[key]


def book_move(key: str | None, pos: Position, rng: random.Random | None = None) -> str | None:
    """The opponent's book move in this position, if the chosen opening has one."""
    if key not in OPENINGS:
        return None
    moves = _book(key).get(pos.key())
    if not moves:
        return None
    legal = [m for m in sorted(moves) if m in pos.legal_moves()]
    return (rng or random).choice(legal) if legal else None
