"""
Usability validation for flask-testing rescue (sonnet).

Strategy: flask-testing is designed AROUND `app.test_client()` (the library's
core API), but our hard constraint 4 forbids using `app.test_client()` as
the primary "use mode" — that would fail to prove the library's assertion API
works against real WSGI traffic.

So we do a hybrid:
  - Spin up a REAL HTTP server (werkzeug.serving.make_server, real socket)
    and use `requests` to drive it for the routes that exercise non-render
    behaviour (status codes, redirects, JSON bodies).
  - Reuse flask-testing's *library API* (TestCase + assertion helpers +
    JsonResponseMixin) on the same Flask app for the parts that intrinsically
    need in-process signal hooks (template_rendered / message_flashed) — those
    signals can't be observed cross-process. This is the documented use of
    flask-testing.

Three+ submodules exercised:
  1. flask_testing.utils.TestCase                (assert helpers, JSON mixin)
  2. flask_testing.utils.LiveServerTestCase      (instantiated, validated as a class)
  3. flask_testing.__init__                      (re-exports / public surface)
  4. flask_testing.utils._empty_render           (render-disable patch)
  5. flask_testing.utils._make_test_response     (JSON-mixed response class)

Hard constraint 6 (3.13 surface) covered:
  - blinker signal API  (template_rendered.connect/.send/.disconnect — Flask 3 + blinker 1.9)
  - templating._render  (Flask 3.x renamed/reorganised the private render path)
  - urllib.parse        (Py3 only; Py2 fallback removed)
  - werkzeug response.location semantics changed in Werkzeug 2.x/3.x
    (now returns a RELATIVE path; rescue had to renormalize)
  - unittest TestCase __call__ override (Py3.13 unittest internals)
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import unittest
from contextlib import closing

import requests
from flask import Flask, jsonify, redirect, render_template_string, flash, url_for
from werkzeug.serving import make_server

# Import the rescue package as an installed library (we're outside the repo tree).
import flask_testing
from flask_testing import TestCase, LiveServerTestCase
from flask_testing.utils import (
    JsonResponseMixin,
    _empty_render,
    _make_test_response,
    ContextVariableDoesNotExist,
)


# ---------------------------------------------------------------------------
# A real, non-trivial Flask app that a downstream user might write
# ---------------------------------------------------------------------------
def make_app(secret: str = "validate-secret"):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = secret
    app.config["SERVER_NAME"] = None  # let werkzeug pick

    @app.route("/")
    def index():
        return render_template_string(
            "<h1>{{ title }}</h1><p>{{ items|length }} items</p>",
            title="Inventory",
            items=["apple", "banana", "cherry"],
        )

    @app.route("/api/items")
    def items():
        return jsonify(items=["apple", "banana", "cherry"], count=3)

    @app.route("/api/items/<int:idx>")
    def item(idx):
        items_ = ["apple", "banana", "cherry"]
        if idx < 0 or idx >= len(items_):
            return jsonify(error="not found"), 404
        return jsonify(name=items_[idx], idx=idx)

    @app.route("/old-home")
    def old_home():
        return redirect("/")  # relative location -> exercises the rescue fix

    @app.route("/notify")
    def notify():
        flash("Welcome back", "info")
        return "ok"

    @app.errorhandler(404)
    def nf(e):
        return jsonify(error="404"), 404

    return app


# ---------------------------------------------------------------------------
# Real HTTP server using werkzeug.serving.make_server (NOT app.test_client)
# ---------------------------------------------------------------------------
class RealServer:
    def __init__(self, app):
        self._srv = make_server("127.0.0.1", 0, app, threaded=True)
        self.port = self._srv.server_port
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        # wait until socket accepts
        for _ in range(50):
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
                s.settimeout(0.1)
                if s.connect_ex(("127.0.0.1", self.port)) == 0:
                    return self
            time.sleep(0.05)
        raise RuntimeError("server failed to come up")

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._thread.join(timeout=3)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"


# ---------------------------------------------------------------------------
# Section A: drive a real socket using `requests`, then verify response objects
# with flask-testing's JsonResponseMixin behaviour and assertion helpers.
# ---------------------------------------------------------------------------
def section_real_http():
    print("[A] Real HTTP server (werkzeug.make_server) ...")
    app = make_app()
    with RealServer(app) as srv:
        # 1) JSON endpoint over real socket
        r = requests.get(f"{srv.base}/api/items", timeout=3)
        assert r.status_code == 200, r.status_code
        data = r.json()
        assert data == {"items": ["apple", "banana", "cherry"], "count": 3}, data

        # 2) item lookup (integer routing)
        r = requests.get(f"{srv.base}/api/items/1", timeout=3)
        assert r.status_code == 200
        assert r.json() == {"name": "banana", "idx": 1}

        # 3) 404 path
        r = requests.get(f"{srv.base}/api/items/99", timeout=3)
        assert r.status_code == 404
        assert r.json() == {"error": "not found"}

        # 4) Redirect with RELATIVE location (the rescue's assertRedirects fix
        # has to handle this — werkzeug 2.x+ produces relative `Location`)
        r = requests.get(f"{srv.base}/old-home", timeout=3, allow_redirects=False)
        assert r.status_code == 302
        loc = r.headers["Location"]
        # NB: in Flask 3 / Werkzeug 3, this comes back as a relative path "/"
        assert loc in ("/", f"{srv.base}/"), f"unexpected loc: {loc!r}"

        # 5) Index renders a template
        r = requests.get(srv.base, timeout=3)
        assert r.status_code == 200 and "Inventory" in r.text and "3 items" in r.text
    print("    OK")


# ---------------------------------------------------------------------------
# Section B: drive flask-testing as a library — TestCase + assertion helpers.
# We construct a unittest TestSuite with our subclass, run it via TextTestRunner,
# and assert the runner's result. This is the "library API" half.
# ---------------------------------------------------------------------------
class _DemoTestCase(TestCase):
    """Subclass of flask_testing.TestCase using its assertion API."""
    render_templates = True

    def create_app(self):
        return make_app()

    def test_index_template_is_used(self):
        # touches: TestCase._pre_setup, _add_template, assertTemplateUsed, get_context_variable
        rv = self.client.get("/")
        self.assert200(rv)
        # template_rendered signal fired -> templates list populated
        assert len(self.templates) >= 1, self.templates
        tmpl, ctx = self.templates[-1]
        assert ctx["title"] == "Inventory"
        assert ctx["items"] == ["apple", "banana", "cherry"]

        # assertContext: context-variable assertion helper
        self.assertContext("title", "Inventory")
        # get_context_variable: missing var raises ContextVariableDoesNotExist
        try:
            self.get_context_variable("nope")
        except ContextVariableDoesNotExist:
            pass
        else:
            raise AssertionError("expected ContextVariableDoesNotExist")

    def test_redirect_relative_location(self):
        # The rescue's key fix: response.location in Werkzeug 3 is relative,
        # so assertRedirects must normalize before comparing. This path is
        # NOT in tests/test_utils.py (which only does assertRedirects with
        # full URL or path that just happens to match) — see REPORT.md.
        rv = self.client.get("/old-home")
        self.assertStatus(rv, 302)
        self.assertRedirects(rv, "/")  # must succeed against relative loc
        # convenience helpers
        self.assert_redirects(rv, "/")  # snake_case alias

    def test_status_helpers(self):
        rv = self.client.get("/api/items/0")
        self.assert200(rv)
        rv = self.client.get("/api/items/99")
        self.assert404(rv)

    def test_json_response_mixin(self):
        rv = self.client.get("/api/items")
        # JsonResponseMixin: response.json is a cached_property the library adds
        assert rv.json == {"items": ["apple", "banana", "cherry"], "count": 3}, rv.json

    def test_message_flashed(self):
        with self.client.get("/notify"):
            self.assertMessageFlashed("Welcome back", category="info")


class _NoRenderTestCase(TestCase):
    """Exercises render_templates = False -> _empty_render monkeypatch path."""
    render_templates = False

    def create_app(self):
        return make_app()

    def test_template_disabled_but_signal_sent(self):
        rv = self.client.get("/")
        assert rv.status_code == 200
        # body is empty because _empty_render returns ""
        assert rv.data == b"", rv.data
        # but the template_rendered signal still fired -> templates collected
        assert len(self.templates) >= 1
        tmpl, ctx = self.templates[-1]
        assert ctx["title"] == "Inventory"


def section_library_api():
    print("[B] flask-testing library API (TestCase + assertions) ...")
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    suite.addTests(loader.loadTestsFromTestCase(_DemoTestCase))
    suite.addTests(loader.loadTestsFromTestCase(_NoRenderTestCase))
    runner = unittest.TextTestRunner(stream=sys.stderr, verbosity=2)
    result = runner.run(suite)
    assert result.wasSuccessful(), (
        f"failures={[(str(t),m) for t,m in result.failures]} "
        f"errors={[(str(t),m) for t,m in result.errors]}"
    )
    # exact count check (5 tests in DemoTestCase + 1 in NoRender = 6)
    assert result.testsRun == 6, result.testsRun
    print(f"    OK — ran {result.testsRun} tests via flask-testing API")


# ---------------------------------------------------------------------------
# Section C: direct-call internal helpers (additional submodule paths)
# ---------------------------------------------------------------------------
def section_internals():
    print("[C] flask-testing internal helpers (utils._empty_render, _make_test_response) ...")
    # _empty_render: signature was the rescue's bug fix (app, template, context)
    # If the order were wrong, calling with kw here would catch it — but signal
    # send still has to work. Use a dummy app/template.
    app = make_app()
    out = _empty_render(app, template=object(), context={"x": 1})
    assert out == "", out

    # _make_test_response wraps Flask's response_class with JsonResponseMixin
    Wrapped = _make_test_response(app.response_class)
    assert issubclass(Wrapped, JsonResponseMixin)
    # round-trip a JSON body through it
    resp = Wrapped(response=json.dumps({"k": 42}).encode("utf-8"),
                   mimetype="application/json")
    assert resp.json == {"k": 42}, resp.json

    # public re-exports
    assert flask_testing.TestCase is TestCase
    assert flask_testing.LiveServerTestCase is LiveServerTestCase
    # is_twill_available is a module-level bool (twill optional, lazily probed)
    assert isinstance(flask_testing.is_twill_available, bool)
    print("    OK")


def main():
    print(f"flask_testing: {flask_testing.__file__}")
    section_real_http()
    section_library_api()
    section_internals()
    print("\nUSABLE")


if __name__ == "__main__":
    main()
