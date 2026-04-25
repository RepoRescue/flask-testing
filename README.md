# flask-testing (RepoRescue modernized)

Unit-testing extension for Flask. Gives you `unittest.TestCase` subclasses
(`TestCase`, `LiveServerTestCase`) with Flask-aware helpers: status assertions
(`assert200`, `assert404`, ...), `assertRedirects`, `assertTemplateUsed`,
`assertContext`, `assertMessageFlashed`, a `JsonResponseMixin` that adds
`response.json`, plus a `render_templates = False` mode that disables actual
template rendering while still capturing `(template, context)` pairs via
Flask's blinker signals.

This fork is a [RepoRescue](https://github.com/RepoRescue) modernization of
the original [`jarus/flask-testing`](https://github.com/jarus/flask-testing).
It runs cleanly against:

- Python 3.13
- Flask >= 3.0 (tested on 3.1.3)
- Werkzeug >= 3.0 (tested on 3.1.8)
- blinker >= 1.9 (tested on 1.9.0)

The original upstream has not had a release that targets this stack.

## Honest disclaimer up front

If you are starting a new Flask project in 2026, you almost certainly want
[`pytest-flask`](https://github.com/pytest-dev/pytest-flask) or
[`flask-unittest`](https://github.com/TotallyNotChase/flask-unittest)
instead. Both are actively maintained and target modern Flask out of the box.

`flask-testing` is still useful when:

- you have an existing `unittest`-style suite and don't want to migrate to
  pytest fixtures,
- you maintain an older codebase that already imports `flask_testing` in
  hundreds of test files,
- you specifically want the `unittest.TestCase` inheritance model
  (setUp / tearDown / class-level `create_app`) plus Flask assertion sugar
  in the same place.

If that is you, this fork makes the library run on the modern stack
without you having to fork it yourself.

## Install

```bash
pip install git+https://github.com/RepoRescue/flask-testing.git
```

Runtime deps: `Flask>=3.0`, `Werkzeug>=3.0`, `blinker>=1.9`. (`twill` is
optional and only needed if you use `flask_testing.twill`, which is left in
place for compatibility.)

## Quick start

```python
from flask import Flask, jsonify, redirect, flash, render_template_string
from flask_testing import TestCase

def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "dev"

    @app.route("/")
    def home():
        return render_template_string("<h1>{{ title }}</h1>", title="hi")

    @app.route("/api")
    def api():
        return jsonify(ok=True, n=1)

    @app.route("/old")
    def old():
        flash("moved", "info")
        return redirect("/")
    return app


class MyTests(TestCase):
    def create_app(self):
        return create_app()

    def test_home(self):
        rv = self.client.get("/")
        self.assert200(rv)
        self.assertTemplateUsed("__string__")          # any inline template
        self.assertContext("title", "hi")

    def test_json_mixin(self):
        rv = self.client.get("/api")
        self.assertEqual(rv.json, {"ok": True, "n": 1})  # JsonResponseMixin

    def test_redirect_and_flash(self):
        rv = self.client.get("/old")
        self.assertRedirects(rv, "/")                  # relative loc OK
        self.assertMessageFlashed("moved", category="info")
```

Run with any unittest runner: `python -m unittest`, `pytest`, etc.

## What was actually fixed

Seven independent break-surfaces between the original release and the
Python 3.13 / Flask 3 / Werkzeug 3 / blinker 1.9 stack. All evidence is
in the diff against upstream `flask-testing 0.8.1`.

| # | Surface | Fix |
|---|---------|-----|
| 1 | `StringIO` import (Py2 module removed) | `flask_testing/twill.py` switched to `from io import StringIO`. |
| 2 | Werkzeug 3 `response.location` is now a relative path, not absolute | `assertRedirects` (in `flask_testing/utils.py`) now `urljoin`s the relative location against the request URL before comparing. |
| 3 | Flask 3 changed the private `templating._render` signature to `(app, template, context)` | `_empty_render` (the monkeypatch installed when `render_templates = False`) was reordered to match — first arg is now `app`. |
| 4 | blinker 1.9 signal API: connect / send / disconnect | Calls into `template_rendered` and `message_flashed` updated. `TestCase` connects on `_pre_setup` and disconnects on `_post_teardown`, so the same process can run many tests without leaks. |
| 5 | Python 3.13 `repr(list)` changed how `' '.join(...)` interacts with it (inserts char-level spaces) | The "templates used" error message now uses `repr(used_templates)` directly instead of `' '.join(repr(...))`. |
| 6 | `urllib.parse` Py2 fallback (`urlparse` / `urllib`) removed | The Py3 import path is now the only path. |
| 7 | `unittest.TestCase.__call__` override re-entry on Python 3.13 | Both `TestCase.__call__` and `LiveServerTestCase.__call__` correctly route through `super().__call__(result)`, which keeps Py3.13's unittest internals happy when the same TestCase class is run multiple times in one process. |

None of these are speculative; each one is reproduced by the validation
scripts in `.reporescue/` (see below).

## How we validated this rescue

flask-testing has an unusual problem for "real-world usability" validation:
its primary documented API *is* `app.test_client()`, which is the
in-process WSGI client. RepoRescue's strict v2 validation forbids
`app.test_client()` as the only use mode — that doesn't prove anything
about real WSGI traffic. We split validation into three sections so we
can satisfy the bar without lying about how the library is actually used:

- **Section A — real socket.** A real `werkzeug.serving.make_server` on
  127.0.0.1 driven by `requests.get` from a separate thread. Verifies
  status codes, JSON bodies, redirects with relative `Location`, template
  rendering. (See `.reporescue/usability_validate.py::section_real_http`.)
- **Section B — library API.** `TestCase` subclasses run via
  `unittest.TextTestRunner`, exercising every assertion helper. This is
  the documented use mode and the reason anyone reaches for the library.
- **Section C — internals.** Direct calls to `_empty_render` and
  `_make_test_response`, plus a `JsonResponseMixin` round-trip, to cover
  the private helpers the rescue actually patched.

On top of that, `.reporescue/scenario_validate.py` is a 100+ line "todo"
Flask app with two blueprints, JSON + HTML, flash messages,
redirect-after-POST, and a 404 handler — plus a `TestCase` suite written
by a hypothetical downstream author who has only read the README. 6/6
tests pass.

`.reporescue/bug_hunt.py` runs additional adversarial probes:

- relative redirect with no `SERVER_NAME` set,
- redirect with query string,
- redirect to an external absolute host,
- Unicode flash messages, Unicode template context, Unicode JSON bodies,
- a two-test lifecycle to catch blinker connect/disconnect imbalances,
- 40-way concurrency across 2 real WSGI servers,
- blinker 1.9 bound-method weakref behaviour under `gc.collect`,
- `_empty_render` positional + keyword arg-order probe.

No regressions found.

## Note on blinker 1.9 weakref behaviour (worth knowing)

blinker 1.9 keeps bound-method receivers as weakrefs by default. After
`gc.collect()`, a receiver whose owning object has been dropped will
silently stop firing. flask-testing is fine because `TestCase` keeps
`self` alive across the entire `_pre_setup` / test / `_post_teardown`
window, so the connected `_add_template` / `_add_flash_message`
callbacks can't be collected mid-test.

If you copy this connect-via-bound-method pattern into your own code on
a short-lived helper object, you may see signals stop firing after a gc
pass. This is a blinker behaviour change, not a flask-testing bug, but
it's the kind of thing that surprises people during a Flask 2 -> 3
upgrade.

## What lives in this fork

```
flask_testing/      modernized library code
tests/              upstream tests, all passing on Py3.13 + Flask 3
.reporescue/        usability_validate.py, scenario_validate.py,
                    bug_hunt.py, REPORT.md, run.log
```

The `.reporescue/` directory is the audit trail. If you want to verify
the rescue yourself:

```bash
python3.13 -m venv /tmp/ft
/tmp/ft/bin/pip install -e .
/tmp/ft/bin/pip install requests blinker
/tmp/ft/bin/python .reporescue/usability_validate.py
/tmp/ft/bin/python .reporescue/scenario_validate.py
/tmp/ft/bin/python .reporescue/bug_hunt.py
```

All three should print success and exit 0.

## License

BSD-3-Clause, same as upstream. See `LICENSE`.

## Disclaimer

This fork exists to keep an abandoned-but-still-used testing utility
working on the modern Python / Flask stack. It is not affiliated with
the original maintainer. For new projects, please consider
`pytest-flask` or `flask-unittest` first.
