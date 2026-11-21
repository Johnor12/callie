"""The rules, checked against the published perft counts and a handful of notation and game-end cases."""

from tests import function_tests
from callie.chess import START_FEN, Game, Position, describe


def perft(pos: Position, depth: int) -> int:
    if depth == 0:
        return 1
    moves = pos.legal_moves()
    if depth == 1:
        return len(moves)
    return sum(perft(pos.push(m), depth - 1) for m in moves)


# https://www.chessprogramming.org/Perft_Results
PERFT = [
    (START_FEN, 3, 8902),
    ("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 3, 97862),  # "Kiwipete"
    ("8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", 4, 43238),  # en passant and pins
    ("r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1", 3, 9467),  # promotions
    ("rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", 3, 62379),
    ("r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10", 3, 89890),
]


def test_perft():
    for fen, depth, nodes in PERFT:
        assert perft(Position.from_fen(fen), depth) == nodes, fen


def test_fen_round_trip():
    for fen, _, _ in PERFT:
        assert Position.from_fen(fen).fen() == fen


def test_bad_fens_are_refused():
    for fen in ("", "8/8/8/8/8/8/8/8 w - - 0 1", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBN w KQkq - 0 1",
                "k7/8/8/8/8/8/8/K6P w - - 0 1", "k6R/8/8/8/8/8/8/K7 w - - 0 1"):  # fmt: skip
        try:
            Position.from_fen(fen)
        except ValueError:
            continue
        raise AssertionError(f"accepted {fen!r}")


def test_san_of_a_whole_game():
    # Morphy's Opera game: captures, castling long, checks, a mating rook.
    text = ("e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5 Bc4 Nf6 Qb3 Qe7 Nc3 c6 Bg5 b5 Nxb5 cxb5 Bxb5+ Nbd7 O-O-O Rd8 "
            "Rxd7 Rxd7 Rd1 Qe6 Bxd7+ Nxd7 Qb8+ Nxb8 Rd8#")  # fmt: skip
    g = Game()
    for san in text.split():
        assert g.push(g.position.parse(san)) == san
    assert g.outcome() == ("1-0", "checkmate")
    assert g.movetext().startswith("1. e4 e5 2. Nf3 d6") and g.movetext().endswith("17. Rd8#")


def test_san_disambiguation():
    pos = Position.from_fen("4k3/8/8/8/R6R/8/8/R3K3 w - - 0 1")
    assert pos.san("a1a3") == "R1a3" and pos.san("a4a3") == "R4a3"
    assert pos.san("a4d4") == "Rad4" and pos.san("h4d4") == "Rhd4"
    three = Position.from_fen("8/7k/8/Q7/8/8/8/Q1Q1K3 w - - 0 1")  # the a1 queen shares a file and a rank
    assert three.san("a1a3") == "Qa1a3"
    assert Position.from_fen("7k/P7/8/8/8/8/8/K7 w - - 0 1").san("a7a8q") == "a8=Q+"


def test_en_passant_and_castling_rights():
    g = Game(moves=["e2e4", "a7a6", "e4e5", "d7d5"])
    assert g.position.fen().split()[3] == "d6"
    assert g.push("e5d6") == "exd6" and g.position.board[35] is None  # the d5 pawn is gone
    g = Game(moves=["g1f3", "g8f6", "g2g3", "g7g6", "f1g2", "f8g7", "e1g1"])
    assert g.sans[-1] == "O-O" and g.position.castling == "kq"
    assert g.position.board[5] == "R" and g.position.board[6] == "K"


def test_pinned_piece_and_check_evasion():
    pos = Position.from_fen("4k3/8/8/8/1b6/2N5/8/4K3 w - - 0 1")
    assert not any(m.startswith("c3") for m in pos.legal_moves())  # the knight is pinned to the king
    assert Position.from_fen("4k3/8/8/8/8/8/3q4/4K3 w - - 0 1").legal_moves() == ["e1f1", "e1d2"]


def test_moves_made_ahead():
    # The side that is not to move: how its pieces move on an empty board, whatever stands there now.
    pre = Position.from_fen(START_FEN).premoves()  # Black's, with White to move
    assert {"e7e5", "e7e6", "e7d6", "g8f6", "g8e7", "f8a3", "d8d1", "e8e7", "e8g8", "e8c8"} <= set(pre)
    assert not {"e7e4", "e7e8", "e2e4", "g8g6", "a7h6"} & set(pre) and len(pre) == len(set(pre))
    pre = Position.from_fen("4k3/P7/8/8/8/8/8/4K2R b K - 0 1").premoves()  # White's: a pawn to promote, one castling
    assert {"a7a8q", "a7a8n", "a7b8r", "e1g1", "h1h8"} <= set(pre) and "e1c1" not in pre and "a7a8" not in pre


def test_draws():
    assert Game("8/8/8/8/8/5k2/5p2/5K2 w - - 0 1").outcome() == ("1/2-1/2", "stalemate")
    assert Game("8/8/8/8/8/5k2/8/5KB1 w - - 0 1").outcome() == ("1/2-1/2", "insufficient material")
    assert Game("8/8/8/8/8/5k2/8/5KR1 w - - 0 1").outcome() is None
    assert Game("8/8/8/8/8/5k2/8/5KR1 w - - 100 80").outcome() == ("1/2-1/2", "fifty-move rule")
    g = Game(moves=["g1f3", "g8f6", "f3g1", "f6g8"] * 2)
    assert g.outcome() == ("1/2-1/2", "threefold repetition")
    assert Game(moves=["g1f3", "g8f6", "f3g1", "f6g8"]).outcome() is None


def test_describe_and_pgn():
    text = describe(Position.from_fen(START_FEN))
    assert "White: Ke1 Qd1 Ra1 Rh1 Bc1 Bf1 Nb1 Ng1 a2" in text and "Legal moves (20)" in text
    pgn = Game(moves=["e2e4", "e7e5"]).pgn({"White": "You", "Black": "Stockfish"}, "*")
    assert '[White "You"]' in pgn and pgn.rstrip().endswith("1. e4 e5 *")


load_tests = function_tests(globals())
