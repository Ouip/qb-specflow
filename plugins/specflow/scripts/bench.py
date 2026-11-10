#!/usr/bin/env python3
"""Local review benchmark: check the ground truth, reproduce it, and score review runs offline.

Usage:
  python bench.py cases <cases dir>
  python bench.py repro <cases dir> --cc <C compiler> [--case <id> ...] [--timeout <s>]
  python bench.py score <cases dir> <run dir> [<run dir> ...] [--json]

No model is called here. A person (or a session) runs the review on each case's `input/` folder, writes the
findings files, and a person judges each finding; this script only counts. Format, procedure and metric
definitions: `plugins/specflow/bench/README.md`.

cases   checks every `<case>/truth.json` and prints the benchmark size by kind. Exit 1 on a malformed case.
repro   compiles `<case>/input/src/*.c` with `<case>/repro/*.c` using --cc and the case's cflags, runs it (after
        the case's optional run_prefix, e.g. `setarch -R` for ThreadSanitizer under high-entropy ASLR), and
        compares exit code and output with the truth file: REPRODUCED (bug or complex case behaves as the
        truth says), EXERCISED (clean case runs clean), MISMATCH, or BLOCKED (compiler missing, build failed,
        timeout). Exit 1 unless every selected case is REPRODUCED or EXERCISED.
score   reads each run folder (meta.json, judgements.json, <case>.findings.json) and prints precision, recall,
        false-positive rate, evidence validity, coverage and stability. A metric whose inputs are incomplete
        (a CONFIRMED finding without a judgement, fewer than 2 runs) prints "not computed" with the reason.
        Exit 1 on a malformed run.
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

_spec = importlib.util.spec_from_file_location("specflow_review_report", Path(__file__).with_name("review_report.py"))
_rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rr)

KINDS = ("bug", "clean", "complex")
META = ("run_id", "run_index", "model", "effort", "prompt_version", "commit_sha", "tools", "source")
CONFIG = ("model", "effort", "prompt_version")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_cases(root):
    """Return ({case_id: truth}, problems)."""
    cases, out = {}, []
    for t in sorted(Path(root).glob("*/truth.json")):
        cid = t.parent.name
        try:
            truth = load_json(t)
        except ValueError as err:
            out.append(f"{cid}: truth.json is not JSON ({err})")
            continue
        p = []
        if truth.get("format") != 1 or truth.get("case_id") != cid:
            p.append("needs format 1 and case_id equal to the folder name")
        if truth.get("kind") not in KINDS:
            p.append(f"kind must be one of {', '.join(KINDS)}")
        bugs = truth.get("bugs")
        if not isinstance(bugs, list) or (truth.get("kind") == "clean") != (bugs == []):
            p.append("a clean case has no bugs; a bug or complex case has at least one")
        for b in bugs or []:
            if not all(_rr.filled(b.get(k)) for k in ("id", "file", "line_range", "trigger", "root_cause", "impact")):
                p.append(f"bug {b.get('id', '?')} needs id, file, line_range, trigger, root_cause, impact")
            elif not (t.parent / "input" / b["file"]).is_file():
                p.append(f"bug {b['id']}: {b['file']} not in input/")
        prov = truth.get("provenance") or {}
        if prov.get("method") not in ("reproduced", "exercised", "human_reviewed"):
            p.append("provenance.method must be reproduced, exercised or human_reviewed")
        if prov.get("method") in ("reproduced", "exercised"):
            r = prov.get("repro") or {}
            if not (isinstance(r.get("cflags"), list) and isinstance(r.get("expect_exit"), int) and r.get("expect_output")):
                p.append("provenance.repro needs cflags, expect_exit and expect_output")
        if not isinstance(truth.get("units"), list) or not truth["units"]:
            p.append("units must list the files a review should cover")
        out += [f"{cid}: {x}" for x in p]
        cases[cid] = truth
    if not cases:
        out.append(f"{root}: no <case>/truth.json")
    return cases, out


def cmd_cases(args):
    cases, problems = load_cases(args.cases)
    for p in problems:
        print(p)
    for cid, t in cases.items():
        print(f"{cid}: {t['kind']}, {len(t['bugs'])} bug(s), {t['provenance']['method']}, "
              f"human review {t['provenance'].get('human_review', 'none')}")
    count = {k: sum(t["kind"] == k for t in cases.values()) for k in KINDS}
    print(f"size: {len(cases)} cases ({', '.join(f'{v} {k}' for k, v in count.items())}), "
          f"{sum(len(t['bugs']) for t in cases.values())} known bugs")
    return 1 if problems else 0


def cmd_repro(args):
    cases, problems = load_cases(args.cases)
    if problems:
        for p in problems:
            print(p)
        return 1
    if not (Path(args.cc).is_file() or shutil.which(args.cc)):
        print(f"compiler {args.cc} not found")
        return 1
    bad = False
    for cid in args.case or list(cases):
        t, d = cases.get(cid), Path(args.cases) / cid
        if t is None:
            print(f"{cid}: no such case")
            bad = True
            continue
        r = t["provenance"].get("repro")
        if not r:
            print(f"{cid}: no repro (provenance {t['provenance']['method']})")
            continue
        srcs = sorted(str(p) for p in (d / "input" / "src").glob("*.c")) + sorted(str(p) for p in (d / "repro").glob("*.c"))
        with tempfile.TemporaryDirectory(prefix="specflow-bench-") as tmp:
            exe = Path(tmp) / "repro"
            build = [args.cc, *r["cflags"], "-g", "-I", str(d / "input" / "src"), *srcs, "-o", str(exe)]
            try:
                b = subprocess.run(build, capture_output=True, text=True, timeout=args.timeout)
                if b.returncode != 0:
                    raise OSError("build failed: " + (b.stderr.strip().splitlines() or ["?"])[-1])
                run = [*r.get("run_prefix", []), str(exe)]
                if not shutil.which(run[0]) and run[0] != str(exe):
                    raise OSError(f"{run[0]} not found (run_prefix)")
                p = subprocess.run(run, capture_output=True, text=True, errors="replace", timeout=args.timeout)
            except subprocess.TimeoutExpired:
                print(f"{cid}: BLOCKED timed out after {args.timeout}s")
                bad = True
                continue
            except OSError as err:
                print(f"{cid}: BLOCKED {err}")
                bad = True
                continue
        out = p.stdout + p.stderr
        ok = p.returncode == r["expect_exit"] and re.search(r["expect_output"], out)
        label = ("EXERCISED" if t["kind"] == "clean" else "REPRODUCED") if ok else "MISMATCH"
        print(f"{cid}: {label} exit {p.returncode} (expected {r['expect_exit']}), "
              f"output {'has' if re.search(r['expect_output'], out) else 'lacks'} {r['expect_output']!r}")
        bad = bad or not ok
    return 1 if bad else 0


def score_run(run, cases, root):
    """Return (metrics dict, problems)."""
    problems = []
    try:
        meta = load_json(run / "meta.json")
        judged = load_json(run / "judgements.json")
    except (OSError, ValueError) as err:
        return None, [f"{run.name}: needs meta.json and judgements.json ({err})"]
    problems += [f"{run.name}: meta.json lacks {k}" for k in META if not _rr.filled(meta.get(k))]
    if meta.get("source") not in ("commit", "working_tree"):
        problems.append(f"{run.name}: meta.json source must be commit or working_tree")
    if not _rr.filled(judged.get("judged_by")):
        problems.append(f"{run.name}: judgements.json lacks judged_by")
    if problems:
        return None, problems
    matches = judged.get("matches", {})
    m = {"run": meta["run_id"], "run_index": meta["run_index"], "config": {k: meta[k] for k in CONFIG},
         "cases_run": [], "cases_missing": [], "per_case": {}, "tp": 0, "fp": 0, "partial": 0, "duplicates": 0,
         "new": 0, "unjudged": 0, "bugs_total": 0, "bugs_found": 0, "clean_run": 0, "clean_with_fp": 0,
         "findings": 0, "valid_findings": 0, "status": {}, "coverage": []}
    for cid, truth in cases.items():
        fpath = run / f"{cid}.findings.json"
        if not fpath.is_file():
            m["cases_missing"].append(cid)
            continue
        try:
            report = load_json(fpath)
        except ValueError as err:
            problems.append(f"{run.name}/{fpath.name}: not JSON ({err})")
            continue
        try:
            src = _rr.Source(Path(root) / cid / "input", report.get("commit_sha"), meta["source"] == "commit")
        except (RuntimeError, ValueError) as err:
            problems.append(f"{run.name}/{fpath.name}: {err}")
            continue
        m["cases_run"].append(cid)
        bug_ids = {b["id"] for b in truth["bugs"]}
        found, fps = set(), 0
        for f in report.get("findings", []):
            if not isinstance(f, dict):
                problems.append(f"{run.name}/{fpath.name}: a finding is not an object")
                continue
            m["findings"] += 1
            st = f.get("status", "?")
            m["status"][st] = m["status"].get(st, 0) + 1
            if not _rr.check_finding(f, src, report.get("commit_sha")):
                m["valid_findings"] += 1
            if st != "CONFIRMED":
                continue
            j = matches.get(cid, {}).get(f.get("finding_id"))
            if j is None:
                m["unjudged"] += 1
                continue
            if j.get("bug") == "NEW":
                m["new"] += 1
            elif j.get("bug") in bug_ids and all(j.get(k) is True for k in ("trigger", "root_cause", "impact")):
                if j["bug"] in found:
                    m["duplicates"] += 1
                else:
                    found.add(j["bug"])
                    m["tp"] += 1
            else:
                fps += 1
                m["fp"] += 1
                m["partial"] += j.get("bug") in bug_ids
        m["bugs_total"] += len(bug_ids)
        m["bugs_found"] += len(found)
        if truth["kind"] == "clean":
            m["clean_run"] += 1
            m["clean_with_fp"] += fps > 0
        reviewed = [str(i.get("item", "")) for i in (report.get("coverage") or {}).get("reviewed", []) if isinstance(i, dict)]
        units = truth["units"]
        hit = [u for u in units if any(r == u or r.startswith(u + ":") for r in reviewed)]
        m["coverage"].append(len(hit) / len(units))
        m["per_case"][cid] = {"found": sorted(found), "has_fp": fps > 0}
    return m, problems


def ratio(num, den):
    return {"value": round(num / den, 3), "num": num, "den": den} if den else {"value": None, "num": num, "den": den,
                                                                           "why": "denominator is 0"}


def metrics(m):
    out = {}
    if m["unjudged"]:
        why = f"{m['unjudged']} CONFIRMED finding(s) have no judgement"
        for k in ("precision", "recall", "false_positive_rate"):
            out[k] = {"value": None, "why": why}
    else:
        out["precision"] = ratio(m["tp"], m["tp"] + m["fp"])
        out["recall"] = ratio(m["bugs_found"], m["bugs_total"])
        out["false_positive_rate"] = ratio(m["clean_with_fp"], m["clean_run"])
    out["evidence_validity"] = ratio(m["valid_findings"], m["findings"])
    cov = m["coverage"]
    out["coverage"] = {"value": round(sum(cov) / len(cov), 3) if cov else None, "cases": len(cov)}
    return out


def stability(runs):
    if len(runs) < 2:
        return {"value": None, "why": "needs 2 or more runs of the same model, effort and prompt version"}
    if any(r["unjudged"] for r in runs):
        return {"value": None, "why": "some CONFIRMED findings have no judgement"}
    multi = [cid for cid in runs[0]["per_case"] if sum(cid in r["per_case"] for r in runs) >= 2]
    stable = [cid for cid in multi
              if len({(tuple(r["per_case"][cid]["found"]), r["per_case"][cid]["has_fp"]) for r in runs if cid in r["per_case"]}) == 1]
    return ratio(len(stable), len(multi)) | {"unstable_cases": sorted(set(multi) - set(stable))}


def fmt_ratio(r):
    if r.get("value") is None:
        return f"not computed ({r.get('why', 'no data')})"
    return f"{r['value']:.2f} ({r['num']}/{r['den']})" if "num" in r else f"{r['value']:.2f}"


def cmd_score(args):
    cases, problems = load_cases(args.cases)
    runs = []
    for rd in args.runs:
        m, p = score_run(Path(rd), cases, args.cases)
        problems += p
        if m:
            runs.append(m)
    configs = {json.dumps(r["config"], sort_keys=True) for r in runs}
    if len(configs) > 1:
        problems.append("runs differ in model, effort or prompt_version; score them separately")
    if problems:
        for p in problems:
            print(p)
        return 1
    result = {"runs": [dict(r, metrics=metrics(r)) for r in runs], "stability": stability(runs)}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    for r in result["runs"]:
        mt = r["metrics"]
        print(f"run {r['run']} (index {r['run_index']}; {', '.join(f'{k} {v}' for k, v in r['config'].items())})")
        print(f"  cases run {len(r['cases_run'])} of {len(cases)}" +
              (f"; missing {', '.join(r['cases_missing'])}" if r["cases_missing"] else ""))
        print(f"  findings {r['findings']} ({', '.join(f'{k} {v}' for k, v in sorted(r['status'].items()))})")
        print(f"  TP {r['tp']}, FP {r['fp']} (partial match {r['partial']}), duplicates {r['duplicates']}, "
              f"FN {r['bugs_total'] - r['bugs_found']}, new defects to add to ground truth {r['new']}, unjudged {r['unjudged']}")
        print(f"  precision {fmt_ratio(mt['precision'])}")
        print(f"  recall {fmt_ratio(mt['recall'])}")
        print(f"  false-positive rate (clean cases with a false CONFIRMED finding) {fmt_ratio(mt['false_positive_rate'])}")
        print(f"  evidence validity {fmt_ratio(mt['evidence_validity'])}")
        print(f"  coverage {fmt_ratio(mt['coverage'])}")
    print(f"stability {fmt_ratio(result['stability'])}")
    return 0


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="local review benchmark")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cases")
    c.add_argument("cases")
    r = sub.add_parser("repro")
    r.add_argument("cases")
    r.add_argument("--cc", required=True)
    r.add_argument("--case", nargs="+")
    r.add_argument("--timeout", type=float, default=120)
    s = sub.add_parser("score")
    s.add_argument("cases")
    s.add_argument("runs", nargs="+")
    s.add_argument("--json", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    return {"cases": cmd_cases, "repro": cmd_repro, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
