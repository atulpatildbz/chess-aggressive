#!/usr/bin/env python3
"""Play the aggressive engine against strength-limited Stockfish.

    python match.py --games 4 --elo 1500
"""
import argparse
import datetime
import shutil

import chess
import chess.engine
import chess.pgn

from aggressive import AggressiveEngine


def play(agg, opp, agg_white, opp_time, log):
    board = chess.Board()
    game = chess.pgn.Game()
    game.headers.update(Event="Aggressive test", Date=datetime.date.today().isoformat(),
                        White="Aggressive" if agg_white else "Stockfish",
                        Black="Stockfish" if agg_white else "Aggressive")
    node = game
    while not board.is_game_over(claim_draw=True) and board.ply() < 300:
        if board.turn == agg_white:
            move, cands = agg.choose(board)
            chosen = next(c for c in cands if c.move == move)
            comment = f"sf={chosen.cp:+d} " + " ".join(f"{k}={v:+.0f}" for k, v in chosen.bonus.items()
                                                       if k not in ("sf", "noise") and abs(v) >= 5)
            if log:
                print(f"{board.fullmove_number}{'.' if board.turn else '...'} {chosen.describe()}")
        else:
            move = opp.play(board, chess.engine.Limit(time=opp_time)).move
            comment = ""
        node = node.add_variation(move, comment=comment)
        board.push(move)
    game.headers["Result"] = board.result(claim_draw=True)
    return game


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=2)
    ap.add_argument("--elo", type=int, default=1500, help="opponent Stockfish UCI_Elo")
    ap.add_argument("--opp-time", type=float, default=0.1)
    ap.add_argument("--pgn", default="games.pgn")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    opp = chess.engine.SimpleEngine.popen_uci(shutil.which("stockfish"))
    opp.configure({"UCI_LimitStrength": True, "UCI_Elo": a.elo})
    score = 0.0
    with AggressiveEngine() as agg, open(a.pgn, "a") as f:
        for g in range(a.games):
            game = play(agg, opp, g % 2 == 0, a.opp_time, not a.quiet)
            res = game.headers["Result"]
            white = game.headers["White"] == "Aggressive"
            pts = {"1-0": 1.0 if white else 0.0, "0-1": 0.0 if white else 1.0}.get(res, 0.5)
            score += pts
            print(f"game {g + 1}: {game.headers['White']} vs {game.headers['Black']} {res} "
                  f"({board_len(game)} moves)  running score {score}/{g + 1}")
            print(game, file=f, end="\n\n")
    opp.quit()


def board_len(game):
    return (game.end().ply() + 1) // 2


if __name__ == "__main__":
    main()
