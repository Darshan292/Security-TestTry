#!/usr/bin/env bash
# One-command local check: tests + bug/security lint + static security scan + dependency audit.
# Usage: scripts/check.sh   (PY defaults to the project venv)
set -uo pipefail
cd "$(dirname "$0")/.."
if [ -n "${PY:-}" ]; then CHECK_PY="$PY"
elif [ -x .venv/bin/python ]; then CHECK_PY=".venv/bin/python"
else CHECK_PY="$(command -v python3 || command -v python || true)"; fi
if [ -z "$CHECK_PY" ] || ! "$CHECK_PY" -c 'import sys; print(sys.executable)' >/dev/null 2>&1; then
  echo "ERROR: Python interpreter not found; set PY=/path/to/python or create .venv" >&2; exit 2
fi
rc=0
run_check() { name="$1"; shift; echo "== $name"; if ! "$@"; then echo "FAILED: $name" >&2; rc=1; fi; }
run_check "pytest" "$CHECK_PY" -m pytest tests -q
run_check "ruff (bugs/security, pgw/)" "$CHECK_PY" -m ruff check pgw demo
run_check "bandit (pgw/, medium+)" "$CHECK_PY" -m bandit -r pgw -q -ll
for lock in runtime dev ner; do
  run_check "pip-audit (locked requirements/$lock.txt)" "$CHECK_PY" -m pip_audit --require-hashes --disable-pip -r "requirements/$lock.txt"
done
exit "$rc"
