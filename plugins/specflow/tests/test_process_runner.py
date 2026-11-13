"""Tests for process_runner. Run: python -m unittest discover -s plugins/specflow/tests (stdlib only).

All tests use real child processes: the fake CLI is a Python script in a temp folder (FAKE) that starts grandchildren,
hangs and leaves stray processes behind. No real tool is started.
"""
import importlib.util
import json
import os
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


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


S = _load("state_io")
R = _load("process_runner")

# Fake CLI. Usage: fake.py <mode> <pids folder> [args...]
# A grandchild's pid is written to pids/<name>; when a marker is given the grandchild sleeps 3 s, then writes the marker
# (it only gets written if the grandchild is still alive).
FAKE = r'''
import os, subprocess, sys, time
from pathlib import Path
mode, pids = sys.argv[1], Path(sys.argv[2])
GRAND = ("import pathlib, sys, time\n"
         "if sys.argv[1]:\n"
         "    time.sleep(3)\n"
         "    pathlib.Path(sys.argv[1]).write_text('still alive')\n"
         "time.sleep(300)\n")


def grandchild(name, marker="", detach=False):
    kw = {}
    if detach:  # try to break away: Windows no console and its own process group; POSIX its own session (leaves the process group)
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200
        else:
            kw["start_new_session"] = True
    p = subprocess.Popen([sys.executable, "-c", GRAND, marker], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    (pids / name).write_text(str(p.pid))
    return p


if mode == "out":               # print stdout / stderr, echo stdin, exit with the given code
    data = sys.stdin.read()
    print("OUT " + data.strip())
    print("ERR this is stderr", file=sys.stderr)
    sys.exit(int(sys.argv[3]))
elif mode == "env":             # print the environment
    print(os.environ.get("TEMP", ""), os.environ.get("TMPDIR", ""), os.environ.get("GIT_DIR", "-"),
          os.environ.get("SPECFLOW_RUN_ID", ""), sep="\n")
elif mode == "hang":            # start a grandchild (writes the marker later), then hang
    grandchild("grand", sys.argv[3])
    (pids / "ready").write_text(str(os.getpid()))
    time.sleep(300)
elif mode == "leftover":        # start a grandchild that tries to break away, then exit at once (exit 0)
    grandchild("grand", detach=True)
    (pids / "ready").write_text(str(os.getpid()))
'''


def alive(pid):
    return S.process_alive(int(pid), None) is not False


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.fake = self.root / "fake.py"
        self.fake.write_text(FAKE, encoding="utf-8")
        self.pids = self.root / "pids"
        self.pids.mkdir()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._kill_leftovers)

    def _kill_leftovers(self):
        for f in self.pids.glob("*"):
            try:
                pid = int(f.read_text())
            except ValueError:
                continue
            try:
                os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
            except OSError:
                pass

    def argv(self, mode, *rest):
        return [sys.executable, str(self.fake), mode, str(self.pids), *map(str, rest)]

    def pid(self, name):
        return int((self.pids / name).read_text())

    def wait_for(self, cond, timeout=20, what="the condition"):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {what}")

    def record(self, run_dir):
        return json.loads((run_dir / R.PROCESS).read_text(encoding="utf-8"))


class NormalRunTest(Base):
    def test_exit_code_logs_and_process_record(self):
        run = self.root / "runs" / "r1"
        stdin = self.root / "in.txt"
        stdin.write_text("prompt text", encoding="utf-8")
        res = R.run_process(self.argv("out", 3), self.root, 30, run, stdin_path=stdin)
        self.assertIsInstance(res, R.ProcessResult)
        self.assertEqual((res.exit_code, res.timed_out, res.cancelled, res.process_tree_stopped), (3, False, False, True))
        self.assertEqual((res.stdout_path, res.stderr_path), (run / "stdout.log", run / "stderr.log"))
        self.assertIn("OUT prompt text", res.stdout_path.read_text(encoding="utf-8"))
        self.assertIn("ERR", res.stderr_path.read_text(encoding="utf-8"))
        rec = self.record(run)
        self.assertEqual((rec["exit_code"], rec["timed_out"], rec["cancelled"], rec["process_tree_stopped"]),
                         (3, False, False, True))
        self.assertEqual(rec["argv"], self.argv("out", 3))
        self.assertEqual(rec["leftover_pids"], [])
        self.assertFalse((run / R.CHECKPOINT).exists())           # a normal end writes no checkpoint

    def test_exit_zero_is_only_a_process_fact(self):
        """run_process reports only process facts; exit 0 does not mean the task is done (success is judged from the evidence in the run directory)."""
        res = R.run_process(self.argv("out", 0), self.root, 30, self.root / "r")
        self.assertEqual(res.exit_code, 0)
        self.assertFalse(hasattr(res, "done"))

    def test_run_dir_lives_under_runs_root(self):
        rt = self.root / "specflow-runtime"
        d = R.create_run_dir(S.runs_root(rt), "r1")
        self.assertEqual(d, rt / "runs" / "r1")
        res = R.run_process(self.argv("out", 0), self.root, 30, d)
        self.assertEqual(res.stdout_path.parent, d)

    def test_run_dir_is_single_use(self):
        run = self.root / "r"
        R.run_process(self.argv("out", 0), self.root, 30, run)
        with self.assertRaises(R.RunnerError) as cm:
            R.run_process(self.argv("out", 0), self.root, 30, run)
        self.assertEqual(cm.exception.code, 3)

    def test_bad_input_is_rejected_before_start(self):
        for argv, t in (([], 5), ("python -V", 5), ([sys.executable], 0), ([sys.executable], -1), ([sys.executable], True)):
            with self.subTest(argv=argv, t=t), self.assertRaises(R.RunnerError) as cm:
                R.run_process(argv, self.root, t, self.root / "bad")
            self.assertEqual(cm.exception.code, 2)
        self.assertFalse((self.root / "bad" / "stdout.log").exists())

    def test_missing_program_is_capability_error(self):
        with self.assertRaises(R.RunnerError) as cm:
            R.run_process([str(self.root / "no-such-program.exe")], self.root, 5, self.root / "r")
        self.assertEqual(cm.exception.code, 5)

    def test_child_env_isolates_temp_and_drops_git_location(self):
        run = self.root / "runs" / "20261113T000000-A4-abcdef"
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.root / "other.git")}):
            env = R.child_env(run)
        res = R.run_process(self.argv("env"), self.root, 30, run, env=env)
        temp, tmpdir, git_dir, run_id = res.stdout_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(Path(temp), run / "tmp")
        self.assertEqual(Path(tmpdir), run / "tmp")
        self.assertTrue((run / "tmp").is_dir())
        self.assertEqual((git_dir, run_id), ("-", run.name))

    def test_shell_mode_keeps_command_string_quoting(self):
        """A shell command string may contain quotes; with shell=True argv is one command."""
        cmd = f'"{sys.executable}" "{self.fake}" out "{self.pids}" 7'
        res = R.run_process([cmd], self.root, 30, self.root / "r", shell=True)
        self.assertEqual(res.exit_code, 7)
        with self.assertRaises(R.RunnerError):
            R.run_process(["a", "b"], self.root, 30, self.root / "r2", shell=True)

    def test_new_run_ids_are_unique_and_dirs_exclusive(self):
        ids = {R.new_run_id("A4") for _ in range(50)}
        self.assertEqual(len(ids), 50)
        rid = R.new_run_id("task A4")
        self.assertRegex(rid, r"^\d{8}T\d{6}-[A-Za-z0-9._-]+-[0-9a-f]{6}$")
        d = R.create_run_dir(self.root / "runs", rid)
        self.assertTrue(d.is_dir())
        with self.assertRaises(R.RunnerError) as cm:
            R.create_run_dir(self.root / "runs", rid)
        self.assertEqual(cm.exception.code, 3)


class StopTest(Base):
    def test_timeout_kills_child_and_grandchild_and_writes_checkpoint(self):
        run = self.root / "r"
        marker = self.root / "marker.txt"
        t0 = time.monotonic()
        res = R.run_process(self.argv("hang", marker), self.root, 1, run)
        self.assertLess(time.monotonic() - t0, 20)
        self.assertEqual((res.timed_out, res.cancelled, res.process_tree_stopped), (True, False, True))
        self.assertFalse(alive(self.pid("ready")))
        self.assertFalse(alive(self.pid("grand")))                 # the grandchild stopped too
        time.sleep(3.5)
        self.assertFalse(marker.exists())                           # no orphan keeps writing (the grandchild would write after 3 s)
        cp = json.loads((run / R.CHECKPOINT).read_text(encoding="utf-8"))
        self.assertEqual(cp["reason"], "timeout")
        self.assertEqual(cp["argv"], self.argv("hang", marker))
        self.assertEqual(Path(cp["logs"]["stdout"]), run / "stdout.log")
        self.assertEqual(Path(cp["logs"]["stderr"]), run / "stderr.log")
        self.assertTrue(cp["process_tree_stopped"])
        self.assertEqual(cp["survivors"], [])
        self.assertTrue(self.record(run)["timed_out"])

    def test_cancel_request_stops_tree_and_writes_checkpoint(self):
        run = self.root / "r"
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("res", R.run_process(self.argv("hang", ""), self.root, 120, run)))
        th.start()
        try:
            self.wait_for(lambda: (self.pids / "ready").exists(), what="the fake CLI to start")
            R.request_cancel(run)
        finally:
            th.join(60)
        res = out["res"]
        self.assertEqual((res.timed_out, res.cancelled, res.process_tree_stopped), (False, True, True))
        self.assertFalse(alive(self.pid("grand")))
        self.assertEqual(json.loads((run / R.CHECKPOINT).read_text(encoding="utf-8"))["reason"], "cancelled")

    def test_keyboard_interrupt_counts_as_cancel(self):
        run = self.root / "r"
        real_wait = R.ProcessTree.wait
        calls = {"n": 0}

        def wait(tree, timeout):
            if (self.pids / "ready").exists():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise KeyboardInterrupt
            return real_wait(tree, timeout)
        with mock.patch.object(R.ProcessTree, "wait", wait):
            res = R.run_process(self.argv("hang", ""), self.root, 120, run)
        self.assertEqual((res.cancelled, res.process_tree_stopped), (True, True))
        self.assertFalse(alive(self.pid("grand")))

    def test_leftover_process_after_main_exit_is_killed_and_recorded(self):
        """The main process exits 0 but leaves a grandchild that tries to break away: find it, kill it, record it; never leave a writable orphan."""
        run = self.root / "r"
        res = R.run_process(self.argv("leftover"), self.root, 30, run)
        self.assertEqual((res.exit_code, res.timed_out, res.process_tree_stopped), (0, False, True))
        grand = self.pid("grand")
        self.wait_for(lambda: not alive(grand), timeout=5, what="the leftover grandchild to stop")
        self.assertIn(grand, self.record(run)["leftover_pids"])

    def test_tree_that_cannot_be_confirmed_stopped_is_reported_and_registered(self):
        """Killed but stop cannot be confirmed: process_tree_stopped=False and the survivors go to register (the claim keeps blocking)."""
        seen = []
        with mock.patch.object(R.ProcessTree, "stop", lambda tree, wait=None: [424242]):
            res = R.run_process(self.argv("hang", ""), self.root, 1, self.root / "r", register=seen.append)
        self.assertFalse(res.process_tree_stopped)
        self.assertEqual(seen[1:], [424242])                       # the main process is registered first, then the survivors
        cp = json.loads((self.root / "r" / R.CHECKPOINT).read_text(encoding="utf-8"))
        self.assertEqual((cp["process_tree_stopped"], cp["survivors"]), (False, [424242]))

    def test_register_failure_still_stops_the_tree(self):
        def boom(pid):
            raise S.StateError("cannot write the claim")
        with self.assertRaises(S.StateError):
            R.run_process(self.argv("hang", ""), self.root, 60, self.root / "r", register=boom)
        if (self.pids / "grand").exists():
            self.wait_for(lambda: not alive(self.pid("grand")), timeout=10, what="the grandchild to stop")


class ClaimIntegrationTest(Base):
    """When the runner alone is killed, a task whose child tree is still writing must not become claimable."""
    DRIVER = r'''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import process_runner as R, state_io as S
rt, argv, run = Path(sys.argv[2]), sys.argv[4:], Path(sys.argv[3])
claim = S.acquire_claim(rt, "task", "A4", run_id="driver")
R.run_process(argv, run.parent, 300, run, register=lambda pid: claim.add_process(pid, group=True))
'''

    def test_killed_runner_never_leaves_a_claimable_task_with_live_writers(self):
        rt = self.root / "runtime"
        drv = self.root / "driver.py"
        drv.write_text(self.DRIVER, encoding="utf-8")
        p = subprocess.Popen([sys.executable, str(drv), str(SCRIPTS), str(rt), str(self.root / "r"),
                              *self.argv("hang", "")], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.wait_for(lambda: (self.pids / "grand").exists() and (self.pids / "ready").exists(), what="the session to start")
            meta = json.loads(next((rt / "claims").glob("task-a4.json")).read_text(encoding="utf-8"))
            self.assertEqual([c["pid"] for c in meta["children"]], [self.pid("ready")])
            self.assertTrue(meta["children"][0]["group"])
        finally:
            p.kill()                                               # kill only the runner
            p.wait()
        writers = [self.pid("ready"), self.pid("grand")]

        def try_claim():
            try:
                S.acquire_claim(rt, "task", "A4", run_id="next").release()
                return True
            except S.ClaimHeld:
                return False
        if os.name == "nt":
            # Job Object (KILL_ON_JOB_CLOSE): when the runner dies the whole tree stops with it; only then can it be taken over
            self.wait_for(lambda: not any(alive(w) for w in writers), timeout=10, what="the tree to stop after the job closed")
        else:
            # POSIX: the process group is still writing -> the claim blocks; it frees only after the whole group stopped
            self.assertTrue(all(alive(w) for w in writers))
            self.assertFalse(try_claim())
            os.killpg(self.pid("ready"), signal.SIGKILL)
            self.wait_for(lambda: not any(alive(w) for w in writers), timeout=10, what="the group to stop")
        self.wait_for(try_claim, timeout=10, what="the claim to become available")


if __name__ == "__main__":
    unittest.main()
