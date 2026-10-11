# Security Policy

## Supported versions
Only the latest tagged release receives fixes. 0.x releases are pre-stable.

## Reporting
Use GitHub private vulnerability reporting (Security tab, "Report a vulnerability"). Do not open a public issue for a vulnerability. Expect an acknowledgement within a week.

## Design guarantees you can test
- The hook reads Git state and project files, and writes one JSON state file outside the repository.
- It makes no network calls, and never pushes, merges, publishes or deploys.
- It does not store the contents of secret-like files (covered by a test).
- Any internal error exits 0 with no output, so the hook cannot block a session by crashing.
- `GUARDIAN_DISABLE=1` turns the hook off.
