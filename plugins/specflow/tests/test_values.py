"""Tests for `couplings.py --values` and values.py. Run: python -m unittest discover -s plugins/specflow/tests
The strategy-cpp tests need a gcc-compatible preprocessor: set SPECFLOW_TEST_CPP to one, or have gcc, clang
or cc on PATH; otherwise they are skipped (and say so)."""
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "couplings.py"
FIX = HERE / "fixtures" / "couplings" / "values"
CPP = os.environ.get("SPECFLOW_TEST_CPP") or next(
    (p for p in (shutil.which(n) for n in ("gcc", "clang", "cc")) if p), None)


def load_values():
    spec = importlib.util.spec_from_file_location("specflow_values", HERE.parent / "scripts" / "values.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


V = load_values()


def run_cli(*extra, values=FIX / "values.json"):
    args = [sys.executable, str(SCRIPT), str(FIX / "table.md"), "--specs", str(FIX / "specs"),
            "--code", str(FIX / "code"), "--values", str(values)]
    p = subprocess.run(args + list(extra), capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout


def results(*extra, values=FIX / "values.json"):
    rc, out = run_cli("--json", *extra, values=values)
    data = json.loads(out)
    return rc, {(r["id"], r["variant"]): r for r in data["values"]}, data


class Values(unittest.TestCase):
    def test_same_unit_same_value_passes(self):
        _, res, _ = results()
        r = res[("1", "default")]
        self.assertEqual(r["status"], "PASS")
        self.assertEqual((r["a"]["source"], r["b"]["source"]),
                         ("host/link.c:2 (define)", "device/include/cfg.h:6 (define)"))

    def test_same_unit_different_value_fails(self):
        rc, res, _ = results()
        self.assertEqual(res[("2", "default")]["status"], "FAIL")
        self.assertEqual(res[("2", "default")]["reason"], "500 ms == 1000 ms is false")
        self.assertEqual(rc, 1)

    def test_seconds_and_milliseconds_compare_after_conversion(self):
        _, res, _ = results()
        r = res[("3", "default")]
        self.assertEqual(r["status"], "PASS")
        self.assertEqual((r["a"]["normalized"], r["b"]["normalized"]), ("1", "1"))
        self.assertEqual(r["b"]["raw"], "((uint32_t)1000U)")

    def test_missing_symbol_is_unverified(self):
        _, res, _ = results()
        self.assertEqual(res[("4", "default")]["status"], "UNVERIFIED")
        self.assertIn("no #define LINK_GONE_MS", res[("4", "default")]["reason"])

    def test_unknown_unit_is_unverified(self):
        _, res, _ = results()
        self.assertEqual(res[("5", "default")]["status"], "UNVERIFIED")
        self.assertIn("unknown unit ticks", res[("5", "default")]["reason"])

    def test_undeclared_variant_is_unverified(self):
        _, res, _ = results()
        self.assertEqual(res[("6", "lab")]["status"], "UNVERIFIED")

    def test_cpp_without_preprocessor_is_blocked_not_pass(self):
        _, res, _ = results()
        for v in ("dev", "prod", "undecided"):
            self.assertEqual(res[("6", v)]["status"], "BLOCKED")
        _, res, _ = results("--cpp", "no-such-preprocessor-specflow")
        self.assertEqual(res[("6", "dev")]["status"], "BLOCKED")
        self.assertIn("cannot run preprocessor", res[("6", "dev")]["reason"])

    def test_manual_coupling_is_listed_not_passed(self):
        _, res, _ = results()
        self.assertEqual(res[("7", None)]["status"], "MANUAL")

    def test_human_output_and_summary(self):
        rc, out = run_cli()
        lines = out.strip().splitlines()
        self.assertIn("value 2: FAIL 500 ms == 1000 ms is false | A LINK_ACK_MS = 500 -> 500 ms @ host/link.c:3 (define)"
                      " | B DEV_ACK_MS = 1000 -> 1000 ms @ device/include/cfg.h:7 (define)", lines)
        self.assertEqual(lines[-1], "values: 2 PASS, 1 FAIL, 3 UNVERIFIED, 3 BLOCKED, 1 MANUAL")
        self.assertNotIn("couplings: ok", out)
        self.assertEqual(rc, 1)

    def test_all_pass_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = json.loads((FIX / "values.json").read_text(encoding="utf-8"))
            data["couplings"] = [c for c in data["couplings"] if c["id"] in ("1", "3", "7")]
            path = Path(tmp) / "values.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            rc, out = run_cli(values=path)
        self.assertEqual(out.strip().splitlines()[-2:], ["values: 2 PASS, 0 FAIL, 0 UNVERIFIED, 0 BLOCKED, 1 MANUAL",
                                                         "couplings: ok"])
        self.assertEqual(rc, 0)

    def test_text_strategy_refuses_conditional_compilation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = json.loads((FIX / "values.json").read_text(encoding="utf-8"))
            data["couplings"][5]["b"]["strategy"] = "define"
            data["couplings"][5]["variants"] = ["dev"]
            path = Path(tmp) / "values.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            _, res, _ = results(values=path)
        self.assertEqual(res[("6", "dev")]["status"], "UNVERIFIED")
        self.assertIn("defined 2 times", res[("6", "dev")]["reason"])

    def test_malformed_value_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {"format": 1, "couplings": [
                {"id": "9", "a": {"file": "x.c", "symbol": "X", "unit": "ms"}, "b": {"value": 1, "unit": "ms", "source": "s"}},
                {"id": "1", "relation": "~=", "a": {"file": "x.c"}, "b": {"file": "y.c", "symbol": "Y", "strategy": "regex"}},
                {"id": "7", "check": "manual"}]}
            path = Path(tmp) / "values.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            rc, out = run_cli(values=path)
        self.assertEqual(out.strip().splitlines(), [
            "values.json coupling 9: no row 9 in the couplings table",
            "values.json coupling 1: relation must be one of ==, equal, <, <=, >, >=",
            "values.json coupling 1: side a needs file and symbol, or value and source",
            "values.json coupling 1: side b strategy must be define or cpp",
            "values.json coupling 7: a manual check needs how",
        ])
        self.assertEqual(rc, 1)

    def test_without_values_flag_old_behaviour_is_unchanged(self):
        args = [sys.executable, str(SCRIPT), str(FIX / "table.md"), "--specs", str(FIX / "specs"), "--code", str(FIX / "code")]
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.stdout.strip().splitlines(), ["couplings: ok"])
        self.assertEqual(p.returncode, 0)


@unittest.skipUnless(CPP, "no gcc-compatible preprocessor (set SPECFLOW_TEST_CPP)")
class ValuesWithPreprocessor(unittest.TestCase):
    def test_variants_are_checked_separately(self):
        _, res, _ = results("--cpp", CPP)
        self.assertEqual(res[("6", "dev")]["status"], "PASS")
        self.assertEqual(res[("6", "dev")]["b"]["source"], "device/include/board.h (cpp -DBOARD_DEV=1)")
        self.assertEqual(res[("6", "prod")]["status"], "FAIL")
        self.assertEqual(res[("6", "prod")]["reason"], "320 px == 480 px is false")

    def test_undecided_variant_hits_error_and_is_not_pass(self):
        _, res, _ = results("--cpp", CPP)
        self.assertEqual(res[("6", "undecided")]["status"], "UNVERIFIED")
        self.assertIn("frame width for this board not decided", res[("6", "undecided")]["reason"])


class Evaluator(unittest.TestCase):
    def value(self, text):
        return V.fmt(V.evaluate(text).v)

    def test_supported_forms(self):
        cases = {"500": "500", "(500U)": "500", "((uint32_t)500)": "500", "2 * 250": "500", "(1u << 10)": "1024",
                 "0x1F4": "500", "010": "8", "1.5f": "1.5", "10 / 3": "3", "-7 / 2": "-3", "-7 % 2": "-1",
                 "(float)1 / 2": "0.5", "1e3": "1000", "3000000000ULL": "3000000000"}
        for text, want in cases.items():
            self.assertEqual(self.value(text), want, text)

    def test_refused_forms(self):
        for text in ("sizeof(int)", "OTHER_MACRO", "0u - 1", "40000 * 60000", "(uint8_t)300", "1 / 0", "500ms",
                     "(int)50000", "1.5 % 2", "open('x')", "a.b", "", "1 ? 2 : 3"):
            with self.assertRaises(V.Unsupported, msg=text):
                V.evaluate(text)

    def test_no_eval_or_exec_calls(self):
        for name in ("values.py", "couplings.py"):
            tree = ast.parse((HERE.parent / "scripts" / name).read_text(encoding="utf-8"))
            calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            self.assertFalse(calls & {"eval", "exec", "compile"}, name)

    def test_define_reader(self):
        guard = "#ifndef X_H\n#define X_H\n#define A 5 /* c */\n#define B \\\n  (A + 1)\n#endif\n"
        self.assertEqual(V.define_value(guard, "A"), ("5", 3))
        self.assertEqual(V.define_value(guard, "B"), ("(A + 1)", 4))
        with self.assertRaises(V.Unsupported):
            V.evaluate(V.define_value(guard, "B")[0])  # another macro: text strategy cannot expand it
        with self.assertRaises(V.Unsupported):
            V.define_value("#ifdef DEV\n#define A 1\n#endif\n", "A")
        with self.assertRaises(V.Unsupported):
            V.define_value("#define A(x) x\n", "A")
        with self.assertRaises(V.Unsupported):
            V.define_value("#define A 1\n#undef A\n", "A")
        with self.assertRaises(LookupError):
            V.define_value("#define AB 1\n", "A")


if __name__ == "__main__":
    unittest.main()
