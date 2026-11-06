"""Pure core for human-workflow evaluation: loopback fixture, independent verdicts, summaries.

stdlib only. No Qt, Ollama or app access. Expected answers live in the fixture and runner only
and must never be put into a model prompt.
"""
from __future__ import annotations

import html
import math
import random
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

PROTOCOL = "human-workflows-v1"
STATUSES = ("pass", "fail", "setup_error", "blocked", "timeout", "cancelled")

_ADJ = ("amber", "brisk", "cobalt", "dusty", "ember", "frosty", "gentle", "hazel", "ivory", "jolly")
_NOUN = ("kettle", "lantern", "satchel", "teapot", "ladder", "compass", "blanket", "trowel")
_TOK = ("quill", "mango", "fjord", "lotus", "cedar", "pixel", "ridge", "tulip", "onyx", "basil")


class FixtureServer:
    """Loopback HTML fixture; `seed` picks facts, a random per-instance nonce guards the URLs."""

    def __init__(self, seed):
        rng = random.Random(seed)
        toks = rng.sample(_TOK, 2)
        adj = rng.sample(_ADJ, 3)
        noun = rng.choice(_NOUN)
        prices = rng.sample(range(20, 999), 3)
        identifiers = rng.sample([number for number in range(100, 1000) if number not in prices], 3)
        self._items = [(f"{a} {noun} {number}", str(p)) for a, number, p in zip(adj, identifiers, prices)]
        self._facts = {
            "delivery": f"{toks[0]}-{rng.randint(100, 999)}",
            "pickup": f"{toks[1]}-{rng.randint(100, 999)}",
            "product": self._items[0][0],
            "price": self._items[0][1],
        }
        self._nonce = secrets.token_hex(8)
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._server = None
        self._thread = None

    # -- lifecycle -------------------------------------------------------
    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer._handle(self)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        try:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
        finally:
            if self._thread is not None:
                self._thread.join(timeout=5)
        return False

    # -- public ----------------------------------------------------------
    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("FixtureServer is not running")
        return f"http://127.0.0.1:{self._server.server_address[1]}/{self._nonce}"

    @property
    def facts(self) -> dict:
        return dict(self._facts)

    def events_since(self, offset: int) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._events[max(int(offset), 0):]]

    def event_count(self) -> int:
        with self._lock:
            return len(self._events)

    # -- internals -------------------------------------------------------
    def _page(self, title, body):
        return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
                f"</head><body><h1>{html.escape(title)}</h1>{body}</body></html>")

    def _render(self, path, query):
        root = f"/{self._nonce}"
        f = self._facts
        if path == f"{root}/home":
            return self._page("A11y fixture home", (
                f"<p>Delivery: {f['delivery']}</p><p>Pickup: {f['pickup']}</p>"
                "<p>Store hours: 9 to 5, closed Sundays.</p>"
                f"<p><a href='{root}/delivery'>Delivery details {f['delivery']}</a> "
                f"<a href='{root}/catalog'>Catalog</a></p>"))
        if path == f"{root}/delivery":
            return self._page("Delivery details", (
                f"<p>Delivery code: {f['delivery']}</p><a href='{root}/home'>Home</a>"))
        if path == f"{root}/catalog":
            q = dict(parse_qsl(query)).get("q", "").strip().lower()
            rows = [(n, p) for n, p in self._items if not q or q in n.lower()]
            if q and not rows:
                noun = self._items[0][0].split()[1]
                rows = [(n, p) for n, p in self._items if noun in q and noun in n]
            items = "".join(f"<li>{html.escape(n)} - price {p}</li>" for n, p in rows)
            return self._page("Catalog", (
                f"<form method='get' action='{root}/catalog'><label>Search "
                "<input name='q' type='text'></label><button type='submit'>Search</button></form>"
                f"<ul>{items}</ul><a href='{root}/home'>Home</a>"))
        return None

    def _handle(self, h):
        parts = urlsplit(h.path)
        with self._lock:
            self._events.append({"t": time.monotonic(), "path": parts.path, "query": parts.query})
        page = self._render(parts.path, parts.query)
        data = (page or "not found").encode()
        h.send_response(200 if page else 404)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)


# -- verdicts ------------------------------------------------------------
_BAD_REPLY = re.compile(
    r"\b(can't|cannot|can not|couldn't|could not|unable|don't know|do not know|not sure|unsure|"
    r"unclear|might|maybe|perhaps|probably|i think|seems|not|no|isn't|wasn't|didn't|sorry|denied|"
    r"permission|i'll|i will|let me|going to|looking|checking|trying|about to)\b", re.I)


def _url_key(url):
    p = urlsplit(url or "")
    return (p.scheme.lower(), p.netloc.lower(), p.path, tuple(sorted(parse_qsl(p.query, keep_blank_values=True))))


def _same_url(a, b):
    return bool(a) and bool(b) and _url_key(a) == _url_key(b)


def _fact_in(reply, fact):
    reply = reply.replace('’', "'")
    pat = r"(?<!\w)(?<!\w[.,\-])" + re.escape(str(fact)) + r"(?!\w)(?![.,\-]\w)"
    return bool(re.search(pat, reply, re.I)) and not _BAD_REPLY.search(reply)


def _resolve(p):
    return Path(p).resolve()


def _check(kind, replies, expected, actual, before, events):
    """Return (ok, reason, setup_error) for the independent outcome."""
    transition = expected.get("require_transition", True)
    if kind == "read":
        fact = expected.get("fact")
        if not fact:
            return False, "expected fact missing", True
        if not [r for r in replies if str(r).strip()]:
            return False, "empty reply", False
        final_reply = next((str(r) for r in reversed(replies) if str(r).strip()), '')
        if not _fact_in(final_reply, fact):
            return False, "reply lacks exact fact or is denied/uncertain/negated", False
        if expected.get("require_http") and not events:
            return False, "no fixture request observed", False
        return True, "fact stated exactly", False
    if kind == "navigate":
        want = expected.get("url")
        if not want:
            return False, "expected url missing", True
        if "url" not in actual:
            return False, "actual url not supplied", True
        if not _same_url(actual["url"], want):
            return False, "wrong destination url", False
        if transition and _same_url(before.get("url"), want):
            return False, "destination already satisfied before the turn", False
        if expected.get("require_http"):
            key = _url_key(actual["url"])
            def hit(e):
                if e.get("url"):
                    return _url_key(e["url"]) == key
                return e.get("path") == key[2] and tuple(sorted(parse_qsl(e.get("query") or "", keep_blank_values=True))) == key[3]
            if not any(hit(e) for e in events):
                return False, "no matching fixture request after the stimulus", False
        return True, "destination reached", False
    if kind in ("select", "folder"):
        want = expected.get("path")
        if not want:
            return False, "expected path missing", True
        field = "selection" if kind == "select" else "folder"
        if field not in actual:
            return False, f"actual {field} not supplied", True
        if kind == "select":
            sel = [_resolve(p) for p in actual["selection"] or []]
            now = len(sel) == 1 and sel[0] == _resolve(want)
            was = [_resolve(p) for p in before.get("selection") or []]
            pre = len(was) == 1 and was[0] == _resolve(want)
        else:
            now = bool(actual["folder"]) and _resolve(actual["folder"]) == _resolve(want)
            pre = bool(before.get("folder")) and _resolve(before["folder"]) == _resolve(want)
        if not now:
            return False, f"wrong {field}", False
        if transition and pre:
            return False, f"{field} already satisfied before the turn", False
        return True, f"{field} matches", False
    return False, f"unknown expected kind {kind!r}", True


def assess_turn(*, state, replies, expected, actual, before, events, timed_out, cancelled=False):
    state = state or {}
    expected, actual, before = expected or {}, actual or {}, before or {}
    replies = [str(r) for r in (replies or [])]
    ok, reason, setup = _check(expected.get("kind"), replies, expected, actual, before, events or [])
    done = (state.get("task") or {}).get("status") == "done"
    false_success = bool(done and not ok)
    if cancelled:
        status, reason = "cancelled", "turn cancelled"
    elif timed_out:
        status, reason = "timeout", "turn timed out"
    elif state.get("pending_action"):
        status, reason = "blocked", "pending confirmation"
    elif (state.get('task') or {}).get('status') not in (None, 'done'):
        status, reason = 'blocked', 'application task has not finished'
    elif setup:
        status = "setup_error"
    else:
        status = "pass" if ok else "fail"
    return {
        "status": status, "reason": reason, "false_success": false_success,
        "evidence": {"expected": dict(expected), "actual": dict(actual), "before": dict(before),
                     "event_count": len(events or []), "reply_count": len(replies)},
    }


# -- aggregation -----------------------------------------------------------
def _pct(values, q):
    if not values:
        return None
    v = sorted(values)
    return round(v[max(math.ceil(q * len(v)) - 1, 0)], 3)


def _counts(rows):
    c = {s: 0 for s in STATUSES}
    c["invalid"] = 0
    for r in rows:
        c[r.get("status") if r.get("status") in STATUSES else "invalid"] += 1
    return c


def summarize(rows, planned_turns, complete):
    rows = list(rows or [])
    counts = _counts(rows)
    durations = lambda rs: [float(r['seconds']) for r in rs if r.get('status') in ('pass', 'fail', 'timeout')
                            and type(r.get('seconds')) in (int, float) and math.isfinite(r['seconds']) and r['seconds'] >= 0]
    secs = durations(rows)
    wf = {}
    for r in rows:
        wf.setdefault(str(r.get("workflow")), []).append(r)
    per = {}
    for name, rs in wf.items():
        c = _counts(rs)
        d = durations(rs)
        per[name] = {"attempted": len(rs), "counts": c, "all_passed": c["pass"] == len(rs),
                     "p50_seconds": _pct(d, 0.5), "p95_seconds": _pct(d, 0.95)}
    false_successes = sum(1 for r in rows if r.get("false_success"))
    recorded = len(rows)
    attempted = sum(r.get('status') in ('pass', 'fail', 'timeout') for r in rows)
    unique = len({(r.get('workflow'), r.get('repeat'), r.get('turn')) for r in rows}) == recorded
    return {
        "counts": counts, "planned": planned_turns, "recorded": recorded, "attempted": attempted,
        "not_attempted": max(planned_turns - attempted, 0), "complete": bool(complete),
        "false_successes": false_successes,
        "model_calls": sum(int(r.get("model_calls") or 0) for r in rows),
        "workflows": per,
        "latency": {"label": "exploratory, includes failures",
                    "p50_seconds": _pct(secs, 0.5), "p95_seconds": _pct(secs, 0.95)},
        "all_workflows_passed": bool(complete and rows and unique and planned_turns > 0 and recorded == planned_turns
                                     and counts["pass"] == planned_turns and false_successes == 0),
    }


_MANIFEST_KEYS = ("protocol", "seed", "case_ids", "runs", "model_config", "fixture_version")


def compare_runs(baseline, candidate):
    mb = (baseline or {}).get("manifest") or {}
    mc = (candidate or {}).get("manifest") or {}
    mismatched = [k for k in _MANIFEST_KEYS if k not in mb or k not in mc or mb[k] != mc[k]]
    if mismatched:
        return {"compatible": False, "mismatched": mismatched, "comparison": None,
                "claim": "none: manifests differ"}

    def summ(run):
        if all(key in run for key in ('counts', 'planned', 'complete', 'latency')):
            return run
        if isinstance(run.get("summary"), dict):
            return run["summary"]
        rows = run.get("rows") or []
        return summarize(rows, run.get("planned_turns", len(rows)), bool(run.get("complete", False)))

    sb, sc = summ(baseline), summ(candidate)
    pb, pc = sb["counts"]["pass"], sc["counts"]["pass"]
    return {
        "compatible": True, "mismatched": [],
        "comparison": {
            "baseline_pass": pb, "candidate_pass": pc, "pass_delta": pc - pb,
            "baseline_fail": sb["planned"] - pb, "candidate_fail": sc["planned"] - pc,
            "baseline_complete": sb["complete"], "candidate_complete": sc["complete"],
            "latency_p50_delta_seconds": _delta(sc["latency"]["p50_seconds"], sb["latency"]["p50_seconds"]),
            "latency_p95_delta_seconds": _delta(sc["latency"]["p95_seconds"], sb["latency"]["p95_seconds"]),
            "latency_label": "exploratory, includes failures",
        },
        "claim": "diagnostic only; no promotion or improvement claim",
    }


def _delta(a, b):
    return None if a is None or b is None else round(a - b, 3)
