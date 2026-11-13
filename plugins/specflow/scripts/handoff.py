#!/usr/bin/env python3
"""handoff: export / verify / report of the handoff manifest between machines. Stdlib only.

The handoff file and the manifests live in the home repo (the repo with "home": true in workflow.json). Where they are
comes from the "handoff" block of workflow.json in the product folder (the folder that holds specflow.md):

  "handoff": {"file": ".notes/handoff.md", "task_notes": "progress/{id}.md", "local_only": ["build/"]}

  file        the handoff file, relative to the home repo root (required). The root manifest handoff.manifest.json
              and the folder handoffs/ sit next to it.
  task_notes  per-task notes, relative to the home repo root; {id} is the task id (optional; null or absent: the
              handoff file is the only source)
  local_only  path prefixes, relative to a repo root (any repo), whose content stays on this machine (optional). The git
              dir, which holds the runtime dir <git common dir>/specflow-runtime/, always counts as local.

  python handoff.py export --task <id> [--task <id>...] [--artifact ID=URI]... [--optional-artifact ID=URI]...
                           [--ref REPO_ID=REF]... [--handoff-id ID] [--machine NAME] [--json]
  python handoff.py verify [--handoff <handoff_id>] [--fetch] [--for <capability>...] [--json]
  python handoff.py report [--task <id>...] [--fetch] [--json]
  python handoff.py new-id [--machine NAME]
  Common: --product DIR (default: the current directory), before or after the subcommand. The home repo is the checkout
  that holds the product folder; the other repos' paths on this machine come from <home repo root>/specflow.local.json.

export: writes handoffs/<handoff_id>.json (fixed, never overwritten) and handoff.manifest.json (the latest entry) only
  when the result is 0 or 4; 2 / 3 / 1 write nothing. First `git add` the handoff file, the task notes and new files to
  carry (home repo hashes come from the index); after export commit exactly the output commit_paths ("the carrier
  commit") and push; then verify --fetch tells whether the handoff is published. A handoff file over 5120 bytes is
  refused. --ref app=task/a1 records the commit of a WIP task branch (or task worktree) without switching the mapped
  checkout (default: the checkout's HEAD); one --ref per repo.
verify: checks every item of the manifest and lists all problems at once (a timeout on one item does not stop the
  others); without --handoff it reads the root manifest, and with --fetch a root manifest on the home repo's remote
  integration branch that names another handoff, on commits this checkout has not pulled, makes it stale (3): pull
  first. Only --fetch talks to remotes (fetch + ls-remote). Only the references in the handoff file and in the notes of
  the manifest's task_ids are checked.
report: read-only audit of the handoff as committed now (e.g. one without a manifest): what another machine cannot get.
None of the commands commits, pushes or changes a working tree. Hashes are always of Git blobs (git cat-file blob),
never of working-tree files.

References are code spans that look like paths, markdown links, [[wikilinks]] and URLs. A repo id right before a code
span (app `src/x.c`) or app:src/x.c names the repo; otherwise it is the home repo. A reference is exempt when the mark
`local only` or `僅本機` directly follows it (blanks or one opening bracket in between).

Levels (JSON "level"): export: "local" (manifest written, no untracked / uncommitted pending item); verify:
"published" (fetched, carrier commit checked, nothing missing) or "resumable" (published and the requested capabilities
measured ok, default spec_work); null otherwise.

Exit codes: 0 pass, 2 input / schema (incl. a suspected credential, an oversized handoff file), 3 hash mismatch or a
local uncommitted change in the way, 4 missing file / not uploaded / evidence unreadable, 5 capability missing,
6 timeout, 1 unexpected error. With several problems: 3 > 4 > 6 > 5.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import posixpath
import re
import secrets
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def _load_sibling(name: str, module_name: str):
    """Load a sibling script by file path under a unique module name (the scripts folder is not a package, and a product
    adapter may itself be importable under the plain name; the core must never bind to that). One copy per process: a
    module already loaded under module_name from this very file is reused (so every core module shares its classes); one
    from another path (e.g. another plugin version) is not. A failed load leaves sys.modules as it was."""
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


wc = _load_sibling("workflow_config", "specflow_workflow_config")

SCHEMA_VERSION = 1
ROOT_MANIFEST_NAME = "handoff.manifest.json"  # next to the handoff file: the latest handoff
HANDOFFS_DIRNAME = "handoffs"                 # next to the handoff file: <handoff_id>.json, never overwritten
HANDOFF_MAX_BYTES = 5120                      # export refuses a longer handoff file
MANIFEST_KEYS = ("schema_version", "handoff_id", "created_at", "source_machine", "task_ids",
                 "repos", "required_files", "artifacts", "pending_transfers")
REPO_KEYS = ("repo_id", "remote_alias", "ref", "commit_sha")
FILE_KEYS = ("repo_id", "relative_path", "sha256")
ARTIFACT_KEYS = ("artifact_id", "uri", "sha256", "size_bytes", "required_for_resume")
PENDING_KEYS = ("repo_id", "relative_path", "artifact_id", "reason", "needed_for")
PENDING_REASONS = ("untracked", "uncommitted", "unpushed", "upload_failed")
LEVELS = ("local", "published", "resumable")
LOCAL_ONLY_MARKS = ("local only", "僅本機")  # right after a reference: it stays on this machine, on purpose
GIT_DIR = ".git"  # never leaves this machine; the runtime dir <git common dir>/specflow-runtime/ is in it
DEFAULT_CAPS = ("spec_work",)
NET_TIMEOUT = 120
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class HandoffError(Exception):
    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


# ---------- configuration ----------

_rel_ok = wc.rel_ok  # the one definition lives in workflow_config, which validates the handoff block too


@dataclass(frozen=True)
class HandoffConfig:
    """The "handoff" block of workflow.json plus the home repo id. Every path is relative to the home repo root."""
    home: str
    file: str
    task_notes: str | None = None
    local_only: tuple[str, ...] = ()

    @property
    def root_manifest(self) -> str:
        return posixpath.join(posixpath.dirname(self.file), ROOT_MANIFEST_NAME)

    @property
    def handoffs_dir(self) -> str:
        return posixpath.join(posixpath.dirname(self.file), HANDOFFS_DIRNAME)

    def manifest_rel(self, handoff_id: str) -> str:
        return f"{self.handoffs_dir}/{handoff_id}.json"

    def note(self, task_id: str) -> str | None:
        return self.task_notes.replace("{id}", task_id) if self.task_notes else None

    def sources(self, task_ids) -> list[tuple[str, str]]:
        """(path, needed_for) of every file whose references are checked: the selected tasks' notes, then the handoff."""
        return [(self.note(t), t) for t in task_ids if self.task_notes] + [(self.file, "handoff")]


def handoff_config(policy: dict) -> HandoffConfig:
    """Read and check the "handoff" block of a loaded workflow.json. Any problem -> HandoffError 2."""
    h = policy.get("handoff")
    if not isinstance(h, dict):
        raise HandoffError(f"{wc.POLICY_NAME} has no \"handoff\" object: add "
                           "{\"file\": \"<handoff file, relative to the home repo root>\"}", 2)
    e = wc.handoff_problems(h)  # the same check validate_policy runs
    tn, lo = h.get("task_notes"), h.get("local_only", [])
    if e:
        raise HandoffError(f"{wc.POLICY_NAME}: " + "; ".join(e), 2)
    return HandoffConfig(wc.home_id(policy), h["file"], tn, tuple(x.rstrip("/") for x in lo))


# ---------- secrets ----------

_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>`\"'）」]+")
_TOKEN_KEYS = {"token", "access_token", "accesstoken", "auth", "auth_token", "api_key", "apikey", "key", "secret",
               "client_secret", "password", "passwd", "pwd", "sig", "signature", "private_token",
               "x-amz-signature", "x-amz-credential", "x-amz-security-token"}
_BARE_TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}"
                            r"|sk-(?:ant-)?[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})")


def find_secrets(text: str) -> list[str]:
    """URLs with credentials, URL parameters that look like credentials, common token strings. The messages never
    contain the secret itself. ssh://git@host is fine."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for url in _URL_RE.findall(line):
            try:
                u = urllib.parse.urlsplit(url)
            except ValueError:
                continue
            host = u.netloc.rsplit("@", 1)[-1]
            if "@" in u.netloc:
                userinfo = u.netloc.rsplit("@", 1)[0]
                if ":" in userinfo or u.scheme.lower() in ("http", "https", "ftp", "ftps"):
                    hits.append(f"line {n}: URL carries a user or token ({u.scheme}://***@{host})")
            for k, _ in urllib.parse.parse_qsl(u.query) + urllib.parse.parse_qsl(u.fragment):
                kl = k.lower()
                if kl in _TOKEN_KEYS or kl.endswith(("_token", "-token")):
                    hits.append(f"line {n}: URL parameter \"{k}=\" looks like a credential ({u.scheme}://{host}/...)")
        for m in _BARE_TOKEN_RE.finditer(line):
            hits.append(f"line {n}: string looks like a token ({m.group(0)[:4]}...)")
    return hits


def _secret_gate(text: str, label: str) -> None:
    hits = find_secrets(text)
    if hits:
        raise HandoffError(f"{label} contains a suspected credential; refused:\n  " + "\n  ".join(hits), 2)


# ---------- git (read-only; fetch only when asked) ----------

class Git:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._files: dict[str, list[str]] = {}

    def run(self, *args, timeout: int = 60) -> subprocess.CompletedProcess:
        env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_LITERAL_PATHSPECS": "1"}
        return subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, timeout=timeout, env=env)

    def text(self, *args, timeout: int = 60) -> str | None:
        r = self.run(*args, timeout=timeout)
        return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else None

    def ok(self, *args) -> bool:
        return self.run(*args).returncode == 0

    def is_repo(self) -> bool:
        return (self.path / ".git").exists() and self.ok("rev-parse", "--git-dir")

    def has_commit(self, sha: str) -> bool:
        return self.ok("cat-file", "-e", f"{sha}^{{commit}}")

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", a, b)

    def obj_type(self, rev: str, rel: str) -> str | None:
        """rev = a commit; rev = "" means the index (the index has no tree objects: a directory is found by ls-files)."""
        t = self.text("cat-file", "-t", f"{rev}:{rel}")
        if t in ("blob", "tree"):
            return t
        if rev == "" and self.text("ls-files", "-z", "--", rel + "/"):
            return "tree"
        return None

    def blob(self, rev: str, rel: str) -> bytes | None:
        r = self.run("cat-file", "blob", f"{rev}:{rel}")
        return r.stdout if r.returncode == 0 else None

    def files(self, rev: str) -> list[str]:
        if rev not in self._files:
            r = self.run("ls-files", "-z") if rev == "" else self.run("ls-tree", "-r", "-z", "--name-only", rev)
            self._files[rev] = [x for x in r.stdout.decode("utf-8", "replace").split("\0") if x] \
                if r.returncode == 0 else []
        return self._files[rev]

    def status(self, *paths: str, untracked: str = "all") -> list[tuple[str, str]]:
        args = ["status", "--porcelain=v1", "-z", f"--untracked-files={untracked}"]
        if paths:
            args += ["--", *paths]
        parts = self.run(*args).stdout.decode("utf-8", "replace").split("\0")
        out, i = [], 0
        while i < len(parts):
            e = parts[i]
            i += 1
            if len(e) < 4:
                continue
            if e[0] in "RC":
                i += 1  # a rename / copy is followed by the original path
            out.append((e[:2], e[3:]))
        return out


def _last_line(b: bytes) -> str:
    lines = [x for x in b.decode("utf-8", "replace").splitlines() if x.strip()]
    return lines[-1].strip() if lines else ""


def _find_carriers(g: Git, manifest_rel: str) -> list[str]:
    """Commits (any ref) that add this fixed manifest: the carrier commits of a handoff."""
    out = g.text("log", "--all", "--diff-filter=A", "--format=%H", "--", manifest_rel)
    return out.split() if out else []


def _remote_refs(g: Git, alias: str, fetch: bool, timeout: int) -> tuple[dict | None, str | None]:
    """Remote ref -> sha. fetch: git fetch for the objects, then ls-remote for the remote as it is now (a deleted ref
    shows); without fetch: the refs/remotes/<alias>/* this machine remembers. Returns (refs, error message)."""
    if fetch:
        r = g.run("fetch", "--quiet", "--no-auto-gc", alias, timeout=timeout)
        if r.returncode:
            return None, f"git fetch {alias} failed: {_last_line(r.stderr)}"
        r = g.run("ls-remote", alias, timeout=timeout)
        if r.returncode:
            return None, f"git ls-remote {alias} failed: {_last_line(r.stderr)}"
        refs: dict[str, str] = {}
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            sha, _, name = line.partition("\t")
            if name.startswith("refs/heads/"):
                refs[name[len("refs/heads/"):]] = sha
            elif name.startswith("refs/tags/"):
                key = name[len("refs/"):]
                if key.endswith("^{}"):
                    refs[key[:-3]] = sha
                else:
                    refs.setdefault(key, sha)
        return refs, None
    pre = f"refs/remotes/{alias}/"
    refs = {}
    for line in (g.text("for-each-ref", "--format=%(objectname) %(refname)", pre) or "").splitlines():
        sha, _, name = line.partition(" ")
        short = name[len(pre):]
        if short and short != "HEAD":
            refs[short] = sha
    return refs, None


def _reach(g: Git, sha: str, ref: str, refs: dict) -> str | None:
    """The remote ref sha is reachable from: the recorded ref first, then the others; None if none."""
    if ref in refs and g.is_ancestor(sha, refs[ref]):
        return ref
    for name, tip in refs.items():
        if name != ref and g.is_ancestor(sha, tip):
            return name
    return None


def _upstream(g: Git, alias: str, sha: str, integration: str, ref_arg: str | None = None) -> tuple[str, bool]:
    """(remote ref name, whether sha is already in the remote ref this machine remembers). ref_arg = the local branch /
    remote-tracking ref / SHA given with export --ref; none given: the current checkout. A branch without an upstream
    (or with an upstream not on alias) counts as not uploaded."""
    if ref_arg is None:
        branch = g.text("symbolic-ref", "--quiet", "--short", "HEAD")
    elif g.ok("show-ref", "--verify", "--quiet", f"refs/heads/{ref_arg}"):
        branch = ref_arg
    else:
        branch = None
        rr = ref_arg[len("refs/remotes/"):] if ref_arg.startswith("refs/remotes/") else ref_arg
        if rr.startswith(alias + "/") and g.ok("show-ref", "--verify", "--quiet", f"refs/remotes/{rr}"):
            return rr[len(alias) + 1:], g.is_ancestor(sha, f"refs/remotes/{rr}")
    if branch:
        up = g.text("for-each-ref", "--format=%(upstream:remotename) %(upstream:remoteref)",
                    f"refs/heads/{branch}") or ""
        remote, _, rref = up.partition(" ")
        if remote == alias and rref.startswith("refs/heads/"):
            ref = rref[len("refs/heads/"):]
            return ref, g.is_ancestor(sha, f"refs/remotes/{alias}/{ref}")
        return branch, False
    pre = f"refs/remotes/{alias}/"
    names = [n[len(pre):] for n in (g.text("for-each-ref", "--contains", sha, "--format=%(refname)", pre)
                                    or "").split() if not n.endswith("/HEAD")]
    if names:
        return (integration if integration in names else names[0]), True
    return ref_arg or "HEAD", False


def _worktree_of(g: Git, branch: str) -> Path | None:
    """The worktree where branch is checked out now (git worktree list); None if none."""
    out = g.run("worktree", "list", "--porcelain", "-z").stdout.decode("utf-8", "replace")
    cur: dict[str, str] = {}
    for field_ in out.split("\0") + [""]:
        if field_:
            k, _, v = field_.partition(" ")
            cur[k] = v
            continue
        if cur.get("branch") == f"refs/heads/{branch}" and cur.get("worktree") and Path(cur["worktree"]).is_dir():
            return Path(cur["worktree"])  # a deleted (prunable) worktree does not count
        cur = {}
    return None


# ---------- manifest ----------

def _uri_ok(u) -> bool:
    try:
        return isinstance(u, str) and urllib.parse.urlsplit(u).scheme.lower() in ("file", "http", "https")
    except ValueError:
        return False


def validate_manifest(d, home: str | None = None) -> list[str]:
    """Every problem (does not stop at the first); an empty list means valid. Unknown fields are always refused (component
    status must never be copied in). home: the home repo id, which repos must then contain."""
    if not isinstance(d, dict):
        return ["the manifest top level must be an object"]
    e: list[str] = []
    extra = sorted(set(d) - set(MANIFEST_KEYS))
    if extra:
        e.append(f"unknown fields: {', '.join(extra)} (component status lives only in components.json)")
    e += [f"missing field {k}" for k in MANIFEST_KEYS if k not in d]
    if d.get("schema_version") != SCHEMA_VERSION:
        e.append(f"schema_version must be {SCHEMA_VERSION}")
    if not isinstance(d.get("handoff_id"), str) or not ID_RE.match(d.get("handoff_id", "")):
        e.append("handoff_id must be a string usable as a file name (letters, digits, . _ -)")
    try:
        if datetime.fromisoformat(d.get("created_at")).tzinfo is None:
            e.append("created_at must carry a time zone")
    except (TypeError, ValueError):
        e.append("created_at must be an ISO 8601 time")
    if not isinstance(d.get("source_machine"), str) or not d.get("source_machine"):
        e.append("source_machine must be a non-empty string")
    tids = d.get("task_ids")
    if not isinstance(tids, list):
        e.append("task_ids must be an array of task ids (export --task)")
    else:
        e.extend(f"task_ids[{i}] is not a valid task id" for i, t in enumerate(tids)
                 if not isinstance(t, str) or not TASK_RE.match(t))
        if len({str(t) for t in tids}) != len(tids):
            e.append("task_ids has duplicates")

    def entries(key, keys, allowed=None):
        v = d.get(key)
        if not isinstance(v, list):
            if key in d:
                e.append(f"{key} must be an array")
            return []
        out = []
        for i, x in enumerate(v):
            if not isinstance(x, dict):
                e.append(f"{key}[{i}] must be an object")
                continue
            bad = sorted(set(x) - set(allowed or keys))
            if bad:
                e.append(f"{key}[{i}]: unknown fields: {', '.join(bad)}")
            if allowed is None:
                e.extend(f"{key}[{i}] is missing {k}" for k in keys if k not in x)
            out.append((i, x))
        return out

    ids = []
    for i, r in entries("repos", REPO_KEYS):
        if not isinstance(r.get("repo_id"), str) or not r.get("repo_id"):
            e.append(f"repos[{i}].repo_id must be a non-empty string")
        elif r["repo_id"] in ids:
            e.append(f"repos[{i}].repo_id \"{r['repo_id']}\" is a duplicate")
        else:
            ids.append(r["repo_id"])
        if not isinstance(r.get("remote_alias"), str) or not re.fullmatch(r"[A-Za-z0-9._-]+", r.get("remote_alias", "")):
            e.append(f"repos[{i}].remote_alias must be a remote name (e.g. origin), not a URL")
        if not isinstance(r.get("ref"), str) or not r.get("ref"):
            e.append(f"repos[{i}].ref must be a non-empty string")
        if not isinstance(r.get("commit_sha"), str) or not HEX40.match(r.get("commit_sha", "")):
            e.append(f"repos[{i}].commit_sha must be 40 lowercase hex digits")
    if home is not None and isinstance(d.get("repos"), list) and home not in ids:
        e.append(f"repos must include the home repo \"{home}\" (its commit before the handoff)")
    seen = set()
    for i, f in entries("required_files", FILE_KEYS):
        if f.get("repo_id") not in ids:
            e.append(f"required_files[{i}].repo_id \"{f.get('repo_id')}\" is not in repos")
        if not _rel_ok(f.get("relative_path")):
            e.append(f"required_files[{i}].relative_path must be a path inside the repo (/ separated, normalized, no ..)")
        if not isinstance(f.get("sha256"), str) or not HEX64.match(f.get("sha256", "")):
            e.append(f"required_files[{i}].sha256 must be 64 lowercase hex digits")
        key = (f.get("repo_id"), f.get("relative_path"))
        if key in seen:
            e.append(f"required_files[{i}] is a duplicate")
        seen.add(key)
    aids = set()
    for i, a in entries("artifacts", ARTIFACT_KEYS):
        if not isinstance(a.get("artifact_id"), str) or not ID_RE.match(a.get("artifact_id", "")):
            e.append(f"artifacts[{i}].artifact_id is not valid")
        elif a["artifact_id"] in aids:
            e.append(f"artifacts[{i}].artifact_id is a duplicate")
        else:
            aids.add(a["artifact_id"])
        if not _uri_ok(a.get("uri")):
            e.append(f"artifacts[{i}].uri must be file:, http: or https:")
        if not isinstance(a.get("sha256"), str) or not HEX64.match(a.get("sha256", "")):
            e.append(f"artifacts[{i}].sha256 must be 64 lowercase hex digits")
        if type(a.get("size_bytes")) is not int or a["size_bytes"] < 0:
            e.append(f"artifacts[{i}].size_bytes must be a non-negative integer")
        if not isinstance(a.get("required_for_resume"), bool):
            e.append(f"artifacts[{i}].required_for_resume must be true or false")
    for i, p in entries("pending_transfers", PENDING_KEYS, allowed=PENDING_KEYS):
        if p.get("reason") not in PENDING_REASONS:
            e.append(f"pending_transfers[{i}].reason must be one of {', '.join(PENDING_REASONS)}")
        if not isinstance(p.get("needed_for"), str) or not p.get("needed_for"):
            e.append(f"pending_transfers[{i}].needed_for must be a task id")
        if ("artifact_id" in p) == ("repo_id" in p):
            e.append(f"pending_transfers[{i}] needs exactly one of repo_id (+ relative_path) or artifact_id")
        elif "repo_id" in p:
            if p["repo_id"] not in ids:
                e.append(f"pending_transfers[{i}].repo_id \"{p['repo_id']}\" is not in repos")
            if "relative_path" in p and not _rel_ok(p["relative_path"]):
                e.append(f"pending_transfers[{i}].relative_path must be a path inside the repo")
            if "relative_path" not in p and p.get("reason") != "unpushed":
                e.append(f"pending_transfers[{i}]: only unpushed may leave out relative_path (a whole commit)")
        elif "relative_path" in p:
            e.append(f"pending_transfers[{i}]: an artifact has no relative_path")
    return e


@dataclass
class HandoffManifest:
    schema_version: int
    handoff_id: str
    created_at: str
    source_machine: str
    task_ids: list
    repos: list
    required_files: list
    artifacts: list
    pending_transfers: list

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in MANIFEST_KEYS}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_dict(cls, d: dict) -> "HandoffManifest":
        errs = validate_manifest(d)
        if errs:
            raise HandoffError("manifest does not match the schema:\n  " + "\n  ".join(errs), 2)
        return cls(**{k: d[k] for k in MANIFEST_KEYS})

    @classmethod
    def from_json(cls, text: str, label: str = "manifest") -> "HandoffManifest":
        try:
            d = json.loads(text)
        except json.JSONDecodeError as e:
            raise HandoffError(f"{label} is not valid JSON: {e}", 2)
        return cls.from_dict(d)


def new_handoff_id(machine: str, now: datetime | None = None) -> str:
    """Time + machine + random, e.g. 20261113T1120-B-3f2a. Only file-name characters of the machine name are kept."""
    now = now or datetime.now().astimezone()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", machine or "").strip("_-")[:20] or "machine"
    return f"{now:%Y%m%dT%H%M}-{safe}-{secrets.token_hex(2)}"


def _id_taken(root: Path, cfg: HandoffConfig, g: Git, hid: str) -> bool:
    rel = cfg.manifest_rel(hid)
    return (root / rel).exists() or bool(_find_carriers(g, rel))


def write_manifest(home_root: Path, cfg: HandoffConfig, m: HandoffManifest) -> Path:
    """The fixed manifest is only created, never overwritten (exists: 3); the root manifest is replaced via temp + replace."""
    root = Path(home_root)
    fixed = root / cfg.manifest_rel(m.handoff_id)
    fixed.parent.mkdir(parents=True, exist_ok=True)
    text = m.to_json()
    try:
        with open(fixed, "x", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except FileExistsError:
        raise HandoffError(f"{fixed} already exists: a fixed manifest is never overwritten; use a new handoff_id", 3)
    top = root / cfg.root_manifest
    tmp = top.with_name(f"{top.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, top)
    return fixed


def load_manifest(home_root: Path, cfg: HandoffConfig, handoff_id: str | None = None, *, fetch_alias: str | None = None,
                  timeout: int = NET_TIMEOUT) -> HandoffManifest:
    """No id: the root manifest. An id: the fixed file in the working tree, else (after fetch) the commit in the git
    history that carries it."""
    root = Path(home_root)
    if handoff_id is None:
        p = root / cfg.root_manifest
        if not p.is_file():
            raise HandoffError(f"no {cfg.root_manifest}: no handoff has been exported with handoff.py export yet "
                               "(or this machine has not pulled)", 4)
        return HandoffManifest.from_json(p.read_text(encoding="utf-8-sig"), str(p))
    if not ID_RE.match(handoff_id):
        raise HandoffError(f"invalid handoff_id: {handoff_id}", 2)
    rel = cfg.manifest_rel(handoff_id)
    p = root / rel
    if p.is_file():
        return HandoffManifest.from_json(p.read_text(encoding="utf-8-sig"), str(p))
    g = Git(root)
    fetch_timed_out = False
    if fetch_alias:
        try:
            g.run("fetch", "--quiet", "--no-auto-gc", fetch_alias, timeout=timeout)
        except subprocess.TimeoutExpired:
            fetch_timed_out = True  # the local history may have it already; verify's own fetch records the timeout
    for c in _find_carriers(g, rel):
        data = g.blob(c, rel)
        if data is not None:
            return HandoffManifest.from_json(data.decode("utf-8-sig"), f"{c[:12]}:{rel}")
    if fetch_timed_out:
        raise HandoffError(f"handoff {handoff_id} not found: git fetch {fetch_alias} timed out ({timeout} s), "
                           "cannot tell whether the remote has it", 6)
    raise HandoffError(f"handoff {handoff_id} not found: neither the {cfg.home} working tree nor its git history has "
                       f"{rel} (has the source machine pushed {cfg.home}?)", 4)


# ---------- references ----------

@dataclass(frozen=True)
class Ref:
    kind: str            # path / url / wikilink / local / placeholder
    token: str           # as written in the document
    repo: str | None = None
    path: str | None = None
    bare: bool = False   # a file name only: no repo and no folder
    is_dir: bool = False
    local_ok: bool = False  # a local-only mark directly follows this reference
    line: int = 0


_SPAN = re.compile(r"(`+)(.+?)\1")
_WIKI = re.compile(r"\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")
_MDLINK = re.compile(r"\]\(([^()\s]+)(?:\s+\"[^\"]*\")?\)")
_EXTS = frozenset("md json py ps1 psm1 bat cmd sh txt c h cc cpp hpp yml yaml toml csv cmake lock log ini cfg "
                  "xml html js ts proto pdf png jpg svg".split())
_STRIP = "'\"()[]{},;，。、；（）「」『』"
_MARK_AFTER = re.compile(r"\s*[（(【\[]?\s*(?:" + "|".join(re.escape(x) + (r"\b" if x.isascii() else "")
                                                         for x in LOCAL_ONLY_MARKS) + ")", re.I)


def _has_ext(p: str) -> bool:
    m = re.search(r"\.([A-Za-z0-9]+)$", p)
    return bool(m) and m.group(1).lower() in _EXTS


def _is_local_prefix(p: str, prefixes: tuple) -> bool:
    return any(p == x or p.startswith(x + "/") for x in prefixes)


def _classify(tok: str, repo: str | None, explicit: re.Pattern, ok: bool, n: int, home: str,
              prefixes: tuple) -> Ref | None:
    t = tok.strip(_STRIP).rstrip(":：")
    if not t or "://" in t or t.startswith("-"):
        return None
    m = explicit.match(t)
    if m:
        repo, t = m.group(1), m.group(2)
    p = t.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", p) or p.startswith(("~", "//")):
        return Ref("local", t, local_ok=ok, line=n)
    if p.startswith("/"):  # /command is not a path; /c/dev/x is an absolute path
        return Ref("local", t, local_ok=ok, line=n) if "/" in p[1:] else None
    if not ("/" in p or _has_ext(p)):
        return None
    if re.search(r"[<>*?{}$%|]", p):
        return Ref("placeholder", t, local_ok=ok, line=n)
    norm = posixpath.normpath(p[2:] if p.startswith("./") else p)
    if norm == ".":
        return None
    if norm == ".." or norm.startswith("../") or _is_local_prefix(norm, prefixes):
        return Ref("local", t, local_ok=ok, line=n)
    return Ref("path", t, repo or home, norm, bare=repo is None and "/" not in norm, is_dir=p.endswith("/"),
               local_ok=ok, line=n)


def _md_ref(target: str, source: str, ok: bool, n: int, home: str, prefixes: tuple) -> Ref | None:
    t = urllib.parse.unquote(target.split("#", 1)[0]).replace("\\", "/")
    if not t:
        return None
    if re.match(r"^[A-Za-z]:/", t) or t.startswith("/"):
        return Ref("local", target, local_ok=ok, line=n)
    norm = posixpath.normpath(posixpath.join(posixpath.dirname(source), t))
    if norm == ".." or norm.startswith("../") or _is_local_prefix(norm, prefixes):
        return Ref("local", target, local_ok=ok, line=n)
    return Ref("path", target, home, norm, is_dir=t.endswith("/"), local_ok=ok, line=n)


def parse_refs(text: str, source: str, home: str, repo_ids=(), local_only=()) -> list[Ref]:
    """References in the handoff file / task notes: path-like words in code spans, markdown links, [[wikilinks]], URLs.
    A repo id right before a code span (app `docs/x.md`) or app:docs/x.md names the repo; otherwise the home repo.
    A file name only is not guessed into a repo (bare). Absolute paths, paths leaving the repo, the git dir and the
    local_only prefixes are "local" (only on this machine). A local-only mark (`local only` / `僅本機`) exempts only the
    reference it directly follows (blanks or one opening bracket in between, e.g. `build/r1/` (local only)); the other
    references on the line are not exempt. source: the path of the document in the home repo (for relative links)."""
    ids = sorted({home, *repo_ids}, key=len, reverse=True)
    alt = "|".join(map(re.escape, ids))
    prefix = re.compile(rf"(?<![\w-])({alt})\s*$")
    explicit = re.compile(rf"^({alt}):(.+)$")
    prefixes = (GIT_DIR, *(x.rstrip("/") for x in local_only))
    out: list[Ref] = []
    for n, line in enumerate(text.splitlines(), 1):
        def marked(end: int) -> bool:
            return bool(_MARK_AFTER.match(line, end))
        for mu in _URL_RE.finditer(line):
            u = mu.group(0).rstrip(".,;:)、。，")
            out.append(Ref("url", u, local_ok=marked(mu.start() + len(u)), line=n))
        for ml in _MDLINK.finditer(line):
            t = ml.group(1)
            if "://" not in t and not t.startswith(("#", "mailto:")):
                r = _md_ref(t, source, marked(ml.end()), n, home, prefixes)
                if r:
                    out.append(r)
        for mw in _WIKI.finditer(line):
            out.append(Ref("wikilink", mw.group(1).strip(), local_ok=marked(mw.end()), line=n))
        for mt in _SPAN.finditer(line):
            pm = prefix.search(line[:mt.start()])
            ok = marked(mt.end())
            for tok in mt.group(2).split():
                r = _classify(tok, pm.group(1) if pm else None, explicit, ok, n, home, prefixes)
                if r:
                    out.append(r)
    return out


def _resolve_wikilink(name: str, files: list[str]) -> list[str]:
    n = name.strip().replace("\\", "/")
    want = n if re.search(r"\.[A-Za-z0-9]{1,5}$", n) else n + ".md"
    if "/" in want:
        return [f for f in files if f == want or f.endswith("/" + want)]
    return [f for f in files if posixpath.basename(f) == want]


# ---------- artifacts ----------

def _open_uri(uri: str, timeout: int):
    u = urllib.parse.urlsplit(uri)
    s = u.scheme.lower()
    if s == "file":
        path = urllib.request.url2pathname(u.path)
        if u.netloc and u.netloc.lower() != "localhost":
            path = ("\\\\" if os.name == "nt" else "//") + u.netloc + path
        return open(path, "rb")
    if s in ("http", "https"):
        return urllib.request.urlopen(urllib.request.Request(uri, headers={"User-Agent": "handoff-verify"}),
                                      timeout=timeout)
    raise HandoffError(f"artifact uri supports only file:, http: and https: (got \"{s or 'no scheme'}\")", 2)


def _hash_uri(uri: str, timeout: int = NET_TIMEOUT) -> tuple[int, str]:
    h, n = hashlib.sha256(), 0
    with _open_uri(uri, timeout) as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return n, h.hexdigest()


# ---------- collect (shared by export and report) ----------

def _mapping(root: Path, policy: dict, home: str) -> dict:
    """This machine's repo paths from <home repo root>/specflow.local.json; the home repo is always root."""
    local = wc.load_local(Path(root) / wc.LOCAL_NAME, policy)
    local[home] = Path(root)
    return local


def collect_handoff(product: Path, task_ids: list[str], *, policy: dict | None = None, local: dict | None = None,
                    artifacts=(), handoff_id: str | None = None, source_machine: str | None = None,
                    home_at: str = "index", refs: dict | None = None, notes: list | None = None,
                    now: datetime | None = None) -> HandoffManifest:
    """Build the manifest (writes nothing). product: the product folder; the home repo is its git top level.
    home_at="index" (export): home repo files are taken from the index, an unstaged change is uncommitted; the home repo
    is never unpushed (it is pushed after the carrier commit). home_at="head" (report): home repo files come from HEAD,
    like every other repo. Other repos default to the HEAD of the mapped checkout; refs={repo_id: branch / remote-tracking
    ref / SHA} records that commit instead (WIP task branch, task worktree); uncommitted / untracked files are looked up in
    the worktree where that branch is checked out (none: not looked up). A recorded commit not in a remote ref (or a
    branch without upstream) is pending unpushed. artifacts: [(artifact_id, uri, required_for_resume)]. notes: extra
    remarks are appended here."""
    if home_at not in ("index", "head"):
        raise ValueError(home_at)
    product = Path(product)
    policy = policy if policy is not None else wc.load_policy(wc.policy_path(product))
    cfg = handoff_config(policy)
    home = cfg.home
    root = wc.home_root(product)
    notes = [] if notes is None else notes
    refs = dict(refs or {})
    local = dict(_mapping(root, policy, home) if local is None else local)
    local[home] = root
    bad = [t for t in task_ids if not TASK_RE.match(t)]
    if bad:
        raise HandoffError(f"invalid task ids: {', '.join(bad)}", 2)
    task_ids = list(dict.fromkeys(task_ids))
    for rid in refs:
        if rid not in policy["repos"]:
            raise HandoffError(f"--ref: unknown repo id \"{rid}\" (known: {' '.join(policy['repos'])})", 2)
        if rid == home:
            raise HandoffError(f"--ref cannot name the home repo {home}: the carrier commit is made on the current "
                               f"{home} checkout", 2)
        if local.get(rid) is None:
            raise HandoffError(f"--ref: repo \"{rid}\" has no mapping on this machine (repos.{rid}.path in "
                               f"{wc.LOCAL_NAME})", 4)

    gits: dict[str, Git] = {}
    works: dict[str, Git | None] = {}  # the worktree of the recorded version: where uncommitted / untracked is seen
    revs: dict[str, str] = {}
    repos, unpushed = [], []
    for rid in policy["repos"]:
        p = local.get(rid)
        if p is None:
            notes.append(f"{rid}: no mapping on this machine ({wc.LOCAL_NAME}); left out of the manifest")
            continue
        g = Git(Path(p))
        head = g.text("rev-parse", "--verify", "HEAD") if g.is_repo() else None
        if head is None:
            if rid == home:
                raise HandoffError(f"{root} is not a git repo with commits", 2)
            if rid in refs:
                raise HandoffError(f"--ref: {p} is not a git repo with commits", 4)
            notes.append(f"{rid}: {p} is not a git repo with commits; left out of the manifest")
            continue
        sha, work = head, g
        if rid in refs:
            sha = g.text("rev-parse", "--verify", "--quiet", f"{refs[rid]}^{{commit}}")
            if sha is None:
                raise HandoffError(f"--ref {rid}={refs[rid]}: {rid} has no such ref", 2)
            wt = _worktree_of(g, refs[rid])
            work = Git(wt) if wt is not None else (g if sha == head else None)
            if work is None:
                notes.append(f"{rid}: {refs[rid]} is not checked out in any worktree; only the commit is recorded, "
                             "uncommitted files are not looked up")
        alias = wc.remote_alias(policy, rid)
        ref, pushed = _upstream(g, alias, sha, policy["repos"][rid]["integration_branch"], refs.get(rid))
        repos.append({"repo_id": rid, "remote_alias": alias, "ref": ref, "commit_sha": sha})
        gits[rid], works[rid] = g, work
        revs[rid] = "" if (rid == home and home_at == "index") else sha
        if rid != home and not pushed:
            unpushed.append(rid)
    hg = gits[home]

    machine = source_machine or platform.node() or "unknown"
    if handoff_id is not None:
        if not ID_RE.match(handoff_id):
            raise HandoffError(f"invalid handoff_id: {handoff_id}", 2)
        if _id_taken(root, cfg, hg, handoff_id):
            raise HandoffError(f"handoff_id \"{handoff_id}\" is already used: a fixed manifest is never overwritten; "
                               "pick a new one", 3)
        hid = handoff_id
    else:
        hid = new_handoff_id(machine, now)
        while _id_taken(root, cfg, hg, hid):
            hid = new_handoff_id(machine, now)

    sources = cfg.sources(task_ids)
    texts = {}
    for rel, need in sources:
        # export reads what the author just wrote (working tree: references, secrets); report reads the committed HEAD
        disk = (root / rel).read_bytes() if (root / rel).is_file() else None
        blob = hg.blob(revs[home], rel)
        data = (disk if disk is not None else blob) if home_at == "index" else (blob if blob is not None else disk)
        if data is None:
            raise HandoffError(f"not found: {rel}" + (f" (task {need} has no notes file)" if need != "handoff" else ""), 4)
        if home_at == "index" and rel == cfg.file and len(data) > HANDOFF_MAX_BYTES:
            raise HandoffError(f"{rel} is {len(data)} bytes, over the {HANDOFF_MAX_BYTES}-byte limit: shorten it "
                               "(move details into task notes or committed files and reference them)", 2)
        text = data.decode("utf-8", "replace")
        _secret_gate(text, rel)
        texts[rel] = (text, need)

    cands: dict[tuple[str, str], str] = {}
    for rel, need in sources:
        cands.setdefault((home, rel), need)
    for rel, (text, need) in texts.items():
        for r in parse_refs(text, rel, home, tuple(policy["repos"]), cfg.local_only):
            if r.local_ok:
                continue
            if r.kind == "path" and not r.is_dir:
                cands.setdefault((r.repo, r.path), need)
            elif r.kind == "wikilink":
                hit = _resolve_wikilink(r.token, hg.files(revs[home]))
                if len(hit) == 1:
                    cands.setdefault((home, hit[0]), need)

    required, pending = [], []
    for (rid, rel), need in cands.items():
        g = gits.get(rid)
        if g is None:
            continue  # repo not mapped: verify lists it as missing
        rev, work = revs[rid], works[rid]
        t = g.obj_type(rev, rel)
        if t == "blob":
            st = work.status(rel) if work is not None else []
            dirty = any(xy[1] != " " for xy, _ in st) if rev == "" else bool(st)
            if dirty:
                pending.append({"repo_id": rid, "relative_path": rel, "reason": "uncommitted", "needed_for": need})
            else:
                required.append({"repo_id": rid, "relative_path": rel,
                                 "sha256": hashlib.sha256(g.blob(rev, rel)).hexdigest()})
        elif t is None and work is not None and (work.path / rel).is_file():
            pending.append({"repo_id": rid, "relative_path": rel, "reason": "untracked", "needed_for": need})
        # a directory or nothing: not listed; verify's reference check reports it
    for rid in unpushed:
        needs = sorted({n for (r, _), n in cands.items() if r == rid and n != "handoff"})
        pending.append({"repo_id": rid, "reason": "unpushed", "needed_for": ",".join(needs) or "handoff"})

    arts, seen = [], set()
    art_need = ",".join(task_ids) or "handoff"
    for aid, uri, req in artifacts:
        if not isinstance(aid, str) or not ID_RE.match(aid) or aid in seen:
            raise HandoffError(f"artifact_id \"{aid}\" is invalid or a duplicate", 2)
        seen.add(aid)
        _secret_gate(uri, f"the uri of artifact {aid}")
        if not _uri_ok(uri):
            raise HandoffError(f"the uri of artifact {aid} must be file:, http: or https:", 2)
        try:
            size, h = _hash_uri(uri)
        except Exception as e:  # noqa: BLE001  an outside read: any failure means unreadable
            if req:
                pending.append({"artifact_id": aid, "reason": "upload_failed", "needed_for": art_need})
            else:
                notes.append(f"artifact {aid} (not required for resume) cannot be read; left out: {e}")
            continue
        arts.append({"artifact_id": aid, "uri": uri, "sha256": h, "size_bytes": size,
                     "required_for_resume": bool(req)})

    created = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    m = HandoffManifest(schema_version=SCHEMA_VERSION, handoff_id=hid, created_at=created, source_machine=machine,
                        task_ids=task_ids, repos=repos, required_files=required, artifacts=arts,
                        pending_transfers=pending)
    errs = validate_manifest(m.to_dict(), home)
    if errs:
        raise HandoffError("the generated manifest does not match the schema:\n  " + "\n  ".join(errs), 2)
    _secret_gate(m.to_json(), "manifest")
    return m


# ---------- verify ----------

@dataclass
class HandoffCheck:
    """Each entry of missing / stale / local_dirty / timed_out / capability_missing is {"item", "reason"}.
    timed_out holds only data-check timeouts (git, artifacts); a capability probe timeout goes to capability_missing (5)
    and does not take away published. item: <repo>:<path>, <repo>@<sha12>, <repo> (mapping / remote), artifact:<id>,
    handoff:<id>, [[wikilink]], capability <name>, or the text as written (file name only, path on this machine)."""
    missing: list = field(default_factory=list)
    stale: list = field(default_factory=list)
    local_dirty: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    capability_missing: list = field(default_factory=list)
    timed_out: list = field(default_factory=list)
    fetched: bool = False
    home_at: str = "carrier"

    def add(self, bucket: str, item: str, reason: str) -> None:
        lst = getattr(self, bucket)
        if all(x["item"] != item for x in lst):
            lst.append({"item": item, "reason": reason})

    @property
    def ready(self) -> bool:
        """The data passes: nothing missing, stale, blocked by a local change or timed out."""
        return not (self.missing or self.stale or self.local_dirty or self.timed_out)

    @property
    def published(self) -> bool:
        """Fetched, checked against the committed carrier commit, everything passes (pending items are missing too)."""
        return self.ready and self.fetched and self.home_at == "carrier"

    @property
    def resumable(self) -> bool:
        return self.published and not self.capability_missing

    @property
    def level(self) -> str | None:
        return "resumable" if self.resumable else "published" if self.published else None

    @property
    def exit_code(self) -> int:
        if self.stale or self.local_dirty:
            return 3
        if self.missing:
            return 4
        if self.timed_out:
            return 6
        if self.capability_missing:
            return 5
        return 0

    def to_dict(self) -> dict:
        return {"exit_code": self.exit_code, "level": self.level, "ready": self.ready, "published": self.published,
                "resumable": self.resumable, "fetched": self.fetched, "missing": self.missing, "stale": self.stale,
                "local_dirty": self.local_dirty, "capability_missing": self.capability_missing,
                "timed_out": self.timed_out, "warnings": self.warnings}


def _carrier(chk: HandoffCheck, g: Git, m: HandoffManifest, cfg: HandoffConfig, ref: str, refs: dict | None) -> str | None:
    """The home repo commit that carries this manifest (the one adding handoffs/<id>.json); its content must equal the
    manifest being checked."""
    rel = cfg.manifest_rel(m.handoff_id)
    cs = _find_carriers(g, rel)
    if not cs:
        chk.add("missing", f"handoff:{m.handoff_id}",
                f"no {cfg.home} commit carries this manifest (not committed, not pushed, or this machine has not "
                "fetched)")
        return None
    if len(cs) > 1 and refs:
        cs.sort(key=lambda c: _reach(g, c, ref, refs) is None)
    c = cs[0]
    try:
        same = json.loads((g.blob(c, rel) or b"null").decode("utf-8-sig")) == m.to_dict()
    except ValueError:
        same = False
    if not same:
        chk.add("stale", f"handoff:{m.handoff_id}", f"the manifest to check differs from the one in carrier commit {c[:12]}")
    return c


def _dirty(chk: HandoffCheck, g: Git, rid: str, req: dict, pend_files: set, blocking: bool) -> None:
    """A required file with a local uncommitted change or an untracked file of the same name blocks (3) and is left
    alone; any other dirt (incl. an uncommitted older manifest) is only a warning. blocking=False (export): the source
    machine's required files were already classified as pending by collect; only warn here."""
    mine = [rel for (r, rel) in req if r == rid and (r, rel) not in pend_files]
    if mine and blocking:
        for xy, p in g.status(*mine):
            chk.add("local_dirty", f"{rid}:{p}", f"uncommitted change or untracked file of the same name on this "
                                                 f"machine ({xy.strip()}); not overwritten, sort it out first")
    skip = set(mine) | {rel for (r, rel) in pend_files if r == rid}
    others = [p for _, p in g.status(untracked="normal") if p not in skip]
    if others:
        chk.warnings.append(f"{rid} has {len(others)} uncommitted / untracked items (left alone): "
                            f"{', '.join(others[:5])}" + (" ..." if len(others) > 5 else ""))


def _tmsg(e: subprocess.TimeoutExpired) -> str:
    cmd = list(e.cmd) if isinstance(e.cmd, (list, tuple)) else [str(e.cmd)]
    what = f"git {cmd[3]}" if len(cmd) > 3 and cmd[0] == "git" else str(cmd[0])
    return f"{what} timed out ({e.timeout:g} s); this item was not fully checked"


def _hints(gits: dict, revs: dict, rid: str, rel: str, bare: bool) -> str:
    out = []
    for other, g in gits.items():
        rev = revs.get(other)
        if rev is None:
            continue
        if bare:
            out += [f"{other}:{f}" for f in g.files(rev) if posixpath.basename(f) == rel]
        elif other != rid and g.obj_type(rev, rel) == "blob":
            out.append(f"{other}:{rel}")
    return f"; maybe {', '.join(out[:3])}" if out else ""


def _audit(m: HandoffManifest, local: dict, fetch: bool, home_at: str, repo_ids: tuple, cfg: HandoffConfig,
           timeout: int = NET_TIMEOUT) -> HandoffCheck:
    chk = HandoffCheck(fetched=fetch, home_at=home_at)
    home = cfg.home
    _secret_gate(m.to_json(), "manifest")
    req = {(f["repo_id"], f["relative_path"]): f for f in m.required_files}
    pend_files = {(p["repo_id"], p["relative_path"]) for p in m.pending_transfers if p.get("relative_path")}
    pend_repos = {p["repo_id"] for p in m.pending_transfers if p.get("repo_id") and not p.get("relative_path")}
    shas = {r["repo_id"]: r["commit_sha"] for r in m.repos}

    # 1. pending: what the source machine itself listed as not uploaded; all missing
    for p in m.pending_transfers:
        if p.get("artifact_id"):
            item = f"artifact:{p['artifact_id']}"
        elif p.get("relative_path"):
            item = f"{p['repo_id']}:{p['relative_path']}"
        else:
            item = f"{p['repo_id']}@{shas[p['repo_id']][:12]}"
        chk.add("missing", item, f"pending ({p['reason']}, needed for {p['needed_for']}): the source machine has not "
                                 "uploaded it")

    # 2. each repo: mapping, reachable from the remote, local dirt (a git call timing out: record it in timed_out, skip
    #    the rest of this repo, go on with the others)
    if not fetch:
        chk.warnings.append("no --fetch: remote checks use the remote refs this machine remembers; published / "
                            "resumable is not decided")
    gits: dict[str, Git] = {}
    revs: dict[str, str | None] = {}
    for r in m.repos:
        try:
            _audit_repo(chk, r, m, local, fetch, home_at, cfg, req, pend_files, pend_repos, gits, revs, timeout)
        except subprocess.TimeoutExpired as e:
            chk.add("timed_out", r["repo_id"], _tmsg(e))

    # 3. required files: in the tree of the recorded version, same blob sha256 (an unusable repo is reported above)
    for f in m.required_files:
        rid, rel = f["repo_id"], f["relative_path"]
        g, rev = gits.get(rid), revs.get(rid)
        if g is None or rev is None:
            continue
        where = "the index" if rev == "" else rev[:12]
        try:
            if g.obj_type(rev, rel) != "blob":
                chk.add("missing", f"{rid}:{rel}", f"not in the tree of {where} (uncommitted, deleted or wrong path)")
                continue
            h = hashlib.sha256(g.blob(rev, rel)).hexdigest()
        except subprocess.TimeoutExpired as e:
            chk.add("timed_out", f"{rid}:{rel}", _tmsg(e))
            continue
        if h != f["sha256"]:
            chk.add("stale", f"{rid}:{rel}", f"sha256 differs: manifest {f['sha256'][:12]}..., {where} has {h[:12]}...")

    # 4. outside artifacts
    for a in m.artifacts:
        item, need = f"artifact:{a['artifact_id']}", a["required_for_resume"]
        try:
            size, h = _hash_uri(a["uri"], timeout)
        except Exception as e:  # noqa: BLE001
            if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
                chk.add("timed_out", item, f"reading {a['uri']} timed out ({timeout} s)")
            elif need:
                chk.add("missing", item, f"cannot read {a['uri']}: {e}")
            else:
                chk.warnings.append(f"{item} (not required for resume) cannot be read: {e}")
            continue
        if (size, h) != (a["size_bytes"], a["sha256"]):
            msg = (f"content differs: manifest {a['size_bytes']} bytes / {a['sha256'][:12]}..., "
                   f"now {size} bytes / {h[:12]}...")
            if need:
                chk.add("stale", item, msg)
            else:
                chk.warnings.append(f"{item} (not required for resume) {msg}")

    # 5. references in the handoff file and the selected tasks' notes (task_ids): each is in the manifest or directly
    #    followed by a local-only mark. Other notes the handoff file links are carried (required_files), but the
    #    references inside them are not checked (same as collect).
    hg, hrev = gits.get(home), revs.get(home)
    if hg is not None and hrev is not None:
        uris = {a["uri"] for a in m.artifacts}
        seen = set()
        for src, _ in cfg.sources(m.task_ids):
            try:
                data = hg.blob(hrev, src)
                if data is None:
                    if (home, src) not in pend_files:
                        chk.add("missing", f"{home}:{src}", "not in the handoff version (the handoff file or a selected "
                                                            "task's notes)")
                    continue
                text = data.decode("utf-8", "replace")
                _secret_gate(text, src)
                for ref in parse_refs(text, src, home, repo_ids, cfg.local_only):
                    key = (ref.kind, ref.repo, ref.path, ref.token)
                    if ref.local_ok or key in seen:
                        continue
                    seen.add(key)
                    _check_ref(chk, ref, src, home, req, pend_files, shas, gits, revs, uris)
            except subprocess.TimeoutExpired as e:
                chk.add("timed_out", f"{home}:{src}", _tmsg(e))
    return chk


def _audit_repo(chk, r, m, local, fetch, home_at, cfg, req, pend_files, pend_repos, gits, revs, timeout) -> None:
    rid, sha, alias, ref = r["repo_id"], r["commit_sha"], r["remote_alias"], r["ref"]
    home = cfg.home
    p = local.get(rid)
    if p is None:
        chk.add("missing", rid, f"repo \"{rid}\" has no mapping on this machine: write this machine's absolute path at "
                                f"repos.{rid}.path in {wc.LOCAL_NAME}")
        return
    g = Git(Path(p))
    if not g.is_repo():
        chk.add("missing", rid, f"{p} is not a git repo (repos.{rid}.path in {wc.LOCAL_NAME})")
        return
    gits[rid] = g
    try:
        refs, err = _remote_refs(g, alias, fetch, timeout)
    except subprocess.TimeoutExpired as e:
        refs, err = None, None
        chk.add("timed_out", rid, f"contacting remote {alias}: {_tmsg(e)} (remote reachability not checked)")
    if err:
        chk.add("missing", rid, f"{err} (cannot check against the remote)")
    if rid == home:
        target = None
        if home_at == "index":
            revs[rid] = ""
            chk.warnings.append(f"the {home} carrier commit does not exist yet: commit and push commit_paths, then "
                                "verify --fetch tells whether the handoff is published")
        elif home_at == "head":
            target = revs[rid] = g.text("rev-parse", "HEAD")
        else:
            target = revs[rid] = _carrier(chk, g, m, cfg, ref, refs)
        if not g.has_commit(sha):
            chk.add("missing", f"{home}@{sha[:12]}", f"the {home} base commit from before the handoff is not on this "
                                                     "machine")
        if target and refs is not None:
            via = _reach(g, target, ref, refs)
            if via is None:
                chk.add("missing", f"{home}@{target[:12]}",
                        f"the {home} commit carrying the handoff is not on remote {alias}/{ref} or any remote ref "
                        f"({home} not pushed, or the push failed)")
            elif via != ref:
                chk.warnings.append(f"{home}: remote {alias}/{ref} does not contain the carrier commit; it is reachable "
                                    f"from {alias}/{via}")
    elif g.has_commit(sha):
        revs[rid] = sha
        if refs is not None and rid not in pend_repos:
            via = _reach(g, sha, ref, refs)
            if via is None:
                chk.add("missing", f"{rid}@{sha[:12]}",
                        f"remote {alias}/{ref} and every other remote ref lack this commit (not pushed, push failed or "
                        "history rewritten)")
            elif via != ref:
                chk.warnings.append(f"{rid}: remote {alias}/{ref} "
                                    f"{'no longer exists' if ref not in refs else 'no longer contains this version'}; "
                                    f"{sha[:12]} is still reachable from {alias}/{via}")
        h = g.text("rev-parse", "HEAD")
        if h and h != sha:
            chk.warnings.append(f"{rid}: local HEAD is {h[:12]}, the handoff version is {sha[:12]} ({ref}); not "
                                "switched automatically")
    else:
        revs[rid] = None
        chk.add("missing", f"{rid}@{sha[:12]}",
                "commit not on this machine" + (" (not there after fetch either: the remote does not have it)"
                                                if fetch else " (run verify --fetch first)"))
    _dirty(chk, g, rid, req, pend_files, blocking=home_at != "index")


def _check_ref(chk, ref: Ref, src, home, req, pend_files, shas, gits, revs, uris) -> None:
    if ref.kind == "url":
        if ref.token not in uris:
            chk.warnings.append(f"external link not in artifacts (no hash, not required for resume): {ref.token} ({src})")
        return
    if ref.kind == "placeholder":
        chk.warnings.append(f"template or wildcard, not checked: {ref.token} ({src})")
        return
    if ref.kind == "local":
        chk.add("missing", ref.token, "only on this machine (absolute path, outside the repo, the git dir or a "
                                      "handoff.local_only prefix): another machine cannot get it; point to a source "
                                      f"in a repo, or put `{LOCAL_ONLY_MARKS[0]}` right after it ({src})")
        return
    if ref.kind == "wikilink":
        hits = _resolve_wikilink(ref.token, gits[home].files(revs[home]))
        if len(hits) != 1:
            chk.add("missing", f"[[{ref.token}]]", (f"wikilink matches no file in {home}" if not hits else
                                                   f"wikilink matches several files: {', '.join(hits[:3])}")
                    + f" ({src})")
            return
        rid, rel, bare, is_dir = home, hits[0], False, False
    else:
        rid, rel, bare, is_dir = ref.repo, ref.path, ref.bare, ref.is_dir
    item = ref.token if bare else f"{rid}:{rel}"
    if (rid, rel) in req or (rid, rel) in pend_files:
        return
    if rid not in shas:
        chk.add("missing", item, f"references a {rid} file, but the manifest has no {rid} (the source machine has no "
                                 f"mapping for it or it is not a git repo) ({src})")
        return
    g, rev = gits.get(rid), revs.get(rid)
    if g is None or rev is None:  # not in the manifest means missing; this machine just cannot look closer
        chk.add("missing", item, f"not listed in the manifest ({rid}'s handoff version is not on this machine; no "
                                 f"more detail) ({src})")
        return
    t = g.obj_type(rev, rel)
    if t == "tree":
        chk.warnings.append(f"directory reference: only its existence is checked, not each file: {item} ({src})")
    elif is_dir:
        chk.add("missing", item, f"directory not in {rid}'s handoff version ({src})")
    elif t == "blob":
        chk.add("missing", item, f"referenced but not listed in the manifest's required_files ({src})")
    elif bare:
        chk.add("missing", item, f"file name only, no repo path (not at the {home} root; write repo + relative path)"
                                 f"{_hints(gits, revs, rid, rel, True)} ({src})")
    else:
        chk.add("missing", item, f"not in {rid}'s handoff version{_hints(gits, revs, rid, rel, False)} ({src})")


def _check_capabilities(chk: HandoffCheck, caps: list, policy: dict, local: dict, which, probe) -> None:
    """Reuses workflow_config.doctor; probes only the tools the selected capabilities use."""
    unknown = [c for c in caps if c not in policy["capabilities"]]
    if unknown:
        raise HandoffError(f"unknown capability: {', '.join(unknown)} (known: {' '.join(policy['capabilities'])})", 2)
    need = {t for c in caps for t in policy["capabilities"][c]["tools"]}
    sub = {**policy, "capabilities": {c: policy["capabilities"][c] for c in caps},
           "tools": {t: s for t, s in policy["tools"].items() if t in need}}
    rep = wc.doctor(sub, local, which=which or shutil.which, probe=probe or wc.default_probe)
    slow = {t.name for t in rep.tools if t.timed_out}
    for c in rep.capabilities:  # a probe timeout is only a missing capability (5), not timed_out: published stays
        if c.measured == "ok":
            continue
        parts = []
        if c.measured == "unmeasured":
            parts.append("cannot be measured by a tool (e.g. a real-hardware rig): confirm by hand before resuming")
        absent = [t for t in c.missing_tools if t not in slow]
        timed = [t for t in c.missing_tools if t in slow]
        if absent:
            parts.append(f"missing tools {' '.join(absent)}")
        if timed:
            parts.append(f"tool version probe timed out: {' '.join(timed)}")
        if c.missing_repos:
            parts.append(f"missing repo mapping {' '.join(c.missing_repos)} ({wc.LOCAL_NAME})")
        chk.capability_missing.append({"item": f"capability {c.name}", "reason": "; ".join(parts) or c.measured})


def verify_handoff(manifest, local_config: dict, fetch: bool, *, policy: dict, capabilities=None, which=None,
                   probe=None, home_at: str = "carrier", timeout: int = NET_TIMEOUT) -> HandoffCheck:
    """Check every item of the manifest and list all problems at once. local_config: {repo_id: path on this machine}
    with the home repo in it; the raw specflow.local.json content is accepted too. policy: the loaded workflow.json
    (home repo, handoff block, capabilities). fetch: True to fetch + ls-remote and check against the remotes.
    capabilities: the ones required (None = spec_work; [] = no check). home_at: carrier (the commit carrying the
    handoff) / head / index."""
    cfg = handoff_config(policy)
    m = manifest if isinstance(manifest, HandoffManifest) else HandoffManifest.from_dict(manifest)
    errs = validate_manifest(m.to_dict(), cfg.home)
    if errs:
        raise HandoffError("manifest does not match the schema:\n  " + "\n  ".join(errs), 2)
    if home_at not in ("carrier", "head", "index"):
        raise ValueError(home_at)
    if isinstance(local_config.get("repos"), dict):
        local = {k: Path(v["path"]) for k, v in local_config["repos"].items() if isinstance(v, dict) and v.get("path")}
    else:
        local = {k: Path(v) for k, v in local_config.items()}
    repo_ids = tuple(dict.fromkeys([*(r["repo_id"] for r in m.repos), *policy["repos"]]))
    chk = _audit(m, local, fetch, home_at, repo_ids, cfg, timeout)
    caps = list(DEFAULT_CAPS if capabilities is None else capabilities)
    if caps:
        _check_capabilities(chk, caps, policy, local, which, probe)
    return chk


# ---------- CLI ----------

def _print_json(o) -> None:
    print(json.dumps(o, ensure_ascii=False, indent=2))


def _print_check(chk: HandoffCheck) -> None:
    for x in chk.stale:
        print(f"  [stale] {x['item']}: {x['reason']}")
    for x in chk.local_dirty:
        print(f"  [local change] {x['item']}: {x['reason']}")
    for x in chk.missing:
        print(f"  [missing] {x['item']}: {x['reason']}")
    for x in chk.timed_out:
        print(f"  [timed out] {x['item']}: {x['reason']}")
    for x in chk.capability_missing:
        print(f"  [capability] {x['item']}: {x['reason']}")
    for w in chk.warnings:
        print(f"  [warning] {w}")


def _setup(a) -> tuple[dict, HandoffConfig, Path, dict]:
    """(policy, handoff config, home repo root, this machine's mapping with the home repo)."""
    policy = wc.load_policy(wc.policy_path(a.product))
    cfg = handoff_config(policy)
    root = wc.home_root(a.product)
    return policy, cfg, root, _mapping(root, policy, cfg.home)


def _default_machine(a) -> str:
    return a.machine or wc.load_machine(wc.local_file(a.product)) or platform.node() or "unknown"


def _kv(s: str, what: str, shape: str) -> tuple[str, str]:
    k, sep, v = s.partition("=")
    if not sep or not k or not v:
        raise HandoffError(f"{what} must be written as {shape}: {s.split('?')[0]}", 2)
    return k, v


def cmd_export(a) -> int:
    policy, cfg, root, local = _setup(a)
    notes: list[str] = []
    arts = [(*_kv(s, "--artifact", "ID=URI"), True) for s in a.artifact] + \
           [(*_kv(s, "--optional-artifact", "ID=URI"), False) for s in a.optional_artifact]
    refs: dict[str, str] = {}
    for s in a.ref:
        k, v = _kv(s, "--ref", "REPO_ID=REF")
        if k in refs:
            raise HandoffError(f"--ref: repo \"{k}\" is given more than once (one ref per repo)", 2)
        refs[k] = v
    m = collect_handoff(a.product, a.task, policy=policy, local=local, artifacts=arts, handoff_id=a.handoff_id,
                        source_machine=_default_machine(a), refs=refs, notes=notes)
    chk = verify_handoff(m, local, False, policy=policy, capabilities=[], home_at="index")  # check (incl. secrets) first
    chk.warnings[:0] = notes
    code = chk.exit_code
    written = code in (0, 4)  # 0 = complete, 4 = missing or pending (still local); 3 and up write nothing, keep the id
    fixed_rel = cfg.manifest_rel(m.handoff_id)
    commit_paths: list[str] = []
    if written:
        write_manifest(root, cfg, m)
        home_req = [f["relative_path"] for f in m.required_files if f["repo_id"] == cfg.home]
        staged = (Git(root).text("diff", "--cached", "--name-only", "-z", "HEAD", "--", *home_req) or "") \
            .split("\0") if home_req else []
        commit_paths = [cfg.root_manifest, fixed_rel, *[p for p in staged if p]]
    level = "local" if written and not any(p.get("reason") in ("untracked", "uncommitted")
                                           for p in m.pending_transfers) else None
    res = {**chk.to_dict(), "command": "export", "handoff_id": m.handoff_id, "written": written,
           "manifest_path": fixed_rel if written else None, "root_manifest_path": cfg.root_manifest,
           "level": level, "commit_paths": commit_paths, "pending_transfers": m.pending_transfers}
    if a.json:
        _print_json(res)
    else:
        print(f"export: handoff {m.handoff_id}" + (f" ({fixed_rel})" if written else " (no manifest written)"))
        _print_check(chk)
        if written:
            print(f"files to commit in {cfg.home}: " + ", ".join(commit_paths))
        print("level: local (commit + push, then verify --fetch tells whether it is published)" if level else
              "level: not even local (required content is uncommitted or untracked, or no manifest was written)")
        print(f"exit code {code}")
    return code


def newer_on_remote(root: Path, cfg: HandoffConfig, policy: dict, alias: str, handoff_id: str) -> str | None:
    """The handoff_id in the root manifest on the home repo's remote integration branch (as last fetched) when it names
    another handoff and that branch has commits this checkout lacks (not pulled yet); None otherwise, also when the
    remote has no root manifest."""
    g = Git(root)
    ref = f"refs/remotes/{alias}/{policy['repos'][cfg.home]['integration_branch']}"
    data = g.blob(ref, cfg.root_manifest)
    if data is None:
        return None
    try:
        rid = json.loads(data.decode("utf-8-sig")).get("handoff_id")
    except (ValueError, AttributeError):
        return None
    if not isinstance(rid, str) or rid == handoff_id or g.is_ancestor(ref, "HEAD"):
        return None
    return rid


def cmd_verify(a) -> int:
    policy, cfg, root, local = _setup(a)
    alias = wc.remote_alias(policy, cfg.home) if a.fetch else None
    m = load_manifest(root, cfg, a.handoff, fetch_alias=alias)
    chk = verify_handoff(m, local, a.fetch, policy=policy, capabilities=a.for_caps)
    if a.fetch and a.handoff is None:  # the root manifest here may be an older one than the remote's
        newer = newer_on_remote(root, cfg, policy, alias, m.handoff_id)
        if newer:
            chk.add("stale", f"handoff:{newer}", f"newer handoff {newer} on remote; pull first")
    res = {**chk.to_dict(), "command": "verify", "handoff_id": m.handoff_id, "source_machine": m.source_machine,
           "created_at": m.created_at}
    if a.json:
        _print_json(res)
    else:
        print(f"verify: handoff {m.handoff_id} (from {m.source_machine}, {m.created_at})")
        _print_check(chk)
        if chk.level:
            print(f"result: {chk.level} (exit code {chk.exit_code})")
        else:
            print(f"result: not ready (exit code {chk.exit_code})"
                  + ("" if a.fetch else "; without --fetch published / resumable is not decided"))
    return chk.exit_code


def cmd_report(a) -> int:
    policy, cfg, root, local = _setup(a)
    notes: list[str] = []
    m = collect_handoff(a.product, a.task, policy=policy, local=local, home_at="head",
                        source_machine=_default_machine(a), notes=notes)
    if a.fetch:  # unpushed is then judged from the remote as fetched, not from remote refs remembered here
        m.pending_transfers = [p for p in m.pending_transfers if p.get("relative_path") or p.get("artifact_id")]
    chk = verify_handoff(m, local, a.fetch, policy=policy, capabilities=[], home_at="head")
    chk.warnings[:0] = notes
    res = {**chk.to_dict(), "command": "report", "report_only": True,
           "repos": m.repos, "required_files": m.required_files, "pending_transfers": m.pending_transfers}
    if a.json:
        _print_json(res)
    else:
        print("report (read-only, nothing written): the handoff as committed now; what another machine cannot get")
        for r in m.repos:
            print(f"  {r['repo_id']}: {r['remote_alias']}/{r['ref']} @ {r['commit_sha'][:12]}")
        _print_check(chk)
        print(f"exit code {chk.exit_code}")
    return chk.exit_code


def cmd_new_id(a) -> int:
    print(new_handoff_id(_default_machine(a)))
    return 0


def _fail(code: int, msg: str, as_json: bool) -> int:
    if as_json:
        _print_json({"exit_code": code, "error": msg})
    print(f"error: {msg}", file=sys.stderr)
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--product", type=Path, default=Path.cwd(),
                    help="the product folder (holds specflow.md and workflow.json); default: the current directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("export")
    p.add_argument("--task", action="append", default=[])
    p.add_argument("--artifact", action="append", default=[], metavar="ID=URI")
    p.add_argument("--optional-artifact", action="append", default=[], metavar="ID=URI")
    p.add_argument("--ref", action="append", default=[], metavar="REPO_ID=REF")
    p.add_argument("--handoff-id")
    p.add_argument("--machine")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_export)
    p = sub.add_parser("verify")
    p.add_argument("--handoff")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--for", dest="for_caps", nargs="+", metavar="CAP")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)
    p = sub.add_parser("report")
    p.add_argument("--task", action="append", default=[])
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_report, machine=None)
    p = sub.add_parser("new-id")
    p.add_argument("--machine")
    p.set_defaults(func=cmd_new_id)
    for sp in sub.choices.values():  # --product must also work after the subcommand
        sp.add_argument("--product", type=Path, default=argparse.SUPPRESS)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    a.product = Path(a.product)
    as_json = getattr(a, "json", False)
    try:
        return a.func(a)
    except (HandoffError, wc.ConfigError) as e:
        return _fail(e.code, str(e), as_json)
    except subprocess.TimeoutExpired as e:
        return _fail(6, f"timeout: {e}", as_json)
    except Exception as e:  # noqa: BLE001
        return _fail(1, f"unexpected error: {type(e).__name__}: {e}", as_json)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
