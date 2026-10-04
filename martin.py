"""A noisy, one-ply beginner bot. Strength is a design target, not rated Elo."""
from dataclasses import dataclass
import random

import chess


VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
          chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


@dataclass(frozen=True)
class BotChoice:
    move: chess.Move
    reason: str


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
    def mating_moves(board: chess.Board, moves: list[chess.Move]) -> list[chess.Move]:
        """Inspect EVERY available move before any script, heuristic, or RNG."""
        mates = []
        for move in moves:
            probe = board.copy(stack=False)
            probe.push(move)
            if probe.is_checkmate():
                mates.append(move)
        return mates

    def choose(self, board: chess.Board, opening_action: int | None = None) -> BotChoice:
        moves = list(board.legal_moves)
        if not moves:
            raise ValueError("Martin has no available move")
        mates = self.mating_moves(board, moves)
        if mates:
            return BotChoice(self.rng.choice(mates), "mate_in_one")

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
                if move in moves:
                    return BotChoice(move, "opening_script")

        mood = self.rng.random()
        reasonable = mood < self.config.reasonable_chance
        blundering = self.config.reasonable_chance <= mood < (
            self.config.reasonable_chance + self.config.blunder_chance)
        scores = []
        for move in moves:
            piece = board.piece_at(move.from_square)
            captured = board.piece_at(move.to_square)
            capture_value = VALUES[captured.piece_type] if captured else (
                1 if board.is_en_passant(move) else 0)
            probe = board.copy(stack=False)
            probe.push(move)
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
            scores.append((score, move))
        return BotChoice(max(scores, key=lambda item: item[0])[1],
                         "beginner_blunder" if blundering else "beginner_move")
