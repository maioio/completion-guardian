# Contributing

## Develop and test
```
python -m unittest discover -s tests -v      # scenario tests, stdlib only, needs git
python tests/validate_structure.py           # dependency-free structure checks
claude plugin validate . --strict            # official validation (needs Claude Code)
```
Python 3.8+ and git are required. There are no runtime dependencies; please keep it that way.

## Ground rules
- A new heuristic needs a scenario test that fails without it (see `tests/test_scenarios.py`).
- The hook must stay silent and exit 0 on any error. Never add network access.
- Keep SKILL.md lean; put detail in `skills/project-completeness-audit/references/`.
- Do not vendor third-party code. External auditors stay optional and read-only.

## Versioning
Semantic Versioning. While below 1.0: minor = new behaviour or breaking state/config change, patch = fixes. Update `plugin.json`, `marketplace.json` and the changelog together.

## Suggested GitHub topics
claude-code, claude-code-plugin, hooks, documentation-drift, project-audit, code-review
