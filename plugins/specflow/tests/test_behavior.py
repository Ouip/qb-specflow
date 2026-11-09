"""Tests for behavior.py. Run: python -m unittest discover -s plugins/specflow/tests"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "behavior.py"
FIX = HERE / "fixtures" / "behavior"
PY = Path(sys.executable).stem  # the program name the maps use, found first on PATH below


class Behavior(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "fx"
        shutil.copytree(FIX, self.dir)
        self.map = json.loads((self.dir / "map.json").read_text(encoding="utf-8"))
        for e in self.map["rules"].values():
            if "command" in e:
                e["command"][0] = PY
        self.save()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        (self.dir / "map.json").write_text(json.dumps(self.map), encoding="utf-8")

    def run_cli(self, *extra, run=False):
        args = [sys.executable, str(SCRIPT), str(self.dir / "H01-heater.md"), "--map", str(self.dir / "map.json")]
        if run:
            args += ["--run", "--workdir", str(self.dir / "proj"), "--allow", PY]
        env = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
        p = subprocess.run(args + list(extra), capture_output=True, text=True, encoding="utf-8", env=env)
        return p.returncode, p.stdout

    def statuses(self, *extra, run=False):
        rc, out = self.run_cli("--json", *extra, run=run)
        data = json.loads(out)
        return rc, {r["rule"]: r for r in data["rules"]}, data

    def test_without_run_a_reference_is_only_reference_only(self):
        rc, rules, data = self.statuses()
        self.assertEqual({k: v["status"] for k, v in rules.items()}, {
            "4.1#1": "REFERENCE_ONLY", "4.1#2": "REFERENCE_ONLY", "4.1#3": "UNVERIFIED",
            "4.1#4": "MANUAL_VERIFIED", "4.1#5": "REFERENCE_ONLY", "4.1#6": "WAIVED"})
        self.assertIn("untested: board revision not decided", rules["4.1#3"]["reason"])
        self.assertEqual(rc, 0)

    def test_human_readable_output_keeps_manual_apart(self):
        rc, out = self.run_cli()
        self.assertIn("H01-heater.md 4.1#4: MANUAL_VERIFIED", out)
        self.assertIn("automatic: MUTATION_VERIFIED 0, TEST_PASSED 0, TEST_FAILED 0", out)
        self.assertIn("manual, not automatic: MANUAL_VERIFIED 1, WAIVED 1", out)
        self.assertIn("not verified: REFERENCE_ONLY 3, UNVERIFIED 1, BLOCKED 0", out)

    def test_strict_rejects_reference_only_for_required_rules(self):
        rc, out = self.run_cli("--strict")
        self.assertIn("strict: 4.1#1 requires mutation, got REFERENCE_ONLY", out)
        self.assertIn("strict: 4.1#2 requires mutation, got REFERENCE_ONLY", out)
        self.assertEqual(rc, 1)

    def test_passing_test_is_at_most_test_passed(self):
        rc, rules, _ = self.statuses(run=True)
        self.assertEqual(rules["4.1#1"]["status"], "TEST_PASSED")
        self.assertEqual(rules["4.1#2"]["status"], "TEST_PASSED")
        self.assertIn("mutation not run", rules["4.1#1"]["reason"])
        self.assertEqual(rc, 0)

    def test_mutation_killed_by_expected_assertion(self):
        heater = self.dir / "proj" / "heater.py"
        before = hashlib.sha256(heater.read_bytes()).hexdigest()
        rc, rules, _ = self.statuses("--mutate", run=True)
        self.assertEqual(rules["4.1#1"]["status"], "MUTATION_VERIFIED")
        self.assertEqual(rules["4.1#1"]["mutation"]["result"], "killed")
        self.assertEqual(hashlib.sha256(heater.read_bytes()).hexdigest(), before)  # working tree untouched

    def test_reference_without_behavior_cannot_be_mutation_verified(self):
        rc, rules, data = self.statuses("--mutate", "--strict", run=True)
        self.assertEqual(rules["4.1#2"]["status"], "TEST_PASSED")
        self.assertEqual(rules["4.1#2"]["mutation"]["result"], "survived")
        self.assertEqual(data["strict_failures"], ["strict: 4.1#2 requires mutation, got TEST_PASSED"])
        self.assertEqual(rc, 1)

    def test_mutant_build_error_is_not_a_kill(self):
        self.map["rules"]["4.1#1"]["mutation"]["replace"] = "t >>> 90"  # syntax error
        self.save()
        _, rules, _ = self.statuses("--mutate", run=True)
        self.assertEqual(rules["4.1#1"]["status"], "TEST_PASSED")
        self.assertEqual(rules["4.1#1"]["mutation"]["result"], "inconclusive")
        self.assertIn("without the expected assertion", rules["4.1#1"]["mutation"]["detail"])

    def test_mutant_timeout_is_not_a_kill(self):
        m = self.map["rules"]["4.1#1"]
        m["mutation"].update(find='return "ERR_RANGE"', replace="while True: pass")
        m["timeout"] = 5
        self.save()
        _, rules, _ = self.statuses("--mutate", run=True)
        self.assertEqual(rules["4.1#1"]["status"], "TEST_PASSED")
        self.assertEqual(rules["4.1#1"]["mutation"]["result"], "inconclusive")
        self.assertIn("timed out", rules["4.1#1"]["mutation"]["detail"])

    def test_assertion_pattern_that_matches_the_passing_run_is_inconclusive(self):
        self.map["rules"]["4.1#1"]["mutation"]["assertion"] = "test_rejects_95"
        self.save()
        _, rules, _ = self.statuses("--mutate", run=True)
        self.assertEqual(rules["4.1#1"]["status"], "TEST_PASSED")
        self.assertIn("also matches the passing run", rules["4.1#1"]["mutation"]["detail"])

    def test_failing_test_is_test_failed_and_exit_1(self):
        self.map["rules"]["4.1#1"]["command"] = [PY, "-c", "import sys; sys.exit(3)"]
        self.save()
        rc, rules, _ = self.statuses(run=True)
        self.assertEqual(rules["4.1#1"]["status"], "TEST_FAILED")
        self.assertEqual(rules["4.1#1"]["exit_code"], 3)
        self.assertEqual(rc, 1)

    def test_exit_0_without_expected_output_is_unverified(self):
        self.map["rules"]["4.1#1"]["command"] = [PY, "-c", "pass"]
        self.save()
        _, rules, _ = self.statuses(run=True)
        self.assertEqual(rules["4.1#1"]["status"], "UNVERIFIED")

    def test_program_not_allowed_or_missing_is_blocked(self):
        self.map["rules"]["4.1#2"]["command"] = ["no-such-program-specflow", "x"]
        self.save()
        args = ["--json", "--workdir", str(self.dir / "proj"), "--allow", "no-such-program-specflow"]
        rc, out = self.run_cli("--run", *args)
        rules = {r["rule"]: r for r in json.loads(out)["rules"]}
        self.assertEqual(rules["4.1#1"]["status"], "BLOCKED")
        self.assertIn("not in --allow", rules["4.1#1"]["reason"])
        self.assertEqual(rules["4.1#2"]["status"], "BLOCKED")
        self.assertIn("not found on PATH", rules["4.1#2"]["reason"])
        self.assertEqual(rc, 0)  # without --strict, BLOCKED is reported, not failed
        rc, _ = self.run_cli("--run", "--strict", *args)
        self.assertEqual(rc, 1)

    def test_path_programs_are_refused(self):
        self.map["rules"]["4.1#1"]["command"] = ["./run.sh"]
        self.save()
        _, rules, _ = self.statuses(run=True)
        self.assertEqual(rules["4.1#1"]["status"], "BLOCKED")
        self.assertIn("bare name", rules["4.1#1"]["reason"])

    def test_incomplete_manual_or_waiver_is_unverified(self):
        self.map["rules"]["4.1#4"]["manual"]["evidence"] = "no-such-record.md"
        self.map["rules"]["4.1#6"]["waiver"]["approved_by"] = " "
        self.save()
        _, rules, _ = self.statuses()
        self.assertEqual(rules["4.1#4"]["status"], "UNVERIFIED")
        self.assertIn("not found", rules["4.1#4"]["reason"])
        self.assertEqual(rules["4.1#6"]["status"], "UNVERIFIED")
        self.assertIn("approved_by", rules["4.1#6"]["reason"])

    def test_manual_and_waiver_satisfy_strict_but_are_not_automatic(self):
        self.map["rules"]["4.1#4"]["require"] = "mutation"
        del self.map["rules"]["4.1#1"]["require"], self.map["rules"]["4.1#2"]["require"]
        self.save()
        rc, _, data = self.statuses("--strict")
        self.assertEqual(data["strict_failures"], [])
        self.assertEqual(data["summary"]["MANUAL_VERIFIED"], 1)
        self.assertEqual(data["summary"]["MUTATION_VERIFIED"], 0)
        self.assertEqual(rc, 0)

    def test_broken_reference_is_unverified(self):
        spec = self.dir / "H01-heater.md"
        spec.write_text(spec.read_text(encoding="utf-8").replace("| 5.1 G3 |", "| 5.1 G9 |"), encoding="utf-8")
        _, rules, _ = self.statuses()
        self.assertEqual(rules["4.1#5"]["status"], "UNVERIFIED")
        self.assertIn("5.1 has no G9", rules["4.1#5"]["reason"])

    def test_broken_reference_still_shown_after_a_run(self):
        spec = self.dir / "H01-heater.md"
        spec.write_text(spec.read_text(encoding="utf-8").replace("| (#1) | 5.1 G1 |", "| (#1) | 5.1 G9 |"),
                        encoding="utf-8")
        _, rules, _ = self.statuses(run=True)
        self.assertEqual(rules["4.1#1"]["status"], "TEST_PASSED")
        self.assertIn("reference: 5.1 has no G9", rules["4.1#1"]["reason"])

    def test_program_names_keep_version_dots(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("specflow_behavior", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.program_name("python3.12"), "python3.12")
        self.assertEqual(mod.program_name("Python.EXE"), "python")
        self.assertEqual(mod.program_name("ctest"), "ctest")

    def test_malformed_map(self):
        self.map["rules"]["4.9#1"] = {"command": [PY]}
        self.map["rules"]["4.1#3"] = {"command": "make test", "waiver": {"reason": "x", "approved_by": "y"}}
        self.map["rules"]["4.1#5"] = {"command": "make test"}
        self.save()
        rc, out = self.run_cli()
        self.assertEqual(out.strip().splitlines(), [
            "map.json 4.9#1: no such rule in the spec",
            "map.json 4.1#3: needs exactly one of command, manual, waiver",
            "map.json 4.1#5: command must be a non-empty list of strings",
        ])
        self.assertEqual(rc, 1)

    def test_run_needs_workdir_and_allow(self):
        rc, out = self.run_cli("--run")
        self.assertIn("need --workdir", out)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
