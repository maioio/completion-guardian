"""Dependency-free structure checks that mirror what the Claude Code validator enforces."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = {"SessionStart", "Stop", "PreToolUse", "PostToolUse", "UserPromptSubmit", "SubagentStop",
          "PreCompact", "SessionEnd", "Notification"}
errors = []


def need(cond, msg):
    if not cond:
        errors.append(msg)


plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
need(re.fullmatch(r"[a-z0-9-]+", plugin.get("name", "")), "plugin name must be kebab-case")
need(re.fullmatch(r"\d+\.\d+\.\d+", plugin.get("version", "")), "plugin version must be semver")

market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
need(market["plugins"][0]["version"] == plugin["version"], "marketplace and plugin versions differ")

hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
for event, groups in hooks.items():
    need(event in EVENTS, f"unknown event {event}")
    for g in groups:
        for h in g["hooks"]:
            need(h["type"] == "command" and "${CLAUDE_PLUGIN_ROOT}" in h["command"],
                 f"{event}: command must use ${{CLAUDE_PLUGIN_ROOT}}")
            need(isinstance(h.get("timeout"), int), f"{event}: timeout missing")
            rel = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\s\"]+)", h["command"])
            need(rel and (ROOT / rel.group(1)).is_file(), f"{event}: script not found")

docs = (list((ROOT / "agents").glob("*.md")) + list((ROOT / "skills").glob("*/SKILL.md"))
        + list((ROOT / "commands").glob("*.md")))
for md in docs:
    text = md.read_text(encoding="utf-8").replace("\r\n", "\n")
    need(text.startswith("---\n") and "\n---\n" in text, f"{md.name}: frontmatter missing")
    need("description:" in text.split("\n---\n")[0], f"{md.name}: description missing")

skill = (ROOT / "skills/project-completeness-audit/SKILL.md").read_text(encoding="utf-8")
need(len(skill.splitlines()) < 200, "SKILL.md should stay lean")
for ref in re.findall(r"references/([\w.-]+\.md)", skill):
    need((ROOT / "skills/project-completeness-audit/references" / ref).is_file(), f"missing reference {ref}")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print("structure OK")
