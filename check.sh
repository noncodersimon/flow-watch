#!/usr/bin/env bash
# Pre-commit check for flow-watch. Mirrors what CLAUDE.md asks for:
# syntax-check the adapters and the front end, then run the test suite.
# No network, no pip install - safe to run anywhere.
set -uo pipefail

cd "$(dirname "$0")"
status=0

step() {
  local label="$1"; shift
  printf '\n== %s ==\n' "$label"
  if "$@"; then
    printf '   ok\n'
  else
    printf '   FAILED\n'
    status=1
  fi
}

step "python syntax (adapters)" python3 -m py_compile adapters/*.py

if command -v node >/dev/null 2>&1; then
  step "javascript syntax (app.js)" node --check app.js
else
  printf '\n== javascript syntax (app.js) ==\n   skipped - node not installed\n'
fi

step "tests" python3 -m unittest discover -s tests -t tests -q

printf '\n'
if [ "$status" -eq 0 ]; then
  echo "All checks passed."
else
  echo "Checks FAILED - do not commit."
fi
exit "$status"
