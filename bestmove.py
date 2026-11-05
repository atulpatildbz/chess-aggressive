#!/usr/bin/env python3
"""Print the aggressive move for a game given as SAN moves (or a FEN).

    python bestmove.py e4 e5 Nf3 Nc6
    python bestmove.py --fen "<fen>"
"""
import argparse

import chess

from aggressive import AggressiveEngine

ap = argparse.ArgumentParser()
ap.add_argument("moves", nargs="*")
ap.add_argument("--fen")
ap.add_argument("--time", type=float, default=1.0)
ap.add_argument("-v", "--verbose", action="store_true")
a = ap.parse_args()

board = chess.Board(a.fen) if a.fen else chess.Board()
for m in a.moves:
    board.push_san(m)

with AggressiveEngine() as eng:
    move, cands = eng.choose(board, root_time=a.time)
    if a.verbose:
        for c in cands[:6]:
            print("  ", c.describe())
    print(board.san(move), move.uci())
