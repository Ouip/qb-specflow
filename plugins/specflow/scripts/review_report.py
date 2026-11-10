#!/usr/bin/env python3
"""Review report check: can every finding be traced to the reviewed version of the code.

Usage: python review_report.py <findings.json> --root <reviewed folder> [--no-git]

Checks only traceability, never meaning. Passing shows that each finding's file exists at the reviewed
commit, its line range is inside that file, each quoted piece of evidence really is on those lines, required
fields are filled, and nothing claims a test or command ran without a record of it. It does NOT show that any
finding is a real bug, that the trigger really happens, or that a recorded command was really run.

Paths are relative to --root. With git, files are read at `commit_sha` (`git show <sha>:<path>`), so later
edits to the working tree do not matter; --no-git reads the working tree and does not check the commit.

findings.json:
  {"format": 1, "commit_sha": "<reviewed commit>", "context": "independent_subagents" | "single_context",
   "coverage": {"reviewed": [{"item": "..."}], "not_reviewed": [{"item": "...", "reason": "..."}],
                "insufficient_evidence": [{"item": "...", "reason": "..."}]},
   "findings": [{
     "finding_id": "DR-1", "status": "CONFIRMED" | "NEEDS_VERIFICATION" | "REJECTED", "severity": "High" | "Medium" | "Low",
     "track": "spec_to_code" | "code_to_risk", "commit_sha": "<same as above>", "file": "src/a.c", "line_range": "10-14",
     "trigger": "...", "expected_behavior": "...", "actual_behavior": "...", "impact": "...",
     "evidence": [{"file": "src/a.c", "line_range": "12", "quote": "<text on those lines>"}],
     "verification_method": ["read", "grep", "trace", "test", "script", "build", "hardware"],
     "executions": [{"command": "...", "exit_code": 0, "output_excerpt": "..."}], "reproduced": false,
     "verification_limitations": "...", "counterevidence_checked": ["<where looked>: <what was found>"]}]}
A method of test, script, build or hardware needs at least one execution record; so does "reproduced": true.
CONFIRMED needs a quoted piece of evidence and at least one counter-evidence entry.
Each problem prints one line and exits 1; otherwise prints `review_report: ok ...` and exits 0. 2 = usage error.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REQUIRED = ["finding_id", "status", "severity", "track", "commit_sha", "file", "line_range", "trigger",
            "expected_behavior", "actual_behavior", "impact", "evidence", "verification_method",
            "verification_limitations", "counterevidence_checked"]
STATUS = {"CONFIRMED", "NEEDS_VERIFICATION", "REJECTED"}
SEVERITY = {"High", "Medium", "Low"}
TRACK = {"spec_to_code", "code_to_risk"}
CONTEXT = {"independent_subagents", "single_context"}
METHODS = {"read", "grep", "trace", "test", "script", "build", "hardware"}
EXECUTED = {"test", "script", "build", "hardware"}
RANGE = re.compile(r"^(\d+)(?:\s*[-–]\s*(\d+))?$")


class Source:
    """File contents at the reviewed commit (git) or in the working tree (--no-git)."""

    def __init__(self, root, sha, use_git):
        self.root, self.sha, self.use_git, self.cache = Path(root).resolve(), sha, use_git, {}
        self.top = self.prefix = None
        if use_git:
            top = self.git("rev-parse", "--show-toplevel")
            if top is None:
                raise RuntimeError(f"{root} is not inside a git repository (use --no-git to skip the commit check)")
            self.top = Path(top.strip()).resolve()
            self.prefix = self.root.relative_to(self.top).as_posix()

    def git(self, *args, cwd=None):
        try:
            p = subprocess.run(["git", "-C", str(cwd or self.root), *args], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return p.stdout if p.returncode == 0 else None

    def commit_ok(self, sha):
        return bool(self.git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"))

    def lines(self, rel):
        """Lines of `rel` at the reviewed version, or None if it is not there."""
        if rel not in self.cache:
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                self.cache[rel] = None
            elif self.use_git:
                path = f"{self.prefix}/{rel}" if self.prefix not in ("", ".") else rel
                text = self.git("show", f"{self.sha}:{path}", cwd=self.top)
                self.cache[rel] = None if text is None else text.splitlines()
            else:
                p = self.root / rel
                self.cache[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.is_file() else None
        return self.cache[rel]


def filled(v):
    return bool(v.strip()) if isinstance(v, str) else bool(v)


def check_location(src, rel, rng, where):
    """Problems with a file + line range; returns (problems, (start, end) or None)."""
    if not isinstance(rel, str) or not rel:
        return [f"{where}: file missing"], None
    lines = src.lines(rel)
    if lines is None:
        return [f"{where}: {rel} does not exist at the reviewed version"], None
    m = RANGE.match(str(rng).strip()) if rng is not None else None
    if not m:
        return [f"{where}: line_range {rng!r} is not <n> or <n>-<m>"], None
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    if not 1 <= a <= b <= len(lines):
        return [f"{where}: line_range {rng} is outside {rel} (1-{len(lines)})"], None
    return [], (a, b)


def squash(text):
    return " ".join(text.split())


def check_finding(f, src, sha):
    fid = f.get("finding_id") or "?"
    where, out = f"finding {fid}", []
    for k in REQUIRED:
        if not filled(f.get(k)):
            out.append(f"{where}: {k} missing or empty")
    if f.get("status") and f["status"] not in STATUS:
        out.append(f"{where}: status must be one of {', '.join(sorted(STATUS))}")
    if f.get("severity") and f["severity"] not in SEVERITY:
        out.append(f"{where}: severity must be High, Medium or Low")
    if f.get("track") and f["track"] not in TRACK:
        out.append(f"{where}: track must be spec_to_code or code_to_risk")
    if f.get("commit_sha") and f["commit_sha"] != sha:
        out.append(f"{where}: commit_sha {f['commit_sha']} differs from the report's {sha}")
    if f.get("file"):
        out += check_location(src, f["file"], f.get("line_range"), where)[0]
    quoted = 0
    for i, ev in enumerate(f.get("evidence") or []):
        w = f"{where} evidence {i + 1}"
        if not isinstance(ev, dict):
            out.append(f"{w}: not an object")
            continue
        probs, span = check_location(src, ev.get("file"), ev.get("line_range"), w)
        out += probs
        if span and filled(ev.get("quote")):
            text = squash(" ".join(src.lines(ev["file"])[span[0] - 1:span[1]]))
            if squash(ev["quote"]) not in text:
                out.append(f"{w}: quote not found in {ev['file']}:{ev['line_range']}")
            else:
                quoted += 1
    methods = f.get("verification_method") or []
    if not isinstance(methods, list) or not set(methods) <= METHODS:
        out.append(f"{where}: verification_method must be a list from {', '.join(sorted(METHODS))}")
        methods = []
    runs = f.get("executions") or []
    good_runs = [r for r in runs if isinstance(r, dict) and filled(r.get("command"))
                 and isinstance(r.get("exit_code"), int) and not isinstance(r.get("exit_code"), bool)
                 and filled(r.get("output_excerpt"))]
    if len(good_runs) != len(runs):
        out.append(f"{where}: every execution needs command, an integer exit_code and output_excerpt")
    claimed = sorted(set(methods) & EXECUTED)
    if claimed and not good_runs:
        out.append(f"{where}: verification_method {', '.join(claimed)} without an execution record")
    if f.get("reproduced") not in (None, True, False):
        out.append(f"{where}: reproduced must be true or false")
    if f.get("reproduced") is True and not good_runs:
        out.append(f"{where}: reproduced is true without an execution record")
    if f.get("status") == "CONFIRMED":
        if not quoted:
            out.append(f"{where}: CONFIRMED needs at least one evidence quote found in the source")
        if not filled(f.get("counterevidence_checked")):
            out.append(f"{where}: CONFIRMED needs counterevidence_checked")
    return out


def check_report(data, src):
    out = []
    if not isinstance(data, dict) or data.get("format") != 1:
        return ["report: needs \"format\": 1"]
    sha = data.get("commit_sha")
    if not filled(sha):
        out.append("report: commit_sha missing")
    elif src.use_git and not src.commit_ok(sha):
        out.append(f"report: commit_sha {sha} is not a commit in this repository")
    if data.get("context") not in CONTEXT:
        out.append("report: context must be independent_subagents or single_context")
    cov = data.get("coverage")
    if not isinstance(cov, dict):
        out.append("report: coverage missing")
    else:
        for key, need_reason in (("reviewed", False), ("not_reviewed", True), ("insufficient_evidence", True)):
            items = cov.get(key)
            if not isinstance(items, list):
                out.append(f"report: coverage.{key} must be a list (empty if none)")
                continue
            for it in items:
                if not (isinstance(it, dict) and filled(it.get("item")) and (filled(it.get("reason")) or not need_reason)):
                    out.append(f"report: coverage.{key} entries need item{' and reason' if need_reason else ''}")
                    break
    findings = data.get("findings")
    if not isinstance(findings, list):
        return out + ["report: findings must be a list"]
    ids = [f.get("finding_id") for f in findings if isinstance(f, dict)]
    for dup in sorted({i for i in ids if i and ids.count(i) > 1}):
        out.append(f"report: finding_id {dup} used more than once")
    for f in findings:
        out += check_finding(f, src, sha) if isinstance(f, dict) else ["report: a finding is not an object"]
    return out


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="review report traceability check")
    ap.add_argument("report")
    ap.add_argument("--root", required=True)
    ap.add_argument("--no-git", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    path = Path(args.report)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"{path.name}: cannot read ({err})")
        return 1
    sha = data.get("commit_sha") if isinstance(data, dict) else None
    try:
        src = Source(args.root, sha, not args.no_git)
    except (RuntimeError, ValueError) as err:
        print(f"{path.name}: {err}")
        return 1
    problems = check_report(data, src)
    for p in problems:
        print(f"{path.name} {p}")
    if problems:
        return 1
    if args.no_git:
        print("commit_sha not checked (--no-git): line numbers refer to the working tree")
    print("review_report: ok (traceable: files, lines, quotes, commit and execution records;"
          " this does not show that any finding is a real bug)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
