# Aggressive Chess Engine

A chess engine that uses Stockfish for analysis but chooses the most **aggressive**
move it can justify: sacrifices, traps, king attacks, and offbeat openings.

## How it picks a move

1. Stockfish suggests its top 8 moves (MultiPV), and the engine also has it score up
   to 10 extra checks, captures and sacrifices that Stockfish would normally rank too low to show.
2. Each candidate gets bonuses for:
   - **Sacrifices**: material left en prise (static exchange evaluation).
   - **Traps**: how much better we do if the opponent plays the *tempting* reply
     (grabbing material, giving check) instead of the best one.
   - **Pressure**: how many opponent replies lose badly.
   - **King attack**: checks, attacks on the king zone, pieces approaching the king, pawn storms.
   - **Offbeat openings**: in the first 8 moves, bonuses for avoiding Stockfish's
     main move and for flank thrusts (b4, f4, g4, h4).
   - Penalties for retreats and queen trades.
3. A sacrifice or trap may cost up to **2 pawns** versus the best move (`sac_drop`).
   An ordinary move may cost only 0.7 (`quiet_drop`). When it's already winning by more
   than 6 pawns, the bonuses are toned down so it converts the win, and forced mates are always played.

All weights live in `Config` in `aggressive.py`.

## Setup

Requires [Stockfish](https://stockfishchess.org/) on your `PATH` (`brew install stockfish`).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Usage

| Command | What it does |
|---|---|
| `.venv/bin/python server.py` | Web board at http://localhost:8000: play the engine, with reasoning shown for each move |
| `.venv/bin/python bestmove.py e4 e5 Nf3 -v` | Print the engine's move for a given game |
| `.venv/bin/python uci.py` | UCI engine for any chess GUI (Cute Chess, Arena, ...) |
| `.venv/bin/python match.py --games 4 --elo 1500` | Test match vs strength-limited Stockfish, saved to PGN |

You can open the web board on a game in progress: `http://localhost:8000/#b:e2e4,e7e5` (you play Black).

`local_test.pgn` has two sample wins against Stockfish limited to 1600 Elo.

## Credits

Piece images: "cburnett" set by Colin M.L. Burnett, from the [Lichess](https://github.com/lichess-org/lila)
repository (GPLv2+).
