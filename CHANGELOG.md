# Changelog

All notable changes are recorded here. Format: Keep a Changelog. Versioning: Semantic Versioning (see CONTRIBUTING.md).

## [0.1.0] - Unreleased

Initial pre-release. Not stable: hook heuristics and the state format may change before 1.0.

### Added
- `project-completeness-audit` skill with MILESTONE and FINAL modes and six reference files.
- Stop hook that requests an audit once per meaningful, unaudited project state.
- SessionStart hook that records a per-session baseline.
- `fresh-eyes-reviewer` agent for an independent, inventory-free review.
- `/project-completion-guardian:audit [milestone|final|status]` command.
- Deterministic fingerprint and anti-loop state stored outside the repository.
- Optional per-project settings in `.claude/project-completion-guardian.local.md`.
- Read-only `probe.py` discovery script (commands in source vs docs, broken links, version drift, Git state).
- 19 scenario tests and a CI workflow.

### Fixed (found by a live end-to-end run)
- `mark`/`status` run from the Bash tool wrote to a different state directory than the hooks (Bash has no `CLAUDE_PLUGIN_DATA`), so the hook never saw the mark. Hooks now leave a `data-dir.txt` pointer in the fallback directory and the CLI follows it.
- Repeated `mark` of the same state no longer grows the audit list (idempotent per fingerprint and mode).
- A state file with the wrong shape made `status`/`mark` crash; it is now moved to `*.bad` and treated as empty (fail safe: re-audit).
- `status` on a never-audited repo reported the whole project as new; it now reports `change_baseline` (`last_audit`, `session` or `none`) and no phantom churn.
- `mark` prints `state_file`.
