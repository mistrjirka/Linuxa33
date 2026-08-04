#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
REFUSING: this self-modifying Bash U0i wrapper is superseded.

Use the Python implementation, which parses the actual gzip/newc archive and
changes only the init_functions.sh payload:

  python3 scripts/make-u0i-python-direct-root.py

No artifact was built and no phone partition was written.
EOF
exit 2
