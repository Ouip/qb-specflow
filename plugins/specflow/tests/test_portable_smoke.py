"""Portable smoke test: a temp sample project that is not firmware (a small Python package), built from the core
templates, doing a two-machine wrapup -> pickup round trip through bare remotes, plus negative cases. Run:
python -m unittest discover -s plugins/specflow/tests -p "test_portable_smoke.py"   (stdlib only, no network)

Every script runs as a subprocess (sys.executable <script>), as a skill or user would run it. The implementer is a fake
Python CLI. installed_plugins.json is a temp file passed with --installed; nothing in the real home folder is touched.
Machine A's clones sit under a path with a space and Chinese characters; machine B's path differs.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN / "scripts"
TEMPLATES = PLUGIN / "templates"
REPO_ROOT = PLUGIN.parent.parent

FAKE_IMPL = r'''
import json, sys
from pathlib import Path
argv = sys.argv[1:]
if "--version" in argv:
    print("fakeimpl 1.0")
    sys.exit(0)
opt = lambda n: argv[argv.index(n) + 1]
sys.stdin.read()
wt = Path(opt("--cd"))
(wt / "pkg" / "core.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8", newline="\n")
Path(opt("-o")).write_text(json.dumps({"task": "A1", "status": "done", "tests": "pass", "unmet": [],
                                       "notes": "added add()"}), encoding="utf-8")
'''

REPORT_SCHEMA = {
    "type": "object", "required": ["task", "status", "tests", "unmet", "notes"], "additionalProperties": False,
    "properties": {
        "task": {"type": "string"},
        "status": {"type": "string", "enum": ["done", "partial", "blocked"]},
        "tests": {"type": "string", "enum": ["pass", "fail", "na"]},
        "unmet": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", "-C", str(cwd), *args],
                       capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(map(str, args))} in {cwd}: {r.stdout}{r.stderr}")
    return r.stdout.strip()


def fill(obj, mapping):
    """Replace [FILL...] strings (values and object keys) the way a person filling the template would."""
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, list):
        return [fill(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {mapping.get(k, k): fill(v, mapping) for k, v in obj.items()}
    return obj


def bash_exe():
    cands = []
    git_path = shutil.which("git")
    if os.name == "nt" and git_path:
        cands.append(Path(git_path).resolve().parent.parent / "bin" / "bash.exe")
    cands.append(shutil.which("bash"))
    for c in cands:
        if c and Path(c).exists():
            try:
                r = subprocess.run([str(c), "-c", "echo ok"], capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0 and r.stdout.strip() == "ok":
                return str(c)
    return None


class World:
    """Bare remotes, machine A and machine B clones, the sample project built from the templates."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.remotes = tmp / "remotes"
        self.spec_remote, self.app_remote = self.remotes / "spec.git", self.remotes / "app.git"
        for r in (self.spec_remote, self.app_remote):
            r.mkdir(parents=True)
            git(r, "init", "-q", "--bare", "-b", "main")
        self.a_root = tmp / "機器 A 工作區"
        self.b_root = tmp / "machine-b"
        self.a_spec, self.a_app = self.a_root / "spec 規格", self.a_root / "app 程式"
        self.b_spec, self.b_app = self.b_root / "specrepo", self.b_root / "apprepo"
        self.a_prod, self.b_prod = self.a_spec / "specs", self.b_spec / "specs"
        self.bin = tmp / "bin"
        self.bin.mkdir()
        py = write(self.bin / "fakeimpl.py", FAKE_IMPL)
        if os.name == "nt":
            write(self.bin / "fakeimpl.cmd", f'@echo off\r\n"{sys.executable}" "{py}" %*\r\nexit /b %ERRORLEVEL%\r\n')
        else:
            cli = write(self.bin / "fakeimpl", f'#!/bin/sh\nexec "{sys.executable}" "{py}" "$@"\n')
            cli.chmod(0o755)
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                    "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        self.installed = self.tmp / "installed_plugins.json"
        self._installed()
        self._seed()
        for src, dst in ((self.spec_remote, self.b_spec), (self.app_remote, self.b_app)):
            dst.parent.mkdir(parents=True, exist_ok=True)
            git(self.tmp, "clone", "-q", "-c", "core.autocrlf=false", str(src), str(dst))
        self._local(self.a_spec, self.a_spec, self.a_app, "A")
        self._local(self.b_spec, self.b_spec, self.b_app, "B")

    # -- helpers
    def run(self, script, *args, cwd=None, env=None):
        r = subprocess.run([sys.executable, str(SCRIPTS / f"{script}.py"), *map(str, args)], cwd=str(cwd or self.tmp),
                           env=env or self.env, capture_output=True, text=True, encoding="utf-8")
        r.all = r.stdout + r.stderr
        return r

    def _installed(self):
        plugins = {"specflow@specflow": [{"scope": "user", "installPath": str(PLUGIN), "version": "0.1.0",
                                          "gitCommitSha": "a" * 40}]}
        for i, dep in enumerate(json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
                                .get("dependencies", [])):
            plugins[dep] = [{"scope": "user", "version": "1.0.0", "gitCommitSha": str(i + 1) * 40}]
        write(self.installed, json.dumps({"version": 2, "plugins": plugins}))

    def _local(self, home, spec, app, machine):
        write(home / "specflow.local.json", json.dumps({
            "schema_version": 1, "machine": machine,
            "repos": {"spec": {"path": str(spec)}, "app": {"path": str(app)}}}, ensure_ascii=False))

    def _seed(self):
        # app repo: a small Python package
        self.a_app.mkdir(parents=True)
        git(self.a_app, "init", "-q", "-b", "main")
        write(self.a_app / "pkg" / "__init__.py", "")
        write(self.a_app / "pkg" / "core.py", "def add(a, b):\n    raise NotImplementedError\n")
        write(self.a_app / "tests" / "test_core.py", "import unittest\n")
        git(self.a_app, "add", "-A")
        git(self.a_app, "commit", "-q", "-m", "init")
        git(self.a_app, "remote", "add", "origin", str(self.app_remote))
        git(self.a_app, "push", "-q", "-u", "origin", "main")
        git(self.a_app, "branch", "comp/thing")
        # spec repo: files come from the templates, [FILL] filled by program
        self.a_spec.mkdir(parents=True)
        git(self.a_spec, "init", "-q", "-b", "main")
        git(self.a_spec, "remote", "add", "origin", str(self.spec_remote))
        md = (TEMPLATES / "specflow.md").read_text(encoding="utf-8")
        rows = {"Product": "sample-pkg", "Language": "English", "Spec folder": "specs", "Code repos": "app",
                "Handoff file": "specs/notes/handoff.md"}
        out = []
        for line in md.splitlines():
            m = re.match(r"\| ([^|]+) \| \[FILL\]", line)
            if m:
                line = line.replace("[FILL]", rows.get(m.group(1).strip(), "none"), 1)
            out.append(line)
        write(self.a_prod / "specflow.md", "\n".join(out).replace("[FILL]", "none") + "\n")
        pol = fill(json.loads((TEMPLATES / "workflow.json").read_text(encoding="utf-8")), {
            "[FILL: spec repo id]": "spec", "[FILL: code repo id]": "app", "[FILL]": "main",
            "[FILL: the handoff file, relative to the spec repo root]": "specs/notes/handoff.md"})
        pol["tools"]["fakeimpl"] = {"version_args": ["--version"]}
        pol["capabilities"]["dispatch"]["tools"] = ["git", "fakeimpl"]
        pol["roles"] = {"implementer": {"tool": "fakeimpl", "model": "m1", "effort": "medium"}}
        pol["dispatch"]["implementer"] = {"argv": ["fakeimpl", "--cd", "{worktree}", "-o", "{report}",
                                                   "--schema", "{schema}", "-m", "{model}"], "extra_allowed": {}}
        pol["dispatch"]["report"] = {"schema": "report.schema.json", "task_field": "task", "status_field": "status",
                                     "done_requires": [{"field": "unmet", "empty": True},
                                                       {"field": "tests", "in": ["pass", "na"]}]}
        pol["handoff"]["task_notes"] = "specs/notes/tasks/{id}.md"
        pol["transitions"] = [
            {"from": "Not started", "to": "In progress", "needs": ["dispatch"]},
            {"from": "In progress", "to": "Awaiting integration", "needs": ["run"]}]
        write(self.a_prod / "workflow.json", json.dumps(pol, indent=2))
        comps = json.loads((TEMPLATES / "components.json").read_text(encoding="utf-8"))
        comps["repos"] = {"app": {"integration_branch": "main"}}   # what init-product does
        write(self.a_prod / "components.json", json.dumps(comps, indent=2))
        write(self.a_prod / "report.schema.json", json.dumps(REPORT_SCHEMA))
        write(self.a_prod / "A1-thing.md", "# A1 thing\nadd() in pkg/core.py\n")
        write(self.a_prod / "other.md", "other session's file\n")
        write(self.a_spec / ".gitignore", "specflow.local.json\n")
        for f in ("specflow.md", "workflow.json", "components.json"):
            assert "[FILL" not in (self.a_prod / f).read_text(encoding="utf-8"), f
        self._local(self.a_spec, self.a_spec, self.a_app, "A")
        for args in (("regen",), ("lock-write", "--installed", self.installed)):
            r = self.run("workflow_config", "--product", self.a_prod, *args)
            assert r.returncode == 0, r.all
        r = self.run("registry", "--product", self.a_prod, "add", "A1", "--name", "thing", "--spec", "A1-thing.md",
                     "--repo", "app")
        assert r.returncode == 0, r.all
        git(self.a_spec, "add", "-A")
        git(self.a_spec, "commit", "-q", "-m", "seed")
        git(self.a_spec, "push", "-q", "-u", "origin", "main")

    def wf(self, prod, *args):
        return self.run("workflow_config", "--product", prod, *args)

    def ho(self, prod, *args):
        return self.run("handoff", "--product", prod, *args)

    def reg(self, prod, *args):
        return self.run("registry", "--product", prod, *args)

    def sync_b(self):
        git(self.b_spec, "fetch", "-q")
        git(self.b_spec, "pull", "-q", "--ff-only")


class PortableSmoke(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._t.cleanup)
        self.w = World(Path(self._t.name))

    def write_handoff(self, hid, extra="", pad=0):
        w = self.w
        write(w.a_prod / "notes" / "handoff.md",
              f"{hid}\n# Handoff\nA1 thing: awaiting integration.\nNext: integrate A1.\n{extra}" + "x" * pad + "\n")
        write(w.a_prod / "notes" / "tasks" / "A1.md", "A1 task note\n")

    def add_own(self, extra=()):
        w = self.w
        git(w.a_spec, "add", "--", "specs/notes/handoff.md", "specs/notes/tasks/A1.md", "specs/components.json", *extra)

    def test_round_trip(self):
        w = self.w
        self.assertEqual(w.wf(w.a_prod, "validate").returncode, 0)
        r = w.wf(w.a_prod, "doctor")
        self.assertEqual(r.returncode, 0, r.all)
        r = w.wf(w.a_prod, "dispatch-check", "--installed", w.installed)
        self.assertEqual(r.returncode, 0, r.all)

        # state changes only by rule
        r = w.reg(w.a_prod, "set", "A1", "--status", "Done")
        self.assertEqual(r.returncode, 1, r.all)
        ev = write(w.a_prod / "evidence" / "A1" / "dispatch.md", "dispatch approved\n")
        r = w.reg(w.a_prod, "set", "A1", "--status", "In progress", "--evidence", "dispatch=specs/evidence/A1/dispatch.md")
        self.assertEqual(r.returncode, 0, r.all)

        # another session's files: one untracked, one modified tracked; they must stay out of every commit
        write(w.a_spec / "specs" / "other-untracked.md", "other session\n")
        write(w.a_prod / "other.md", "other session's file, edited\n")
        before_other = git(w.a_spec, "status", "--porcelain", "--", "specs/other.md", "specs/other-untracked.md")
        self.assertEqual(len(before_other.splitlines()), 2, before_other)

        # fake implementer through the runner
        prompt = write(w.tmp / "prompt.md", "Implement add() in pkg/core.py only.\n")
        r = w.run("dispatch_run", "--product", w.a_prod, "run", "A1", "--prompt-file", prompt, "--allow", "pkg/**",
                  "--frozen", "tests/**", "--installed", w.installed)
        self.assertEqual(r.returncode, 0, r.all)
        runs = Path(git(w.a_spec, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "specflow-runtime" / "runs"
        run_dirs = [d for d in runs.iterdir() if d.is_dir()]
        self.assertEqual(len(run_dirs), 1)
        result = json.loads((run_dirs[0] / "result.json").read_text(encoding="utf-8"))
        manifest = json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((result["verdict"], result["exit_code"]), ("done", 0))
        self.assertEqual(result["changed_files"], ["pkg/core.py"])
        wt, branch = Path(result["worktree"]), manifest["worktree"]["branch"]
        self.assertEqual(wt.parent.parent.resolve(), w.a_root.resolve())
        # the main app checkout is untouched
        self.assertEqual(git(w.a_app, "status", "--porcelain"), "")

        # evidence of the run, then done
        write(w.a_prod / "evidence" / "A1" / "result.json", json.dumps(result, indent=1))
        r = w.reg(w.a_prod, "set", "A1", "--status", "Awaiting integration", "--evidence",
                  "run=specs/evidence/A1/result.json")
        self.assertEqual(r.returncode, 0, r.all)

        # code repo: commit the run branch and push -u
        git(wt, "add", "--", "pkg/core.py")
        git(wt, "commit", "-q", "-m", "A1: add()")
        git(wt, "push", "-q", "-u", "origin", branch)

        hid = w.ho(w.a_prod, "new-id").stdout.strip()
        self.assertTrue(hid, hid)
        self.write_handoff(hid)
        self.add_own(["specs/evidence/A1/dispatch.md", "specs/evidence/A1/result.json"])
        r = w.ho(w.a_prod, "export", "--task", "A1", "--handoff-id", hid, "--ref", f"app={branch}", "--json")
        self.assertEqual(r.returncode, 0, r.all)
        exp = json.loads(r.stdout)
        self.assertEqual(exp["handoff_id"], hid)
        git(w.a_spec, "add", "--", *exp["commit_paths"])
        git(w.a_spec, "commit", "-q", "-m", "handoff", "--", "specs/notes/handoff.md", "specs/notes/tasks/A1.md",
            "specs/components.json", "specs/evidence/A1/dispatch.md", "specs/evidence/A1/result.json",
            *exp["commit_paths"])
        git(w.a_spec, "push", "-q", "origin", "main")
        r = w.ho(w.a_prod, "verify", "--fetch", "--json")
        v = json.loads(r.stdout)
        self.assertTrue(v["published"], r.all)
        # A has what spec_work (the default capability) needs, measured on this machine: published and resumable
        self.assertEqual(v["level"], "resumable", r.all)
        self.assertEqual(v["handoff_id"], hid)

        # the other session's files: still uncommitted, in no commit
        after_other = git(w.a_spec, "status", "--porcelain", "--", "specs/other.md", "specs/other-untracked.md")
        self.assertEqual(after_other, before_other)
        committed = git(w.a_spec, "log", "--name-only", "--format=", "origin/main").splitlines()
        self.assertNotIn("specs/other-untracked.md", committed)
        self.assertEqual(git(w.a_spec, "show", "HEAD:specs/other.md"), "other session's file")

        # machine B: fetch, ff-only pull, verify
        w.sync_b()
        r = w.ho(w.b_prod, "verify", "--fetch", "--json")
        self.assertEqual(r.returncode, 0, r.all)
        vb = json.loads(r.stdout)
        self.assertEqual((vb["level"], vb["handoff_id"]), ("resumable", hid))
        show_a, show_b = w.reg(w.a_prod, "show"), w.reg(w.b_prod, "show")
        self.assertEqual(show_a.returncode, 0, show_a.all)
        self.assertEqual(show_a.stdout, show_b.stdout)
        self.assertIn("Awaiting integration", show_b.stdout)

    def test_forgot_to_push_app(self):
        w = self.w
        git(w.a_app, "switch", "-q", "-c", "feature/a1")
        write(w.a_app / "pkg" / "core.py", "def add(a, b):\n    return a + b\n")
        git(w.a_app, "commit", "-q", "-am", "A1: add()")
        sha12 = git(w.a_app, "rev-parse", "HEAD")[:12]
        hid = w.ho(w.a_prod, "new-id").stdout.strip()
        self.write_handoff(hid)
        self.add_own()
        r = w.ho(w.a_prod, "export", "--task", "A1", "--handoff-id", hid, "--ref", "app=feature/a1", "--json")
        self.assertEqual(r.returncode, 4, r.all)
        exp = json.loads(r.stdout)
        self.assertEqual(exp["level"], "local")
        self.assertIn(f"app@{sha12}", [m["item"] for m in exp["missing"]])
        git(w.a_spec, "add", "--", *exp["commit_paths"])
        git(w.a_spec, "commit", "-q", "-m", "handoff (local)", "--", "specs/notes/handoff.md",
            "specs/notes/tasks/A1.md", "specs/components.json", *exp["commit_paths"])
        git(w.a_spec, "push", "-q", "origin", "main")
        w.sync_b()
        r = w.ho(w.b_prod, "verify", "--fetch", "--json")
        self.assertEqual(r.returncode, 4, r.all)
        vb = json.loads(r.stdout)
        self.assertIn(f"app@{sha12}", [m["item"] for m in vb["missing"]])
        self.assertNotEqual(vb["level"], "resumable")

    def test_oversize_handoff_is_refused(self):
        w = self.w
        hid = w.ho(w.a_prod, "new-id").stdout.strip()
        self.write_handoff(hid, pad=5200)
        self.assertGreater((w.a_prod / "notes" / "handoff.md").stat().st_size, 5120)
        self.add_own()
        r = w.ho(w.a_prod, "export", "--task", "A1", "--handoff-id", hid, "--json")
        self.assertEqual(r.returncode, 2, r.all)
        self.assertFalse((w.a_prod / "notes" / "handoff.manifest.json").exists())



class PluginGuards(unittest.TestCase):
    def test_session_start_hook_does_not_touch_git_state(self):
        data = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        cmds = [h["command"] for g in data["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertTrue(cmds)
        for c in cmds:
            self.assertIsNone(re.search(r"\b(pull|push|commit|rebase|fetch)\b", c), c)

    def test_lint_generic(self):
        bash = bash_exe()
        if not bash:
            self.skipTest("no working bash on this machine; lint-generic.sh not run")
        r = subprocess.run([bash, "plugins/specflow/scripts/lint-generic.sh"], cwd=str(REPO_ROOT),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
