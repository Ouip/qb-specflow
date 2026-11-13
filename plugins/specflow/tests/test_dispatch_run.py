"""Tests for dispatch_run.py (pre-dispatch gate, implementer in a git worktree, verdict from evidence only). Run:
python -m unittest discover -s plugins/specflow/tests -p "test_dispatch_run.py"   (stdlib only)

Everything runs in temp repos. The implementer is a fake CLI (a Python script behind a .cmd / sh wrapper) that edits files
in its worktree, writes (or does not write) a report, hangs, or leaves a stray process behind. No real tool is started.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name, module_name):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


dr = _load("dispatch_run", "specflow_dispatch_run")
S, R, WC = dr.state_io, dr.process_runner, dr.workflow_config

TEMPLATE = ["fakeimpl", "--cd", "{worktree}", "-o", "{report}", "--schema", "{schema}", "-m", "{model}"]
POLICY = {
    "schema_version": 1,
    "repos": {"docs": {"integration_branch": "main", "home": True}, "app": {"integration_branch": "main"}},
    "tools": {"git": {"version_args": ["--version"]}, "fakeimpl": {"version_args": ["--version"]}},
    "capabilities": {"dispatch": {"tools": ["git", "fakeimpl"], "repos": ["app"]}},
    "roles": {"implementer": {"tool": "fakeimpl", "model": "m1", "effort": "medium"}},
    "dispatch": {
        "timeout_seconds": 60, "requires_lock": False, "requires_capabilities": [],
        "implementer": {"argv": TEMPLATE, "extra_allowed": {"--ephemeral": False, "--image": True, "-i": True}},
        "report": {"schema": "report.schema.json", "task_field": "task", "status_field": "status",
                   "done_requires": [{"field": "unmet", "empty": True}, {"field": "tests", "in": ["pass", "na"]}]}},
}
SCHEMA = {
    "type": "object", "required": ["task", "status", "tests", "unmet", "notes"], "additionalProperties": False,
    "properties": {
        "task": {"type": "string"},
        "status": {"type": "string", "enum": ["done", "partial", "blocked"]},
        "tests": {"type": "string", "enum": ["pass", "fail", "na"]},
        "unmet": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "count": {"type": "integer", "minimum": 0},
    },
}
LOCK = {
    "schema_version": 1,
    "core": {"plugin": "specflow@mkt", "version": "0.1.0", "commit": "a" * 40},
    "plugins": {"specflow@mkt": {"marketplace": "mkt", "source": {"type": "github"}, "version": "0.1.0",
                                 "commit": "a" * 40, "required": True}},
}

FAKE = r'''
import json, os, subprocess, sys, time
from pathlib import Path
argv, log = sys.argv[1:], Path(os.environ["FAKE_LOG"])
if "--version" in argv:
    print("fakeimpl 1.0")
    sys.exit(0)
mode = os.environ.get("FAKE_MODE", "ok")


def opt(name):
    return argv[argv.index(name) + 1] if name in argv else None


prompt = sys.stdin.read()
(log / "args.json").write_text(json.dumps({"argv": argv, "cwd": os.getcwd(), "prompt": prompt,
    "temp": os.environ.get("TEMP"), "pid": os.getpid()}, ensure_ascii=False), encoding="utf-8")
wt, out, schema = Path(opt("--cd")), Path(opt("-o")), Path(opt("--schema"))
REPORT = {"task": "A1", "status": "done", "tests": "pass", "unmet": [], "notes": "did it"}


def report(**over):
    out.write_text(json.dumps({**REPORT, **over}, ensure_ascii=False), encoding="utf-8")


def edit(rel, text="int changed;\n"):
    p = wt / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def grandchild(marker="", detach=False):
    code = ("import pathlib, sys, time\n"
            "if sys.argv[1]:\n"
            "    time.sleep(3)\n"
            "    pathlib.Path(sys.argv[1]).write_text('alive')\n"
            "time.sleep(300)\n")
    kw = {}
    if detach:
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200
        else:
            kw["start_new_session"] = True
    g = subprocess.Popen([sys.executable, "-c", code, marker], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    (log / "grand").write_text(str(g.pid))


if mode == "ok":
    edit("src/a1/a.c")
    edit("src/a1/b.c")
    report()
elif mode == "none":
    edit("src/a1/a.c")
elif mode == "wrong_task":
    edit("src/a1/a.c")
    report(task="A2")
elif mode == "broken":
    out.write_text("{ broken JSON", encoding="utf-8")
elif mode == "missing":
    r = dict(REPORT)
    del r["notes"]
    out.write_text(json.dumps(r), encoding="utf-8")
elif mode in ("partial", "blocked"):
    report(status=mode, unmet=["X not done"])
elif mode == "contradict":
    report(tests="fail")
elif mode == "unmet":
    report(unmet=["X not done"])
elif mode == "frozen":
    edit("include/a1.h")
    report()
elif mode == "outside":
    edit("docs/x.md")
    report()
elif mode == "rc1":
    report()
    sys.exit(1)
elif mode == "tamper_run_schema":
    # the run directory copy of the schema is writable: loosen it, then write a report the real schema refuses
    schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    edit("src/a1/a.c")
    out.write_text(json.dumps({"task": "A1", "status": "done"}), encoding="utf-8")
elif mode == "change_product_schema":
    # stricter now: the judge must still use the schema read at the start, so this valid report stays done
    Path(os.environ["FAKE_PRODUCT_SCHEMA"]).write_text(json.dumps({"type": "object", "required": ["extra"]}),
                                                       encoding="utf-8")
    edit("src/a1/a.c")
    report()
elif mode == "nulls":
    edit("src/a1/a.c")
    report(notes=None, unmet=None)
elif mode == "notes_int":
    edit("src/a1/a.c")
    report(notes=5)
elif mode == "hang":
    grandchild(os.environ.get("FAKE_MARKER", ""))
    (log / "ready").write_text(str(os.getpid()))
    time.sleep(300)
elif mode == "leftover":
    grandchild(detach=True)
    edit("src/a1/a.c")
    report()
elif mode in ("sneak", "detach"):
    G = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "-C", str(wt)]
    edit("src/a1/a.c")
    if mode == "sneak":
        # commit a frozen file on the run branch, then put the base content back in the worktree only
        edit("include/a1.h")
        subprocess.run([*G, "add", "-A"], check=True, capture_output=True)
        subprocess.run([*G, "commit", "-q", "-m", "x"], check=True, capture_output=True)
        subprocess.run([*G, "checkout", "HEAD~1", "--", "include/a1.h"], check=True, capture_output=True)
    else:
        subprocess.run([*G, "checkout", "-q", "--detach"], check=True, capture_output=True)
    report()
elif mode == "move_base":
    # worktrees share refs: commit a frozen change, move the start ref comp/a1 onto it, put the run branch back
    G = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "-C", str(wt)]
    edit("include/a1.h")
    subprocess.run([*G, "add", "-A"], check=True, capture_output=True)
    subprocess.run([*G, "commit", "-q", "-m", "x"], check=True, capture_output=True)
    subprocess.run([*G, "update-ref", "refs/heads/comp/a1", "HEAD"], check=True, capture_output=True)
    subprocess.run([*G, "reset", "-q", "--hard", "HEAD~1"], check=True, capture_output=True)
    edit("src/a1/a.c")
    report()
'''


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def make_cli(d, name, script):
    d.mkdir(parents=True, exist_ok=True)
    py = d / f"{name}.py"
    py.write_text(script, encoding="utf-8")
    if os.name == "nt":
        cli = d / f"{name}.cmd"
        cli.write_text(f'@echo off\r\n"{sys.executable}" "{py}" %*\r\nexit /b %ERRORLEVEL%\r\n', encoding="utf-8")
    else:
        cli = d / name
        cli.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{py}" "$@"\n', encoding="utf-8")
        cli.chmod(0o755)
    return cli


def alive(pid):
    return S.process_alive(int(pid), None) is not False


def kill_pid(pid):
    try:
        os.kill(int(pid), signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
    except (OSError, ValueError):
        pass


def comp(cid, name, repo="app", status="in_progress"):
    return {"id": cid, "name": name, "spec": f"{cid}.md", "repo": repo, "deps": [], "status": status, "blocked": None}


class Base(unittest.TestCase):
    """tmp/'home repo' (git) holds the product folder 'spec'; tmp/'app repo' (git) is the code repo with branch comp/a1;
    this machine's mapping is home repo root/specflow.local.json."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name)
        self.home = self.tmp / "home repo"
        self.spec = self.home / "spec"
        self.spec.mkdir(parents=True)
        git(self.home, "init", "-q", "-b", "main")
        self.app = self.tmp / "app repo"
        self.app.mkdir()
        git(self.app, "init", "-q", "-b", "main")
        for rel, text in {"src/a1/a.c": "int a;\n", "include/a1.h": "#pragma once\n", "docs/readme.md": "r\n",
                          ".gitignore": "build/\n"}.items():
            (self.app / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.app / rel).write_text(text, encoding="utf-8")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-q", "-m", "init")
        git(self.app, "branch", "comp/a1")
        self.base = git(self.app, "rev-parse", "comp/a1")
        (self.home / "specflow.local.json").write_text(
            json.dumps({"schema_version": 1, "repos": {"app": {"path": str(self.app)}}}), encoding="utf-8")
        self.policy = json.loads(json.dumps(POLICY))
        self.save_policy()
        (self.spec / "report.schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
        (self.spec / "workflow.lock.json").write_text(json.dumps(LOCK), encoding="utf-8")
        (self.spec / "specflow.md").write_text("---\nformat: 2\ncore: 0.1.0\n---\n", encoding="utf-8")
        (self.spec / "components.json").write_text(json.dumps({
            "components": [comp("A1", "a1"), comp("A2", "a2")], "repos": {}}), encoding="utf-8")
        self.cli = make_cli(self.tmp / "bin", "fakeimpl", FAKE)
        self.log = self.tmp / "log"
        self.log.mkdir()
        self.prompt = self.tmp / "prompt.md"
        self.prompt.write_text("Read the spec and implement. Only change src/a1/**.", encoding="utf-8")
        env = mock.patch.dict(os.environ, {"FAKE_LOG": str(self.log), "FAKE_MODE": "ok"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(lambda: [kill_pid(f.read_text()) for f in self.log.glob("grand")])

    # ---- helpers
    def save_policy(self):
        (self.spec / "workflow.json").write_text(json.dumps(self.policy), encoding="utf-8")

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dr.main(["--product", str(self.spec), *[str(a) for a in argv]])
        return rc, out.getvalue() + err.getvalue()

    def dispatch(self, *extra, mode="ok", allow=("src/a1/**",), frozen=("include/**",), timeout=None, task="A1",
                 more=(), tool=True):
        os.environ["FAKE_MODE"] = mode
        for f in ("args.json", "ready", "grand"):
            (self.log / f).unlink(missing_ok=True)
        argv = ["run", task, "--prompt-file", self.prompt, *more]
        if tool:
            argv += ["--tool", self.cli]
        for a in allow:
            argv += ["--allow", a]
        for f in frozen:
            argv += ["--frozen", f]
        if timeout:
            argv += ["--timeout", timeout]
        if extra:
            argv += ["--", *extra]
        return self.main(*argv)

    @property
    def runs_dir(self):
        return S.runs_root(S.runtime_root(self.spec))

    @property
    def runtime(self):
        return S.runtime_root(self.spec)

    def runs(self):
        return sorted(d for d in self.runs_dir.iterdir() if d.is_dir()) if self.runs_dir.exists() else []

    def last(self):
        run = max(self.runs(), key=lambda d: (d / "manifest.json").stat().st_mtime_ns)
        return (run, json.loads((run / "manifest.json").read_text(encoding="utf-8")),
                json.loads((run / "result.json").read_text(encoding="utf-8")))

    def args(self):
        return json.loads((self.log / "args.json").read_text(encoding="utf-8"))


class DispatchTest(Base):
    def test_done_report_in_scope_is_done_and_manifest_binds_the_run(self):
        before = git(self.app, "status", "--porcelain")
        rc, out = self.dispatch()
        self.assertEqual(rc, 0, out)
        self.assertEqual(git(self.app, "status", "--porcelain"), before)  # the main checkout is untouched
        self.assertEqual(before, "")
        run, m, r = self.last()
        wt = Path(m["worktree"]["path"])
        self.assertEqual((m["run_id"], m["kind"], m["task_id"]), (run.name, "dispatch", "A1"))
        self.assertEqual(m["source"], {"repo_id": "app", "repo_path": str(self.app), "base_ref": "comp/a1",
                                       "base_sha": self.base})
        self.assertEqual(m["worktree"]["branch"], f"run/{run.name}")
        self.assertEqual(wt, self.tmp / "wt" / run.name)
        self.assertIn("not an OS sandbox", m["worktree"]["isolation"])
        self.assertIn("ignored", " ".join(m["limits"]))
        self.assertIn("outside the worktree", " ".join(m["limits"]))
        self.assertIn("WSL", " ".join(m["limits"]))
        self.assertEqual(m["scope"], {"allowed": ["src/a1/**"], "frozen": ["include/**"]})
        self.assertEqual((m["tool"]["name"], m["tool"]["model"], m["tool"]["effort"]), ("fakeimpl", "m1", "medium"))
        self.assertEqual(m["timeout_seconds"], 60)
        self.assertEqual(m["inputs"]["prompt"]["sha256"], S.file_revision(self.prompt))
        self.assertEqual(m["inputs"]["schema"]["sha256"], hashlib.sha256((self.spec / "report.schema.json").read_bytes()).hexdigest())
        self.assertEqual((r["verdict"], r["exit_code"], r["problems"]), ("done", 0, []))
        self.assertEqual(r["changed_files"], ["src/a1/a.c", "src/a1/b.c"])
        self.assertEqual(r["report"]["sha256"], S.file_revision(run / "report.json"))
        self.assertEqual((r["report"]["task"], r["report"]["status"]), ("A1", "done"))
        # the tool got exactly the template, filled in
        argv = self.args()["argv"]
        self.assertEqual(argv, ["--cd", str(wt), "-o", str(run / "report.json"), "--schema", str(run / "result.schema.json"),
                                "-m", "m1"])
        self.assertEqual(m["argv"][1:], argv)
        self.assertEqual(Path(self.args()["cwd"]).resolve(), wt.resolve())
        self.assertEqual(self.args()["prompt"], self.prompt.read_text(encoding="utf-8"))
        self.assertEqual(Path(self.args()["temp"]), run / "tmp")
        self.assertTrue(wt.is_dir())  # no automatic cleanup
        self.assertEqual(S.list_claims(self.runtime), [])
        self.assertIn("done", out)

    def assert_not_done(self, mode, code, verdict, needle):
        rc, out = self.dispatch(mode=mode)
        run, m, r = self.last()
        self.assertEqual((rc, r["exit_code"], r["verdict"]), (code, code, verdict), out)
        self.assertTrue(any(needle in p for p in r["problems"]), r["problems"])
        self.assertEqual(S.list_claims(self.runtime), [])
        return r

    def test_exit_zero_without_report_is_not_done(self):
        self.assert_not_done("none", 4, "no_evidence", "no report file")

    def test_wrong_task_in_report(self):
        self.assert_not_done("wrong_task", 4, "no_evidence", "A2")

    def test_broken_json_report(self):
        self.assert_not_done("broken", 4, "no_evidence", "JSON")

    def test_report_missing_required_field(self):
        self.assert_not_done("missing", 4, "no_evidence", "notes")

    def test_done_that_contradicts_itself_by_config(self):
        self.assert_not_done("contradict", 4, "no_evidence", "tests")
        self.assert_not_done("unmet", 4, "no_evidence", "unmet")

    def test_nonzero_exit_with_done_report(self):
        self.assert_not_done("rc1", 4, "no_evidence", "exit code is 1")

    def test_partial_and_blocked_are_not_done(self):
        for mode in ("partial", "blocked"):
            with self.subTest(mode=mode):
                rc, out = self.dispatch(mode=mode)
                _, _, r = self.last()
                self.assertEqual((rc, r["verdict"], r["problems"]), (4, mode, []), out)
                self.assertEqual(r["report"]["status"], mode)

    def test_frozen_path_change_is_scope_violation(self):
        r = self.assert_not_done("frozen", 3, "scope_violation", "include/a1.h")
        self.assertEqual(r["scope_violations"], ["include/a1.h (frozen)"])

    def test_frozen_change_on_the_run_branch_restored_in_the_worktree_is_scope_violation(self):
        """The scope check covers what a person merges (branch run/<run id>), not only the worktree."""
        r = self.assert_not_done("sneak", 3, "scope_violation", "include/a1.h")
        self.assertEqual(r["scope_violations"], ["include/a1.h (frozen)"])
        self.assertEqual(r["changed_files"], ["include/a1.h", "src/a1/a.c"])

    def test_frozen_directory_forms_freeze_the_directory(self):
        for g in ("include/", "include", "./include/**", "./include"):
            with self.subTest(g=g):
                rc, out = self.dispatch(mode="frozen", frozen=(g,))
                _, m, r = self.last()
                self.assertEqual((rc, r["verdict"]), (3, "scope_violation"), out)
                self.assertEqual(r["scope_violations"], ["include/a1.h (frozen)"])
                self.assertEqual(m["scope"]["frozen"], ["include/**"])

    def test_frozen_glob_matching_no_base_file_is_2_and_creates_nothing(self):
        for g in ("Include/**", "tests/scenarios/", "include/*.c", "./"):
            with self.subTest(g=g):
                rc, out = self.dispatch(frozen=(g,))
                self.assertEqual(rc, 2, out)
                self.assertIn(g, out)
        self.assertEqual(self.dispatch(frozen=("nope/**",), more=("--dry-run",))[0], 2)
        self.assertEqual(self.runs(), [])
        self.assertFalse((self.tmp / "wt").exists())
        self.assertEqual(S.list_claims(self.runtime), [])
        self.assertEqual(git(self.app, "branch", "--list", "run/*"), "")
        self.assertFalse((self.log / "args.json").exists())

    def test_start_ref_moved_during_the_run_is_scope_violation(self):
        """A commit moved onto the start ref (shared with the repo) is in no diff of the run, but it would be merged."""
        r = self.assert_not_done("move_base", 3, "scope_violation", "comp/a1")
        self.assertEqual(r["changed_files"], ["src/a1/a.c"])
        self.assertEqual(len(r["scope_violations"]), 1, r["scope_violations"])
        self.assertIn(self.base[:12], r["scope_violations"][0])

    def test_base_head_watches_the_branch_it_names_not_the_worktree_head(self):
        """--base HEAD means the source repo's current branch: the worktree's own HEAD (the run branch tip, which moves
        with every allowed commit) is not the start ref."""
        git(self.app, "checkout", "-q", "comp/a1")
        rc, out = self.dispatch(mode="sneak", frozen=(), allow=("src/a1/**", "include/**"), more=("--base", "HEAD"))
        self.assertEqual((rc, self.last()[2]["verdict"]), (0, "done"), out)
        rc, out = self.dispatch(mode="move_base", more=("--base", "HEAD"))  # comp/a1 itself moved: still caught
        _, _, r = self.last()
        self.assertEqual((rc, r["verdict"]), (3, "scope_violation"), out)
        self.assertTrue(any("refs/heads/comp/a1" in v for v in r["scope_violations"]), r["scope_violations"])

    def test_start_without_a_ref_is_not_watched(self):
        """A bare SHA or a detached HEAD names no ref that a merge could take a moved commit from."""
        git(self.app, "checkout", "-q", "--detach", "comp/a1")
        for base in (self.base, "HEAD"):
            with self.subTest(base=base):
                rc, out = self.dispatch(mode="sneak", frozen=(), allow=("src/a1/**", "include/**"),
                                        more=("--base", base))
                self.assertEqual((rc, self.last()[2]["verdict"]), (0, "done"), out)

    def test_worktree_head_off_the_run_branch_is_scope_violation(self):
        r = self.assert_not_done("detach", 3, "scope_violation", "run/")
        self.assertEqual(r["changed_files"], ["src/a1/a.c"])

    def test_untracked_file_outside_allowed_is_scope_violation(self):
        r = self.assert_not_done("outside", 3, "scope_violation", "docs/x.md")
        self.assertEqual(r["scope_violations"], ["docs/x.md (outside the allowed scope)"])

    def test_old_reports_are_never_reused(self):
        self.assertEqual(self.dispatch(mode="ok")[0], 0)
        first, _, _ = self.last()
        (self.app / ".reports").mkdir()  # a fixed report file from an older flow must not count for this run
        (self.app / ".reports" / "A1.last.json").write_text((first / "report.json").read_text(encoding="utf-8"), encoding="utf-8")
        rc, out = self.dispatch(mode="none")
        second, m, r = self.last()
        self.assertEqual((rc, r["verdict"]), (4, "no_evidence"), out)
        self.assertNotEqual(first, second)
        self.assertFalse((second / "report.json").exists())

    def test_hung_tool_times_out_tree_killed_no_orphans(self):
        marker = self.tmp / "marker.txt"
        with mock.patch.dict(os.environ, {"FAKE_MARKER": str(marker)}):
            rc, out = self.dispatch(mode="hang", timeout=1)
        run, m, r = self.last()
        self.assertEqual((rc, r["verdict"]), (6, "timeout"), out)
        self.assertEqual(m["timeout_seconds"], 1)
        self.assertFalse(alive(self.args()["pid"]))
        self.assertFalse(alive((self.log / "grand").read_text()))
        time.sleep(3.5)
        self.assertFalse(marker.exists())  # the grandchild did not go on writing (it would after 3 s)
        cp = json.loads((run / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual((cp["reason"], cp["process_tree_stopped"]), ("timeout", True))
        self.assertEqual(S.list_claims(self.runtime), [])

    def test_cancel_stops_dispatch_and_writes_checkpoint(self):
        res = {}
        th = threading.Thread(target=lambda: res.setdefault("rc", self.dispatch(mode="hang")))
        th.start()
        try:
            end = time.monotonic() + 30
            while not (self.log / "ready").exists() and time.monotonic() < end:
                time.sleep(0.05)
            (run,) = self.runs()
            self.assertEqual(self.main("cancel", run.name)[0], 0)
        finally:
            th.join(60)
        rc, out = res["rc"]
        _, _, r = self.last()
        self.assertEqual((rc, r["verdict"]), (6, "cancelled"), out)
        self.assertFalse(alive((self.log / "grand").read_text()))
        self.assertEqual(json.loads((run / "checkpoint.json").read_text(encoding="utf-8"))["reason"], "cancelled")

    def test_leftover_process_is_killed_and_recorded(self):
        rc, out = self.dispatch(mode="leftover")
        _, _, r = self.last()
        grand = int((self.log / "grand").read_text())
        self.assertEqual((rc, r["verdict"]), (0, "done"), out)
        self.assertFalse(alive(grand))
        self.assertIn(grand, r["leftover_pids"])
        self.assertTrue(r["process"]["process_tree_stopped"])

    def test_unconfirmed_tree_stop_is_3(self):
        real = R.run_process

        def fake(*a, **k):
            res = real(*a, **k)
            return R.ProcessResult(res.exit_code, False, False, False, res.stdout_path, res.stderr_path)
        with mock.patch.object(R, "run_process", fake):
            rc, out = self.dispatch()
        _, _, r = self.last()
        self.assertEqual((rc, r["verdict"]), (3, "tree_not_stopped"), out)

    def test_injected_extra_args_are_rejected_before_anything_runs(self):
        img = self.tmp / "a.png"
        img.write_bytes(b"png")
        bad = [["--cd", str(self.tmp)], ["-o", "x.json"], ["--schema", "x.json"], ["-m", "o3"], ["--model", "o3"],
               ["-mo3"], ["--cd=x"], ["--sandbox", "none"], ["--json"], ["resume"], ["another prompt"],
               ["--image"], ["-i", "no-such.png"], ["--ephemeral=1"], ["-i", str(img), "--cd", "x"]]
        for extra in bad:
            with self.subTest(extra=extra):
                rc, out = self.dispatch(*extra)
                self.assertEqual(rc, 2, out)
        self.assertEqual(self.runs(), [])
        self.assertFalse((self.log / "args.json").exists())  # the tool was never started
        self.assertFalse((self.tmp / "wt").exists())
        self.assertEqual(git(self.app, "worktree", "list", "--porcelain").count("worktree "), 1)

    def test_fixed_flag_message_names_the_template(self):
        rc, out = self.dispatch("--cd", "x")
        self.assertEqual(rc, 2)
        self.assertIn("fixed", out)

    def test_whitelisted_extra_args_pass_through(self):
        img = self.tmp / "a.png"
        img.write_bytes(b"png")
        rc, out = self.dispatch("--ephemeral", "--image", str(img))
        self.assertEqual(rc, 0, out)
        _, m, _ = self.last()
        self.assertEqual(m["tool"]["extra_args"], ["--ephemeral", "--image", str(img)])
        self.assertEqual(self.args()["argv"][-3:], ["--ephemeral", "--image", str(img)])

    def test_relative_extra_arg_file_reaches_the_tool_resolved(self):
        """The tool runs in the worktree: a file named relative to the caller's directory is passed resolved."""
        (self.tmp / "a.png").write_bytes(b"png")
        want = str((self.tmp / "a.png").resolve())
        old = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, old)
        for extra in (("--image", "a.png"), ("--image=a.png",)):
            with self.subTest(extra=extra):
                rc, out = self.dispatch(*extra)
                self.assertEqual(rc, 0, out)
                self.assertEqual(self.last()[1]["tool"]["extra_args"], ["--image", want])
                self.assertEqual(self.args()["argv"][-2:], ["--image", want])

    @unittest.skipUnless(os.name == "nt", "only Windows runs a .cmd / .bat through cmd.exe")
    def test_batch_file_tool_refuses_cmd_metacharacters_before_anything_is_created(self):
        img = self.tmp / "a%PATH%.png"
        img.write_bytes(b"png")
        self.assertEqual(self.cli.suffix, ".cmd")
        rc, out = self.dispatch("--image", str(img))
        self.assertEqual(rc, 2, out)
        self.assertIn("cmd.exe", out)
        for model in ("m1&echo x", "m1|x", "m1>x", "m1^x"):
            with self.subTest(model=model):
                self.policy["roles"]["implementer"]["model"] = model
                self.save_policy()
                self.assertEqual(self.dispatch()[0], 2)
                self.assertEqual(self.dispatch(more=("--dry-run",))[0], 2)
        self.assertEqual(self.runs(), [])
        self.assertFalse((self.tmp / "wt").exists())
        self.assertEqual(S.list_claims(self.runtime), [])
        self.assertFalse((self.log / "args.json").exists())

    def test_bad_input_is_rejected(self):
        self.assertEqual(self.dispatch(task="Z9")[0], 2)  # not a component, no --repo/--base/--unit
        self.assertEqual(self.dispatch(allow=())[0], 2)
        self.assertEqual(self.dispatch(allow=("../x/**",))[0], 2)
        self.assertEqual(self.dispatch(allow=("src/**",), frozen=("/abs",))[0], 2)
        self.assertEqual(self.main("run", "A1", "--prompt-file", self.prompt, "--tool", self.cli, "--timeout", "0",
                                   "--allow", "src/**")[0], 2)
        self.prompt.unlink()
        self.assertEqual(self.dispatch()[0], 2)
        self.assertEqual(self.runs(), [])

    def test_component_dispatch_refuses_unit_and_repo(self):
        """A component fixes repo and report units itself: --unit and --repo on it are input errors (2)."""
        self.assertEqual(self.dispatch(more=("--unit", "A2"))[0], 2)
        self.assertEqual(self.dispatch(more=("--repo", "app"))[0], 2)
        self.assertEqual(self.runs(), [])

    def test_fix_dispatch_names_repo_base_and_report_units(self):
        fix = ("--repo", "app", "--base", "comp/a1")
        self.assertEqual(self.dispatch(task="fix1", more=fix)[0], 2)                          # no --unit
        self.assertEqual(self.dispatch(task="fix1", more=("--repo", "app", "--unit", "A1"))[0], 2)  # no --base
        self.assertEqual(self.dispatch(task="fix1", more=(*fix, "--unit", "A9"))[0], 2)       # unit is not a component
        self.assertEqual(self.dispatch(task="fix1", more=("--repo", "nope", "--base", "comp/a1", "--unit", "A1"))[0], 2)
        self.assertEqual(self.dispatch(task="a/b", more=(*fix, "--unit", "A1"))[0], 2)        # bad task name
        self.assertEqual(self.runs(), [])
        rc, out = self.dispatch(task="fix1", more=(*fix, "--unit", "A1", "A2"))
        run, m, r = self.last()
        self.assertEqual((rc, r["verdict"], m["task_id"], m["report_units"]), (0, "done", "fix1", ["A1", "A2"]), out)
        self.assertEqual(m["source"]["base_ref"], "comp/a1")
        rc, out = self.dispatch(task="fix2", more=(*fix, "--unit", "A2"))  # the report says A1, which is not in --unit
        self.assertEqual((rc, self.last()[2]["verdict"]), (4, "no_evidence"), out)

    def test_unknown_repo_on_component_is_2(self):
        data = json.loads((self.spec / "components.json").read_text(encoding="utf-8"))
        data["components"][0]["repo"] = "ghost"
        (self.spec / "components.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.dispatch()[0], 2)
        self.assertEqual(self.runs(), [])

    def test_missing_base_branch_is_evidence_error(self):
        rc, out = self.dispatch(more=("--base", "comp/nothing"))
        self.assertEqual(rc, 4, out)
        self.assertEqual(self.runs(), [])

    def test_missing_repo_mapping_is_4(self):
        (self.home / "specflow.local.json").unlink()
        rc, out = self.dispatch()
        self.assertEqual(rc, 4, out)
        self.assertIn("specflow.local.json", out)

    def test_tool_not_found_is_5(self):
        rc, out = self.dispatch(tool=False)  # "fakeimpl" is not on PATH here
        self.assertEqual(rc, 5, out)
        self.assertEqual(self.runs(), [])

    def test_dry_run_creates_nothing(self):
        rc, out = self.dispatch(more=("--dry-run",))
        self.assertEqual(rc, 0, out)
        self.assertIn("DRYRUN", out)
        self.assertIn("<worktree>", out)
        self.assertEqual(self.runs(), [])
        self.assertFalse((self.log / "args.json").exists())
        self.assertFalse((self.tmp / "wt").exists())

    def test_second_dispatch_of_same_task_is_refused_while_first_holds_claim(self):
        held = S.acquire_claim(self.runtime, "resource", "dispatch:A1", run_id="other-run")
        try:
            rc, out = self.dispatch()
        finally:
            held.release()
        self.assertEqual(rc, 3, out)
        self.assertIn("other-run", out)
        self.assertFalse((self.tmp / "wt").exists())

    def test_claim_is_held_by_the_tool_tree_while_it_runs(self):
        res = {}
        th = threading.Thread(target=lambda: res.setdefault("rc", self.dispatch(mode="hang", timeout=30)))
        th.start()
        try:
            end = time.monotonic() + 30
            while not (self.log / "ready").exists() and time.monotonic() < end:
                time.sleep(0.05)
            (c,) = S.list_claims(self.runtime)
            self.assertEqual((c["kind"], c["name"]), ("resource", "dispatch:A1"))
            self.assertTrue(c["children"])
            (run,) = self.runs()
            self.main("cancel", run.name)
        finally:
            th.join(60)


class RoleTest(Base):
    def test_model_placeholder_without_implementer_role_is_2(self):
        del self.policy["roles"]
        self.save_policy()
        rc, out = self.dispatch()
        self.assertEqual(rc, 2, out)
        self.assertIn("roles.implementer", out)
        self.assertEqual(self.runs(), [])

    def test_template_without_model_or_effort_runs_without_roles(self):
        del self.policy["roles"]
        self.policy["dispatch"]["implementer"]["argv"] = ["fakeimpl", "--cd", "{worktree}", "-o", "{report}",
                                                          "--schema", "{schema}"]
        self.save_policy()
        rc, out = self.dispatch()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.last()[1]["tool"]["model"], None)

    def test_effort_placeholder_needs_effort(self):
        self.policy["dispatch"]["implementer"]["argv"] = [*TEMPLATE, "--effort", "{effort}"]
        del self.policy["roles"]["implementer"]["effort"]
        self.save_policy()
        self.assertEqual(self.dispatch()[0], 2)
        self.policy["roles"]["implementer"]["effort"] = "high"
        self.save_policy()
        rc, out = self.dispatch()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.args()["argv"][-2:], ["--effort", "high"])

    def test_dispatch_needs_implementer_and_report_blocks(self):
        for key in ("implementer", "report"):
            with self.subTest(key=key):
                saved = self.policy["dispatch"].pop(key)
                self.save_policy()
                rc, out = self.dispatch()
                self.assertEqual(rc, 2, out)
                self.assertIn(f"dispatch.{key}", out)
                self.policy["dispatch"][key] = saved
        self.assertEqual(self.runs(), [])


class SchemaEvidenceTest(Base):
    """The judge reads the report schema from the product folder, never from the writable run directory."""

    def test_run_dir_schema_copy_is_not_trusted(self):
        rc, out = self.dispatch(mode="tamper_run_schema")
        _, m, r = self.last()
        self.assertEqual((rc, r["verdict"]), (4, "no_evidence"), out)
        self.assertTrue(any("tests" in p or "unmet" in p for p in r["problems"]), r["problems"])

    def test_product_schema_changed_during_the_run_does_not_count(self):
        """The judge uses the schema read at the start (its sha256 is in the manifest), not a file read later."""
        sha = hashlib.sha256((self.spec / "report.schema.json").read_bytes()).hexdigest()
        with mock.patch.dict(os.environ, {"FAKE_PRODUCT_SCHEMA": str(self.spec / "report.schema.json")}):
            rc, out = self.dispatch(mode="change_product_schema")
        _, m, r = self.last()
        self.assertEqual((rc, r["verdict"]), (0, "done"), out)
        self.assertEqual(m["inputs"]["schema"]["sha256"], sha)

    def test_missing_product_schema_at_start_creates_nothing(self):
        (self.spec / "report.schema.json").unlink()
        rc, out = self.dispatch()
        self.assertEqual(rc, 4, out)
        self.assertEqual(self.runs(), [])
        self.assertFalse((self.tmp / "wt").exists())

    def test_schema_removed_before_judging_does_not_count(self):
        real = R.run_process

        def fake(*a, **k):
            res = real(*a, **k)
            (self.spec / "report.schema.json").unlink()
            return res
        with mock.patch.object(R, "run_process", fake):
            rc, out = self.dispatch()
        _, _, r = self.last()
        self.assertEqual((rc, r["verdict"]), (0, "done"), out)

    def save_schema(self, schema):
        (self.spec / "report.schema.json").write_text(json.dumps(schema), encoding="utf-8")

    def test_type_list_with_null_is_judged(self):
        """"type": ["array", "null"] used to crash the judge (exit 1, no result.json)."""
        schema = json.loads(json.dumps(SCHEMA))
        schema["properties"]["unmet"] = {"type": ["array", "null"], "items": {"type": ["string"]}}
        schema["properties"]["notes"] = {"type": ["string", "null"]}
        self.save_schema(schema)
        for mode, code, verdict in (("ok", 0, "done"), ("nulls", 0, "done"), ("notes_int", 4, "no_evidence")):
            with self.subTest(mode=mode):
                rc, out = self.dispatch(mode=mode)
                _, _, r = self.last()
                self.assertEqual((rc, r["exit_code"], r["verdict"]), (code, code, verdict), out)
        self.assertIn("report field notes must be string or null", r["problems"])

    def test_schema_outside_the_checked_subset_is_2_and_creates_nothing(self):
        props = SCHEMA["properties"]
        bad = [
            {**SCHEMA, "properties": {**props, "notes": {"type": "string", "maxLength": 5}}},
            {**SCHEMA, "properties": {**props, "notes": {"type": "string", "pattern": "^x"}}},
            {**SCHEMA, "properties": {**props, "notes": {"type": "date"}}},
            {**SCHEMA, "properties": {**props, "notes": {"type": ["string", 5]}}},
            {**SCHEMA, "properties": {**props, "notes": {"type": []}}},
            {**SCHEMA, "properties": {**props, "unmet": {"type": "array", "items": {"type": "string", "minLength": 1}}}},
            {**SCHEMA, "properties": {**props, "unmet": {"type": "array", "items": {"type": ["string", "nope"]}}}},
            {**SCHEMA, "properties": {**props, "tests": {"type": "string", "enum": "pass"}}},
            {**SCHEMA, "properties": {**props, "count": {"type": "integer", "minimum": "0"}}},
            {**SCHEMA, "properties": {**props, "notes": {"type": "object", "properties": {}}}},
            {**SCHEMA, "properties": {**props, "notes": "string"}},
            {**SCHEMA, "properties": []},
            {**SCHEMA, "required": "task"},
            {**SCHEMA, "additionalProperties": {"type": "string"}},
            {**SCHEMA, "type": "array"},
            {**SCHEMA, "anyOf": [{"required": ["task"]}]},
        ]
        for schema in bad:
            with self.subTest(schema=schema):
                self.save_schema(schema)
                rc, out = self.dispatch()
                self.assertEqual(rc, 2, out)
                self.assertIn("report schema", out)
        self.assertEqual(self.runs(), [])
        self.assertFalse((self.tmp / "wt").exists())
        self.assertEqual(S.list_claims(self.runtime), [])
        self.assertFalse((self.log / "args.json").exists())

    def test_annotations_and_a_pattern_on_the_task_field_are_accepted(self):
        props = SCHEMA["properties"]
        self.save_schema({"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "t", "description": "d",
                          **SCHEMA, "properties": {**props, "task": {"type": "string", "pattern": "^A[0-9]$",
                                                                     "description": "the unit"},
                                                   "unmet": {**props["unmet"], "items": {"type": "string",
                                                                                         "description": "x"}}}})
        rc, out = self.dispatch()
        self.assertEqual(rc, 0, out)

    def test_judge_failure_is_no_evidence_with_result_json(self):
        with mock.patch.object(dr, "check_report", side_effect=RuntimeError("boom")):
            rc, out = self.dispatch()
        run, m, r = self.last()
        self.assertEqual((rc, r["exit_code"], r["verdict"]), (4, 4, "no_evidence"), out)
        self.assertTrue(any("RuntimeError" in p and "boom" in p for p in r["problems"]), r["problems"])
        self.assertEqual((r["run_id"], r["task_id"]), (run.name, "A1"))
        self.assertTrue(r["process"]["process_tree_stopped"])
        self.assertEqual(S.list_claims(self.runtime), [])

    def test_schema_path_must_stay_in_the_product_folder(self):
        for bad in ("../x.json", "/abs/x.json", "a\\b.json"):
            with self.subTest(bad=bad):
                self.assertTrue(WC.validate_policy({**POLICY, "dispatch": {
                    **POLICY["dispatch"], "report": {**POLICY["dispatch"]["report"], "schema": bad}}}))


class GateTest(Base):
    """Real gate: lock against a fake installed_plugins.json, capabilities against PATH."""

    def gated(self):
        self.policy["dispatch"]["requires_lock"] = True
        self.policy["dispatch"]["requires_capabilities"] = ["dispatch"]
        self.save_policy()

    def installed(self, **over):
        e = {"scope": "user", "version": "0.1.0", "gitCommitSha": "a" * 40, **over}
        f = self.tmp / "installed_plugins.json"
        f.write_text(json.dumps({"version": 2, "plugins": {"specflow@mkt": [e]}}), encoding="utf-8")
        return f

    def path_env(self):
        return mock.patch.dict(os.environ, {"PATH": str(self.cli.parent) + os.pathsep + os.environ["PATH"]})

    def test_lock_mismatch_is_3_and_creates_nothing(self):
        self.gated()
        with self.path_env():
            rc, out = self.dispatch(more=("--installed", self.installed(gitCommitSha="b" * 40)))
        self.assertEqual(rc, 3, out)
        self.assertIn("blocked", out)
        self.assertEqual(self.runs(), [])
        self.assertFalse((self.tmp / "wt").exists())
        self.assertFalse((self.log / "args.json").exists())

    def test_missing_tool_is_5_and_creates_nothing(self):
        self.gated()
        rc, out = self.dispatch(more=("--installed", self.installed()))  # fakeimpl is not on PATH
        self.assertEqual(rc, 5, out)
        self.assertIn("fakeimpl", out)
        self.assertEqual(self.runs(), [])
        self.assertFalse((self.tmp / "wt").exists())

    def test_probe_timeout_is_6(self):
        self.gated()

        def slow(argv, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)
        real = WC.dispatch_gate
        with self.path_env(), mock.patch.object(WC, "dispatch_gate",
                                                lambda product, installed=None: real(product, installed, probe=slow)):
            rc, out = self.dispatch(more=("--installed", self.installed()))
        self.assertEqual(rc, 6, out)
        self.assertEqual(self.runs(), [])

    def test_gate_passes_all_the_way_to_done(self):
        self.gated()
        with self.path_env():
            rc, out = self.dispatch(more=("--installed", self.installed()), tool=False)
        self.assertEqual(rc, 0, out)
        run, m, r = self.last()
        self.assertEqual(r["verdict"], "done")
        self.assertEqual(Path(m["tool"]["path"]).stem, "fakeimpl")
        self.assertEqual(self.args()["argv"][0], "--cd")

    def test_check_runs_only_the_gate(self):
        self.gated()
        with self.path_env():
            self.assertEqual(self.main("check", "--installed", self.installed())[0], 0)
            self.assertEqual(self.main("check", "--installed", self.installed(version="9.9.9"))[0], 3)
        self.assertEqual(self.runs(), [])


class CancelCmdTest(Base):
    def test_cancel_writes_request_into_run_dir(self):
        run = self.runs_dir / "20261113T000000-A1-abcdef"
        run.mkdir(parents=True)
        rc, _ = self.main("cancel", run.name)
        self.assertEqual(rc, 0)
        self.assertTrue((run / R.CANCEL).exists())

    def test_cancel_unknown_and_bad_ids(self):
        self.assertEqual(self.main("cancel", "no-such-run")[0], 4)
        for bad in ("..", ".", "../x", "a/b"):
            with self.subTest(bad=bad):
                self.assertEqual(self.main("cancel", bad)[0], 2)


class SiblingTest(unittest.TestCase):
    def test_one_copy_of_each_core_module(self):
        self.assertIs(dr.state_io, sys.modules["specflow_state_io"])
        self.assertIs(dr.process_runner.state_io, dr.state_io)
        self.assertIs(dr.registry.state_io, dr.state_io)
        self.assertIs(dr.registry.workflow_config, dr.workflow_config)

    def test_glob_matching(self):
        self.assertTrue(dr.in_scope("src/a1/x/y.c", ["src/a1/**"]))
        self.assertTrue(dr.in_scope("a.c", ["**/a.c"]))
        self.assertFalse(dr.in_scope("src/a1/x/y.c", ["src/a1/*"]))
        self.assertFalse(dr.in_scope("src/b/y.c", ["src/a1/**"]))
        self.assertTrue(all(dr._bad_glob(g) for g in ("", "a\\b", "C:x", "/x", "../x", "a/../b")))


if __name__ == "__main__":
    unittest.main()
