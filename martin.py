"""A noisy, one-ply beginner bot. Strength is a design target, not rated Elo."""
from dataclasses import dataclass
import random

import chess
from rules import MovePlan, Position, RuleContext, RuleLayer


VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
          chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


@dataclass(frozen=True)
class BotChoice:
    move: chess.Move
    reason: str
    plan: MovePlan | None = None


@dataclass(frozen=True)
class BotConfig:
    reasonable_chance: float = 0.20
    blunder_chance: float = 0.45
    noise: float = 4.0


class Martin:
    def __init__(self, rng: random.Random, config: BotConfig | None = None):
        self.rng = rng
        self.config = config or BotConfig()

    @staticmethod
    def mating_moves(position: Position, actions: list[MovePlan], rules: RuleLayer,
                     context: RuleContext) -> list[MovePlan]:
        """Inspect EVERY available move before any script, heuristic, or RNG."""
        mates = []
        for plan in actions:
            probe = rules.project(position, plan, context)
            result = rules.outcome(probe, context)
            if result and result.status == "checkmate" and result.winner == "martin":
                mates.append(plan)
        return mates

    def choose(self, position: Position, rules: RuleLayer, context: RuleContext,
               opening_action: int | None = None) -> BotChoice:
        board = position.board
        actions = rules.get_martin_legal_moves(position, context)
        if not actions:
            raise ValueError("Martin has no available move")
        mates = self.mating_moves(position, actions, rules, context)
        if mates:
            queen_mates = {(p.move.from_square, p.move.to_square) for p in mates
                           if p.move.promotion == chess.QUEEN}
            preferred_mates = [p for p in mates if not p.move.promotion or
                               p.move.promotion == chess.QUEEN or
                               (p.move.from_square, p.move.to_square) not in queen_mates]
            plan = self.rng.choice(preferred_mates)
            return BotChoice(plan.move, "mate_in_one", plan)

        if opening_action is not None:
            rank = "1" if board.turn == chess.WHITE else "8"
            pawn_rank = "2" if board.turn == chess.WHITE else "7"
            advance_rank = "4" if board.turn == chess.WHITE else "5"
            # The e-pawn advance opens the diagonal to g4/g5. Qe2/e7 is
            # an alternate fallback if a modified position blocks that route.
            scripts = [
                [f"e{pawn_rank}e{advance_rank}"],
                [f"d{rank}g{'4' if board.turn else '5'}",
                 f"d{rank}g{'3' if board.turn else '6'}", f"d{rank}e{pawn_rank}"],
                [f"e{rank}d{rank}"],
            ]
            for uci in scripts[opening_action]:
                move = chess.Move.from_uci(uci)
                plan = next((plan for plan in actions if plan.move == move), None)
                if plan:
                    return BotChoice(move, "opening_script", plan)

        # Prefer queens, retaining underpromotions only when queen promotion
        # would stalemate the human. The mandatory mate scan already tried ALL.
        preferred = []
        for plan in actions:
            if plan.move.promotion and plan.move.promotion != chess.QUEEN:
                queen = next((other for other in actions if other.move.from_square == plan.move.from_square
                              and other.move.to_square == plan.move.to_square
                              and other.move.promotion == chess.QUEEN), None)
                if queen:
                    result = rules.outcome(rules.project(position, queen, context), context)
                    if result is None or result.status != "draw":
                        continue
            preferred.append(plan)

        mood = self.rng.random()
        reasonable = mood < self.config.reasonable_chance
        blundering = self.config.reasonable_chance <= mood < (
            self.config.reasonable_chance + self.config.blunder_chance)
        scores = []
        for plan in preferred:
            move = plan.move
            piece = board.piece_at(move.from_square)
            captured = board.piece_at(plan.capture_square if plan.capture_square is not None else move.to_square)
            capture_value = VALUES[captured.piece_type] if captured else (
                1 if board.is_en_passant(move) else 0)
            probe = rules.board_after(board, plan)
            hanging = probe.is_attacked_by(probe.turn, move.to_square)
            central = chess.square_file(move.to_square) in (3, 4)
            score = self.rng.gauss(0, self.config.noise)
            if reasonable:
                score += 2.3 * capture_value + 0.6 * central
                score -= VALUES[piece.piece_type] * hanging
                score += 0.6 * probe.is_check()
            elif blundering:
                score += 1.1 * VALUES[piece.piece_type] * hanging
                score -= 1.5 * capture_value
                score += 1.0 * (piece.piece_type in (chess.KING, chess.QUEEN))
            else:
                # Pointless pawn/king shuffles, with only a faint capture bias.
                score += 0.2 * capture_value + 0.5 * (piece.piece_type == chess.PAWN)
            if move.promotion:
                score += 1.0
            scores.append((score, plan))
        plan = max(scores, key=lambda item: item[0])[1]
        return BotChoice(plan.move, "beginner_blunder" if blundering else "beginner_move", plan)
