#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
REFUSING: this U0i builder is superseded and intentionally disabled.

The exact generated postmarketOS initramfs did not satisfy this script's
assumption that find_root_partition() directly parses pmos_root= from
/proc/cmdline. No recovery image was produced by the failed attempt.

Use instead:
  scripts/make-u0i-direct-root-function-recovery.sh

That builder inspects the exact embedded functions, proves how
wait_root_partition() consumes find_root_partition() output, and patches only
the copied initramfs function while leaving the kernel command line unchanged.
EOF
exit 2
