"""Tests for handoff.py (handoff manifest export / verify / report). Run:
python -m unittest discover -s plugins/specflow/tests -p "test_handoff.py"   (stdlib only, about 100 s)

Everything runs in temp repos with temp bare remotes; no real repo or remote is touched. Machines A and B are two
folders on this computer; B's paths have spaces and non-ASCII characters. fetch only talks to the temp bare remotes.
The home repo, the handoff file, the task-note pattern and the local-only prefixes come from workflow.json.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
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


ho = _load("handoff", "specflow_handoff")
SCHEMA_PATH = SCRIPTS / "handoff.manifest.schema.json"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_TERMINAL_PROMPT": "0"}

HANDOFF_CFG = {"file": ".notes/handoff.md", "task_notes": "progress/{id}.md", "local_only": ["build/"]}
HANDOFF = "更新：2026-11-13 A\n\n## 待辦\n1. 上機 → `progress/A1.md`、app `docs/reviews/A1-g5.md`\n"
PROGRESS = "# A1\n\n## 現在\n- 規格：`specs/A1-brew.md`\n- 關卡紀錄：app `docs/reviews/A1-g5.md`\n"


def make_policy(home="spec", handoff=None):
    return {"schema_version": 1,
            "repos": {home: {"remote_alias": "origin", "integration_branch": "master", "home": True},
                      "app": {"remote_alias": "origin", "integration_branch": "master"},
                      "lib": {"remote_alias": "origin", "integration_branch": "master"}},
            "tools": {"git": {"version_args": ["--version"]}, "cc": {"version_args": ["--version"]}},
            "capabilities": {"spec_work": {"tools": ["git"], "repos": []},
                             "app_build": {"tools": ["git", "cc"], "repos": ["app"]},
                             "hardware_e2e": {"tools": [], "repos": ["app"], "measurable": False}},
            "dispatch": {"timeout_seconds": 600},
            "handoff": dict(HANDOFF_CFG if handoff is None else handoff)}


def git(cwd, *args, ok=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV)
    if ok and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r


def head(cwd):
    return git(cwd, "rev-parse", "HEAD").stdout.strip()


def write(p, text, nl="\n"):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline=nl) as f:
        f.write(text)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def items(lst):
    return [x["item"] for x in lst]


class World(unittest.TestCase):
    """Machine A has the home repo and app, both pushed to bare remotes. Machine B clones later. lib is in the policy
    but mapped nowhere. The class attributes say where the product folder and the handoff file are."""
    HOME = "spec"
    PRODUCT = "specs"                # product folder (holds workflow.json) inside the home repo; "" = the home root
    HANDOFF_CFG = HANDOFF_CFG
    HANDOFF_TEXT = HANDOFF
    NOTES = {"A1": PROGRESS}

    def setUp(self):
        self.t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.t.cleanup)
        self.base = Path(self.t.name)
        self.policy = make_policy(self.HOME, self.HANDOFF_CFG)
        self.cfg = ho.handoff_config(self.policy)
        self.hfile = self.HANDOFF_CFG["file"]
        self.hdir = posixpath.dirname(self.hfile)
        self.remote = {r: self.base / "remotes" / f"{r}.git" for r in (self.HOME, "app")}
        for b in self.remote.values():
            b.parent.mkdir(parents=True, exist_ok=True)
            git(self.base, "init", "-q", "--bare", "-b", "master", str(b))
        self.A = {self.HOME: self.base / "A" / self.HOME, "app": self.base / "A" / "app"}
        for rid, p in self.A.items():
            p.mkdir(parents=True)
            git(p, "init", "-q", "-b", "master")
            git(p, "config", "core.autocrlf", "false")
            git(p, "remote", "add", "origin", str(self.remote[rid]))
        h, f = self.A[self.HOME], self.A["app"]
        write(h / self.PRODUCT / "workflow.json", json.dumps(self.policy, indent=2) + "\n")
        write(h / ".gitignore", "specflow.local.json\n")
        write(h / "specs/A1-brew.md", "# A1 brew\n")
        write(h / "wiki/log.md", "log\n")
        write(h / self.hfile, self.HANDOFF_TEXT)
        for tid, text in self.NOTES.items():
            write(h / self.note(tid), text)
        write(f / "docs/reviews/A1-g5.md", "G5 passed\n")
        write(f / "dependencies.lock", "lock\n")
        for p in self.A.values():
            git(p, "add", "-A")
            git(p, "commit", "-q", "-m", "init")
            git(p, "push", "-q", "-u", "origin", "master")
        self.B = None
        self.set_local("A", self.A)

    # ---- helpers ----
    def note(self, tid):
        return self.HANDOFF_CFG["task_notes"].replace("{id}", tid)

    def paths(self, machine):
        return self.A if machine == "A" else self.B

    def set_local(self, machine, mapping):
        """This machine's specflow.local.json at its home repo root (git-ignored)."""
        root = self.paths(machine)[self.HOME]
        write(root / "specflow.local.json",
              json.dumps({"schema_version": 1, "repos": {k: {"path": str(v)} for k, v in mapping.items()}},
                         ensure_ascii=False))

    def local_dict(self, machine):
        return dict(self.paths(machine))

    def clone_b(self):
        root = self.base / "B 機" / "工作 區"
        self.B = {self.HOME: root / f"倉庫 {self.HOME}", "app": root / "程式 app"}
        for rid, p in self.B.items():
            git(self.base, "clone", "-q", "-c", "core.autocrlf=false", str(self.remote[rid]), str(p))
        self.set_local("B", self.B)
        return self.B

    def cli(self, machine, *args, json_out=True):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--product", str(self.paths(machine)[self.HOME] / self.PRODUCT), *args]
        if json_out:
            argv.append("--json")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ho.main(argv)
        self.last_text = out.getvalue() + err.getvalue()
        data = json.loads(out.getvalue()) if json_out and out.getvalue().strip() else {}
        return code, data

    def export(self, tasks=("A1",), extra=(), stage=True):
        h = self.A[self.HOME]
        if stage:
            notes = [self.note(t) for t in tasks] if self.HANDOFF_CFG.get("task_notes") else []
            git(h, "add", "--", self.hfile, *notes)
        args = ["export", "--machine", "A", *[x for t in tasks for x in ("--task", t)], *extra]
        return self.cli("A", *args)

    def wrapup(self, tasks=("A1",), extra=(), push=True):
        """Like a wrapup: stage own home-repo files -> export -> commit commit_paths -> push."""
        code, out = self.export(tasks, extra)
        h = self.A[self.HOME]
        if out.get("commit_paths"):
            git(h, "add", "--", *out["commit_paths"])
            git(h, "commit", "-q", "-m", "handoff", "--", *out["commit_paths"])
            if push:
                out["push_rc"] = git(h, "push", "-q", "origin", "HEAD", ok=False).returncode
        return code, out

    def manifest_of(self, machine, hid):
        p = self.paths(machine)[self.HOME] / self.hdir / "handoffs" / f"{hid}.json"
        return ho.HandoffManifest.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def app_commit(self, rel, text, msg="app"):
        f = self.A["app"]
        write(f / rel, text)
        git(f, "add", "--", rel)
        git(f, "commit", "-q", "-m", msg)
        return head(f)

    def set_notes(self, extra_line):
        write(self.A[self.HOME] / self.note("A1"), PROGRESS + extra_line + "\n")


# ---------------------------------------------------------------- unit: config, parsing and schema

class TestConfig(unittest.TestCase):
    def test_full_block(self):
        c = ho.handoff_config(make_policy())
        self.assertEqual((c.home, c.file, c.task_notes, c.local_only),
                         ("spec", ".notes/handoff.md", "progress/{id}.md", ("build",)))
        self.assertEqual((c.root_manifest, c.handoffs_dir), (".notes/handoff.manifest.json", ".notes/handoffs"))
        self.assertEqual(c.manifest_rel("x1"), ".notes/handoffs/x1.json")
        self.assertEqual(c.sources(["A1", "A2"]),
                         [("progress/A1.md", "A1"), ("progress/A2.md", "A2"), (".notes/handoff.md", "handoff")])

    def test_handoff_file_at_root_and_no_task_notes(self):
        for block in ({"file": "handoff.md"}, {"file": "handoff.md", "task_notes": None, "local_only": []}):
            with self.subTest(block=block):
                c = ho.handoff_config(make_policy("docs", block))
                self.assertEqual((c.home, c.task_notes, c.local_only), ("docs", None, ()))
                self.assertEqual((c.root_manifest, c.handoffs_dir), ("handoff.manifest.json", "handoffs"))
                self.assertEqual(c.sources(["A1"]), [("handoff.md", "handoff")])

    def test_bad_blocks_are_2(self):
        bad = [None, "handoff.md", {}, {"file": "/abs/handoff.md"}, {"file": "../handoff.md"}, {"file": "C:/h.md"},
               {"file": "a\\h.md"}, {"file": "h.md", "task_notes": "progress/x.md"},
               {"file": "h.md", "task_notes": "../{id}.md"}, {"file": "h.md", "task_notes": 3},
               {"file": "h.md", "local_only": "build/"}, {"file": "h.md", "local_only": ["/abs"]},
               {"file": "h.md", "local_only": [".."]}, {"file": "h.md", "local_only": [""]},
               {"file": "h.md", "extra": 1}]
        for block in bad:
            with self.subTest(block=block):
                p = make_policy()
                if block is None:
                    del p["handoff"]
                else:
                    p["handoff"] = block
                with self.assertRaises(ho.HandoffError) as cm:
                    ho.handoff_config(p)
                self.assertEqual(cm.exception.code, 2)

    def test_cli_without_handoff_block_is_2(self):
        with tempfile.TemporaryDirectory() as t:
            p = make_policy()
            del p["handoff"]
            write(Path(t) / "workflow.json", json.dumps(p))
            err = io.StringIO()
            for cmd in (["verify"], ["export", "--task", "A1"], ["report"]):
                with self.subTest(cmd=cmd), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(ho.main(["--product", t, *cmd]), 2)
            self.assertIn("handoff", err.getvalue())


class TestParseRefs(unittest.TestCase):
    def refs(self, text):
        return ho.parse_refs(text, "progress/A1.md", "spec", ("spec", "app", "lib"), ("build/",))

    def test_repo_prefix_and_explicit_form(self):
        r = self.refs("在 app `docs/reviews/a.md`、`app:docs/b.md`、lib `src/m.c`、`specs/x.md`、spec `wiki/y.md`")
        got = [(x.repo, x.path) for x in r if x.kind == "path"]
        self.assertEqual(got, [("app", "docs/reviews/a.md"), ("app", "docs/b.md"), ("lib", "src/m.c"),
                               ("spec", "specs/x.md"), ("spec", "wiki/y.md")])

    def test_bare_filename_is_marked_not_guessed(self):
        r = [x for x in self.refs("app `docs/reviews/` 的 `A1-g2.md`、`registry.py check`") if x.kind == "path"]
        self.assertEqual([(x.path, x.bare, x.is_dir) for x in r],
                         [("docs/reviews", False, True), ("A1-g2.md", True, False), ("registry.py", True, False)])
        self.assertEqual(r[0].repo, "app")
        self.assertEqual(r[1].repo, "spec")  # the home repo; app is not guessed

    def test_non_paths_are_skipped(self):
        r = self.refs("`/post-merge`、`HW_VALVE_RUN_MS`、`int`、`/tokeneconomizer:config`、`0.0.3`、`te`")
        self.assertEqual([x for x in r if x.kind != "url"], [])

    def test_local_only_paths(self):
        r = self.refs("`C:/dev/tools`、`build/` 的 log、`../outside.md`、`D:\\x\\y.log`")
        self.assertEqual([x.kind for x in r], ["local"] * 4)

    def test_git_dir_and_local_only_prefix_in_any_repo(self):
        """The runtime dir lives in the git dir: always local. handoff.local_only applies in every repo."""
        r = self.refs("`.git/specflow-runtime/runs/r1/`、app `build/out.bin`、app `.git/config`、`buildx/a.md`、"
                      "`.github/ci.yml`")
        self.assertEqual([x.kind for x in r], ["local", "local", "local", "path", "path"])
        self.assertEqual([(x.repo, x.path) for x in r[3:]], [("spec", "buildx/a.md"), ("spec", ".github/ci.yml")])

    def test_local_only_marker_on_line(self):
        r = self.refs("原機 log `C:/tmp/run.log`（僅本機）")
        self.assertTrue(all(x.local_ok for x in r))

    def test_local_only_mark_binds_to_preceding_ref_only(self):
        """The mark exempts only the reference right before it, not the whole line."""
        r = self.refs("- app `docs/reviews/A1-run.md`；原機 log `build/r1/`（僅本機）")
        self.assertEqual([(x.token, x.local_ok) for x in r],
                         [("docs/reviews/A1-run.md", False), ("build/r1/", True)])
        r = self.refs("`a/x.md` 僅本機、`b/y.md`、（僅本機）`c/z.md`")
        self.assertEqual([x.local_ok for x in r], [True, False, False])
        r = self.refs("[[note]]（僅本機） [l](d/e.md) 僅本機 https://x.example/a")
        self.assertEqual([(x.kind, x.local_ok) for x in r], [("url", False), ("path", True), ("wikilink", True)])

    def test_english_local_only_mark(self):
        r = self.refs("`a/x.md` local only, `b/y.md`, (local only) `c/z.md`")
        self.assertEqual([x.local_ok for x in r], [True, False, False])
        r = self.refs("app `docs/r.md` (local only); `C:/tmp/run.log` Local only; `d/e.md` local onlyish")
        self.assertEqual([x.local_ok for x in r], [True, True, False])
        r = self.refs("[[note]] (local only) [l](d/e.md) local only https://x.example/a")
        self.assertEqual([(x.kind, x.local_ok) for x in r], [("url", False), ("path", True), ("wikilink", True)])

    def test_placeholder_and_glob(self):
        r = self.refs("`progress/<id>.md`、`progress/A*.md`")
        self.assertEqual([x.kind for x in r], ["placeholder", "placeholder"])

    def test_wikilink_mdlink_url(self):
        r = self.refs("[[board-not-started]] [說明](../specs/a%20b.md#x) https://claude.ai/artifact/Ab1")
        kinds = {x.kind: x for x in r}
        self.assertEqual(kinds["wikilink"].token, "board-not-started")
        self.assertEqual((kinds["path"].repo, kinds["path"].path), ("spec", "specs/a b.md"))
        self.assertEqual(kinds["url"].token, "https://claude.ai/artifact/Ab1")

    def test_backslash_path(self):
        r = self.refs("`scripts\\run.ps1 e2e -Tag <x>`")
        self.assertEqual([(x.kind, x.path) for x in r], [("path", "scripts/run.ps1")])


class TestSecrets(unittest.TestCase):
    def test_detects_credentials_and_tokens(self):
        for text in ("https://bob:hunter2@example.com/x", "https://ghp_abcdefghijklmnopqrstuvwxyz0123@github.com/x",
                     "http://h/x?access_token=abc123", "https://h/a?X-Amz-Signature=ff00&b=1",
                     "token ghp_abcdefghijklmnopqrstuvwxyz0123456789"):
            self.assertTrue(ho.find_secrets(text), text)

    def test_ssh_user_and_plain_urls_are_fine(self):
        for text in ("ssh://git@192.0.2.10:2222/team/app.git", "https://claude.ai/artifact/JKk4BGqvuAzr1HG89AVazX",
                     "https://example.com/a?page=2"):
            self.assertEqual(ho.find_secrets(text), [], text)

    def test_messages_do_not_echo_the_secret(self):
        msgs = " ".join(ho.find_secrets("https://bob:hunter2@example.com/x\nhttp://h/x?token=s3cr3tvalue"))
        self.assertNotIn("hunter2", msgs)
        self.assertNotIn("s3cr3tvalue", msgs)


def _good_manifest():
    return {
        "schema_version": 1, "handoff_id": "20261113T1120-B-3f2a", "created_at": "2026-11-13T11:20:00+08:00",
        "source_machine": "B", "task_ids": ["A1"],
        "repos": [{"repo_id": "spec", "remote_alias": "origin", "ref": "master", "commit_sha": "a" * 40},
                  {"repo_id": "app", "remote_alias": "origin", "ref": "task/a1", "commit_sha": "b" * 40}],
        "required_files": [{"repo_id": "spec", "relative_path": "progress/A1.md", "sha256": "c" * 64}],
        "artifacts": [{"artifact_id": "log", "uri": "https://example.com/log.txt", "sha256": "d" * 64,
                       "size_bytes": 3, "required_for_resume": False}],
        "pending_transfers": [{"repo_id": "app", "relative_path": "docs/reviews/A1-run.md", "reason": "untracked",
                               "needed_for": "A1"},
                              {"repo_id": "app", "reason": "unpushed", "needed_for": "A1"}],
    }


class TestSchema(unittest.TestCase):
    def test_good_manifest_is_valid(self):
        self.assertEqual(ho.validate_manifest(_good_manifest()), [])
        self.assertEqual(ho.validate_manifest(_good_manifest(), "spec"), [])
        m = ho.HandoffManifest.from_dict(_good_manifest())
        self.assertEqual(json.loads(m.to_json()), _good_manifest())

    def test_home_repo_must_be_in_repos(self):
        errs = ho.validate_manifest(_good_manifest(), "docs")
        self.assertTrue(any('"docs"' in e for e in errs), errs)

    def test_bad_manifest_reports_every_problem_with_code_2(self):
        d = _good_manifest()
        d["schema_version"] = 2
        d["status"] = "done"  # component status must never be copied into a manifest
        d["repos"][1]["commit_sha"] = "abc"
        d["repos"][1]["remote_alias"] = "https://u:p@h/x.git"
        d["required_files"][0]["relative_path"] = "C:/abs/A1.md"
        d["pending_transfers"][0]["reason"] = "lost"
        errs = ho.validate_manifest(d)
        self.assertGreaterEqual(len(errs), 6, errs)
        with self.assertRaises(ho.HandoffError) as cm:
            ho.HandoffManifest.from_dict(d)
        self.assertEqual(cm.exception.code, 2)

    def test_schema_file_matches_validator(self):
        s = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(tuple(s["required"]), ho.MANIFEST_KEYS)
        self.assertFalse(s["additionalProperties"])
        defs = s["$defs"]
        self.assertEqual(tuple(defs["pending"]["properties"]["reason"]["enum"]), ho.PENDING_REASONS)
        self.assertEqual(tuple(defs["repo"]["required"]), ho.REPO_KEYS)
        self.assertEqual(tuple(defs["file"]["required"]), ho.FILE_KEYS)
        self.assertEqual(tuple(defs["artifact"]["required"]), ho.ARTIFACT_KEYS)

    def test_relpath_rules_match_schema(self):
        """The schema's relpath rejects the same unnormalized paths as the validator."""
        pat = re.compile(json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]["relpath"]["pattern"])
        for p in ("./a.md", "a//b.md", "a/./b.md", "a/", "a/.", ".", "..", "../a", "a/../b", "/a", "C:/a", "a\\b"):
            with self.subTest(bad=p):
                self.assertFalse(ho._rel_ok(p))
                self.assertIsNone(pat.search(p))
        for p in ("a.md", "docs/reviews/A1-g5.md", "中文 路徑/x y.md", ".notes/handoff.md", "a..b.md"):
            with self.subTest(good=p):
                self.assertTrue(ho._rel_ok(p))
                self.assertIsNotNone(pat.search(p))

    def test_task_ids_must_be_valid(self):
        d = _good_manifest()
        d["task_ids"] = ["A1", "A1", "../x"]
        self.assertGreaterEqual(len(ho.validate_manifest(d)), 2)

    def test_new_id_is_filename_safe_and_unique(self):
        ids = {ho.new_handoff_id("B 機") for _ in range(20)}
        self.assertGreater(len(ids), 1)
        for i in ids:
            self.assertRegex(i, r"^\d{8}T\d{4}-B-[0-9a-f]{4}$")


class TestSiblingLoad(unittest.TestCase):
    """One workflow_config per process, shared with the other core scripts; never one from another path."""

    LOADER = ("import importlib.util, sys, types\nfrom pathlib import Path\n"
              "def load(n, m):\n"
              "    s = importlib.util.spec_from_file_location(m, str(Path(sys.argv[1]) / (n + '.py')))\n"
              "    mod = importlib.util.module_from_spec(s); sys.modules[m] = mod; s.loader.exec_module(mod)\n"
              "    return mod\n")

    def run_child(self, body):
        r = subprocess.run([sys.executable, "-c", self.LOADER + body, str(SCRIPTS)], capture_output=True, text=True)
        return r.stdout.split(), r.stderr

    def test_shares_workflow_config_with_registry(self):
        out, err = self.run_child("r = load('registry', 'specflow_registry')\nh = load('handoff', 'specflow_handoff')\n"
                                  "print(h.wc is r.workflow_config, h.wc is sys.modules['specflow_workflow_config'])\n")
        self.assertEqual(out, ["True", "True"], err)

    def test_module_from_another_path_is_not_reused(self):
        out, err = self.run_child(
            "fake = types.ModuleType('specflow_workflow_config'); fake.__file__ = str(Path(sys.argv[1]).parent / 'x.py')\n"
            "sys.modules['specflow_workflow_config'] = fake\nh = load('handoff', 'specflow_handoff')\n"
            "print(h.wc is not fake, Path(h.wc.__file__).resolve() == (Path(sys.argv[1]) / 'workflow_config.py').resolve())\n")
        self.assertEqual(out, ["True", "True"], err)


# ---------------------------------------------------------------- export

class TestExport(World):
    def test_export_writes_fixed_and_root_manifest(self):
        code, out = self.export()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["level"], "local")
        hid = out["handoff_id"]
        h = self.A["spec"]
        fixed = h / ".notes" / "handoffs" / f"{hid}.json"
        root = h / ".notes" / "handoff.manifest.json"
        self.assertEqual(json.loads(fixed.read_text(encoding="utf-8")), json.loads(root.read_text(encoding="utf-8")))
        self.assertEqual((out["manifest_path"], out["root_manifest_path"]),
                         (f".notes/handoffs/{hid}.json", ".notes/handoff.manifest.json"))
        m = self.manifest_of("A", hid)
        self.assertEqual({r["repo_id"] for r in m.repos}, {"spec", "app"})  # lib has no mapping: not listed
        self.assertEqual({(f["repo_id"], f["relative_path"]) for f in m.required_files},
                         {("spec", ".notes/handoff.md"), ("spec", "progress/A1.md"),
                          ("spec", "specs/A1-brew.md"), ("app", "docs/reviews/A1-g5.md")})
        self.assertEqual(m.pending_transfers, [])
        self.assertIn(".notes/handoff.manifest.json", out["commit_paths"])
        self.assertIn(f".notes/handoffs/{hid}.json", out["commit_paths"])

    def test_home_records_base_commit_not_itself(self):
        base = head(self.A["spec"])
        code, out = self.wrapup()
        m = self.manifest_of("A", out["handoff_id"])
        home = next(r for r in m.repos if r["repo_id"] == "spec")
        self.assertEqual(home["commit_sha"], base)
        self.assertNotEqual(head(self.A["spec"]), base)

    def test_unique_ids_and_fixed_manifests_never_overwritten(self):
        c1, o1 = self.export()
        fixed1 = self.A["spec"] / ".notes" / "handoffs" / f"{o1['handoff_id']}.json"
        before = fixed1.read_bytes()
        c2, o2 = self.export()
        self.assertNotEqual(o1["handoff_id"], o2["handoff_id"])
        self.assertEqual(fixed1.read_bytes(), before)
        root = json.loads((self.A["spec"] / ".notes" / "handoff.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(root["handoff_id"], o2["handoff_id"])
        c3, o3 = self.export(extra=("--handoff-id", o1["handoff_id"]))
        self.assertEqual(c3, 3)
        self.assertEqual(fixed1.read_bytes(), before)

    def test_hash_is_git_blob_not_worktree_crlf(self):
        f = self.A["app"]
        git(f, "config", "core.autocrlf", "true")
        write(f / "docs/reviews/A1-crlf.md", "一\n二\n", nl="\r\n")
        git(f, "add", "--", "docs/reviews/A1-crlf.md")
        git(f, "commit", "-q", "-m", "crlf")
        git(f, "push", "-q")
        self.set_notes("- app `docs/reviews/A1-crlf.md`")
        code, out = self.wrapup()
        self.assertEqual(code, 0, out)
        m = self.manifest_of("A", out["handoff_id"])
        h = next(x["sha256"] for x in m.required_files if x["relative_path"] == "docs/reviews/A1-crlf.md")
        self.assertEqual(h, sha("一\n二\n".encode()))
        self.assertNotEqual(h, sha((f / "docs/reviews/A1-crlf.md").read_bytes()))
        self.clone_b()  # B has autocrlf=false: LF working tree, same blob
        code, out = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 0, out)

    def test_export_does_not_commit_push_or_touch_others(self):
        h, f = self.A["spec"], self.A["app"]
        write(h / "wiki/log.md", "another session's uncommitted change\n")  # someone else's: left alone
        write(h / "notes 他人.md", "untracked\n")
        heads = (head(h), head(f))
        remote_heads = tuple(git(r, "rev-parse", "master").stdout for r in self.remote.values())
        code, out = self.export()
        self.assertEqual(code, 0, out)
        self.assertEqual((head(h), head(f)), heads)
        self.assertEqual(tuple(git(r, "rev-parse", "master").stdout for r in self.remote.values()), remote_heads)
        self.assertEqual((h / "wiki/log.md").read_text(encoding="utf-8"), "another session's uncommitted change\n")
        st = git(h, "status", "--porcelain").stdout
        self.assertIn(" M wiki/log.md", st)
        self.assertIn("?? \"notes", st)
        self.assertTrue(any("wiki/log.md" in w for w in out["warnings"]), out["warnings"])

    def test_manifest_has_no_local_paths_or_component_status(self):
        code, out = self.export()
        text = (self.A["spec"] / ".notes" / "handoffs" / f"{out['handoff_id']}.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.base), text)
        self.assertNotIn(self.base.as_posix(), text)
        self.assertNotIn("status", json.loads(text))

    def test_source_dirty_required_file_is_uncommitted_pending(self):
        f = self.A["app"]
        write(f / "docs/reviews/A1-g5.md", "changed, not committed\n")
        code, out = self.export()
        self.assertEqual(code, 4)
        self.assertIn({"repo_id": "app", "relative_path": "docs/reviews/A1-g5.md", "reason": "uncommitted",
                       "needed_for": "A1"}, out["pending_transfers"])
        self.assertIsNone(out["level"])  # own change not committed: not even local
        self.assertEqual((f / "docs/reviews/A1-g5.md").read_text(encoding="utf-8"), "changed, not committed\n")
        self.assertIn(" M docs/reviews/A1-g5.md", git(f, "status", "--porcelain").stdout)

    def test_unstaged_home_notes_are_uncommitted(self):
        self.set_notes("- a new line")
        code, out = self.export(stage=False)
        self.assertEqual(code, 4)
        self.assertTrue(any(p.get("relative_path") == "progress/A1.md" and p["reason"] == "uncommitted"
                            for p in out["pending_transfers"]), out)

    def test_secret_in_handoff_refuses_export_with_2(self):
        for line in ("research https://bob:hunter2@example.com/x", "download https://h.example/a?access_token=s3cr3tvalue"):
            with self.subTest(line=line):
                write(self.A["spec"] / ".notes/handoff.md", HANDOFF + line + "\n")
                code, out = self.export()
                self.assertEqual(code, 2, out)
                self.assertFalse((self.A["spec"] / ".notes" / "handoffs").exists())
                self.assertNotIn("hunter2", self.last_text)
                self.assertNotIn("s3cr3tvalue", self.last_text)

    def test_secret_in_artifact_uri_refuses_export_with_2(self):
        code, out = self.export(extra=("--artifact", "log=https://h.example/log.txt?token=s3cr3tvalue"))
        self.assertEqual(code, 2)
        self.assertNotIn("s3cr3tvalue", self.last_text)

    def test_unknown_task_notes_is_4_and_bad_task_id_is_2(self):
        code, _ = self.export(tasks=("A9",), stage=False)
        self.assertEqual(code, 4)
        code, _ = self.export(tasks=("../x",), stage=False)
        self.assertEqual(code, 2)

    def test_export_writes_nothing_unless_result_is_0_or_4(self):
        """A result of 3 (or 2 / 1) writes no fixed manifest and leaves the root manifest alone."""
        real = ho.verify_handoff

        def stale(*a, **k):
            chk = real(*a, **k)
            chk.add("stale", "spec:x.md", "simulated hash mismatch")
            return chk
        with mock.patch.object(ho, "verify_handoff", stale):
            code, out = self.export()
        self.assertEqual(code, 3)
        self.assertFalse(out["written"])
        self.assertEqual(out["commit_paths"], [])
        self.assertFalse((self.A["spec"] / ".notes" / "handoffs").exists())
        self.assertFalse((self.A["spec"] / ".notes" / "handoff.manifest.json").exists())

    def test_leftover_uncommitted_manifest_is_reported(self):
        """A fixed manifest left uncommitted by an earlier export is not hidden."""
        code, out = self.export()
        self.assertTrue(out["written"])
        code, out = self.export()
        self.assertTrue(any(".notes/handoff" in w for w in out["warnings"]), out["warnings"])

    def test_oversized_handoff_is_refused_with_2_and_nothing_written(self):
        self.assertEqual(ho.HANDOFF_MAX_BYTES, 5120)
        h = self.A["spec"]
        body = HANDOFF + "\n"
        fill = 5120 - len(body.encode("utf-8")) - 1
        write(h / ".notes/handoff.md", body + "x" * (fill + 1) + "\n")  # 5121 bytes
        self.assertEqual((h / ".notes/handoff.md").stat().st_size, 5121)
        code, out = self.export()
        self.assertEqual(code, 2, out)
        self.assertIn("5120", out["error"])
        self.assertFalse((h / ".notes" / "handoffs").exists())
        self.assertFalse((h / ".notes" / "handoff.manifest.json").exists())
        write(h / ".notes/handoff.md", body + "x" * fill + "\n")  # exactly 5120 bytes: allowed
        self.assertEqual((h / ".notes/handoff.md").stat().st_size, 5120)
        code, out = self.export()
        self.assertEqual(code, 0, out)

    def test_same_repo_twice_in_ref_is_2(self):
        code, out = self.export(extra=("--ref", "app=master", "--ref", "app=master"))
        self.assertEqual(code, 2, out)
        self.assertIn("app", out["error"])
        self.assertFalse((self.A["spec"] / ".notes" / "handoffs").exists())

    def test_ref_option_records_task_worktree_commit(self):
        """--ref app=<branch>: records the task branch / worktree commit; the main checkout does not move."""
        f = self.A["app"]
        master = head(f)
        wt = self.base / "A" / "app 任務 a1"
        git(f, "worktree", "add", "-q", "-b", "task/a1", str(wt))
        write(wt / "src/brew.c", "// WIP\n")
        git(wt, "add", "--", "src/brew.c")
        git(wt, "commit", "-q", "-m", "WIP: draft")
        wip = head(wt)
        self.set_notes("- WIP: app `src/brew.c`")
        code, out = self.export(extra=("--ref", "app=task/a1"))  # not pushed yet
        self.assertEqual(code, 4)
        self.assertIn({"repo_id": "app", "reason": "unpushed", "needed_for": "A1"}, out["pending_transfers"])
        git(wt, "push", "-q", "-u", "origin", "task/a1")
        write(wt / "src/brew.c", "// uncommitted change in the task worktree\n")
        code, out = self.export(extra=("--ref", "app=task/a1"))
        self.assertEqual(code, 4)
        self.assertIn({"repo_id": "app", "relative_path": "src/brew.c", "reason": "uncommitted", "needed_for": "A1"},
                      out["pending_transfers"])
        git(wt, "checkout", "-q", "--", "src/brew.c")
        code, out = self.wrapup(extra=("--ref", "app=task/a1"))
        self.assertEqual(code, 0, out)
        app = next(r for r in self.manifest_of("A", out["handoff_id"]).repos if r["repo_id"] == "app")
        self.assertEqual((app["ref"], app["commit_sha"]), ("task/a1", wip))
        self.assertEqual(head(f), master)  # the main checkout did not switch
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 0, b)
        for bad in ("spec=master", "nope=master", "app=no-such-branch", "app"):
            with self.subTest(bad=bad):
                self.assertEqual(self.export(extra=("--ref", bad))[0], 2)


# ---------------------------------------------------------------- verify scenarios

class TestVerifyScenarios(World):
    def test_published_and_resumable_on_other_machine_with_different_paths(self):
        code, out = self.wrapup()
        self.assertEqual((code, out["push_rc"]), (0, 0), out)
        code, a = self.cli("A", "verify", "--fetch")
        self.assertEqual(code, 0, a)
        self.assertTrue(a["published"])  # wrapup on the source machine: published
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 0, b)
        self.assertEqual(b["level"], "resumable")
        self.assertEqual(b["handoff_id"], out["handoff_id"])
        self.assertNotEqual(self.A["app"], self.B["app"])

    def test_code_push_failure(self):
        hook = self.remote["app"] / "hooks" / "pre-receive"
        write(hook, "#!/bin/sh\necho rejected-by-test >&2\nexit 1\n")
        new = self.app_commit("docs/reviews/A1-g6.md", "G6\n")
        self.assertNotEqual(git(self.A["app"], "push", "-q", ok=False).returncode, 0)  # code push failed
        self.set_notes("- app `docs/reviews/A1-g6.md`")
        code, out = self.wrapup()
        self.assertEqual(code, 4)
        self.assertEqual(out["level"], "local")
        self.assertIn({"repo_id": "app", "reason": "unpushed", "needed_for": "A1"}, out["pending_transfers"])
        code, a = self.cli("A", "verify", "--fetch")
        self.assertEqual(code, 4)
        self.assertFalse(a["published"])
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 4)
        self.assertIn(f"app@{new[:12]}", items(b["missing"]))
        self.assertIsNone(b["level"])

    def test_home_push_failure(self):
        self.clone_b()
        write(self.B["spec"] / "wiki/log.md", "B pushed first\n")
        git(self.B["spec"], "commit", "-q", "-am", "B")
        git(self.B["spec"], "push", "-q")
        code, out = self.wrapup()
        self.assertEqual(code, 0, out)
        self.assertNotEqual(out["push_rc"], 0)  # non-fast-forward, rejected
        carrier = head(self.A["spec"])
        code, a = self.cli("A", "verify", "--fetch")
        self.assertEqual(code, 4)
        self.assertIn(f"spec@{carrier[:12]}", items(a["missing"]))
        self.assertFalse(a["published"])
        code, b = self.cli("B", "verify", "--fetch", "--handoff", out["handoff_id"])
        self.assertEqual(code, 4)
        self.assertIn(out["handoff_id"], b["error"])

    def test_missing_commit_after_history_rewrite(self):
        f = self.A["app"]
        git(f, "checkout", "-q", "-b", "wip")
        x = self.app_commit("src/wip.c", "int x;\n")
        git(f, "push", "-q", "-u", "origin", "wip")
        code, out = self.wrapup()
        self.assertEqual(code, 0, out)
        git(f, "commit", "-q", "--amend", "-m", "rewritten")
        git(f, "push", "-q", "-f", "origin", "wip")
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 4)
        self.assertIn(f"app@{x[:12]}", items(b["missing"]))

    def test_untracked_report(self):
        write(self.A["app"] / "docs/reviews/A1-run.md", "a report never added to git\n")
        self.set_notes("- run record: app `docs/reviews/A1-run.md`")
        code, out = self.wrapup()
        self.assertEqual(code, 4)
        self.assertIn({"repo_id": "app", "relative_path": "docs/reviews/A1-run.md", "reason": "untracked",
                       "needed_for": "A1"}, out["pending_transfers"])
        self.assertIsNone(out["level"])
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 4)
        self.assertIn("app:docs/reviews/A1-run.md", items(b["missing"]))

    def test_bad_hash_home_file_changed_after_export(self):
        h = self.A["spec"]
        code, out = self.export()
        self.set_notes("- changed again after export")
        git(h, "add", "--", "progress/A1.md", *out["commit_paths"])
        git(h, "commit", "-q", "-m", "handoff")
        git(h, "push", "-q")
        code, a = self.cli("A", "verify", "--fetch")
        self.assertEqual(code, 3)
        self.assertIn("spec:progress/A1.md", items(a["stale"]))

    def test_bad_hash_in_manifest_object(self):
        code, out = self.wrapup()
        self.clone_b()
        d = self.manifest_of("B", out["handoff_id"]).to_dict()
        for f in d["required_files"]:
            if f["repo_id"] == "app":
                f["sha256"] = "0" * 64
        chk = ho.verify_handoff(ho.HandoffManifest.from_dict(d), self.local_dict("B"), True, policy=self.policy)
        self.assertEqual(chk.exit_code, 3)
        self.assertIn("app:docs/reviews/A1-g5.md", items(chk.stale))
        self.assertFalse(chk.ready)

    def test_dirty_clone_blocks_with_3_and_is_not_touched(self):
        self.wrapup()
        self.clone_b()
        target = self.B["app"] / "docs/reviews/A1-g5.md"
        write(target, "B's own uncommitted change\n")
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 3)
        self.assertIn("app:docs/reviews/A1-g5.md", items(b["local_dirty"]))
        self.assertEqual(target.read_text(encoding="utf-8"), "B's own uncommitted change\n")

    def test_unrelated_dirty_file_only_warns(self):
        self.wrapup()
        self.clone_b()
        write(self.B["app"] / "dependencies.lock", "changed by a build\n")
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 0, b)
        self.assertTrue(any("dependencies.lock" in w for w in b["warnings"]), b["warnings"])
        self.assertEqual((self.B["app"] / "dependencies.lock").read_text(encoding="utf-8"), "changed by a build\n")

    def test_wip_task_branch_without_merge(self):
        f = self.A["app"]
        git(f, "checkout", "-q", "-b", "task/a1")
        wip = self.app_commit("src/brew.c", "// WIP\n", "WIP: draft")
        git(f, "push", "-q", "-u", "origin", "task/a1")
        code, out = self.wrapup()
        self.assertEqual(code, 0, out)
        m = self.manifest_of("A", out["handoff_id"])
        app = next(r for r in m.repos if r["repo_id"] == "app")
        self.assertEqual((app["ref"], app["commit_sha"]), ("task/a1", wip))
        self.assertNotEqual(git(self.remote["app"], "rev-parse", "master").stdout.strip(), wip)  # not merged
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 0, b)
        self.assertEqual(b["level"], "resumable")

    def test_ref_deleted_but_sha_reachable_from_other_ref(self):
        f = self.A["app"]
        git(f, "checkout", "-q", "-b", "task/a1")
        self.app_commit("src/brew.c", "// done\n")
        git(f, "push", "-q", "-u", "origin", "task/a1")
        code, out = self.wrapup()
        git(f, "checkout", "-q", "master")
        git(f, "merge", "-q", "--ff-only", "task/a1")
        git(f, "push", "-q", "origin", "master")
        git(f, "push", "-q", "origin", "--delete", "task/a1")
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 0, b)
        self.assertTrue(any("task/a1" in w for w in b["warnings"]), b["warnings"])

    def test_ref_deleted_and_unreachable_is_missing(self):
        f = self.A["app"]
        git(f, "checkout", "-q", "-b", "task/x")
        x = self.app_commit("src/x.c", "// x\n")
        git(f, "push", "-q", "-u", "origin", "task/x")
        code, out = self.wrapup()
        self.assertEqual(code, 0, out)
        git(f, "push", "-q", "origin", "--delete", "task/x")
        code, a = self.cli("A", "verify", "--fetch")  # A still has the commit, but the remote does not
        self.assertEqual(code, 4)
        self.assertIn(f"app@{x[:12]}", items(a["missing"]))

    def test_referenced_but_unlisted_and_local_only(self):
        write(self.A["spec"] / ".notes/handoff.md", HANDOFF + "\n".join([
            "- 看 `A1-g2.md`",
            "- app `docs/reviews/A1-g9.md`",
            "- log 在 `build/x.log`",
            "- run dir `.git/specflow-runtime/runs/r1/`",
            "- 原機 log `C:/tmp/run.log`（僅本機）",
            "- other log `D:/tmp/run2.log` (local only)",
            "- 研究頁 https://claude.ai/artifact/abc",
        ]) + "\n")
        code, out = self.wrapup()
        self.assertEqual(code, 4)
        miss = items(out["missing"])
        for want in ("A1-g2.md", "app:docs/reviews/A1-g9.md", "build/x.log", ".git/specflow-runtime/runs/r1/"):
            self.assertIn(want, miss)
        self.assertFalse(any("C:/tmp/run.log" in x or "D:/tmp/run2.log" in x for x in miss))
        self.assertTrue(any("https://claude.ai/artifact/abc" in w for w in out["warnings"]))
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 4)
        for want in ("A1-g2.md", "app:docs/reviews/A1-g9.md", "build/x.log"):
            self.assertIn(want, items(b["missing"]))

    def test_handoff_links_other_task_notes(self):
        """The handoff file links notes of a task not selected: that file is carried, its references are not checked."""
        h = self.A["spec"]
        write(h / "specs/A2-x.md", "# A2\n")
        write(h / "progress/A2.md", "# A2\n\n- spec: `specs/A2-x.md`, `name-only.md`\n")
        git(h, "add", "-A")
        git(h, "commit", "-q", "-m", "A2")
        git(h, "push", "-q")
        write(h / ".notes/handoff.md", HANDOFF + "2. A2 to do → `progress/A2.md`\n")
        code, out = self.wrapup()
        self.assertEqual(code, 0, out)
        m = self.manifest_of("A", out["handoff_id"])
        self.assertEqual(m.task_ids, ["A1"])
        self.assertIn(("spec", "progress/A2.md"), {(f["repo_id"], f["relative_path"]) for f in m.required_files})
        code, a = self.cli("A", "verify", "--fetch")
        self.assertEqual(code, 0, a)
        self.assertTrue(a["published"])

    def test_local_only_mark_does_not_exempt_other_refs_on_line(self):
        """Another reference on the same line marked local only: the untracked report is still pending."""
        write(self.A["app"] / "docs/reviews/A1-run.md", "untracked report\n")
        self.set_notes("- board report app `docs/reviews/A1-run.md`; local log `build/r1/`（僅本機）")
        code, out = self.wrapup()
        self.assertEqual(code, 4)
        self.assertIn({"repo_id": "app", "relative_path": "docs/reviews/A1-run.md", "reason": "untracked",
                       "needed_for": "A1"}, out["pending_transfers"])
        self.assertFalse(any("build/" in x for x in items(out["missing"])))
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 4)
        self.assertIsNone(b["level"])
        self.assertIn("app:docs/reviews/A1-run.md", items(b["missing"]))

    def test_reference_dropped_from_manifest_is_missing(self):
        code, out = self.wrapup()
        self.clone_b()
        d = self.manifest_of("B", out["handoff_id"]).to_dict()
        d["required_files"] = [f for f in d["required_files"] if f["repo_id"] != "app"]
        chk = ho.verify_handoff(ho.HandoffManifest.from_dict(d), self.local_dict("B"), True, policy=self.policy)
        self.assertIn("app:docs/reviews/A1-g5.md", items(chk.missing))

    def test_verify_lists_all_missing_in_one_run(self):
        write(self.A["app"] / "docs/reviews/A1-run.md", "untracked\n")
        hook = self.remote["app"] / "hooks" / "pre-receive"
        write(hook, "#!/bin/sh\nexit 1\n")
        self.app_commit("src/new.c", "int n;\n")
        self.set_notes("- app `docs/reviews/A1-run.md`、`A1-g2.md`、app `docs/none.md`")
        self.wrapup()
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 4)
        miss = items(b["missing"])
        self.assertIn("app:docs/reviews/A1-run.md", miss)
        self.assertIn("A1-g2.md", miss)
        self.assertIn("app:docs/none.md", miss)
        self.assertTrue(any(x.startswith("app@") for x in miss))

    def test_two_machines_need_their_own_mapping(self):
        code, out = self.wrapup()
        self.clone_b()
        self.set_local("B", {"spec": self.B["spec"]})  # B has no mapping for app
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 4)
        self.assertTrue(any(x["item"] == "app" and "specflow.local.json" in x["reason"] for x in b["missing"]), b)


class TestArtifacts(World):
    def setUp(self):
        super().setUp()
        self.art = self.base / "產物 區" / "run log.txt"
        write(self.art, "log content\n")

    def test_artifact_ok_then_changed_then_gone(self):
        code, out = self.wrapup(extra=("--artifact", f"runlog={self.art.as_uri()}"))
        self.assertEqual(code, 0, out)
        m = self.manifest_of("A", out["handoff_id"])
        self.assertEqual(m.artifacts[0]["size_bytes"], len(self.art.read_bytes()))
        self.assertEqual(m.artifacts[0]["sha256"], sha(self.art.read_bytes()))
        self.clone_b()
        self.assertEqual(self.cli("B", "verify", "--fetch")[0], 0)
        write(self.art, "changed\n")
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 3)
        self.assertIn("artifact:runlog", items(b["stale"]))
        self.art.unlink()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 4)
        self.assertIn("artifact:runlog", items(b["missing"]))

    def test_optional_artifact_only_warns(self):
        code, out = self.wrapup(extra=("--optional-artifact", f"page={self.art.as_uri()}"))
        self.assertEqual(code, 0, out)
        self.art.unlink()
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 0, b)
        self.assertTrue(any("page" in w for w in b["warnings"]))

    def test_unreadable_required_artifact_is_upload_failed(self):
        gone = (self.base / "沒有這個.bin").as_uri()
        code, out = self.export(extra=("--artifact", f"bin={gone}"))
        self.assertEqual(code, 4)
        self.assertIn({"artifact_id": "bin", "reason": "upload_failed", "needed_for": "A1"}, out["pending_transfers"])


# ---------------------------------------------------------------- levels and exit codes

class TestLevelsAndExitCodes(World):
    def test_three_levels(self):
        hook = self.remote["app"] / "hooks" / "pre-receive"
        write(hook, "#!/bin/sh\nexit 1\n")
        self.app_commit("src/a.c", "a\n")
        code, out = self.wrapup()
        self.assertEqual((code, out["level"]), (4, "local"))  # pending items: still local
        hook.unlink()
        git(self.A["app"], "push", "-q")
        code, out = self.wrapup()  # wrap up again (new handoff id)
        self.assertEqual(code, 0, out)
        code, a = self.cli("A", "verify")
        self.assertEqual(code, 0, a)
        self.assertFalse(a["published"])  # no fetch: not published
        self.assertIsNone(a["level"])
        code, a = self.cli("A", "verify", "--fetch")
        self.assertTrue(a["published"])
        self.assertEqual(a["level"], "resumable")
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual((code, b["level"]), (0, "resumable"))
        code, b = self.cli("B", "verify", "--fetch", "--for", "hardware_e2e")
        self.assertEqual((code, b["level"]), (5, "published"))  # published, but not resumable on this machine

    def test_capability_missing_exit_5_via_api(self):
        code, out = self.wrapup()
        self.clone_b()
        m = self.manifest_of("B", out["handoff_id"])
        fake = lambda n: None if n == "cc" else shutil.which(n)  # noqa: E731
        chk = ho.verify_handoff(m, self.local_dict("B"), True, policy=self.policy, capabilities=["app_build"],
                                which=fake)
        self.assertEqual(chk.exit_code, 5)
        self.assertTrue(chk.published)
        self.assertFalse(chk.resumable)
        self.assertEqual(chk.level, "published")
        self.assertTrue(any("missing tools cc" in x["reason"] for x in chk.capability_missing), chk.capability_missing)

    def test_capability_probe_timeout_is_capability_not_data(self):
        """A tool version probe that times out is a missing capability (5), not a data timeout: published stays."""
        code, out = self.wrapup()
        self.clone_b()
        m = self.manifest_of("B", out["handoff_id"])

        def slow(argv, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)
        chk = ho.verify_handoff(m, self.local_dict("B"), True, policy=self.policy, probe=slow)
        self.assertEqual(chk.exit_code, 5)
        self.assertTrue(chk.published)
        self.assertEqual(chk.timed_out, [])
        self.assertTrue(any("timed out" in x["reason"] for x in chk.capability_missing), chk.capability_missing)

    def test_cli_exit_codes(self):
        code, out = self.cli("A", "verify")
        self.assertEqual(code, 4)  # no manifest yet
        code, out = self.wrapup()
        self.clone_b()
        self.assertEqual(self.cli("B", "verify", "--fetch")[0], 0)
        self.assertEqual(self.cli("B", "verify", "--fetch", "--for", "nope")[0], 2)
        self.assertEqual(self.cli("B", "verify", "--fetch", "--for", "hardware_e2e")[0], 5)
        root = self.B["spec"] / ".notes" / "handoff.manifest.json"
        d = json.loads(root.read_text(encoding="utf-8"))
        d["required_files"][0]["sha256"] = "0" * 64
        root.write_text(json.dumps(d), encoding="utf-8")
        self.assertEqual(self.cli("B", "verify", "--fetch")[0], 3)
        root.write_text("{ broken", encoding="utf-8")
        self.assertEqual(self.cli("B", "verify", "--fetch")[0], 2)

    def test_human_output_without_json(self):
        self.wrapup()
        code, _ = self.cli("A", "verify", "--fetch", json_out=False)
        self.assertEqual(code, 0)
        self.assertIn("resumable", self.last_text)


class TestTimeouts(World):
    """The remote goes through a fake ssh that sleeps: a timeout is recorded as {item, reason} and the rest is checked."""

    def slow_ssh(self, repo):
        git(repo, "remote", "set-url", "origin", "ssh://example.invalid/x.git")
        script = self.base / "slow ssh.py"
        script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        return mock.patch.dict(os.environ, {"GIT_SSH_COMMAND": f'"{sys.executable}" "{script.as_posix()}"'})

    def test_fetch_timeout_is_exit_6_and_keeps_collecting(self):
        art = self.base / "產物" / "log.txt"
        write(art, "log\n")
        code, out = self.wrapup(extra=("--artifact", f"log={art.as_uri()}"))
        self.assertEqual(code, 0, out)
        self.clone_b()
        m = self.manifest_of("B", out["handoff_id"])
        with self.slow_ssh(self.B["app"]):
            chk = ho.verify_handoff(m, self.local_dict("B"), True, policy=self.policy, timeout=2)
            self.assertEqual(chk.exit_code, 6, chk.to_dict())
            self.assertEqual(items(chk.timed_out), ["app"])
            self.assertFalse(chk.published)
            art.unlink()
            chk = ho.verify_handoff(m, self.local_dict("B"), True, policy=self.policy, timeout=2)
        self.assertEqual(chk.exit_code, 4)  # missing wins, but the timeout is still listed
        self.assertIn("artifact:log", items(chk.missing))
        self.assertEqual(items(chk.timed_out), ["app"])

    def test_load_manifest_fetch_timeout(self):
        code, out = self.wrapup()
        self.clone_b()
        hid = out["handoff_id"]
        (self.B["spec"] / ".notes" / "handoffs" / f"{hid}.json").unlink()  # only the git history has it now
        with self.slow_ssh(self.B["spec"]):
            m = ho.load_manifest(self.B["spec"], self.cfg, hid, fetch_alias="origin", timeout=2)
            self.assertEqual(m.handoff_id, hid)
            with self.assertRaises(ho.HandoffError) as cm:
                ho.load_manifest(self.B["spec"], self.cfg, "20990101T0000-X-0000", fetch_alias="origin", timeout=2)
        self.assertEqual(cm.exception.code, 6)


# ---------------------------------------------------------------- report-only

class TestReportOnly(World):
    def test_report_lists_unobtainable_and_writes_nothing(self):
        h = self.A["spec"]
        write(self.A["app"] / "docs/reviews/A1-run.md", "untracked\n")
        write(h / ".notes/handoff.md", HANDOFF + "- run `qr-loop.bat`; see app `docs/reviews/A1-run.md`\n"
              "- [[board-not-started]]\n- progress page https://claude.ai/artifact/xyz\n")
        git(h, "commit", "-q", "-am", "current handoff (no manifest)")
        git(h, "push", "-q")
        before = git(h, "status", "--porcelain").stdout
        code, out = self.cli("A", "report", "--task", "A1", "--fetch")
        self.assertEqual(code, 4)
        miss = items(out["missing"])
        for want in ("qr-loop.bat", "app:docs/reviews/A1-run.md", "[[board-not-started]]"):
            self.assertIn(want, miss)
        self.assertTrue(any("claude.ai/artifact/xyz" in w for w in out["warnings"]))
        self.assertFalse((h / ".notes" / "handoffs").exists())
        self.assertFalse((h / ".notes" / "handoff.manifest.json").exists())
        self.assertEqual(git(h, "status", "--porcelain").stdout, before)

    def test_report_flags_unpushed_home(self):
        h = self.A["spec"]
        git(h, "checkout", "-q", "-b", "local-branch")
        write(h / "wiki/log.md", "x\n")
        git(h, "commit", "-q", "-am", "local")
        code, out = self.cli("A", "report", "--task", "A1", "--fetch")
        self.assertEqual(code, 4)
        self.assertIn(f"spec@{head(h)[:12]}", items(out["missing"]))


# ---------------------------------------------------------------- another home repo, no task notes

class TestOtherHome(World):
    """Nothing about the home repo is assumed: it is called docs, the product folder is its root, the handoff file is
    at the root and there are no task notes (the handoff file is the only source)."""
    HOME = "docs"
    PRODUCT = ""
    HANDOFF_CFG = {"file": "handoff.md"}
    HANDOFF_TEXT = "# Handoff\n\n- next: app `docs/reviews/A1-g5.md`, design `specs/A1-brew.md`\n"
    NOTES = {}

    def test_round_trip(self):
        h = self.A["docs"]
        write(h / "progress/A1.md", "- `nowhere/x.md`\n")  # not a source: there are no task notes
        git(h, "add", "--", "progress/A1.md")
        git(h, "commit", "-q", "-m", "notes outside the handoff")
        git(h, "push", "-q")
        code, out = self.wrapup()
        self.assertEqual((code, out["level"], out["push_rc"]), (0, "local", 0), out)
        hid = out["handoff_id"]
        self.assertEqual((out["manifest_path"], out["root_manifest_path"]),
                         (f"handoffs/{hid}.json", "handoff.manifest.json"))
        self.assertTrue((h / "handoffs" / f"{hid}.json").is_file())
        m = self.manifest_of("A", hid)
        self.assertEqual(m.task_ids, ["A1"])
        self.assertEqual({r["repo_id"] for r in m.repos}, {"docs", "app"})
        self.assertEqual({(f["repo_id"], f["relative_path"]) for f in m.required_files},
                         {("docs", "handoff.md"), ("docs", "specs/A1-brew.md"), ("app", "docs/reviews/A1-g5.md")})
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual((code, b["level"]), (0, "resumable"), b)
        code, r = self.cli("B", "report", "--fetch")
        self.assertEqual(code, 0, r)
        self.assertNotIn("vault", json.dumps([out, b, r]))

    def test_missing_reference_names_the_home_repo(self):
        write(self.A["docs"] / "handoff.md", self.HANDOFF_TEXT + "- also `notes/gone.md` and `build/x.log`\n")
        code, out = self.wrapup(tasks=("A9",))  # no notes file is needed for any task
        self.assertEqual(code, 4, out)
        # no local_only prefixes here: build/x.log is an ordinary path of the home repo
        self.assertEqual(sorted(items(out["missing"])), ["docs:build/x.log", "docs:notes/gone.md"])
        self.clone_b()
        code, b = self.cli("B", "verify", "--fetch")
        self.assertEqual(code, 4)
        self.assertIn("docs:notes/gone.md", items(b["missing"]))


if __name__ == "__main__":
    unittest.main()
