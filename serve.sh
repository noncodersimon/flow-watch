#!/usr/bin/env bash
# Local preview. There is no build step - the site is the repo root, so
# serving it statically is all that is needed. Data comes from /data, the
# same JSON the deployed GitHub Pages site reads.
set -euo pipefail

PORT="${1:-8000}"
cd "$(dirname "$0")"

echo "flow-watch preview: http://localhost:${PORT}/"
echo "Ctrl-C to stop."
exec python3 -m http.server "$PORT"
