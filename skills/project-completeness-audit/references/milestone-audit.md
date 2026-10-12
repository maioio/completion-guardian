# MILESTONE algorithm

1. `git status` and `git diff` (plus `git diff <last-audited-head>..HEAD` if commits were made). List changed files.
2. Probe: `python "<skill dir>/scripts/probe.py"`. Keep only findings that touch changed names or surfaces.
3. For each changed behaviour: new/removed command, flag, endpoint, config key, env var, file format, dependency, default.
4. Per item check: tests cover it and pass; the real interface runs; README/docs/examples/CHANGELOG/help text mention it; nothing documented was removed or renamed.
5. Search the repo for stale mentions of renamed or removed names.
6. Fix docs/tests/metadata drift in scope. Report anything riskier.
7. Report, then `guardian.py mark --mode milestone`.

Cost control: stop reading once a layer is confirmed; do not re-audit unchanged areas.
