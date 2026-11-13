"""Shared child-process runner: timeout / cancel, whole-tree cleanup, one directory per run. Stdlib only.

  run_process(argv, cwd, timeout_seconds, run_dir) -> ProcessResult
  ProcessResult = exit_code / timed_out / cancelled / process_tree_stopped / stdout_path / stderr_path

A dispatcher (implementer session), a session loop and a baseline check all go through here. Run directories live in
`state_io.runs_root(runtime)/<run id>` (`<git common dir>/specflow-runtime/runs/<run id>`).

Process tree ("stopped" is always checked, never assumed):
- Windows: a Job Object (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE). The child is created suspended and released only after it is
  in the job, so every descendant is born inside the job (breakaway is not allowed). Stopping = TerminateJobObject, then
  reading the job's ActiveProcesses down to 0. If the runner itself is killed the OS closes the job handle and the whole
  tree stops with it.
- POSIX: a new session (also a new process group). On Linux the runner also becomes a child subreaper, so descendants that
  leave the group and become orphans re-attach to the runner; stopping SIGKILLs the group members + descendants (walked by
  ppid) + re-attached orphans, then checks that none is left. If the runner itself is killed there is no OS guarantee: the
  caller registers the child as a group in a claim (state_io.owner_alive then looks at the whole group). A POSIX without
  /proc (macOS) looks at the process group only.

KNOWN LIMITATION (not fixed): on Windows, a process started from inside WSL that detaches itself from its group is not in the
Job Object (a WSL Linux process is not a Windows process; killing wsl.exe usually ends them, but this is not verified).
`process_tree_stopped` therefore only speaks for the Windows side of the tree. Orphan cleanup inside WSL is out of scope.

Run directory (written by run_process):
  stdout.log, stderr.log   child output (single use: refused if present; one run directory runs one process)
  process.json             process facts: argv, cwd, pid, start/end time, timeout, exit code, timed_out, cancelled,
                           process_tree_stopped, leftover_pids (still there after the main process ended, then killed),
                           survivors (could not be killed)
  checkpoint.json          written only on timeout or cancel: what ran, how far, where the logs are, whether the tree stopped;
                           such a run does not count as a success
  cancel.request           cancel request (request_cancel or a cancel command writes it); the runner looks every 0.2 s
  tmp/                     the child's TEMP / TMP / TMPDIR (child_env)
manifest.json / result.json / prompt.md / report.json are written by the caller.

Exit 0 is only a process fact and does not mean the task is done; the caller decides that from the evidence in the run directory.
"""
import contextlib
import importlib.util
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _load_sibling(name: str, module_name: str):
    """Load a sibling script by file path under a unique module name (the scripts folder is not a package, and a product
    adapter may itself be importable as `state_io`; the core must never bind to that). One copy per process: a module
    already loaded under module_name from this very file is reused (so every core module shares its classes and KEEP);
    one from another path (e.g. another plugin version) is not. A failed load leaves sys.modules as it was."""
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


state_io = _load_sibling("state_io", "specflow_state_io")  # process liveness, /proc, write-once evidence

STDOUT, STDERR = "stdout.log", "stderr.log"
PROCESS, CHECKPOINT, CANCEL = "process.json", "checkpoint.json", "cancel.request"
TMP_DIR = "tmp"
POLL = 0.2         # seconds; how often to check for child exit, cancel request, timeout
STOP_WAIT = 15.0   # seconds; after killing, wait at most this long to confirm the whole tree stopped
# git location variables a child must not inherit (e.g. when run from a git hook): otherwise its git commands act on another repo
GIT_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                     "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR")


class RunnerError(Exception):
    """code: 2 bad input, 3 run directory already used, 5 cannot start the program or this machine cannot manage a process tree."""

    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None        # the child's exit code; when killed it is the kill's value, see timed_out / cancelled
    timed_out: bool
    cancelled: bool
    process_tree_stopped: bool   # whole tree confirmed stopped (checked); False = something could not be killed, the claim must keep blocking
    stdout_path: Path
    stderr_path: Path


# ---------- run directory ----------

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_run_id(label: str) -> str:
    """Time + label (e.g. a component id) + random; different runs never collide."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("_") or "run"
    return f"{datetime.now():%Y%m%dT%H%M%S}-{safe}-{secrets.token_hex(3)}"


def create_run_dir(runs_root: Path, run_id: str) -> Path:
    """Create a new run directory; refuse if it exists (RunnerError 3): an old run's output is never taken for this run's."""
    runs_root = Path(runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)
    d = runs_root / run_id
    try:
        d.mkdir()
    except FileExistsError:
        raise RunnerError(f"run directory {d} already exists: every run needs a new id", 3)
    return d


def write_once(path: Path, obj) -> dict:
    """JSON evidence in a run directory is written once (state_io.write_evidence: no overwrite, no half file); returns {"path","sha256"}."""
    path = Path(path)
    return state_io.write_evidence(path.parent, path.name,
                                   (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


def child_env(run_dir: Path, base: dict | None = None) -> dict:
    """The child's environment: TEMP / TMP / TMPDIR point at this run's tmp/, git location variables are removed, and
    SPECFLOW_RUN_ID / SPECFLOW_RUN_DIR are set. This only keeps temp files and git commands in their own place; it is not a sandbox."""
    run_dir = Path(run_dir)
    tmp = run_dir / TMP_DIR
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ if base is None else base)
    for k in GIT_LOCATION_VARS:
        env.pop(k, None)
    env.update(TEMP=str(tmp), TMP=str(tmp), TMPDIR=str(tmp), SPECFLOW_RUN_ID=run_dir.name, SPECFLOW_RUN_DIR=str(run_dir))
    return env


def request_cancel(run_dir: Path) -> Path:
    """Ask a running run to stop (the runner kills the whole tree and writes a checkpoint). Returns the request file path."""
    p = Path(run_dir) / CANCEL
    p.write_text(_now() + "\n", encoding="utf-8")
    return p


# ---------- process tree ----------

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD)]
    _k32.QueryInformationJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.restype = ctypes.c_long

    _CREATE_SUSPENDED, _CREATE_NEW_PROCESS_GROUP = 0x00000004, 0x00000200
    _KILL_ON_JOB_CLOSE = 0x00002000
    _BASIC_ACCOUNTING, _BASIC_PID_LIST, _EXTENDED_LIMIT = 1, 3, 9
    _PROCESS_TERMINATE, _PROCESS_SET_QUOTA, _PROCESS_SUSPEND_RESUME = 0x0001, 0x0100, 0x0800

    class _BasicLimit(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", ctypes.c_ulonglong * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _Accounting(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    class _PidList(ctypes.Structure):
        _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD), ("NumberOfProcessIdsInList", wintypes.DWORD),
                    ("ProcessIdList", ctypes.c_size_t * 1024)]

    def _win_error(what):
        return RunnerError(f"{what} failed (Windows error {ctypes.get_last_error()}): this machine cannot manage a process tree with a Job Object, so nothing is dispatched", 5)

    def _new_job():
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            raise _win_error("CreateJobObject")
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        if not _k32.SetInformationJobObject(job, _EXTENDED_LIMIT, ctypes.byref(info), ctypes.sizeof(info)):
            err = _win_error("SetInformationJobObject")
            _k32.CloseHandle(job)
            raise err
        return job

    def _assign_and_resume(job, pid):
        h = _k32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_SET_QUOTA | _PROCESS_SUSPEND_RESUME, False, pid)
        if not h:
            raise _win_error("OpenProcess")
        try:
            if not _k32.AssignProcessToJobObject(job, h):
                raise _win_error("AssignProcessToJobObject")
            if _ntdll.NtResumeProcess(h) != 0:
                raise RunnerError("failed to release the suspended child (NtResumeProcess)", 5)
        finally:
            _k32.CloseHandle(h)


class ProcessTree:
    """A child process and all its descendants (mechanism: see the module docstring). members() / stop() results are actually checked."""

    mechanism = "job_object" if os.name == "nt" else "process_group"

    def __init__(self, args, *, cwd, stdin, stdout, stderr, env=None, shell=False):
        kw = dict(cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr, env=env, shell=shell)
        self._job = None
        if os.name == "nt":
            self._job = _new_job()
            try:
                self.popen = subprocess.Popen(args, creationflags=_CREATE_SUSPENDED | _CREATE_NEW_PROCESS_GROUP, **kw)
            except BaseException:
                self.close()
                raise
            try:
                _assign_and_resume(self._job, self.popen.pid)
            except BaseException:
                self.popen.kill()
                self.popen.wait()
                self.close()
                raise
        else:
            _become_subreaper()
            table = state_io.proc_table() or {}
            me = os.getpid()
            self._others = {pid for pid, (pp, _pg, _st) in table.items() if pp == me}  # children the runner already had
            self.popen = subprocess.Popen(args, start_new_session=True, **kw)
        self.pid = self.popen.pid

    def wait(self, timeout):
        """Wait up to timeout seconds for the child to end; return its exit code, or None if it has not ended."""
        try:
            return self.popen.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def members(self) -> list[int]:
        """Pids of tree members that have not ended (including the child itself)."""
        if os.name == "nt":
            acct = _Accounting()
            if not _k32.QueryInformationJobObject(self._job, _BASIC_ACCOUNTING, ctypes.byref(acct),
                                                  ctypes.sizeof(acct), None):
                return [-1]  # cannot query: assume someone is left (never assume stopped)
            if acct.ActiveProcesses == 0:
                return []
            lst = _PidList()
            _k32.QueryInformationJobObject(self._job, _BASIC_PID_LIST, ctypes.byref(lst), ctypes.sizeof(lst), None)
            pids = [int(p) for p in lst.ProcessIdList[:lst.NumberOfProcessIdsInList]]
            return sorted(p for p in pids if state_io.process_alive(p, None) is not False) or [-1]
        table = state_io.proc_table()
        if table is None:  # no /proc: process group only
            return [self.pid] if state_io.group_alive(self.pid) else []
        me, kids = os.getpid(), {}
        for pid, (pp, _pg, _st) in table.items():
            kids.setdefault(pp, []).append(pid)
        seeds = {self.pid} | {pid for pid, (pp, pg, _st) in table.items()
                              if pg == self.pid or (pp == me and pid not in self._others)}
        seen, todo = set(), list(seeds)
        while todo:
            pid = todo.pop()
            if pid in seen or pid not in table:
                continue
            seen.add(pid)
            todo += kids.get(pid, [])
        return sorted(p for p in seen if table[p][2] not in ("Z", "X"))

    def stop(self, wait=STOP_WAIT) -> list[int]:
        """Kill the whole tree until it is checked to be fully stopped (at most `wait` seconds); return the pids still alive after the check (empty = confirmed stopped)."""
        end = time.monotonic() + (STOP_WAIT if wait is None else wait)
        while True:
            left = self.members()
            if os.name == "nt":
                _k32.TerminateJobObject(self._job, 1)
            else:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(self.pid, signal.SIGKILL)
                for pid in left:
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.popen.wait(0.1)
            self._reap_orphans()
            left = self.members()
            if not left:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.popen.wait(5)  # tree checked empty: the child itself has ended; reap it for the exit code
                return left
            if time.monotonic() >= end:
                return left
            time.sleep(0.1)

    def _reap_orphans(self):
        """Linux: reap orphans that re-attached to the runner once they ended (never touch Popen's own child, so it can still report its exit code)."""
        table = state_io.proc_table() if os.name != "nt" else None
        for pid, (pp, _pg, st) in (table or {}).items():
            if pp == os.getpid() and st == "Z" and pid != self.pid and pid not in self._others:
                with contextlib.suppress(ChildProcessError, OSError):
                    os.waitpid(pid, os.WNOHANG)

    def close(self):
        if self._job:
            _k32.CloseHandle(self._job)  # KILL_ON_JOB_CLOSE: whatever is still in the job stops too
            self._job = None


_SUBREAPER = False


def _become_subreaper():
    """Linux: make the runner a child subreaper, so descendants that become orphans re-attach to the runner (not lost under init)."""
    global _SUBREAPER
    if _SUBREAPER or not sys.platform.startswith("linux"):
        return
    import ctypes
    with contextlib.suppress(OSError, AttributeError):
        ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    _SUBREAPER = True


# ---------- running ----------

def _check_input(argv, timeout_seconds, shell):
    if not (isinstance(argv, list) and argv and argv[0] and all(isinstance(a, str) for a in argv)):
        raise RunnerError("argv must be a list of strings, the first being the program", 2)
    if shell and len(argv) != 1:
        raise RunnerError("with shell=True argv holds exactly one command string", 2)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise RunnerError(f"timeout_seconds must be a positive number: {timeout_seconds!r}", 2)


def run_process(argv: list[str], cwd: Path, timeout_seconds: int, run_dir: Path, *,
                stdin_path: Path | None = None, env: dict | None = None, register=None,
                shell: bool = False) -> ProcessResult:
    """Run one program (the whole tree) in run_dir: stdout / stderr go to run_dir; on timeout or cancel the whole tree is killed and a checkpoint is written.

    stdin_path: the child's stdin (e.g. a prompt file); empty if not given. env: the full environment (normally child_env(run_dir)).
    register(pid): called as soon as the child starts (the caller registers it in a claim); when the tree cannot be confirmed
    stopped, each survivor is passed to it too.
    shell=True: argv holds exactly one shell command string (the original quoting is kept).
    Cancel: run_dir/cancel.request appears (request_cancel) or Ctrl+C. Descendants left behind after the main process ends are killed and recorded in process.json.
    """
    _check_input(argv, timeout_seconds, shell)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    out_p, err_p = run_dir / STDOUT, run_dir / STDERR
    try:
        fo = open(out_p, "xb")
    except FileExistsError:
        raise RunnerError(f"{run_dir} already ran a process: use a new directory for every run", 3)
    fe = open(err_p, "xb")
    started, t0 = _now(), time.monotonic()
    reason, leftover, survivors = None, [], []
    with fo, fe, (open(stdin_path, "rb") if stdin_path else contextlib.nullcontext(subprocess.DEVNULL)) as fi:
        try:
            tree = ProcessTree(argv[0] if shell else argv, cwd=cwd, stdin=fi, stdout=fo, stderr=fe, env=env, shell=shell)
        except OSError as e:
            raise RunnerError(f"cannot start {argv[0]}: {e}", 5)
        try:
            if register:
                register(tree.pid)
            deadline = t0 + timeout_seconds
            try:
                while tree.wait(POLL) is None:
                    if (run_dir / CANCEL).exists():
                        reason = "cancelled"
                        break
                    if time.monotonic() >= deadline:
                        reason = "timeout"
                        break
            except KeyboardInterrupt:
                reason = "cancelled"
            if reason is None:
                leftover = tree.members()  # the main process ended but descendants remain: stop them too
            if reason or leftover:
                survivors = tree.stop()
        except BaseException:
            tree.stop()
            tree.close()
            raise
        tree.close()
    stopped = not survivors
    if register:
        for pid in survivors:
            if pid > 0:
                register(pid)
    rc = tree.popen.returncode
    record = {"argv": argv, "shell": shell, "cwd": str(cwd), "pid": tree.pid, "mechanism": tree.mechanism,
              "started_at": started, "ended_at": _now(), "elapsed_seconds": round(time.monotonic() - t0, 1),
              "timeout_seconds": timeout_seconds, "exit_code": rc, "timed_out": reason == "timeout",
              "cancelled": reason == "cancelled", "process_tree_stopped": stopped,
              "leftover_pids": leftover, "survivors": survivors, "stdout": STDOUT, "stderr": STDERR}
    write_once(run_dir / PROCESS, record)
    if reason:
        write_once(run_dir / CHECKPOINT, {
            "reason": reason, "run_dir": str(run_dir), "argv": argv, "cwd": str(cwd), "pid": tree.pid,
            "started_at": started, "stopped_at": record["ended_at"], "elapsed_seconds": record["elapsed_seconds"],
            "timeout_seconds": timeout_seconds, "logs": {"stdout": str(out_p), "stderr": str(err_p)},
            "process_tree_stopped": stopped, "survivors": survivors,
            "next": "this run did not finish; its output and report do not count as success; read the logs to decide where to resume, and start a new run to redispatch"})
    return ProcessResult(rc, reason == "timeout", reason == "cancelled", stopped, out_p, err_p)
