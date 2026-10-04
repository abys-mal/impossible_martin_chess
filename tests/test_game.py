import random
import unittest

import chess

from app import create_app
from game import Game
from martin import Martin, BotChoice
from rules import Position, RuleLayer, MovePlan, RuleContext


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


def started(color="white", seed=48291):
    clock = Clock()
    game = Game(color, seed, clock=clock, opening_delay=0, bot_delay=0.7)
    for _ in range(3): game.tick()
    return game, clock


def attempt(game, move):
    return game.attempt_move(move, game.id, game.revision)


class GameTests(unittest.TestCase):
    def test_both_opening_colors_and_clock(self):
        for color, expected in [("white", ["e7e5","d8g5","e8d8"]),
                                ("black", ["e2e4","d1g4","e1d1"])]:
            with self.subTest(color=color):
                game, clock = started(color)
                self.assertEqual([h["uci"] for h in game.history], expected)
                self.assertEqual(game.phase, "human")
                self.assertEqual(game.remaining(clock()), 1200)
                self.assertEqual(game.position.board.turn, game.human_color)
                self.assertTrue(game.position.board.is_valid())
                self.assertEqual(len(game.position.board.move_stack), 0)
                clock.advance(2)
                self.assertEqual(game.snapshot()["human_time"], 1198)

    def test_no_clock_or_human_moves_during_opening(self):
        clock = Clock()
        game = Game(clock=clock, opening_delay=1)
        for action in range(3):
            self.assertFalse(attempt(game, "e2e3")[0])
            self.assertEqual(game.remaining(clock()), 1200)
            clock.advance(1)
            game.tick()
        self.assertEqual(game.phase, "human")

    def test_real_penalty_boundary_and_no_bot_early(self):
        game, clock = started()
        clock.advance(2.999)
        self.assertTrue(attempt(game,"e2e3")[0])
        self.assertEqual(game.phase,"penalty")
        self.assertEqual(len(game.history),4)
        initial = game.remaining(clock())
        clock.advance(14.999)
        game.tick()
        self.assertEqual(game.phase,"penalty")
        self.assertEqual(len(game.history),4)
        self.assertAlmostEqual(game.remaining(clock()),initial-14.999)
        self.assertFalse(attempt(game,"d2d4")[0])
        clock.advance(.001)
        game.tick()
        self.assertEqual(game.phase,"bot")
        banked = game.remaining(clock())
        clock.advance(.69)
        game.tick()
        self.assertEqual(game.remaining(clock()),banked)
        self.assertEqual(len(game.history),4)
        clock.advance(.01)
        game.tick()
        self.assertEqual(game.phase,"human")
        self.assertEqual(len(game.history),5)
        self.assertEqual(game.remaining(clock()),banked)

    def test_exact_three_seconds_no_penalty(self):
        game, clock = started()
        clock.advance(3)
        self.assertTrue(attempt(game,"e2e3")[0])
        self.assertEqual(game.phase,"bot")
        self.assertEqual(game.remaining(clock()),1197)
        clock.advance(.7)
        game.tick()
        self.assertEqual(game.phase,"human")
        self.assertEqual(game.remaining(clock()),1197)

    def test_zero_second_move_penalized(self):
        game, clock = started()
        self.assertTrue(attempt(game,"e2e3")[0])
        self.assertEqual(game.phase,"penalty")
        self.assertEqual(game.snapshot()["penalty_remaining"],15)

    def test_timeout_during_penalty_prevents_bot(self):
        game, clock = started()
        game.human_remaining = 4
        clock.advance(1)
        self.assertTrue(attempt(game,"e2e3")[0])
        clock.advance(3)
        game.tick()
        self.assertEqual(game.status,"timeout")
        self.assertEqual(game.winner,"martin")
        self.assertEqual(len(game.history),4)
        self.assertEqual(game.remaining(clock()),0)

    def test_timeout_even_without_browser_requests(self):
        game, clock = started()
        clock.advance(1200)
        game.tick()
        self.assertEqual(game.status,"timeout")
        self.assertFalse(attempt(game,"e2e3")[0])

    def test_scheduler_lateness_does_not_bill_bot_thinking(self):
        game, clock = started()
        clock.advance(1)
        attempt(game,"e2e3")
        clock.advance(100)
        game.tick()
        self.assertEqual(game.phase,"human")
        self.assertEqual(game.remaining(clock()),1184)

    def test_clock_expires_before_late_penalty_tick(self):
        game, clock = started()
        game.human_remaining=10
        attempt(game,"e2e3")
        clock.advance(100)
        game.tick()
        self.assertEqual(game.status,"timeout")
        self.assertEqual(len(game.history),4)

    def test_invalid_moves_do_not_reset_clock_or_change_board(self):
        game, clock = started()
        fen=game.position.board.fen()
        clock.advance(1)
        for move in ["e2e5","garbage","e7e6","a3a4",None,"0000"]:
            self.assertFalse(attempt(game,move)[0])
        self.assertEqual(game.position.board.fen(),fen)
        self.assertEqual(game.remaining(clock()),1199)
        clock.advance(2)
        self.assertTrue(attempt(game,"e2e3")[0])
        self.assertEqual(game.phase,"bot")

    def test_stale_revision_and_game_id_rejected(self):
        game, clock = started()
        clock.advance(3)
        self.assertFalse(game.attempt_move("e2e3","other",game.revision)[0])
        self.assertFalse(game.attempt_move("e2e3",game.id,game.revision-1)[0])
        self.assertEqual(len(game.history),3)
        self.assertTrue(attempt(game,"e2e3")[0])
        self.assertFalse(attempt(game,"d2d4")[0])

    def test_resign_freezes_clock(self):
        game, clock = started()
        clock.advance(5)
        game.resign()
        clock.advance(100)
        self.assertEqual(game.snapshot()["human_time"],1195)
        self.assertEqual(game.status,"resigned")

    def test_human_checkmate_finishes_without_another_bot_move(self):
        game,clock=started()
        game.position=Position("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
        clock.advance(3)
        self.assertTrue(attempt(game,"f7g7")[0])
        self.assertEqual(game.status,"checkmate")
        self.assertEqual(game.winner,"human")
        clock.advance(30); game.tick()
        self.assertEqual(len(game.history),4)

    def test_bot_checkmate_is_authoritative_game_result(self):
        game,clock=started()
        game.position=Position("8/8/8/8/8/6k1/5q2/7K b - - 0 1")
        game.stop_clock(clock()); game.phase="bot"; game.due_at=clock()
        game.tick()
        self.assertEqual(game.status,"checkmate")
        self.assertEqual(game.winner,"martin")
        self.assertEqual(game.history[-1]["reason"],"mate_in_one")

    def test_human_pawn_stays_pawn_and_reports_event(self):
        game,clock=started()
        game.position=Position("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
        clock.advance(4)
        self.assertFalse(attempt(game,"a7a8n")[0])
        self.assertTrue(attempt(game,"a7a8")[0])
        self.assertEqual(game.position.board.piece_at(chess.A8).piece_type,chess.PAWN)
        self.assertEqual(game.status,"playing")
        self.assertTrue(any('remains a pawn' in e['message'] for e in game.events))

    def test_stalemate_draw(self):
        game,clock=started()
        game.position=Position("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
        self.assertTrue(game.terminal(clock()))
        self.assertEqual(game.status,"draw")
        self.assertIn("stalemate",game.result)

    def test_seed_replays_and_hints_do_not_consume_rng(self):
        first, clock1 = started()
        second, clock2 = started()
        for _ in range(12):
            if first.phase == "finished": break
            for _ in range(7): first.snapshot()
            move = sorted(first.snapshot()["legal_moves"])[0]
            clock1.advance(4); clock2.advance(4)
            self.assertTrue(attempt(first,move)[0])
            self.assertTrue(attempt(second,move)[0])
            clock1.advance(.8); clock2.advance(.8)
            first.tick(); second.tick()
        self.assertEqual(first.history,second.history)
        self.assertEqual(first.position.board.fen(),second.position.board.fen())

    def test_checks_do_not_interrupt_opening(self):
        clock=Clock()
        game=Game("white",1,clock=clock,opening_delay=0)
        game.position=Position("3qk3/4p3/8/8/8/K7/2N5/8 b - - 0 1")
        scripted=["e7e5","d8e7","e8d8"]
        game.bot.choose=lambda position,rules,context,action: BotChoice(chess.Move.from_uci(scripted[action]),"test")
        game.tick(); game.tick()
        self.assertEqual(game.opening_done,2)
        self.assertEqual(game.phase,"opening")
        self.assertEqual(game.remaining(clock()),1200)
        self.assertFalse(attempt(game,"a3b3")[0])  # stale revision: tick finishes the opening
        self.assertEqual(game.opening_done,3)
        self.assertEqual(game.phase,"human")


class BotTests(unittest.TestCase):
    def test_never_misses_mate_in_one_including_during_script(self):
        boards=[chess.Board("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1"),
                chess.Board("8/8/8/8/8/6k1/5q2/7K b - - 0 1")]
        fools=chess.Board()
        for uci in ["f2f3","e7e5","g2g4"]: fools.push_uci(uci)
        boards.append(fools)
        for board in boards:
            position=Position(board.fen()); rules=RuleLayer()
            context=RuleContext("martin",not board.turn,"bot",1,None,random.Random(0))
            self.assertTrue(Martin.mating_moves(position,rules.get_martin_legal_moves(position,context),rules,context))
            for seed in range(10):
                for action in [None,0,1,2]:
                    with self.subTest(fen=board.fen(),seed=seed,action=action):
                        choice=Martin(random.Random(seed)).choose(position,rules,context,action)
                        result=rules.outcome(rules.project(position,choice.plan,context),context)
                        self.assertEqual(result.status,"checkmate")
                        self.assertEqual(result.winner,"martin")

    def test_fallback_move_and_randomness(self):
        position=Position(); rules=RuleLayer()
        context=RuleContext("martin",chess.BLACK,"opening",1,None,random.Random(0))
        for action in range(3):
            choice=Martin(random.Random(1)).choose(position,rules,context,action)
            self.assertIn(choice.move,[p.move for p in rules.get_martin_legal_moves(position,context)])
            position.apply(choice.plan); position.prepare_turn(chess.WHITE,consecutive=True)
        moves={Martin(random.Random(seed)).choose(Position(),rules,context).move.uci() for seed in range(40)}
        self.assertGreater(len(moves),5)


class PositionTests(unittest.TestCase):
    def assert_registry(self,position):
        self.assertEqual(set(position.at),set(position.board.piece_map()))
        for square,identity in position.at.items():
            tracked=position.pieces[identity]
            self.assertEqual(tracked.square,square)
            self.assertEqual(position.board.piece_at(square),chess.Piece(tracked.kind,tracked.color))

    def test_en_passant_keeps_physical_id(self):
        position=Position()
        identity=position.at[chess.E2]
        captured=position.at[chess.D7]
        for uci in ["e2e4","a7a6","e4e5","d7d5","e5d6"]:
            position.apply(MovePlan(chess.Move.from_uci(uci))); self.assert_registry(position)
        self.assertEqual(position.at[chess.D6],identity)
        self.assertIsNone(position.pieces[captured].square)

    def test_both_castles_keep_rook_and_king_ids(self):
        for move,source,target in [("e1g1",chess.H1,chess.F1),("e1c1",chess.A1,chess.D1),
                                    ("e8g8",chess.H8,chess.F8),("e8c8",chess.A8,chess.D8)]:
            fen="r3k2r/8/8/8/8/8/8/R3K2R " + ("w" if move[1]=="1" else "b") + " KQkq - 0 1"
            position=Position(fen); identity=position.at[source]
            position.apply(MovePlan(chess.Move.from_uci(move)))
            self.assertEqual(position.at[target],identity); self.assert_registry(position)

    def test_promotion_preserves_identity(self):
        for promotion in "qrbn":
            position=Position("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
            identity=position.at[chess.A7]
            position.apply(MovePlan(chess.Move.from_uci("a7a8"+promotion)))
            self.assertEqual(position.at[chess.A8],identity)
            self.assertTrue(position.pieces[identity].promoted)
            self.assert_registry(position)

    def test_custom_move_executor_can_override_standard_legality(self):
        class Teleport:
            def before_move(self,position,plan,context):
                def execute(position,plan):
                    position.relocate_piece(plan.move.from_square,plan.move.to_square)
                    position.board.turn=not position.board.turn
                plan.execute=execute; plan.notation="Experimental teleport"; return plan
            def after_move(self,position,plan,context): return ["Teleport applied."]
        game,clock=started()
        game.rules=RuleLayer([Teleport()]); clock.advance(3)
        identity=game.position.at[chess.A2]
        self.assertTrue(attempt(game,"a2a5")[0])
        self.assertEqual(game.position.at[chess.A5],identity)
        self.assertEqual(game.history[-1]["san"],"Experimental teleport")
        self.assert_registry(game.position)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock()
        def factory(**kwargs): return Game(**kwargs,clock=self.clock,opening_delay=0)
        self.app=create_app(start_scheduler=False,game_factory=factory)
        self.app.config.update(TESTING=True)
        self.client=self.app.test_client()

    def test_page_and_static_assets(self):
        self.assertEqual(self.client.get("/").status_code,200)
        for asset in ["game.js","style.css","pieces/wq.svg","pieces/bk.svg"]:
            with self.client.get("/static/"+asset) as response:
                self.assertEqual(response.status_code,200)
        self.assertIsNone(self.client.get("/api/state").json["game"])

    def test_bad_payloads_and_seed_validation(self):
        for payload in [[],{"color":"red"},{"seed":True},{"seed":-1},{"seed":2**53},{"seed":"1"}]:
            self.assertEqual(self.client.post("/api/new",json=payload).status_code,400)
        self.assertEqual(self.client.post("/api/new",data="x").status_code,400)
        self.assertEqual(self.client.post("/api/move",json={}).status_code,404)
        self.assertEqual(self.client.post("/api/resign",json={}).status_code,404)

    def test_session_isolation_and_reload(self):
        first=self.client.post("/api/new",json={"color":"white","seed":48291}).json
        second_client=self.app.test_client()
        self.assertIsNone(second_client.get("/api/state").json["game"])
        second=second_client.post("/api/new",json={"color":"black","seed":2}).json
        self.assertNotEqual(first["game_id"],second["game_id"])
        self.assertEqual(self.client.get("/api/state").json["game_id"],first["game_id"])
        self.assertEqual(second_client.get("/api/state").json["human_color"],"black")

    def test_move_and_resign_contract(self):
        self.client.post("/api/new",json={})
        state=self.client.get("/api/state").json
        state=self.client.get("/api/state").json
        self.clock.advance(4)
        response=self.client.post("/api/move",json={"move":"e2e3","game_id":state["game_id"],"revision":state["revision"],"human_time":99999})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json["accepted"])
        self.assertEqual(response.json["human_time"],1196)
        self.assertEqual(response.json["phase"],"bot")
        self.assertEqual(response.headers["Cache-Control"],"no-store")
        self.assertEqual(self.client.post("/api/resign",json={"game_id":"wrong"}).status_code,409)
        self.assertEqual(self.client.post("/api/resign",json={"game_id":state["game_id"]}).json["status"],"resigned")

    def test_cross_origin_rejected(self):
        response=self.client.post("/api/new",json={},headers={"Origin":"https://example.com"})
        self.assertEqual(response.status_code,403)


if __name__ == "__main__": unittest.main()
