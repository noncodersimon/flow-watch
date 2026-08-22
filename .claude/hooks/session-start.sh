#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# flow-watch has no build step and the tests are stdlib-only, so the only
# thing a fresh container is missing is yfinance. Nothing in the test suite
# needs it, but adapters/etf_flows.py cannot be imported or run without it,
# so installing it here means the adapter is debuggable straight away.
set -euo pipefail

# Local machines already have their own environment - only fix up remote.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

echo "flow-watch: installing adapter dependencies"
# --upgrade keeps this idempotent and takes advantage of the cached
# container state on subsequent sessions.
python3 -m pip install --quiet --upgrade --requirement requirements.txt

echo "flow-watch: ready - run ./check.sh for syntax and tests"
