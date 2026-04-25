"""
Path B scenario: pretend to be a downstream developer who has only read the
README + flask-testing docs, and is writing a real test suite for a small
"todo list" Flask service.

This script is run from a CLEAN venv with `pip install -e <rescue>` already
done. It deliberately does NOT import anything from `tests/` of the rescue.
It exercises the public flask-testing API as a library user would, and uses
unittest's runner to surface failures the same way a CI pipeline would.

What we're "shipping": a Flask `todo_app` with two blueprints, JSON + HTML,
flash messages, redirect-after-POST, and an error path. We then write a
flask-testing-based test suite for it. If flask-testing's assertion API is
broken under Python 3.13 / Flask 3 / blinker 1.9 / Werkzeug 3, this script
fails.
"""
from __future__ import annotations
import json
import sys
import unittest

from flask import Blueprint, Flask, abort, flash, jsonify, redirect, render_template_string, request, url_for
from flask_testing import TestCase


# ---------------------------------------------------------------------------
# The "downstream" application under test (≥30 lines of real business code)
# ---------------------------------------------------------------------------
def create_todo_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "dev"
    app.config["TESTING"] = True

    store: dict[int, dict] = {}
    counter = {"n": 0}

    web = Blueprint("web", __name__)
    api = Blueprint("api", __name__, url_prefix="/api")

    @web.route("/")
    def home():
        return render_template_string(
            "<h1>{{ title }}</h1>{% for t in todos %}<li>{{ t.text }}</li>{% endfor %}",
            title="My Todos",
            todos=list(store.values()),
        )

    @web.route("/todos", methods=["POST"])
    def create_web():
        text = request.form.get("text", "").strip()
        if not text:
            flash("Text is required", "error")
            return redirect(url_for("web.home"))
        counter["n"] += 1
        store[counter["n"]] = {"id": counter["n"], "text": text, "done": False}
        flash(f"Added: {text}", "success")
        return redirect(url_for("web.home"))

    @api.route("/todos")
    def list_api():
        return jsonify(todos=list(store.values()), total=len(store))

    @api.route("/todos/<int:tid>")
    def get_api(tid):
        if tid not in store:
            abort(404)
        return jsonify(store[tid])

    @app.errorhandler(404)
    def nf(_):
        return jsonify(error="not found"), 404

    app.register_blueprint(web)
    app.register_blueprint(api)
    # seed
    store[1] = {"id": 1, "text": "buy milk", "done": False}
    counter["n"] = 1
    return app


# ---------------------------------------------------------------------------
# Downstream tests, written purely against flask-testing's documented API
# ---------------------------------------------------------------------------
class TodoTests(TestCase):
    def create_app(self):
        return create_todo_app()

    def test_home_renders_seed(self):
        rv = self.client.get("/")
        self.assert200(rv)
        self.assertIn(b"buy milk", rv.data)
        # template + context tracked via blinker signals
        self.assertContext("title", "My Todos")

    def test_api_list_json(self):
        rv = self.client.get("/api/todos")
        self.assert200(rv)
        body = json.loads(rv.data)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["todos"][0]["text"], "buy milk")

    def test_api_404_path(self):
        rv = self.client.get("/api/todos/999")
        self.assert404(rv)
        self.assertEqual(json.loads(rv.data), {"error": "not found"})

    def test_create_redirect_and_flash(self):
        rv = self.client.post("/todos", data={"text": "walk dog"})
        # redirect goes to web.home, which is "/" (relative location under Werkzeug 3)
        self.assertRedirects(rv, "/")
        self.assertMessageFlashed("Added: walk dog", category="success")

    def test_create_validation_flash(self):
        rv = self.client.post("/todos", data={"text": "   "})
        self.assertRedirects(rv, "/")
        self.assertMessageFlashed("Text is required", category="error")

    def test_status_helpers_chain(self):
        # 200 -> 200 -> 404 chain over a single test, exercises teardown
        self.assert200(self.client.get("/"))
        self.assert200(self.client.get("/api/todos/1"))
        self.assert404(self.client.get("/api/todos/42"))


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TodoTests)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    if not result.wasSuccessful():
        print("SCENARIO_FAILED", file=sys.stderr)
        return 1
    assert result.testsRun == 6, result.testsRun
    print(f"SCENARIO_OK ({result.testsRun} tests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
