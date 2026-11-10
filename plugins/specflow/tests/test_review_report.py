"""Tests for review_report.py. Run: python -m unittest discover -s plugins/specflow/tests"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "review_report.py"
SKILL = HERE.parent / "skills" / "deep-review" / "SKILL.md"

CODE = """#include <string.h>
#define BUF_LEN 16

int frame_copy(char *dst, const char *src, int len)
{
    memcpy(dst, src, len);
    return len;
}
"""


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


class ReviewReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.tmp.name) / "repo"
        (cls.repo / "fw" / "src").mkdir(parents=True)
        (cls.repo / "fw" / "src" / "frame.c").write_text(CODE, encoding="utf-8")
        git(cls.repo, "init", "-q")
        git(cls.repo, "add", ".")
        git(cls.repo, "commit", "-q", "-m", "reviewed version")
        cls.sha = git(cls.repo, "rev-parse", "HEAD")
        # a later edit in the working tree: the check must read the reviewed commit, not this
        (cls.repo / "fw" / "src" / "frame.c").write_text(CODE.replace("memcpy(dst, src, len);", "/* gone */"),
                                                       encoding="utf-8")
        cls.good = {
            "format": 1, "commit_sha": cls.sha, "context": "single_context",
            "coverage": {"reviewed": [{"item": "src/frame.c"}], "not_reviewed": [],
                         "insufficient_evidence": [{"item": "callers of frame_copy", "reason": "not in the diff"}]},
            "findings": [{
                "finding_id": "DR-1", "status": "CONFIRMED", "severity": "High", "track": "code_to_risk",
                "commit_sha": cls.sha, "file": "src/frame.c", "line_range": "4-8",
                "trigger": "len = 40 from the UART length byte", "expected_behavior": "len is capped at BUF_LEN",
                "actual_behavior": "memcpy writes 40 bytes into a 16-byte buffer", "impact": "stack corruption",
                "evidence": [{"file": "src/frame.c", "line_range": "6", "quote": "memcpy(dst, src, len);"}],
                "verification_method": ["read", "trace"], "verification_limitations": "not run on hardware",
                "counterevidence_checked": ["callers in src/: none check len"]}],
        }

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_check(self, report, *extra, root=None):
        path = Path(self.tmp.name) / "findings.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        p = subprocess.run([sys.executable, str(SCRIPT), str(path), "--root", str(root or self.repo / "fw"), *extra],
                           capture_output=True, text=True, encoding="utf-8")
        return p.returncode, p.stdout.strip().splitlines()

    def variant(self, **changes):
        r = copy.deepcopy(self.good)
        r["findings"][0].update(changes)
        return r

    def test_traceable_report_passes_and_says_what_it_does_not_prove(self):
        rc, out = self.run_check(self.good)
        self.assertEqual(rc, 0, out)
        self.assertIn("does not show that any finding is a real bug", out[-1])

    def test_missing_file_is_rejected(self):
        rc, out = self.run_check(self.variant(file="src/nope.c"))
        self.assertIn("findings.json finding DR-1: src/nope.c does not exist at the reviewed version", out)
        self.assertEqual(rc, 1)

    def test_bad_line_ranges_are_rejected(self):
        for rng in ("0-3", "7-99", "6-4", "abc"):
            rc, out = self.run_check(self.variant(line_range=rng))
            self.assertEqual(rc, 1, rng)
            self.assertTrue(any("line_range" in l for l in out), (rng, out))

    def test_inconsistent_or_unknown_commit_is_rejected(self):
        rc, out = self.run_check(self.variant(commit_sha="0" * 40))
        self.assertIn(f"findings.json finding DR-1: commit_sha {'0' * 40} differs from the report's {self.sha}", out)
        r = copy.deepcopy(self.good)
        r["commit_sha"] = r["findings"][0]["commit_sha"] = "1" * 40
        rc, out = self.run_check(r)
        self.assertIn(f"findings.json report: commit_sha {'1' * 40} is not a commit in this repository", out)
        self.assertEqual(rc, 1)

    def test_quote_is_read_at_the_reviewed_commit(self):
        rc, _ = self.run_check(self.good)  # the working tree no longer has the memcpy line
        self.assertEqual(rc, 0)
        rc, out = self.run_check(self.good, "--no-git")
        self.assertIn("findings.json finding DR-1 evidence 1: quote not found in src/frame.c:6", out)

    def test_invented_quote_is_rejected(self):
        ev = [{"file": "src/frame.c", "line_range": "6", "quote": "strcpy(dst, src);"}]
        rc, out = self.run_check(self.variant(evidence=ev))
        self.assertIn("findings.json finding DR-1 evidence 1: quote not found in src/frame.c:6", out)
        self.assertIn("findings.json finding DR-1: CONFIRMED needs at least one evidence quote found in the source", out)

    def test_test_method_without_an_execution_record_is_rejected(self):
        rc, out = self.run_check(self.variant(verification_method=["read", "test"]))
        self.assertIn("findings.json finding DR-1: verification_method test without an execution record", out)
        self.assertEqual(rc, 1)
        run = [{"command": "ctest -R frame", "exit_code": 8, "output_excerpt": "frame_copy_overflow ... Failed"}]
        rc, out = self.run_check(self.variant(verification_method=["read", "test"], executions=run))
        self.assertEqual(rc, 0, out)

    def test_reproduced_claim_without_execution_is_rejected(self):
        rc, out = self.run_check(self.variant(reproduced=True))
        self.assertIn("findings.json finding DR-1: reproduced is true without an execution record", out)
        self.assertEqual(rc, 1)

    def test_saying_not_reproduced_is_allowed(self):
        rc, out = self.run_check(self.variant(verification_limitations="not reproduced: no hardware or compiler"))
        self.assertEqual(rc, 0, out)

    def test_confirmed_without_counterevidence_is_rejected(self):
        rc, out = self.run_check(self.variant(counterevidence_checked=[]))
        self.assertIn("findings.json finding DR-1: counterevidence_checked missing or empty", out)
        self.assertIn("findings.json finding DR-1: CONFIRMED needs counterevidence_checked", out)
        self.assertEqual(rc, 1)

    def test_needs_verification_may_lack_counterevidence_result(self):
        rc, out = self.run_check(self.variant(status="NEEDS_VERIFICATION", counterevidence_checked=["not yet: caller unknown"]))
        self.assertEqual(rc, 0, out)

    def test_missing_fields_and_bad_enums(self):
        r = self.variant(status="Confirmed", severity="Critical", track="both")
        del r["findings"][0]["impact"]
        rc, out = self.run_check(r)
        for want in ("impact missing or empty", "status must be one of", "severity must be High, Medium or Low",
                     "track must be spec_to_code or code_to_risk"):
            self.assertTrue(any(want in l for l in out), want)

    def test_report_level_fields(self):
        r = copy.deepcopy(self.good)
        r["context"] = "two agents"
        r["coverage"]["not_reviewed"] = [{"item": "src/isr.c"}]
        rc, out = self.run_check(r)
        self.assertIn("findings.json report: context must be independent_subagents or single_context", out)
        self.assertIn("findings.json report: coverage.not_reviewed entries need item and reason", out)

    def test_path_outside_root_is_rejected(self):
        rc, out = self.run_check(self.variant(file="../fw/src/frame.c"))
        self.assertEqual(rc, 1)

    def test_not_a_git_repo_needs_no_git(self):
        plain = Path(self.tmp.name) / "plain"
        (plain / "src").mkdir(parents=True, exist_ok=True)
        (plain / "src" / "frame.c").write_text(CODE, encoding="utf-8")
        rc, out = self.run_check(self.good, root=plain)
        self.assertEqual(rc, 1)
        self.assertIn("not inside a git repository", out[0])
        rc, out = self.run_check(self.good, "--no-git", root=plain)
        self.assertEqual(rc, 0, out)
        self.assertIn("commit_sha not checked (--no-git)", out[0])


class SkillAndValidatorAgree(unittest.TestCase):
    def test_every_required_field_is_named_in_the_skill(self):
        spec = __import__("importlib.util").util.spec_from_file_location("rr", SCRIPT)
        mod = __import__("importlib.util").util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        skill = SKILL.read_text(encoding="utf-8")
        for name in mod.REQUIRED + sorted(mod.STATUS) + sorted(mod.TRACK) + sorted(mod.CONTEXT):
            self.assertIn(f"`{name}`", skill, name)
        for word in ("REVIEWED", "NOT_REVIEWED", "INSUFFICIENT_EVIDENCE", "review_report.py"):
            self.assertIn(word, skill)


if __name__ == "__main__":
    unittest.main()
