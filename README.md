# Impossible Martin

A local Flask chess experiment. You get 20 minutes; Martin gets unlimited time,
three consecutive opening actions, and perfect mate-in-one recognition.
Everything runs locally, including the SVG pieces. No database or external engine.

## Run on Windows

From this project directory, activate the existing environment and install dependencies:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

If PowerShell blocks activation, use the interpreter directly:

```powershell
.\.venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5001. Port 5001 avoids another local chess app already using
5000; set the `PORT` environment variable to change it. Click **Start game**, choose your color, optionally enter
a seed, and start. Click or drag pieces; promotion offers all four choices.
Use **New game** to replace the current game, or **Resign** to concede.

## Implemented behavior

- Martin plays three consecutive actions before your first turn. Standard initial
  positions yield `e7e5, d8g5, e8d8` (Martin Black) or `e2e4, d1g4, e1d1`
  (Martin White). The pawn advance opens the queen's diagonal to the g-file;
  Qe7/Qe2 is an alternate fallback if a modified position blocks that route.
- Checks do not give you a response between opening actions. Actual checkmate
  ends the game. Repetition accounting begins after the handicap opening.
- Your clock starts after Martin completes action three and resumes immediately
  after each subsequent bot move. It pauses during ordinary bot thinking.
- A legal, nonterminal human move completed in **less than 3.000 seconds** incurs
  a real **15-second wait**. Your clock keeps running; board input is blocked and
  Martin cannot move until the wait finishes. Exactly 3.000 seconds is safe.
  A move that ends the game resolves the result immediately.
- Clocks use monotonic server time. The scheduler enforces moves and deadlines
  even when the tab is hidden or closed. Invalid attempts never reset the clock.
  Browser countdowns are a display estimate corrected from the server.
- Martin checks **every legal move** for mate in one before any script or RNG.
  Otherwise he uses noisy capture, hanging-piece, and pointless-move heuristics,
  with no search beyond one ply. His approximate beginner strength is not an
  independently measured 250 Elo rating and is not Chess.com's actual Martin.
- Checkmate, resignation, timeout, stalemate, insufficient material, fivefold
  repetition, and the 75-move rule end games. No draw-claim UI in this milestone.
- The displayed seed reproduces bot choices given the same human moves. The bot
  and handicap layer use separate random streams; polling consumes no RNG.

## Structure and extension points

| File | Responsibility |
| --- | --- |
| `app.py` | Flask routes, per-browser in-memory sessions, locking, scheduler |
| `game.py` | Phase machine, clocks, opening, penalty, history, game results |
| `martin.py` | Mandatory mate scan, opening choices, tunable weak-move policy |
| `rules.py` | Position adapter, persistent physical piece IDs, move hooks |
| `static/game.js` | Display, click/drag input, polling, promotion selection |
| `templates/index.html`, `static/style.css` | Responsive interface |

`RuleLayer.validate()` sends a `MovePlan` through `before_move` hooks, then checks
standard legality unless the rule supplies a custom executor. `after_move` hooks
return events. `Position` keeps an independent registry of physical piece IDs,
including captured pieces, promotions, castling rooks, and last action numbers.
Custom executors can use `relocate_piece`, `remove_piece`, and `create_piece`
to synchronize the chess board and registry. They must also manage turn,
castling/en-passant rights, and any custom repetition semantics. Override
`human_move_hints()` and `outcome()` for variants outside standard chess. Move
hints must be deterministic and must not consume gameplay RNG. Extend Martin's
available-move/probe adapter when adding nonstandard bot moves.

Consecutive opening actions explicitly select Martin's side and clear stale
en-passant state. After the opening, ordinary turns use python-chess. Handicaps
listed in the original brief beyond opening and speed penalties are extension
points, not enabled rules.

## API

- `POST /api/new`: `{"color":"white","seed":48291}` (seed optional).
- `GET /api/state`: current state or `{"game":null}`.
- `POST /api/move`: `{"move":"e2e4","game_id":"…","revision":7}`.
- `POST /api/resign`: `{"game_id":"…"}`.

Responses include phase, FEN, pieces, server clock, penalty countdown, legal-move
hints, history, events, result, and seed. Move rejection returns HTTP 409 with
`accepted:false`, a reason, and the current state. Revisions prevent duplicate or
stale moves. Games are isolated by signed browser-session cookies; tabs in the
same browser session share a game. Restarting Flask clears all games. This app
binds to loopback and is intended for a single local process, not production.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use a controllable clock to exercise exact threshold/deadline boundaries,
opening colors, mate priority, timeouts, identity tracking, custom executors,
API validation, stale actions, and browser-session isolation.

Piece SVGs are generated by `python-chess` (`chess.svg`) from its embedded
Chess Merida artwork, distributed under GPLv3+. See
https://github.com/niklasf/python-chess and https://github.com/lichess-org/chessground/tree/master/public/piece/merida.
