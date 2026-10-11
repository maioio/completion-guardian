# FINAL algorithm

Everything in MILESTONE, over the whole project, plus:

1. **Inventory**: build a capability table (name, status, source, tests, runtime check, docs location).
2. **Clean build/install**: fresh temp checkout or venv; install the built artifact (wheel, npm pack, binary); run the smoke command. Never publish.
3. **Tests/CI**: run the full suite. Read the latest CI result (`gh run list` if available). If `final_requires_clean_ci` is true, a red or missing CI is a blocker.
4. **Interfaces**: run every public CLI command's `--help`; exercise API/MCP interfaces; compare to docs.
5. **Public surface** (when `check_public_surface`): README, package metadata, docs site, repo description/topics, release notes, examples agree with each other and with RUNTIME.
6. **Deployment** (when `check_deployment`): config present and documented; report the live state read-only. Never deploy.
7. **Git reconciliation, report only**: unmerged branches, stale worktrees, stashes, unpushed commits, work stranded outside the default branch. Never merge, delete or rewrite.
8. **Historical isolation**: old reports and changelogs are labelled HISTORICAL and not linked as current.
9. Report with a verdict per layer; then `guardian.py mark --mode final`.
