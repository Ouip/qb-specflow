"""Local shared-state writes: locks, revision (CAS), atomic writes, claims, evidence. Stdlib only; Windows (msvcrt) and POSIX (fcntl).

Boundaries:
- A lock covers this machine only. All worktrees of one repo share one runtime root:
  `<git common dir>/specflow-runtime/` (`git rev-parse --git-common-dir` is the same for every worktree; it lives in .git,
  is never committed, and is never carried to another machine by push/pull). A local lock cannot stop another machine;
  across machines the explicit handoff is the only protocol, and no mutual exclusion is claimed.
- components.json: the whole read-modify-write happens under one lock; revision = sha256 of the file bytes (no counter
  field is stored, so the authority file format does not change); a write is same-directory temp + flush + fsync +
  os.replace, so a partial JSON is never left behind.
- handoff.md: the same lock + CAS (write_handoff). A stale input hash is refused; the caller re-reads, merges (keeping
  every open item) and writes again.
- claims (tasks and named local resources): an OS-held lock plus owner metadata (machine, pid, start time, worktree,
  run id). A claim is held until the owner process tree is confirmed stopped; age never matters; records from another
  machine are not imported and their PIDs are not trusted.
- evidence: write immutable evidence first (write_evidence), then reference it with update_registry; a reference that
  cannot be resolved or whose hash differs is refused. There is no transaction across several files.

`expected_revision=None` means "the caller holds no revision" and is the same idea everywhere, but what a function can do
with that depends on whether the file must already exist:
- cas_write / write_handoff: None = create only. The file must not exist yet; if it does, RevisionConflict.
- update_registry / update_repo: the file must exist (it is edited, never created). None = no revision check; the change
  is validated and applied under the lock against the CURRENT content, so no other writer's update is lost.
In both cases a str revision that is no longer current raises RevisionConflict carrying the current revision.

Exceptions inherit StateError; `code` maps to the exit codes: 1 input refused, 2 environment/format, 3 conflict
(revision or claim), 4 evidence missing, 6 lock wait timed out.
"""
import contextlib
import errno
import glob
import hashlib
import json
import os
import platform
import re
import secrets
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes
else:
    import fcntl

RUNTIME_DIRNAME = "specflow-runtime"  # under the git common dir
RUNS_DIRNAME = "runs"                 # run directories: <runtime>/runs/<run id>
TMP_SUFFIX = ".state-tmp"             # suffix of atomic-write temp files
LOCK_TIMEOUT = 30.0                   # seconds; a writer holds the lock only to read-modify-write one small file
MACHINE = platform.node()
CLAIM_KINDS = ("task", "resource")
# Named local resources are written "kind" or "kind:id", e.g. integration-branch:app or dispatch:A1 (one implementer
# per task at a time). These two are built in; a product adds more kinds through `resources` in workflow.json and
# passes them as acquire_claim(resources=...). Kind and name comparison ignores case (COM5 and com5 are one resource).
BUILTIN_RESOURCES = ("integration-branch", "dispatch")


class StateError(Exception):
    code = 2

    def __init__(self, msg, code=None):
        super().__init__(msg)
        if code is not None:
            self.code = code


class ChangeRejected(StateError):
    code = 1


class RevisionConflict(StateError):
    """The revision the caller read is no longer the file; `current` is the current revision (None if the file is absent)."""
    code = 3

    def __init__(self, msg, current):
        super().__init__(msg)
        self.current = current


class EvidenceMissing(StateError):
    code = 4


class LockTimeout(StateError):
    code = 6


class ClaimHeld(StateError):
    """The claim is held; `owner` is the owner metadata that was read (None if unreadable)."""
    code = 3

    def __init__(self, msg, owner=None):
        super().__init__(msg)
        self.owner = owner


# ---------- runtime root ----------

def _git(start, *args):
    try:
        r = subprocess.run(["git", "-C", str(start), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise StateError(f"cannot run git ({e})")
    return r.stdout.strip() if r.returncode == 0 else ""


def runtime_root(start: Path) -> Path:
    """Local runtime root shared by all worktrees of a repo: `<git common dir>/specflow-runtime`. Computes the path only; creates nothing."""
    start = Path(start)
    if start.is_file():
        start = start.parent
    out = _git(start, "rev-parse", "--git-common-dir")
    if not out:
        raise StateError(f"{start} is not inside a git repo: local locks and claims live in the git common dir (shared by all worktrees), so nothing is written without one")
    common = Path(out)
    if not common.is_absolute():
        common = start / common
    return common.resolve() / RUNTIME_DIRNAME


def runs_root(runtime: Path) -> Path:
    """Parent of all run directories: `<runtime>/runs`. A run directory is `<runs_root>/<run id>`. Computes the path only."""
    return Path(runtime) / RUNS_DIRNAME


def _toplevel(start: Path) -> Path:
    out = _git(start, "rev-parse", "--show-toplevel")
    if not out:
        raise StateError(f"{start} is not inside a git repo; cannot find the evidence root")
    return Path(out)


# ---------- OS locks ----------

def _open_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o644)


def _try_lock(fd: int) -> bool:
    """Take the exclusive lock without waiting; False if another handle (including another handle in this process) holds it."""
    try:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLOCK", errno.EDEADLK)):
            return False
        raise


def _unlock(fd: int) -> None:
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = LOCK_TIMEOUT):
    """OS exclusive lock (waits up to `timeout` seconds, then raises LockTimeout). The OS drops it when the holder exits, so a stuck lock cannot be left behind."""
    path = Path(path)
    fd = _open_lock(path)
    try:
        end = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= end:
                raise LockTimeout(f"waited more than {timeout:g}s for the lock {path}: a writer is stuck; find and stop it, then retry")
            time.sleep(0.003 + secrets.randbelow(20) / 1000)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


# ---------- atomic writes, revision ----------

def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def file_revision(path: Path) -> str | None:
    """sha256 of the file bytes (the revision used for CAS); None if the file is absent."""
    try:
        return _sha256(Path(path).read_bytes())
    except FileNotFoundError:
        return None


def _replace(src, dst):
    for i in range(100):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:  # Windows: refused while a reader briefly holds the target open; retry
            if os.name != "nt" or i == 99:
                raise StateError(f"cannot replace {dst} ({e}): another process keeps it open or there is no write permission; close it and retry") from e
            time.sleep(0.02)


def _fsync_dir(d: Path) -> None:
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """Same-directory temp -> flush -> fsync -> os.replace: a reader sees the old file or the new one, never half.
    Preventing lost updates is the caller's lock; this function only guarantees no partial file."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=TMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt":  # mkstemp makes 0600; keep the original mode
            with contextlib.suppress(OSError):
                os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode) if path.exists() else 0o644)
        _replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def _sweep_temps(path: Path) -> None:
    """Call while holding the lock: a temp left for this file was left by a writer killed mid-write (a live writer holds the lock while it writes a temp)."""
    for p in path.parent.glob(f".{glob.escape(path.name)}.*{TMP_SUFFIX}"):
        with contextlib.suppress(OSError):
            p.unlink()


def _conflict(path, expected, current):
    def short(x):
        return x[:12] if x else "absent"
    return RevisionConflict(f"{path.name} was changed (you read {short(expected)}, now {short(current)}): "
                            "re-read, merge, then write again", current)


def cas_write(path: Path, expected_revision: str | None, data: bytes, *, lock: str,
              runtime: Path | None = None, timeout: float = LOCK_TIMEOUT) -> str:
    """Whole-file CAS write: while holding `<runtime>/locks/<lock>.lock`, write atomically only if the current revision
    equals expected_revision. expected_revision=None means "the file must not exist yet" (create only; see the module
    docstring for how update_registry treats None). Returns the new revision; a stale one raises RevisionConflict
    (carrying the current revision)."""
    path = Path(path)
    rt = Path(runtime) if runtime else runtime_root(path.parent)
    with file_lock(rt / "locks" / f"{lock}.lock", timeout):
        _sweep_temps(path)
        current = file_revision(path)
        if current != expected_revision:
            raise _conflict(path, expected_revision, current)
        atomic_write(path, data)
    return _sha256(data)


def write_handoff(path: Path, expected_revision: str | None, text: str, *,
                  runtime: Path | None = None, timeout: float = LOCK_TIMEOUT) -> str:
    """Locked CAS write of a handoff file. expected_revision = the file_revision the caller read (None = create only).
    If another session changed the file, RevisionConflict is raised; the caller re-reads, merges (keeping every open item)
    and writes again. Returns the new revision."""
    return cas_write(path, expected_revision, text.encode("utf-8"), lock="handoff", runtime=runtime, timeout=timeout)


# ---------- components.json (the component registry) ----------

class _Keep:
    def __repr__(self):
        return "KEEP"


KEEP = _Keep()  # "leave unchanged" for RegistryChange.blocked


@dataclass(frozen=True)
class RegistryChange:
    """One change to one component. Fields that are None / KEEP are left alone."""
    component_id: str
    target_status: str | None = None   # new status; must be in states.order
    blocked: object = KEEP             # KEEP = unchanged; None = unblock; str = the reason (who must do what)
    evidence_refs: tuple | None = None  # replaces the component's evidence; each {"path": repo-relative, "sha256"}, written first
    reason: str = ""                   # why; returned with the result
    deps: tuple | None = None          # replaces the dependency list
    new: dict | None = None            # add a component: {"name", "spec", "repo"}; status starts at states.order[0]


def dump_registry(data: dict) -> bytes:
    """Fixed layout of components.json: one component per line (a status change touches one line in a diff), LF newlines."""
    head = json.dumps({k: v for k, v in data.items() if k != "components"}, ensure_ascii=False, indent=2)
    rows = ",\n".join("    " + json.dumps(c, ensure_ascii=False) for c in data["components"])
    return (head[:-2] + ',\n  "components": [\n' + rows + "\n  ]\n}\n").encode("utf-8")


def find_cycle(comps):
    """Return the ids on a dependency cycle (first == last), or None if there is none."""
    deps = {c.get("id"): c.get("deps") or [] for c in comps}
    seen = {}  # id -> 1 visiting, 2 done

    def visit(n, path):
        if seen.get(n) == 1:
            return path[path.index(n):] + [n]
        if seen.get(n) == 2 or n not in deps:
            return None
        seen[n] = 1
        for d in deps[n]:
            found = visit(d, path + [n])
            if found:
                return found
        seen[n] = 2
        return None

    for n in deps:
        found = visit(n, [])
        if found:
            return found
    return None


def _update_json(path: Path, expected_revision, mutate, runtime, timeout) -> dict:
    """Full read-modify-write of components.json: lock -> sweep stale temps -> read -> CAS -> mutate -> atomic write."""
    path = Path(path)
    rt = Path(runtime) if runtime else runtime_root(path.parent)
    with file_lock(rt / "locks" / "registry.lock", timeout):
        _sweep_temps(path)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise StateError(f"not found: {path}")
        rev = _sha256(raw)
        if expected_revision is not None and expected_revision != rev:
            raise _conflict(path, expected_revision, rev)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise StateError(f"{path} is not valid JSON: {e} (not overwritten; fix the file first)")
        result = mutate(data)
        out = dump_registry(data)
        if out != raw:
            atomic_write(path, out)
    return {"revision": _sha256(out), "previous_revision": rev, "changed": out != raw, **result}


def _apply(data: dict, ch: RegistryChange, spec_dir: Path, root) -> dict:
    comps = data.get("components")
    order = (data.get("states") or {}).get("order") or []
    if not isinstance(comps, list) or not order:
        raise StateError("components.json lacks components or states.order")
    ids = [c.get("id") for c in comps]
    cid = ch.component_id
    err = []
    if ch.new is not None:
        if not (isinstance(cid, str) and re.fullmatch(r"[A-Za-z0-9_-]+", cid)):
            err.append(f"component id {cid!r} may use only letters, digits, - and _")
        if cid in ids:
            err.append(f"component {cid} already exists")
        vals = {k: ch.new.get(k) for k in ("name", "spec", "repo")}
        for k, v in vals.items():
            if not (isinstance(v, str) and v.strip()):
                err.append(f"a new component needs {k}")
        if vals["repo"] and vals["repo"] not in (data.get("repos") or {}):
            err.append(f"repo {vals['repo']!r} is not in the repos of components.json")
        if isinstance(vals["spec"], str) and vals["spec"].strip() and not (spec_dir / vals["spec"].strip()).is_file():
            err.append(f"spec file {vals['spec'].strip()} not found next to components.json (write the spec before registering)")
        if err:
            raise ChangeRejected("; ".join(err))
        row = {"id": cid, "name": vals["name"].strip(), "spec": vals["spec"].strip(), "repo": vals["repo"],
               "deps": [], "status": order[0], "blocked": None}
        comps.append(row)
    else:
        row = next((c for c in comps if c.get("id") == cid), None)
        if row is None:
            raise ChangeRejected(f"no component {cid}")
        if ch.target_status is None and ch.blocked is KEEP and ch.deps is None and ch.evidence_refs is None:
            raise ChangeRejected("nothing to change: give at least one of status, blocked, deps, evidence")
    if ch.target_status is not None:
        if ch.target_status in order:
            row["status"] = ch.target_status
        else:
            err.append(f"status must be one of: {' '.join(order)}")
    if ch.blocked is not KEEP:
        if ch.blocked is None:
            row["blocked"] = None
        elif isinstance(ch.blocked, str) and ch.blocked.strip():
            row["blocked"] = ch.blocked.strip()
        else:
            err.append("a block needs a reason: who must do what")
    if ch.deps is not None:
        deps = list(ch.deps)
        bad = [d for d in deps if d not in ids or d == cid]
        if bad:
            err.append(f"dependency {', '.join(map(str, bad))} is not another component")
        elif len(set(deps)) != len(deps):
            err.append("duplicate dependency")
        else:
            row["deps"] = deps
            loop = find_cycle(comps)
            if loop:
                err.append("dependencies would form a cycle: " + " -> ".join(loop))
    if err:
        raise ChangeRejected("; ".join(err))
    if ch.evidence_refs is not None:
        refs = list(ch.evidence_refs)
        base = Path(root) if root else (_toplevel(spec_dir) if refs else None)
        missing = [m for m in (check_evidence(base, r) for r in refs) if m]
        if missing:
            raise EvidenceMissing("write evidence first, then reference it: " + "; ".join(missing))
        if refs:
            row["evidence"] = [{"path": r["path"], "sha256": r["sha256"]} for r in refs]
        else:
            row.pop("evidence", None)
    return row


def update_registry(path: Path, expected_revision: str | None, change: RegistryChange, *,
                    runtime: Path | None = None, root: Path | None = None, timeout: float = LOCK_TIMEOUT) -> dict:
    """The only write path for components.json (set / add commands go through here).

    expected_revision is the sha256 of the file bytes the caller read (file_revision), not a counter: the authority file
    gets no counter field, so two machines editing different components do not collide on a counter line.
    expected_revision=None = the caller holds no revision. The file must exist (it is edited, never created); no revision
    check is made and the change is validated and applied under the lock against the CURRENT file (no other writer's update
    is lost). Contrast cas_write, where None means "create only". A str revision that is no longer current -> RevisionConflict.

    Lock: `<runtime>/locks/registry.lock` (runtime defaults to the git common dir derived from path, one lock for all
    worktrees). The paths in evidence_refs are relative to `root` (default: top level of the repo holding path); each must
    exist and match its sha256, else EvidenceMissing and nothing is written. A failed validation raises ChangeRejected and
    the file is untouched.
    Returns {"revision", "previous_revision", "changed", "component" (the row after the change), "reason"}.
    """
    path = Path(path)

    def mutate(data):
        return {"component": dict(_apply(data, change, path.parent, root)), "reason": change.reason}
    return _update_json(path, expected_revision, mutate, runtime, timeout)


def update_repo(path: Path, expected_revision: str | None, repo_id: str, *, integration_branch: str | None = None,
                baseline: list[str] | None = None, runtime: Path | None = None, timeout: float = LOCK_TIMEOUT) -> dict:
    """The repos.<id> entry of components.json (integration branch, baseline commands); same lock, same CAS, and the same
    meaning of expected_revision=None as update_registry. The result includes "repo" (the entry after the change)."""
    def mutate(data):
        repos = data.get("repos") or {}
        if repo_id not in repos:
            raise ChangeRejected(f"no repo {repo_id} (have: {', '.join(repos) or 'none'})")
        if integration_branch is None and baseline is None:
            raise ChangeRejected("nothing to change: give integration_branch or baseline")
        r = repos[repo_id]
        if integration_branch is not None:
            if not integration_branch.strip():
                raise ChangeRejected("integration branch must not be blank")
            r["integration_branch"] = integration_branch.strip()
        if baseline is not None:
            if any(not (isinstance(b, str) and b.strip()) for b in baseline):
                raise ChangeRejected("baseline commands must not be blank")
            r["baseline"] = list(baseline)
        return {"repo": dict(r)}
    return _update_json(path, expected_revision, mutate, runtime, timeout)


# ---------- evidence ----------

def _evidence_path(root: Path, relpath) -> Path:
    if not (isinstance(relpath, str) and relpath) or "\\" in relpath:
        raise StateError(f"evidence path must be a repo-relative path using /: {relpath!r}")
    p, w = PurePosixPath(relpath), PureWindowsPath(relpath)
    if p.is_absolute() or w.drive or w.is_absolute() or ".." in p.parts:
        raise StateError(f"evidence path must be repo-relative: {relpath}")
    return Path(root).joinpath(*p.parts)


def write_evidence(root: Path, relpath: str, data: bytes) -> dict:
    """Write one immutable piece of evidence and return the {"path", "sha256"} for RegistryChange.evidence_refs.
    An existing file with the same content is reused; different content is always refused (evidence is never rewritten;
    use a new path, e.g. a new run id). Published by hard link after temp + fsync: nothing is overwritten and no half file appears."""
    target = _evidence_path(root, relpath)
    ref = {"path": PurePosixPath(relpath).as_posix(), "sha256": _sha256(data)}
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=TMP_SUFFIX)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, target)
            except FileExistsError:
                pass
            except OSError as e:  # e.g. the filesystem has no hard links, or no permission
                raise StateError(f"cannot publish evidence {relpath} ({e}): no hard links or no write permission at this location") from e
            else:
                _fsync_dir(target.parent)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
    if file_revision(target) != ref["sha256"]:
        raise StateError(f"evidence {relpath} exists with different content: evidence is never rewritten, use a new path")
    return ref


def check_evidence(root: Path, ref) -> str | None:
    """None if the evidence reference resolves and its sha256 matches; otherwise one sentence saying why."""
    if not (isinstance(ref, dict) and isinstance(ref.get("path"), str) and isinstance(ref.get("sha256"), str)):
        return f"bad reference format (need {{\"path\", \"sha256\"}}): {ref!r}"
    try:
        p = _evidence_path(root, ref["path"])
    except StateError as e:
        return str(e)
    got = file_revision(p)
    if got is None:
        return f"{ref['path']} does not exist"
    if got != ref["sha256"]:
        return f"sha256 of {ref['path']} differs (referenced {ref['sha256'][:12]}, now {got[:12]})"
    return None


# ---------- process liveness ----------

if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetExitCodeProcess.restype = wintypes.BOOL
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _k32.GetProcessTimes.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _PROCESS_QUERY_LIMITED_INFORMATION, _STILL_ACTIVE, _ERROR_INVALID_PARAMETER = 0x1000, 259, 87

    def _probe(pid):
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return ("dead" if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER else "unknown"), None
        try:
            code = wintypes.DWORD()
            if not _k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return "unknown", None
            if code.value != _STILL_ACTIVE:
                return "dead", None
            t = [wintypes.FILETIME() for _ in range(4)]
            if not _k32.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
                return "alive", None
            return "alive", str((t[0].dwHighDateTime << 32) | t[0].dwLowDateTime)
        finally:
            _k32.CloseHandle(h)
else:
    _BOOT_ID = None

    def _boot_id():
        global _BOOT_ID
        if _BOOT_ID is None:
            try:
                _BOOT_ID = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            except OSError:
                _BOOT_ID = ""
        return _BOOT_ID

    def _probe(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "dead", None
        except PermissionError:
            pass  # exists, but belongs to another user
        try:
            raw = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return "alive", None  # no /proc (not Linux): existence only, no protection against PID reuse
        fields = raw[raw.rindex(")") + 2:].split()
        if fields[0] in ("Z", "X"):  # zombie: already finished, just not reaped
            return "dead", None
        return "alive", f"{_boot_id()}:{fields[19]}"


def _probe_safe(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "dead", None
    return _probe(pid)


def proc_table() -> dict | None:
    """Linux: all current processes {pid: (ppid, pgrp, state)} (reads /proc); None where there is no /proc (Windows, macOS)."""
    if os.name == "nt":
        return None
    try:
        names = os.listdir("/proc")
    except OSError:
        return None
    out = {}
    for n in names:
        if not n.isdigit():
            continue
        try:
            raw = Path(f"/proc/{n}/stat").read_text()
        except OSError:
            continue  # just exited
        f = raw[raw.rindex(")") + 2:].split()
        out[int(n)] = (int(f[1]), int(f[2]), f[0])
    return out


def group_alive(pgid) -> bool | None:
    """POSIX: does process group pgid still hold a running process (zombies do not count)? True / False / None = cannot tell (also None on Windows)."""
    if os.name == "nt" or not isinstance(pgid, int) or isinstance(pgid, bool) or pgid <= 0:
        return None
    table = proc_table()
    if table is not None:
        return any(pg == pgid and st not in ("Z", "X") for _pp, pg, st in table.values())
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_start(pid: int) -> str | None:
    """A start-time token for the process (Windows: creation FILETIME; Linux: boot_id + starttime); None if absent or unreadable."""
    state, start = _probe_safe(pid)
    return start if state == "alive" else None


def process_alive(pid, start) -> bool | None:
    """True alive, False confirmed finished (including a PID reused by another process), None cannot tell (e.g. no permission)."""
    state, now = _probe_safe(pid)
    if state == "dead":
        return False
    if state == "unknown":
        return None
    if start and now and start != now:
        return False
    return True


def _entry_alive(p: dict) -> bool:
    """May a registered process still be alive (cannot-tell counts as alive)? One registered as a group (POSIX): even if
    it has finished, a running process in the same process group counts as alive - a descendant it started that may still write."""
    if process_alive(p.get("pid"), p.get("start")) is not False:
        return True
    return bool(p.get("group")) and os.name != "nt" and group_alive(p.get("pid")) is not False


def owner_alive(meta: dict) -> bool:
    """Is the owner process tree of this local claim still alive - the only entry point that decides whether a claim can be taken over.
    Looks at the owner itself and the child processes registered with it (Claim.add_process); any one alive or
    unconfirmable counts as alive. Age never matters. Only for local records: the caller must not send records from
    another machine here. Once the owner has released and only children remain (owner_released), only the children count.

    Process trees (process_runner): on Windows the child tree sits in a Job Object (KILL_ON_JOB_CLOSE), so when the owner
    dies the OS stops the whole tree and registering the child itself is enough; POSIX has no such guarantee, so the child
    is registered as a group (it leads a new session) and a running process in the group counts as alive."""
    owner = [] if meta.get("owner_released") else [{"pid": meta.get("pid"), "start": meta.get("start")}]
    procs = [*owner, *(meta.get("children") or [])]
    return any(_entry_alive(p) for p in procs)


# ---------- claim ----------

def _claim_paths(runtime: Path, kind: str, name: str, resources=()):
    if kind not in CLAIM_KINDS:
        raise StateError(f"claim kind must be one of {', '.join(CLAIM_KINDS)}: {kind}")
    if not (isinstance(name, str) and name.strip()):
        raise StateError("a claim needs a name")
    # Resource names compare ignoring case (COM5 and com5 are one resource); task claims keep exact case, because
    # component ids are case-sensitive (A1 and a1 are different components).
    key = name.strip().lower() if kind == "resource" else name.strip()
    if kind == "resource":
        allowed = {r.lower() for r in (*BUILTIN_RESOURCES, *resources)}
        if key.split(":", 1)[0] not in allowed:
            raise StateError(f"a named local resource must be one of {', '.join(sorted(allowed))} "
                             f"(e.g. integration-branch:app); more kinds come from `resources` in workflow.json: {name}")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)
    if safe != key or safe != safe.lower():  # upper case gets a hash too: file names must differ on case-insensitive filesystems
        safe = safe.lower() + "-" + _sha256(key.encode("utf-8"))[:8]
    d = Path(runtime) / "claims"
    return d / f"{kind}-{safe}.lock", d / f"{kind}-{safe}.json"


def _read_meta(path: Path):
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) else None


def _write_json(path: Path, obj) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))


def _held(kind, name, m, why):
    if not m:
        return ClaimHeld(f"{kind} {name} is held ({why})", m)
    kids = ", ".join(str(c.get("pid")) for c in m.get("children") or [])
    return ClaimHeld(f"{kind} {name} is held ({why}): {m.get('machine')} pid {m.get('pid')}"
                     + (f" (children {kids})" if kids else "")
                     + f", worktree {m.get('worktree')}, run {m.get('run_id')}, since {m.get('acquired_at')}", m)


class Claim:
    """A claim that was taken. The holder keeps the OS lock until release(); if the process is killed the OS drops the
    lock but the metadata stays, and owner_alive later decides whether the owner process tree really stopped."""

    def __init__(self, fd, meta_path, meta, previous):
        self._fd, self.meta_path, self.meta, self.previous = fd, meta_path, meta, previous

    def add_process(self, pid: int, *, group: bool = False) -> None:
        """Register a child process the owner started (process_runner's register hooks in here); while it lives the claim cannot be taken over.
        group=True: it leads a process group (a new POSIX session), and a running process in the group also counts as alive (see owner_alive)."""
        if self._fd is None:
            raise StateError("the claim is already released")
        entry = {"pid": int(pid), "start": process_start(int(pid))}
        if group:
            entry["group"] = True
        self.meta["children"].append(entry)
        _write_json(self.meta_path, self.meta)

    def release(self) -> bool:
        """Release the OS lock. Delete the metadata and return True only when every registered child is confirmed finished;
        otherwise keep the metadata (marked owner_released, from then on only the children count) and return False:
        nobody else can take the claim until those processes stop."""
        if self._fd is None:
            return True
        free = not any(_entry_alive(c) for c in self.meta["children"])
        try:
            if free:
                with contextlib.suppress(FileNotFoundError):
                    self.meta_path.unlink()
            else:
                self.meta["owner_released"] = True
                _write_json(self.meta_path, self.meta)
        finally:
            _unlock(self._fd)
            os.close(self._fd)
            self._fd = None
        return free

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.release()


def acquire_claim(runtime: Path, kind: str, name: str, *, run_id: str | None = None,
                  worktree: Path | None = None, resources=()) -> Claim:
    """Take a claim on a task (kind="task", name = component id) or a named local resource (kind="resource"; the kind part of
    name must be in BUILTIN_RESOURCES or in `resources`). Resource names compare ignoring case; task names keep exact case. A held claim raises ClaimHeld: the OS lock
    is still held, or the owner process tree in the metadata is alive or cannot be confirmed. A takeover needs the owner and its
    children confirmed finished (the old record is kept in Claim.previous); a record from another machine is not trusted and
    this machine's state decides. A claim is valid on this machine only: it cannot stop another machine; cross-machine
    takeover follows the handoff protocol."""
    lock_path, meta_path = _claim_paths(runtime, kind, name, resources)
    fd = _open_lock(lock_path)
    if not _try_lock(fd):
        os.close(fd)
        raise _held(kind, name, _read_meta(meta_path), "the owner process still holds the lock")
    try:
        _sweep_temps(meta_path)
        previous = None
        if meta_path.exists():
            old = _read_meta(meta_path)
            if old is None:
                raise ClaimHeld(f"{kind} {name}: cannot read the claim record {meta_path}; make sure no process uses it, delete it, then retry")
            if old.get("machine") == MACHINE and owner_alive(old):
                raise _held(kind, name, old, "the owner process tree is running or cannot be confirmed")
            previous = old  # local: confirmed stopped; from another machine: not imported, not trusted
        me = os.getpid()
        meta = {"schema_version": 1, "kind": kind, "name": name, "machine": MACHINE,
                "pid": me, "start": process_start(me), "children": [],
                "worktree": str(Path(worktree).resolve()) if worktree else None,
                "run_id": run_id or f"{datetime.now():%Y%m%dT%H%M%S}-{secrets.token_hex(3)}",
                "acquired_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        _write_json(meta_path, meta)
    except BaseException:
        _unlock(fd)
        os.close(fd)
        raise
    return Claim(fd, meta_path, meta, previous)


def list_claims(runtime: Path) -> list[dict]:
    """Claim records in the runtime (including stopped ones waiting for a takeover). Each gets "alive": local ones via owner_alive; None for another machine's or unreadable ones."""
    d = Path(runtime) / "claims"
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        m = _read_meta(f)
        if m is None:
            out.append({"kind": "?", "name": f.name, "alive": None, "broken": str(f)})
            continue
        out.append({**m, "alive": owner_alive(m) if m.get("machine") == MACHINE else None})
    return out
