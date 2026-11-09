"""Tests for bench.py. Run: python -m unittest discover -s plugins/specflow/tests
The repro test needs gcc (or SPECFLOW_TEST_CC) and is skipped without it."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "bench.py"
CASES = HERE.parent / "bench" / "cases"
CC = os.environ.get("SPECFLOW_TEST_CC") or shutil.which("gcc")

META = {"run_id": "t-1", "run_index": 1, "model": "m", "effort": "high", "prompt_version": "bench-prompt-1",
        "commit_sha": "0" * 40, "source": "working_tree", "tools": ["Read"]}


def finding(fid, status="CONFIRMED", file="src/heater.c", rng="7-9", quote="if (t < 30 || t >= 90) {"):
    return {"finding_id": fid, "status": status, "severity": "High", "track": "spec_to_code",
            "commit_sha": "0" * 40, "file": file, "line_range": rng, "trigger": "t", "expected_behavior": "e",
            "actual_behavior": "a", "impact": "i",
            "evidence": [{"file": file, "line_range": rng, "quote": quote}],
            "verification_method": ["read"], "verification_limitations": "none",
            "counterevidence_checked": ["callers: none"]}


def report(findings, reviewed):
    return {"format": 1, "commit_sha": "0" * 40, "context": "single_context", "findings": findings,
            "coverage": {"reviewed": [{"item": r} for r in reviewed], "not_reviewed": [], "insufficient_evidence": []}}


def run_cli(*args):
    p = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout


class Bench(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_run(self, name, findings_by_case, matches, meta=None):
        d = self.root / name
        d.mkdir()
        (d / "meta.json").write_text(json.dumps(dict(META, run_id=name, **(meta or {}))), encoding="utf-8")
        (d / "judgements.json").write_text(json.dumps({"judged_by": "tester", "matches": matches}), encoding="utf-8")
        for cid, rep in findings_by_case.items():
            (d / f"{cid}.findings.json").write_text(json.dumps(rep), encoding="utf-8")
        return d

    def standard_run(self, name="r1", **kw):
        boundary = report([finding("DR-1"), finding("DR-2", rng="14-17", quote="return target;")],
                          ["src/heater.c", "src/heater.h", "spec/H01-heater.md"])
        clean = report([finding("DR-1", file="src/frame.c", rng="9", quote="memcpy(store, p, len);")],
                       ["src/frame.c", "src/uart.c"])
        matches = {"bug-boundary-range": {
            "DR-1": {"bug": "bug-boundary-range/1", "trigger": True, "root_cause": True, "impact": True},
            "DR-2": {"bug": None, "trigger": False, "root_cause": False, "impact": False}},
            "clean-guarded-length": {"DR-1": {"bug": None, "trigger": False, "root_cause": False, "impact": False}}}
        matches.update(kw.pop("matches", {}))
        return self.make_run(name, {"bug-boundary-range": boundary, "clean-guarded-length": clean}, matches, **kw)

    def score(self, *runs):
        rc, out = run_cli("score", CASES, *runs, "--json")
        return rc, (json.loads(out) if rc == 0 else out)

    def test_cases_lists_size(self):
        rc, out = run_cli("cases", CASES)
        self.assertIn("size: 7 cases (4 bug, 2 clean, 1 complex), 6 known bugs", out)
        self.assertEqual(rc, 0)

    def test_malformed_case_is_rejected(self):
        bad = self.root / "cases" / "x"
        bad.mkdir(parents=True)
        (bad / "truth.json").write_text(json.dumps({"format": 1, "case_id": "x", "kind": "clean",
                                                    "bugs": [{"id": "x/1"}], "units": []}), encoding="utf-8")
        rc, out = run_cli("cases", self.root / "cases")
        self.assertIn("x: a clean case has no bugs; a bug or complex case has at least one", out)
        self.assertIn("x: provenance.method must be reproduced, exercised or human_reviewed", out)
        self.assertEqual(rc, 1)

    def test_precision_recall_and_false_positive_rate(self):
        rc, data = self.score(self.standard_run())
        self.assertEqual(rc, 0, data)
        r = data["runs"][0]
        self.assertEqual((r["tp"], r["fp"], r["bugs_found"], r["bugs_total"]), (1, 2, 1, 1))
        mt = r["metrics"]
        self.assertEqual((mt["precision"]["num"], mt["precision"]["den"]), (1, 3))
        self.assertEqual(mt["recall"]["value"], 1.0)
        self.assertEqual((mt["false_positive_rate"]["num"], mt["false_positive_rate"]["den"]), (1, 1))
        self.assertEqual(mt["evidence_validity"]["value"], 1.0)
        self.assertEqual(r["cases_run"], ["bug-boundary-range", "clean-guarded-length"])

    def test_location_without_root_cause_is_not_a_true_positive(self):
        run = self.standard_run(matches={"bug-boundary-range": {
            "DR-1": {"bug": "bug-boundary-range/1", "trigger": True, "root_cause": False, "impact": True},
            "DR-2": {"bug": None}}})
        _, data = self.score(run)
        r = data["runs"][0]
        self.assertEqual((r["tp"], r["partial"]), (0, 1))
        self.assertEqual(r["metrics"]["recall"]["value"], 0.0)

    def test_unjudged_findings_block_the_scores(self):
        run = self.standard_run(matches={"clean-guarded-length": {}})
        _, data = self.score(run)
        mt = data["runs"][0]["metrics"]
        for k in ("precision", "recall", "false_positive_rate"):
            self.assertIsNone(mt[k]["value"])
            self.assertIn("no judgement", mt[k]["why"])

    def test_new_defect_counts_as_neither(self):
        run = self.standard_run(matches={"clean-guarded-length": {"DR-1": {"bug": "NEW"}}})
        _, data = self.score(run)
        r = data["runs"][0]
        self.assertEqual((r["new"], r["clean_with_fp"]), (1, 0))

    def test_evidence_validity_counts_untraceable_findings(self):
        run = self.make_run("r1", {"bug-boundary-range": report(
            [finding("DR-1"), finding("DR-2", status="NEEDS_VERIFICATION", quote="t > 95")], ["src/heater.c"])},
            {"bug-boundary-range": {"DR-1": {"bug": "bug-boundary-range/1", "trigger": True, "root_cause": True,
                                             "impact": True}}})
        _, data = self.score(run)
        ev = data["runs"][0]["metrics"]["evidence_validity"]
        self.assertEqual((ev["num"], ev["den"]), (1, 2))
        self.assertAlmostEqual(data["runs"][0]["metrics"]["coverage"]["value"], round(1 / 3, 3))

    def test_stability_needs_two_runs_of_one_config(self):
        _, data = self.score(self.standard_run("r1"))
        self.assertIsNone(data["stability"]["value"])
        r2 = self.standard_run("r2", meta={"run_index": 2})
        _, data = self.score(self.root / "r1", r2)
        self.assertEqual(data["stability"]["value"], 1.0)
        r3 = self.standard_run("r3", meta={"run_index": 3}, matches={"bug-boundary-range": {
            "DR-1": {"bug": None}, "DR-2": {"bug": None}}})
        _, data = self.score(self.root / "r1", r2, r3)
        self.assertEqual(data["stability"]["unstable_cases"], ["bug-boundary-range"])
        r4 = self.standard_run("r4", meta={"model": "other"})
        rc, out = self.score(self.root / "r1", r4)
        self.assertIn("runs differ in model, effort or prompt_version", out)
        self.assertEqual(rc, 1)

    def test_run_without_required_meta_is_rejected(self):
        run = self.standard_run()
        meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
        del meta["model"], meta["prompt_version"]
        (run / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        rc, out = run_cli("score", CASES, run)
        self.assertIn("r1: meta.json lacks model", out)
        self.assertIn("r1: meta.json lacks prompt_version", out)
        self.assertEqual(rc, 1)

    def test_repro_without_compiler_is_not_reproduced(self):
        rc, out = run_cli("repro", CASES, "--cc", "no-such-compiler-specflow")
        self.assertIn("compiler no-such-compiler-specflow not found", out)
        self.assertEqual(rc, 1)

    @unittest.skipUnless(CC, "no gcc (set SPECFLOW_TEST_CC)")
    def test_repro_with_compiler(self):
        rc, out = run_cli("repro", CASES, "--cc", CC, "--case", "bug-boundary-range", "clean-goto-cleanup")
        self.assertIn("bug-boundary-range: REPRODUCED", out)
        self.assertIn("clean-goto-cleanup: EXERCISED", out)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
