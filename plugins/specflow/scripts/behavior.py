#!/usr/bin/env python3
"""Rule -> behavior check: is each rule of spec section 4 verified by behavior, not just referenced.

Usage: python behavior.py <spec.md> --map <verify-map.json> [--catalog <scenario catalog.md> ...]
                          [--run --workdir <dir> --allow <program> ...] [--mutate] [--timeout <s>] [--strict] [--json]

coverage.py only shows that a Verify reference resolves. This script gives each rule one status:
  MUTATION_VERIFIED  the rule's command passed, then failed with the expected assertion when the rule was
                     broken on purpose in an isolated copy (--mutate)
  TEST_PASSED        the rule's command ran and passed; nothing shows it would fail if the rule were broken
  TEST_FAILED        the rule's command ran and failed
  MANUAL_VERIFIED    a person ran a procedure; evidence file, reviewer and date recorded. Not an automatic check
  WAIVED             a person waived verification with a reason. Not a verification
  REFERENCE_ONLY     the Verify reference resolves but no command ran for the rule
  UNVERIFIED         `untested:`, a broken reference, incomplete manual evidence, or a run that cannot be trusted
  BLOCKED            the command could not run: not allowed, not on PATH, timed out, or the isolated copy failed

Verify map (JSON; Rule IDs as coverage.py prints them, e.g. `4.1#1`). Each entry has exactly one of
command, manual or waiver:
  {"format": 1, "rules": {
    "4.1#1": {"test": "<test ID>", "command": ["<program>", "<arg>", ...],
              "expect_output": "<regex the passing output must contain; optional>",
              "mutation": {"file": "<path under --workdir>", "find": "<exact text, once>", "replace": "<text>",
                           "assertion": "<regex only the expected failing assertion prints>"},
              "require": "test" | "mutation", "timeout": <seconds>},
    "4.1#4": {"manual": {"procedure": "...", "evidence": "<path relative to the map>", "reviewer": "...", "date": "..."}},
    "4.1#5": {"waiver": {"reason": "...", "approved_by": "..."}}}}

Running commands:
- Nothing runs without --run. --run needs --workdir (commands run there) and --allow (program names that may
  run, e.g. `--allow python ctest`). A command is an argument list, never a shell string; its program must be
  a bare name found on PATH. Each run stops after min(rule timeout, --timeout) seconds (default 120).
- --mutate (implies --run) copies --workdir to a temporary folder (without .git), runs each command there, then
  applies the rule's mutation and runs it again. The working tree is never written. A mutation counts as killed
  only when the mutant run fails AND its output matches `assertion` AND the passing run's output does not;
  a build error, a timeout or any other failure is "inconclusive", never killed.

Exit codes: 0 = no failure; 1 = malformed map, a test that ran and failed, or (with --strict) a rule with
`require` that falls short; 2 = usage error. Without --strict, REFERENCE_ONLY, UNVERIFIED and BLOCKED are
reported but do not fail. With --strict, `require: test` needs TEST_PASSED or better and `require: mutation`
needs MUTATION_VERIFIED; MANUAL_VERIFIED and WAIVED satisfy either but are counted apart from automatic checks.
"""
import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("specflow_coverage", Path(__file__).with_name("coverage.py"))
_cov = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cov)

AUTOMATIC = ["MUTATION_VERIFIED", "TEST_PASSED", "TEST_FAILED"]
HUMAN = ["MANUAL_VERIFIED", "WAIVED"]
NOT_VERIFIED = ["REFERENCE_ONLY", "UNVERIFIED", "BLOCKED"]
RANK = {"TEST_PASSED": 1, "MUTATION_VERIFIED": 2}
NEED = {"test": 1, "mutation": 2}
COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "node_modules", ".venv")
EXE_SUFFIX = (".exe", ".cmd", ".bat", ".com")
TAIL = 20


def check_map(data, rule_ids, name):
    """Return problem strings for a malformed map."""
    if not isinstance(data, dict) or data.get("format") != 1 or not isinstance(data.get("rules"), dict):
        return [f"{name}: needs {{\"format\": 1, \"rules\": {{...}}}}"]
    out = []
    for rid, e in data["rules"].items():
        where = f"{name} {rid}"
        if rid not in rule_ids:
            out.append(f"{where}: no such rule in the spec")
        if not isinstance(e, dict):
            out.append(f"{where}: entry is not an object")
            continue
        kinds = [k for k in ("command", "manual", "waiver") if k in e]
        if len(kinds) != 1:
            out.append(f"{where}: needs exactly one of command, manual, waiver")
            continue
        if "command" in e:
            c = e["command"]
            if not (isinstance(c, list) and c and all(isinstance(a, str) for a in c)):
                out.append(f"{where}: command must be a non-empty list of strings")
            m = e.get("mutation")
            pats = {"expect_output": e.get("expect_output")}
            if m is not None:
                if not (isinstance(m, dict) and all(isinstance(m.get(k), str) and m[k] for k in ("file", "find", "assertion"))
                        and isinstance(m.get("replace"), str)):
                    out.append(f"{where}: mutation needs file, find, replace and assertion")
                    m = {}
                pats["assertion"] = m.get("assertion")
            for key, pat in pats.items():
                try:
                    re.compile(pat or "")
                except re.error as err:
                    out.append(f"{where}: {key} is not a valid regex ({err})")
            if "timeout" in e and not (isinstance(e["timeout"], (int, float)) and e["timeout"] > 0):
                out.append(f"{where}: timeout must be a positive number")
        if e.get("require") not in (None, "test", "mutation"):
            out.append(f"{where}: require must be test or mutation")
    return out


def program_name(prog):
    """Lower-case program name without a Windows executable suffix: python.exe -> python, python3.12 stays."""
    name = prog.lower()
    return next((name[:-len(x)] for x in EXE_SUFFIX if name.endswith(x)), name)


def run(argv, cwd, timeout, allow):
    """Return (outcome, exit code or None, output). outcome: ran / blocked."""
    prog = argv[0]
    if "/" in prog or "\\" in prog:
        return "blocked", None, f"program {prog} must be a bare name found on PATH"
    if program_name(prog) not in allow:
        return "blocked", None, f"program {prog} not in --allow"
    path = shutil.which(prog)
    if not path:
        return "blocked", None, f"program {prog} not found on PATH"
    try:
        p = subprocess.run([path] + argv[1:], cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return "blocked", None, f"timed out after {timeout}s"
    except OSError as err:
        return "blocked", None, f"could not start: {err}"
    return "ran", p.returncode, (p.stdout or "") + (p.stderr or "")


def tail(text):
    return "\n".join(text.strip().splitlines()[-TAIL:])


def mutate(entry, root, base_out, timeout, allow):
    """Apply the mutation inside the isolated copy `root`, run, restore. Return (result, detail)."""
    m = entry["mutation"]
    if re.search(m["assertion"], base_out):
        return "inconclusive", "assertion pattern also matches the passing run; it must match only the failing assertion"
    target = (root / m["file"]).resolve()
    if root.resolve() not in target.parents or not target.is_file():
        return "inconclusive", f"mutation file {m['file']} not found inside --workdir"
    original = target.read_bytes()
    find, replace = m["find"].encode("utf-8"), m["replace"].encode("utf-8")
    hits = original.count(find)
    if hits != 1:
        return "inconclusive", f"find text occurs {hits} times in {m['file']}; it must occur once"
    try:
        target.write_bytes(original.replace(find, replace))
        outcome, rc, out = run(entry["command"], root, timeout, allow)
    finally:
        target.write_bytes(original)
    if outcome != "ran":
        return "inconclusive", f"mutant run {out}"
    if rc == 0:
        return "survived", "tests still pass with the rule broken"
    if re.search(m["assertion"], out):
        return "killed", f"mutant failed (exit {rc}) with the expected assertion"
    return "inconclusive", f"mutant failed (exit {rc}) without the expected assertion (build error or another failure)"


def judge(rid, entry, verify, ref_problems, args, root, map_dir):
    """Return a result dict for one rule."""
    r = {"rule": rid, "verify": verify, "require": (entry or {}).get("require"), "test": (entry or {}).get("test")}
    if entry and "waiver" in entry:
        w = entry["waiver"] if isinstance(entry["waiver"], dict) else {}
        missing = [k for k in ("reason", "approved_by") if not str(w.get(k, "")).strip()]
        if missing:
            r.update(status="UNVERIFIED", reason=f"waiver lacks {', '.join(missing)}")
        else:
            r.update(status="WAIVED", reason=f"waived by {w['approved_by']}: {w['reason']}")
        return r
    if entry and "manual" in entry:
        mv = entry["manual"] if isinstance(entry["manual"], dict) else {}
        missing = [k for k in ("procedure", "evidence", "reviewer", "date") if not str(mv.get(k, "")).strip()]
        if missing:
            r.update(status="UNVERIFIED", reason=f"manual record lacks {', '.join(missing)}")
        elif not (map_dir / mv["evidence"]).is_file():
            r.update(status="UNVERIFIED", reason=f"manual evidence {mv['evidence']} not found")
        else:
            r.update(status="MANUAL_VERIFIED", reason=f"by {mv['reviewer']} on {mv['date']}, evidence {mv['evidence']}")
        return r
    ref = "; ".join(ref_problems)
    if not (entry and "command" in entry):
        if ref:
            r.update(status="UNVERIFIED", reason=f"reference: {ref}")
        elif verify is not None and _cov.UNTESTED.match(verify.strip()):
            r.update(status="UNVERIFIED", reason=verify.strip())
        else:
            r.update(status="REFERENCE_ONLY", reason="reference resolves; no command in the verify map")
        return r
    if not args.run:
        r.update(status="REFERENCE_ONLY", reason="command not run (no --run)" + (f"; reference: {ref}" if ref else ""))
        return r
    execute(r, entry, args, root)
    if ref:
        r["reason"] += f"; reference: {ref}"
    return r


def execute(r, entry, args, root):
    """Run the rule's command (and its mutation with --mutate); fill status and reason into r."""
    timeout = min(entry.get("timeout", args.timeout), args.timeout)
    outcome, rc, out = run(entry["command"], root, timeout, args.allow)
    r.update(command=entry["command"], exit_code=rc, output_tail=tail(out),
             mutation={"result": "not run", "detail": ""})
    if outcome != "ran":
        r.update(status="BLOCKED", reason=out)
        return r
    if rc != 0:
        r.update(status="TEST_FAILED", reason=f"exit {rc}")
        return r
    if entry.get("expect_output") and not re.search(entry["expect_output"], out):
        r.update(status="UNVERIFIED", reason="exit 0 but expect_output not found; the test may not have run")
        return r
    r.update(status="TEST_PASSED", reason="exit 0")
    if args.mutate and entry.get("mutation"):
        result, detail = mutate(entry, root, out, timeout, args.allow)
        r["mutation"] = {"result": result, "detail": detail}
        if result == "killed":
            r.update(status="MUTATION_VERIFIED", reason=detail)
        else:
            r["reason"] = f"exit 0; mutation {result}: {detail}"
    elif entry.get("mutation"):
        r["reason"] = "exit 0; mutation not run (no --mutate)"
    return r


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="rule -> behavior check")
    ap.add_argument("spec")
    ap.add_argument("--map", required=True)
    ap.add_argument("--catalog", nargs="+", default=[])
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--mutate", action="store_true")
    ap.add_argument("--workdir")
    ap.add_argument("--allow", nargs="+", default=[])
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    args.run = args.run or args.mutate
    if args.run and (not args.workdir or not Path(args.workdir).is_dir() or not args.allow):
        print("--run and --mutate need --workdir <existing dir> and --allow <program> ...")
        return 2
    args.allow = {program_name(a) for a in args.allow}
    spec, map_path = Path(args.spec), Path(args.map)
    catalogs = [Path(p).read_text(encoding="utf-8") for p in args.catalog]
    verify = {}
    rule_ids, ref_problems = _cov.scan(spec.read_text(encoding="utf-8"), spec.name, catalogs, verify)
    try:
        data = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"{map_path.name}: cannot read ({err})")
        return 1
    problems = check_map(data, set(rule_ids), map_path.name)
    if problems:
        for p in problems:
            print(p)
        return 1
    by_rule = {rid: [p.split(": ", 1)[1] for p in ref_problems if p.startswith(f"{spec.name} {rid}: ")]
               for rid in rule_ids}
    tmp = tempfile.TemporaryDirectory(prefix="specflow-mutate-") if args.mutate else None
    try:
        if tmp:
            root = Path(tmp.name) / "work"
            try:
                shutil.copytree(args.workdir, root, ignore=COPY_IGNORE)
            except (OSError, shutil.Error) as err:
                print(f"cannot copy --workdir to an isolated folder: {err}")
                return 1
        else:
            root = Path(args.workdir) if args.workdir else None
        results = [judge(rid, data["rules"].get(rid), verify.get(rid), by_rule[rid], args, root, map_path.parent)
                   for rid in rule_ids]
    finally:
        if tmp:
            tmp.cleanup()
    strict_fail = []
    if args.strict:
        for r in results:
            need = NEED.get(r["require"])
            if need and r["status"] not in HUMAN and RANK.get(r["status"], 0) < need:
                strict_fail.append(f"strict: {r['rule']} requires {r['require']}, got {r['status']}")
    count = {s: sum(r["status"] == s for r in results) for s in AUTOMATIC + HUMAN + NOT_VERIFIED}
    failed = count["TEST_FAILED"] > 0 or bool(strict_fail)
    if args.json:
        print(json.dumps({"spec": spec.name, "rules": results, "summary": count,
                          "strict_failures": strict_fail, "exit_code": 1 if failed else 0},
                         ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"{spec.name} {r['rule']}: {r['status']} ({r['reason']})")
        line = lambda names: ", ".join(f"{s} {count[s]}" for s in names)
        print(f"automatic: {line(AUTOMATIC)}")
        print(f"manual, not automatic: {line(HUMAN)}")
        print(f"not verified: {line(NOT_VERIFIED)}")
        for s in strict_fail:
            print(s)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
