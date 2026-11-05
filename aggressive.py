"""
Aggressive chess move selector built on top of Stockfish.

Stockfish proposes candidate moves (MultiPV). Every candidate whose evaluation
stays within an allowed window of the best move is then re-scored for
"aggression":

  * sacrifices        - material we leave en prise / give away (SEE based)
  * traps             - how much better we do if the opponent plays the
                        *tempting* reply (grabs material, gives check) instead
                        of the best one
  * pressure          - how many opponent replies lose badly (few safe moves)
  * king attack       - extra attacks on the enemy king zone, checks,
                        pieces moving closer to the enemy king, pawn storms
  * anti-book         - in the opening, deviate from Stockfish's main move
  * style penalties   - retreats, queen trades

Sacrifices / traps are allowed to cost up to `sac_drop` centipawns
(default 200 = "-2 from current"), quiet "aggressive" moves only `quiet_drop`.
"""
from __future__ import annotations

import random
import shutil
from dataclasses import dataclass, field

import chess
import chess.engine

VALUES = {chess.PAWN: 100, chess.KNIGHT: 300, chess.BISHOP: 320,
          chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 20000}
MATE = 10000


@dataclass
class Config:
    root_time: float = 1.0        # seconds for the root MultiPV search
    multipv: int = 8              # Stockfish's top candidates considered
    extra_forcing: int = 10       # + this many checks / sacrifices / captures
    reply_depth: int = 10         # depth for analysing opponent replies
    reply_multipv: int = 40       # how many opponent replies to score
    sac_drop: int = 200           # max eval loss for sacrifices / traps (cp)
    quiet_drop: int = 70          # max eval loss for ordinary moves (cp)
    floor: int = -300             # never choose a move evaluated below this
    opening_plies: int = 16       # plies treated as "opening"
    # weights (bonus is in centipawn-like units)
    w_sac: float = 0.35
    w_trap: float = 0.45
    w_pressure: float = 80.0
    w_check: float = 25.0
    w_king_zone: float = 12.0
    w_king_dist: float = 6.0
    w_storm: float = 12.0
    w_threat: float = 0.06
    w_offbeat: float = 45.0
    w_retreat: float = -20.0
    w_queen_trade: float = -70.0
    noise: float = 8.0
    seed: int | None = None


@dataclass
class Candidate:
    move: chess.Move
    san: str
    rank: int
    cp: int
    sac: int = 0
    trap: float = 0.0
    pressure: float = 0.0
    bonus: dict = field(default_factory=dict)
    allowed: bool = True
    total: float = 0.0

    def describe(self) -> str:
        parts = " ".join(f"{k}={v:+.0f}" for k, v in self.bonus.items() if abs(v) >= 1)
        return (f"{self.san:<8} sf={self.cp:+5d} total={self.total:+7.1f} "
                f"{'' if self.allowed else '[out of window] '}{parts}")


# ---------------------------------------------------------------- helpers

def score_cp(score: chess.engine.PovScore, color: chess.Color) -> int:
    return score.pov(color).score(mate_score=MATE)


def least_valuable_capture(board: chess.Board, square: chess.Square):
    best, best_val = None, None
    for mv in board.legal_moves:
        if mv.to_square == square and board.is_capture(mv):
            val = VALUES[board.piece_type_at(mv.from_square)]
            if mv.promotion:
                val = VALUES[mv.promotion]
            if best_val is None or val < best_val:
                best, best_val = mv, val
    return best


def see(board: chess.Board, move: chess.Move) -> int:
    """Static exchange evaluation of a capture (gain for the side to move)."""
    if board.is_en_passant(move):
        captured = VALUES[chess.PAWN]
    else:
        piece = board.piece_type_at(move.to_square)
        captured = VALUES[piece] if piece else 0
    if move.promotion:
        captured += VALUES[move.promotion] - VALUES[chess.PAWN]
    board.push(move)
    reply = least_valuable_capture(board, move.to_square)
    gain = captured - (max(0, see(board, reply)) if reply else 0)
    board.pop()
    return gain


def max_capture_gain(board: chess.Board) -> int:
    """Best SEE capture available to the side to move (material on offer)."""
    best = 0
    for mv in board.legal_moves:
        if board.is_capture(mv):
            best = max(best, see(board, mv))
    return best


def king_zone(board: chess.Board, color: chess.Color) -> chess.SquareSet:
    k = board.king(color)
    if k is None:
        return chess.SquareSet()
    zone = chess.SquareSet(chess.BB_KING_ATTACKS[k]) | chess.SquareSet.from_square(k)
    step = 8 if color == chess.WHITE else -8      # squares in front of the king
    for sq in list(zone):
        t = sq + step
        if 0 <= t < 64 and abs(chess.square_file(t) - chess.square_file(sq)) == 0:
            zone.add(t)
    return zone


def zone_attacks(board: chess.Board, attacker: chess.Color) -> int:
    return sum(len(board.attackers(attacker, sq)) for sq in king_zone(board, not attacker))


def threatened_value(board: chess.Board, attacker: chess.Color) -> int:
    """Value of enemy pieces attacked by cheaper pieces or undefended."""
    total = 0
    for sq, piece in board.piece_map().items():
        if piece.color == attacker or piece.piece_type == chess.KING:
            continue
        atk = board.attackers(attacker, sq)
        if not atk:
            continue
        cheapest = min(VALUES[board.piece_type_at(a)] for a in atk)
        if cheapest < VALUES[piece.piece_type] or not board.attackers(piece.color, sq):
            total += VALUES[piece.piece_type]
    return total


def reply_plausibility(board: chess.Board, mv: chess.Move) -> float:
    """How 'tempting' a reply looks to a naive opponent."""
    w = 1.0
    if board.is_capture(mv):
        victim = VALUES[chess.PAWN] if board.is_en_passant(mv) else VALUES.get(board.piece_type_at(mv.to_square), 0)
        attacker = VALUES[board.piece_type_at(mv.from_square)]
        w += 3.0 + victim / 150.0
        if victim >= attacker:
            w += 2.0                      # looks like a free / good trade
    if board.gives_check(mv):
        w += 1.5
    if mv.promotion:
        w += 3.0
    return w


# ---------------------------------------------------------------- engine

class AggressiveEngine:
    def __init__(self, stockfish_path: str | None = None, config: Config | None = None,
                 threads: int = 4, hash_mb: int = 256):
        path = stockfish_path or shutil.which("stockfish")
        if not path:
            raise RuntimeError("stockfish not found in PATH")
        self.cfg = config or Config()
        self.rng = random.Random(self.cfg.seed)
        self.sf = chess.engine.SimpleEngine.popen_uci(path)
        self.sf.configure({"Threads": threads, "Hash": hash_mb})

    def close(self):
        self.sf.quit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- public ---------------------------------------------------------
    def choose(self, board: chess.Board, root_time: float | None = None):
        """Return (move, candidates) where candidates carry the reasoning."""
        cfg = self.cfg
        us = board.turn
        legal = list(board.legal_moves)
        if len(legal) == 1:
            c = Candidate(legal[0], board.san(legal[0]), 0, 0)
            return legal[0], [c]

        infos = self.sf.analyse(board, chess.engine.Limit(time=root_time or cfg.root_time),
                                multipv=min(cfg.multipv, len(legal)))
        cands = []
        for rank, info in enumerate(infos):
            if "pv" not in info:
                continue
            mv = info["pv"][0]
            cands.append(Candidate(mv, board.san(mv), rank, score_cp(info["score"], us)))
        best = cands[0]

        # Stockfish's top-N rarely includes speculative sacrifices, so search
        # the forcing / material-offering moves explicitly.
        seen = {c.move for c in cands}
        extra = [m for m in self._forcing_moves(board) if m not in seen][:cfg.extra_forcing]
        if extra and abs(best.cp) < MATE - 100:
            infos = self.sf.analyse(board, chess.engine.Limit(time=(root_time or cfg.root_time) * 0.6),
                                    multipv=len(extra), root_moves=extra)
            for info in infos:
                if "pv" in info and info["pv"][0] not in seen:
                    mv = info["pv"][0]
                    seen.add(mv)
                    cands.append(Candidate(mv, board.san(mv), len(cands), score_cp(info["score"], us)))

        # Forced mate for us, or we are completely lost: just play Stockfish.
        if best.cp >= MATE - 100 or best.cp <= -MATE + 100:
            return best.move, cands

        winning = best.cp > 600     # tone it down when the game is already won
        style = 0.4 if winning else 1.0

        before_zone = zone_attacks(board, us)
        self._hanging_before = 0
        if not board.is_check():
            board.push(chess.Move.null())
            self._hanging_before = max_capture_gain(board)
            board.pop()
        before_threat = threatened_value(board, us)
        enemy_king = board.king(not us)
        in_opening = board.ply() < cfg.opening_plies

        for c in cands:
            self._score(board, c, best, before_zone, before_threat,
                        enemy_king, in_opening, style)

        allowed = [c for c in cands if c.allowed] or [best]
        choice = max(allowed, key=lambda c: c.total)
        return choice.move, sorted(cands, key=lambda c: -c.total)

    # -- internals ------------------------------------------------------
    def _forcing_moves(self, board: chess.Board):
        """Checks, sacrifices and captures, most 'violent' first."""
        us = board.turn
        ek = board.king(not us)
        scored = []
        for mv in board.legal_moves:
            s = 0
            if board.gives_check(mv):
                s += 3
            if board.is_capture(mv):
                s += 1
            board.push(mv)
            offered = max_capture_gain(board) if not board.is_game_over() else 0
            board.pop()
            if offered >= 100:
                s += 2
                if ek is not None and chess.square_distance(mv.to_square, ek) <= 2:
                    s += 2
            if s:
                scored.append((s, mv))
        scored.sort(key=lambda t: -t[0])
        return [m for _, m in scored]

    def _score(self, board, c: Candidate, best: Candidate, before_zone, before_threat,
               enemy_king, in_opening, style):
        cfg = self.cfg
        us = board.turn
        mv = c.move
        piece = board.piece_at(mv.from_square)
        b = c.bonus

        captured = 0
        if board.is_capture(mv):
            captured = VALUES[chess.PAWN] if board.is_en_passant(mv) else VALUES[board.piece_type_at(mv.to_square)]
        is_check = board.gives_check(mv)
        queen_trade = (board.piece_type_at(mv.to_square) == chess.QUEEN and piece.piece_type == chess.QUEEN)

        board.push(mv)
        try:
            # material we leave on the table
            offered = 0 if board.is_game_over() else max_capture_gain(board)
            c.sac = max(0, offered - captured - self._hanging_before)
            if c.sac < 80:
                c.sac = 0

            # opponent replies: pressure + trap value vs a naive opponent
            c.trap, c.pressure = self._reply_profile(board, us, c.cp)

            after_zone = zone_attacks(board, us)
            after_threat = threatened_value(board, us)
        finally:
            board.pop()

        b["sf"] = min(max(c.cp, -1000), 1000)
        b["sac"] = cfg.w_sac * min(c.sac, 900) * style
        b["trap"] = cfg.w_trap * c.trap * style
        b["pressure"] = cfg.w_pressure * c.pressure * style
        b["check"] = cfg.w_check * is_check * style
        b["kzone"] = cfg.w_king_zone * (after_zone - before_zone) * style
        b["threat"] = cfg.w_threat * (after_threat - before_threat) * style

        if enemy_king is not None and piece.piece_type != chess.KING:
            d0 = chess.square_distance(mv.from_square, enemy_king)
            d1 = chess.square_distance(mv.to_square, enemy_king)
            b["kdist"] = cfg.w_king_dist * (d0 - d1) * style

        if piece.piece_type == chess.PAWN and enemy_king is not None:
            kf = chess.square_file(enemy_king)
            castled = kf <= 2 or kf >= 5
            near = abs(chess.square_file(mv.to_square) - kf) <= 1
            rel_rank = chess.square_rank(mv.to_square) if us == chess.WHITE else 7 - chess.square_rank(mv.to_square)
            if near and (castled or rel_rank >= 4) and not board.is_capture(mv):
                b["storm"] = cfg.w_storm * (1 + rel_rank / 4) * style

        rank_from = chess.square_rank(mv.from_square)
        rank_to = chess.square_rank(mv.to_square)
        forward = (rank_to - rank_from) if us == chess.WHITE else (rank_from - rank_to)
        if forward < 0 and not captured and piece.piece_type != chess.KING:
            b["retreat"] = cfg.w_retreat * style
        if queen_trade and best.cp < 300:
            b["qtrade"] = cfg.w_queen_trade

        if in_opening:
            if c.rank > 0:
                b["offbeat"] = cfg.w_offbeat
            # thematic flank thrusts: f4, g4, h4, b4 (and ...f5, ...g5 etc.)
            if (piece.piece_type == chess.PAWN and chess.square_distance(mv.from_square, mv.to_square) == 2
                    and chess.square_file(mv.from_square) in (1, 5, 6, 7)):
                b["wing"] = cfg.w_storm * 2
            b["noise"] = self.rng.uniform(-cfg.noise, cfg.noise) * 3
        else:
            b["noise"] = self.rng.uniform(-cfg.noise, cfg.noise)

        # eval window: sacrifices/traps may cost more than quiet moves
        drop = best.cp - c.cp
        speculative = c.sac >= 100 or c.trap >= 150
        limit = cfg.sac_drop if speculative else cfg.quiet_drop
        c.allowed = (c is best) or (drop <= limit and c.cp >= min(cfg.floor, best.cp - limit))
        c.total = sum(b.values())

    def _reply_profile(self, board: chess.Board, us: chess.Color, cand_cp: int):
        """Analyse all opponent replies. Returns (trap_gain_cp, pressure 0..1)."""
        cfg = self.cfg
        n = board.legal_moves.count()
        if n == 0:
            return 0.0, 0.0
        infos = self.sf.analyse(board, chess.engine.Limit(depth=cfg.reply_depth),
                                multipv=min(cfg.reply_multipv, n))
        replies = []
        for info in infos:
            if "pv" in info:
                replies.append((info["pv"][0], score_cp(info["score"], us)))
        if not replies:
            return 0.0, 0.0
        their_best = min(v for _, v in replies)       # best for them = worst for us
        bad = sum(1 for _, v in replies if v - their_best >= 200)
        unscored = n - len(replies)                    # worst replies weren't listed
        pressure = (bad + unscored) / n

        # expected outcome vs an opponent biased toward tempting moves
        weights = [reply_plausibility(board, mv) for mv, _ in replies]
        expected = sum(w * min(v, 1500) for w, (_, v) in zip(weights, replies)) / sum(weights)
        trap = max(0.0, min(expected - max(their_best, cand_cp - 50), 600.0))
        return trap, pressure


if __name__ == "__main__":
    import sys
    fen = " ".join(sys.argv[1:]) or chess.STARTING_FEN
    bd = chess.Board(fen)
    with AggressiveEngine() as eng:
        move, cands = eng.choose(bd)
        for c in cands:
            print(c.describe())
        print("bestmove", bd.san(move), move.uci())
