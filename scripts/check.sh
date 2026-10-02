#!/usr/bin/env bash
# One-command local check: tests + bug/security lint + static security scan + dependency audit.
# Usage: scripts/check.sh   (PY defaults to the project venv)
set -uo pipefail
PY=${PY:-/tmp/claude-0/venv/bin/python}
cd "$(dirname "$0")/.."
rc=0
echo "== pytest";     $PY -m pytest tests -q 2>&1 | tail -1 || rc=1
echo "== ruff (bugs/security, pgw/)"; $PY -m ruff check pgw demo || rc=1
echo "== bandit (pgw/, medium+)";      $PY -m bandit -r pgw -q -ll || rc=1
echo "== pip-audit (installed env)";   $PY -m pip_audit -l --progress-spinner off 2>&1 | grep -v "^$" | tail -15 || true
exit $rc
