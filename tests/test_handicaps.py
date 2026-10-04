"""Focused fixtures for actual variant legality and all cross-rule mechanics."""
from dataclasses import replace
import random
import unittest

import chess

from app import create_app
from game import Game
from martin import Martin, BotChoice
from rules import MovePlan, Position, RuleContext, RuleLayer
from test_game import Clock, started, attempt


def context(color=chess.WHITE, actor="human", seed=31):
    return RuleContext(actor,color,"human" if actor=="human" else "bot",1,None,random.Random(seed))


def apply(rules, position, plan, ctx):
    position.apply(plan)
    rules.after_move(position,plan,ctx)


def fixture_game(fen, color="white", seed=31):
    game,clock=started(color,seed)
    game.position=Position(fen)
    return game,clock


class HumanRuleTests(unittest.TestCase):
    def setUp(self): self.rules=RuleLayer(); self.ctx=context()

    def test_pawn_single_steps_both_colors(self):
        for color,double,single in [(chess.WHITE,"e2e4","e2e3"),(chess.BLACK,"e7e5","e7e6")]:
            position=Position(); position.prepare_turn(color)
            ctx=context(color)
            self.assertFalse(self.rules.validate(position,chess.Move.from_uci(double),ctx).accepted)
            self.assertTrue(self.rules.validate(position,chess.Move.from_uci(single),ctx).accepted)

    def test_all_four_edges_for_all_human_sliders(self):
        cases={"r":[("d4a4","d4b4"),("d4h4","d4g4"),("d4d1","d4d2"),("d4d8","d4d7")],
               "b":[("d4a7","d4b6"),("d4h8","d4g7"),("d4a1","d4b2"),("d4g1","d4f2")],
               "q":[("d4a4","d4b4"),("d4h4","d4g4"),("d4d1","d4d2"),("d4d8","d4d7")]}
        for symbol,moves in cases.items():
            position=Position(f"4k3/8/8/8/3{symbol.upper()}4/8/8/4K3 w - - 0 1")
            for forbidden,allowed in moves:
                with self.subTest(piece=symbol,move=forbidden):
                    self.assertFalse(self.rules.validate(position,chess.Move.from_uci(forbidden),self.ctx).accepted)
                    self.assertTrue(self.rules.validate(position,chess.Move.from_uci(allowed),self.ctx).accepted)

    def test_sliders_can_leave_starting_edges(self):
        for fen,move in [("4k3/8/8/8/8/8/8/1R2K3 w - - 0 1","b1b2"),
                         ("4k3/8/8/8/8/8/8/2B1K3 w - - 0 1","c1d2"),
                         ("4k3/8/8/8/8/8/8/3QK3 w - - 0 1","d1d2"),
                         ("4k3/8/8/8/R7/8/8/4K3 w - - 0 1","a4b4")]:
            self.assertTrue(self.rules.validate(Position(fen),chess.Move.from_uci(move),self.ctx).accepted)

    def test_frozen_king_cannot_move_capture_or_castle(self):
        position=Position("4k3/8/8/8/8/8/3n4/R3K2R w KQ - 0 1")
        for uci in ["e1e2","e1d2","e1g1","e1c1"]:
            self.assertFalse(self.rules.validate(position,chess.Move.from_uci(uci),self.ctx).accepted)

    def test_check_resolved_by_capture_or_block_only(self):
        position=Position("4r2k/8/8/8/8/8/3R4/4K3 w - - 0 1")
        moves={p.move.uci() for p in self.rules.get_human_legal_moves(position,self.ctx)}
        self.assertEqual(moves,{"d2e2"})
        position=Position("k7/8/8/8/8/5n2/5R2/4K3 w - - 0 1")
        self.assertEqual([p.move.uci() for p in self.rules.get_human_legal_moves(position,self.ctx)],["f2f3"])

    def test_double_check_is_mate_with_frozen_king(self):
        position=Position("4r2k/8/8/8/8/5n2/3R4/4K3 w - - 0 1")
        result=self.rules.outcome(position,self.ctx)
        self.assertEqual(result.status,"checkmate")

    def test_recovery_tracks_physical_id_and_releases_after_another_move(self):
        position=Position("4k3/8/8/8/8/8/1R3R2/4K3 w - - 0 1")
        first=position.at[chess.B2]; second=position.at[chess.F2]
        apply(self.rules,position,self.rules.validate(position,chess.Move.from_uci("b2b3"),self.ctx),self.ctx)
        position.prepare_turn(chess.WHITE)
        self.assertEqual(position.resting_piece_id,first)
        self.assertFalse(self.rules.validate(position,chess.Move.from_uci("b3b4"),self.ctx).accepted)
        apply(self.rules,position,self.rules.validate(position,chess.Move.from_uci("f2f3"),self.ctx),self.ctx)
        position.prepare_turn(chess.WHITE)
        self.assertEqual(position.resting_piece_id,second)
        self.assertTrue(self.rules.validate(position,chess.Move.from_uci("b3b4"),self.ctx).accepted)

    def test_capture_causes_recovery(self):
        position=Position("4k3/8/8/8/8/1p6/1R6/4K3 w - - 0 1")
        ctx=context(seed=2)
        apply(self.rules,position,self.rules.validate(position,chess.Move.from_uci("b2b3"),ctx),ctx)
        position.prepare_turn(chess.WHITE)
        self.assertFalse(self.rules.validate(position,chess.Move.from_uci("b3c3"),ctx).accepted)

    def test_knights_ignore_edges_and_recovery(self):
        position=Position("4k3/8/8/8/8/8/2N5/4K3 w - - 0 1")
        position.resting_piece_id=position.at[chess.C2]
        ctx=context(seed=2)
        plan=self.rules.validate(position,chess.Move.from_uci("c2a1"),ctx)
        self.assertTrue(plan.accepted)
        apply(self.rules,position,plan,ctx)
        position.prepare_turn(chess.WHITE)
        self.assertIsNone(position.resting_piece_id)
        self.assertTrue(self.rules.validate(position,chess.Move.from_uci("a1c2"),ctx).accepted)

    def test_exhaustion_waits_for_human_turn_and_never_piece_count_alone(self):
        position=Position("4k3/8/8/8/8/8/1R6/4K3 w - - 0 1")
        self.assertIsNone(self.rules.outcome(position,self.ctx))
        apply(self.rules,position,self.rules.validate(position,chess.Move.from_uci("b2b3"),self.ctx),self.ctx)
        self.assertIsNone(self.rules.outcome(position,self.ctx))  # Martin still has moves.
        position.prepare_turn(chess.WHITE)
        self.assertEqual(self.rules.outcome(position,self.ctx).status,"exhaustion")
        knight=Position("4k3/8/8/8/8/8/2N5/4K3 w - - 0 1")
        self.assertIsNone(self.rules.outcome(knight,self.ctx))

    def test_resting_only_defender_means_checkmate(self):
        position=Position("k7/8/8/8/8/5n2/5R2/4K3 w - - 0 1")
        position.resting_piece_id=position.at[chess.F2]
        self.assertEqual(self.rules.outcome(position,self.ctx).status,"checkmate")

    def test_deterministic_knight_malfunction(self):
        position=Position()
        plan=self.rules.validate(position,chess.Move.from_uci("g1f3"),context(seed=31))
        self.assertTrue(plan.accepted)
        self.assertEqual(plan.move.uci(),"g1h3")
        self.assertEqual(plan.intended_uci,"g1f3")
        self.assertIn("Your knight got confused!",plan.events)

    def test_knight_redirection_can_accidentally_capture(self):
        position=Position("4k3/8/8/8/8/7r/4P2P/4K1N1 w - - 0 1")
        plan=self.rules.validate(position,chess.Move.from_uci("g1f3"),context(seed=31))
        self.assertEqual(plan.move.uci(),"g1h3")
        victim=position.at[chess.H3]
        apply(self.rules,position,plan,self.ctx)
        self.assertIsNone(position.pieces[victim].square)

    def test_game_reports_exhaustion_after_martin_reply(self):
        game,clock=fixture_game("4k3/8/8/8/8/8/1R6/4K3 w - - 0 1")
        clock.advance(4)
        self.assertTrue(attempt(game,"b2b3")[0])
        self.assertEqual(game.phase,"bot")
        game.bot.choose=lambda pos,rules,ctx,action: BotChoice(chess.Move.from_uci("e8f8"),"test")
        clock.advance(.7); game.tick()
        self.assertEqual(game.status,"exhaustion")
        self.assertEqual(game.winner,"martin")

    def test_knight_with_one_destination_moves_normally(self):
        # Restrict via check: the knight's sole legal response captures f3.
        position=Position("k7/8/8/8/8/5n2/7P/4K1N1 w - - 0 1")
        legal=self.rules.get_human_legal_moves(position,self.ctx)
        self.assertEqual([p.move.uci() for p in legal],["g1f3"])
        plan=self.rules.validate(position,chess.Move.from_uci("g1f3"),self.ctx)
        self.assertEqual(plan.move.uci(),"g1f3")
        self.assertFalse(plan.events)

    def test_human_final_rank_both_colors_and_pawn_captures(self):
        for color,fen,move in [(chess.WHITE,"4k2r/6P1/8/8/8/8/8/4K3 w - - 0 1","g7h8"),
                               (chess.BLACK,"4k3/8/8/8/8/8/6p1/4K2R b - - 0 1","g2h1")]:
            position=Position(fen); ctx=context(color,seed=2)
            identity=position.at[chess.Move.from_uci(move).from_square]
            plan=self.rules.validate(position,chess.Move.from_uci(move),ctx)
            self.assertTrue(plan.accepted)
            apply(self.rules,position,plan,ctx)
            pawn=position.pieces[identity]
            self.assertEqual(pawn.kind,chess.PAWN); self.assertFalse(pawn.promoted)
            self.assertEqual(position.board.piece_at(pawn.square).piece_type,chess.PAWN)
            # python-chess considers this invalid, but our adapter retains it.
            self.assertFalse(position.board.is_valid())

    def test_polling_and_legality_consume_no_rng(self):
        position=Position(); before=self.ctx.rng.getstate()
        for _ in range(10):
            self.rules.get_human_legal_moves(position,self.ctx)
            self.rules.get_martin_legal_moves(position,self.ctx)
            self.rules.outcome(position,self.ctx)
        self.assertEqual(self.ctx.rng.getstate(),before)

    def test_fivefold_repetition_includes_recovery_rights(self):
        position=Position("1n2k3/8/8/8/8/8/P7/1N2K3 w - - 0 1")
        self.rules.record_position(position)
        for _ in range(4):
            for actor,uci in [("human","b1c3"),("martin","b8c6"),
                              ("human","c3b1"),("martin","c6b8")]:
                ctx=replace(self.ctx,actor=actor)
                apply(self.rules,position,MovePlan(chess.Move.from_uci(uci)),ctx)
                self.rules.record_position(position)
        self.assertEqual(self.rules.outcome(position,self.ctx).status,"draw")
        position.resting_piece_id=position.at[chess.A2]
        self.assertIsNone(self.rules.outcome(position,self.ctx))


class CaptureRefusalTests(unittest.TestCase):
    FEN="4k3/8/8/8/8/3q4/3R4/4K3 w - - 0 1"
    def setUp(self): self.rules=RuleLayer(); self.ctx=context(seed=31)

    def test_refusal_seed_and_repeat_block_without_rng_or_board_change(self):
        position=Position(self.FEN); before=position.board.fen()
        move=chess.Move.from_uci("d2d3")
        plan=self.rules.validate(position,move,self.ctx)
        self.assertFalse(plan.accepted); self.assertIn("Martin says no",plan.message)
        self.assertEqual(position.board.fen(),before)
        rng=self.ctx.rng.getstate()
        for _ in range(20): self.assertFalse(self.rules.validate(position,move,self.ctx).accepted)
        self.assertEqual(self.ctx.rng.getstate(),rng)
        self.assertNotIn("d2d3",self.rules.human_move_hints(position,self.ctx))

    def test_only_legal_capture_never_rolls_refusal(self):
        position=Position("k7/8/8/8/8/5n2/5R2/4K3 w - - 0 1")
        before=self.ctx.rng.getstate()
        plan=self.rules.validate(position,chess.Move.from_uci("f2f3"),self.ctx)
        self.assertTrue(plan.accepted)
        self.assertEqual(self.ctx.rng.getstate(),before)

    def test_legal_recapture_protects_piece_without_rng(self):
        position=Position("4k3/8/8/8/8/2rq4/3R4/4K3 w - - 0 1")
        before=self.ctx.rng.getstate()
        self.assertTrue(self.rules.validate(position,chess.Move.from_uci("d2d3"),self.ctx).accepted)
        self.assertEqual(self.ctx.rng.getstate(),before)

    def test_pinned_defender_is_not_protection(self):
        position=Position("4k3/8/8/8/4b3/3q4/3RR3/4K3 w - - 0 1")
        plan=self.rules.validate(position,chess.Move.from_uci("d2d3"),self.ctx)
        self.assertFalse(plan.accepted)
        self.assertIn("Martin says no",plan.message)

    def test_legal_reverse_ep_recapture_also_protects_a_piece(self):
        position=Position("4k3/8/8/8/8/3qp3/3R4/4K3 w - - 0 1")
        before=self.ctx.rng.getstate()
        self.assertTrue(self.rules.validate(position,chess.Move.from_uci("d2d3"),self.ctx).accepted)
        self.assertEqual(before,self.ctx.rng.getstate())

    def test_rejection_keeps_clock_and_does_not_cause_speed_penalty(self):
        game,clock=fixture_game(self.FEN)
        clock.advance(1)
        self.assertFalse(attempt(game,"d2d3")[0])
        self.assertEqual(game.phase,"human"); self.assertEqual(len(game.history),3)
        self.assertIsNone(game.position.resting_piece_id)
        clock.advance(2)
        self.assertEqual(game.snapshot()["human_time"],1197)
        self.assertTrue(attempt(game,"d2e2")[0])
        self.assertEqual(game.phase,"bot")
        self.assertFalse(game.position.refused_captures)


class MartinSpecialTests(unittest.TestCase):
    def setUp(self): self.rules=RuleLayer()

    def test_early_promotion_both_colors_forward_and_capture(self):
        cases=[(chess.BLACK,"4k3/8/P7/8/8/8/8/4K3 w - - 0 1","a6a7"),
               (chess.WHITE,"4k3/8/8/8/8/p7/8/4K3 b - - 0 1","a3a2"),
               (chess.BLACK,"4k3/1r6/P7/8/8/8/8/4K3 w - - 0 1","a6b7"),
               (chess.WHITE,"4k3/8/8/8/8/p7/1R6/4K3 b - - 0 1","a3b2")]
        for human,fen,uci in cases:
            position=Position(fen); ctx=context(human,"martin")
            plans=[p for p in self.rules.get_martin_legal_moves(position,ctx) if p.move.uci()==uci+"q"]
            self.assertEqual(len(plans),1)
            identity=position.at[plans[0].move.from_square]
            apply(self.rules,position,plans[0],ctx)
            self.assertEqual(position.pieces[identity].kind,chess.QUEEN)
            self.assertTrue(position.pieces[identity].promoted)

    def test_reverse_ep_multiple_victims_both_colors(self):
        for human in (chess.WHITE,chess.BLACK):
            for kind in (chess.PAWN,chess.KNIGHT,chess.BISHOP,chess.ROOK,chess.QUEEN):
                position=Position("4k3/8/8/8/8/8/8/4K3 w - - 0 1")
                # Place a victim beside a Martin pawn as if it just moved there.
                victim=position.create_piece(chess.D4,kind,human)
                pawn=position.create_piece(chess.E4,chess.PAWN,not human)
                position.reverse_ep=[{"pawn_id":pawn,"victim_id":victim}]
                position.prepare_turn(not human)
                ctx=context(human,"martin")
                target=chess.D3 if human else chess.D5
                plans=[p for p in self.rules.get_martin_legal_moves(position,ctx)
                       if p.kind=="reverse_ep" and p.move.to_square==target]
                self.assertEqual(len(plans),1)
                apply(self.rules,position,plans[0],ctx)
                self.assertIsNone(position.pieces[victim].square)
                self.assertEqual(position.pieces[pawn].square,target)
                self.assertIsNone(position.board.piece_at(chess.D4))
                self.assertEqual(position.at[target],pawn)
                self.assertFalse(position.reverse_ep)

    def test_actual_human_move_records_reverse_ep_and_other_bot_move_expires_it(self):
        position=Position("4k3/8/8/8/4p3/3R4/8/4K3 w - - 0 1")
        ctx=context(seed=2)
        apply(self.rules,position,self.rules.validate(position,chess.Move.from_uci("d3d4"),ctx),ctx)
        botctx=replace(ctx,actor="martin")
        self.assertIn("e4d3",[p.move.uci() for p in self.rules.get_martin_legal_moves(position,botctx)])
        other=next(p for p in self.rules.get_martin_legal_moves(position,botctx) if p.move.uci()=="e8f8")
        apply(self.rules,position,other,botctx)
        position.prepare_turn(chess.BLACK)
        self.assertFalse(any(p.kind=="reverse_ep" for p in self.rules.get_martin_legal_moves(position,botctx)))

    def test_reverse_ep_cannot_land_on_occupied_square_or_expose_king(self):
        position=Position("4k3/8/8/8/3Rp3/3N4/8/4K3 b - - 0 1")
        position.reverse_ep=[{"pawn_id":position.at[chess.E4],"victim_id":position.at[chess.D4]}]
        ctx=context(actor="martin")
        self.assertFalse(any(p.kind=="reverse_ep" for p in self.rules.get_martin_legal_moves(position,ctx)))
        position=Position("4k3/8/8/8/3Np3/8/4R3/4K3 b - - 0 1")
        position.reverse_ep=[{"pawn_id":position.at[chess.E4],"victim_id":position.at[chess.D4]}]
        self.assertFalse(any(p.kind=="reverse_ep" for p in self.rules.get_martin_legal_moves(position,ctx)))

    def test_reverse_ep_can_promote_early(self):
        position=Position("4k3/8/8/8/8/3Rp3/8/4K3 b - - 0 1")
        position.reverse_ep=[{"pawn_id":position.at[chess.E3],"victim_id":position.at[chess.D3]}]
        ctx=context(actor="martin")
        plan=next(p for p in self.rules.get_martin_legal_moves(position,ctx) if p.kind=="reverse_ep" and p.move.promotion==chess.QUEEN)
        identity=position.at[chess.E3]
        apply(self.rules,position,plan,ctx)
        self.assertEqual(position.pieces[identity].kind,chess.QUEEN)
        self.assertEqual(position.pieces[identity].square,chess.D2)

    def test_handicap_mate_that_standard_chess_can_escape(self):
        position=Position("4k3/8/8/6n1/8/8/3R4/4K3 b - - 0 1")
        ctx=context(actor="martin")
        actions=self.rules.get_martin_legal_moves(position,ctx)
        mates=Martin.mating_moves(position,actions,self.rules,ctx)
        self.assertIn("g5f3",[p.move.uci() for p in mates])
        plan=next(p for p in mates if p.move.uci()=="g5f3")
        probe=self.rules.project(position,plan,ctx)
        self.assertFalse(probe.board.is_checkmate())
        for seed in range(12):
            choice=Martin(random.Random(seed)).choose(position,self.rules,ctx)
            self.assertIn(choice.move,[p.move for p in mates])

    def test_resting_defender_accounted_for_in_mate_scan(self):
        position=Position("4k3/8/8/6n1/8/8/5R2/4K3 b - - 0 1")
        ctx=context(actor="martin")
        position.resting_piece_id=position.at[chess.F2]
        mates=Martin.mating_moves(position,self.rules.get_martin_legal_moves(position,ctx),self.rules,ctx)
        self.assertIn("g5f3",[p.move.uci() for p in mates])

    def test_mate_scan_includes_reverse_ep(self):
        position=Position("k3r3/8/8/8/3Rp3/8/8/N3K3 b - - 0 1")
        position.reverse_ep=[{"pawn_id":position.at[chess.E4],"victim_id":position.at[chess.D4]}]
        ctx=context(actor="martin")
        for seed in range(10):
            choice=Martin(random.Random(seed)).choose(position,self.rules,ctx)
            self.assertEqual(choice.move.uci(),"e4d3")
            self.assertEqual(choice.plan.kind,"reverse_ep")
            result=self.rules.outcome(self.rules.project(position,choice.plan,ctx),ctx)
            self.assertEqual(result.status,"checkmate")

    def test_early_promotion_mate_prefers_queen_if_queen_also_mates(self):
        position=Position("k7/8/8/8/8/5p2/8/N3K3 b - - 0 1")
        ctx=context(actor="martin")
        for seed in range(10):
            choice=Martin(random.Random(seed)).choose(position,self.rules,ctx)
            self.assertEqual(choice.move.uci(),"f3f2q")
            self.assertEqual(choice.reason,"mate_in_one")
            result=self.rules.outcome(self.rules.project(position,choice.plan,ctx),ctx)
            self.assertEqual(result.status,"checkmate")


class PremoveTests(unittest.TestCase):
    def test_even_winning_premove_waits_and_timeout_during_wait_still_loses(self):
        for time_left,expected in [(1200,"checkmate"),(5,"timeout")]:
            game,clock=fixture_game("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
            game.human_remaining=time_left
            self.assertTrue(game.complete_human_move(chess.Move.from_uci("f7g7"),premove=True)[0])
            self.assertEqual(game.phase,"penalty")
            clock.advance(15); game.tick()
            self.assertEqual(game.status,expected)

    def test_capture_of_queued_piece_discards_premove(self):
        game,clock=fixture_game("1r2k3/8/8/8/8/8/4P3/1N1QK3 b - - 0 1")
        game.stop_clock(clock()); game.phase="bot"; game.due_at=clock()+1
        self.assertTrue(game.queue_premove("b1a3",game.id,2)[0])
        game.bot.choose=lambda pos,rules,ctx,action: BotChoice(chess.Move.from_uci("b8b1"),"test")
        clock.advance(1); game.tick()
        self.assertEqual(game.phase,"human")
        self.assertIsNone(game.premove)
        self.assertTrue(any("no longer there" in e["message"] for e in game.events))

    def test_queue_replace_cancel_and_no_rng_consumption(self):
        clock=Clock(); game=Game(clock=clock,opening_delay=10)
        before=game.rng.getstate()
        self.assertTrue(game.queue_premove("e2e3",game.id,1)[0])
        self.assertTrue(game.queue_premove("d2d3",game.id,1)[0])
        self.assertEqual(game.premove["uci"],"d2d3")
        self.assertTrue(game.queue_premove(None,game.id,1)[0])
        self.assertIsNone(game.premove)
        self.assertEqual(before,game.rng.getstate())

    def test_opening_premove_executes_immediately_and_penalty_always(self):
        clock=Clock(); game=Game(clock=clock,opening_delay=1)
        self.assertTrue(game.queue_premove("e2e3",game.id,1)[0])
        for _ in range(3): clock.advance(1); game.tick()
        self.assertEqual(game.phase,"penalty")
        self.assertEqual(game.last_human_elapsed,0)
        self.assertEqual(game.history[-1]["uci"],"e2e3")
        self.assertTrue(game.history[-1]["premove"])
        self.assertEqual(game.snapshot()["penalty_remaining"],15)
        self.assertEqual(game.snapshot()["human_time"],1200)

    def test_premove_invalidated_by_check_is_discarded(self):
        game,clock=fixture_game("4k3/8/8/6n1/8/8/3R4/4K3 b - - 0 1")
        # Add a knight that can capture f3, so the resulting check is escapable.
        game.position.create_piece(chess.H2,chess.KNIGHT,chess.WHITE)
        game.stop_clock(clock()); game.phase="bot"; game.due_at=clock()+1
        self.assertTrue(game.queue_premove("d2d3",game.id,2)[0])
        game.bot.choose=lambda pos,rules,ctx,action: BotChoice(chess.Move.from_uci("g5f3"),"test")
        clock.advance(1); game.tick()
        self.assertEqual(game.phase,"human")
        self.assertIsNone(game.premove)
        self.assertTrue(any(e["kind"]=="premove_discard" for e in game.events))

    def test_refused_premove_returns_control_with_clock_running(self):
        game,clock=fixture_game(CaptureRefusalTests.FEN)
        game.position.prepare_turn(chess.BLACK)
        game.stop_clock(clock()); game.phase="bot"; game.due_at=clock()+1
        self.assertTrue(game.queue_premove("d2d3",game.id,2)[0])
        game.bot.choose=lambda pos,rules,ctx,action: BotChoice(chess.Move.from_uci("e8f8"),"test")
        clock.advance(1); game.tick()
        self.assertEqual(game.phase,"human"); self.assertIsNone(game.premove)
        self.assertEqual(len(game.history),4)
        self.assertIn("d2d3",game.position.refused_captures)
        clock.advance(2); self.assertEqual(game.remaining(clock()),1198)

    def test_premove_rejects_frozen_resting_double_step_and_edges(self):
        clock=Clock(); game=Game(clock=clock,opening_delay=10)
        for uci in ["e1e2","e2e4","b1b3"]:
            self.assertFalse(game.queue_premove(uci,game.id,1)[0])
        game.position=Position("4k3/8/8/8/8/1R6/8/4K3 b - - 0 1")
        game.position.resting_piece_id=game.position.at[chess.B3]
        self.assertFalse(game.queue_premove("b3b4",game.id,1)[0])
        game.position.resting_piece_id=None
        self.assertFalse(game.queue_premove("b3a3",game.id,1)[0])

    def test_premove_not_allowed_during_penalty_or_human_turn(self):
        game,clock=started()
        self.assertFalse(game.queue_premove("d2d3",game.id,2)[0])
        attempt(game,"e2e3")
        self.assertEqual(game.phase,"penalty")
        self.assertFalse(game.queue_premove("d2d3",game.id,2)[0])

    def test_premoved_knight_still_malfunctions(self):
        clock=Clock(); game=Game(seed=31,clock=clock,opening_delay=1)
        game.queue_premove("g1f3",game.id,1)
        for _ in range(3): clock.advance(1); game.tick()
        self.assertEqual(game.history[-1]["uci"],"g1h3")
        self.assertEqual(game.phase,"penalty")

    def test_premove_api_contract_and_target_turn(self):
        clock=Clock()
        app=create_app(start_scheduler=False,game_factory=lambda **kw: Game(**kw,clock=clock,opening_delay=10))
        client=app.test_client()
        self.assertEqual(client.post("/api/premove",json={}).status_code,404)
        state=client.post("/api/new",json={}).json
        payload={"game_id":state["game_id"],"target_turn":1,"move":"e2e3"}
        result=client.post("/api/premove",json=payload)
        self.assertEqual(result.status_code,200); self.assertEqual(result.json["premove"]["uci"],"e2e3")
        payload["target_turn"]=999
        self.assertEqual(client.post("/api/premove",json=payload).status_code,409)


if __name__=="__main__": unittest.main()
