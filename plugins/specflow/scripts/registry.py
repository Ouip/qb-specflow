#!/usr/bin/env python3
"""registry: the only way to change components.json (component status, blocked, deps, evidence). Stdlib only.

components.json and workflow.json sit in the product folder (the spec folder that holds specflow.md); pass it with
--product (default: the current directory), before or after the subcommand.

  python registry.py show                                   components, what can be worked on next, the revision
  python registry.py check                                  structure, rules, referenced evidence; legacy warnings
  python registry.py set <id> [--status S] [--block REASON | --unblock] [--deps [ID ...]]
                              [--evidence KIND=PATH ...] [--reason TEXT] [--expect REVISION]
  python registry.py add <id> --name N --spec FILE --repo R [--deps ID ...] [--expect REVISION]
  python registry.py repo <repo id> [--integration-branch B] [--baseline CMD ...] [--expect REVISION]
  python registry.py claims                                 claims on this machine and whether their owners still run

Status rules are policy, in workflow.json (both optional):
  "transitions": [{"from": S1, "to": S2, "needs": [KIND, ...], "repos": [REPO, ...], "rework": true}, ...]
  "evidence_kinds": {KIND: {"human": true}}
Without transitions any status in states.order is accepted and set says so. With them a status change needs the one
rule for (current status, target, the component's repo; a rule listing the repo wins over one without repos), every
kind in its needs given with --evidence in the same call (an existing file; its sha256 is recorded), and --reason for a
rework rule; the row's evidence becomes exactly the refs given, and set reports forward or rework. The rule is decided
under the registry lock on the row as it is then. --expect turns on CAS; without it the change applies to the current
file. A human kind is never written by the core (record_evidence refuses it); it can only be referenced.

Exit codes: 0 pass, 1 refused (no rule, rework without reason, bad change), 2 input / format (incl. rules naming a
status not in states.order), 3 conflict (stale --expect revision, claim held), 4 missing (evidence or a needed kind,
workflow.json, components.json), 6 lock wait timed out.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REGISTRY_NAME = "components.json"
NO_RULES = "no transition rules in workflow.json: any status in states.order is accepted"


def _load_sibling(name: str, module_name: str):
    """Load a sibling script by file path under a unique module name (the scripts folder is not a package, and a product
    adapter may itself be importable under the plain name; the core must never bind to that)."""
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).resolve().parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod  # registered before exec: dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


state_io = _load_sibling("state_io", "specflow_state_io")
workflow_config = _load_sibling("workflow_config", "specflow_workflow_config")
KEEP = state_io.KEEP


# ---------- reading ----------

def registry_path(product: Path) -> Path:
    return Path(product) / REGISTRY_NAME


def load_registry(product: Path) -> tuple[dict, str]:
    """(data, revision) from one read of components.json; revision is what --expect takes."""
    path = registry_path(product)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise state_io.StateError(f"not found: {path}", 4)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise state_io.StateError(f"{path} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise state_io.StateError(f"{path}: the top level must be an object")
    return data, hashlib.sha256(raw).hexdigest()  # the revision of exactly the bytes parsed


def load_policy(product: Path) -> dict:
    """workflow.json of the product, validated. Missing -> ConfigError 4: without the policy nothing is decided."""
    return workflow_config.load_policy(workflow_config.policy_path(product))


def transitions(policy: dict) -> list | None:
    """The status-change rules, or None when the product has none (any status in states.order is accepted)."""
    return policy.get("transitions")


def human_kinds(policy: dict) -> set[str]:
    return {k for k, v in (policy.get("evidence_kinds") or {}).items() if v.get("human")}


def evidence_root(product: Path) -> Path:
    """Evidence paths are relative to the home repo root (the git top level of the product folder)."""
    return workflow_config.home_root(product)


# ---------- evidence ----------

def evidence_ref(product: Path, kind: str, relpath: str) -> dict:
    """Reference an evidence file that already exists: {"kind", "path", "sha256"}. Nothing is written; a missing file
    raises EvidenceMissing (4). This is the only way a human kind gets into components.json."""
    return state_io.reference_evidence(evidence_root(product), relpath, kind)


def record_evidence(product: Path, kind: str, relpath: str, data: bytes, policy: dict | None = None) -> dict:
    """Write new immutable evidence of `kind` (state_io.write_evidence) and return its reference. Refused (1) for a human
    kind (evidence_kinds.<kind>.human): no core command writes the content of a human record. The core cannot prove who
    wrote a file that it only references; keeping a model from writing one is a rule of the skills, not of this code."""
    policy = policy if policy is not None else load_policy(product)
    if kind in human_kinds(policy):
        raise state_io.ChangeRejected(f"{kind} is a human evidence kind: a person writes that file; the core only "
                                      "references it (evidence_ref / set --evidence) and never writes it")
    ref = state_io.write_evidence(evidence_root(product), relpath, data)
    return {"kind": kind, **ref}


# ---------- writing ----------

def set_component(product: Path, cid: str, *, status: str | None = None, blocked=KEEP, deps=None, evidence=None,
                  reason: str = "", expect: str | None = None, policy: dict | None = None,
                  runtime: Path | None = None) -> dict:
    """Change one component through state_io.update_registry under the product's transition rules (decided under the
    lock on the current row). expect=None: no revision check, applied to the current file; a revision string turns CAS on
    (RevisionConflict if the file changed). evidence: refs from evidence_ref / record_evidence. blocked: KEEP (this
    module's), None = unblock, or a reason. Returns update_registry's result, incl. "status_before" and "direction"."""
    product = Path(product)
    policy = policy if policy is not None else load_policy(product)
    change = state_io.RegistryChange(cid, target_status=status, blocked=blocked, reason=reason,
                                     deps=None if deps is None else tuple(deps),
                                     evidence_refs=None if evidence is None else tuple(evidence))
    return state_io.update_registry(registry_path(product), expect, change, transitions=transitions(policy),
                                    runtime=runtime, root=evidence_root(product))


# ---------- checks ----------

def problems(data: dict, product: Path) -> list[str]:
    """Structure problems of components.json; an empty list means none."""
    out = []
    st = data.get("states") if isinstance(data.get("states"), dict) else {}
    order = st.get("order") if isinstance(st.get("order"), list) else []
    if not order:
        out.append("states.order must be a non-empty array of status names")
    for key in ("workable", "dep_ok"):
        if not isinstance(st.get(key), list) or not st[key] or not set(st[key]) <= set(order):
            out.append(f"states.{key} must be a non-empty array of values from states.order")
    comps = data.get("components")
    if not isinstance(comps, list):
        return out + ["components must be an array"]
    ids = [c.get("id") if isinstance(c, dict) else None for c in comps]
    for dup in sorted({i for i in ids if i is not None and ids.count(i) > 1}):
        out.append(f"duplicate id: {dup}")
    repos = data.get("repos") if isinstance(data.get("repos"), dict) else {}
    good = []
    for c in comps:
        if not isinstance(c, dict):
            out.append(f"a component must be an object: {c!r}")
            continue
        cid = c.get("id", "?")
        miss = [k for k in ("id", "name", "spec", "repo", "deps", "status") if k not in c]
        if miss:
            out.append(f"{cid}: missing {', '.join(miss)}")
            continue
        good.append(c)
        if c["status"] not in order:
            out.append(f"{cid}: status {c['status']!r} is not in states.order")
        if c["repo"] not in repos:
            out.append(f"{cid}: repo {c['repo']!r} is not in repos")
        for d in c["deps"]:
            if d not in ids or d == cid:
                out.append(f"{cid}: dependency {d!r} is not another component")
        if not (isinstance(c["spec"], str) and (Path(product) / c["spec"]).is_file()):
            out.append(f"{cid}: spec file {c['spec']} not found next to {REGISTRY_NAME}")
        b = c.get("blocked")
        if b is not None and not (isinstance(b, str) and b.strip()):
            out.append(f"{cid}: blocked must be a reason (who must do what) or null")
    loop = state_io.find_cycle(good)
    if loop:
        out.append("dependency cycle: " + " -> ".join(loop))
    return out


def evidence_failures(data: dict, root: Path) -> list[str]:
    """Referenced evidence that no longer resolves or whose sha256 differs."""
    out = []
    for c in data.get("components") or []:
        for ref in (c.get("evidence") or []) if isinstance(c, dict) else []:
            err = state_io.check_evidence(root, ref)
            if err:
                out.append(f"{c.get('id')}: referenced evidence fails: {err}")
    return out


def rule_problems(data: dict, rules) -> list[str]:
    """Every status a rule names must be in states.order of components.json (else set refuses every status change)."""
    order = ((data.get("states") or {}).get("order") or []) if isinstance(data.get("states"), dict) else []
    unknown = sorted({r[k] for r in rules or () for k in ("from", "to")} - set(order))
    return [f"transitions in workflow.json name statuses not in states.order: {', '.join(unknown)}"] if unknown else []


def legacy_warnings(data: dict, rules) -> list[str]:
    """Components whose evidence does not satisfy any rule into their current status (e.g. set before the rules
    existed). Reported, never a failure: the status stays, the next change goes through the rules."""
    if not rules:
        return []
    out = []
    for c in data.get("components") or []:
        if not (isinstance(c, dict) and "status" in c):
            continue
        cur, repo = c["status"], c.get("repo")
        into = [x for x in (state_io.find_transition(rules, f, cur, repo)
                            for f in dict.fromkeys(r["from"] for r in rules if r["to"] == cur)) if x]
        have = {e.get("kind") for e in c.get("evidence") or [] if isinstance(e, dict)}
        if into and not any(set(x.get("needs") or ()) <= have for x in into):
            need = "; ".join(f"from {x['from']}: {', '.join(k for k in x['needs'] if k not in have)}" for x in into)
            out.append(f"legacy: {c.get('id')} is {cur!r} without the evidence a rule into it needs ({need})")
    return out


def candidates(data: dict) -> list[dict]:
    """Components that can be worked on: status in workable, not blocked, every dependency in dep_ok. Further-along
    workable statuses first; equal statuses keep list order."""
    st = data["states"]
    by_id = {c["id"]: c for c in data["components"]}
    work, ok = st["workable"], set(st["dep_ok"])
    out = [c for c in data["components"]
           if c["status"] in work and not c.get("blocked") and all(by_id[d]["status"] in ok for d in c["deps"])]
    return sorted(out, key=lambda c: -work.index(c["status"]))


def summary(data: dict) -> str:
    comps = data["components"]
    n = Counter(c["status"] for c in comps)
    counts = ", ".join(f"{s} {n[s]}" for s in data["states"]["order"] if n[s])
    cands = candidates(data)
    blocked = [c["id"] for c in comps if c.get("blocked")]
    line = f"{len(comps)} components ({counts}); next: {cands[0]['id'] if cands else 'none'}"
    if blocked:
        line += f"; blocked {len(blocked)}: {' '.join(blocked)}"
    return line


# ---------- CLI ----------

def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def cmd_show(a) -> int:
    data, rev = load_registry(a.product)
    bad = problems(data, a.product)
    if bad:
        for p in bad:
            print(f"  [fail] {p}")
        print(f"fix {REGISTRY_NAME} first (registry.py check)")
        return 1
    by_id = {c["id"]: c for c in data["components"]}
    ok = set(data["states"]["dep_ok"])
    w = max((len(c["name"]) for c in data["components"]), default=0)
    for c in data["components"]:
        line = f"{c['id']}  {c['name']:<{w}}  {c['status']}"
        wait = [d for d in c["deps"] if by_id[d]["status"] not in ok]
        if c["status"] in data["states"]["workable"] and wait:
            line += f"  waits for: {' '.join(wait)}"
        if c.get("blocked"):
            line += f"  blocked: {c['blocked']}"
        print(line)
    print(summary(data))
    print(f"revision: {rev}")
    return 0


def cmd_check(a) -> int:
    policy = load_policy(a.product)
    rules = transitions(policy)
    data, _rev = load_registry(a.product)
    fails = problems(data, a.product) + rule_problems(data, rules)
    fails += evidence_failures(data, evidence_root(a.product))
    for m in fails:
        print(f"  [fail] {m}")
    if not fails:
        print(f"  [ok] {REGISTRY_NAME} ({len(data['components'])} components)")
    for m in legacy_warnings(data, rules):
        print(f"  [warning] {m}")
    if rules is None:
        print(f"  [note] {NO_RULES}")
    print(f"check: {len(fails)} failed" if fails else "check: ok")
    return 1 if fails else 0


def _parse_evidence(product: Path, items) -> list[dict] | None:
    if items is None:
        return None
    refs = []
    for item in items:
        kind, sep, path = item.partition("=")
        if not (sep and kind.strip() and path.strip()):
            raise state_io.StateError(f"--evidence takes KIND=PATH (path relative to the home repo root): {item!r}", 2)
        refs.append(evidence_ref(product, kind.strip(), path.strip()))
    return refs


def cmd_set(a) -> int:
    policy = load_policy(a.product)
    blocked = a.block if a.block is not None else (None if a.unblock else KEEP)
    res = set_component(a.product, a.id, status=a.status, blocked=blocked, deps=a.deps,
                        evidence=_parse_evidence(a.product, a.evidence), reason=a.reason, expect=a.expect,
                        policy=policy)
    c = res["component"]
    moved = f"{res['status_before']} -> {c['status']} ({res['direction']})" if res["direction"] else c["status"]
    print(f"{c['id']}: {moved}" + (f"; blocked: {c['blocked']}" if c.get("blocked") else "")
          + (f"; deps: {' '.join(c['deps']) or 'none'}" if a.deps is not None else ""))
    if a.status is not None and transitions(policy) is None:
        print(f"note: {NO_RULES}")
    print(f"revision: {res['revision']}")
    return 0


def cmd_add(a) -> int:
    res = state_io.update_registry(registry_path(a.product), a.expect, state_io.RegistryChange(
        a.id, deps=tuple(a.deps), new={"name": a.name, "spec": a.spec, "repo": a.repo}), root=evidence_root(a.product))
    c = res["component"]
    print(f"added {c['id']} ({c['name']}, {c['spec']}, {c['repo']}): {c['status']}; deps: {' '.join(c['deps']) or 'none'}")
    print(f"revision: {res['revision']}")
    return 0


def cmd_repo(a) -> int:
    res = state_io.update_repo(registry_path(a.product), a.expect, a.repo_id, integration_branch=a.integration_branch,
                               baseline=a.baseline)
    r = res["repo"]
    print(f"{a.repo_id}: integration branch {r.get('integration_branch')}; {len(r.get('baseline') or [])} baseline commands")
    print(f"revision: {res['revision']}")
    return 0


def cmd_claims(a) -> int:
    rows = state_io.list_claims(state_io.runtime_root(a.product))
    if not rows:
        print("no claims")
    for m in rows:
        if m.get("broken"):
            print(f"{m['broken']}: record unreadable; make sure no process uses it, then delete it")
            continue
        alive = {True: "owner tree running", False: "owner stopped, can be taken over", None: "cannot tell"}[m["alive"]]
        print(f"{m.get('kind')} {m.get('name')}: {m.get('machine')} pid {m.get('pid')} ({alive}), "
              f"worktree {m.get('worktree')}, run {m.get('run_id')}, since {m.get('acquired_at')}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--product", type=Path, default=Path.cwd(),
                    help="the product folder (holds specflow.md); default: the current directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").set_defaults(func=cmd_show)
    sub.add_parser("check").set_defaults(func=cmd_check)
    p = sub.add_parser("set")
    p.add_argument("id")
    p.add_argument("--status")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--block", metavar="REASON")
    g.add_argument("--unblock", action="store_true")
    p.add_argument("--deps", nargs="*", metavar="ID", help="replaces the dependency list; no ID = no dependencies")
    p.add_argument("--evidence", action="append", metavar="KIND=PATH",
                   help="an existing evidence file, relative to the home repo root; repeatable")
    p.add_argument("--reason", default="")
    p.add_argument("--expect", metavar="REVISION", help="refuse (exit 3) unless components.json is still this revision")
    p.set_defaults(func=cmd_set)
    p = sub.add_parser("add")
    p.add_argument("id")
    p.add_argument("--name", required=True)
    p.add_argument("--spec", required=True, help="spec file name in the product folder")
    p.add_argument("--repo", required=True, help="repo id in the repos of components.json")
    p.add_argument("--deps", nargs="*", default=[], metavar="ID")
    p.add_argument("--expect", metavar="REVISION")
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("repo")
    p.add_argument("repo_id")
    p.add_argument("--integration-branch")
    p.add_argument("--baseline", action="append", metavar="CMD", help="repeatable; replaces the whole list")
    p.add_argument("--expect", metavar="REVISION")
    p.set_defaults(func=cmd_repo)
    sub.add_parser("claims").set_defaults(func=cmd_claims)
    for sp in sub.choices.values():  # --product must also work after the subcommand
        sp.add_argument("--product", type=Path, default=argparse.SUPPRESS)
    try:
        a = ap.parse_args(argv)
        if a.cmd == "set" and not (a.status or a.block is not None or a.unblock or a.deps is not None or a.evidence):
            ap.error("set needs at least one of --status, --block, --unblock, --deps, --evidence")
        if a.cmd == "repo" and a.integration_branch is None and a.baseline is None:
            ap.error("repo needs --integration-branch or --baseline")
    except SystemExit as e:
        return 2 if e.code else 0
    a.product = Path(a.product)
    try:
        return a.func(a)
    except state_io.RevisionConflict as e:
        _err(f"{e}\ncurrent revision: {e.current}")
        return e.code
    except state_io.StateError as e:
        _err(str(e))
        return e.code
    except workflow_config.ConfigError as e:
        _err(str(e))
        return e.code
    except subprocess.TimeoutExpired as e:
        _err(f"timeout: {e}")
        return 6
    except Exception as e:  # noqa: BLE001
        _err(f"unexpected error: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
