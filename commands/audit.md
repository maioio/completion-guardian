---
description: Run or inspect the Project Completeness Audit (milestone, final, or status)
argument-hint: milestone | final | status
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, Skill, Agent
---

Mode requested: "$ARGUMENTS" (default `milestone` when empty).

- `status`: run `python "${CLAUDE_PLUGIN_ROOT}/skills/project-completeness-audit/scripts/guardian.py" status` and summarize: current fingerprint, whether it was audited, what changed since the last audit, config in effect. Do not audit.
- `milestone` or `final`: invoke the `project-completeness-audit` skill in that mode for the current project, follow its procedure and finish by running `python "${CLAUDE_PLUGIN_ROOT}/skills/project-completeness-audit/scripts/guardian.py" mark --mode <mode>` after fixes.

Never merge, push, publish, deploy or delete as part of an audit; report those as follow-ups.
