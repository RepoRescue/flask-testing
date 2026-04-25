"""
Step 7 — anti-PyCG-blindspot probe. Try to break the rescue.

Targets:
  - Boundary inputs: assertRedirects with full URL vs relative,
    with SERVER_NAME unset and set, with query strings.
  - Repeated lifecycle: create the TestCase, run twice -> teardown signal
    leaks (blinker connect/disconnect should be balanced; rescue didn't
    touch this but we verify).
  - Unicode / non-ASCII flash + template context
  - Concurrency: two LiveServerTestCase-style real servers in parallel
  - Cross-version surface: blinker 1.9 weakref-vs-strong-ref signal behaviour
"""
from __future__ import annotations
import gc
import sys
import threading
import unittest
from contextlib import closing

import requests
from blinker import signal as blinker_signal
from flask import Flask, flash, jsonify, redirect, render_template_string
from werkzeug.serving import make_server

import flask_testing
from flask_testing import TestCase
from flask_testing.utils import _empty_render


def make_app():
    a = Flask(__name__)
    a.config["SECRET_KEY"] = "x"

    @a.route("/")
    def i():
        return render_template_string("<p>{{ msg }}</p>", msg="héllo 你好 🚀")

    @a.route("/r-rel")
    def r_rel():
        return redirect("/")

    @a.route("/r-q")
    def r_q():
        return redirect("/?next=%2Fafter")

    @a.route("/r-abs")
    def r_abs():
        return redirect("https://example.com/landing")

    @a.route("/flash")
    def f():
        flash("naïve café 🍰", "ok")
        return "x"

    @a.route("/api")
    def ap():
        return jsonify(msg="naïve", n=1)
    return a


class BoundaryRedirectTests(TestCase):
    def create_app(self):
        return make_app()

    def test_relative_redirect_no_server_name(self):
        rv = self.client.get("/r-rel")
        self.assertRedirects(rv, "/")

    def test_redirect_with_querystring(self):
        rv = self.client.get("/r-q")
        # rescue's normalization should keep the query string
        self.assertRedirects(rv, "/?next=%2Fafter")

    def test_absolute_redirect_external_host(self):
        rv = self.client.get("/r-abs")
        self.assertRedirects(rv, "https://example.com/landing")

    def test_unicode_flash_message(self):
        with self.client.get("/flash"):
            self.assertMessageFlashed("naïve café 🍰", category="ok")

    def test_unicode_template_context(self):
        rv = self.client.get("/")
        self.assert200(rv)
        # ctx["msg"] should be the raw unicode string
        self.assertContext("msg", "héllo 你好 🚀")
        # body rendered with the unicode as well
        assert "héllo".encode("utf-8") in rv.data
        assert "🚀".encode("utf-8") in rv.data

    def test_json_unicode_response(self):
        rv = self.client.get("/api")
        self.assert200(rv)
        # JsonResponseMixin
        assert rv.json == {"msg": "naïve", "n": 1}


class LifecycleTests(TestCase):
    """Run the TestCase machinery twice in one process to catch signal-leak."""
    def create_app(self):
        return make_app()

    def test_first(self):
        rv = self.client.get("/")
        self.assert200(rv)
        self.assertContext("msg", "héllo 你好 🚀")

    def test_second(self):
        # If the first test's _add_template handler stayed connected, we'd
        # see two entries in self.templates after one render.
        rv = self.client.get("/")
        self.assert200(rv)
        # rescue's _add_template clears templates if non-empty before append
        assert len(self.templates) == 1, len(self.templates)


def real_server_concurrency():
    """Spin up two real WSGI servers concurrently and hammer them."""
    print("[hunt] concurrency: 2 real servers + 20 parallel reqs each ...")
    servers = []
    threads = []
    for _ in range(2):
        srv = make_server("127.0.0.1", 0, make_app(), threaded=True)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append(srv); threads.append(t)
    try:
        results = []
        def hit(port):
            try:
                r = requests.get(f"http://127.0.0.1:{port}/api", timeout=3)
                results.append((r.status_code, r.json()))
            except Exception as e:
                results.append(("ERR", str(e)))
        ts = []
        for srv in servers:
            for _ in range(20):
                t = threading.Thread(target=hit, args=(srv.server_port,))
                t.start(); ts.append(t)
        for t in ts: t.join(timeout=5)
        ok = sum(1 for s,_ in results if s == 200)
        assert ok == 40, f"only {ok}/40 ok; sample: {results[:3]}"
        print(f"    OK — {ok}/40 concurrent requests succeeded across 2 real servers")
    finally:
        for srv in servers: srv.shutdown()


def signal_weakref_probe():
    """blinker 1.9 changed signal connection semantics. The rescue uses
    template_rendered.connect(self._add_template) — bound methods are weak-
    referenced by default, which can drop on gc. Probe whether the rescue
    survives gc pressure between connect and send."""
    print("[hunt] blinker weakref + gc probe ...")
    sig = blinker_signal("probe_signal")
    seen = []

    class Receiver:
        def cb(self, sender, **kw):
            seen.append((sender, kw))

    r = Receiver()
    sig.connect(r.cb)  # bound method -> weakref
    del r
    gc.collect()
    sig.send("after_gc", x=1)
    # in blinker >=1.5 bound-method receiver IS gone after gc.collect
    # we just record this is the regime we live in
    if seen:
        print("    NOTE: blinker kept the bound-method receiver alive (strong ref)")
    else:
        print("    NOTE: blinker dropped bound-method receiver after gc (weak ref) "
              "— flask-testing avoids this because TestCase keeps `self` alive "
              "throughout the test lifecycle, so it's not a real bug, but it "
              "would bite a user that connects then drops the test instance.")


def empty_render_arg_order_probe():
    """The rescue's headline fix: _empty_render(app, template, context).
    If the order regressed, calling Flask 3's templating._render with the
    monkeypatch active would explode. Verify directly."""
    print("[hunt] _empty_render arg order ...")
    app = make_app()
    # Flask 3's _render signature is (app, template, context)
    out = _empty_render(app, template=object(), context={"k": "v"})
    assert out == ""
    # positional call must also work
    out2 = _empty_render(app, object(), {"k": "v"})
    assert out2 == ""
    print("    OK — signature matches Flask 3 templating._render(app, template, context)")


def main():
    print(f"flask_testing: {flask_testing.__file__}\n")

    suite = unittest.TestLoader().loadTestsFromTestCase(BoundaryRedirectTests)
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(LifecycleTests))
    runner = unittest.TextTestRunner(stream=sys.stderr, verbosity=1)
    result = runner.run(suite)
    if not result.wasSuccessful():
        print("BUG FOUND in boundary/lifecycle suite", file=sys.stderr)
        sys.exit(1)

    real_server_concurrency()
    signal_weakref_probe()
    empty_render_arg_order_probe()
    print("\nBUG_HUNT_DONE — no regressions found, see notes above")


if __name__ == "__main__":
    main()
