"""Tests for couplings.py. Run: python -m unittest discover -s plugins/specflow/tests"""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "couplings.py"
FIX = HERE / "fixtures" / "couplings"


def run_cli(table, code=True):
    args = [sys.executable, str(SCRIPT), str(FIX / table), "--specs", str(FIX / "specs")]
    if code:
        args += ["--code", str(FIX / "code")]
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout.strip().splitlines()


class Couplings(unittest.TestCase):
    def test_a_all_three_kinds_found(self):
        rc, out = run_cli("ok.md")
        self.assertEqual(out, ["couplings: ok"])
        self.assertEqual(rc, 0)

    def test_b_missing_section(self):
        rc, out = run_cli("bad-section.md")
        self.assertEqual(out, ["row 1: H01 4.9 not found"])
        self.assertEqual(rc, 1)

    def test_c_symbol_not_found(self):
        rc, out = run_cli("bad-symbol.md")
        self.assertEqual(out, ["row 1: NO_SUCH_SYM not found"])
        self.assertEqual(rc, 1)

    def test_d_path_not_found(self):
        rc, out = run_cli("bad-path.md")
        self.assertEqual(out, ["row 1: src/heater/nope.c not found"])
        self.assertEqual(rc, 1)

    def test_e_without_code_paths_are_skipped(self):
        rc, out = run_cli("ok.md", code=False)
        self.assertEqual(out, [
            "row 1: src/heater/heater.c skipped (no --code)",
            "row 1: heater.c:2 skipped (no --code)",
            "row 1: tools/gen_table.py skipped (no --code)",
            "couplings: ok",
        ])
        self.assertEqual(rc, 0)

    def test_f_table_inside_specs_does_not_match_itself(self):
        rc, out = run_cli("specs/連動表.md")
        self.assertEqual(out, ["row 1: ONLY_IN_TABLE_SYM not found"])
        self.assertEqual(rc, 1)

    def test_english_columns(self):
        rc, out = run_cli("en-ok.md")
        self.assertEqual(out, ["couplings: ok"])
        self.assertEqual(rc, 0)

    def test_no_coupling_table(self):
        rc, out = run_cli("specs/H01-heater.md")
        self.assertEqual(out, ["H01-heater.md: no table with #, Side A and Side B columns"])
        self.assertEqual(rc, 1)

    def test_list_prints_every_reference_by_side(self):
        run = lambda table: subprocess.run(
            [sys.executable, str(SCRIPT), str(FIX / table), "--specs", str(FIX / "specs"), "--list"],
            capture_output=True, text=True, encoding="utf-8")
        p = run("ok.md")
        self.assertEqual(p.stdout.strip().splitlines(), [
            "row 1 A: H01 3.1", "row 1 A: H01 4.2", "row 1 A: HEATER_PWM_HZ", "row 1 A: HEATER_*",
            "row 1 B: D02 3.1", "row 1 B: src/heater/heater.c", "row 1 B: heater.c:2", "row 1 B: tools/gen_table.py",
        ])
        self.assertEqual(p.returncode, 0)
        self.assertEqual(run("bad-path.md").returncode, 0)  # --list only lists; a missing path is not an error

    def test_refs_extracted_from_cell(self):
        spec = importlib.util.spec_from_file_location("specflow_couplings", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.refs("加熱 H01 3.1／4.2、`HEATER_PWM_HZ`、`a/b.c`"), [
            ("section", "H01", "3.1"), ("section", "H01", "4.2"),
            ("symbol", "HEATER_PWM_HZ", None), ("path", "a/b.c", None),
        ])


if __name__ == "__main__":
    unittest.main()
