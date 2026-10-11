# Evidence model

A claim is only as good as the strongest layer that confirms it. Record which layers you used.

| Layer | Proof | Typical commands |
|---|---|---|
| SOURCE | The code defines it | read the entry point, grep the symbol |
| TESTS | A test exercises it and passes | run the project's test command |
| RUNTIME | The real interface behaves as claimed | `<tool> --help`, a smoke call, curl the endpoint, start the MCP server and list tools |
| GIT | It is committed, on the intended branch, not stranded elsewhere | `git status`, `git log`, `git branch --no-merged`, `git worktree list` |
| PACKAGE/BUILD | A clean build or install contains it | build in a fresh temp checkout/venv, install the artifact, run it |
| DOCS | README, docs site, examples, CHANGELOG state it correctly | compare against RUNTIME output |
| PUBLIC SURFACE | Everything a stranger sees agrees: README, package page, site, repo description/topics, release notes | diff the claims against each other |

Rules: documentation never proves behaviour; a passing test suite never proves the docs; a feature in a stranded branch is not shipped. Discover the commands from the project itself (Makefile, CI workflow, package scripts); do not assume them.
