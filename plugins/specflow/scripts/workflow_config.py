#!/usr/bin/env python3
"""workflow_config: product execution policy (workflow.json), plugin lock (workflow.lock.json), the per-machine path
mapping (specflow.local.json) and doctor. Stdlib only.

All files sit in the product folder (the spec folder that holds specflow.md); pass it with --product (default: the
current directory). The per-machine mapping is read from the home repo root (the repo marked "home": true in
workflow.json; its default path is the git top level of the product folder) and must not be committed.

  python workflow_config.py validate [--product DIR]            schema of policy and lock
  python workflow_config.py lock-check [--installed FILE] [--json]
  python workflow_config.py lock-write [--installed FILE] [--plugin-json FILE]   write workflow.lock.json
  python workflow_config.py dispatch-check [--installed FILE]   pre-dispatch gate: lock mismatch 3, capability missing 5
  python workflow_config.py doctor [--for CAP...] [--json]      declared vs measured capabilities
  python workflow_config.py settings                            effective settings sources (SessionStart commands only)
  python workflow_config.py regen [--check]                     generate specflow.local.example.json from the policy
  python workflow_config.py repo-path REPO_ID                   this machine's local path of a repo

Exit codes: 0 pass, 2 input / schema, 3 version or dirty conflict (lock mismatch, generated file differs), 4 missing file,
5 capability missing, 6 timeout, 1 unexpected error.
Component status / dependencies / blocked live only in components.json; they are never stored here. The rules for
changing a status (optional `transitions`, `evidence_kinds`) are policy and live here; registry.py applies them.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
POLICY_NAME = "workflow.json"
LOCK_NAME = "workflow.lock.json"
SPECFLOW_MD = "specflow.md"
LOCAL_NAME = "specflow.local.json"
EXAMPLE_NAME = "specflow.local.example.json"
SCHEMA_VERSION = 1
FORBIDDEN_STATE_KEYS = ("components", "status", "blocked", "deps", "states")  # only components.json has these


class ConfigError(Exception):
    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


# ---------- reading files and schema ----------

def _load(path: Path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"not found: {path}", 4)
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}", 4)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}", 2)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the top level must be an object", 2)
    return data


def _is_str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x for x in v)


def home_id(policy: dict) -> str:
    """The id of the home repo: the one repo with "home": true (validate_policy guarantees exactly one)."""
    homes = [rid for rid, r in policy["repos"].items() if isinstance(r, dict) and r.get("home") is True]
    if len(homes) != 1:
        raise ConfigError("repos must have exactly one entry with \"home\": true", 2)
    return homes[0]


def remote_alias(policy: dict, repo_id: str) -> str:
    """The git remote name of a repo; "origin" when the policy does not set remote_alias."""
    return policy["repos"][repo_id].get("remote_alias") or "origin"


def policy_resources(policy: dict) -> tuple[str, ...]:
    """Extra named-resource kinds this product adds on top of the built-in ones (state_io.BUILTIN_RESOURCES)."""
    return tuple(policy.get("resources") or ())


RULE_KEYS = ("from", "to", "needs", "repos", "rework")


def _is_kind(k) -> bool:
    """An evidence kind name: non-empty, no whitespace, no '=' (the CLI writes evidence as KIND=PATH)."""
    return isinstance(k, str) and bool(re.fullmatch(r"[^=\s]+", k))


def _transition_problems(rules, repos: dict) -> list[str]:
    """`transitions`: status-change rules (policy, not state). Each rule {"from", "to", "needs"?: [kind...],
    "repos"?: [repo id...], "rework"?: bool}. For one from -> to step a component gets at most one rule: the one listing
    its repo, else the one without repos. Whether the state names exist is checked against components.json (registry.py)."""
    if not isinstance(rules, list) or not rules:
        return ["transitions must be a non-empty array of rules (leave it out for no rules)"]
    e: list[str] = []
    scopes: dict[tuple, list] = {}  # (from, to) -> repo scopes seen; None = every repo
    for i, r in enumerate(rules):
        at = f"transitions[{i}]"
        if not isinstance(r, dict):
            e.append(f"{at} must be an object")
            continue
        extra = sorted(set(r) - set(RULE_KEYS))
        if extra:
            e.append(f"{at}: unknown field {', '.join(extra)} (allowed: {', '.join(RULE_KEYS)})")
        ends = [r.get(k) for k in ("from", "to")]
        if not all(isinstance(s, str) and s.strip() for s in ends):
            e.append(f"{at}: from and to must be non-empty status names")
            continue
        if ends[0] == ends[1]:
            e.append(f"{at}: from and to are the same status")
        needs = r.get("needs", [])
        if not (isinstance(needs, list) and all(_is_kind(k) for k in needs)) or len(set(needs)) != len(needs):
            e.append(f"{at}.needs must be an array of distinct evidence kinds (no spaces, no '=')")
        scope = None
        if "repos" in r:
            rr = r["repos"]
            if not _is_str_list(rr) or not rr or len(set(rr)) != len(rr):
                e.append(f"{at}.repos must be a non-empty array of distinct repo ids")
            else:
                e += [f"{at}.repos: \"{x}\" is not in repos" for x in rr if x not in repos]
                scope = set(rr)
        if "rework" in r and not isinstance(r["rework"], bool):
            e.append(f"{at}.rework must be true or false")
        for prev in scopes.setdefault(tuple(ends), []):
            if (prev is None and scope is None) or (prev and scope and prev & scope):
                e.append(f"{at}: a second rule for {ends[0]} -> {ends[1]} with the same repos: "
                         "a component must get exactly one rule for a step")
                break
        scopes[tuple(ends)].append(scope)
    return e


def _evidence_kind_problems(kinds) -> list[str]:
    """`evidence_kinds`: {kind: {"human": bool}}. A human kind is written by a person; no core command writes it."""
    if not isinstance(kinds, dict):
        return ["evidence_kinds must be an object {kind: {\"human\": true|false}}"]
    e: list[str] = []
    for k, v in kinds.items():
        if not _is_kind(k):
            e.append(f"evidence_kinds: \"{k}\" is not a kind name (no spaces, no '=')")
        elif not isinstance(v, dict) or set(v) - {"human"} or not isinstance(v.get("human", False), bool):
            e.append(f"evidence_kinds.{k} must be an object with only \"human\": true|false")
    return e


def validate_policy(d: dict) -> list[str]:
    """Return every problem (does not stop at the first); an empty list means valid."""
    e: list[str] = []
    if d.get("schema_version") != SCHEMA_VERSION:
        e.append(f"schema_version must be {SCHEMA_VERSION}")
    for k in FORBIDDEN_STATE_KEYS:
        if k in d:
            e.append(f"top-level field \"{k}\" is not allowed: component state lives only in components.json")
    # Required: repos, tools, capabilities, dispatch.timeout_seconds. Optional but validated when present: entry,
    # test_profiles, roles, resources, repos.*.remote_alias (default "origin"), dispatch.session_timeout_seconds,
    # transitions, evidence_kinds.
    for k in ("repos", "tools", "capabilities", "dispatch"):
        if not isinstance(d.get(k), dict) or not d.get(k):
            e.append(f"{k} is missing (must be a non-empty object)")
    for k in ("entry", "test_profiles", "roles"):
        if k in d and not isinstance(d[k], dict):
            e.append(f"{k} must be an object")
    repos, tools = d.get("repos") or {}, d.get("tools") or {}
    caps = d.get("capabilities") or {}
    roles = d.get("roles") if isinstance(d.get("roles"), dict) else {}
    for rid, r in repos.items():
        if not isinstance(r, dict) or not isinstance(r.get("integration_branch"), str) or not r.get("integration_branch"):
            e.append(f"repos.{rid}.integration_branch must be a non-empty string")
        if isinstance(r, dict) and "remote_alias" in r and not (isinstance(r["remote_alias"], str) and r["remote_alias"]):
            e.append(f"repos.{rid}.remote_alias must be a non-empty string")
    homes = [rid for rid, r in repos.items() if isinstance(r, dict) and r.get("home") is True]
    if repos and len(homes) != 1:
        e.append(f"repos must have exactly one entry with \"home\": true (found {len(homes)})")
    res = d.get("resources", [])
    if not _is_str_list(res) or any(":" in x for x in res):
        e.append("resources must be an array of non-empty strings without ':' (extra named-resource kinds)")
    for tn, t in tools.items():
        if not isinstance(t, dict) or not _is_str_list(t.get("version_args", [])):
            e.append(f"tools.{tn}.version_args must be an array of strings")
    for cn, c in caps.items():
        if not isinstance(c, dict):
            e.append(f"capabilities.{cn} must be an object")
            continue
        if not _is_str_list(c.get("tools")):
            e.append(f"capabilities.{cn}.tools must be an array of strings")
        else:
            e += [f"capabilities.{cn}: tool \"{t}\" is not in tools" for t in c["tools"] if t not in tools]
        if not _is_str_list(c.get("repos")):
            e.append(f"capabilities.{cn}.repos must be an array of strings")
        else:
            e += [f"capabilities.{cn}: repo \"{r}\" is not in repos" for r in c["repos"] if r not in repos]
    profiles = d.get("test_profiles") if isinstance(d.get("test_profiles"), dict) else {}
    for pn, p in profiles.items():
        if not isinstance(p, dict):
            e.append(f"test_profiles.{pn} must be an object")
            continue
        if p.get("repo") not in repos:
            e.append(f"test_profiles.{pn}: repo \"{p.get('repo')}\" is not in repos")
        if p.get("capability") not in caps:
            e.append(f"test_profiles.{pn}: capability \"{p.get('capability')}\" is not in capabilities")
        if not _is_str_list(p.get("argv")) or not p.get("argv"):
            e.append(f"test_profiles.{pn}.argv must be a non-empty array of strings")
        if not isinstance(p.get("timeout_seconds"), int) or p["timeout_seconds"] <= 0:
            e.append(f"test_profiles.{pn}.timeout_seconds must be a positive integer")
    for rn, r in roles.items():
        if not isinstance(r, dict) or not r.get("tool") or not r.get("model"):
            e.append(f"roles.{rn} needs tool and model")
            continue
        if r.get("fallback_role") and r["fallback_role"] not in roles:
            e.append(f"roles.{rn}.fallback_role \"{r['fallback_role']}\" is not in roles")
    disp = d.get("dispatch") or {}
    if not isinstance(disp.get("timeout_seconds"), int) or disp.get("timeout_seconds", 0) <= 0:
        e.append("dispatch.timeout_seconds must be a positive integer")
    if "session_timeout_seconds" in disp:  # optional: per-session timeout of a session loop
        st = disp["session_timeout_seconds"]
        if not isinstance(st, int) or isinstance(st, bool) or st <= 0:
            e.append("dispatch.session_timeout_seconds must be a positive integer")
    for c in disp.get("requires_capabilities", []):
        if c not in caps:
            e.append(f"dispatch.requires_capabilities: \"{c}\" is not in capabilities")
    if "transitions" in d:
        e += _transition_problems(d["transitions"], repos if isinstance(repos, dict) else {})
    if "evidence_kinds" in d:
        e += _evidence_kind_problems(d["evidence_kinds"])
    return e


def validate_lock(d: dict) -> list[str]:
    e: list[str] = []
    if d.get("schema_version") != SCHEMA_VERSION:
        e.append(f"schema_version must be {SCHEMA_VERSION}")
    plugins = d.get("plugins")
    if not isinstance(plugins, dict) or not plugins:
        return e + ["plugins is missing (non-empty object)"]
    for k, p in plugins.items():
        if not isinstance(p, dict):
            e.append(f"plugins.{k} must be an object")
            continue
        if not isinstance(p.get("version"), str) or not p.get("version"):
            e.append(f"plugins.{k}.version must be a string")
        if not isinstance(p.get("commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", p.get("commit", "")):
            e.append(f"plugins.{k}.commit must be 40 hex digits")
        if not isinstance(p.get("required"), bool):
            e.append(f"plugins.{k}.required must be true/false")
        if not isinstance(p.get("source"), dict) or not p["source"].get("type"):
            e.append(f"plugins.{k}.source.type is required")
    core = d.get("core")
    if not isinstance(core, dict) or core.get("plugin") not in plugins:
        e.append("core.plugin must be one of plugins")
    else:
        for f in ("version", "commit"):
            if core.get(f) != plugins[core["plugin"]].get(f):
                e.append(f"core.{f} differs from plugins.{core['plugin']}.{f}")
    return e


def load_policy(path: Path) -> dict:
    d = _load(path)
    errs = validate_policy(d)
    if errs:
        raise ConfigError(f"{path} does not match the schema:\n  " + "\n  ".join(errs), 2)
    return d


def load_lock(path: Path) -> dict:
    d = _load(path)
    errs = validate_lock(d)
    if errs:
        raise ConfigError(f"{path} does not match the schema:\n  " + "\n  ".join(errs), 2)
    return d


# ---------- product folder, home repo, per-machine mapping ----------

def policy_path(product: Path) -> Path:
    return Path(product) / POLICY_NAME


def lock_path(product: Path) -> Path:
    return Path(product) / LOCK_NAME


def git_toplevel(path: Path) -> Path | None:
    """The git top level containing path, or None if path is not in a git work tree (or git is unavailable)."""
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def home_root(product: Path) -> Path:
    """Root of the home repo on this machine: the git top level of the product folder (the folder itself if not in git)."""
    product = Path(product).resolve()
    return git_toplevel(product) or product


def local_file(product: Path) -> Path:
    """`<home repo root>/specflow.local.json`: this machine's mapping; never committed."""
    return home_root(product) / LOCAL_NAME


@dataclass
class RepoResolution:
    repo_id: str
    path: Path | None
    exists: bool = False
    is_git: bool = False


def _abs(s: str) -> bool:
    return PureWindowsPath(s).is_absolute() or PurePosixPath(s).is_absolute()


def load_local(path: Path, policy: dict) -> dict[str, Path]:
    """specflow.local.json -> {repo_id: Path}. A missing file means no mapping (empty). Only repo ids in the policy are
    accepted and paths must be absolute. The optional top-level "machine" (a label for this machine) must be a string."""
    if not Path(path).exists():
        return {}
    d = _load(Path(path))
    if "machine" in d and not (isinstance(d["machine"], str) and d["machine"]):
        raise ConfigError(f"{path}: machine must be a non-empty string", 2)
    out: dict[str, Path] = {}
    for rid, r in (d.get("repos") or {}).items():
        if rid not in policy["repos"]:
            raise ConfigError(f"{path}: unknown repo id \"{rid}\" (known: {' '.join(policy['repos'])})", 2)
        p = r.get("path") if isinstance(r, dict) else None
        if not isinstance(p, str) or not p:
            continue  # only other fields were written (e.g. integration_branch): no path to handle here
        if not _abs(p):
            raise ConfigError(f"{path}: repos.{rid}.path must be an absolute path, not \"{p}\"", 2)
        out[rid] = Path(p)
    return out


def load_machine(path: Path) -> str | None:
    """The optional "machine" label in specflow.local.json (None if the file or the field is absent)."""
    try:
        m = json.loads(Path(path).read_text(encoding="utf-8")).get("machine")
    except (OSError, ValueError, AttributeError):
        return None
    return m if isinstance(m, str) and m else None


def resolve_repos(policy: dict, local: dict[str, Path]) -> dict[str, RepoResolution]:
    res = {}
    for rid in policy["repos"]:
        p = local.get(rid)
        res[rid] = RepoResolution(rid, p, bool(p and p.exists()), bool(p and (p / ".git").exists()))
    return res


def mapping_with_home(policy: dict, local: dict[str, Path], product: Path) -> dict[str, Path]:
    """The mapping plus the home repo, which defaults to the home repo root when it is not mapped explicitly."""
    out = dict(local)
    out.setdefault(home_id(policy), home_root(product))
    return out


def repo_path(repo_id: str, local_file: Path, fallback: Path | None = None) -> Path | None:
    """For other tools: this machine's repo path, read only from the local mapping file (no default paths).
    No mapping, a broken file or a non-absolute path returns fallback; never raises."""
    try:
        d = json.loads(Path(local_file).read_text(encoding="utf-8"))
        p = d["repos"][repo_id]["path"]
        if isinstance(p, str) and p and _abs(p):
            return Path(p)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return fallback


def require_repo_path(repo_id: str, local_file: Path) -> Path:
    """Like repo_path, but raises ConfigError(4) naming the repo id and the file to edit when there is no mapping."""
    lf = Path(local_file)
    p = repo_path(repo_id, lf)
    if p is None:
        raise ConfigError(f"repo \"{repo_id}\" has no mapping on this machine: write an absolute path at "
                          f"repos.{repo_id}.path in {lf} (template {EXAMPLE_NAME})", 4)
    return p


def local_file_ignored(root: Path) -> bool | None:
    """True/False: is specflow.local.json git-ignored in `root`. None when root is not a git work tree or git fails."""
    try:
        r = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", LOCAL_NAME], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {0: True, 1: False}.get(r.returncode)


# ---------- plugin lock ----------

@dataclass
class LockCheck:
    ok: bool = True
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "blocking": self.blocking, "warnings": self.warnings}


def _effective_entry(entries: list) -> dict | None:
    """A plugin may have several scopes: the user scope wins (it applies globally); otherwise the first entry."""
    ents = [x for x in entries if isinstance(x, dict)]
    return next((x for x in ents if x.get("scope") == "user"), ents[0] if ents else None)


def check_lock(lock: dict, installed: dict, specflow_md: Path | None = None) -> LockCheck:
    """Installed plugins against the lock: version and commit must both match. A mismatch of a required plugin blocks;
    an optional one only warns. specflow_md: the product file's `core:` version must also equal the lock's core."""
    res = LockCheck()
    have = installed.get("plugins", {}) if isinstance(installed, dict) else {}
    for key, p in lock["plugins"].items():
        sink = res.blocking if p["required"] else res.warnings
        ent = _effective_entry(have.get(key, []))
        if ent is None:
            sink.append(f"{key}: not installed (lock wants {p['version']} @ {p['commit'][:12]})")
            continue
        if ent.get("version") != p["version"] or ent.get("gitCommitSha") != p["commit"]:
            sink.append(f"{key}: installed {ent.get('version')} @ {str(ent.get('gitCommitSha'))[:12]}, "
                        f"lock is {p['version']} @ {p['commit'][:12]}")
    if specflow_md is not None:
        try:
            m = re.search(r"^core:\s*(\S+)", Path(specflow_md).read_text(encoding="utf-8"), re.M)
        except OSError as e:
            res.blocking.append(f"cannot read {specflow_md}: {e}")
            m = None
        else:
            if not m:
                res.blocking.append(f"{specflow_md}: no core: version line")
            elif m.group(1) != lock["core"]["version"]:
                res.blocking.append(f"{specflow_md}: core: {m.group(1)}, the lock's core is {lock['core']['version']}")
    res.ok = not res.blocking
    return res


def default_installed() -> Path:
    return Path.home() / ".claude" / "plugins" / "installed_plugins.json"


def _same_path(a, b) -> bool:
    if not isinstance(a, str) or not a:
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def build_lock(installed: dict, plugin_json: dict, marketplaces: dict | None = None, locked_at: str | None = None) -> dict:
    """Make a lock from installed_plugins.json: the plugin that ships this script (name from plugin.json) as the core,
    plus every plugin in plugin.json "dependencies" ("name@marketplace"), all required. Uses the user-scope entry.
    marketplaces: known_marketplaces.json content, used for source info when available."""
    have = installed.get("plugins", {}) if isinstance(installed, dict) else {}
    name = plugin_json.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError("plugin.json has no name", 2)
    deps = plugin_json.get("dependencies") or []
    if not _is_str_list(deps):
        raise ConfigError("plugin.json dependencies must be an array of strings", 2)
    core_keys = [k for k in have if k.split("@", 1)[0] == name]
    if not core_keys:
        raise ConfigError(f"{name} is not installed (nothing named {name}@... in installed_plugins.json)", 4)
    # The core is the install this script belongs to: the entry whose installPath is PLUGIN_ROOT. With the plugin
    # installed from several marketplaces, guessing would pin the wrong one.
    mine = [k for k in core_keys if _same_path((_effective_entry(have[k]) or {}).get("installPath"), PLUGIN_ROOT)]
    if len(mine) != 1:
        raise ConfigError(
            f"cannot tell which install of {name} this script belongs to: {len(mine)} entries of {', '.join(sorted(core_keys))} "
            f"have an installPath equal to {PLUGIN_ROOT}; run lock-write from the installed copy (exactly one must match)", 2)
    wanted = [mine[0], *deps]
    plugins = {}
    for key in wanted:
        ent = _effective_entry(have.get(key, []))
        if ent is None:
            raise ConfigError(f"{key} is not installed: install it, then run lock-write again", 4)
        sha, ver = ent.get("gitCommitSha"), ent.get("version")
        if not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) and isinstance(ver, str) and ver):
            raise ConfigError(f"{key}: installed entry has no usable version / gitCommitSha", 2)
        mkt = key.split("@", 1)[1] if "@" in key else ""
        src = ((marketplaces or {}).get(mkt) or {}).get("source")
        source = {"type": src.get("source") or "unknown", **({"repo": src["repo"]} if src.get("repo") else {})} \
            if isinstance(src, dict) else {"type": "unknown"}
        plugins[key] = {"marketplace": mkt, "source": source, "version": ver, "commit": sha, "required": True}
    core = wanted[0]
    out = {"schema_version": SCHEMA_VERSION}
    if locked_at:
        out["locked_at"] = locked_at
    out["core"] = {"plugin": core, "version": plugins[core]["version"], "commit": plugins[core]["commit"]}
    out["plugins"] = plugins
    return out


# ---------- doctor ----------

def default_probe(argv: list[str], timeout: int) -> tuple[int, str]:
    r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    out = next((ln.strip() for ln in (r.stdout + "\n" + r.stderr).splitlines() if ln.strip()), "")
    return r.returncode, out[:200]


@dataclass
class ToolResult:
    name: str
    found: bool
    path: str | None = None
    version: str | None = None
    timed_out: bool = False
    broken: bool = False  # found but the version probe returned non-zero: not usable


@dataclass
class CapabilityResult:
    name: str
    declared: bool
    measured: str  # ok / missing / unmeasured
    missing_tools: list[str] = field(default_factory=list)
    missing_repos: list[str] = field(default_factory=list)


@dataclass
class DoctorReport:
    tools: list[ToolResult]
    capabilities: list[CapabilityResult]
    timed_out: bool = False
    warnings: list[str] = field(default_factory=list)

    def exit_code(self, requested: list[str] | None = None) -> int:
        want = requested if requested else ["spec_work"]
        by = {c.name: c for c in self.capabilities}
        if any(n not in by for n in want):
            return 2
        if self.timed_out:
            return 6
        return 5 if any(by[n].measured != "ok" for n in want) else 0

    def to_dict(self) -> dict:
        return {"tools": [t.__dict__ for t in self.tools],
                "capabilities": [c.__dict__ for c in self.capabilities],
                "timed_out": self.timed_out, "warnings": self.warnings}


def doctor(policy: dict, local: dict[str, Path], which=shutil.which, probe=default_probe,
           timeout: int = 20) -> DoctorReport:
    """Declared (the policy says it must exist) and measured (which + version probe + repo path) are recorded apart.
    A capability with measurable=false (e.g. a real-hardware rig) can only be unmeasured, never counted as available."""
    tools, timed_out = [], False
    for name, spec in policy["tools"].items():
        found = which(name)
        t = ToolResult(name, bool(found), found)
        if found:
            try:
                rc, out = probe([found, *spec.get("version_args", ["--version"])], timeout)
                t.version = out.strip() if rc == 0 else None
                t.broken = rc != 0
            except subprocess.TimeoutExpired:
                t.timed_out = timed_out = True
        tools.append(t)
    have = {t.name for t in tools if t.found and not t.broken and not t.timed_out}
    caps = []
    for name, c in policy["capabilities"].items():
        if c.get("measurable") is False:
            caps.append(CapabilityResult(name, True, "unmeasured"))
            continue
        mt = [t for t in c["tools"] if t not in have]
        mr = [r for r in c["repos"] if not (r in local and (local[r] / ".git").exists())]
        caps.append(CapabilityResult(name, True, "missing" if mt or mr else "ok", mt, mr))
    return DoctorReport(tools, caps, timed_out)


def local_file_warnings(root: Path) -> list[str]:
    """Doctor warnings about the per-machine mapping file: not git-ignored means a path could be committed by accident."""
    if local_file_ignored(root) is False:
        return [f"{LOCAL_NAME} is not git-ignored in {root}: add it to .gitignore (it holds this machine's paths)"]
    return []


# ---------- effective settings sources ----------

SETTINGS_CANDIDATES = [
    ("user", None, ".claude/settings.json"),
    ("project", "root", ".claude/settings.json"),
    ("project-local", "root", ".claude/settings.local.json"),
    ("product", "product", ".claude/settings.json"),
    ("product-local", "product", ".claude/settings.local.json"),
]


def effective_settings(root: Path, home: Path, product: Path | None = None) -> list[dict]:
    """Location, existence and SessionStart commands of each settings layer. Permissions and other content are not output.
    root: home repo root; home: the user's home folder; product: the product folder (product layers are skipped without it)."""
    out = []
    for scope, base, rel in SETTINGS_CANDIDATES:
        if base == "product" and product is None:
            continue
        b = {None: home, "root": root, "product": product}[base]
        p = Path(b) / rel
        item = {"scope": scope, "path": p.as_posix(), "exists": p.is_file(), "session_start": []}
        if item["exists"]:
            try:
                hooks = json.loads(p.read_text(encoding="utf-8")).get("hooks", {}).get("SessionStart", [])
                item["session_start"] = [h.get("command", "") for g in hooks for h in g.get("hooks", [])]
            except (OSError, ValueError, AttributeError):
                item["error"] = "unreadable or not valid JSON"
        out.append(item)
    return out


# ---------- generation ----------

def render_local_example(policy: dict) -> str:
    """Content of specflow.local.example.json: one placeholder path per repo id. Output is fixed, so regenerating gives no diff."""
    obj = {
        "_comment": f"Copy to {LOCAL_NAME} (never committed) and set each path to this machine's absolute path; "
                    f"non-ASCII and spaces in paths are fine. Optional \"machine\" is a label for this machine. "
                    f"Generated from {POLICY_NAME}: python workflow_config.py regen",
        "schema_version": SCHEMA_VERSION,
        "repos": {rid: {"path": f"<absolute path of {rid} on this machine>"} for rid in policy["repos"]},
    }
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


# ---------- pre-dispatch check ----------

def dispatch_gate(product: Path, installed: Path | None = None, which=shutil.which,
                  probe=default_probe) -> tuple[int, list[str]]:
    """Block decision before a dispatch: returns (exit code, messages).
    dispatch.requires_lock: lock mismatch -> 3; dispatch.requires_capabilities not all measured ok -> 5 (probe timeout 6)."""
    product = Path(product)
    policy = load_policy(policy_path(product))
    disp = policy["dispatch"]
    msgs: list[str] = []
    if disp.get("requires_lock"):
        lock = load_lock(lock_path(product))
        res = check_lock(lock, _load(installed or default_installed()), product / SPECFLOW_MD)
        msgs += [f"[blocked] {m}" for m in res.blocking]
        if not res.ok:
            return 3, msgs
    caps = disp.get("requires_capabilities", [])
    if caps:
        local = mapping_with_home(policy, load_local(local_file(product), policy), product)
        rep = doctor(policy, local, which=which, probe=probe)
        code = rep.exit_code(caps)
        if code:
            by = {c.name: c for c in rep.capabilities}
            for n in caps:
                c = by[n]
                if c.measured != "ok":
                    msgs.append(f"[blocked] capability {n}: measured {c.measured}"
                                + (f", missing tools {' '.join(c.missing_tools)}" if c.missing_tools else "")
                                + (f", missing repo mapping {' '.join(c.missing_repos)} ({LOCAL_NAME})" if c.missing_repos else ""))
            if rep.timed_out:
                msgs.append("[blocked] tool version probe timed out")
            return code, msgs
    return 0, msgs


# ---------- CLI ----------

def _print_json(o):
    print(json.dumps(o, ensure_ascii=False, indent=2))


def cmd_validate(a) -> int:
    load_policy(policy_path(a.product))
    load_lock(lock_path(a.product))
    print(f"validate: {POLICY_NAME} and {LOCK_NAME} are valid")
    return 0


def cmd_lock_check(a) -> int:
    lock = load_lock(lock_path(a.product))
    res = check_lock(lock, _load(a.installed or default_installed()), a.product / SPECFLOW_MD)
    if a.json:
        _print_json(res.to_dict())
    else:
        for m in res.blocking:
            print(f"  [blocked] {m}")
        for m in res.warnings:
            print(f"  [note] {m}")
        print("lock-check: compatible" if res.ok else "lock-check: incompatible; dispatches that depend on these plugins must stop")
    return 0 if res.ok else 3


def cmd_lock_write(a) -> int:
    installed_file = a.installed or default_installed()
    pj_file = a.plugin_json or PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    mk_file = installed_file.parent / "known_marketplaces.json"
    try:
        marketplaces = json.loads(mk_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        marketplaces = None
    from datetime import date
    lock = build_lock(_load(installed_file), _load(pj_file), marketplaces if isinstance(marketplaces, dict) else None,
                      date.today().isoformat())
    errs = validate_lock(lock)
    if errs:
        raise ConfigError("generated lock is invalid:\n  " + "\n  ".join(errs), 2)
    target = lock_path(a.product)
    target.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"lock-write: wrote {target} (core {lock['core']['plugin']} {lock['core']['version']}, "
          f"{len(lock['plugins'])} plugins)")
    return 0


def cmd_doctor(a) -> int:
    policy = load_policy(policy_path(a.product))
    local = mapping_with_home(policy, load_local(local_file(a.product), policy), a.product)
    rep = doctor(policy, local)
    rep.warnings += local_file_warnings(home_root(a.product))
    if a.json:
        _print_json(rep.to_dict())
    else:
        for t in rep.tools:
            print(f"  [{'broken' if t.broken else 'ok' if t.found else 'missing'}] tool {t.name}" + (f": {t.version}" if t.version else ""))
        for c in rep.capabilities:
            extra = (f" (missing tools {' '.join(c.missing_tools)})" if c.missing_tools else "") + \
                    (f" (missing repo mapping {' '.join(c.missing_repos)})" if c.missing_repos else "")
            print(f"  capability {c.name}: declared yes / measured {c.measured}{extra}")
        for w in rep.warnings:
            print(f"  [warning] {w}")
    return rep.exit_code(a.for_caps)


def cmd_dispatch_check(a) -> int:
    code, msgs = dispatch_gate(a.product, a.installed)
    for m in msgs:
        print(f"  {m}")
    print("dispatch-check: ok to dispatch" if code == 0 else f"dispatch-check: cannot dispatch (exit code {code})")
    return code


def cmd_settings(a) -> int:
    policy = load_policy(policy_path(a.product))
    root = home_root(a.product)
    for s in effective_settings(root, Path.home(), a.product):
        print(f"  {s['scope']:<14} {'yes' if s['exists'] else 'no '}  {s['path']}")
        for c in s["session_start"]:
            print(f"      SessionStart: {c}")
    cwd = (policy.get("entry") or {}).get("cwd")
    if isinstance(cwd, str) and cwd:
        print(f"fixed entry: open the session in {root / cwd}")
    return 0


def cmd_regen(a) -> int:
    policy = load_policy(policy_path(a.product))
    text = render_local_example(policy)
    target = home_root(a.product) / EXAMPLE_NAME
    cur = target.read_text(encoding="utf-8") if target.exists() else None
    if a.check:
        if cur == text:
            print("regen: no diff")
            return 0
        print(f"regen: {EXAMPLE_NAME} differs from what the policy generates (run: python workflow_config.py regen)")
        return 3
    if cur != text:
        target.write_text(text, encoding="utf-8", newline="\n")
        print(f"regen: wrote {target}")
    else:
        print("regen: no change")
    return 0


def cmd_repo_path(a) -> int:
    policy = load_policy(policy_path(a.product))
    if a.repo_id not in policy["repos"]:
        raise ConfigError(f"unknown repo id \"{a.repo_id}\"", 2)
    lf = local_file(a.product)
    p = load_local(lf, policy).get(a.repo_id)
    if p is None and a.repo_id == home_id(policy):
        p = home_root(a.product)
    if p is None:
        p = require_repo_path(a.repo_id, lf)
    print(p)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--product", type=Path, default=Path.cwd(),
                    help="the product folder (holds specflow.md); default: the current directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("validate", cmd_validate), ("settings", cmd_settings)):
        sub.add_parser(name).set_defaults(func=fn)
    p = sub.add_parser("lock-check")
    p.add_argument("--installed", type=Path)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_lock_check)
    p = sub.add_parser("lock-write")
    p.add_argument("--installed", type=Path)
    p.add_argument("--plugin-json", type=Path)
    p.set_defaults(func=cmd_lock_write)
    p = sub.add_parser("dispatch-check")
    p.add_argument("--installed", type=Path)
    p.set_defaults(func=cmd_dispatch_check)
    p = sub.add_parser("doctor")
    p.add_argument("--for", dest="for_caps", nargs="+", metavar="CAP")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("regen")
    p.add_argument("--check", action="store_true")
    p.set_defaults(func=cmd_regen)
    p = sub.add_parser("repo-path")
    p.add_argument("repo_id")
    p.set_defaults(func=cmd_repo_path)
    # --product must also work after the subcommand
    for sp in sub.choices.values():
        sp.add_argument("--product", type=Path, default=argparse.SUPPRESS)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    a.product = Path(a.product)
    try:
        return a.func(a)
    except ConfigError as e:
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
