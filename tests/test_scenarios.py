"""Scenario tests A-G for the Stop-hook decision logic and the probe.

Each test builds a throwaway Git repo, drives the real hook entry points with
real JSON on stdin, and asserts on stdout. Standard library only:

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "project-completeness-audit" / "scripts"
GUARDIAN = str(SCRIPTS / "guardian.py")
PROBE = str(SCRIPTS / "probe.py")

CLI_V1 = '''import argparse


def main():
    p = argparse.ArgumentParser(prog="tool")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("init")
    sub.add_parser("run")
    args = p.parse_args()
    return 0
'''
CLI_V2 = CLI_V1.replace('    sub.add_parser("run")\n', '    sub.add_parser("run")\n    sub.add_parser("export")\n')
README_V1 = "# tool\n\nUse `tool init` and `tool run`.\n"
PYPROJECT = '[project]\nname = "tool"\nversion = "0.1.0"\n\n[project.scripts]\ntool = "tool.cli:main"\n'


class Fixture:
    def __init__(self, case: unittest.TestCase):
        self.dir = Path(tempfile.mkdtemp(prefix="guardian-fx-"))
        self.data = Path(tempfile.mkdtemp(prefix="guardian-data-"))
        case.addCleanup(shutil.rmtree, self.dir, True)
        case.addCleanup(shutil.rmtree, self.data, True)
        self.cfg = Path(tempfile.mkdtemp(prefix="guardian-cfg-"))
        case.addCleanup(shutil.rmtree, self.cfg, True)
        # hooks get CLAUDE_PLUGIN_DATA; the model's Bash tool does not (self.bash_env), only CLAUDE_CONFIG_DIR
        self.env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(self.data), "CLAUDE_CONFIG_DIR": str(self.cfg)}
        self.bash_env = {k: v for k, v in self.env.items() if k != "CLAUDE_PLUGIN_DATA"}
        self.env.pop("GUARDIAN_DISABLE", None)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.write("README.md", README_V1)
        self.write("pyproject.toml", PYPROJECT)
        self.write("tool/__init__.py", "")
        self.write("tool/cli.py", CLI_V1)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")

    def git(self, *a: str) -> str:
        return subprocess.run(["git", *a], cwd=self.dir, capture_output=True, text=True, check=True).stdout

    def write(self, rel: str, text: str) -> None:
        f = self.dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def hook(self, event: str, session: str = "s1", transcript: str | None = None) -> str:
        payload = {"cwd": str(self.dir), "session_id": session, "hook_event_name": event}
        if transcript:
            payload["transcript_path"] = transcript
        r = subprocess.run([sys.executable, GUARDIAN, event], input=json.dumps(payload), cwd=self.dir,
                           capture_output=True, text=True, env=self.env, check=False)
        self.last = r
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def start(self, session: str = "s1") -> None:
        self.hook("session-start", session)

    def stop(self, session: str = "s1", transcript: str | None = None) -> dict | None:
        out = self.hook("stop", session, transcript)
        return json.loads(out) if out else None

    def cli(self, *a: str, env: dict | None = None, project: str | Path | None = None) -> dict:
        r = subprocess.run([sys.executable, GUARDIAN, *a, "--project", str(project or self.dir)], capture_output=True,
                           text=True, env=env or self.env, check=True)
        return json.loads(r.stdout)

    def run_cli(self, *a: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, GUARDIAN, *a, "--project", str(self.dir)], capture_output=True,
                              text=True, env=env or self.env, check=False)

    def state_file(self) -> Path:
        return Path(self.cli("status")["state_file"])


class ScenarioTests(unittest.TestCase):
    def test_a_tiny_typo_does_not_trigger(self):
        fx = Fixture(self)
        fx.start()
        fx.write("README.md", README_V1.replace("Use", "Usee"))
        self.assertIsNone(fx.stop())

    def test_a2_whitespace_only_code_change_does_not_trigger(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V1.replace("def main():", "def main():\n\n"))
        self.assertIsNone(fx.stop())

    def test_b_new_cli_command_without_docs_triggers(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)  # 1 added line, README untouched
        res = fx.stop()
        self.assertIsNotNone(res)
        self.assertEqual(res["decision"], "block")
        self.assertIn("Project Completeness Audit", res["reason"])
        self.assertIn("project-completeness-audit", res["reason"])
        self.assertIn("MILESTONE", res["reason"])

    def test_b2_probe_reports_the_undocumented_command(self):
        fx = Fixture(self)
        fx.write("tool/cli.py", CLI_V2)
        out = json.loads(subprocess.run([sys.executable, PROBE, str(fx.dir)], capture_output=True, text=True,
                                        check=True).stdout)
        self.assertIn("export", out["commands_in_source"])
        self.assertIn("export", out["source_commands_not_mentioned_in_any_doc"]["tool"])

    def test_c_significant_api_feature_triggers(self):
        fx = Fixture(self)
        fx.start()
        body = "\n".join(f"def handler_{i}(x):\n    return x + {i}\n" for i in range(40))
        fx.write("tool/api.py", "from flask import Flask\napp = Flask(__name__)\n\n" + body)
        res = fx.stop()
        self.assertIsNotNone(res)

    def test_d_readme_claims_nonexistent_command_is_caught_by_probe(self):
        fx = Fixture(self)
        fx.write("README.md", README_V1 + "\nAlso `tool deploy` ships your app.\n")
        out = json.loads(subprocess.run([sys.executable, PROBE, str(fx.dir)], capture_output=True, text=True,
                                        check=True).stdout)
        self.assertIn("deploy", out["doc_claims_without_source"]["tool"])
        self.assertNotIn("run", out["doc_claims_without_source"]["tool"])

    def test_e_finalization_requires_final_mode(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)
        fx.write("CHANGELOG.md", "# Changelog\n\n## 0.2.0\n- add export\n")
        res = fx.stop()
        self.assertIsNotNone(res)
        self.assertIn("FINAL", res["reason"])

    def test_e2_final_words_in_transcript_escalate(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)
        tr = fx.dir.parent / (fx.dir.name + ".jsonl")
        self.addCleanup(lambda: tr.exists() and tr.unlink())
        tr.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "finalize the project"}}) + "\n",
                      encoding="utf-8")
        res = fx.stop(transcript=str(tr))
        self.assertIn("FINAL", res["reason"])

    def test_f_audited_then_stop_again_does_not_loop(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)
        self.assertIsNotNone(fx.stop())
        fx.cli("mark", "--mode", "milestone")
        self.assertIsNone(fx.stop())
        self.assertIsNone(fx.stop())  # and again
        self.assertTrue(fx.cli("status")["audited_current_state"])

    def test_f2_unmarked_state_is_prompted_only_once(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)
        self.assertIsNotNone(fx.stop())
        self.assertIsNone(fx.stop())  # never nag twice about the identical state

    def test_g_audited_then_meaningful_change_triggers_again(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)
        self.assertIsNotNone(fx.stop())
        fx.cli("mark", "--mode", "milestone")
        fx.write("tool/cli.py", CLI_V2 + '\n\ndef extra():\n    sub.add_parser("purge")\n    sub.add_parser("sync")\n')
        res = fx.stop()
        self.assertIsNotNone(res)

    def test_g2_trivial_change_after_audit_does_not_retrigger(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)
        fx.stop()
        fx.cli("mark", "--mode", "milestone")
        fx.write("README.md", README_V1.replace("Use", "Usee"))
        self.assertIsNone(fx.stop())

    def test_commit_after_audit_is_not_new_work(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)
        fx.stop()
        fx.cli("mark", "--mode", "milestone")
        fx.git("add", "-A")
        fx.git("commit", "-q", "-m", "export command")
        self.assertIsNone(fx.stop())

    def test_new_session_does_not_reprompt_audited_work(self):
        fx = Fixture(self)
        fx.start("s1")
        fx.write("tool/cli.py", CLI_V2)
        fx.stop("s1")
        fx.cli("mark", "--mode", "milestone")
        fx.start("s2")
        self.assertIsNone(fx.stop("s2"))

    def test_disable_switches(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)
        fx.env["GUARDIAN_DISABLE"] = "1"
        self.assertIsNone(fx.stop())
        del fx.env["GUARDIAN_DISABLE"]
        fx.write(".claude/project-completion-guardian.local.md", "---\nenabled: false\n---\n")
        self.assertIsNone(fx.stop())

    def test_audit_level_config(self):
        fx = Fixture(self)
        fx.write(".claude/project-completion-guardian.local.md", "---\naudit_level: relaxed\n---\n")
        self.assertEqual(fx.cli("config")["audit_level"], "relaxed")

    def test_non_git_directory_is_ignored(self):
        d = Path(tempfile.mkdtemp(prefix="guardian-nogit-"))
        self.addCleanup(shutil.rmtree, d, True)
        r = subprocess.run([sys.executable, GUARDIAN, "stop"], input=json.dumps({"cwd": str(d)}), capture_output=True,
                           text=True, check=False)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))

    def test_garbage_stdin_never_breaks_the_hook(self):
        r = subprocess.run([sys.executable, GUARDIAN, "stop"], input="not json", capture_output=True, text=True,
                           check=False)
        self.assertEqual(r.returncode, 0)

    def test_secret_files_are_never_stored(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)
        fx.write(".claude/x.local.md", "api_key: SECRETVALUE123\n")
        fx.stop()
        fx.cli("mark", "--mode", "milestone")
        for f in fx.data.rglob("*.json"):
            self.assertNotIn("SECRETVALUE123", f.read_text(encoding="utf-8"))

    # ---- lifecycle defects found by the live run (see CHANGELOG 0.1.1)

    def test_mark_from_bash_is_seen_by_the_hook(self):
        """The Bash tool has no CLAUDE_PLUGIN_DATA; its mark must land where the hook reads."""
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)
        self.assertIsNotNone(fx.stop())
        marked = fx.cli("mark", "--mode", "milestone", env=fx.bash_env)
        self.assertTrue(Path(marked["state_file"]).is_relative_to(fx.data))
        self.assertIsNone(fx.stop("s2"))  # fresh session, hook env: the mark is recognized
        self.assertTrue(fx.cli("status", env=fx.bash_env)["audited_current_state"])

    def test_bash_status_before_any_hook_uses_fallback_dir(self):
        fx = Fixture(self)
        out = fx.cli("status", env=fx.bash_env)
        self.assertTrue(Path(out["state_file"]).is_relative_to(fx.cfg))

    def test_duplicate_mark_is_idempotent(self):
        fx = Fixture(self)
        fx.write("tool/cli.py", CLI_V2)
        for _ in range(3):
            fx.cli("mark", "--mode", "milestone")
        self.assertEqual(len(json.loads(fx.state_file().read_text(encoding="utf-8"))["audited"]), 1)

    def test_milestone_mark_does_not_satisfy_final(self):
        fx = Fixture(self)
        fx.write("tool/cli.py", CLI_V2)
        fx.cli("mark", "--mode", "milestone")
        st = fx.cli("status")
        self.assertEqual((st["audited_current_state"], st["audited_final_current_state"]), (True, False))
        fx.cli("mark", "--mode", "final")
        self.assertTrue(fx.cli("status")["audited_final_current_state"])

    def test_prompted_is_not_audited(self):
        fx = Fixture(self)
        fx.start()
        fx.write("tool/cli.py", CLI_V2)
        self.assertIsNotNone(fx.stop())  # asked once, never marked
        self.assertFalse(fx.cli("status")["audited_current_state"])
        self.assertIsNone(fx.stop())  # silent, but still not "audited"
        self.assertFalse(fx.cli("status")["audited_current_state"])

    def test_wrong_shape_state_fails_safe(self):
        fx = Fixture(self)
        fx.write("tool/cli.py", CLI_V2)
        fx.cli("mark", "--mode", "milestone")
        sf = fx.state_file()
        for bad in ('{"audited": 5}', "{}", "[1]", "{{{", '{"audited":[{"x":1}],"sessions":{},"prompted":{}}'):
            sf.write_text(bad, encoding="utf-8")
            r = fx.run_cli("status")
            self.assertEqual(r.returncode, 0, (bad, r.stderr))
            self.assertFalse(json.loads(r.stdout)["audited_current_state"], bad)  # unknown => not audited
            self.assertEqual(json.loads(fx.hook("stop"))["decision"], "block", bad)  # no crash; unknown => re-audit
            self.assertTrue(sf.with_suffix(".bad").exists(), bad)  # evidence kept
            sf.with_suffix(".bad").unlink()
        self.assertEqual(fx.run_cli("mark", "--mode", "milestone").returncode, 0)
        self.assertTrue(fx.cli("status")["audited_current_state"])

    def test_interrupted_write_leaves_usable_state(self):
        fx = Fixture(self)
        fx.write("tool/cli.py", CLI_V2)
        fx.cli("mark", "--mode", "milestone")
        sf = fx.state_file()
        sf.with_suffix(".tmp").write_text('{"audited": [', encoding="utf-8")  # crash mid-write
        self.assertTrue(fx.cli("status")["audited_current_state"])
        fx.cli("mark", "--mode", "final")
        self.assertTrue(fx.cli("status")["audited_final_current_state"])

    def test_project_path_forms_share_one_state(self):
        fx = Fixture(self)
        fx.write("tool/cli.py", CLI_V2)
        fx.cli("mark", "--mode", "milestone")
        for p in (fx.dir / "tool", str(fx.dir) + os.sep, fx.dir / "tool" / ".." ):
            self.assertTrue(fx.cli("status", project=p)["audited_current_state"], str(p))

    def test_moved_repo_is_reaudited_not_trusted(self):
        fx = Fixture(self)
        fx.write("tool/cli.py", CLI_V2)
        fx.cli("mark", "--mode", "milestone")
        moved = fx.dir.parent / (fx.dir.name + "-moved")
        self.addCleanup(shutil.rmtree, moved, True)
        shutil.copytree(fx.dir, moved)
        self.assertFalse(fx.cli("status", project=moved)["audited_current_state"])

    def test_clean_project_reports_no_meaningful_change(self):
        fx = Fixture(self)
        st = fx.cli("status")
        self.assertEqual(st["change_baseline"], "none")
        self.assertFalse(st["change_since_last_audit"]["meaningful"])  # not "the whole repo is new"
        self.assertFalse(st["audited_current_state"])
        fx.start()
        self.assertIsNone(fx.stop())


if __name__ == "__main__":
    unittest.main()
