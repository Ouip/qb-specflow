"""Tests for coverage.py. Run: python -m unittest discover -s plugins/specflow/tests"""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "coverage.py"
FIX = HERE / "fixtures" / "coverage"
CATALOG = FIX / "catalog.md"


def load():
    spec = importlib.util.spec_from_file_location("specflow_coverage", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cli(name, *catalogs):
    args = [sys.executable, str(SCRIPT), str(FIX / name)]
    if catalogs:
        args += ["--catalog", *map(str, catalogs)]
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout.strip().splitlines()


class Coverage(unittest.TestCase):
    def test_a_all_verified(self):
        rc, out = run_cli("ok.md", CATALOG)
        self.assertEqual(out, ["coverage: ok"])
        self.assertEqual(rc, 0)

    def test_b_blank_cell_reports_only_that_rule(self):
        rc, out = run_cli("blank.md", CATALOG)
        self.assertEqual(out, ["blank.md 4.1#3: Verify cell is empty"])
        self.assertEqual(rc, 1)

    def test_c_group_missing_in_section_5(self):
        rc, out = run_cli("missing-group.md", CATALOG)
        self.assertEqual(out, ["missing-group.md 4.1#1: 5.1 has no G9"])
        self.assertEqual(rc, 1)

    def test_d_scenario_not_in_catalog(self):
        rc, out = run_cli("missing-scenario.md", CATALOG)
        self.assertEqual(out, ["missing-scenario.md 4.1#3: scenario I99 not in catalog"])
        self.assertEqual(rc, 1)

    def test_d2_scenario_without_catalog(self):
        rc, out = run_cli("ok.md")
        self.assertIn("ok.md 4.1#3: scenario E01 not in catalog (no --catalog given)", out)
        self.assertEqual(rc, 1)

    def test_e_table_without_column(self):
        rc, out = run_cli("no-col.md", CATALOG)
        self.assertEqual(out, [f"no-col.md 4.1#{n}: table has no Verify column" for n in range(1, 6)])
        self.assertEqual(rc, 1)

    def test_f_not_tested_with_reason_passes(self):
        mod = load()
        _, problems = mod.scan((FIX / "ok.md").read_text(encoding="utf-8"), "ok.md", [])
        self.assertFalse([p for p in problems if "#5" in p or "4.2#2" in p])

    def test_f2_not_tested_without_reason(self):
        rc, out = run_cli("notest-noreason.md", CATALOG)
        self.assertEqual(out, ["notest-noreason.md 4.1#5: untested without a reason"])
        self.assertEqual(rc, 1)

    def test_g_header_and_separator_are_not_rules(self):
        mod = load()
        rules, _ = mod.scan((FIX / "ok.md").read_text(encoding="utf-8"), "ok.md", [])
        self.assertEqual(rules, ["4.1#1", "4.1#2", "4.1#3", "4.1#4", "4.1#5", "4.2#1", "4.2#2"])

    def test_list_item_without_marker(self):
        rc, out = run_cli("list-missing.md", CATALOG)
        self.assertEqual(out, ["list-missing.md 4.2#1: no [verify: ...] marker"])
        self.assertEqual(rc, 1)

    def test_english_spec_all_verified(self):
        rc, out = run_cli("en-ok.md", CATALOG)
        self.assertEqual(out, ["coverage: ok"])
        self.assertEqual(rc, 0)

    def test_english_spec_problems(self):
        rc, out = run_cli("en-bad.md", CATALOG)
        self.assertEqual(out, [
            "en-bad.md 4.1#2: Verify cell is empty",
            "en-bad.md 4.1#3: untested without a reason",
            "en-bad.md 4.2#1: no [verify: ...] marker",
        ])
        self.assertEqual(rc, 1)

    def test_pipe_inside_backticks_stays_in_cell(self):
        mod = load()
        self.assertEqual(mod.split_row("| 1 | 只有 `ON|OFF` | (#1) | 5.1 G1 |"),
                         ["1", "只有 `ON|OFF`", "(#1)", "5.1 G1"])


if __name__ == "__main__":
    unittest.main()
