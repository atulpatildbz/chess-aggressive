#!/usr/bin/env python3
"""UCI front-end so the aggressive engine can be loaded in any chess GUI."""
import sys

import chess

from aggressive import AggressiveEngine, Config


def main():
    cfg = Config()
    eng = None
    board = chess.Board()
    out = lambda s: (sys.stdout.write(s + "\n"), sys.stdout.flush())

    for line in sys.stdin:
        tok = line.split()
        if not tok:
            continue
        cmd = tok[0]
        if cmd == "uci":
            out("id name Aggressive (Stockfish-based)")
            out("id author chess-aggressive")
            out("option name SacDrop type spin default 200 min 0 max 1000")
            out("option name QuietDrop type spin default 70 min 0 max 500")
            out("uciok")
        elif cmd == "setoption" and "name" in tok and "value" in tok:
            name = tok[tok.index("name") + 1].lower()
            val = int(tok[tok.index("value") + 1])
            if name == "sacdrop":
                cfg.sac_drop = val
            elif name == "quietdrop":
                cfg.quiet_drop = val
        elif cmd == "isready":
            eng = eng or AggressiveEngine(config=cfg)
            out("readyok")
        elif cmd == "ucinewgame":
            board = chess.Board()
        elif cmd == "position":
            if tok[1] == "startpos":
                board = chess.Board()
                rest = tok[2:]
            else:
                i = tok.index("moves") if "moves" in tok else len(tok)
                board = chess.Board(" ".join(tok[2:i]))
                rest = tok[i:]
            if rest and rest[0] == "moves":
                for m in rest[1:]:
                    board.push_uci(m)
        elif cmd == "go":
            eng = eng or AggressiveEngine(config=cfg)
            t = cfg.root_time
            args = dict(zip(tok[1::2], tok[2::2]))
            if "movetime" in args:
                t = int(args["movetime"]) / 1000 / 3       # the rest goes to reply analysis
            elif "wtime" in args:
                left = int(args["wtime" if board.turn else "btime"]) / 1000
                t = max(0.1, min(cfg.root_time, left / 120))
            move, cands = eng.choose(board, root_time=t)
            for c in cands[:5]:
                out("info string " + c.describe())
            out(f"bestmove {move.uci()}")
        elif cmd == "quit":
            break
    if eng:
        eng.close()


if __name__ == "__main__":
    main()
