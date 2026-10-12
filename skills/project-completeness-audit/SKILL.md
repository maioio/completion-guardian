---
name: project-completeness-audit
description: This skill should be used when the Project Completion Guardian hook asks for a "Project Completeness Audit", when the user asks to "audit project completeness", "check for missing docs", "verify the project is complete", "reconcile docs with code", "final project check", or "milestone audit", and before declaring a feature, release or whole project finished.
---

# Project Completeness Audit

Find what a finished-looking project forgot: missing OR stale/false information across code, tests, runtime behaviour, packaging, docs and public surface. Evidence beats memory: confirm every claim by inspecting or running the real thing.

## Modes

- **MILESTONE** - audit the changed surfaces and what depends on them. Use after a feature, fix or refactor.
- **FINAL** - reconcile the whole project. Use when the project, release or PR is being finalized, or when the hook says FINAL.

## Procedure

1. **Discover first, never assume the stack.** Run `python "${CLAUDE_PLUGIN_ROOT}/scripts/probe.py" <project>`. It reports stack, entry points, commands found in source vs. docs, broken links, version mismatches, Git branch/worktree state. Treat it as a lead list, not a verdict.
2. **Scope.** MILESTONE: `git diff` plus files that reference the changed names. FINAL: everything.
3. **Check each evidence layer** (see `references/evidence-model.md`): SOURCE, TESTS, RUNTIME, GIT, PACKAGE/BUILD, DOCS, PUBLIC SURFACE. Run real CLI/API/MCP interfaces (`--help`, a smoke call) instead of trusting text.
4. **Classify every capability** with exactly one status from `references/status-vocabulary.md`. Anything advertised publicly but not ready is `NOT_READY` and a finding.
5. **Look both ways**: missing information (shipped, undocumented) AND false information (documented, not shipped or changed).
6. **FINAL only** (see `references/final-audit.md`): clean-checkout build/install, tests, CI state, deployment, Git reconciliation (report only), public surface.
7. **Fix in-scope drift** (docs, examples, tests, metadata). Report out-of-scope or risky items instead of acting.
8. **Report** using `references/report-template.md`: findings with evidence, fixed, open, verdict.
9. **Mark audited** (required, ends the hook loop):
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/guardian.py" mark --mode milestone|final`
   Run it AFTER fixes, so the fingerprint covers the final state.

## Requirements and limits

- Steps 1 and 9 run `probe.py` and `guardian.py`, which ship with the `project-completion-guardian` plugin (`${CLAUDE_PLUGIN_ROOT}`). Without the plugin, do the discovery by hand and skip marking.
- `mark` needs Bash permission. State is only fingerprint plus milestone/final: no per-item completed, ignored or deferred state.
- A moved or renamed repo is re-audited, not recognized. `status` needs the `--project <path>` form.

## Hard safety rules

Never: merge PRs, push protected branches, publish releases or packages, deploy to production, delete major historical code, rewrite Git history, change external services, or print secrets. Report these as follow-ups for the user. Git reconciliation is read-only.

## Optional external tools

Repomix, DeepWiki-style summaries and similar may help map a large repo. Inspect the tool first (source, license, what it sends where); never install blindly; run read-only; treat their output as evidence to verify, never as truth. See `references/external-tools.md`.

## Independent review

For high-stakes or FINAL audits, delegate to the `fresh-eyes-reviewer` agent: it inspects the project without your capability inventory, then compare its findings with yours.

## References

- `references/evidence-model.md` - the seven layers and what counts as proof
- `references/status-vocabulary.md` - status definitions
- `references/milestone-audit.md` / `references/final-audit.md` - mode algorithms
- `references/report-template.md` - output format
- `references/external-tools.md` - vetting rules
