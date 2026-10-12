# completion-guardian

A Claude Code plugin that stops you from calling a project "done" while it is missing something or says something false.

> Status: **v0.1.0, pre-release, not stable.** Heuristics, thresholds and the state format may change before 1.0. It is not published in any plugin marketplace; the repository carries marketplace-ready metadata only.

Plugin name: `project-completion-guardian`. License: MIT.

## The problem

AI-assisted work ends with a green test run and a confident "done". Then the new CLI command is not in the README, the docs still describe a flag that was removed, the changelog and version disagree, or a feature branch was never reconciled. Missing **and** stale information both slip through, because nobody inventories the project from the outside.

## The solution

Three parts that work together:

1. **Skill `project-completeness-audit`**: discovers the project (never assumes a stack), builds an inventory of what it can do, and reconciles that against source, tests, runtime behaviour, Git, package/build metadata, docs and the public surface.
2. **Stop hook**: when meaningful work appears finished and the exact project state has not been audited, it asks Claude to run the skill once. Trivial edits never trigger it.
3. **Agent `fresh-eyes-reviewer`**: an independent reviewer that reads the project as a newcomer, without the primary audit's inventory.

## How it decides (hook)

On every Stop it computes a deterministic **fingerprint** (Git HEAD plus the content hashes of the changed, relevant files). Then:

| Situation | Result |
|---|---|
| Change is trivial (typo, whitespace, comments-level churn) | allow stop, silent |
| Meaningful change, fingerprint not audited | block once with an audit request |
| Same fingerprint already audited (or already requested) | allow stop, no loop |
| Changed again after the audit | request another audit |

"Meaningful" is measured by normalized churn per category (source, tests, public surface, docs), with extra weight for new commands, flags and routes, so a one-line new CLI command counts while a typo does not. Thresholds depend on `audit_level` (`relaxed`, `balanced`, `strict`).

## MILESTONE vs FINAL

- **MILESTONE**: the changed and affected surfaces only. Chosen by default.
- **FINAL**: whole-project reconciliation (all interfaces, clean build/install, tests and CI, Git branches and worktrees, deployment). Chosen when a changelog/release file changes or the conversation says the project is being finalized. A FINAL audit also satisfies a MILESTONE request for the same state.

Findings use a fixed status vocabulary: `PUBLIC_SUPPORTED`, `PUBLIC_ADVANCED`, `EXPERIMENTAL`, `OPTIONAL`, `INTERNAL`, `LEGACY`, `DEPRECATED`, `HISTORICAL`, `NOT_READY`. See `skills/project-completeness-audit/references/`.

## Anti-loop

The audited fingerprint is recorded after the audit (`guardian.py mark`). The hook then allows stopping for that state, and additionally never requests the same `fingerprint:mode` twice. Timestamps are metadata only. State lives outside the repository (`<plugin data dir>/state/<hash-of-project-path>.json`), so it is never committed.

## Install globally

User scope, no changes to any repository. From a clone of this repo:

```
claude plugin marketplace add /path/to/completion-guardian
claude plugin install project-completion-guardian@completion-guardian --scope user
```

Or load it for one session without installing: `claude --plugin-dir /path/to/completion-guardian`.

Requirements: Python 3.8+ and git on PATH. No other dependencies.

## Manual command

```
/project-completion-guardian:audit milestone
/project-completion-guardian:audit final
/project-completion-guardian:audit status
```

## Configuration (optional)

`.claude/project-completion-guardian.local.md` in a project (keep it out of Git; `*.local.md` is ignored here):

```markdown
---
enabled: true
audit_level: balanced        # relaxed | balanced | strict
final_requires_clean_ci: true
check_public_surface: true
check_deployment: true
external_auditors: false     # allow optional, inspected, read-only external tools
---
```

Temporarily disable everywhere: set `GUARDIAN_DISABLE=1` in the environment. Disable for one project: `enabled: false`.

## Works with

Any Git repository. Stack discovery covers Python, Node/TypeScript, Go, Rust and generic layouts (CLI entry points, HTTP routes, package manifests, docs, CI). Non-Git folders are ignored by the hook.

## Safety

An audit never merges, pushes, publishes, deploys, rewrites history, deletes historical code, changes external services, or prints secrets. Those are reported as follow-ups for a human. Optional external tools (for example Repomix or DeepWiki-style summaries) are only used after inspecting them, never installed blindly, run read-only, and their output is treated as leads, not evidence.

## Demo: "CLI command missing from the docs"

Fixture in `examples/cli-missing-from-docs/` (a tiny CLI with `init` and `run`, documented in its README).

1. Copy the fixture to a scratch folder and `git init && git add -A && git commit -m base`.
2. Start Claude Code there with the plugin installed.
3. Ask: "add an `export` command to the CLI".
4. Claude adds one `add_parser("export")` line and tries to finish; README is untouched.
5. The Stop hook sees a new command on the public surface and blocks once: "Meaningful project work appears complete, but the current project state has not passed the Project Completeness Audit. Run the project-completeness-audit skill in MILESTONE or FINAL mode…".
6. The audit's probe reports `export` as present in source but not in any doc; Claude documents it, adds a test, then runs `guardian.py mark --mode milestone`.
7. Claude stops. A second Stop with no further change is silent; a later meaningful change is audited again.

## Architecture

```
.claude-plugin/plugin.json, marketplace.json
hooks/hooks.json           SessionStart + Stop -> scripts/run.sh
scripts/run.sh             picks python3 / python / py, runs the skill's guardian.py
skills/project-completeness-audit/   self-contained skill (this folder is the .skill)
  SKILL.md, references/
  scripts/guardian.py      snapshot, fingerprint, significance, state, anti-loop
  scripts/probe.py         read-only discovery (source vs docs, links, versions, Git)
agents/fresh-eyes-reviewer.md
commands/audit.md
tests/                     scenario + structure tests
```

Token control: scripts do the deterministic work; the model is used only for semantic judgement; references load on demand.

## Development and testing

```
python -m unittest discover -s tests -v
python tests/validate_structure.py
claude plugin validate . --strict
```

CI runs the tests on Linux and Windows (Python 3.9 and 3.12) plus the structure checks. See CONTRIBUTING.md and SECURITY.md.

## Known limitations

- Git repositories only.
- Significance is a heuristic: a docs-only false claim with no code change will not trigger the hook (run the manual command or a FINAL audit for that).
- Churn for files committed and then edited within one session is estimated from the session baseline when available.
- The Stop hook input fields beyond the working directory are treated as optional, because they may vary between Claude Code versions.
- Marking an audit requires the model to run `guardian.py mark`, which needs Bash permission. If Bash is denied the hook asks once for that state and then stays silent (no loop), but the state stays unmarked.
- No per-item state: findings are not tracked as completed, ignored or deferred. The only state is the audited fingerprint, per mode.
- A moved or renamed repository has a different state file, so it is safely re-audited rather than recognized.
- `guardian.py status` requires the `--project <path>` form (`status .` is not supported).
- Malformed state is moved to `*.bad` and treated as never audited (fail safe).
- The `.skill` is self-contained for audits (`probe.py`, `mark`, `status`), but a skill cannot register hooks: the automatic Stop-hook trigger exists only with the plugin installed.
- v0.1.0 is unreleased and unstable.
