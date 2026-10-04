"""Authoritative game phases, monotonic clock, history, and rule orchestration."""
from collections import deque
import random
import secrets
import threading
import time
import uuid

import chess

from martin import Martin
from rules import Position, RuleContext, RuleLayer


HUMAN_SECONDS = 20 * 60
SPEED_THRESHOLD = 3.000
PENALTY_SECONDS = 15.0
OPENING_DELAY = 0.75
BOT_DELAY = 0.70


class Game:
    def __init__(self, human_color="white", seed=None, *, clock=time.monotonic,
                 rules=None, opening_delay=OPENING_DELAY, bot_delay=BOT_DELAY):
        self.id = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.clock = clock
        self.seed = secrets.randbits(53) if seed is None else seed
        self.human_color = chess.WHITE if human_color == "white" else chess.BLACK
        self.bot_color = not self.human_color
        # Separate streams prevent future handicap randomness changing bot choices.
        self.bot = Martin(random.Random(self.seed))
        self.rule_rng = random.Random(f"rules:{self.seed}")
        self.rules = rules or RuleLayer()
        self.position = Position()
        self.position.prepare_turn(self.bot_color, consecutive=True)
        self.phase = "opening"
        self.status = "playing"
        self.winner = None
        self.result = None
        self.opening_done = 0
        self.opening_delay, self.bot_delay = opening_delay, bot_delay
        self.due_at = clock() + opening_delay
        self.human_remaining = float(HUMAN_SECONDS)
        self.running_since = None
        self.human_turn_since = None
        self.last_human_piece_id = None
        self.last_human_elapsed = None
        self.history = []
        self.events = deque(maxlen=50)
        self.revision = 0
        self.last_seen = clock()
        self.event("opening", "Martin gets three consecutive opening moves. Your clock is paused.")

    def event(self, kind, message):
        self.events.append({"id": self.revision + 1, "kind": kind, "message": message})
        self.revision += 1

    def remaining(self, now):
        elapsed = now - self.running_since if self.running_since is not None else 0
        return max(0.0, self.human_remaining - elapsed)

    def stop_clock(self, now):
        self.human_remaining = self.remaining(now)
        self.running_since = None

    def finish(self, status, message, winner, now):
        self.stop_clock(now)
        self.status, self.phase = status, "finished"
        self.winner, self.result = winner, message
        self.due_at = None
        self.event("result", message)

    def check_timeout(self, now):
        if self.phase != "finished" and self.remaining(now) <= 0:
            self.finish("timeout", "Time's up. Martin wins.", "martin", now)
            return True
        return False

    def terminal(self, now):
        outcome = self.rules.outcome(self.position)
        if outcome is None:
            return False
        if outcome.winner is None:
            self.finish("draw", f"Draw: {outcome.termination.name.lower().replace('_', ' ')}.", None, now)
        else:
            winner = "human" if outcome.winner == self.human_color else "martin"
            self.finish("checkmate", "Checkmate. You win!" if winner == "human" else
                        "Checkmate. Martin wins.", winner, now)
        return True

    def context(self, actor):
        return RuleContext(actor, self.human_color, self.phase,
                           self.position.action_number + 1,
                           self.last_human_piece_id, self.rule_rng)

    def commit(self, plan, actor, reason=None):
        board = self.position.board
        context = self.context(actor)
        color = "white" if board.turn else "black"
        notation = plan.notation or (board.san(plan.move) if plan.execute is None
                                    else plan.move.uci())
        self.position.apply(plan)
        self.history.append({"action": len(self.history) + 1, "actor": actor,
                             "color": color, "uci": plan.move.uci(), "san": notation,
                             "phase": self.phase, "reason": reason,
                             "piece_id": self.position.last_piece_id})
        for message in self.rules.after_move(self.position, plan, context):
            self.event("rule", message)
        self.revision += 1

    def start_human_turn(self, now):
        self.position.prepare_turn(self.human_color)
        self.phase, self.due_at = "human", None
        self.running_since = self.human_turn_since = now
        self.revision += 1

    def tick(self, now=None):
        """Called under lock by BOTH the background scheduler and API requests.

        No browser timer is needed for deadlines, moves, or timeouts. If the
        scheduler runs late, penalty time is charged through its exact deadline;
        ordinary bot thinking is never charged to the human.
        """
        now = self.clock() if now is None else now
        if self.phase == "finished":
            return
        if self.phase == "penalty" and now >= self.due_at:
            deadline = self.due_at
            if self.check_timeout(deadline):
                return
            self.stop_clock(deadline)
            self.phase, self.due_at = "bot", deadline + self.bot_delay
            self.event("penalty_end", "Piece upright. Martin can move now.")
        if self.check_timeout(now):
            return
        if self.phase not in ("opening", "bot") or now < self.due_at:
            return

        opening = self.phase == "opening"
        self.position.prepare_turn(self.bot_color, consecutive=opening)
        # During the opening, checks do not hand the turn to the human. Mate is
        # still decisive; an actual checkmate ends the game immediately.
        if not list(self.position.board.legal_moves):
            self.terminal(now)
            return
        choice = self.bot.choose(self.position.board, self.opening_done if opening else None)
        plan = self.rules.validate(self.position, choice.move, self.context("martin"))
        if not plan.accepted:
            raise RuntimeError("A Martin rule rejected its own available move: " + str(plan.message))
        self.commit(plan, "martin", choice.reason)
        completed_at = self.clock()
        if self.terminal(completed_at):
            return
        if opening:
            self.opening_done += 1
            if self.opening_done < 3:
                self.position.prepare_turn(self.bot_color, consecutive=True)
                self.due_at = completed_at + self.opening_delay
                self.event("opening", f"Martin's opening: {self.opening_done}/3 actions complete.")
                return
            # Begin standard repetition accounting at the handicap baseline.
            self.position.board.clear_stack()
            self.event("opening_complete", "Three moves for Martin. Your turn; your 20-minute clock starts now.")
        self.start_human_turn(completed_at)

    def attempt_move(self, uci, game_id, revision):
        now = self.clock()
        self.tick(now)
        if game_id != self.id or revision != self.revision:
            return False, "The position changed. Try again on the updated board."
        if self.phase != "human":
            return False, "Please wait until it is your turn."
        if not isinstance(uci, str):
            return False, "Send a move such as e2e4."
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return False, "Invalid move coordinates. Use a move such as e2e4."
        piece = self.position.board.piece_at(move.from_square)
        if piece is None or piece.color != self.human_color:
            return False, "Choose one of your pieces."
        plan = self.rules.validate(self.position, move, self.context("human"))
        if not plan.accepted:
            return False, plan.message
        # Include validation time, since acceptance is the completion boundary.
        now = self.clock()
        if self.check_timeout(now):
            return False, "Time's up."
        self.last_human_elapsed = now - self.human_turn_since
        self.commit(plan, "human")
        self.last_human_piece_id = self.position.last_piece_id
        if self.terminal(now):
            return True, self.result
        if self.last_human_elapsed < SPEED_THRESHOLD:
            self.phase, self.due_at = "penalty", now + PENALTY_SECONDS
            self.event("penalty", "You moved too quickly and knocked over a piece!")
            # Keep the already-running human clock running during the real wait.
        else:
            self.stop_clock(now)
            self.phase, self.due_at = "bot", now + self.bot_delay
            self.revision += 1
        return True, None

    def resign(self):
        now = self.clock()
        self.tick(now)
        if self.phase != "finished":
            self.finish("resigned", "You resigned. Martin wins.", "martin", now)

    def snapshot(self, accepted=None, message=None):
        now = self.clock()
        self.tick(now)
        now = self.clock()
        last_bot = next((h["uci"] for h in reversed(self.history) if h["actor"] == "martin"), None)
        return {"game_id": self.id, "revision": self.revision, "seed": self.seed,
                "accepted": accepted, "message": message,
                "fen": self.position.board.fen(), "pieces": self.position.visible_pieces(),
                "phase": self.phase, "status": self.status,
                "human_color": "white" if self.human_color else "black",
                "turn": "human" if self.phase == "human" else
                        "martin" if self.phase in ("opening", "bot") else None,
                "human_time": self.remaining(now), "clock_running": self.running_since is not None,
                "penalty_remaining": max(0, self.due_at - now) if self.phase == "penalty" else 0,
                "opening_done": self.opening_done, "last_human_elapsed": self.last_human_elapsed,
                "in_check": self.position.board.is_check(),
                "legal_moves": self.rules.human_move_hints(self.position, self.context("human"))
                    if self.phase == "human" else [],
                "martin_move": last_bot, "move_history": self.history,
                "events": list(self.events), "winner": self.winner, "result": self.result}
