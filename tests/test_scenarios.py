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

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
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
        self.env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(self.data)}
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
        f.write_text(text, encoding="utf-8", newline="\n")

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

    def cli(self, *a: str) -> dict:
        r = subprocess.run([sys.executable, GUARDIAN, *a, "--project", str(self.dir)], capture_output=True,
                           text=True, env=self.env, check=True)
        return json.loads(r.stdout)


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


if __name__ == "__main__":
    unittest.main()
