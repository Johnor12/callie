"""The rules of chess: positions, legal moves, FEN, SAN, and how a game ends. No dependencies.

Squares are numbered a1 = 0 ... h8 = 63. Pieces are FEN letters (upper case is White). A move is its UCI string
("e2e4", "e7e8q"): that is what the engine speaks and what the database stores.
"""

from __future__ import annotations

FILES = "abcdefgh"
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
PIECE_NAMES = {"K": "king", "Q": "queen", "R": "rook", "B": "bishop", "N": "knight", "P": "pawn"}
PIECE_VALUES = {"P": 1, "N": 3, "B": 3, "R": 5, "Q": 9, "K": 0}

_ORTHOGONAL = ((1, 0), (-1, 0), (0, 1), (0, -1))
_DIAGONAL = ((1, 1), (1, -1), (-1, 1), (-1, -1))
_KNIGHT_STEPS = ((1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2))
# A king or rook that leaves one of these squares, or a rook captured on one, ends these castling rights.
_RIGHTS_LOST = {4: "KQ", 0: "Q", 7: "K", 60: "kq", 56: "q", 63: "k"}


def square(name: str) -> int:
    return FILES.index(name[0]) + 8 * (int(name[1]) - 1)


def square_name(s: int) -> str:
    return FILES[s & 7] + str((s >> 3) + 1)


def _tables():
    knight, king, rays = [], [], []
    for s in range(64):
        f, r = s & 7, s >> 3

        def hops(steps):
            return [(f + df) + 8 * (r + dr) for df, dr in steps if 0 <= f + df < 8 and 0 <= r + dr < 8]

        knight.append(hops(_KNIGHT_STEPS))
        king.append(hops(_ORTHOGONAL + _DIAGONAL))
        per_dir = []
        for df, dr in _ORTHOGONAL + _DIAGONAL:
            ray, x, y = [], f + df, r + dr
            while 0 <= x < 8 and 0 <= y < 8:
                ray.append(x + 8 * y)
                x, y = x + df, y + dr
            per_dir.append(ray)
        rays.append(per_dir)
    return knight, king, rays


_KNIGHT, _KING, _RAYS = _tables()  # _RAYS[s][0:4] are the orthogonal rays, [4:8] the diagonal ones
_SLIDES = {"R": range(0, 4), "B": range(4, 8), "Q": range(0, 8)}


def parse_uci(move: str) -> tuple[int, int, str | None]:
    if not (4 <= len(move) <= 5) or move[0] not in FILES or move[2] not in FILES:
        raise ValueError(f"not a move: {move!r}")
    if move[1] not in "12345678" or move[3] not in "12345678" or (len(move) == 5 and move[4] not in "qrbn"):
        raise ValueError(f"not a move: {move!r}")
    return square(move[:2]), square(move[2:4]), (move[4] if len(move) == 5 else None)


class Position:
    __slots__ = ("board", "turn", "castling", "ep", "halfmove", "fullmove", "_legal", "_key")

    def __init__(self, board, turn, castling, ep, halfmove, fullmove):
        self.board: list[str | None] = board
        self.turn: str = turn  # "w" or "b"
        self.castling: str = castling  # some of "KQkq", in that order
        self.ep: int | None = ep  # the square a pawn that just advanced two passed over
        self.halfmove: int = halfmove  # plies since a capture or a pawn move
        self.fullmove: int = fullmove
        self._legal: list[str] | None = None
        self._key: str | None = None

    # ---------- FEN ----------

    @classmethod
    def from_fen(cls, fen: str = START_FEN) -> Position:
        parts = fen.split()
        if len(parts) < 4:
            raise ValueError(f"not a FEN (needs placement, side, castling, en passant): {fen!r}")
        rows = parts[0].split("/")
        if len(rows) != 8:
            raise ValueError(f"FEN placement needs 8 ranks: {fen!r}")
        board: list[str | None] = [None] * 64
        for i, row in enumerate(rows):
            f, r = 0, 7 - i
            for ch in row:
                if ch.isdigit():
                    f += int(ch)
                elif ch.upper() in PIECE_NAMES and f < 8:
                    board[f + 8 * r] = ch
                    f += 1
                else:
                    raise ValueError(f"bad FEN placement {row!r}")
            if f != 8:
                raise ValueError(f"FEN rank {row!r} does not have 8 squares")
        if board.count("K") != 1 or board.count("k") != 1:
            raise ValueError("a position needs exactly one king of each colour")
        if any(p in ("P", "p") for p in board[:8] + board[56:]):
            raise ValueError("a pawn cannot stand on the first or last rank")
        turn = parts[1]
        if turn not in ("w", "b"):
            raise ValueError(f"FEN side to move must be w or b, not {turn!r}")
        # Rights are kept only where the king and rook still stand on their squares.
        homes = {"K": (4, 7, "K", "R"), "Q": (4, 0, "K", "R"), "k": (60, 63, "k", "r"), "q": (60, 56, "k", "r")}
        castling = "".join(
            c
            for c in "KQkq"
            if c in parts[2] and board[homes[c][0]] == homes[c][2] and board[homes[c][1]] == homes[c][3]
        )
        ep = None
        if parts[3] != "-":
            try:
                ep = square(parts[3])
            except (ValueError, IndexError):
                raise ValueError(f"bad en passant square {parts[3]!r}") from None
            # Only kept when the pawn that made the double step is really there.
            behind, rank, pawn = (ep - 8, 5, "p") if turn == "w" else (ep + 8, 2, "P")
            if ep >> 3 != rank or board[behind] != pawn or board[ep] is not None:
                ep = None
        try:
            halfmove = int(parts[4]) if len(parts) > 4 else 0
            fullmove = int(parts[5]) if len(parts) > 5 else 1
        except ValueError:
            raise ValueError(f"bad FEN move counters in {fen!r}") from None
        pos = cls(board, turn, castling, ep, halfmove, max(1, fullmove))
        if pos.attacked(pos.king("b" if turn == "w" else "w"), turn):
            raise ValueError("the side that just moved is in check")
        return pos

    def placement(self) -> str:
        rows = []
        for r in range(7, -1, -1):
            row, empty = "", 0
            for f in range(8):
                p = self.board[f + 8 * r]
                if p is None:
                    empty += 1
                else:
                    row += (str(empty) if empty else "") + p
                    empty = 0
            rows.append(row + (str(empty) if empty else ""))
        return "/".join(rows)

    def fen(self) -> str:
        ep = square_name(self.ep) if self.ep is not None else "-"
        return f"{self.placement()} {self.turn} {self.castling or '-'} {ep} {self.halfmove} {self.fullmove}"

    def key(self) -> str:
        """What makes two positions "the same" for the repetition rule: the en passant square only counts when
        a capture there is legal."""
        if self._key is None:
            ep = "-"
            if self.ep is not None:
                target = square_name(self.ep)
                if any(m[2:4] == target and self.board[square(m[:2])] in ("P", "p") for m in self.legal_moves()):
                    ep = target
            self._key = f"{self.placement()} {self.turn} {self.castling or '-'} {ep}"
        return self._key

    # ---------- attacks ----------

    def king(self, color: str) -> int:
        return self.board.index("K" if color == "w" else "k")

    def attackers(self, s: int, by: str) -> list[int]:
        """The squares of every piece of colour ``by`` that attacks square ``s``."""
        b, white = self.board, by == "w"
        pawn, knight, king = ("P", "N", "K") if white else ("p", "n", "k")
        rook, bishop, queen = ("R", "B", "Q") if white else ("r", "b", "q")
        out, f = [], s & 7
        # A white pawn attacks the two squares diagonally above it, so it is found diagonally below ``s``.
        for df in (-1, 1):
            q = s + df + (-8 if white else 8)
            if 0 <= f + df < 8 and 0 <= q < 64 and b[q] == pawn:
                out.append(q)
        out += [q for q in _KNIGHT[s] if b[q] == knight]
        out += [q for q in _KING[s] if b[q] == king]
        for d, ray in enumerate(_RAYS[s]):
            for q in ray:
                p = b[q]
                if p is not None:
                    if p == queen or p == (rook if d < 4 else bishop):
                        out.append(q)
                    break
        return out

    def attacked(self, s: int, by: str) -> bool:
        b, white = self.board, by == "w"
        pawn, knight, king = ("P", "N", "K") if white else ("p", "n", "k")
        rook, bishop, queen = ("R", "B", "Q") if white else ("r", "b", "q")
        f = s & 7
        for df in (-1, 1):
            q = s + df + (-8 if white else 8)
            if 0 <= f + df < 8 and 0 <= q < 64 and b[q] == pawn:
                return True
        for q in _KNIGHT[s]:
            if b[q] == knight:
                return True
        for q in _KING[s]:
            if b[q] == king:
                return True
        for d, ray in enumerate(_RAYS[s]):
            for q in ray:
                p = b[q]
                if p is not None:
                    if p == queen or p == (rook if d < 4 else bishop):
                        return True
                    break
        return False

    def in_check(self) -> bool:
        return self.attacked(self.king(self.turn), "b" if self.turn == "w" else "w")

    # ---------- moves ----------

    def _pseudo(self):
        """Every move that obeys how the pieces move, whether or not it leaves the mover's king in check."""
        b, white = self.board, self.turn == "w"
        them = "b" if white else "w"
        for frm in range(64):
            p = b[frm]
            if p is None or p.isupper() != white:
                continue
            kind = p.upper()
            if kind == "P":
                f, r = frm & 7, frm >> 3
                step = 8 if white else -8
                promotes = r == (6 if white else 1)
                to = frm + step
                if b[to] is None:
                    if promotes:
                        for promo in "qrbn":
                            yield frm, to, promo
                    else:
                        yield frm, to, None
                        if r == (1 if white else 6) and b[to + step] is None:
                            yield frm, to + step, None
                for df in (-1, 1):
                    if not 0 <= f + df < 8:
                        continue
                    to = frm + step + df
                    t = b[to]
                    if (t is not None and t.isupper() != white) or (t is None and to == self.ep):
                        if promotes:
                            for promo in "qrbn":
                                yield frm, to, promo
                        else:
                            yield frm, to, None
            elif kind == "N" or kind == "K":
                for to in (_KNIGHT if kind == "N" else _KING)[frm]:
                    t = b[to]
                    if t is None or t.isupper() != white:
                        yield frm, to, None
                if kind == "K":
                    # Castling: rights held, the squares between empty, and the king not in, through or into check.
                    home, ks, qs, rook = (4, "K", "Q", "R") if white else (60, "k", "q", "r")
                    if frm != home or not (ks in self.castling or qs in self.castling):
                        continue
                    if self.attacked(home, them):
                        continue
                    if ks in self.castling and b[home + 3] == rook and b[home + 1] is None and b[home + 2] is None:
                        if not self.attacked(home + 1, them) and not self.attacked(home + 2, them):
                            yield frm, home + 2, None
                    if qs in self.castling and b[home - 4] == rook and b[home - 1] is None and b[home - 2] is None:
                        if (
                            b[home - 3] is None
                            and not self.attacked(home - 1, them)
                            and not self.attacked(home - 2, them)
                        ):
                            yield frm, home - 2, None
            else:
                rays = _RAYS[frm]
                for d in _SLIDES[kind]:
                    for to in rays[d]:
                        t = b[to]
                        if t is None:
                            yield frm, to, None
                        else:
                            if t.isupper() != white:
                                yield frm, to, None
                            break

    def _make(self, frm: int, to: int, promo: str | None) -> Position:
        b = self.board[:]
        p, target, white = b[frm], b[to], self.turn == "w"
        kind = p.upper()
        b[frm] = None
        ep = None
        if kind == "P":
            if to == self.ep and target is None:
                b[to + (-8 if white else 8)] = None  # en passant: the captured pawn is beside, not on, the square
            elif abs(to - frm) == 16:
                ep = (frm + to) // 2
            if promo:
                p = promo.upper() if white else promo.lower()
        elif kind == "K" and abs(to - frm) == 2:
            if to > frm:
                b[to - 1], b[to + 1] = b[to + 1], None
            else:
                b[to + 1], b[to - 2] = b[to - 2], None
        b[to] = p
        castling = self.castling
        if castling:
            lost = _RIGHTS_LOST.get(frm, "") + _RIGHTS_LOST.get(to, "")
            if lost:
                castling = "".join(c for c in castling if c not in lost)
        halfmove = 0 if kind == "P" or target is not None else self.halfmove + 1
        return Position(b, "b" if white else "w", castling, ep, halfmove, self.fullmove + (0 if white else 1))

    def legal_moves(self) -> list[str]:
        if self._legal is None:
            out, me = [], self.turn
            for frm, to, promo in self._pseudo():
                nxt = self._make(frm, to, promo)
                if not nxt.attacked(nxt.king(me), nxt.turn):
                    out.append(square_name(frm) + square_name(to) + (promo or ""))
            self._legal = out
        return self._legal

    def premoves(self) -> list[str]:
        """Every move the side that is not to move might have once the other has moved: how each of its pieces
        moves on an empty board, since what stands in the way of one, or on its square, may be gone by then.
        Castling only with the right to it, which a move cannot give back."""
        white = self.turn == "b"
        out = []
        for frm, p in enumerate(self.board):
            if p is None or p.isupper() != white:
                continue
            kind, f, r = p.upper(), frm & 7, frm >> 3
            promos: tuple[str, ...] = ("",)
            if kind == "P":
                step = 8 if white else -8
                tos = [frm + step] + [frm + step + df for df in (-1, 1) if 0 <= f + df < 8]
                if r == (1 if white else 6):
                    tos.append(frm + 2 * step)
                elif r == (6 if white else 1):
                    promos = tuple("qrbn")
            elif kind == "N":
                tos = _KNIGHT[frm]
            elif kind == "K":
                home, ks, qs = (4, "K", "Q") if white else (60, "k", "q")
                tos = _KING[frm] + [home + side for right, side in ((ks, 2), (qs, -2)) if right in self.castling]
            else:
                tos = [to for d in _SLIDES[kind] for to in _RAYS[frm][d]]
            out += [square_name(frm) + square_name(to) + promo for to in tos for promo in promos]
        return out

    def push(self, move: str) -> Position:
        """The position after a legal move."""
        if move not in self.legal_moves():
            raise ValueError(f"illegal move {move} in {self.fen()}")
        return self._make(*parse_uci(move))

    def is_capture(self, move: str) -> bool:
        frm, to, _ = parse_uci(move)
        return self.board[to] is not None or (self.board[frm] in ("P", "p") and to == self.ep)

    # ---------- notation ----------

    def san(self, move: str) -> str:
        """Standard algebraic notation of a legal move, with its check or mate mark."""
        frm, to, promo = parse_uci(move)
        legal = self.legal_moves()
        if move not in legal:
            raise ValueError(f"illegal move {move} in {self.fen()}")
        p = self.board[frm]
        kind = p.upper()
        if kind == "K" and abs(to - frm) == 2:
            s = "O-O" if to > frm else "O-O-O"
        elif kind == "P":
            s = (move[0] + "x" if self.is_capture(move) else "") + move[2:4] + ("=" + promo.upper() if promo else "")
        else:
            # Told apart from another piece of the same kind that could go there: by file, else rank, else both.
            rivals = [m for m in legal if m[2:4] == move[2:4] and m[:2] != move[:2] and self.board[square(m[:2])] == p]
            where = ""
            if rivals:
                if all(m[0] != move[0] for m in rivals):
                    where = move[0]
                elif all(m[1] != move[1] for m in rivals):
                    where = move[1]
                else:
                    where = move[:2]
            s = kind + where + ("x" if self.board[to] is not None else "") + move[2:4]
        nxt = self._make(frm, to, promo)
        if nxt.in_check():
            s += "+" if nxt.legal_moves() else "#"
        return s

    def parse(self, text: str) -> str:
        """A legal move from what was typed: SAN ("Nf3", "exd5", "O-O", "e8=Q") or UCI ("g1f3")."""
        text = text.strip()
        legal = self.legal_moves()
        if text.lower() in legal:
            return text.lower()
        want = _bare(text)
        hits = [m for m in legal if _bare(self.san(m)) == want]
        if len(hits) != 1:
            raise ValueError(f"{text!r} is {'ambiguous' if hits else 'not a legal move'} in {self.fen()}")
        return hits[0]

    def line_san(self, moves: list[str], numbered: bool = True) -> str:
        """A sequence of moves from here as text: "12. Nf3 Nc6 13. Bb5" (or "12... Nc6 13. Bb5")."""
        pos, out = self, []
        for i, m in enumerate(moves):
            if numbered and (pos.turn == "w" or i == 0):
                out.append(f"{pos.fullmove}." if pos.turn == "w" else f"{pos.fullmove}...")
            out.append(pos.san(m))
            pos = pos._make(*parse_uci(m))
        return " ".join(out)

    # ---------- how it ends ----------

    def insufficient_material(self) -> bool:
        """Neither side can mate: bare kings, one minor piece, or bishops that all stand on one colour."""
        minors = []
        for s, p in enumerate(self.board):
            if p is None or p in "Kk":
                continue
            if p.upper() in "PRQ":
                return False
            minors.append((p.upper(), ((s & 7) + (s >> 3)) & 1))
        if len(minors) <= 1:
            return True
        return all(k == "B" for k, _ in minors) and len({c for _, c in minors}) == 1

    def material(self) -> dict[str, int]:
        """Points on the board per side (pawn 1, minor 3, rook 5, queen 9)."""
        out = {"w": 0, "b": 0}
        for p in self.board:
            if p is not None:
                out["w" if p.isupper() else "b"] += PIECE_VALUES[p.upper()]
        return out


def _bare(san: str) -> str:
    return san.replace("+", "").replace("#", "").replace("!", "").replace("?", "").replace("0", "O").replace("=", "")


class Game:
    """A start position and the moves played from it: what the repetition rule and the notation need."""

    def __init__(self, start_fen: str = START_FEN, moves: list[str] | tuple[str, ...] = ()):
        self.positions = [Position.from_fen(start_fen)]
        self.moves: list[str] = []
        self.sans: list[str] = []
        for m in moves:
            self.push(m)

    @property
    def position(self) -> Position:
        return self.positions[-1]

    def push(self, move: str) -> str:
        """Play a legal move (UCI) and return its SAN."""
        pos = self.position
        san = pos.san(move)  # raises on an illegal move
        self.positions.append(pos._make(*parse_uci(move)))
        self.moves.append(move)
        self.sans.append(san)
        return san

    def outcome(self) -> tuple[str, str] | None:
        """(result, reason) once the game is over by the rules: "1-0", "0-1" or "1/2-1/2"."""
        pos = self.position
        if not pos.legal_moves():
            if pos.in_check():
                return ("0-1" if pos.turn == "w" else "1-0"), "checkmate"
            return "1/2-1/2", "stalemate"
        if pos.insufficient_material():
            return "1/2-1/2", "insufficient material"
        if pos.halfmove >= 100:
            return "1/2-1/2", "fifty-move rule"
        key = pos.key()
        if pos.halfmove >= 8 and sum(1 for p in self.positions if p.key() == key) >= 3:
            return "1/2-1/2", "threefold repetition"
        return None

    def movetext(self) -> str:
        return self.positions[0].line_san(self.moves)

    def pgn(self, headers: dict[str, str], result: str = "*") -> str:
        tags = {"Event": "Callie game", "Site": "?", "Date": "????.??.??", "Round": "-"}
        tags.update(headers)
        tags["Result"] = result
        if self.positions[0].fen() != START_FEN:
            tags["SetUp"], tags["FEN"] = "1", self.positions[0].fen()
        head = "\n".join(f'[{k} "{v}"]' for k, v in tags.items())
        words, lines, line = (self.movetext() + " " + result).split(), [], ""
        for w in words:
            if len(line) + len(w) + 1 > 80:
                lines.append(line)
                line = w
            else:
                line = f"{line} {w}".strip()
        return head + "\n\n" + "\n".join(lines + [line]) + "\n"


def describe(pos: Position) -> str:
    """A position in words, for a reader that cannot see the board: a diagram, each side's pieces, what is legal."""
    rows = []
    for r in range(7, -1, -1):
        rows.append(f"{r + 1}  " + " ".join(pos.board[f + 8 * r] or "." for f in range(8)))
    rows.append("   a b c d e f g h")
    order = "KQRBNP"

    def pieces(white: bool) -> str:
        found = [(order.index(p.upper()), s) for s, p in enumerate(pos.board) if p is not None and p.isupper() == white]
        return " ".join(("" if order[k] == "P" else order[k]) + square_name(s) for k, s in sorted(found))

    legal = pos.legal_moves()
    mat = pos.material()
    side = "White" if pos.turn == "w" else "Black"
    state = (
        "checkmated"
        if pos.in_check() and not legal
        else "stalemated"
        if not legal
        else "in check"
        if pos.in_check()
        else ""
    )
    out = [
        f"FEN: {pos.fen()}",
        *rows,
        f"White: {pieces(True)}",
        f"Black: {pieces(False)}",
        f"Material: White {mat['w']}, Black {mat['b']} ({mat['w'] - mat['b']:+d} for White)",
        f"{side} to move" + (f", {state}" if state else "") + f". Castling rights: {pos.castling or 'none'}.",
    ]
    if legal:
        out.append(f"Legal moves ({len(legal)}): " + " ".join(sorted(pos.san(m) for m in legal)))
    return "\n".join(out)
