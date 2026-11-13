"""Tests for registry.py and the status-transition rules behind it (state_io, workflow_config). Run:
python -m unittest discover -s plugins/specflow/tests -p "test_registry.py"   (stdlib only)

Every product lives in a temp git repo (locks and claims live in its git common dir); concurrency uses real processes.
"""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
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


R = _load("registry", "specflow_registry")
S = R.state_io            # the copies registry.py itself uses (exception classes, KEEP)
WC = R.workflow_config

ORDER = ["Not started", "In progress", "Awaiting integration", "Awaiting hardware", "Done"]
STATES = {"order": ORDER, "workable": ORDER[:2], "dep_ok": ORDER[2:]}
RULES = [
    {"from": "Not started", "to": "In progress", "needs": ["dispatch"]},
    {"from": "In progress", "to": "Awaiting integration", "needs": ["g4", "g5", "merge"]},
    {"from": "Awaiting integration", "to": "Awaiting hardware", "needs": ["int"]},
    {"from": "Awaiting hardware", "to": "Done", "needs": ["e2e", "manual"], "repos": ["app"]},
    {"from": "Awaiting hardware", "to": "Done", "needs": ["manual"]},
    {"from": "Done", "to": "Awaiting hardware", "rework": True},
]
KINDS = {"manual": {"human": True}}
POLICY = {"schema_version": 1,
          "repos": {"spec": {"integration_branch": "main", "home": True}, "app": {"integration_branch": "main"}},
          "tools": {"git": {}},
          "capabilities": {"spec_work": {"tools": ["git"], "repos": []}},
          "dispatch": {"timeout_seconds": 600}}

# Child process: sets its own component through the CLI, once with a rule and evidence, then n times in a row.
WORKER = r'''
import importlib.util, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("specflow_registry", sys.argv[1])
R = importlib.util.module_from_spec(spec)
sys.modules["specflow_registry"] = R
spec.loader.exec_module(R)
product, go, cid, n = sys.argv[2], Path(sys.argv[3]), sys.argv[4], int(sys.argv[5])
while not go.exists():
    time.sleep(0.002)
rc = R.main(["--product", product, "set", cid, "--status", "In progress", "--evidence", f"dispatch=evidence/{cid}.txt"])
if rc:
    sys.exit(rc)
for k in range(n):
    rc = R.main(["--product", product, "set", cid, "--block", f"{cid}-{k}"])
    if rc:
        sys.exit(rc)
'''


def sha(b):
    return hashlib.sha256(b).hexdigest()


def comp(cid, status="Not started", repo="app", deps=(), blocked=None):
    return {"id": cid, "name": cid.lower(), "spec": f"{cid}-x.md", "repo": repo,
            "deps": list(deps), "status": status, "blocked": blocked}


def policy(rules=RULES, kinds=KINDS):
    p = copy.deepcopy(POLICY)
    if rules is not None:
        p["transitions"] = copy.deepcopy(rules)
    if kinds is not None:
        p["evidence_kinds"] = copy.deepcopy(kinds)
    return p


class Product(unittest.TestCase):
    """tmp/proj: a git repo (the evidence root) holding the product folder proj/spec."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.product = self.root / "spec"
        self.product.mkdir()
        self.reg = self.product / "components.json"
        self.set_policy(policy())

    def set_policy(self, pol):
        (self.product / "workflow.json").write_text(json.dumps(pol), encoding="utf-8")

    def save(self, comps):
        """Fixture: put a components.json in place directly (real writes always go through the setter)."""
        data = {"format": 1, "states": STATES,
                "repos": {"app": {"integration_branch": "main"}, "spec": {"integration_branch": "main"}},
                "components": comps}
        for c in comps:
            (self.product / c["spec"]).write_text("x", encoding="utf-8")
        self.reg.write_bytes(S.dump_registry(data))
        return data

    def ev(self, rel, data=b"ok"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return rel

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = R.main(["--product", str(self.product), *map(str, argv)])
        return rc, out.getvalue() + err.getvalue()

    def row(self, cid):
        return next(c for c in json.loads(self.reg.read_text(encoding="utf-8"))["components"] if c["id"] == cid)

    def revision(self):
        rc, out = self.cli("show")
        self.assertEqual(rc, 0, out)
        return re.search(r"^revision: ([0-9a-f]{64})$", out, re.M).group(1)


class TransitionTest(Product):
    def test_legal_transition_with_evidence(self):
        self.save([comp("A1")])
        self.ev("evidence/A1/run1.json", b"{}")
        rc, out = self.cli("set", "A1", "--status", "In progress", "--evidence", "dispatch=evidence/A1/run1.json")
        self.assertEqual(rc, 0, out)
        self.assertIn("forward", out)
        self.assertEqual(self.row("A1")["status"], "In progress")
        self.assertEqual(self.row("A1")["evidence"],
                         [{"kind": "dispatch", "path": "evidence/A1/run1.json", "sha256": sha(b"{}")}])

    def test_skipped_step_is_refused_and_lists_where_it_can_go(self):
        self.save([comp("A1")])
        self.ev("evidence/A1/e2e.txt")
        self.ev("evidence/A1/manual.md")
        before = self.reg.read_bytes()
        rc, out = self.cli("set", "A1", "--status", "Done", "--evidence", "e2e=evidence/A1/e2e.txt",
                           "--evidence", "manual=evidence/A1/manual.md")
        self.assertEqual(rc, 1, out)
        self.assertIn("allowed from", out)
        self.assertIn("In progress", out)
        self.assertEqual(self.reg.read_bytes(), before)

    def test_missing_evidence_is_refused(self):
        self.save([comp("A1"), comp("B1", "In progress")])
        self.ev("evidence/A1/run1.json")
        self.ev("evidence/B1/g4.txt")
        self.ev("evidence/B1/g5.txt")
        before = self.reg.read_bytes()
        rc, out = self.cli("set", "A1", "--status", "In progress")                            # no evidence at all
        self.assertEqual(rc, 4, out)
        self.assertIn("dispatch", out)
        rc, out = self.cli("set", "A1", "--status", "In progress", "--evidence", "g4=evidence/A1/run1.json")
        self.assertEqual(rc, 4, out)                                                         # wrong kind does not count
        rc, out = self.cli("set", "A1", "--status", "In progress", "--evidence", "dispatch=evidence/A1/none.json")
        self.assertEqual(rc, 4, out)                                                         # file does not exist
        rc, out = self.cli("set", "B1", "--status", "Awaiting integration",
                           "--evidence", "g4=evidence/B1/g4.txt", "--evidence", "g5=evidence/B1/g5.txt")
        self.assertEqual(rc, 4, out)                                                         # one of three missing
        self.assertIn("merge", out)
        self.assertEqual(self.reg.read_bytes(), before)

    def test_hash_mismatch_is_refused(self):
        self.save([comp("A1")])
        self.ev("evidence/A1/run1.json", b"one")
        stale = R.evidence_ref(self.product, "dispatch", "evidence/A1/run1.json")
        self.ev("evidence/A1/run1.json", b"changed after it was referenced")
        before = self.reg.read_bytes()
        with self.assertRaises(S.EvidenceMissing) as cm:
            R.set_component(self.product, "A1", status="In progress", evidence=[stale])
        self.assertEqual(cm.exception.code, 4)
        self.assertIn("sha256", str(cm.exception))
        self.assertEqual(self.reg.read_bytes(), before)
        # a reference that was right when it was made, but whose file changed later, fails check
        R.set_component(self.product, "A1", status="In progress",
                        evidence=[R.evidence_ref(self.product, "dispatch", "evidence/A1/run1.json")])
        self.ev("evidence/A1/run1.json", b"tampered")
        rc, out = self.cli("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("sha256", out)

    def test_repo_filter(self):
        self.save([comp("A1", "Awaiting hardware"), comp("B1", "Awaiting hardware", repo="spec")])
        self.ev("evidence/manual.md")
        # the rule limited to app wins for an app component: manual alone is not enough there
        rc, out = self.cli("set", "A1", "--status", "Done", "--evidence", "manual=evidence/manual.md")
        self.assertEqual(rc, 4, out)
        self.assertIn("e2e", out)
        # another repo gets the general rule
        rc, out = self.cli("set", "B1", "--status", "Done", "--evidence", "manual=evidence/manual.md")
        self.assertEqual(rc, 0, out)
        # without a general rule, the app-only rule does not apply to other repos
        self.set_policy(policy([r for i, r in enumerate(RULES) if i != 4]))
        self.assertEqual(self.cli("set", "B1", "--status", "Awaiting hardware", "--reason", "went red")[0], 0)
        rc, out = self.cli("set", "B1", "--status", "Done", "--evidence", "manual=evidence/manual.md")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.row("B1")["status"], "Awaiting hardware")

    def test_rework_needs_reason_and_reports_direction(self):
        self.save([comp("A1", "Awaiting hardware")])
        self.ev("evidence/A1/e2e.txt")
        self.ev("evidence/A1/manual.md")
        rc, out = self.cli("set", "A1", "--status", "Done", "--evidence", "e2e=evidence/A1/e2e.txt",
                           "--evidence", "manual=evidence/A1/manual.md")
        self.assertEqual(rc, 0, out)
        self.assertIn("forward", out)
        before = self.reg.read_bytes()
        rc, out = self.cli("set", "A1", "--status", "Awaiting hardware")                     # rework without a reason
        self.assertEqual(rc, 1, out)
        self.assertIn("reason", out)
        self.assertEqual(self.reg.read_bytes(), before)
        res = R.set_component(self.product, "A1", status="Awaiting hardware", reason="end-to-end went red")
        self.assertEqual((res["direction"], res["status_before"], res["component"]["status"], res["reason"]),
                         ("rework", "Done", "Awaiting hardware", "end-to-end went red"))
        self.assertEqual([e["kind"] for e in res["component"]["evidence"]], ["e2e", "manual"])   # kept, never dropped
        rc, out = self.cli("set", "A1", "--status", "Done", "--evidence", "e2e=evidence/A1/e2e.txt",
                           "--evidence", "manual=evidence/A1/manual.md")
        self.assertEqual(rc, 0, out)
        rc, out = self.cli("set", "A1", "--status", "Awaiting hardware", "--reason", "red again")
        self.assertEqual(rc, 0, out)
        self.assertIn("rework", out)
        self.assertIn("reason: red again", out)
        rc, out = self.cli("set", "A1", "--status", "Done", "--evidence", "e2e=evidence/A1/e2e.txt",
                           "--evidence", "manual=evidence/A1/manual.md")
        self.assertEqual(rc, 0, out)
        rc, out = self.cli("set", "A1", "--status", "Awaiting hardware", "--reason", "red a third time", "--json")
        self.assertEqual(rc, 0, out)
        got = json.loads(out)
        self.assertEqual((got["direction"], got["reason"], got["status_before"]), ("rework", "red a third time", "Done"))

    def test_same_status_is_not_a_transition(self):
        self.save([comp("A1")])
        res = R.set_component(self.product, "A1", status="Not started", blocked="waiting for the owner")
        self.assertIsNone(res["direction"])
        self.assertEqual(self.row("A1")["blocked"], "waiting for the owner")


class NoRulesTest(Product):
    def setUp(self):
        super().setUp()
        self.set_policy(policy(None, None))

    def test_any_status_in_order_with_a_note(self):
        self.save([comp("A1")])
        rc, out = self.cli("set", "A1", "--status", "Done")
        self.assertEqual(rc, 0, out)
        self.assertIn("no transition rules", out)
        self.assertEqual(self.row("A1")["status"], "Done")
        self.assertEqual(self.cli("set", "A1", "--status", "Finished")[0], 1)
        self.assertIsNone(R.set_component(self.product, "A1", status="Not started")["direction"])
        rc, out = self.cli("check")
        self.assertEqual(rc, 0, out)
        self.assertIn("no transition rules", out)

    def test_block_unblock_deps_and_evidence_as_before(self):
        self.save([comp("A1"), comp("B1"), comp("C1", deps=["A1"])])
        self.assertEqual(self.cli("set", "C1", "--block", "waiting for the owner")[0], 0)
        self.assertEqual(self.row("C1")["blocked"], "waiting for the owner")
        self.assertEqual(self.cli("set", "C1", "--unblock")[0], 0)
        self.assertIsNone(self.row("C1")["blocked"])
        self.assertEqual(self.cli("set", "C1", "--deps", "A1", "B1")[0], 0)
        self.assertEqual(self.row("C1")["deps"], ["A1", "B1"])
        before = self.reg.read_bytes()
        for bad in (["A1", "--deps", "C1"], ["C1", "--deps", "Z9"], ["C1", "--deps", "C1"]):   # cycle, unknown, itself
            self.assertEqual(self.cli("set", *bad)[0], 1, bad)
        self.assertEqual(self.reg.read_bytes(), before)
        self.assertEqual(self.cli("set", "C1", "--deps")[0], 0)
        self.assertEqual(self.row("C1")["deps"], [])
        self.ev("evidence/C1/r.txt")
        self.assertEqual(self.cli("set", "C1", "--evidence", "note=evidence/C1/r.txt")[0], 0)
        self.assertEqual(self.row("C1")["evidence"][0]["kind"], "note")
        self.assertEqual(self.row("C1")["status"], "Not started")


class RuleStateTest(Product):
    def test_rule_naming_an_unknown_state(self):
        self.set_policy(policy(RULES + [{"from": "Done", "to": "Shipped"}]))
        self.save([comp("A1")])
        rc, out = self.cli("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("Shipped", out)
        self.ev("evidence/A1/run1.json")
        before = self.reg.read_bytes()
        rc, out = self.cli("set", "A1", "--status", "In progress", "--evidence", "dispatch=evidence/A1/run1.json")
        self.assertEqual(rc, 2, out)                         # the rules cannot be trusted: no status change at all
        self.assertIn("Shipped", out)
        self.assertEqual(self.reg.read_bytes(), before)
        self.assertEqual(self.cli("set", "A1", "--block", "fix the rules first")[0], 0)   # rules apply to status only

    def test_rule_repo_missing_from_components_repos(self):
        """A rule limited to a repo id components.json does not have never applies; a weaker general rule would be used
        instead. check fails and status changes are refused."""
        pol = policy([dict(r, repos=["web"]) if r.get("repos") else r for r in RULES])
        pol["repos"]["web"] = {"integration_branch": "main"}
        self.set_policy(pol)
        self.save([comp("A1", "Awaiting hardware")])
        rc, out = self.cli("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("web", out)
        self.ev("evidence/manual.md")
        before = self.reg.read_bytes()
        rc, out = self.cli("set", "A1", "--status", "Done", "--evidence", "manual=evidence/manual.md")
        self.assertEqual(rc, 2, out)
        self.assertIn("web", out)
        self.assertEqual(self.reg.read_bytes(), before)

    def test_rework_rule_must_move_back(self):
        """A rework rule needs no evidence, so one that moves forward in states.order would be an evidence-free jump."""
        self.set_policy(policy(RULES + [{"from": "Not started", "to": "Done", "rework": True}]))
        self.save([comp("A1")])
        rc, out = self.cli("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("Not started -> Done", out)
        before = self.reg.read_bytes()
        rc, out = self.cli("set", "A1", "--status", "Done", "--reason", "skip ahead")
        self.assertEqual(rc, 2, out)
        self.assertEqual(self.reg.read_bytes(), before)


class EvidenceAccumulatesTest(Product):
    def test_evidence_only_set_never_removes_refs(self):
        self.save([comp("A1", "Awaiting hardware")])
        self.ev("evidence/A1/e2e.txt")
        self.ev("evidence/A1/manual.md")
        self.ev("evidence/A1/note.txt")
        rc, out = self.cli("set", "A1", "--status", "Done", "--evidence", "e2e=evidence/A1/e2e.txt",
                           "--evidence", "manual=evidence/A1/manual.md")
        self.assertEqual(rc, 0, out)
        proof = self.row("A1")["evidence"]
        rc, out = self.cli("set", "A1", "--evidence", "note=evidence/A1/note.txt")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.row("A1")["evidence"][:2], proof)                   # the acceptance proof stays
        self.assertEqual(self.row("A1")["evidence"][2]["kind"], "note")
        self.assertEqual(self.cli("set", "A1", "--evidence", "note=evidence/A1/note.txt")[0], 0)
        self.assertEqual(len(self.row("A1")["evidence"]), 3)                      # an identical ref is not repeated

    def test_forward_step_keeps_earlier_refs_but_needs_its_own(self):
        self.save([comp("A1")])
        for k in ("dispatch", "g4", "g5", "merge"):
            self.ev(f"evidence/A1/{k}.txt")
        self.assertEqual(self.cli("set", "A1", "--status", "In progress",
                                  "--evidence", "dispatch=evidence/A1/dispatch.txt")[0], 0)
        self.assertEqual(self.cli("set", "A1", "--evidence", "g4=evidence/A1/g4.txt")[0], 0)
        rc, out = self.cli("set", "A1", "--status", "Awaiting integration",       # g4 on the row does not count
                           "--evidence", "g5=evidence/A1/g5.txt", "--evidence", "merge=evidence/A1/merge.txt")
        self.assertEqual(rc, 4, out)
        self.assertIn("g4", out)
        rc, out = self.cli("set", "A1", "--status", "Awaiting integration", "--evidence", "g4=evidence/A1/g4.txt",
                           "--evidence", "g5=evidence/A1/g5.txt", "--evidence", "merge=evidence/A1/merge.txt")
        self.assertEqual(rc, 0, out)
        self.assertEqual([e["kind"] for e in self.row("A1")["evidence"]], ["dispatch", "g4", "g5", "merge"])


class SiblingLoadTest(unittest.TestCase):
    """One state_io per process for every core module that loads it; a same-named module from another path (another
    plugin version) is never reused; a failed load leaves no half module behind."""
    LOADERS = ("registry.py", "process_runner.py")

    def test_one_state_io_per_process(self):
        load = ("import importlib.util, sys\n"
                "def load(name, path):\n"
                "    spec = importlib.util.spec_from_file_location(name, path)\n"
                "    m = importlib.util.module_from_spec(spec)\n"
                "    sys.modules[name] = m\n"
                "    spec.loader.exec_module(m)\n"
                "    return m\n")
        for a, b in (self.LOADERS, self.LOADERS[::-1]):
            code = load + (f"A = load('probe_a', {str(SCRIPTS / a)!r})\n"
                           f"B = load('probe_b', {str(SCRIPTS / b)!r})\n"
                           "print(A.state_io is B.state_io, A.state_io.KEEP is B.state_io.KEEP)\n")
            with self.subTest(order=(a, b)):
                r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
                self.assertEqual(r.stdout.split(), ["True", "True"], r.stderr)

    def test_same_name_from_another_path_is_not_reused(self):
        for script in self.LOADERS:
            with self.subTest(script), tempfile.TemporaryDirectory() as d:
                fake = types.ModuleType("specflow_state_io")
                fake.__file__ = str(Path(d) / "state_io.py")
                with mock.patch.dict(sys.modules, {"specflow_state_io": fake}):
                    spec = importlib.util.spec_from_file_location("specflow_probe", SCRIPTS / script)
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    self.assertIsNot(mod.state_io, fake)
                    self.assertEqual(Path(mod.state_io.__file__).resolve(), (SCRIPTS / "state_io.py").resolve())
                    self.assertIs(sys.modules["specflow_state_io"], mod.state_io)

    def test_failed_load_leaves_no_broken_module(self):
        for script in self.LOADERS:
            for had_one in (True, False):
                with self.subTest(script=script, had_one=had_one), tempfile.TemporaryDirectory() as d, \
                        mock.patch.dict(sys.modules):
                    shutil.copy(SCRIPTS / script, d)
                    (Path(d) / "state_io.py").write_text("raise RuntimeError('broken sibling')\n", encoding="utf-8")
                    if not had_one:
                        sys.modules.pop("specflow_state_io", None)
                    before = sys.modules.get("specflow_state_io")
                    spec = importlib.util.spec_from_file_location("specflow_probe", Path(d) / script)
                    with self.assertRaises(RuntimeError):
                        spec.loader.exec_module(importlib.util.module_from_spec(spec))
                    self.assertIs(sys.modules.get("specflow_state_io"), before)


class HumanKindTest(Product):
    def test_human_kind_is_never_generated(self):
        self.save([comp("A1", "Awaiting hardware")])
        ref = R.record_evidence(self.product, "e2e", "evidence/A1/e2e.log", b"pass")          # a machine kind: written
        self.assertEqual((ref["kind"], (self.root / "evidence/A1/e2e.log").read_bytes()), ("e2e", b"pass"))
        manual = self.root / "evidence/A1/manual.md"
        with self.assertRaises(S.ChangeRejected):                                             # a human kind: never
            R.record_evidence(self.product, "manual", "evidence/A1/manual.md", b"accepted")
        self.assertFalse(manual.exists())
        rc, out = self.cli("set", "A1", "--status", "Done", "--evidence", "e2e=evidence/A1/e2e.log",
                           "--evidence", "manual=evidence/A1/manual.md")
        self.assertEqual(rc, 4, out)                                                          # nobody wrote it yet
        self.assertFalse(manual.exists())                                                     # and set did not either
        manual.write_text("checked by hand\n", encoding="utf-8")                              # a person writes it
        rc, out = self.cli("set", "A1", "--status", "Done", "--evidence", "e2e=evidence/A1/e2e.log",
                           "--evidence", "manual=evidence/A1/manual.md")
        self.assertEqual(rc, 0, out)
        self.assertIn({"kind": "manual", "path": "evidence/A1/manual.md", "sha256": sha(manual.read_bytes())},
                      self.row("A1")["evidence"])


class StaleReadTest(Product):
    def test_rules_are_checked_against_the_row_under_the_lock(self):
        """The caller read A1 at Not started; before its write takes the lock another process moves A1 on. The rule is
        decided on the row read under the lock (Awaiting integration -> In progress: no rule), not on the stale read."""
        self.save([comp("A1")])
        for k in ("dispatch", "g4", "g5", "merge"):
            self.ev(f"evidence/A1/{k}.txt")
        rev0 = self.revision()
        other = [["set", "A1", "--status", "In progress", "--evidence", "dispatch=evidence/A1/dispatch.txt"],
                 ["set", "A1", "--status", "Awaiting integration", "--evidence", "g4=evidence/A1/g4.txt",
                  "--evidence", "g5=evidence/A1/g5.txt", "--evidence", "merge=evidence/A1/merge.txt"]]
        real, fired = S.file_lock, []

        @contextlib.contextmanager
        def racing(path, timeout=S.LOCK_TIMEOUT):
            if not fired and Path(path).name == "registry.lock":
                for argv in other:    # another process gets in between this writer's start and its lock
                    fired.append(subprocess.run([sys.executable, str(SCRIPTS / "registry.py"), "--product",
                                                 str(self.product), *argv], capture_output=True, text=True,
                                                encoding="utf-8", errors="replace"))
            with real(path, timeout):
                yield
        with mock.patch.object(S, "file_lock", racing):
            rc, out = self.cli("set", "A1", "--status", "In progress", "--evidence", "dispatch=evidence/A1/dispatch.txt")
        self.assertEqual([r.returncode for r in fired], [0, 0], [r.stdout + r.stderr for r in fired])
        self.assertEqual(rc, 1, out)
        self.assertIn("no transition rule", out)
        self.assertEqual(self.row("A1")["status"], "Awaiting integration")
        rc, out = self.cli("set", "A1", "--block", "x", "--expect", rev0)                     # CAS with the stale revision
        self.assertEqual(rc, 3, out)


class CasTest(Product):
    def test_expect_turns_on_cas(self):
        self.save([comp("A1"), comp("B1")])
        rev = self.revision()
        self.assertEqual(self.cli("set", "A1", "--block", "x", "--expect", rev)[0], 0)
        rc, out = self.cli("set", "B1", "--block", "y", "--expect", rev)
        self.assertEqual(rc, 3, out)
        self.assertIn(self.revision(), out)                   # the current revision, to re-read and retry with
        self.assertIsNone(self.row("B1")["blocked"])
        self.assertEqual(self.cli("set", "B1", "--block", "y")[0], 0)   # no --expect: applied to the current file
        self.assertEqual((self.row("A1")["blocked"], self.row("B1")["blocked"]), ("x", "y"))


class ConcurrencyTest(Product):
    def test_parallel_set_loses_nothing(self):
        n, k = 6, 10
        self.save([comp(f"C{i}") for i in range(n)])
        for i in range(n):
            self.ev(f"evidence/C{i}.txt", f"run {i}".encode())
        worker = self.root.parent / "worker.py"
        worker.write_text(WORKER, encoding="utf-8")
        go = self.root.parent / "go"
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        ps = []
        for i in range(n):
            log = open(self.root.parent / f"w{i}.log", "wb")
            self.addCleanup(log.close)
            p = subprocess.Popen([sys.executable, str(worker), str(SCRIPTS / "registry.py"), str(self.product), str(go),
                                  f"C{i}", str(k)], cwd=self.root, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT)
            p.log = log.name
            ps.append(p)
        go.write_text("go")
        for p in ps:
            self.assertEqual(p.wait(timeout=120), 0, Path(p.log).read_text(encoding="utf-8", errors="replace"))
        rows = json.loads(self.reg.read_text(encoding="utf-8"))["components"]
        self.assertEqual({c["id"]: (c["status"], c["blocked"], c["evidence"][0]["kind"]) for c in rows},
                         {f"C{i}": ("In progress", f"C{i}-{k - 1}", "dispatch") for i in range(n)})
        self.assertEqual(list(self.product.glob("*" + S.TMP_SUFFIX)), [])


class CommandsTest(Product):
    def test_show_lists_components_next_and_revision(self):
        self.save([comp("A1", "Done"), comp("B1", deps=["A1"]), comp("C1", deps=["D1"]),
                   comp("D1", blocked="waiting for parts"), comp("E1", "In progress", deps=["A1"])])
        rc, out = self.cli("show")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"C1 .*waits for: D1")
        self.assertRegex(out, r"D1 .*blocked: waiting for parts")
        self.assertIn("next: E1", out)                        # in progress first, then list order
        self.assertEqual(self.revision(), sha(self.reg.read_bytes()))
        self.assertEqual([c["id"] for c in R.candidates(json.loads(self.reg.read_text(encoding="utf-8")))],
                         ["E1", "B1"])

    def test_check_reports_problems(self):
        data = self.save([comp("A1", deps=["B1"]), comp("B1", deps=["A1"]), comp("C1", "Finished", deps=["Z9"])])
        data["components"].append(comp("C1"))
        data["components"][0]["blocked"] = " "
        (self.product / "B1-x.md").unlink()
        self.reg.write_bytes(S.dump_registry(data))
        rc, out = self.cli("check")
        self.assertEqual(rc, 1, out)
        for want in ("duplicate id: C1", "Finished", "Z9", "cycle", "A1: blocked", "B1-x.md"):
            self.assertIn(want, out)
        self.assertEqual(self.cli("show")[0], 1)              # show refuses a broken registry too

    def test_check_lists_legacy_as_warning_not_failure(self):
        self.save([comp("A1", "Awaiting integration"), comp("B1")])
        rc, out = self.cli("check")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"legacy: A1 .*g4, g5, merge")
        self.assertNotIn("legacy: B1", out)                   # the first status has no rule into it

    def test_rework_rule_does_not_hide_a_legacy_row(self):
        """A rework rule needs no evidence, so it is no way into a status that a row without evidence could have taken."""
        self.save([comp("A1", "Awaiting hardware")])     # forward rule needs int; rework Done -> here needs nothing
        rc, out = self.cli("check")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"legacy: A1 .*from Awaiting integration: int")
        self.assertNotIn("from Done", out)
        self.set_policy(policy([{"from": "Not started", "to": "In progress", "needs": ["dispatch"]},
                                {"from": "In progress", "to": "Not started", "rework": True}]))
        self.save([comp("B1")])                          # only a rework rule leads into Not started: no warning
        rc, out = self.cli("check")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("legacy", out)

    def test_add_repo_and_claims(self):
        self.save([comp("A1", "Done")])
        (self.product / "N1-new.md").write_text("x", encoding="utf-8")
        argv = ["add", "N1", "--name", "new", "--spec", "N1-new.md", "--repo", "app", "--deps", "A1"]
        self.assertEqual(self.cli(*argv)[0], 0)
        self.assertEqual(self.row("N1"), {"id": "N1", "name": "new", "spec": "N1-new.md", "repo": "app",
                                          "deps": ["A1"], "status": "Not started", "blocked": None})
        self.assertEqual(self.cli(*argv)[0], 1)               # duplicate id
        rc, _ = self.cli("repo", "app", "--integration-branch", "dev", "--baseline", "t unit", "--baseline", "t int")
        self.assertEqual(rc, 0)
        reg = json.loads(self.reg.read_text(encoding="utf-8"))["repos"]["app"]
        self.assertEqual((reg["integration_branch"], reg["baseline"]), ("dev", ["t unit", "t int"]))
        self.assertEqual(self.cli("repo", "nope", "--integration-branch", "x")[0], 1)
        rc, out = self.cli("claims")
        self.assertEqual((rc, "no claims" in out), (0, True))
        c = S.acquire_claim(S.runtime_root(self.product), "task", "A1", run_id="r9")
        try:
            rc, out = self.cli("claims")
        finally:
            c.release()
        self.assertEqual(rc, 0)
        self.assertIn("task A1", out)
        self.assertIn("r9", out)

    def test_input_errors(self):
        self.save([comp("A1")])
        self.assertEqual(self.cli("set", "A1")[0], 2)                         # nothing to change
        self.assertEqual(self.cli("set", "A1", "--evidence", "no-kind")[0], 2)  # not KIND=PATH
        self.assertEqual(self.cli("set", "Z9", "--block", "x")[0], 1)         # no such component
        (self.product / "workflow.json").unlink()
        self.assertEqual(self.cli("set", "A1", "--block", "x")[0], 4)         # no policy: nothing is decided
        self.assertIsNone(self.row("A1")["blocked"])

    def test_product_after_subcommand(self):
        self.save([comp("A1")])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = R.main(["show", "--product", str(self.product)])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("A1", out.getvalue())


class TransitionSchemaTest(unittest.TestCase):
    def test_rules_validate_and_are_optional(self):
        self.assertEqual(WC.validate_policy(policy()), [])
        self.assertEqual(WC.validate_policy(policy(None, None)), [])

    def test_bad_rules_are_reported(self):
        cases = {
            "empty": [],
            "not an array": {"from": "a", "to": "b"},
            "not an object": ["a->b"],
            "no to": [{"from": "a"}],
            "blank from": [{"from": " ", "to": "b"}],
            "to itself": [{"from": "a", "to": "a"}],
            "unknown field": [{"from": "a", "to": "b", "need": ["x"]}],
            "needs not array": [{"from": "a", "to": "b", "needs": "x"}],
            "kind with =": [{"from": "a", "to": "b", "needs": ["x=y"]}],
            "duplicate kind": [{"from": "a", "to": "b", "needs": ["x", "x"]}],
            "unknown repo": [{"from": "a", "to": "b", "repos": ["nope"]}],
            "empty repos": [{"from": "a", "to": "b", "repos": []}],
            "rework not bool": [{"from": "a", "to": "b", "rework": "yes"}],
            "two general rules": [{"from": "a", "to": "b"}, {"from": "a", "to": "b", "needs": ["x"]}],
            "overlapping repos": [{"from": "a", "to": "b", "repos": ["app"]},
                                  {"from": "a", "to": "b", "repos": ["app", "spec"]}],
        }
        for name, rules in cases.items():
            with self.subTest(name):
                errs = WC.validate_policy(policy(rules, None))
                self.assertTrue(errs and all("transitions" in e for e in errs), errs)

    def test_specific_and_general_rule_for_one_step_are_fine(self):
        rules = [{"from": "a", "to": "b", "repos": ["app"], "needs": ["x", "y"]}, {"from": "a", "to": "b", "needs": ["y"]},
                 {"from": "a", "to": "b", "repos": ["spec"]}]
        self.assertEqual(WC.validate_policy(policy(rules, None)), [])

    def test_bad_evidence_kinds_are_reported(self):
        for name, kinds in {"not an object": ["manual"], "value not object": {"manual": True},
                            "human not bool": {"manual": {"human": "yes"}}, "unknown field": {"manual": {"humane": True}},
                            "kind with =": {"a=b": {"human": True}}}.items():
            with self.subTest(name):
                errs = WC.validate_policy(policy(None, kinds))
                self.assertTrue(errs and all("evidence_kinds" in e for e in errs), errs)

    def test_state_fields_are_still_rejected(self):
        for k in ("status", "blocked", "deps", "components", "states"):
            with self.subTest(k):
                p = policy()
                p[k] = {}
                self.assertTrue(any(f'"{k}"' in e for e in WC.validate_policy(p)))


if __name__ == "__main__":
    unittest.main()
