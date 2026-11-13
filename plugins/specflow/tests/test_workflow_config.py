"""Tests for workflow_config.py. Run: python -m unittest discover -s plugins/specflow/tests

No real build or tool runs: tool probes use fake which/probe. Everything lives in temp folders; the installed-plugins
file is always a temp file passed with --installed.
"""
import contextlib
import copy
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location("workflow_config", SCRIPTS / "workflow_config.py")
wc = importlib.util.module_from_spec(_spec)
sys.modules["workflow_config"] = wc
_spec.loader.exec_module(wc)

POLICY = {
    "schema_version": 1,
    "entry": {"cwd": "spec"},
    "repos": {
        "docs": {"remote_alias": "origin", "integration_branch": "main", "home": True},
        "app": {"remote_alias": "origin", "integration_branch": "main"},
    },
    "tools": {"git": {"version_args": ["--version"]}, "python": {"version_args": ["--version"]},
              "runner": {"version_args": ["--version"]}},
    "capabilities": {
        "spec_work": {"tools": ["git", "python"], "repos": []},
        "docs_work": {"tools": ["git"], "repos": ["docs"]},
        "build": {"tools": ["git", "runner"], "repos": ["app"]},
        "rig": {"tools": [], "repos": ["app"], "measurable": False},
    },
    "test_profiles": {"unit": {"repo": "app", "capability": "build", "argv": ["run", "unit"], "timeout_seconds": 60}},
    "roles": {"implementer": {"tool": "runner", "model": "m1", "fallback_role": "backup"},
              "backup": {"tool": "runner", "model": "m2"}},
    "dispatch": {"timeout_seconds": 600, "session_timeout_seconds": 3600, "requires_lock": True,
                 "requires_capabilities": ["build"]},
    "resources": ["port"],
}
LOCK = {
    "schema_version": 1,
    "core": {"plugin": "specflow@mkt", "version": "0.1.0", "commit": "a" * 40},
    "plugins": {
        "specflow@mkt": {"marketplace": "mkt", "source": {"type": "github"}, "version": "0.1.0",
                         "commit": "a" * 40, "required": True},
        "dep@mkt2": {"marketplace": "mkt2", "source": {"type": "github"}, "version": "1.0.0",
                     "commit": "b" * 40, "required": True},
        "extra@mkt3": {"marketplace": "mkt3", "source": {"type": "directory"}, "version": "2.0.0",
                       "commit": "c" * 40, "required": False},
    },
}


def policy():
    return copy.deepcopy(POLICY)


def lock():
    return copy.deepcopy(LOCK)


def installed_from(lk, **override):
    """A fake installed_plugins.json built from a lock; override: key -> fields to replace."""
    plugins = {}
    for key, p in lk["plugins"].items():
        e = {"scope": "user", "version": p["version"], "gitCommitSha": p["commit"]}
        e.update(override.get(key, {}))
        plugins[key] = [e]
    return {"version": 2, "plugins": plugins}


def git_init(d):
    Path(d).mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(d)], check=True)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.t.cleanup)
        self.tmp = Path(self.t.name)

    def write(self, rel, obj):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return p

    def make_product(self, git=True, pol=None, core="0.1.0"):
        """tmp/'proj repo' (a git repo) holding the product folder 'spec' with the policy, lock and specflow.md."""
        proj = self.tmp / "proj repo"
        if git:
            git_init(proj)
        spec = proj / "spec"
        spec.mkdir(parents=True, exist_ok=True)
        (spec / "workflow.json").write_text(json.dumps(pol or policy()), encoding="utf-8")
        (spec / "workflow.lock.json").write_text(json.dumps(lock()), encoding="utf-8")
        (spec / "specflow.md").write_text(f"---\nformat: 2\ncore: {core}\n---\n", encoding="utf-8")
        return proj, spec

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = wc.main([str(a) for a in argv])
        return rc, out.getvalue(), err.getvalue()


class TestSchema(Tmp):
    def test_fixtures_are_valid(self):
        self.assertEqual(wc.validate_policy(policy()), [])
        self.assertEqual(wc.validate_lock(lock()), [])

    def test_policy_has_no_component_state(self):
        """components.json is the only authority: the policy must not carry component state fields."""
        text = json.dumps(POLICY)
        for k in ('"status"', '"blocked"', '"deps"', '"components"'):
            self.assertNotIn(k, text)
        bad = policy()
        bad["components"] = []
        self.assertTrue(any("components" in e for e in wc.validate_policy(bad)))

    def test_bad_schema_reports_every_problem(self):
        bad = policy()
        bad["schema_version"] = 99
        del bad["tools"]
        bad["repos"]["app"].pop("integration_branch")
        self.assertGreaterEqual(len(wc.validate_policy(bad)), 3)

    def test_minimal_policy_validates(self):
        """Only repos, tools, capabilities and dispatch.timeout_seconds are required (e.g. a docs-only project)."""
        mini = {"schema_version": 1,
                "repos": {"docs": {"integration_branch": "main", "home": True}},
                "tools": {"git": {}},
                "capabilities": {"spec_work": {"tools": ["git"], "repos": []}},
                "dispatch": {"timeout_seconds": 600}}
        self.assertEqual(wc.validate_policy(mini), [])
        self.assertEqual(wc.remote_alias(mini, "docs"), "origin")
        self.assertEqual(wc.remote_alias(policy(), "app"), "origin")
        for k in ("repos", "tools", "capabilities", "dispatch"):
            bad = copy.deepcopy(mini)
            del bad[k]
            with self.subTest(missing=k):
                self.assertTrue(any(k in e for e in wc.validate_policy(bad)))
        bad = copy.deepcopy(mini)
        del bad["dispatch"]["timeout_seconds"]
        self.assertTrue(any("timeout_seconds" in e for e in wc.validate_policy(bad)))

    def test_optional_keys_are_validated_when_present(self):
        def errs(mut):
            d = policy()
            mut(d)
            return wc.validate_policy(d)
        cases = {
            "entry": lambda d: d.__setitem__("entry", "spec"),
            "test_profiles": lambda d: d["test_profiles"]["unit"].__setitem__("argv", []),
            "roles": lambda d: d["roles"]["implementer"].pop("model"),
            "remote_alias": lambda d: d["repos"]["app"].__setitem__("remote_alias", ""),
            "session_timeout_seconds": lambda d: d["dispatch"].__setitem__("session_timeout_seconds", 0),
        }
        for key, mut in cases.items():
            with self.subTest(key=key):
                self.assertTrue(any(key in e for e in errs(mut)), errs(mut))
        for key in ("entry", "test_profiles", "roles"):
            d = policy()
            del d[key]
            d["dispatch"].pop("session_timeout_seconds", None)
            with self.subTest(absent=key):
                self.assertEqual(wc.validate_policy(d), [])

    def test_handoff_block_is_validated_when_present(self):
        """A bad handoff block fails validate / doctor up front, not only when handoff.py runs."""
        ok = policy()
        ok["handoff"] = {"file": ".notes/handoff.md", "task_notes": "progress/{id}.md", "local_only": ["build/"]}
        self.assertEqual(wc.validate_policy(ok), [])
        bad_blocks = ["handoff.md", {}, {"file": "/abs/h.md"}, {"file": "../h.md"}, {"file": "h.md", "extra": 1},
                      {"file": "h.md", "task_notes": "progress/x.md"}, {"file": "h.md", "local_only": "build/"},
                      {"file": "h.md", "local_only": [".."]}]
        for b in bad_blocks:
            with self.subTest(block=b):
                d = policy()
                d["handoff"] = b
                self.assertTrue(any("handoff" in e for e in wc.validate_policy(d)), wc.validate_policy(d))
        d = self.make_product(pol={**policy(), "handoff": {"file": "/abs"}})[1]
        self.assertEqual(self.run_cli("validate", "--product", d)[0], 2)

    def test_dispatch_implementer_and_report_are_validated_when_present(self):
        impl = {"argv": ["tool", "--cd", "{worktree}", "-o", "{report}", "--schema", "{schema}", "-m", "{model}",
                         "--effort", "{effort}", "--dir", "{run_dir}"], "extra_allowed": {"--ephemeral": False, "-i": True}}
        rep = {"schema": "report.schema.json", "task_field": "task", "status_field": "status",
               "done_requires": [{"field": "unmet", "empty": True}, {"field": "tests", "in": ["pass", "na"]},
                                 {"field": "ok", "equals": True}]}
        d = policy()
        d["dispatch"].update(implementer=impl, report=rep)
        self.assertEqual(wc.validate_policy(d), [])

        def errs(mut):
            x = policy()
            x["dispatch"].update(implementer=copy.deepcopy(impl), report=copy.deepcopy(rep))
            mut(x["dispatch"])
            return wc.validate_policy(x)
        cases = {
            "implementer.argv": lambda t: t["implementer"].__setitem__("argv", []),
            "implementer.argv ": lambda t: t["implementer"].__setitem__("argv", ["tool", 3]),
            "{bogus}": lambda t: t["implementer"]["argv"].append("{bogus}"),
            "extra_allowed": lambda t: t["implementer"].__setitem__("extra_allowed", {"--x": "yes"}),
            "extra_allowed ": lambda t: t["implementer"].__setitem__("extra_allowed", {"x": False}),
            "fixed": lambda t: t["implementer"].__setitem__("extra_allowed", {"--cd": False}),
            "unknown field": lambda t: t["implementer"].__setitem__("extra", 1),
            "report.schema": lambda t: t["report"].__setitem__("schema", "../x.json"),
            "report.schema ": lambda t: t["report"].__setitem__("schema", ""),
            "report.task_field": lambda t: t["report"].__setitem__("task_field", ""),
            "report.status_field": lambda t: t["report"].pop("status_field"),
            "done_requires": lambda t: t["report"].__setitem__("done_requires", {"field": "x"}),
            "done_requires[0]": lambda t: t["report"]["done_requires"][0].pop("empty"),
            "done_requires[1]": lambda t: t["report"]["done_requires"][1].update(equals=1),
            "done_requires[0] ": lambda t: t["report"]["done_requires"][0].__setitem__("empty", False),
            "unknown field ": lambda t: t["report"].__setitem__("extra", 1),
        }
        for key, mut in cases.items():
            with self.subTest(key=key):
                got = errs(mut)
                self.assertTrue(any(key.strip() in e for e in got), got)
        for absent in ("implementer", "report"):  # each block is optional for validate; dispatch_run needs both
            x = policy()
            x["dispatch"].update(implementer=impl, report=rep)
            del x["dispatch"][absent]
            self.assertEqual(wc.validate_policy(x), [])

    def test_exactly_one_home_repo(self):
        none_home = policy()
        none_home["repos"]["docs"].pop("home")
        two_homes = policy()
        two_homes["repos"]["app"]["home"] = True
        for bad in (none_home, two_homes):
            with self.subTest(homes=[r for r, v in bad["repos"].items() if v.get("home")]):
                self.assertTrue(any("home" in e for e in wc.validate_policy(bad)))
        self.assertEqual(wc.home_id(policy()), "docs")

    def test_resources_are_extra_kinds(self):
        self.assertEqual(wc.policy_resources(policy()), ("port",))
        no_res = policy()
        del no_res["resources"]
        self.assertEqual(wc.validate_policy(no_res), [])
        self.assertEqual(wc.policy_resources(no_res), ())
        for v in ("port", ["a:b"], [""], [1]):
            bad = policy()
            bad["resources"] = v
            with self.subTest(v=v):
                self.assertTrue(any("resources" in e for e in wc.validate_policy(bad)))

    def test_session_timeout_is_optional_positive_int(self):
        for v in (0, -1, "3600", True):
            bad = policy()
            bad["dispatch"]["session_timeout_seconds"] = v
            with self.subTest(v=v):
                self.assertTrue(any("session_timeout_seconds" in e for e in wc.validate_policy(bad)))

    def test_unknown_repo_in_profile_is_rejected(self):
        bad = policy()
        bad["test_profiles"]["unit"]["repo"] = "nope"
        self.assertTrue(any("nope" in e for e in wc.validate_policy(bad)))

    def test_load_policy_bad_json_is_code_2(self):
        p = self.write("workflow.json", "{ not json")
        with self.assertRaises(wc.ConfigError) as cm:
            wc.load_policy(p)
        self.assertEqual(cm.exception.code, 2)

    def test_load_policy_schema_violation_is_code_2(self):
        p = self.write("workflow.json", {"schema_version": 1})
        with self.assertRaises(wc.ConfigError) as cm:
            wc.load_policy(p)
        self.assertEqual(cm.exception.code, 2)

    def test_load_missing_file_is_code_4(self):
        with self.assertRaises(wc.ConfigError) as cm:
            wc.load_policy(self.tmp / "none.json")
        self.assertEqual(cm.exception.code, 4)

    def test_cli_validate_reads_the_product_folder(self):
        _proj, spec = self.make_product()
        self.assertEqual(self.run_cli("validate", "--product", spec)[0], 0)
        self.assertEqual(self.run_cli("--product", spec, "validate")[0], 0)
        self.assertEqual(self.run_cli("validate", "--product", self.tmp / "empty")[0], 4)


class TestLocalMapping(Tmp):
    def test_non_ascii_and_space_paths(self):
        d = self.tmp / "dir with space" / "repo \u00e9\u4e2d"
        git_init(d)
        lp = self.write("specflow.local.json", {"schema_version": 1, "repos": {"docs": {"path": d.as_posix()}}})
        r = wc.resolve_repos(policy(), wc.load_local(lp, policy()))
        self.assertTrue(r["docs"].is_git)
        self.assertEqual(r["docs"].path, d)
        self.assertFalse(r["app"].exists)  # unmapped repos are not guessed

    def test_two_machines_same_policy_different_paths(self):
        a, b = self.tmp / "A" / "app", self.tmp / "B machine" / "app"
        for d in (a, b):
            git_init(d)
        pa = self.write("A.json", {"repos": {"app": {"path": a.as_posix()}}})
        pb = self.write("B.json", {"repos": {"app": {"path": b.as_posix()}}})
        pol = policy()
        ra = wc.resolve_repos(pol, wc.load_local(pa, pol))
        rb = wc.resolve_repos(pol, wc.load_local(pb, pol))
        self.assertEqual((ra["app"].path, rb["app"].path), (a, b))
        self.assertTrue(ra["app"].is_git and rb["app"].is_git)

    def test_unknown_repo_id_in_local_is_code_2(self):
        lp = self.write("l.json", {"repos": {"nope": {"path": "x"}}})
        with self.assertRaises(wc.ConfigError) as cm:
            wc.load_local(lp, policy())
        self.assertEqual(cm.exception.code, 2)

    def test_local_missing_file_means_empty_mapping(self):
        self.assertEqual(wc.load_local(self.tmp / "none.json", policy()), {})

    def test_relative_path_in_local_is_rejected(self):
        lp = self.write("l.json", {"repos": {"app": {"path": "app"}}})
        with self.assertRaises(wc.ConfigError):
            wc.load_local(lp, policy())

    def test_optional_machine_label(self):
        ok = self.write("ok.json", {"machine": "laptop-1", "repos": {}})
        self.assertEqual(wc.load_local(ok, policy()), {})
        self.assertEqual(wc.load_machine(ok), "laptop-1")
        self.assertIsNone(wc.load_machine(self.write("none.json", {"repos": {}})))
        self.assertIsNone(wc.load_machine(self.tmp / "absent.json"))
        bad = self.write("bad.json", {"machine": 5, "repos": {}})
        with self.assertRaises(wc.ConfigError) as cm:
            wc.load_local(bad, policy())
        self.assertEqual(cm.exception.code, 2)

    def test_repo_path_helper_falls_back(self):
        sentinel = Path("X:/fallback")
        self.assertEqual(wc.repo_path("app", self.tmp / "none.json", fallback=sentinel), sentinel)
        d = self.tmp / "with space"
        d.mkdir()
        lp = self.write("l.json", {"repos": {"app": {"path": d.as_posix()}}})
        self.assertEqual(wc.repo_path("app", lp, fallback=sentinel), d)

    def test_repo_path_has_no_default(self):
        self.assertIsNone(wc.repo_path("app", self.tmp / "none.json"))

    def test_require_repo_path_names_id_and_file(self):
        lf = self.tmp / "specflow.local.json"
        with self.assertRaises(wc.ConfigError) as cm:
            wc.require_repo_path("app", lf)
        self.assertEqual(cm.exception.code, 4)
        self.assertIn("app", str(cm.exception))
        self.assertIn("specflow.local.json", str(cm.exception))

    def test_local_file_sits_at_the_home_repo_root(self):
        proj, spec = self.make_product()
        self.assertEqual(wc.home_root(spec), proj.resolve())
        self.assertEqual(wc.local_file(spec), proj.resolve() / "specflow.local.json")
        # outside git the product folder itself stands in for the home repo root
        lone = self.tmp / "lone"
        lone.mkdir()
        self.assertEqual(wc.home_root(lone), lone.resolve())

    def test_cli_repo_path(self):
        proj, spec = self.make_product()
        self.assertEqual(self.run_cli("repo-path", "app", "--product", spec)[0], 4)   # unmapped
        rc, out, _ = self.run_cli("repo-path", "docs", "--product", spec)             # home defaults to the home root
        self.assertEqual((rc, Path(out.strip())), (0, proj.resolve()))
        self.assertEqual(self.run_cli("repo-path", "nope", "--product", spec)[0], 2)
        d = self.tmp / "app repo"
        git_init(d)
        (proj / "specflow.local.json").write_text(json.dumps({"repos": {"app": {"path": d.as_posix()}}}), encoding="utf-8")
        rc, out, _ = self.run_cli("repo-path", "app", "--product", spec)
        self.assertEqual((rc, Path(out.strip())), (0, d))


class TestLock(Tmp):
    def test_matching_install_passes(self):
        res = wc.check_lock(lock(), installed_from(lock()))
        self.assertTrue(res.ok, res.blocking)
        self.assertEqual(res.blocking, [])

    def test_incompatible_pin_is_blocking(self):
        res = wc.check_lock(lock(), installed_from(lock(), **{"specflow@mkt": {"version": "0.0.2"}}))
        self.assertFalse(res.ok)
        self.assertTrue(any("specflow@mkt" in b for b in res.blocking))

    def test_same_version_different_commit_is_blocking(self):
        res = wc.check_lock(lock(), installed_from(lock(), **{"specflow@mkt": {"gitCommitSha": "0" * 40}}))
        self.assertFalse(res.ok)

    def test_missing_required_plugin_is_blocking(self):
        inst = installed_from(lock())
        del inst["plugins"]["specflow@mkt"]
        self.assertFalse(wc.check_lock(lock(), inst).ok)

    def test_optional_plugin_mismatch_only_warns(self):
        res = wc.check_lock(lock(), installed_from(lock(), **{"extra@mkt3": {"version": "9.9.9"}}))
        self.assertTrue(res.ok)
        self.assertTrue(res.warnings)

    def test_user_scope_entry_wins_over_project_scope(self):
        inst = installed_from(lock())
        inst["plugins"]["specflow@mkt"].insert(
            0, {"scope": "local", "projectPath": "x", "version": "0.0.1", "gitCommitSha": "1" * 40})
        self.assertTrue(wc.check_lock(lock(), inst).ok)

    def test_specflow_md_core_must_match_lock(self):
        md = self.write("specflow.md", "---\nformat: 2\ncore: 0.0.2\npython: python\n---\n")
        res = wc.check_lock(lock(), installed_from(lock()), specflow_md=md)
        self.assertFalse(res.ok)
        self.assertTrue(any("core" in b for b in res.blocking))
        ok_md = self.write("ok.md", f"---\nformat: 2\ncore: {lock()['core']['version']}\n---\n")
        self.assertTrue(wc.check_lock(lock(), installed_from(lock()), specflow_md=ok_md).ok)

    def test_lock_with_inconsistent_core_is_invalid(self):
        bad = lock()
        bad["core"]["commit"] = "d" * 40
        self.assertTrue(any("core.commit" in e for e in wc.validate_lock(bad)))
        bad = lock()
        bad["plugins"]["dep@mkt2"]["commit"] = "short"
        self.assertTrue(any("40 hex" in e for e in wc.validate_lock(bad)))

    def test_cli_lock_check_exit_3_on_mismatch(self):
        _proj, spec = self.make_product()
        inst = self.write("inst.json", installed_from(lock(), **{"specflow@mkt": {"version": "0.0.2"}}))
        rc, out, _ = self.run_cli("lock-check", "--product", spec, "--installed", inst)
        self.assertEqual(rc, 3)
        self.assertIn("specflow@mkt", out)

    def test_cli_lock_check_exit_0_when_match(self):
        _proj, spec = self.make_product()
        inst = self.write("inst.json", installed_from(lock()))
        self.assertEqual(self.run_cli("lock-check", "--product", spec, "--installed", inst)[0], 0)

    def test_cli_lock_check_missing_installed_is_4(self):
        _proj, spec = self.make_product()
        self.assertEqual(self.run_cli("lock-check", "--product", spec, "--installed", self.tmp / "no.json")[0], 4)


class TestLockWrite(Tmp):
    def setUp(self):
        super().setUp()
        self.inst_file = self.write("plugins/installed_plugins.json", {"version": 2, "plugins": {
            "specflow@mkt": [{"scope": "local", "projectPath": "x", "version": "0.0.1", "gitCommitSha": "1" * 40},
                             {"scope": "user", "installPath": str(wc.PLUGIN_ROOT), "version": "0.1.0", "gitCommitSha": "a" * 40}],
            "dep@mkt2": [{"scope": "user", "version": "1.0.0", "gitCommitSha": "b" * 40}],
            "unrelated@mkt3": [{"scope": "user", "version": "3.0.0", "gitCommitSha": "e" * 40}]}})
        self.write("plugins/known_marketplaces.json", {
            "mkt": {"source": {"source": "github", "repo": "owner/specflow-repo"}},
            "mkt2": {"source": {"source": "github", "repo": "owner/dep-repo"}}})
        self.pj = self.write("plugin.json", {"name": "specflow", "version": "0.1.0", "dependencies": ["dep@mkt2"]})

    def test_writes_core_and_dependencies_from_installed_plugins(self):
        _proj, spec = self.make_product()
        (spec / "workflow.lock.json").unlink()
        rc, out, err = self.run_cli("lock-write", "--product", spec, "--installed", self.inst_file, "--plugin-json", self.pj)
        self.assertEqual(rc, 0, err)
        lk = json.loads((spec / "workflow.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(wc.validate_lock(lk), [])
        self.assertEqual(lk["core"], {"plugin": "specflow@mkt", "version": "0.1.0", "commit": "a" * 40})  # user scope wins
        self.assertEqual(set(lk["plugins"]), {"specflow@mkt", "dep@mkt2"})                                # not unrelated@mkt3
        self.assertEqual(lk["plugins"]["dep@mkt2"]["source"], {"type": "github", "repo": "owner/dep-repo"})
        self.assertTrue(all(p["required"] for p in lk["plugins"].values()))
        self.assertTrue(wc.check_lock(lk, json.loads(self.inst_file.read_text(encoding="utf-8"))).ok)
        self.assertEqual(self.run_cli("lock-check", "--product", spec, "--installed", self.inst_file)[0], 0)

    def test_missing_marketplaces_file_gives_unknown_source(self):
        _proj, spec = self.make_product()
        (self.inst_file.parent / "known_marketplaces.json").unlink()
        self.assertEqual(self.run_cli("lock-write", "--product", spec, "--installed", self.inst_file, "--plugin-json", self.pj)[0], 0)
        lk = json.loads((spec / "workflow.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lk["plugins"]["dep@mkt2"]["source"], {"type": "unknown"})

    def test_uninstalled_dependency_is_exit_4_and_writes_nothing(self):
        _proj, spec = self.make_product()
        (spec / "workflow.lock.json").unlink()
        self.write("plugin2.json", {"name": "specflow", "dependencies": ["gone@mkt9"]})
        rc, _out, err = self.run_cli("lock-write", "--product", spec, "--installed", self.inst_file,
                                     "--plugin-json", self.tmp / "plugin2.json")
        self.assertEqual(rc, 4)
        self.assertIn("gone@mkt9", err)
        self.assertFalse((spec / "workflow.lock.json").exists())

    def test_core_not_installed_is_exit_4(self):
        _proj, spec = self.make_product()
        pj = self.write("pj3.json", {"name": "nothere", "dependencies": []})
        self.assertEqual(self.run_cli("lock-write", "--product", spec, "--installed", self.inst_file, "--plugin-json", pj)[0], 4)

    def _two_marketplaces(self, here):
        entry = lambda path, sha: [{"scope": "user", "installPath": str(path), "version": "0.1.0", "gitCommitSha": sha}]
        return self.write("plugins/two.json", {"version": 2, "plugins": {
            "specflow@mkt": entry(here, "a" * 40),
            "specflow@other": entry(self.tmp / "elsewhere", "f" * 40),
            "dep@mkt2": entry(self.tmp / "dep", "b" * 40)}})

    def test_core_is_the_install_whose_path_is_this_plugin(self):
        _proj, spec = self.make_product()
        inst = self._two_marketplaces(wc.PLUGIN_ROOT)
        self.assertEqual(self.run_cli("lock-write", "--product", spec, "--installed", inst, "--plugin-json", self.pj)[0], 0)
        lk = json.loads((spec / "workflow.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lk["core"]["plugin"], "specflow@mkt")
        # the other marketplace's copy is the one running -> that one is pinned (sorted order would pick specflow@mkt)
        inst = self.write("plugins/three.json", json.loads(self._two_marketplaces(self.tmp / "x").read_text(encoding="utf-8")))
        data = json.loads(inst.read_text(encoding="utf-8"))
        data["plugins"]["specflow@other"][0]["installPath"] = str(wc.PLUGIN_ROOT)
        inst.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.run_cli("lock-write", "--product", spec, "--installed", inst, "--plugin-json", self.pj)[0], 0)
        lk = json.loads((spec / "workflow.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lk["core"]["plugin"], "specflow@other")

    def test_ambiguous_or_unmatched_core_is_exit_2(self):
        _proj, spec = self.make_product()
        (spec / "workflow.lock.json").unlink()
        none_match = self._two_marketplaces(self.tmp / "x")          # neither installPath is this plugin
        rc, _o, err = self.run_cli("lock-write", "--product", spec, "--installed", none_match, "--plugin-json", self.pj)
        self.assertEqual(rc, 2)
        self.assertIn("installPath", err)
        data = json.loads(none_match.read_text(encoding="utf-8"))
        for k in ("specflow@mkt", "specflow@other"):
            data["plugins"][k][0]["installPath"] = str(wc.PLUGIN_ROOT)
        both = self.write("plugins/both.json", data)
        self.assertEqual(self.run_cli("lock-write", "--product", spec, "--installed", both, "--plugin-json", self.pj)[0], 2)
        self.assertFalse((spec / "workflow.lock.json").exists())

    def test_default_plugin_json_is_this_plugins(self):
        pj = json.loads((SCRIPTS.parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(pj["name"], "specflow")


class TestDoctor(Tmp):
    def fake_which(self, present):
        return lambda name: f"/bin/{name}" if name in present else None

    @staticmethod
    def ok_probe(argv, timeout):
        return 0, "tool 1.0\n"

    def test_missing_tool_is_reported_measured_missing(self):
        rep = wc.doctor(policy(), {}, which=self.fake_which({"git", "python"}), probe=self.ok_probe)
        by = {t.name: t for t in rep.tools}
        self.assertFalse(by["runner"].found)
        self.assertTrue(by["git"].found)
        self.assertEqual(by["git"].version, "tool 1.0")

    def test_declared_vs_measured_capability(self):
        rep = wc.doctor(policy(), {}, which=self.fake_which({"git", "python"}), probe=self.ok_probe)
        caps = {c.name: c for c in rep.capabilities}
        self.assertEqual((caps["spec_work"].declared, caps["spec_work"].measured), (True, "ok"))
        self.assertEqual(caps["build"].measured, "missing")
        self.assertIn("runner", caps["build"].missing_tools)
        self.assertEqual(caps["rig"].measured, "unmeasured")   # declared only: never counted as available

    def test_exit_5_only_for_requested_capabilities(self):
        def code(cap):
            return wc.doctor(policy(), {}, which=self.fake_which({"git", "python"}), probe=self.ok_probe).exit_code([cap])
        self.assertEqual((code("spec_work"), code("build"), code("rig")), (0, 5, 5))
        self.assertEqual(wc.doctor(policy(), {}, which=self.fake_which({"git"}), probe=self.ok_probe).exit_code(["nope"]), 2)

    def test_capability_needing_repo_requires_mapping(self):
        pol = policy()
        w = self.fake_which(set(pol["tools"]))
        caps = {c.name: c for c in wc.doctor(pol, {}, which=w, probe=self.ok_probe).capabilities}
        self.assertEqual(caps["build"].measured, "missing")
        self.assertIn("app", caps["build"].missing_repos)
        d = self.tmp / "app repo"
        git_init(d)
        rep2 = wc.doctor(pol, {"app": d}, which=w, probe=self.ok_probe)
        self.assertEqual({c.name: c for c in rep2.capabilities}["build"].measured, "ok")

    def test_probe_timeout_is_exit_6(self):
        def slow(argv, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)
        rep = wc.doctor(policy(), {}, which=self.fake_which({"git", "python"}), probe=slow)
        self.assertTrue(rep.timed_out)
        self.assertEqual(rep.exit_code(["spec_work"]), 6)

    def test_nonzero_version_probe_is_broken_not_ok(self):
        rep = wc.doctor(policy(), {}, which=self.fake_which({"git", "python"}), probe=lambda argv, timeout: (1, "boom"))
        by = {t.name: t for t in rep.tools}
        self.assertTrue(by["git"].broken)
        self.assertIsNone(by["git"].version)
        caps = {c.name: c for c in rep.capabilities}
        self.assertEqual(caps["spec_work"].measured, "missing")
        self.assertIn("git", caps["spec_work"].missing_tools)
        self.assertEqual(rep.exit_code(["spec_work"]), 5)

    def test_default_probe_runs_a_real_tool(self):
        rc, out = wc.default_probe([sys.executable, "--version"], 20)
        self.assertEqual(rc, 0)
        self.assertIn("Python", out)

    def test_json_output_separates_declared_and_measured(self):
        js = wc.doctor(policy(), {}, which=self.fake_which({"git", "python"}), probe=self.ok_probe).to_dict()
        c = next(x for x in js["capabilities"] if x["name"] == "build")
        self.assertIn("declared", c)
        self.assertIn("measured", c)
        self.assertEqual(js["warnings"], [])

    def test_doctor_warns_when_local_file_is_not_git_ignored(self):
        proj, spec = self.make_product()
        rc, out, _ = self.run_cli("doctor", "--product", spec, "--json", "--for", "spec_work")
        warn = json.loads(out)["warnings"]
        self.assertEqual(len(warn), 1)
        self.assertIn("specflow.local.json", warn[0])
        (proj / ".gitignore").write_text("specflow.local.json\n", encoding="utf-8")
        _rc, out, _ = self.run_cli("doctor", "--product", spec, "--json", "--for", "spec_work")
        self.assertEqual(json.loads(out)["warnings"], [])
        _rc, text, _ = self.run_cli("doctor", "--product", spec, "--for", "spec_work")
        self.assertNotIn("warning", text)

    def test_doctor_does_not_warn_outside_git(self):
        _proj, spec = self.make_product(git=False)
        self.assertIsNone(wc.local_file_ignored(wc.home_root(spec)))
        _rc, out, _ = self.run_cli("doctor", "--product", spec, "--json", "--for", "spec_work")
        self.assertEqual(json.loads(out)["warnings"], [])

    def test_home_repo_defaults_to_the_home_root(self):
        proj, spec = self.make_product()
        local = wc.mapping_with_home(policy(), {}, spec)
        self.assertEqual(local["docs"], proj.resolve())
        rep = wc.doctor(policy(), local, which=self.fake_which({"git"}), probe=self.ok_probe)
        self.assertEqual({c.name: c for c in rep.capabilities}["docs_work"].measured, "ok")
        explicit = wc.mapping_with_home(policy(), {"docs": self.tmp / "elsewhere"}, spec)
        self.assertEqual(explicit["docs"], self.tmp / "elsewhere")


class TestDispatchGate(Tmp):
    def map_app(self, proj):
        d = self.tmp / "app repo"
        git_init(d)
        (proj / "specflow.local.json").write_text(json.dumps({"repos": {"app": {"path": d.as_posix()}}}), encoding="utf-8")

    @staticmethod
    def all_tools(name):
        return f"/bin/{name}"

    @staticmethod
    def ok_probe(argv, timeout):
        return 0, "v1"

    def test_pass(self):
        proj, spec = self.make_product()
        self.map_app(proj)
        inst = self.write("inst.json", installed_from(lock()))
        code, msgs = wc.dispatch_gate(spec, inst, which=self.all_tools, probe=self.ok_probe)
        self.assertEqual(code, 0, msgs)

    def test_lock_mismatch_blocks_with_3(self):
        proj, spec = self.make_product()
        self.map_app(proj)
        inst = self.write("inst.json", installed_from(lock(), **{"specflow@mkt": {"version": "0.0.2"}}))
        code, msgs = wc.dispatch_gate(spec, inst, which=self.all_tools, probe=self.ok_probe)
        self.assertEqual(code, 3)
        self.assertTrue(any("specflow@mkt" in m for m in msgs))

    def test_product_core_version_must_match_the_lock(self):
        proj, spec = self.make_product(core="0.0.9")
        self.map_app(proj)
        inst = self.write("inst.json", installed_from(lock()))
        code, msgs = wc.dispatch_gate(spec, inst, which=self.all_tools, probe=self.ok_probe)
        self.assertEqual(code, 3)
        self.assertTrue(any("core" in m for m in msgs))

    def test_requires_lock_false_skips_lock(self):
        pol = policy()
        pol["dispatch"]["requires_lock"] = False
        proj, spec = self.make_product(pol=pol)
        self.map_app(proj)
        inst = self.write("inst.json", installed_from(lock(), **{"specflow@mkt": {"version": "0.0.2"}}))
        self.assertEqual(wc.dispatch_gate(spec, inst, which=self.all_tools, probe=self.ok_probe)[0], 0)

    def test_missing_required_capability_blocks_with_5(self):
        _proj, spec = self.make_product()  # app is not mapped
        inst = self.write("inst.json", installed_from(lock()))
        code, msgs = wc.dispatch_gate(spec, inst, which=self.all_tools, probe=self.ok_probe)
        self.assertEqual(code, 5)
        self.assertTrue(any("build" in m for m in msgs))

    def test_cli_dispatch_check(self):
        proj, spec = self.make_product()
        self.map_app(proj)
        bad = self.write("bad.json", installed_from(lock(), **{"specflow@mkt": {"version": "0.0.2"}}))
        self.assertEqual(self.run_cli("dispatch-check", "--product", spec, "--installed", bad)[0], 3)


class TestRegen(Tmp):
    def test_render_is_stable_and_lists_every_repo(self):
        text = wc.render_local_example(policy())
        self.assertEqual(text, wc.render_local_example(policy()))
        ex = json.loads(text)
        self.assertEqual(set(ex["repos"]), set(policy()["repos"]))
        self.assertIn("specflow.local.json", ex["_comment"])

    def test_regen_check_detects_drift_and_regen_fixes(self):
        proj, spec = self.make_product()
        target = proj / "specflow.local.example.json"
        self.assertEqual(self.run_cli("regen", "--check", "--product", spec)[0], 3)   # absent
        target.write_text("{}\n", encoding="utf-8")
        self.assertEqual(self.run_cli("regen", "--check", "--product", spec)[0], 3)   # different
        self.assertEqual(self.run_cli("regen", "--product", spec)[0], 0)
        self.assertEqual(self.run_cli("regen", "--check", "--product", spec)[0], 0)
        before = target.read_bytes()
        self.run_cli("regen", "--product", spec)
        self.assertEqual(before, target.read_bytes())
        self.assertEqual(target.read_text(encoding="utf-8"), wc.render_local_example(policy()))

    def test_example_has_no_personal_absolute_path(self):
        text = wc.render_local_example(policy())
        self.assertNotRegex(text, r"[A-Za-z]:[\\/]|/home/|/Users/")


class TestEffectiveSettings(Tmp):
    def test_lists_sources_and_session_start(self):
        home, root = self.tmp / "home", self.tmp / "root"
        product = root / "spec"
        self.write("home/.claude/settings.json", {"permissions": {"allow": ["Bash(secret:*)"]}})
        self.write("root/spec/.claude/settings.local.json",
                   {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "python x/session_start.py"}]}]}})
        src = wc.effective_settings(root, home, product)
        self.assertTrue(any(s["exists"] for s in src))
        local = next(s for s in src if s["scope"] == "product-local")
        self.assertTrue(local["path"].endswith("spec/.claude/settings.local.json"))
        self.assertEqual(local["session_start"], ["python x/session_start.py"])
        self.assertNotIn("secret", json.dumps(src))  # permissions are not leaked
        self.assertEqual({s["scope"] for s in wc.effective_settings(root, home)}, {"user", "project", "project-local"})

    def test_cli_settings_without_entry(self):
        pol = policy()
        del pol["entry"]
        _proj, spec = self.make_product(pol=pol)
        rc, out, err = self.run_cli("settings", "--product", spec)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("fixed entry", out)
        pol["entry"] = {}
        (spec / "workflow.json").write_text(json.dumps(pol), encoding="utf-8")
        self.assertEqual(self.run_cli("settings", "--product", spec)[0], 0)

    def test_cli_settings_prints_entry(self):
        proj, spec = self.make_product()
        rc, out, _ = self.run_cli("settings", "--product", spec)
        self.assertEqual(rc, 0)
        self.assertIn("fixed entry", out)


if __name__ == "__main__":
    unittest.main()
