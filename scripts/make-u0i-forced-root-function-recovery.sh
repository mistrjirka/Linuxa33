#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
REFUSING: this U0i builder is superseded and intentionally disabled.

The exact generated postmarketOS initramfs executes normal hooks in child
shells. A hook therefore cannot redefine find_root_partition() in PID 1's
shell, and the failed builder correctly produced no recovery image.

Use instead:
  scripts/make-u0i-direct-root-function-recovery.sh

That builder patches only find_root_partition() inside a copied U0h initramfs,
proves that wait_root_partition() consumes its stdout, preserves the caller
function byte-for-byte, and verifies the entire repacked tree and hard-link
topology before building recovery.
EOF
exit 2
