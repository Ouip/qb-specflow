#!/usr/bin/env python3
"""dispatch_run: run the pre-dispatch gate, then start an implementer tool in an isolated git worktree and judge the run
from evidence only. Stdlib only.

  python dispatch_run.py --product DIR check [--installed FILE]
  python dispatch_run.py --product DIR run TASK --prompt-file P --allow GLOB... [--frozen GLOB...] [--base REF]
                         [--repo R --unit U...] [--timeout S] [--dry-run] [--tool PATH] [--installed FILE]
                         [-- EXTRA_ARGS...]
  python dispatch_run.py --product DIR cancel RUN_ID

--product is the product folder (holds specflow.md, workflow.json, components.json); it may also come after the
subcommand. `check` runs only the gate. `run` does, in this order: read the policy and check the arguments (2), run the
gate (lock mismatch 3, capability missing 5, probe timeout 6; nothing is created when it fails), find the program (5)
and the base commit (4), check --frozen against the base commit's files and the report schema (2), take the claim
resource dispatch:<TASK> (3 if held), open a worktree and a run directory, start the tool and judge. `cancel` asks a
running run to stop (the runner kills the whole process tree and writes a checkpoint).

TASK is a component id of components.json (its repo is the component's repo, the base is --base or comp/<name>, and the
report's task must be TASK), or a fix dispatch: any other name ([A-Za-z0-9._-]) with --repo (a repo id of workflow.json),
--base and --unit (the component ids the report's task may be). --allow / --frozen are repo-relative, / separated globs
(* does not cross /, ** does); every changed file must match --allow and none --frozen. A --frozen glob may name a
directory (dir or dir/ means dir/**; a leading ./ is dropped); one that matches no file of the base commit is refused
(2), since it would freeze nothing. --tool replaces the program (argv[0]) of the template; extra arguments after -- are
only accepted if dispatch.implementer.extra_allowed lists them.

workflow.json, block "dispatch" (besides timeout_seconds, requires_lock, requires_capabilities):
  "implementer": {"argv": ["<tool>", "--cd", "{worktree}", "-o", "{report}", "--schema", "{schema}", "-m", "{model}"],
                  "extra_allowed": {"--ephemeral": false, "--image": true}}
  "report": {"schema": "<report schema, relative to the product folder>", "task_field": "task", "status_field": "status",
             "done_requires": [{"field": "unmet", "empty": true}, {"field": "tests", "in": ["pass", "na"]}]}
argv placeholders: {worktree} {run_dir} {report} (= run_dir/report.json) {schema} (a copy in the run directory)
{model} {effort} (from roles.implementer; if the template uses one and the role does not give it, exit 2). The prompt
is the tool's stdin. extra_allowed: flag -> true if it takes an existing file (passed resolved against the caller's
directory: the tool runs in the worktree), false if it takes no value; a flag that the template already fixes is refused
(2), as is anything not listed. On Windows a program that is a .cmd / .bat runs through cmd.exe, which parses the command
line again: an argument with & | < > ^ % is then refused (2) before anything is created. done_requires: extra
conditions a `done` report meets (empty: the field is [] / "" / null; in: the value is one of the list; equals: the
value is exactly it).
The report schema may use only what the judge checks: top level type "object", required, additionalProperties
(true / false), properties; per property type (a name, or a list such as ["array", "null"]), enum, minimum, items
(type only); pattern only on the task field (not read: the task is compared with the dispatch's units); annotations
($schema, $id, $comment, title, description, default, examples) anywhere. Anything else is refused (2) at the start.

Verdicts and exit codes (a tool exit 0 is only a process fact): done 0; tree_not_stopped 3 (the claim stays);
timeout / cancelled 6; scope_violation 3; no_evidence 4 (no report, wrong task, broken JSON, fields do not match the
schema, a `done` that breaks done_requires, tool exit code != 0, the judging itself failed); partial / blocked 4.
Other: arguments 2, gate 3 / 5 / 6, claim held 3, worktree cannot be opened 3, program or machine cannot run it 5,
missing base commit / policy / repo mapping 4.

Evidence is only what this run left: run directory (manifest.json, prompt.md, result.schema.json, report.json, logs,
process.json, checkpoint.json on timeout or cancel, result.json) and the worktree. An older report is never read. The
report schema is read from the product folder once, at the start, never from the run directory (the tool can write
there): its sha256 is recorded in the manifest, and the judge uses the schema as read then (a change to the file during
the run does not count).

Limits (also written into every manifest): a git worktree is not an OS sandbox, the tool can still write outside it. The
scope check only sees what git reports, in the worktree and on branch run/<run id> together (commits, tracked changes
and untracked files that are not ignored; a worktree no longer on that branch is a scope violation); ignored files and
writes outside the worktree are not seen. A worktree shares refs with its repo: the start ref (--base / comp/<name>,
by its full name; --base HEAD means the branch the source repo has checked out) that no longer points at the start
commit when judging is a scope violation; a start given as a bare SHA or a detached HEAD has no ref to watch, and other
refs the tool may write (e.g. git update-ref on another branch) are not checked. On Windows a process started inside
WSL that detaches itself is not in the Job Object, so process_tree_stopped only speaks for the Windows side (see
process_runner.py). Nothing here cleans up a worktree or merges a branch: a person reviews run/<run id> and merges it.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _load_sibling(name: str, module_name: str):
    """Load a sibling script by file path under a unique module name (the scripts folder is not a package, and a product
    adapter may itself be importable under the plain name; the core must never bind to that). One copy per process: a
    module already loaded under module_name from this very file is reused; one from another path is not. A failed load
    leaves sys.modules as it was."""
    path = Path(__file__).resolve().parent / f"{name}.py"
    old = sys.modules.get(module_name)
    try:
        if old is not None and Path(old.__file__).resolve() == path:
            return old
    except (AttributeError, TypeError, OSError):
        pass
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod  # registered before exec: dataclasses look the module up by name
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        if old is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = old
        raise
    return mod


state_io = _load_sibling("state_io", "specflow_state_io")
process_runner = _load_sibling("process_runner", "specflow_process_runner")
workflow_config = _load_sibling("workflow_config", "specflow_workflow_config")
registry = _load_sibling("registry", "specflow_registry")

ConfigError = workflow_config.ConfigError
TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STATUSES = ("done", "partial", "blocked")
ISOLATION = ("git worktree: its own working directory, index, build output, logs and temp; not an OS sandbox: the "
             "implementer can still write outside the worktree; staying inside --allow is checked after the run from "
             "the actual changes")
LIMITS = [
    "not an OS sandbox: the implementer can write anywhere its user can",
    "the scope check sees only what git reports in the worktree and on branch run/<run id> (commits, tracked changes "
    "and untracked files that are not ignored); ignored files and writes outside the worktree are not seen",
    "a worktree shares refs with its repo: a start ref that moved off the start commit is a scope violation; a start "
    "given as a bare SHA or a detached HEAD has no ref to watch, and other refs the implementer may write are not "
    "checked",
    "on Windows a process started inside WSL that detaches itself is not in the Job Object; process_tree_stopped "
    "only speaks for the Windows side",
    "no automatic worktree cleanup and no automatic merge: a person reviews and merges the run/<run id> branch",
]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _bad(msg: str, code: int = 2):
    return ConfigError(msg, code)


# ---------- scope globs ----------

def _glob_re(pat):
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pat.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pat[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pat[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pat[i]), i + 1
    return re.compile(out + r"\Z")


def in_scope(path, patterns):
    """Repo-relative path (/ separated) matches any glob (* does not cross /, ** does)."""
    return any(_glob_re(p).match(path) for p in patterns)


def _bad_glob(g):
    return not g or "\\" in g or ":" in g or g.startswith("/") or ".." in g.split("/")


def _frozen_form(g):
    """A --frozen glob as written, made comparable: a leading ./ is dropped, a trailing / means the whole directory."""
    while g.startswith("./"):
        g = g[2:]
    return g.rstrip("/") + "/**" if g.endswith("/") else g


def frozen_globs(repo, base_sha, frozen):
    """The --frozen globs as checked: _frozen_form, and a directory of the base commit's tree means all of it (dir/**).
    A glob that matches no file of that tree would freeze nothing (a typo, a wrong case, a path that is not there):
    ConfigError(2) naming every such glob, before anything is created."""
    r = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "-z", base_sha], capture_output=True)
    if r.returncode:
        raise _bad(f"cannot list the files of {base_sha[:12]}: {r.stderr.decode('utf-8', 'replace').strip()}", 4)
    files = [x.decode("utf-8", "replace") for x in r.stdout.split(b"\0") if x]
    dirs = {"/".join(p.split("/")[:i]) for p in files for i in range(1, p.count("/") + 1)}
    out, none = [], []
    for g in frozen:
        f = _frozen_form(g)
        f = f + "/**" if f in dirs else f
        if not any(_glob_re(f).match(p) for p in files):
            none.append(g)
        out.append(f)
    if none:
        raise _bad(f"--frozen {' '.join(none)}: matches no file of {base_sha[:12]}, so it would freeze nothing (check "
                   "the spelling, the case and the path; a directory may be given as dir or dir/)")
    return out


# ---------- argv template ----------

def _used(template: list[str]) -> set[str]:
    return {m for a in template for m in re.findall(r"\{(\w+)\}", a)}


def fill_template(template: list[str], values: dict) -> list[str]:
    return [re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]), a) for a in template]


_CMD_SPECIAL = "&|<>^%"


def cmd_reparse_problem(argv: list[str]) -> str | None:
    """On Windows a .cmd / .bat program runs through cmd.exe, which parses the whole command line again: & | < > ^ % in
    any argument would act as cmd.exe syntax (start another command, redirect, expand a variable). None if safe."""
    if os.name != "nt" or Path(argv[0]).suffix.lower() not in (".cmd", ".bat"):
        return None
    bad = [x for x in argv if any(c in x for c in _CMD_SPECIAL)]
    if not bad:
        return None
    return (f"the implementer program {argv[0]} is a .cmd / .bat file: Windows runs it through cmd.exe, which parses "
            f"the command line again, so {' '.join(_CMD_SPECIAL)} in an argument would act as cmd.exe syntax; refused: "
            f"{', '.join(bad)} (change that path or value, or use a program that is not a batch file)")


def extra_args(allowed: dict, template: list[str], extra: list[str]) -> list[str]:
    """Extra tool arguments checked against dispatch.implementer.extra_allowed; returns what to append, else ConfigError(2)."""
    fixed = workflow_config.template_flags(template)
    out, i = [], 0
    while i < len(extra):
        a = extra[i]
        name, eq, val = a.partition("=") if a.startswith("--") else (a, "", "")
        if name not in allowed:
            is_fixed = name in fixed or (a.startswith("-") and not a.startswith("--") and a[:2] in fixed)
            why = "is fixed by the argv template and cannot be overridden" if is_fixed else "is not allowed"
            raise _bad(f"extra argument \"{a}\" {why} (allowed: {' '.join(allowed) or 'none'})")
        if allowed[name]:
            if not eq:
                i += 1
                val = extra[i] if i < len(extra) else ""
            if not val or val.startswith("-") or not Path(val).is_file():
                raise _bad(f"{name} needs an existing file: {val or '(none given)'}")
            out += [name, str(Path(val).resolve())]  # the tool runs in the worktree, not in the caller's directory
        elif eq:
            raise _bad(f"{name} takes no value: {a}")
        else:
            out.append(name)
        i += 1
    return out


# ---------- report and verdict ----------

_JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
_TYPE_NAMES = (*_JSON_TYPES, "null")
_ANNOTATIONS = {"$schema", "$id", "$comment", "title", "description", "default", "examples"}


def _type_list(t):
    """A schema "type" as a list of names (a name, or a non-empty list of names); None if it is neither."""
    ts = [t] if isinstance(t, str) else t if isinstance(t, list) else None
    return ts if ts and all(isinstance(x, str) and x in _TYPE_NAMES for x in ts) else None


def _type_ok(v, t) -> bool:
    if t == "null":
        return v is None
    return isinstance(v, _JSON_TYPES[t]) and not (t in ("integer", "number") and isinstance(v, bool))


def schema_problems(schema: dict, task_field: str) -> list[str]:
    """What the report schema uses beyond what check_report checks; a dispatch refuses it at the start (2) instead of
    silently ignoring it. The subset: top level type "object", required, additionalProperties (true / false),
    properties; per property type (a name or a list of names, "null" included), enum, minimum, items (type only);
    pattern only on the task field (not read there: the task is compared with the dispatch's units); annotations
    ($schema, $id, $comment, title, description, default, examples) anywhere."""
    out = []

    def keys(d, allowed, where):
        out.extend(f"{where}: {k} is not supported" for k in d if k not in allowed and k not in _ANNOTATIONS)

    keys(schema, {"type", "required", "additionalProperties", "properties"}, "top level")
    if schema.get("type", "object") != "object":
        out.append("top level: type must be \"object\"")
    req = schema.get("required", [])
    if not (isinstance(req, list) and all(isinstance(x, str) for x in req)):
        out.append("required must be a list of field names")
    if not isinstance(schema.get("additionalProperties", True), bool):
        out.append("additionalProperties must be true or false")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return out + ["properties must be an object"]
    for k, spec in props.items():
        w = f"properties.{k}"
        if not isinstance(spec, dict):
            out.append(f"{w} must be an object")
            continue
        keys(spec, {"type", "enum", "minimum", "items"} | ({"pattern"} if k == task_field else set()), w)
        if "type" in spec and _type_list(spec["type"]) is None:
            out.append(f"{w}.type must be one of {', '.join(_TYPE_NAMES)} or a list of them")
        if "enum" in spec and not isinstance(spec["enum"], list):
            out.append(f"{w}.enum must be a list")
        m = spec.get("minimum", 0)
        if isinstance(m, bool) or not isinstance(m, (int, float)):
            out.append(f"{w}.minimum must be a number")
        it = spec.get("items", {})
        if not isinstance(it, dict):
            out.append(f"{w}.items must be an object")
            continue
        keys(it, {"type"}, f"{w}.items")
        if "type" in it and _type_list(it["type"]) is None:
            out.append(f"{w}.items.type must be one of {', '.join(_TYPE_NAMES)} or a list of them")
    return out


def _field_problems(k, v, spec):
    ts = _type_list(spec.get("type")) or []
    if ts and not any(_type_ok(v, t) for t in ts):
        return [f"report field {k} must be {' or '.join(ts)}"]
    out = []
    if "enum" in spec and v not in spec["enum"]:
        out.append(f"report field {k}: value {v!r} is not in {spec['enum']}")
    if "minimum" in spec and isinstance(v, (int, float)) and v < spec["minimum"]:
        out.append(f"report field {k} must not be below {spec['minimum']}")
    its = _type_list((spec.get("items") or {}).get("type")) or []
    if isinstance(v, list) and its and any(not any(_type_ok(x, t) for t in its) for x in v):
        out.append(f"every item of report field {k} must be {' or '.join(its)}")
    return out


def _is_empty(v) -> bool:
    return v is None or (isinstance(v, (str, list, dict)) and len(v) == 0)


def _require_problem(rep: dict, r: dict) -> str | None:
    f = r["field"]
    if f not in rep:
        return f"status is done but the report has no field {f}"
    v = rep[f]
    if "empty" in r and not _is_empty(v):
        return f"status is done but {f} is {v!r} (must be empty)"
    if "in" in r and v not in r["in"]:
        return f"status is done but {f} is {v!r} (allowed: {', '.join(map(repr, r['in']))})"
    if "equals" in r and not (v == r["equals"] and type(v) is type(r["equals"])):
        return f"status is done but {f} is {v!r} (must be {r['equals']!r})"
    return None


def check_report(path, schema: dict, units: list[str], cfg: dict):
    """This run's report: (content or None, problems). Checks valid JSON, the schema's required / type / enum / minimum /
    additionalProperties, the task field naming one of `units`, a known status, and for `done` every dispatch.report
    done_requires rule. A schema pattern is not read: the task is compared with `units` directly."""
    path = Path(path)
    if not path.is_file():
        return None, [f"no report file {path.name}: a tool exit code 0 does not mean it is done"]
    try:
        rep = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return None, [f"the report is not valid JSON ({e})"]
    if not isinstance(rep, dict):
        return None, ["the report must be a JSON object"]
    props = schema.get("properties") or {}
    probs = [f"the report lacks field {k}" for k in schema.get("required", []) if k not in rep]
    if schema.get("additionalProperties") is False:
        probs += [f"the report has field {k} that the schema does not have" for k in rep if k not in props]
    for k, spec in props.items():
        if k in rep:
            probs += _field_problems(k, rep[k], spec)
    tf, sf = cfg["task_field"], cfg["status_field"]
    if rep.get(tf) not in units:
        probs.append(f"the report's {tf} is {rep.get(tf)!r}; this dispatch is {' / '.join(units)}: not a report of this task")
    status = rep.get(sf)
    if status not in STATUSES:
        probs.append(f"the report's {sf} is {status!r}; it must be one of {', '.join(STATUSES)}")
    elif status == "done":
        probs += [p for p in (_require_problem(rep, r) for r in cfg.get("done_requires", [])) if p]
    return rep, probs


def changed_files(wt, base_sha, branch):
    """What the run changed against the base: the worktree (commits, uncommitted, untracked; not what git ignores) and
    the branch a person merges (refs/heads/<branch>), together: (list, error or None)."""
    out = set()
    for args in (["diff", "--name-only", "--no-renames", "-z", base_sha],
                 ["diff", "--name-only", "--no-renames", "-z", base_sha, f"refs/heads/{branch}", "--"],
                 ["ls-files", "--others", "--exclude-standard", "-z"]):
        r = subprocess.run(["git", "-C", str(wt), *args], capture_output=True)
        if r.returncode:
            return sorted(out), r.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed"
        out |= {x.decode("utf-8", "replace") for x in r.stdout.split(b"\0") if x}
    return sorted(out), None


def head_problem(wt, branch) -> str | None:
    """The worktree must still be on refs/heads/<branch>: otherwise what it holds is not what the branch brings."""
    r = subprocess.run(["git", "-C", str(wt), "symbolic-ref", "-q", "HEAD"], capture_output=True)
    head = r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else ""
    if head == f"refs/heads/{branch}":
        return None
    return (f"(the worktree HEAD is {head or 'detached'}, not refs/heads/{branch}: the scope of what a merge of "
            f"{branch} brings cannot be verified from the worktree)")


def start_ref(repo, base_ref) -> str | None:
    """The full name of the ref the start was given as (comp/a1 -> refs/heads/comp/a1; HEAD -> the branch the source
    repo has checked out), so it is watched by name and not through the worktree's own HEAD; None for a bare SHA, a
    detached HEAD or an expression (nothing a merge could take a moved commit from)."""
    full = _git_out(repo, "rev-parse", "--symbolic-full-name", base_ref)
    return full if full.startswith("refs/") else None


def base_ref_problem(repo, watch, base_sha) -> str | None:
    """The start ref (start_ref, resolved in the source repo) must still point at the start commit: a worktree shares
    refs with its repo, so a commit moved onto it (git update-ref, git push . HEAD:<ref>) is in no diff of this run but
    would reach a merge. watch None: nothing to check."""
    if watch is None:
        return None
    now = _git_out(repo, "rev-parse", "--verify", "--quiet", f"{watch}^{{commit}}")
    if now == base_sha:
        return None
    return (f"(the start ref {watch} moved during the run: it was {base_sha[:12]}, now "
            f"{now[:12] or 'it does not resolve'}; what was put there is not in the scope check)")


def _process_facts(res):
    return {"exit_code": res.exit_code, "timed_out": res.timed_out, "cancelled": res.cancelled,
            "process_tree_stopped": res.process_tree_stopped}


def _verdict(res, violations, problems, status):
    """(exit code, verdict): process facts first, then scope, then evidence."""
    return (3, "tree_not_stopped") if not res.process_tree_stopped else \
        (6, "timeout") if res.timed_out else (6, "cancelled") if res.cancelled else \
        (3, "scope_violation") if violations else (4, "no_evidence") if problems else \
        (4, status) if status in ("partial", "blocked") else (0, "done")


def judge_dispatch(res, run_dir, wt, repo, watch, base_sha, task_id, units, allow, frozen, cfg, schema):
    """Verdict of a dispatch run, from this run's directory and worktree only. repo / watch / base_sha: the source repo,
    the start ref's full name (start_ref; None: not watched) and the commit it pointed at when the run started.
    cfg: the dispatch.report block; schema:
    the report schema as read from the product folder when the run started (never re-read: the run directory copy is
    writable by the tool, and a change in the product folder during the run does not count). Returns result.json's content."""
    run_dir = Path(run_dir)
    branch = f"run/{run_dir.name}"
    changed, err = changed_files(wt, base_sha, branch)
    violations = [f"{f} (frozen)" if in_scope(f, frozen) else f"{f} (outside the allowed scope)"
                  for f in changed if in_scope(f, frozen) or not in_scope(f, allow)]
    if err:
        violations.append(f"(cannot list the changes, so the scope cannot be verified: {err})")
    for v in (head_problem(wt, branch), base_ref_problem(repo, watch, base_sha)):
        if v:
            violations.append(v)
    path = run_dir / "report.json"
    rep, problems = check_report(path, schema, units, cfg)
    if res.exit_code != 0 and not (res.timed_out or res.cancelled):
        problems.insert(0, f"the tool exit code is {res.exit_code}")
    problems += [f"changed outside the scope: {v}" for v in violations]
    status = rep.get(cfg["status_field"]) if rep else None
    code, verdict = _verdict(res, violations, problems, status)
    proc = json.loads((run_dir / process_runner.PROCESS).read_text(encoding="utf-8"))
    return {"schema_version": 1, "run_id": run_dir.name, "task_id": task_id, "finished_at": _now(),
            "exit_code": code, "verdict": verdict, "problems": problems, "process": _process_facts(res),
            "leftover_pids": proc["leftover_pids"],
            "report": None if not path.is_file() else {
                "path": "report.json", "sha256": state_io.file_revision(path),
                "task": rep.get(cfg["task_field"]) if rep else None, "status": status},
            "changed_files": changed, "scope_violations": violations, "worktree": str(wt)}


def unjudged(res, run_dir, wt, task_id, err) -> dict:
    """result.json of a run whose judging itself failed: never done (no_evidence 4 unless a process fact says more)."""
    run_dir = Path(run_dir)
    try:
        left = json.loads((run_dir / process_runner.PROCESS).read_text(encoding="utf-8"))["leftover_pids"]
    except Exception:  # noqa: BLE001
        left = None
    problems = [f"the run could not be judged ({type(err).__name__}: {err}): it does not count as done"]
    code, verdict = _verdict(res, [], problems, None)
    return {"schema_version": 1, "run_id": run_dir.name, "task_id": task_id, "finished_at": _now(),
            "exit_code": code, "verdict": verdict, "problems": problems, "process": _process_facts(res),
            "leftover_pids": left, "report": None, "changed_files": [], "scope_violations": [], "worktree": str(wt)}


def _stop_note(res, run_dir) -> None:
    if not res.process_tree_stopped:
        print(f"  the process tree could not be confirmed stopped: the claim stays and blocks a redispatch "
              f"(python registry.py claims); see {Path(run_dir) / process_runner.PROCESS}")
    elif res.timed_out or res.cancelled:
        print(f"  {'timed out' if res.timed_out else 'cancelled'}: the whole process tree is stopped; this run does not "
              f"count as success; where it stopped is in {Path(run_dir) / process_runner.CHECKPOINT}")


def print_result(result, run_dir):
    print(f"  result: {result['verdict']} (exit code {result['exit_code']}; tool exit={result['process']['exit_code']})")
    rep = Path(run_dir) / "report.json"
    if result["report"] and result["report"]["status"]:
        try:
            r = json.loads(rep.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            r = {}
        print("  REPORT " + " ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in r.items() if k != "notes"))
        if r.get("notes"):
            print(f"         notes: {r['notes']}")
    print(f"  changed files: {len(result['changed_files'])} ({result['worktree']})")
    for p in result["problems"]:
        print(f"  [problem] {p}")
    if result["leftover_pids"]:
        print(f"  [note] processes were left after the tool ended and have been stopped: "
              f"{' '.join(map(str, result['leftover_pids']))}")


# ---------- commands ----------

def _git_out(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""


def gate(product: Path, installed: Path | None) -> int:
    """The pre-dispatch gate (workflow_config.dispatch_gate); prints why it blocked."""
    code, msgs = workflow_config.dispatch_gate(product, installed)
    for m in msgs:
        print(f"  {m}")
    return code


def cmd_check(a) -> int:
    code = gate(a.product, a.installed)
    print("dispatch gate: ok to dispatch" if code == 0 else f"dispatch gate: cannot dispatch (exit code {code})")
    return code


def _plan(a, policy: dict):
    """Repo, base and report units of the task: a component fixes all three; a fix dispatch gives them. ConfigError(2) on bad input."""
    comps = registry.load_registry(a.product)[0].get("components", [])
    comp = next((c for c in comps if isinstance(c, dict) and c.get("id") == a.task), None)
    if comp is not None:
        if a.repo or a.unit:
            raise _bad(f"{a.task} is a component: its repo and report units come from components.json, so --repo and "
                       "--unit are not accepted")
        if not comp.get("name"):
            raise _bad(f"component {a.task} has no name in components.json (the base branch is comp/<name>)")
        if comp.get("repo") not in policy["repos"]:
            raise _bad(f"component {a.task}: repo \"{comp.get('repo')}\" is not in the repos of workflow.json")
        return comp["repo"], a.base or f"comp/{comp['name']}", [a.task]
    ids = {c.get("id") for c in comps if isinstance(c, dict)}
    if not (a.repo in policy["repos"] and a.base and a.unit and set(a.unit) <= ids):
        raise _bad(f"{a.task} is not a component: a fix dispatch needs --repo (a repo id of workflow.json), --base "
                   "(the starting branch) and --unit (the component ids a report may name; repeatable)")
    return a.repo, a.base, list(a.unit)


def cmd_run(a) -> int:
    product = a.product
    policy = workflow_config.load_policy(workflow_config.policy_path(product))
    disp = policy["dispatch"]
    for k in ("implementer", "report"):
        if k not in disp:
            raise _bad(f"dispatch.{k} is missing in workflow.json: a dispatch needs dispatch.implementer and dispatch.report")
    imp, rcfg = disp["implementer"], disp["report"]
    if not TASK_RE.match(a.task):
        raise _bad(f"task name \"{a.task}\": use letters, digits, . _ - (starting with a letter or digit)")
    repo_id, base_ref, units = _plan(a, policy)
    extra = extra_args(imp.get("extra_allowed", {}), imp["argv"], a.extra)
    bad = [g for g in a.allow if _bad_glob(g)] + [g for g in a.frozen if _bad_glob(_frozen_form(g))]
    if not a.allow or bad:
        raise _bad("--allow gives the paths the implementer may change (repo-relative, / separated globs such as "
                   "src/unit/**)" + (f"; not acceptable: {' '.join(bad)}" if bad else ""))
    prompt = Path(a.prompt_file)
    if not prompt.is_file():
        raise _bad(f"prompt file not found: {prompt}")
    role = (policy.get("roles") or {}).get("implementer") or {}
    for ph in ("model", "effort"):
        if ph in _used(imp["argv"]) and not role.get(ph):
            raise _bad(f"dispatch.implementer.argv uses {{{ph}}} but roles.implementer has no {ph} in workflow.json")
    code = gate(product, a.installed)
    if code:
        print(f"dispatch gate did not pass (exit code {code}); nothing was dispatched or created "
              f"(details: python workflow_config.py dispatch-check / doctor)")
        return code
    timeout = a.timeout or disp["timeout_seconds"]
    lf = workflow_config.local_file(product)
    mapping = workflow_config.mapping_with_home(policy, workflow_config.load_local(lf, policy), product)
    repo = mapping.get(repo_id) or workflow_config.require_repo_path(repo_id, lf)
    program = a.tool or imp["argv"][0]
    tool = shutil.which(program)
    if not tool:
        print(f"cannot find the implementer program {program}")
        return 5
    base_sha = _git_out(repo, "rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}")
    if not base_sha:
        print(f"{repo_id} ({repo}) has no {base_ref}: make the preparation commit or name the start with --base")
        return 4
    watch = start_ref(repo, base_ref)
    frozen = frozen_globs(repo, base_sha, a.frozen)
    schema_file = product / rcfg["schema"]
    try:
        schema_bytes = schema_file.read_bytes()
    except OSError as e:
        raise _bad(f"report schema {schema_file} cannot be read: {e}", 4)
    try:
        schema = json.loads(schema_bytes.decode("utf-8"))
        if not isinstance(schema, dict):
            raise ValueError("the top level must be an object")
    except (UnicodeDecodeError, ValueError) as e:
        raise _bad(f"report schema {schema_file} is not a JSON object: {e}")
    unsupported = schema_problems(schema, rcfg["task_field"])
    if unsupported:
        raise _bad(f"report schema {schema_file} uses what the judge does not check, so it is refused rather than "
                   "silently ignored: " + "; ".join(unsupported))
    schema_sha = hashlib.sha256(schema_bytes).hexdigest()
    wt_root = repo.parent / "wt"
    values = {"model": role.get("model"), "effort": role.get("effort")}
    runtime = state_io.runtime_root(product)
    run_id = process_runner.new_run_id(a.task)
    wt, run_dir = wt_root / run_id, state_io.runs_root(runtime) / run_id  # paths only: nothing is created yet
    argv = [tool, *fill_template(imp["argv"][1:], {**values, "worktree": wt, "run_dir": run_dir,
                                                     "report": run_dir / "report.json",
                                                     "schema": run_dir / "result.schema.json"}), *extra]
    unsafe = cmd_reparse_problem(argv)
    if unsafe:
        raise _bad(unsafe)
    if a.dry_run:
        dry = [tool, *fill_template(imp["argv"][1:], {**values, "worktree": "<worktree>", "run_dir": "<run dir>",
                                                        "report": "<run dir>/report.json", "schema": "<run dir>/result.schema.json"}), *extra]
        print("DRYRUN " + " ".join(dry) + f"  <prompt of {len(prompt.read_text(encoding='utf-8'))} characters on stdin>")
        print(f"       worktree {wt_root / '<run id>'} (branch run/<run id>, start {base_ref} {base_sha[:12]}), "
              f"timeout {timeout} s, allow {' '.join(a.allow)}, frozen {' '.join(frozen) or 'none'}")
        return 0
    claim = state_io.acquire_claim(runtime, "resource", f"dispatch:{a.task}", run_id=run_id, worktree=wt,
                                   resources=workflow_config.policy_resources(policy))
    try:
        r = subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", f"run/{run_id}", str(wt), base_sha],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            print(f"cannot open worktree {wt}: {r.stderr.strip()}")
            return 3
        run_dir = process_runner.create_run_dir(state_io.runs_root(runtime), run_id)
        shutil.copyfile(prompt, run_dir / "prompt.md")
        (run_dir / "result.schema.json").write_bytes(schema_bytes)  # a copy for the tool to read; the judge never trusts it
        process_runner.write_once(run_dir / "manifest.json", {
            "schema_version": 1, "run_id": run_id, "kind": "dispatch", "task_id": a.task, "created_at": _now(),
            "machine": state_io.MACHINE, "report_units": units,
            "source": {"repo_id": repo_id, "repo_path": str(repo), "base_ref": base_ref, "base_sha": base_sha},
            "worktree": {"path": str(wt), "branch": f"run/{run_id}", "isolation": ISOLATION},
            "limits": LIMITS,
            "scope": {"allowed": a.allow, "frozen": frozen}, "cwd": str(wt),
            "tool": {"name": imp["argv"][0], "path": tool, "model": role.get("model"), "effort": role.get("effort"),
                     "extra_args": extra},
            "argv": argv, "timeout_seconds": timeout,
            "inputs": {"prompt": {"path": "prompt.md", "sha256": state_io.file_revision(run_dir / "prompt.md"),
                                  "from": str(prompt)},
                       "schema": {"path": "result.schema.json", "from": str(schema_file), "sha256": schema_sha}},
            "outputs": {"stdout": process_runner.STDOUT, "stderr": process_runner.STDERR, "report": "report.json",
                        "process": process_runner.PROCESS, "checkpoint": process_runner.CHECKPOINT,
                        "result": "result.json", "temp": process_runner.TMP_DIR}})
        print(f"dispatch {a.task}: run {run_id}\n  worktree {wt} (branch run/{run_id}, start {base_ref} {base_sha[:12]})\n"
              f"  run directory {run_dir} (timeout {timeout} s; cancel: python dispatch_run.py --product {product} "
              f"cancel {run_id})", flush=True)
        res = process_runner.run_process(argv, wt, timeout, run_dir, stdin_path=run_dir / "prompt.md",
                                         env=process_runner.child_env(run_dir),
                                         register=lambda pid: claim.add_process(pid, group=True))
        try:
            result = judge_dispatch(res, run_dir, wt, repo, watch, base_sha, a.task, units, a.allow, frozen, rcfg,
                                    schema)
        except Exception as e:  # noqa: BLE001 - a judge failure is a run without evidence, never one without result.json
            result = unjudged(res, run_dir, wt, a.task, e)
        process_runner.write_once(run_dir / "result.json", result)
        print_result(result, run_dir)
        _stop_note(res, run_dir)
        return result["exit_code"]
    finally:
        claim.release()


def cmd_cancel(a) -> int:
    if Path(a.run_id).name != a.run_id or a.run_id in (".", ".."):
        raise _bad(f"bad run id: {a.run_id}")
    runs = state_io.runs_root(state_io.runtime_root(a.product))
    d = runs / a.run_id
    if not d.is_dir():
        print(f"no run {a.run_id} in {runs}")
        return 4
    process_runner.request_cancel(d)
    print(f"asked run {a.run_id} to stop: the runner kills the whole process tree and writes {d / process_runner.CHECKPOINT}")
    return 0


def _positive(s: str) -> int:
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--product", type=Path, default=Path.cwd(),
                    help="the product folder (holds specflow.md); default: the current directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="run only the pre-dispatch gate")
    p.add_argument("--installed", type=Path, help="installed_plugins.json to check the lock against (default: the user's)")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("run", help="gate, then the implementer in a new worktree; the verdict comes from evidence")
    p.add_argument("task", help="a component id, or a fix dispatch name (with --repo, --base, --unit)")
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--allow", nargs="+", action="extend", default=[], metavar="GLOB", help="paths it may change (required)")
    p.add_argument("--frozen", nargs="+", action="extend", default=[], metavar="GLOB", help="paths it must not touch")
    p.add_argument("--base", help="the worktree's start (a component defaults to comp/<name>; a fix dispatch needs it)")
    p.add_argument("--repo", help="fix dispatch: repo id of workflow.json")
    p.add_argument("--unit", nargs="+", action="extend", metavar="ID", help="fix dispatch: component ids a report may name")
    p.add_argument("--timeout", type=_positive, help="seconds (default: dispatch.timeout_seconds)")
    p.add_argument("--dry-run", action="store_true", help="check and print the command; create nothing")
    p.add_argument("--tool", help="program to run instead of argv[0] of the template")
    p.add_argument("--installed", type=Path, help="installed_plugins.json for the gate (default: the user's)")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("cancel", help="ask a running run to stop")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_cancel)
    for sp in sub.choices.values():  # --product must also work after the subcommand
        sp.add_argument("--product", type=Path, default=argparse.SUPPRESS)
    args = sys.argv[1:] if argv is None else list(argv)
    extra = []
    if "--" in args:  # everything after -- is for the implementer tool, checked against extra_allowed
        i = args.index("--")
        args, extra = args[:i], args[i + 1:]
    try:
        a = ap.parse_args(args)
    except SystemExit as e:
        return 2 if e.code else 0
    if extra and a.cmd != "run":
        print("error: arguments after -- are only for run", file=sys.stderr)
        return 2
    a.extra, a.product = extra, Path(a.product)
    try:
        return a.func(a)
    except (ConfigError, state_io.StateError, process_runner.RunnerError) as e:
        print(f"error: {e}", file=sys.stderr)
        return e.code
    except subprocess.TimeoutExpired as e:
        print(f"timeout: {e}", file=sys.stderr)
        return 6
    except Exception as e:  # noqa: BLE001
        print(f"unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
