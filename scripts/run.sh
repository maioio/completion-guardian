#!/usr/bin/env bash
# Launcher: finds a working Python 3 and runs guardian.py. Never fails a hook.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for py in python3 python py; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info<(3,8))' >/dev/null 2>&1; then
    exec "$py" "$here/../skills/project-completeness-audit/scripts/guardian.py" "$@"
  fi
done
echo "project-completion-guardian: no Python 3.8+ found; guard skipped" >&2
exit 0
