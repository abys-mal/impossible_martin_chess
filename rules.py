"""Authoritative variant actions, physical pieces, projections, and rule hooks.

python-chess provides geometry and attacks; RuleLayer defines actual legality.
Clock phases and UI behavior belong elsewhere. Analysis and hints never roll RNG.
"""
from collections import Counter
from dataclasses import dataclass, field, replace
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
    kind: str = "standard"
    capture_square: chess.Square | None = None
    intended_uci: str | None = None


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
        self.resting_piece_id: str | None = None
        self.refused_captures: set[str] = set()
        self.reverse_ep: list[dict] = []
        self.repetitions = Counter()
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

    def copy(self):
        clone = object.__new__(Position)
        clone.board = self.board.copy(stack=False)
        clone.pieces = {key: replace(piece) for key, piece in self.pieces.items()}
        clone.at = dict(self.at)
        clone.action_number = self.action_number
        clone.last_piece_id = self.last_piece_id
        clone.resting_piece_id = self.resting_piece_id
        clone.refused_captures = set(self.refused_captures)
        clone.reverse_ep = [dict(opportunity) for opportunity in self.reverse_ep]
        clone.repetitions = self.repetitions.copy()
        return clone

    def apply(self, plan: MovePlan):
        self.action_number += 1
        if plan.execute:
            plan.execute(self, plan)
            return
        move = plan.move
        board = self.board
        identity = self.at[move.from_square]
        moving = self.pieces[identity]
        capture_square = plan.capture_square if plan.capture_square is not None else move.to_square
        if plan.capture_square is None and board.is_en_passant(move):
            capture_square += -8 if board.turn else 8
        castling = board.is_castling(move)
        if plan.kind == "reverse_ep":
            board.remove_piece_at(capture_square)
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


@dataclass(frozen=True)
class VariantOutcome:
    status: str
    winner: str | None
    message: str


class RuleLayer:
    """Deterministic action generation, then stochastic attempt-time rules.

    No random numbers are consumed by hints, premove validation, projection, or
    terminal/mate detection. Only real human attempts and Martin choices roll.
    """
    KNIGHT_CHANCE = 0.10
    REFUSAL_CHANCE = 0.08

    def __init__(self, rules: list[HandicapRule] | None = None):
        self.rules = rules or []

    @staticmethod
    def attacked(board, color):
        king = board.king(color)
        return king is None or board.is_attacked_by(not color, king)

    @staticmethod
    def edge(square):
        return chess.square_file(square) in (0, 7) or chess.square_rank(square) in (0, 7)

    @staticmethod
    def board_after(board, plan):
        probe = board.copy(stack=False)
        if plan.kind == "reverse_ep":
            probe.remove_piece_at(plan.capture_square)
        # push never adds an implicit promotion. The action generator explicitly
        # strips human promotions and adds Martin's early promotion suffix.
        probe.push(plan.move)
        return probe

    def human_restriction(self, position, move, context):
        piece = position.board.piece_at(move.from_square)
        if piece is None or piece.color != context.human_color:
            return "Choose one of your pieces."
        if piece.piece_type == chess.KING:
            return "Your king cannot move or castle."
        if piece.piece_type == chess.PAWN:
            if move.promotion:
                return "Your pawns do not promote. Move to the last rank without choosing a piece."
            if abs(chess.square_rank(move.to_square) - chess.square_rank(move.from_square)) == 2:
                return "Your pawn is tired: only one square forward."
        if piece.piece_type in (chess.ROOK, chess.BISHOP, chess.QUEEN) and self.edge(move.to_square):
            return f"That square is too close to the edge for your tired {chess.piece_name(piece.piece_type)}."
        if piece.piece_type != chess.KNIGHT and position.at.get(move.from_square) == position.resting_piece_id:
            return f"Your {chess.piece_name(piece.piece_type)} is tired and must rest this turn."
        if move.uci() in position.refused_captures:
            return "Martin already refused that capture. Choose a different move this turn."
        return None

    def get_human_legal_moves(self, position, context, *, ignore_refusals=False, premove=False):
        board = position.board.copy(stack=False)
        board.turn = context.human_color
        if premove:
            board.ep_square = None
        geometry = list(board.generate_pseudo_legal_moves())
        if premove:
            # A conditional diagonal pawn capture may target a currently empty
            # square. Occupancy and king safety are rechecked after Martin moves.
            for source in board.pieces(chess.PAWN, context.human_color):
                for target in board.attacks(source):
                    if board.piece_at(target) is None:
                        geometry.append(chess.Move(source, target))
        actions, seen = [], set()
        for original in geometry:
            piece = board.piece_at(original.from_square)
            move = chess.Move(original.from_square, original.to_square) if (
                piece.piece_type == chess.PAWN) else original
            if move.uci() in seen:
                continue
            seen.add(move.uci())
            target = board.piece_at(move.to_square)
            if target and target.piece_type == chess.KING:
                continue
            # Ignore refused moves only for diagnostic/no-refusal baseline use.
            restriction = self.human_restriction(position, move, context)
            if restriction and not (ignore_refusals and move.uci() in position.refused_captures
                                    and restriction.startswith("Martin already")):
                continue
            plan = MovePlan(move, intended_uci=move.uci())
            if premove or not self.attacked(self.board_after(board, plan), context.human_color):
                actions.append(plan)
        return actions

    def get_martin_legal_moves(self, position, context):
        board = position.board.copy(stack=False)
        color = not context.human_color
        board.turn = color
        actions = []
        for move in board.generate_pseudo_legal_moves():
            target = board.piece_at(move.to_square)
            if target and target.piece_type == chess.KING:
                continue
            piece = board.piece_at(move.from_square)
            early_rank = 6 if color else 1
            is_early = piece.piece_type == chess.PAWN and chess.square_rank(move.to_square) == early_rank
            if is_early:
                # Include underpromotions so mate-in-one and stalemate avoidance
                # can inspect every actual action. Ordinary policy prefers queen.
                variants = [chess.Move(move.from_square, move.to_square, kind)
                            for kind in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT)]
            else:
                variants = [move]
            for variant in variants:
                plan = MovePlan(variant, kind="early_promotion" if is_early else "standard")
                if not self.attacked(self.board_after(board, plan), color):
                    actions.append(plan)
        for opportunity in position.reverse_ep:
            pawn = position.pieces.get(opportunity["pawn_id"])
            victim = position.pieces.get(opportunity["victim_id"])
            if not pawn or not victim or pawn.square is None or victim.square is None:
                continue
            if pawn.kind != chess.PAWN or pawn.color != color or victim.color == color:
                continue
            if chess.square_rank(pawn.square) != chess.square_rank(victim.square) or abs(
                    chess.square_file(pawn.square) - chess.square_file(victim.square)) != 1:
                continue
            target = victim.square + (8 if color else -8)
            if not 0 <= target < 64 or board.piece_at(target) is not None:
                continue
            promotion = chess.QUEEN if chess.square_rank(target) == (6 if color else 1) else None
            promotions = (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT) if promotion else (None,)
            for kind in promotions:
                plan = MovePlan(chess.Move(pawn.square, target, kind), kind="reverse_ep",
                                capture_square=victim.square)
                if not self.attacked(self.board_after(board, plan), color):
                    actions.append(plan)
        return actions

    def project(self, position, plan, context):
        probe = position.copy()
        probe.apply(plan)
        self.after_move(probe, plan, context)
        return probe

    def unprotected_capture(self, position, plan, context):
        board = position.board
        victim = board.piece_at(plan.move.to_square)
        if victim is None and board.is_en_passant(plan.move):
            victim_square = plan.move.to_square + (-8 if context.human_color else 8)
            victim = board.piece_at(victim_square)
        if victim is None or victim.color == context.human_color:
            return False
        # A pinned defender or an illegal king recapture is not protection.
        probe = self.project(position, plan, context)
        bot_context = replace(context, actor="martin")
        return not any(candidate.move.to_square == plan.move.to_square or
                       candidate.capture_square == plan.move.to_square
                       for candidate in self.get_martin_legal_moves(probe, bot_context))

    def validate(self, position, move, context):
        plan = MovePlan(move, intended_uci=move.uci())
        for rule in self.rules:
            plan = rule.before_move(position, plan, context)
            if not plan.accepted:
                return plan
        if plan.execute is not None:
            return plan
        if context.actor == "martin":
            return next((candidate for candidate in self.get_martin_legal_moves(position, context)
                         if candidate.move == move), MovePlan(move, False, "That Martin action is not legal."))
        restriction = self.human_restriction(position, move, context)
        if restriction:
            return MovePlan(move, False, restriction)
        legal = self.get_human_legal_moves(position, context)
        plan = next((candidate for candidate in legal if candidate.move == move), None)
        if plan is None:
            return MovePlan(move, False, "That move is not legal or would leave your frozen king in check.")
        # Refusal precedes knight malfunction. A denied attempt changes only its
        # turn-local refusal record; no piece, recovery state, or clock is reset.
        if len(legal) > 1 and self.unprotected_capture(position, plan, context):
            if context.rng.random() < self.REFUSAL_CHANCE:
                position.refused_captures.add(move.uci())
                return MovePlan(move, False, "Martin says no. You can't take that piece.")
        piece = position.board.piece_at(move.from_square)
        if piece.piece_type == chess.KNIGHT:
            alternatives = [candidate for candidate in legal if candidate.move.from_square == move.from_square
                            and candidate.move.to_square != move.to_square]
            if context.rng.random() < self.KNIGHT_CHANCE and alternatives:
                redirected = context.rng.choice(alternatives)
                redirected.intended_uci = move.uci()
                redirected.events.append("Your knight got confused!")
                return redirected
        return plan

    def validate_premove(self, position, move, context):
        restriction = self.human_restriction(position, move, context)
        if restriction:
            return restriction
        if not any(plan.move == move for plan in self.get_human_legal_moves(position, context, premove=True)):
            return "That premove is not geometrically possible for this piece."
        return None

    def after_move(self, position, plan, context):
        events = list(plan.events)
        piece = position.pieces[position.last_piece_id]
        if context.actor == "human":
            position.resting_piece_id = piece.id if piece.kind not in (chess.KNIGHT, chess.KING) else None
            position.refused_captures.clear()
            position.reverse_ep = []
            for file_offset in (-1, 1):
                file = chess.square_file(piece.square) + file_offset
                if not 0 <= file < 8:
                    continue
                square = chess.square(file, chess.square_rank(piece.square))
                neighbor_id = position.at.get(square)
                neighbor = position.pieces.get(neighbor_id)
                if neighbor and neighbor.color != context.human_color and neighbor.kind == chess.PAWN:
                    position.reverse_ep.append({"pawn_id": neighbor.id, "victim_id": piece.id})
            if piece.kind == chess.PAWN and chess.square_rank(piece.square) in (0, 7):
                events.append("Your pawn reached the end... and remains a pawn.")
        else:
            position.reverse_ep = []
            if plan.kind == "reverse_ep":
                events.append("Reverse en passant! Martin took the piece beside his pawn.")
            if plan.move.promotion and chess.square_rank(piece.square) in (1, 6):
                events.append("Martin promoted early!")
        for rule in self.rules:
            events.extend(rule.after_move(position, plan, context))
        return events

    def human_move_hints(self, position, context):
        return [plan.move.uci() for plan in self.get_human_legal_moves(position, context)]

    @staticmethod
    def repetition_key(position):
        resting = position.pieces.get(position.resting_piece_id)
        reverse = tuple(sorted((position.pieces[o["pawn_id"]].square,
                                position.pieces[o["victim_id"]].square)
                               for o in position.reverse_ep))
        board = position.board
        return (board.board_fen(promoted=True), board.turn, board.clean_castling_rights(),
                board.ep_square, resting.square if resting else None, reverse)

    def record_position(self, position):
        position.repetitions[self.repetition_key(position)] += 1

    def outcome(self, position, context):
        board = position.board
        if board.turn == context.human_color:
            if not self.get_human_legal_moves(position, context):
                if self.attacked(board, context.human_color):
                    return VariantOutcome("checkmate", "martin", "Checkmate — Martin wins.")
                return VariantOutcome("exhaustion", "martin", "You have no usable pieces. You lose by exhaustion.")
        elif not self.get_martin_legal_moves(position, context):
            if self.attacked(board, not context.human_color):
                return VariantOutcome("checkmate", "human", "Checkmate — you win!")
            return VariantOutcome("draw", None, "Draw: Martin has no legal action (stalemate).")
        # Standard insufficient-material tests are unsound with a frozen king:
        # even a lone enemy knight can give an inescapable check in this variant.
        if board.halfmove_clock >= 150:
            return VariantOutcome("draw", None, "Draw: 75 moves without a pawn move or capture.")
        if position.repetitions[self.repetition_key(position)] >= 5:
            return VariantOutcome("draw", None, "Draw: fivefold repetition of the same handicap position.")
        return None

    def notation(self, position, plan, context):
        board = position.board
        piece = board.piece_at(plan.move.from_square)
        capture = plan.capture_square is not None or board.is_capture(plan.move)
        if plan.kind == "reverse_ep" or plan.move not in board.legal_moves:
            prefix = "" if piece.piece_type == chess.PAWN else piece.symbol().upper()
            text = f"{prefix}{chess.square_name(plan.move.from_square)}{'x' if capture else '-'}{chess.square_name(plan.move.to_square)}"
            if plan.move.promotion:
                text += "=" + chess.piece_symbol(plan.move.promotion).upper()
            if plan.kind == "reverse_ep":
                text += " r.e.p."
        else:
            text = board.san(plan.move).rstrip("+#")
        probe = self.project(position, plan, context)
        result = self.outcome(probe, context)
        if result and result.status == "checkmate":
            text += "#"
        elif self.attacked(probe.board, probe.board.turn):
            text += "+"
        return text
