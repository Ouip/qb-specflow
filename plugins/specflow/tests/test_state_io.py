"""Tests for state_io. Run: python -m unittest discover -s plugins/specflow/tests (stdlib only).

Concurrency always uses real child processes (each runs the same worker, see WORKER); everything lives in temp folders.
"""
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


S = _load("state_io")

STATES = {"order": ["todo", "doing", "merge", "accept", "done"],
          "workable": ["todo", "doing"], "dep_ok": ["merge", "accept", "done"]}

# Child process: one kind of writer per mode. Work starts only when the go file appears, so all children start together.
WORKER = r'''
import json, os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["STATE_IO_DIR"])
import state_io as S


def go(path):
    while not Path(path).exists():
        time.sleep(0.002)


def rt_of(x):
    return S.runtime_root(Path.cwd()) if x == "auto" else Path(x)


mode, a = sys.argv[1], sys.argv[2:]
if mode == "update":            # reg rt go cid n: change one component n times in a row
    reg, rt, g, cid, n = a
    go(g)
    for k in range(int(n)):
        S.update_registry(Path(reg), None, S.RegistryChange(cid, blocked=f"{cid}-{k}"), runtime=Path(rt))
elif mode == "cas":             # reg rt go cid rev: race with the same revision
    reg, rt, g, cid, rev = a
    go(g)
    try:
        S.update_registry(Path(reg), rev, S.RegistryChange(cid, blocked="winner"), runtime=Path(rt))
    except S.RevisionConflict:
        sys.exit(3)
elif mode == "handoff":         # path rt go rev text
    p, rt, g, rev, text = a
    go(g)
    try:
        S.write_handoff(Path(p), rev, text, runtime=Path(rt))
    except S.RevisionConflict:
        sys.exit(3)
elif mode == "hang":            # reg rt marker: stop after the temp is written and before the replace, waiting to be killed
    reg, rt, marker = a

    def hang(src, dst):
        Path(marker).write_text(str(src), encoding="utf-8")
        time.sleep(300)
    os.replace = hang
    S.update_registry(Path(reg), None, S.RegistryChange("C0", blocked="half"), runtime=Path(rt))
elif mode == "hold":            # rt kind name ready stop child: take a claim, optionally start a child process and register it
    rt, kind, name, ready, stop, child = a
    claim = S.acquire_claim(rt_of(rt), kind, name, worktree=Path.cwd(), run_id="holder")
    info = {"pid": os.getpid()}
    if child == "1":
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        claim.add_process(p.pid)
        info["child"] = p.pid
    Path(ready).write_text(json.dumps(info), encoding="utf-8")
    while not Path(stop).exists():
        time.sleep(0.02)
    claim.release()
elif mode == "try":             # rt kind name: exit 0 if it can be taken (released at once), 3 if it is held
    rt, kind, name = a
    try:
        c = S.acquire_claim(rt_of(rt), kind, name, worktree=Path.cwd(), run_id="try")
    except S.ClaimHeld as e:
        print(e)
        sys.exit(3)
    c.release()
'''


def comp(cid, status="todo", deps=(), blocked=None):
    return {"id": cid, "name": cid.lower(), "spec": f"{cid}-x.md", "repo": "app",
            "deps": list(deps), "status": status, "blocked": blocked}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True, capture_output=True)


def kill_pid(pid):
    try:
        os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
    except OSError:
        pass


class Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.rt = self.root / "runtime"
        (self.root / "specs").mkdir()
        self.reg = self.root / "specs" / "components.json"
        self.worker = self.root / "worker.py"
        self.worker.write_text(WORKER, encoding="utf-8")
        self.procs, self.pids, self.n = [], [], 0
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._kill_all)

    def _kill_all(self):
        for pid in self.pids:
            kill_pid(pid)
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()

    def save(self, n=6, comps=None):
        data = {"format": 1, "states": STATES,
                "repos": {"app": {"integration_branch": "main", "baseline": []}},
                "components": comps if comps is not None else [comp(f"C{i}") for i in range(n)]}
        for c in data["components"]:
            (self.root / "specs" / c["spec"]).write_text("x", encoding="utf-8")
        self.reg.write_bytes(S.dump_registry(data))
        return data

    def load(self):
        return json.loads(self.reg.read_text(encoding="utf-8"))

    def row(self, cid):
        return next(c for c in self.load()["components"] if c["id"] == cid)

    def spawn(self, *args, cwd=None):
        self.n += 1
        out = self.root / f"w{self.n}.out"
        env = dict(os.environ, STATE_IO_DIR=str(SCRIPTS), PYTHONIOENCODING="utf-8")
        with open(out, "wb") as f:
            p = subprocess.Popen([sys.executable, str(self.worker), *map(str, args)], cwd=cwd or self.root,
                                 env=env, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT)
        p.out = out
        self.procs.append(p)
        return p

    def finish(self, p, timeout=60):
        p.wait(timeout=timeout)
        return p.returncode, p.out.read_text(encoding="utf-8", errors="replace")

    def wait_for(self, cond, timeout=20, what="the condition"):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.02)
        self.fail(f"timed out waiting for {what}")

    def temps(self, d=None):
        return list((d or self.reg.parent).glob("*" + S.TMP_SUFFIX))


class AtomicWriteTest(Tmp):
    def test_replaces_whole_file_and_leaves_no_temp(self):
        p = self.root / "a.json"
        p.write_bytes(b"old")
        S.atomic_write(p, b'{"new": 1}\n')
        self.assertEqual(p.read_bytes(), b'{"new": 1}\n')
        self.assertEqual(self.temps(self.root), [])

    def test_failure_before_replace_keeps_old_file(self):
        p = self.root / "a.json"
        p.write_bytes(b"old")
        with mock.patch.object(S.os, "fsync", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                S.atomic_write(p, b"new")
        self.assertEqual(p.read_bytes(), b"old")
        self.assertEqual(self.temps(self.root), [])

    def test_replace_still_denied_after_retries_is_state_error(self):
        """Still PermissionError after the retries (another process keeps the target open) -> StateError (2), not a traceback."""
        p = self.root / "a.json"
        p.write_bytes(b"old")
        with mock.patch.object(S.os, "replace", side_effect=PermissionError(13, "in use")), \
                mock.patch.object(S.time, "sleep", lambda s: None):
            with self.assertRaises(S.StateError) as cm:
                S.atomic_write(p, b"new")
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("a.json", str(cm.exception))
        self.assertEqual(p.read_bytes(), b"old")
        self.assertEqual(self.temps(self.root), [])


class RegistryTest(Tmp):
    def update(self, change, expected=None):
        return S.update_registry(self.reg, expected, change, runtime=self.rt, root=self.root)

    def test_revision_is_sha256_of_bytes_and_format_kept(self):
        self.save(3)
        before = S.file_revision(self.reg)
        self.assertEqual(before, sha(self.reg.read_bytes()))
        res = self.update(S.RegistryChange("C1", target_status="doing", reason="dispatch"))
        self.assertEqual((res["previous_revision"], res["revision"]), (before, sha(self.reg.read_bytes())))
        self.assertEqual((res["component"]["status"], res["changed"], res["reason"]), ("doing", True, "dispatch"))
        text = self.reg.read_text(encoding="utf-8")
        self.assertEqual(sum(1 for x in text.splitlines() if x.strip().startswith('{"id"')), 3)
        self.assertNotIn("\r", text)

    def test_stale_revision_is_refused(self):
        self.save(3)
        rev0 = S.file_revision(self.reg)
        self.update(S.RegistryChange("C0", blocked="first"), rev0)
        after = self.reg.read_bytes()
        with self.assertRaises(S.RevisionConflict) as cm:
            self.update(S.RegistryChange("C1", blocked="second"), rev0)
        self.assertEqual(cm.exception.current, sha(after))
        self.assertEqual(cm.exception.code, 3)
        self.assertEqual(self.reg.read_bytes(), after)

    def test_bad_changes_rejected_without_writing(self):
        self.save(comps=[comp("A"), comp("B", deps=["A"])])
        before = self.reg.read_bytes()
        for ch in (S.RegistryChange("Z", target_status="doing"),           # no such component
                   S.RegistryChange("A", target_status="finished"),       # not in states.order
                   S.RegistryChange("A", blocked="  "),                   # a block without a reason
                   S.RegistryChange("A", deps=("Q",)),                    # dependency is not a component
                   S.RegistryChange("A", deps=("A",)),                    # depends on itself
                   S.RegistryChange("A", deps=("B",)),                    # cycle A->B->A
                   S.RegistryChange("A")):                                # nothing changed
            with self.subTest(ch=ch), self.assertRaises(S.ChangeRejected):
                self.update(ch)
        self.assertEqual(self.reg.read_bytes(), before)

    def test_block_unblock_and_deps(self):
        self.save(comps=[comp("A"), comp("B"), comp("C")])
        self.update(S.RegistryChange("C", blocked="waiting for the owner", deps=("A", "B")))
        self.assertEqual((self.row("C")["blocked"], self.row("C")["deps"]), ("waiting for the owner", ["A", "B"]))
        self.update(S.RegistryChange("C", blocked=None))
        self.assertIsNone(self.row("C")["blocked"])

    def test_add_component(self):
        self.save(comps=[comp("A")])
        (self.root / "specs" / "N1-new.md").write_text("x", encoding="utf-8")
        new = {"name": "new", "spec": "N1-new.md", "repo": "app"}
        res = self.update(S.RegistryChange("N1", deps=("A",), new=new))
        self.assertEqual(res["component"], {"id": "N1", "name": "new", "spec": "N1-new.md", "repo": "app",
                                            "deps": ["A"], "status": "todo", "blocked": None})
        self.assertEqual([c["id"] for c in self.load()["components"]], ["A", "N1"])
        before = self.reg.read_bytes()
        for cid, n in (("N1", new),                                       # duplicate id
                       ("N2", dict(new, repo="nope")),                    # repo not in repos
                       ("N3", dict(new, spec="missing.md")),              # spec file absent
                       ("N4", dict(new, name=""))):                       # missing name
            with self.subTest(cid=cid), self.assertRaises(S.ChangeRejected):
                self.update(S.RegistryChange(cid, new=n))
        self.assertEqual(self.reg.read_bytes(), before)

    def test_update_repo(self):
        self.save(1)
        S.update_repo(self.reg, None, "app", integration_branch="dev", baseline=["t unit", "t int"], runtime=self.rt)
        self.assertEqual(self.load()["repos"]["app"], {"integration_branch": "dev", "baseline": ["t unit", "t int"]})
        with self.assertRaises(S.ChangeRejected):
            S.update_repo(self.reg, None, "nope", integration_branch="x", runtime=self.rt)

    def test_components_not_a_list_of_objects_is_refused_untouched(self):
        """An update rewrites the whole file: a components value of another shape would be lost (an object became a
        list of its keys)."""
        for comps in ({"C0": comp("C0")}, ["C0"], None, "x"):
            with self.subTest(comps=comps):
                raw = json.dumps({"format": 1, "states": STATES, "repos": {"app": {"integration_branch": "main"}},
                                  "components": comps}).encode("utf-8")
                self.reg.write_bytes(raw)
                for call in (lambda: S.update_repo(self.reg, None, "app", integration_branch="dev", runtime=self.rt),
                             lambda: self.update(S.RegistryChange("C0", blocked="x"))):
                    with self.assertRaises(S.StateError) as cm:
                        call()
                    self.assertEqual(cm.exception.code, 2)
                    self.assertEqual(self.reg.read_bytes(), raw)

    def test_broken_json_is_not_overwritten(self):
        self.reg.write_text("{ broken", encoding="utf-8")
        with self.assertRaises(S.StateError):
            self.update(S.RegistryChange("C0", blocked="x"))
        self.assertEqual(self.reg.read_text(encoding="utf-8"), "{ broken")


class NoneRevisionTest(Tmp):
    """expected_revision=None: create-only for cas_write, no revision check (but under the lock) for update_registry."""

    def test_cas_write_none_means_create_only(self):
        p = self.root / "x.txt"
        rev = S.cas_write(p, None, b"one", lock="x", runtime=self.rt)
        self.assertEqual(rev, sha(b"one"))
        with self.assertRaises(S.RevisionConflict) as cm:
            S.cas_write(p, None, b"two", lock="x", runtime=self.rt)
        self.assertEqual((cm.exception.current, p.read_bytes()), (rev, b"one"))

    def test_update_registry_none_means_no_check_against_current_file(self):
        self.save(2)
        S.update_registry(self.reg, S.file_revision(self.reg), S.RegistryChange("C0", blocked="a"), runtime=self.rt)
        res = S.update_registry(self.reg, None, S.RegistryChange("C1", blocked="b"), runtime=self.rt)
        self.assertTrue(res["changed"])
        self.assertEqual((self.row("C0")["blocked"], self.row("C1")["blocked"]), ("a", "b"))   # nothing lost
        with self.assertRaises(S.StateError):                                                   # the file must exist
            S.update_registry(self.root / "absent.json", None, S.RegistryChange("C0", blocked="x"), runtime=self.rt)


class EvidenceTest(Tmp):
    def test_evidence_first_then_reference(self):
        self.save(2)
        ref = {"path": "evidence/C0/run1.json", "sha256": sha(b"{}")}
        before = self.reg.read_bytes()
        with self.assertRaises(S.EvidenceMissing) as cm:      # referencing evidence that is not written yet: refused
            S.update_registry(self.reg, None, S.RegistryChange("C0", target_status="doing", evidence_refs=(ref,)),
                              runtime=self.rt, root=self.root)
        self.assertEqual((cm.exception.code, self.reg.read_bytes()), (4, before))
        got = S.write_evidence(self.root, "evidence/C0/run1.json", b"{}")
        self.assertEqual(got, ref)
        S.update_registry(self.reg, None, S.RegistryChange("C0", target_status="doing", evidence_refs=(got,)),
                          runtime=self.rt, root=self.root)
        self.assertEqual(self.row("C0")["evidence"], [ref])

    def test_evidence_is_immutable(self):
        ref = S.write_evidence(self.root, "evidence/a.txt", b"one")
        self.assertEqual(S.write_evidence(self.root, "evidence/a.txt", b"one"), ref)   # same content: reused
        with self.assertRaises(S.StateError):
            S.write_evidence(self.root, "evidence/a.txt", b"two")                     # different content: not overwritten
        self.assertEqual((self.root / "evidence/a.txt").read_bytes(), b"one")
        self.assertIsNone(S.check_evidence(self.root, ref))
        (self.root / "evidence/a.txt").write_bytes(b"tampered")
        self.assertIn("sha256", S.check_evidence(self.root, ref))
        for bad in ("../x", "/abs/x", "C:/x"):
            with self.subTest(bad=bad), self.assertRaises(S.StateError):
                S.write_evidence(self.root, bad, b"x")

    def test_link_failure_is_state_error(self):
        """The filesystem refuses hard links (or another OSError) -> StateError (2); no temp and no half file are left."""
        with mock.patch.object(S.os, "link", side_effect=OSError(1, "no hard links")):
            with self.assertRaises(S.StateError) as cm:
                S.write_evidence(self.root, "evidence/b.txt", b"x")
        self.assertEqual(cm.exception.code, 2)
        self.assertFalse((self.root / "evidence/b.txt").exists())
        self.assertEqual(self.temps(self.root / "evidence"), [])


class ConcurrencyTest(Tmp):
    def test_parallel_updates_to_different_components_lose_nothing(self):
        n, k = 6, 15
        self.save(n)
        go = self.root / "go"
        ps = [self.spawn("update", self.reg, self.rt, go, f"C{i}", k) for i in range(n)]
        go.write_text("go")
        for p in ps:
            rc, out = self.finish(p)
            self.assertEqual(rc, 0, out)
        data = self.load()
        self.assertEqual({c["id"]: c["blocked"] for c in data["components"]},
                         {f"C{i}": f"C{i}-{k - 1}" for i in range(n)})
        self.assertEqual(self.temps(), [])

    def test_same_revision_race_exactly_one_wins(self):
        n = 6
        self.save(n)
        rev = S.file_revision(self.reg)
        go = self.root / "go"
        ps = [self.spawn("cas", self.reg, self.rt, go, f"C{i}", rev) for i in range(n)]
        go.write_text("go")
        codes = sorted(self.finish(p)[0] for p in ps)
        self.assertEqual(codes, [0] + [3] * (n - 1))
        self.assertEqual(sum(c["blocked"] == "winner" for c in self.load()["components"]), 1)

    def test_writer_killed_mid_write_leaves_previous_file(self):
        self.save(3)
        before = self.reg.read_bytes()
        marker = self.root / "marker"
        p = self.spawn("hang", self.reg, self.rt, marker)
        self.wait_for(marker.exists, what="the writer to stop before replace")
        self.assertEqual(len(self.temps()), 1)        # the temp is written, not yet swapped in
        p.kill()
        p.wait()
        self.assertEqual(self.reg.read_bytes(), before)
        json.loads(before)
        # the lock is released when the process ends: the next writer gets it, sweeps the leftover temp and writes normally
        S.update_registry(self.reg, None, S.RegistryChange("C1", blocked="after"), runtime=self.rt, timeout=10)
        self.assertEqual(self.row("C1")["blocked"], "after")
        self.assertEqual(self.temps(), [])


class HandoffTest(Tmp):
    def test_stale_handoff_overwrite_refused(self):
        h = self.root / "handoff.md"
        r1 = S.write_handoff(h, None, "# v1\n- item A\n", runtime=self.rt)
        self.assertEqual(r1, sha("# v1\n- item A\n".encode()))
        r2 = S.write_handoff(h, r1, "# v2\n- item A\n- item B\n", runtime=self.rt)   # session A wraps up
        with self.assertRaises(S.RevisionConflict) as cm:                            # B holds the old r1
            S.write_handoff(h, r1, "# v3\n- item A\n", runtime=self.rt)
        self.assertEqual(cm.exception.current, r2)
        self.assertIn("item B", h.read_text(encoding="utf-8"))
        S.write_handoff(h, cm.exception.current, "# v3\n- item A\n- item B\n- item C\n", runtime=self.rt)  # re-read and merge
        self.assertIn("item C", h.read_text(encoding="utf-8"))
        with self.assertRaises(S.RevisionConflict):                                  # exists but written as if new
            S.write_handoff(h, None, "# x\n", runtime=self.rt)

    def test_parallel_writers_with_same_input_one_wins(self):
        h = self.root / "handoff.md"
        rev = S.write_handoff(h, None, "# v1\n", runtime=self.rt)
        go = self.root / "go"
        ps = [self.spawn("handoff", h, self.rt, go, rev, f"# from {i}\n") for i in range(5)]
        go.write_text("go")
        self.assertEqual(sorted(self.finish(p)[0] for p in ps), [0, 3, 3, 3, 3])


class ProcessTest(unittest.TestCase):
    def test_probe(self):
        me = os.getpid()
        start = S.process_start(me)
        self.assertTrue(start)
        self.assertIs(S.process_alive(me, start), True)
        self.assertIs(S.process_alive(me, "not-" + start), False)    # same PID, different start time = PID reused
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertIs(S.process_alive(p.pid, None), False)
        for bad in (0, -1, None):
            self.assertIs(S.process_alive(bad, None), False)


class ClaimTest(Tmp):
    def meta_file(self, kind, name):
        return S._claim_paths(self.rt, kind, name)[1]

    def plant(self, kind, name, **over):
        """Simulate a claim with only metadata and no process holding the OS lock (e.g. the owner ended, or it was copied from another machine)."""
        meta = {"schema_version": 1, "kind": kind, "name": name, "machine": S.MACHINE, "pid": os.getpid(),
                "start": S.process_start(os.getpid()), "children": [], "worktree": str(self.root),
                "run_id": "planted", "acquired_at": "2000-01-01T00:00:00+00:00"}
        meta.update(over)
        f = self.meta_file(kind, name)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(meta), encoding="utf-8")

    def test_named_resources_share_the_claim_mechanism(self):
        res = ("port",)  # an extra kind a product adds through `resources` in workflow.json
        c = S.acquire_claim(self.rt, "resource", "port:COM5", run_id="r1", resources=res)
        with self.assertRaises(S.ClaimHeld) as cm:
            S.acquire_claim(self.rt, "resource", "port:COM5", run_id="r2", resources=res)
        self.assertEqual(cm.exception.code, 3)
        S.acquire_claim(self.rt, "resource", "port:COM6", resources=res).release()          # a different port is independent
        for name in ("integration-branch:app", "dispatch:A4"):                              # built in
            S.acquire_claim(self.rt, "resource", name).release()
        c.release()
        S.acquire_claim(self.rt, "resource", "port:COM5", resources=res).release()
        for kind, name in (("resource", "printer"), ("lease", "A4"), ("task", ""), ("resource", "port:COM5")):
            with self.subTest(kind=kind, name=name), self.assertRaises(S.StateError):       # "port" is not built in
                S.acquire_claim(self.rt, kind, name)

    def test_resource_names_compare_ignoring_case(self):
        res = ("Port",)
        c = S.acquire_claim(self.rt, "resource", "PORT:COM5", resources=res)
        for other in ("port:com5", "Port:Com5"):
            with self.subTest(other=other), self.assertRaises(S.ClaimHeld):
                S.acquire_claim(self.rt, "resource", other, resources=res)
        c.release()
        S.acquire_claim(self.rt, "resource", "Dispatch:A4").release()                         # built-in kinds too

    def test_task_claims_keep_exact_case(self):
        """Component ids are case-sensitive (A1 and a1 are different components); only resource names fold case."""
        c = S.acquire_claim(self.rt, "task", "A1")
        S.acquire_claim(self.rt, "task", "a1").release()          # a different task: not blocked
        with self.assertRaises(S.ClaimHeld):
            S.acquire_claim(self.rt, "task", "A1")
        c.release()
        self.assertEqual(len({S._claim_paths(self.rt, "task", n)[1] for n in ("A1", "a1")}), 2)

    def test_two_worktrees_of_one_repo_share_runtime_and_second_claim_is_refused(self):
        repo, wt2 = self.root / "repo", self.root / "wt2"
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / "a.txt").write_text("a", encoding="utf-8")
        git(repo, "add", "a.txt")
        git(repo, "commit", "-q", "-m", "init")
        git(repo, "worktree", "add", "-q", "-b", "other", str(wt2))
        rt1, rt2 = S.runtime_root(repo), S.runtime_root(wt2)
        self.assertEqual(rt1, rt2)
        self.assertEqual(rt1, (repo / ".git").resolve() / "specflow-runtime")
        self.assertEqual(S.runs_root(rt1), rt1 / "runs")
        self.assertEqual(rt1.parent, (repo / ".git").resolve())                      # inside the git common dir, not in the work tree
        ready, stop = self.root / "ready", self.root / "stop"
        holder = self.spawn("hold", "auto", "task", "A4", ready, stop, "0", cwd=repo)
        self.wait_for(ready.exists, what="the holder to take the claim")
        rc, out = self.finish(self.spawn("try", "auto", "task", "A4", cwd=wt2))
        self.assertEqual(rc, 3, out)
        self.assertIn(str(repo.resolve()), out)                                      # it says who holds it
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True,
                                        text=True).stdout, "")                       # the runtime is not in Git
        stop.write_text("stop")
        self.assertEqual(self.finish(holder)[0], 0)
        self.assertEqual(self.finish(self.spawn("try", "auto", "task", "A4", cwd=wt2))[0], 0)

    def test_claim_held_until_owner_tree_is_confirmed_dead(self):
        ready, stop = self.root / "ready", self.root / "stop"
        holder = self.spawn("hold", self.rt, "task", "A4", ready, stop, "1")
        self.wait_for(ready.exists, what="the holder to take the claim")
        child = json.loads(ready.read_text(encoding="utf-8"))["child"]
        self.pids.append(child)
        with self.assertRaises(S.ClaimHeld):                     # owner alive: the OS lock is still held
            S.acquire_claim(self.rt, "task", "A4")
        holder.kill()                                            # kill only the owner; the child is still writing
        holder.wait()
        self.assertIs(S.process_alive(child, None), True)
        with self.assertRaises(S.ClaimHeld):                     # the OS lock is gone but the process tree has not stopped: still no redispatch
            S.acquire_claim(self.rt, "task", "A4")
        kill_pid(child)
        self.wait_for(lambda: S.process_alive(child, None) is False, what="the child to end")
        c = S.acquire_claim(self.rt, "task", "A4", run_id="next")   # takeover only after the whole tree is confirmed stopped
        self.assertEqual(c.previous["run_id"], "holder")
        c.release()

    def test_killed_owner_without_children_releases(self):
        ready, stop = self.root / "ready", self.root / "stop"
        holder = self.spawn("hold", self.rt, "task", "A5", ready, stop, "0")
        self.wait_for(ready.exists, what="the holder to take the claim")
        holder.kill()
        holder.wait()

        def take():  # Windows may release a dead process's file lock a little late: retry briefly instead of failing the first time
            try:
                S.acquire_claim(self.rt, "task", "A5").release()
                return True
            except S.ClaimHeld:
                return False
        self.wait_for(take, timeout=10, what="takeover after the owner died")

    @unittest.skipIf(os.name == "nt", "on Windows the process tree is managed by a Job Object (the whole tree stops when the runner dies); no process group is consulted")
    def test_registered_group_keeps_claim_after_its_leader_exits(self):
        """POSIX: the child registered as a group has exited but a grandchild in the same group still runs -> the claim still blocks; it is released only when the whole group stopped."""
        gfile = self.root / "grand"
        leader = subprocess.Popen(
            [sys.executable, "-c", "import subprocess, sys, pathlib\n"
             "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
             "pathlib.Path(sys.argv[1]).write_text(str(g.pid))\n", str(gfile)], start_new_session=True)
        self.procs.append(leader)
        c = S.acquire_claim(self.rt, "task", "A13")
        c.add_process(leader.pid, group=True)
        self.assertTrue(c.meta["children"][0]["group"])
        leader.wait()
        grand = int(gfile.read_text())
        self.pids.append(grand)
        self.assertFalse(c.release())                             # the leader has stopped, someone in the group is left
        with self.assertRaises(S.ClaimHeld):
            S.acquire_claim(self.rt, "task", "A13")
        os.killpg(leader.pid, signal.SIGKILL)
        self.wait_for(lambda: S.group_alive(leader.pid) is False, what="the group to stop")
        S.acquire_claim(self.rt, "task", "A13").release()

    def test_release_keeps_claim_while_registered_child_alive(self):
        c = S.acquire_claim(self.rt, "task", "A6")
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        self.procs.append(p)
        c.add_process(p.pid)
        self.assertFalse(c.release())                            # the child is still alive: not released
        with self.assertRaises(S.ClaimHeld):
            S.acquire_claim(self.rt, "task", "A6")
        p.kill()
        p.wait()
        S.acquire_claim(self.rt, "task", "A6").release()

    def test_live_owner_blocks_even_without_os_lock_and_age_does_not_matter(self):
        self.plant("task", "A7")                                # this very process, taken in the year 2000
        with self.assertRaises(S.ClaimHeld):
            S.acquire_claim(self.rt, "task", "A7")

    def test_reused_pid_is_not_the_owner(self):
        self.plant("task", "A8", start="0-other-process")
        c = S.acquire_claim(self.rt, "task", "A8")
        self.assertEqual(c.previous["run_id"], "planted")
        c.release()

    def test_unknown_liveness_counts_as_alive(self):
        self.plant("task", "A9", pid=424242, start=None)
        with mock.patch.object(S, "process_alive", return_value=None):
            with self.assertRaises(S.ClaimHeld):
                S.acquire_claim(self.rt, "task", "A9")

    def test_other_machine_record_is_never_honored(self):
        self.plant("task", "A10", machine="another-machine")
        with mock.patch.object(S, "process_alive", side_effect=AssertionError("must not look up another machine's PID")):
            c = S.acquire_claim(self.rt, "task", "A10")
        self.assertEqual(c.meta["machine"], S.MACHINE)
        c.release()

    def test_corrupt_metadata_is_not_taken_over(self):
        f = self.meta_file("task", "A11")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{ broken", encoding="utf-8")
        with self.assertRaises(S.ClaimHeld):
            S.acquire_claim(self.rt, "task", "A11")

    def test_list_claims(self):
        self.assertEqual(S.list_claims(self.rt), [])
        c = S.acquire_claim(self.rt, "task", "A12", run_id="r", worktree=self.root)
        rows = S.list_claims(self.rt)
        self.assertEqual([(m["kind"], m["name"], m["run_id"], m["alive"]) for m in rows], [("task", "A12", "r", True)])
        c.release()
        self.assertEqual(S.list_claims(self.rt), [])

    def test_runtime_root_requires_git(self):
        with self.assertRaises(S.StateError):
            S.runtime_root(self.root)


if __name__ == "__main__":
    unittest.main()
