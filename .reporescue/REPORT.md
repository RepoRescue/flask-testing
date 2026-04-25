# flask-testing — Usability Validation

**Selected rescue**: `sonnet` (srconly: PASS)
**Scenario type**: B (end-user library API), with real-HTTP layer added so primary use-mode is not just `app.test_client()`
**Real-world use**: Library used by Flask app authors as a `unittest.TestCase` subclass that gives them flask-aware assertion helpers (`assertRedirects`, `assertContext`, `assertTemplateUsed`, `assertMessageFlashed`, `assert{200,400,401,404,500}`) and a JSON-decoding response mixin.

## Step 0: Import sanity

```
$ repos/rescue_sonnet/flask-testing/venv-t2/bin/python -c "import flask_testing"
flask_testing: /home/zhihao/hdd/RepoRescue_Clean/repos/rescue_sonnet/flask-testing/flask_testing/__init__.py
exports: TestCase, LiveServerTestCase, Twill, TwillTestCase, is_twill_available, twill, utils
```
-> OK

## Step 1: Best rescue selection

| Model  | T2 | T2 srconly |
|--------|----|------------|
| sonnet | PASS | **PASS** |
| minimax / kimi / glm | PASS | (not checked; sonnet wins on priority + srconly) |

Picked sonnet because it tops the priority list AND its source-only patch passes T2 - a real source fix, not "AI quietly installed an extra wheel".

## Step 2: Scenario type

flask-testing is a type-B end-user library API: `pip install flask-testing`, subclass `TestCase`, write asserts. There is no CLI, it's not a parser, it's not directly an agent-callable analyser.

It is itself a testing framework - its primary documented use IS `self.client = app.test_client()`. Hard constraint 4 forbids `app.test_client()` as the primary use mode (in-process WSGI). Resolution: two-layer validation.

1. **Section A (real HTTP)** - `werkzeug.serving.make_server` on a real socket, driven by `requests.get` from a separate thread. This is the "library is not lying about WSGI / response semantics" check that flask-testing's own helpers cannot give you.
2. **Section B (library-API)** - `TestCase` subclasses run via `unittest.TextTestRunner`. Documented use mode, exercising every assertion helper.

Both layers must pass.

## Step 4: Install + core feature (clean venv)

```bash
python3.13 -m venv /tmp/flask-testing-clean
/tmp/flask-testing-clean/bin/pip install -e repos/rescue_sonnet/flask-testing
/tmp/flask-testing-clean/bin/pip install requests blinker
cd /tmp/flask-testing-clean
/tmp/flask-testing-clean/bin/python <abs-path>/artifacts/flask-testing/usability_validate.py
```

Result: **PASS** (see `run.log`).
Installed: Flask 3.1.3 / Werkzeug 3.1.8 / blinker 1.9.0 / requests 2.33.1 / Flask-Testing 0.8.1 (-e from rescue tree).

## Submodules touched (hard constraint 5: >=3)

| # | Module | Evidence |
|---|--------|----------|
| 1 | `flask_testing` (top-level) | `flask_testing.TestCase`, `flask_testing.LiveServerTestCase`, `flask_testing.is_twill_available` |
| 2 | `flask_testing.utils.TestCase` | 5 assertion helpers (`assert200/404`, `assertRedirects`, `assertContext`, `assertTemplateUsed`, `assertMessageFlashed`) + lifecycle |
| 3 | `flask_testing.utils.JsonResponseMixin` | `rv.json` cached_property used 4x |
| 4 | `flask_testing.utils._empty_render` | direct call + monkeypatch via `render_templates=False` |
| 5 | `flask_testing.utils._make_test_response` | direct call, `Wrapped(...).json` round-trip |

5 distinct submodules / public APIs (target >=3).

## Hard constraint 6: Py3.13 / Flask3 / Werkzeug3 / blinker1.9 surfaces stressed

| Surface | Evidence |
|---------|----------|
| `StringIO` (Py2 module removed) | `flask_testing/twill.py:14-17` rescue switched to `from io import StringIO` |
| `response.location` semantics (Werkzeug 2/3 returns relative path) | rescue patched `flask_testing/utils.py:316-321` to `urljoin` relative locations; `usability_validate.py::section_real_http` step 4 + `bug_hunt.py::test_relative_redirect_no_server_name` exercise end-to-end |
| Flask 3 `templating._render` signature | rescue swapped `_empty_render(template, context, app)` -> `_empty_render(app, template, context)` (`utils.py:91`); `bug_hunt.py::empty_render_arg_order_probe` calls keyword + positional |
| blinker 1.9 signal API | `template_rendered.connect/.send/.disconnect` + `message_flashed`; `LifecycleTests` runs twice in one process to verify connect/disconnect balance; `bug_hunt.py::signal_weakref_probe` documents bound-method weakref regime |
| `repr(list)` formatting in 3.13 | rescue removed `' '.join(repr(used_templates))` (inserts char-level spaces in 3.13) -> bare `repr(used_templates)` (`utils.py:254`) |
| `urllib.parse` (Py2 fallback removed) | `utils.py:30-33` Py3 path is live |
| `unittest.TestCase.__call__` override (3.13 unittest internals) | `TestCase.__call__` and `LiveServerTestCase.__call__` invoke `super().__call__(result)`; tests run multiple times same process |

7 distinct break-surfaces stressed - well above the >=1 floor. **Not TRIVIAL_RESCUE.**

## Beyond unit tests (constraint 3)

`tests/test_utils.py` already calls `self.assertRedirects(response, "/")`, so repeating that wouldn't qualify. Our coverage that goes beyond:

- **Real TCP socket** via `werkzeug.serving.make_server` + `requests.get`. Grep: `grep -n "make_server\|serve_forever\|requests\." tests/test_utils.py` -> 0 matches.
- **40-way concurrency** (2 real servers x 20 parallel reqs) in `bug_hunt.py::real_server_concurrency` - tests/ has zero concurrency.
- **Unicode** (`hello 你好 ROCKET`, `naive cafe CAKE`) - tests/test_utils.py is ASCII-only.
- **Two-test lifecycle** asserts `len(self.templates) == 1` after second render - upstream would silently tolerate a leak.
- **Direct `_empty_render` / `_make_test_response`** (Section C) - private helpers, hit only indirectly by tests/.

## Step 6: Downstream / Scenario

- **Path A**: WebSearch for star>=100, recent-commit downstreams declaring flask-testing as test dep. Found only `jarus/flask-testing` itself, plus `pytest-flask` and `TotallyNotChase/flask-unittest` (both alternatives, NOT downstream). flask-testing reverse-deps on PyPI are mostly abandoned. **Path A skipped.**
- **Path B**: `scenario_validate.py` - 100+ line todo-list Flask app (two blueprints, JSON, flash, redirect-after-POST, 404 handler) + a `TestCase` suite written as a downstream author. **6/6 tests PASS.**

Constraint 8 satisfied via Path B.

## Step 7: Bug-hunt

`bug_hunt.py` ran 6 boundary tests + 2 lifecycle tests + concurrency + signal-weakref probe + arg-order probe.

- Tried: relative-loc redirect with no SERVER_NAME, redirect with query-string, external-host absolute redirect, Unicode flash, Unicode template ctx, Unicode JSON, two-test lifecycle leak check, 40-way concurrent real-HTTP, blinker bound-method weakref under gc, `_empty_render` positional/keyword.
- Found: **no regressions** introduced by the rescue.
- Note: blinker 1.9 drops bound-method receivers after gc when the holding object dies. flask-testing keeps `self` alive across `_pre_setup`/`_post_teardown` so this is fine for the library, but a downstream user mimicking the pattern by hand on a short-lived object could see signals silently stop firing. Docs-note territory, not a rescue blocker.

## Verdict

STATUS: USABLE

Reason: sonnet's source-only rescue installs cleanly into a fresh Python 3.13 venv, lets a downstream developer write a real Flask test suite using the documented `TestCase` API (6/6 scenario tests pass), and survives end-to-end with real WSGI traffic over a real socket including 40-way concurrency, Unicode payloads, and signal-lifecycle re-entry. Seven independent Py3.13 / Flask 3 / Werkzeug 3 / blinker 1.9 break-surfaces are exercised - this is a real rescue, not TRIVIAL_RESCUE.
