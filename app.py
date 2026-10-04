"""Local, in-memory Flask app. Run with `python app.py`."""
import logging
import os
import secrets
import threading
import time

from flask import Flask, jsonify, render_template, request, session

from game import Game


def create_app(*, start_scheduler=True, game_factory=Game):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=4096,
                      SESSION_COOKIE_NAME="impossible_martin_session",
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")
    games = {}
    store_lock = threading.RLock()
    shutdown = threading.Event()
    app.extensions["games"] = games
    app.extensions["scheduler_stop"] = shutdown

    def find_game():
        with store_lock:
            game = games.get(session.get("game_id"))
        if game:
            game.last_seen = time.monotonic()
        return game

    def scheduler():
        while not shutdown.wait(0.10):
            with store_lock:
                current = list(games.values())
            for game in current:
                try:
                    with game.lock:
                        game.tick()
                except Exception:
                    app.logger.exception("Game scheduler failed")
                    with game.lock:
                        game.finish("error", "The game stopped after a server error. Start a new game.",
                                    None, game.clock())
            # Keep browser-disconnected games running for an hour; then discard.
            with store_lock:
                stale = [key for key, game in games.items()
                         if time.monotonic() - game.last_seen > 3600]
                for key in stale:
                    del games[key]

    if start_scheduler:
        threading.Thread(target=scheduler, name="chess-scheduler", daemon=True).start()

    @app.after_request
    def response_headers(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.before_request
    def check_local_origin():
        # Prevent another website from sending moves/new-game requests to localhost.
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="Cross-origin requests are not allowed."), 403

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/state")
    def state():
        game = find_game()
        if not game:
            return jsonify(game=None)
        with game.lock:
            return jsonify(game.snapshot())

    @app.post("/api/new")
    def new_game():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="Send a JSON object."), 400
        color = body.get("color", "white")
        seed = body.get("seed")
        if color not in ("white", "black"):
            return jsonify(error="Choose white or black."), 400
        if seed is not None and (type(seed) is not int or not 0 <= seed <= 2**53 - 1):
            return jsonify(error="Seed must be an integer from 0 to 9007199254740991."), 400
        game = game_factory(human_color=color, seed=seed)
        with store_lock:
            previous = session.get("game_id")
            if len(games) >= 64 and previous not in games:
                return jsonify(error="Too many open games. Close an existing session and try later."), 503
            games.pop(previous, None)
            games[game.id] = game
            session["game_id"] = game.id
        with game.lock:
            return jsonify(game.snapshot())

    @app.post("/api/move")
    def move():
        game = find_game()
        if not game:
            return jsonify(error="Start a new game first."), 404
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="Send a JSON object."), 400
        with game.lock:
            accepted, message = game.attempt_move(body.get("move"), body.get("game_id"),
                                                  body.get("revision"))
            return jsonify(game.snapshot(accepted, message)), 200 if accepted else 409

    @app.post("/api/resign")
    def resign():
        game = find_game()
        if not game:
            return jsonify(error="Start a new game first."), 404
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get("game_id") != game.id:
            return jsonify(error="The game changed. Refresh and try again."), 409
        with game.lock:
            game.resign()
            return jsonify(game.snapshot())

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", "5001")),
                     debug=False, threaded=True)
