import importlib.util
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "human_eval_core.py"
_spec = importlib.util.spec_from_file_location("human_eval_core", _PATH)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read().decode()


def turn(expected, actual, before, **kw):
    args = dict(state={}, replies=[], events=[], timed_out=False)
    args.update(kw)
    return core.assess_turn(expected=expected, actual=actual, before=before, **args)


class FixtureServerTests(unittest.TestCase):
    def test_pages_and_facts(self):
        with core.FixtureServer(1) as s:
            f = s.facts
            self.assertEqual(set(f), {"delivery", "pickup", "product", "price"})
            self.assertNotEqual(f["delivery"], f["pickup"])
            status, body = get(s.base_url + "/home")
            self.assertEqual(status, 200)
            self.assertIn("A11y fixture home", body)
            self.assertIn(f["delivery"], body)
            self.assertIn(f["pickup"], body)
            self.assertIn("Delivery details", body)
            self.assertIn("/delivery'", body)
            self.assertIn(">Catalog<", body)
            _, delivery = get(s.base_url + "/delivery")
            self.assertIn("/home", delivery)

    def test_catalog_search(self):
        with core.FixtureServer(2) as s:
            f = s.facts
            _, form = get(s.base_url + "/catalog")
            self.assertIn("name='q'", form)
            self.assertIn("method='get'", form)
            _, hit = get(s.base_url + "/catalog?q=" + quote(f["product"]))
            self.assertIn(f["product"], hit)
            self.assertIn(f"price {f['price']}", hit)
            noun = f["product"].split()[1]
            _, similar = get(s.base_url + "/catalog?q=" + noun)
            self.assertGreaterEqual(similar.count("<li>"), 2)

    def test_nonce_unique_and_seed_reproducible(self):
        with core.FixtureServer(3) as a, core.FixtureServer(3) as b:
            self.assertEqual(a.facts, b.facts)
            self.assertNotEqual(a.base_url.rsplit("/", 1)[1], b.base_url.rsplit("/", 1)[1])
            wrong = a.base_url.rsplit("/", 1)[0] + "/deadbeef/home"
            self.assertEqual(get(wrong)[0], 404)

    def test_event_log_offsets(self):
        with core.FixtureServer(4) as s:
            get(s.base_url + "/home")
            mark = s.event_count()
            self.assertEqual(mark, 1)
            get(s.base_url + "/catalog?q=abc")
            ev = s.events_since(mark)
            self.assertEqual(len(ev), 1)
            self.assertTrue(ev[0]["path"].endswith("/catalog"))
            self.assertEqual(ev[0]["query"], "q=abc")
            self.assertIsInstance(ev[0]["t"], float)
            self.assertEqual(s.events_since(s.event_count()), [])

    def test_server_stops_after_exit(self):
        with core.FixtureServer(5) as s:
            url = s.base_url + "/home"
        with self.assertRaises(Exception):
            urllib.request.urlopen(url, timeout=2)


class NavigateTests(unittest.TestCase):
    A = "http://127.0.0.1:9/abc/home"
    B = "http://127.0.0.1:9/abc/catalog?q=x"

    def exp(self, **kw):
        return dict({"kind": "navigate", "url": self.B}, **kw)

    def test_pass_with_transition(self):
        r = turn(self.exp(), {"url": self.B}, {"url": self.A})
        self.assertEqual(r["status"], "pass")
        self.assertFalse(r["false_success"])
        self.assertEqual(r["evidence"]["actual"], {"url": self.B})

    def test_lying_done_wrong_destination(self):
        r = turn(self.exp(), {"url": self.A}, {"url": self.A},
                 state={"task": {"status": "done"}}, replies=["Done, opened the catalog."])
        self.assertEqual(r["status"], "fail")
        self.assertTrue(r["false_success"])

    def test_exact_nonce_and_query(self):
        for bad in ("http://127.0.0.1:9/zzz/catalog?q=x", "http://127.0.0.1:9/abc/catalog?q=y",
                    "http://127.0.0.1:9/abc/catalog"):
            self.assertEqual(turn(self.exp(), {"url": bad}, {"url": self.A})["status"], "fail")

    def test_pre_satisfied_fails(self):
        r = turn(self.exp(), {"url": self.B}, {"url": self.B})
        self.assertEqual(r["status"], "fail")

    def test_require_http(self):
        e = self.exp(require_http=True)
        self.assertEqual(turn(e, {"url": self.B}, {"url": self.A}, events=[])["status"], "fail")
        ev = [{"t": 1.0, "path": "/abc/catalog", "query": "q=x"}]
        self.assertEqual(turn(e, {"url": self.B}, {"url": self.A}, events=ev)["status"], "pass")
        stale = [{"t": 1.0, "path": "/abc/home", "query": ""}]
        self.assertEqual(turn(e, {"url": self.B}, {"url": self.A}, events=stale)["status"], "fail")

    def test_timeout_stays_timeout(self):
        r = turn(self.exp(), {"url": self.B}, {"url": self.A}, timed_out=True)
        self.assertEqual(r["status"], "timeout")

    def test_cancel_and_pending(self):
        r = turn(self.exp(), {"url": self.B}, {"url": self.A}, cancelled=True)
        self.assertEqual(r["status"], "cancelled")
        r = turn(self.exp(), {"url": self.A}, {"url": self.A},
                 state={"pending_action": {"kind": "x"}, "task": {"status": "done"}})
        self.assertEqual(r["status"], "blocked")
        self.assertTrue(r["false_success"])
        r = turn(self.exp(), {"url": self.B}, {"url": self.A}, state={"pending_action": {"k": 1}},
                 timed_out=True)
        self.assertEqual(r["status"], "timeout")

    def test_missing_actual_is_setup_error(self):
        self.assertEqual(turn(self.exp(), {}, {"url": self.A})["status"], "setup_error")


class ReadTests(unittest.TestCase):
    def read(self, reply, fact="42"):
        replies = [reply] if reply is not None else []
        return turn({"kind": "read", "fact": fact}, {}, {}, replies=replies)["status"]

    def test_exact_fact_passes(self):
        self.assertEqual(self.read("The price is 42."), "pass")
        self.assertEqual(self.read("It ships by quill-123.", "quill-123"), "pass")

    def test_wrong_boundary_fails(self):
        self.assertEqual(self.read("The price is 420."), "fail")
        self.assertEqual(self.read("The price is 42.5."), "fail")
        self.assertEqual(self.read("Code quill-1234.", "quill-123"), "fail")
        self.assertEqual(self.read("Code xquill-123.", "quill-123"), "fail")

    def test_negated_uncertain_promise_empty_fail(self):
        for r in ("The price is not 42.", "I am not sure, maybe 42.", "I can't read it, 42?",
                  "Let me check whether it is 42.", "", None):
            self.assertEqual(self.read(r), "fail", r)

    def test_final_contradiction_overrides_an_earlier_correct_reply(self):
        result = turn({'kind': 'read', 'fact': 'quill-123'}, {}, {},
                      replies=['Delivery is quill-123.', 'Correction: delivery is not quill-123.'])
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(self.read('I can’t read it, but 42.'), 'fail')

    def test_catalog_product_identifier_cannot_satisfy_price_oracle(self):
        fixture = core.FixtureServer(258)
        self.assertEqual(self.read('I found '+fixture.facts['product']+'.', fixture.facts['price']), 'fail')
        for seed in range(1000):
            facts = core.FixtureServer(seed).facts
            self.assertNotEqual(facts['product'].split()[-1], facts['price'])


class FileTests(unittest.TestCase):
    def test_select_exact_vs_duplicate_parent(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a"), Path(d, "b")
            a.mkdir(), b.mkdir()
            want, dup = a / "report.txt", b / "report.txt"
            want.write_text("x"), dup.write_text("y")
            exp = {"kind": "select", "path": str(want)}
            self.assertEqual(turn(exp, {"selection": [str(want)]}, {"selection": []})["status"], "pass")
            self.assertEqual(turn(exp, {"selection": [str(dup)]}, {"selection": []})["status"], "fail")
            self.assertEqual(turn(exp, {"selection": [str(want), str(dup)]}, {"selection": []})["status"], "fail")
            self.assertEqual(turn(exp, {"selection": [str(want)]}, {"selection": [str(want)]})["status"], "fail")

    def test_folder(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a"), Path(d, "b")
            a.mkdir(), b.mkdir()
            exp = {"kind": "folder", "path": str(a)}
            self.assertEqual(turn(exp, {"folder": str(a) + "/."}, {"folder": str(b)})["status"], "pass")
            self.assertEqual(turn(exp, {"folder": str(b)}, {"folder": str(b)})["status"], "fail")
            self.assertEqual(turn(exp, {"folder": str(a)}, {"folder": str(a)})["status"], "fail")


def row(w, t, status="pass", secs=1.0, **kw):
    return dict({"workflow": w, "repeat": 0, "turn": t, "status": status, "seconds": secs}, **kw)


class SummaryTests(unittest.TestCase):
    def test_blocked_prerequisites_are_not_task_latency_samples(self):
        result = core.summarize([row('w', 0, 'blocked', 0)], 1, True)
        self.assertEqual(result['attempted'], 0)
        self.assertEqual(result['not_attempted'], 1)
        self.assertIsNone(result['latency']['p50_seconds'])

    def test_duplicate_passing_row_cannot_replace_missing_turn(self):
        self.assertFalse(core.summarize([row('w', 0), row('w', 0)], 2, True)['all_workflows_passed'])
    def test_all_pass_requires_everything(self):
        rows = [row("w", 0), row("w", 1)]
        self.assertTrue(core.summarize(rows, 2, True)["all_workflows_passed"])
        self.assertFalse(core.summarize(rows, 2, False)["all_workflows_passed"])
        self.assertFalse(core.summarize(rows, 3, True)["all_workflows_passed"])

    def test_one_failed_turn_fails_workflow(self):
        s = core.summarize([row("w", 0), row("w", 1, "fail", 9.0)], 2, True)
        self.assertFalse(s["all_workflows_passed"])
        self.assertFalse(s["workflows"]["w"]["all_passed"])
        self.assertEqual(s["counts"]["fail"], 1)
        self.assertEqual(s["latency"]["p95_seconds"], 9.0)

    def test_interrupted_prefix_cannot_pass(self):
        s = core.summarize([row("w", 0)], 4, False)
        self.assertFalse(s["all_workflows_passed"])
        self.assertEqual((s["attempted"], s["not_attempted"]), (1, 3))

    def test_empty_run(self):
        s = core.summarize([], 0, True)
        self.assertFalse(s["all_workflows_passed"])
        self.assertIsNone(s["latency"]["p50_seconds"])

    def test_false_success_blocks_pass(self):
        s = core.summarize([row("w", 0, false_success=True)], 1, True)
        self.assertEqual(s["false_successes"], 1)
        self.assertFalse(s["all_workflows_passed"])


class CompareTests(unittest.TestCase):
    M = {"protocol": core.PROTOCOL, "seed": 1, "case_ids": ["a"], "runs": 1,
         "model_config": {"m": "x"}, "fixture_version": "1"}

    def run_(self, status, **over):
        return {"manifest": dict(self.M, **over), "rows": [row("w", 0, status)],
                "planned_turns": 1, "complete": True}

    def test_incompatible(self):
        r = core.compare_runs(self.run_("pass"), self.run_("pass", seed=2))
        self.assertFalse(r["compatible"])
        self.assertIn("seed", r["mismatched"])
        self.assertFalse(core.compare_runs({}, {})["compatible"])

    def test_compatible_no_promotion(self):
        r = core.compare_runs(self.run_("fail"), self.run_("pass"))
        self.assertTrue(r["compatible"])
        self.assertEqual(r["comparison"]["pass_delta"], 1)
        self.assertIn("no promotion", r["claim"])


if __name__ == "__main__":
    unittest.main()
