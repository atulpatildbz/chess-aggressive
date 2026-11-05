#!/usr/bin/env python3
"""Local web board to play against the aggressive engine.

    .venv/bin/python server.py        # then open http://localhost:8000
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import chess

from aggressive import AggressiveEngine

HERE = Path(__file__).resolve().parent
ENGINE = AggressiveEngine()
LOCK = threading.Lock()


def state(board: chess.Board) -> dict:
    replay = chess.Board()
    sans = []
    for mv in board.move_stack:
        sans.append(replay.san(mv))
        replay.push(mv)
    over = board.is_game_over(claim_draw=True)
    return {
        "fen": board.fen(),
        "turn": "w" if board.turn else "b",
        "legal": [m.uci() for m in board.legal_moves],
        "san": sans,
        "check": board.is_check(),
        "over": over,
        "result": board.result(claim_draw=True) if over else None,
        "last": board.move_stack[-1].uci() if board.move_stack else None,
    }


def board_from(moves) -> chess.Board:
    board = chess.Board()
    for m in moves:
        board.push_uci(m)
    return board


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, (HERE / "board.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path.startswith("/pieces/") and (HERE / self.path[1:]).resolve().parent == HERE / "pieces":
            f = HERE / self.path[1:]
            if f.is_file():
                self._send(200, f.read_bytes(), "image/svg+xml")
            else:
                self._send(404, {"error": "not found"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            board = board_from(req.get("moves", []))
            if self.path == "/api/state":
                self._send(200, state(board))
            elif self.path == "/api/engine":
                if board.is_game_over(claim_draw=True):
                    self._send(200, {**state(board), "engine": None})
                    return
                with LOCK:
                    move, cands = ENGINE.choose(board, root_time=float(req.get("time", 1.0)))
                chosen = next((c for c in cands if c.move == move), None)
                info = {
                    "move": move.uci(),
                    "san": board.san(move),
                    "eval": chosen.cp if chosen else None,
                    "best_eval": max((c.cp for c in cands), default=None),
                    "bonus": {k: round(v) for k, v in (chosen.bonus.items() if chosen else [])
                              if k not in ("sf", "noise") and abs(v) >= 5},
                    "candidates": [{"san": c.san, "eval": c.cp, "score": round(c.total),
                                    "allowed": c.allowed} for c in cands[:6]],
                }
                board.push(move)
                self._send(200, {**state(board), "engine": info})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:  # bad move list etc.
            self._send(400, {"error": str(e)})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("Aggressive engine board: http://localhost:8000")
    try:
        srv.serve_forever()
    finally:
        ENGINE.close()
