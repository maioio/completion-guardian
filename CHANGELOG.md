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
