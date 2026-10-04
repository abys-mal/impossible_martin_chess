"""Position adapter and rule hooks; no UI or clock behavior belongs here.

Future rules may transform/reject a MovePlan or supply a custom executor.
Custom executors use Position's piece methods to retain physical identities.
RuleLayer also owns move hints and terminal detection, so custom variants can
override standard-chess assumptions in one place.
"""
from dataclasses import dataclass, field
import random
from typing import Callable, Protocol

import chess


@dataclass
class PhysicalPiece:
    id: str
    color: chess.Color
    kind: chess.PieceType
    square: chess.Square | None
    promoted: bool = False
    last_action: int | None = None


@dataclass
class MovePlan:
    move: chess.Move
    accepted: bool = True
    message: str | None = None
    events: list[str] = field(default_factory=list)
    execute: Callable[["Position", "MovePlan"], None] | None = None
    notation: str | None = None


@dataclass
class RuleContext:
    actor: str
    human_color: chess.Color
    phase: str
    action_number: int
    last_human_piece_id: str | None
    rng: random.Random


class HandicapRule(Protocol):
    def before_move(self, position: "Position", plan: MovePlan,
                    context: RuleContext) -> MovePlan: ...
    def after_move(self, position: "Position", plan: MovePlan,
                   context: RuleContext) -> list[str]: ...


class Position:
    def __init__(self, fen: str | None = None):
        self.board = chess.Board(fen) if fen else chess.Board()
        self.pieces: dict[str, PhysicalPiece] = {}
        self.at: dict[chess.Square, str] = {}
        self.action_number = 0
        self.last_piece_id: str | None = None
        for square, piece in sorted(self.board.piece_map().items()):
            self.create_piece(square, piece.piece_type, piece.color,
                              promoted=bool(self.board.promoted & chess.BB_SQUARES[square]))

    def create_piece(self, square, kind, color, promoted=False):
        if square in self.at:
            self.remove_piece(square)
        identity = f"{'w' if color else 'b'}-{len(self.pieces) + 1:02d}"
        self.pieces[identity] = PhysicalPiece(identity, color, kind, square, promoted)
        self.at[square] = identity
        self.board.set_piece_at(square, chess.Piece(kind, color), promoted=promoted)
        return identity

    def remove_piece(self, square):
        identity = self.at.pop(square, None)
        if identity:
            self.pieces[identity].square = None
        self.board.remove_piece_at(square)

    def relocate_piece(self, source, target, promotion=None):
        """Custom-rule building block; update both the adapter and chess board."""
        identity = self.at[source]
        self.remove_piece(target)
        self.at.pop(source)
        piece = self.pieces[identity]
        piece.square = target
        piece.last_action = self.action_number
        if promotion:
            piece.kind, piece.promoted = promotion, True
        self.at[target] = identity
        self.board.remove_piece_at(source)
        self.board.set_piece_at(target, chess.Piece(piece.kind, piece.color),
                                promoted=piece.promoted)
        self.last_piece_id = identity

    def prepare_turn(self, color, consecutive=False):
        """Explicit same-side actions, including clearing stale en-passant rights."""
        self.board.turn = color
        if consecutive:
            self.board.ep_square = None
            self.board.clear_stack()

    def apply(self, plan: MovePlan):
        self.action_number += 1
        if plan.execute:
            plan.execute(self, plan)
            return
        move = plan.move
        board = self.board
        identity = self.at[move.from_square]
        moving = self.pieces[identity]
        capture_square = move.to_square
        if board.is_en_passant(move):
            capture_square += -8 if board.turn else 8
        castling = board.is_castling(move)
        # board.push is isolated here; track identity independently of the board.
        board.push(move)
        captured = self.at.pop(capture_square, None)
        if captured:
            self.pieces[captured].square = None
        self.at.pop(move.from_square)
        self.at[move.to_square] = identity
        moving.square, moving.last_action = move.to_square, self.action_number
        if move.promotion:
            moving.kind, moving.promoted = move.promotion, True
        if castling:
            rank = chess.square_rank(move.from_square)
            kingside = move.to_square > move.from_square
            rook_from = chess.square(7 if kingside else 0, rank)
            rook_to = chess.square(5 if kingside else 3, rank)
            rook_id = self.at.pop(rook_from)
            self.at[rook_to] = rook_id
            self.pieces[rook_id].square = rook_to
            self.pieces[rook_id].last_action = self.action_number
        self.last_piece_id = identity

    def visible_pieces(self):
        return [{"id": p.id, "square": chess.square_name(p.square),
                 "color": "white" if p.color else "black",
                 "type": chess.piece_symbol(p.kind), "promoted": p.promoted}
                for p in self.pieces.values() if p.square is not None]


class RuleLayer:
    def __init__(self, rules: list[HandicapRule] | None = None):
        self.rules = rules or []

    def validate(self, position: Position, move: chess.Move,
                 context: RuleContext) -> MovePlan:
        plan = MovePlan(move)
        for rule in self.rules:
            plan = rule.before_move(position, plan, context)
            if not plan.accepted:
                return plan
        if plan.execute is None and plan.move not in position.board.legal_moves:
            return MovePlan(plan.move, False, "That move is not legal in this position.")
        return plan

    def after_move(self, position, plan, context):
        events = list(plan.events)
        for rule in self.rules:
            events.extend(rule.after_move(position, plan, context))
        return events

    def human_move_hints(self, position, context):
        # Override with deterministic restrictions when adding handicaps. Never
        # consume gameplay RNG here: polling must not change game outcomes.
        return [move.uci() for move in position.board.legal_moves]

    def outcome(self, position):
        # Automatic draws only. Threefold/50-move draws require a claim button.
        return position.board.outcome(claim_draw=False)
