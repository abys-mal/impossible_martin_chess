# Impossible Martin

A local Flask + python-chess experiment with a weak, noisy beginner opponent
and an aggressively unfair set of rules. No database, external engine, or remote
assets. Martin's nominal ~250 strength is a design target, not a measured Elo.

This project was inspired by this YouTube video: [https://www.youtube.com/watch?v=IqFPOly_Y48](https://www.youtube.com/watch?v=IqFPOly_Y48). It was programmed using Codex.

## Run

From this project directory:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

If activation is blocked, use `.\.venv\Scripts\python.exe app.py` directly.
Open **http://127.0.0.1:5001**. Set the `PORT` environment variable to use another
port. The app binds to loopback and is designed for one local Python process.

Click **Start game**, choose White or Black, and optionally supply a seed.
Click or drag to move. The board faces your side. New Game replaces the current
experiment; Resign concedes. Restarting Flask clears all in-memory games.

## The rules

| Rule | Human | Martin |
| --- | --- | --- |
| Opening | Waits for all three opening actions | Three consecutive actions: e-pawn two squares, queen to g-file, king to queen's home square |
| Pawns | One forward square; diagonal captures; never promote | Normal pawn geometry; promotes immediately on rank 7 (White) or rank 2 (Black) |
| Rooks, bishops, queens | Cannot land on a/h-files or ranks 1/8; may move away from an edge | Normal destinations |
| King | Frozen on e1/e8; no movement, capture, or castling | Normal king rules |
| Recovery | The physical non-knight piece just moved rests for your next turn | No recovery restriction |
| Knights | Ignore edges and recovery; 10% chance of a different legal destination for that same knight | Normal knights |
| Captures | An unprotected capture can be refused (8%); exact rejected move remains blocked this turn | Normal captures plus reverse en passant |
| Clock | 20 minutes; runs while thinking and during forced penalties | Unlimited |
| Speed | A completed move in less than 3.000 seconds forces a real 15-second wait | Waits until that penalty ends |

The usual opening is `e7e5, d8g5, e8d8` when Martin is Black, or
`e2e4, d1g4, e1d1` when Martin is White. Checks never give the human an intervening
opening turn. Actual handicap checkmate can end the game during the opening.

King safety remains active. To escape check, the human must capture or block
with an otherwise permitted piece. No legal response while in check means
checkmate. No permitted human move without check means **exhaustion**, not
stalemate. Merely having King + Rook does not lose; a resting last usable rook
when the next human turn arrives does. Knights do not incur recovery.

Human pawns arriving on the last rank remain physical pawns, with no promotion
dialog or suffix. python-chess may call those positions invalid; the adapter
keeps them intact. Martin normally promotes to a queen. All underpromotions are
included in the mandatory mate search; an equally mating queen is preferred.

After any completed human move, each horizontally adjacent Martin pawn can
capture that physical piece on Martin's immediately following action by moving
diagonally forward to the square beyond it. The destination must be empty and
Martin's king must remain safe. The victim need not be a pawn. The opportunity
expires after any Martin action. Reverse en passant can itself promote early
or deliver mate.

A capture is protected if Martin has a legal recapture after the proposed move,
including reverse en passant. Pinned defenders and unsafe king recaptures do not
count. Refusal is rolled once per attempted capture, before knight redirection.
Rejected captures never move pieces, start a penalty, reset the clock, or advance
recovery. The only remaining handicap-legal move always bypasses refusal.
An accidental knight capture is the final redirected action, not a second
player attempt. Blocked captures are excluded from redirection alternatives.

## Premoves and timing

During Martin's opening or ordinary turn, click or drag to queue **one** premove.
Another selection replaces it. Cancel with the button, Escape, or right-click.
Blue squares identify the queue. Known king/fatigue/recovery restrictions and
geometry are checked when queued; conditional pawn captures may target an empty
square. Full occupancy and king-safety validation happens after Martin moves.

The server records the intended physical piece. If Martin captures it, changes
the position so the action is illegal, or delivers mate, the premove does not
execute. A legal queue executes immediately on the server, including knight RNG
and capture refusal. A refused premove clears the queue and returns control with
the clock running. A successful premove always has recorded thinking time zero
and incurs the full penalty.

Clocks use monotonic server time and a background scheduler. Opening actions,
premoves, deadlines, and timeout enforcement work without browser polling. The
human clock starts immediately when Martin finishes action three, then resumes
after each bot action. Ordinary bot thinking is not charged. During the penalty,
all board and premove input is blocked; the human clock continues and Martin
cannot act. A late scheduler tick does not charge extra bot-thinking time.

Even a fast/premoved move that ends the game incurs its wait: the result is
pending until the penalty finishes. If time expires during that wait, timeout
takes precedence. Slow terminal moves resolve immediately. Resignation ends the
experiment; starting New Game abandons it and starts a separate experiment.

Muted pieces with a small `z` are resting. The king has a blue frozen marker.
Legal destinations, selected pieces, check, last actions, queued premoves, and
refused destinations have separate markings. Field notes explain unusual events;
move history records intended moves, actual actions, and premove tags.

## Implementation

| File | Responsibility |
| --- | --- |
| `app.py` | Flask API, signed browser sessions, locks, scheduler |
| `game.py` | Opening/human/bot/penalty phases, clock, premoves, results, history |
| `rules.py` | Actual action generation, validation, projection, variant outcomes, physical-piece adapter |
| `martin.py` | Mandatory variant mate scan, opening script, noisy one-ply policy |
| `static/game.js` | Render server state, input, clock estimates, queue controls |
| `templates/index.html`, `static/style.css` | Responsive interface and rule guide |

`get_human_legal_moves` and `get_martin_legal_moves` return `MovePlan` actions.
python-chess supplies pseudo-legal geometry and attacks; the adapter explicitly
tests king safety on the resulting action, strips human promotions, adds early
promotions, and represents off-square reverse captures. It never captures kings.
Neither `Board.legal_moves` nor `Board.is_checkmate` is authoritative for this
variant. SAN is annotated using actual outcomes; custom actions retain explicit
coordinate notation, promotion suffixes, and `r.e.p.` markers.

`Position` retains physical IDs through relocation, capture, castling, and
promotion. Copies include recovery, refusal, and reverse-capture state. Martin
projects every candidate without mutating the live position or consuming RNG,
then checks for an attacked human king with no handicap-legal response. Mating
actions override both opening scripts and weak heuristics. Outside that scan,
Martin uses only noisy one-ply captures, hanging-piece and pointless-move scores.

Each game uses **one shared `random.Random(seed)`** for bot choices, refusals,
knight malfunction, and alternative destinations. Hints, snapshots, deterministic
analysis, and queueing never consume gameplay RNG. The same seed and same player
actions reproduce random outcomes; think-time/timeout reproduction also requires
the same timing. Seeds are visible and can be supplied at New Game.

Future `before_move` hooks can transform/reject plans or supply custom executors;
`after_move` hooks produce events. Custom executors must update the piece adapter,
turn, castling rights, en-passant rights and move counters consistently. Extend
action generation as well as validation for any new variant mechanic so Martin's
mate scan and human exhaustion detection see exactly the same rules.

Automatic draws include Martin stalemate, 75 moves without a pawn move/capture,
and fivefold repetition. Repetition keys include recovery and reverse-capture
rights, with accounting starting after the opening. Standard insufficient-material
draws are deliberately disabled: a lone knight can mate a frozen king. There is
no 50-move/threefold claim UI.

## API

- `POST /api/new`: `{"color":"white","seed":31}` (seed optional).
- `GET /api/state`: current game state, or `{"game":null}`.
- `POST /api/move`: `{"move":"e2e3","game_id":"…","revision":7}`.
- `POST /api/premove`: `{"move":"g1f3","game_id":"…","target_turn":1}`;
  use `"move":null` to cancel. `target_turn` is `human_turn_index + 1`.
- `POST /api/resign`: `{"game_id":"…"}`.

Snapshots include board pieces/FEN, actual legal hints, premove hints and queue,
recovery ID, refused captures, phase, clock values, events, history, result and
seed. A rejected action returns HTTP 409 with `accepted:false` and current state.
Revisions stop duplicate/stale human moves; target-turn IDs stop stale queues.
Tabs in one browser session share one game. API responses are not cached.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The suite exercises both colors, clocks and deadline boundaries, all handicaps,
seeded malfunction/refusal, king-safety checks, physical identity, exhaustion,
promotion, reverse-capture expiry and safety, protected captures, premove lifecycle,
stale API requests, and mandatory handicap-aware mates (including early promotion
and reverse en passant). Timed live checks additionally verify the real wait and
the scheduler acting without polling. No Git operations are required.

Piece SVGs come from `chess.svg` (Chess Merida artwork, GPLv3+). See
https://github.com/niklasf/python-chess and
https://github.com/lichess-org/chessground/tree/master/public/piece/merida.
