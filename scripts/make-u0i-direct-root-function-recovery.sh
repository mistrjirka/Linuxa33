#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
REFUSING: this Bash U0i builder is superseded.

It repeatedly failed on assumptions about the generated postmarketOS shell
implementation. Use the byte-preserving Python builder instead:

  python3 scripts/make-u0i-python-direct-root.py

No artifact was built and no phone partition was written.
EOF
exit 2
