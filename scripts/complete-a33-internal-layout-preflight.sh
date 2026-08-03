#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
REFUSING: the cache+userdata layout preflight is obsolete.

The exact U0g recovery uses the unified initramfs path: /init_2nd.sh is embedded
and runs before the optional initramfs-extra fallback. A pmOS_boot filesystem on
cache is not required for the first real-rootfs test.

The already completed private userdata preflight remains valid for the exact
prepared root image. Run the U0g unified-root verifier next:

  bash scripts/verify-a33-u0g-unified-root-handoff.sh

Cache must remain untouched. After the verifier passes, use the gated
userdata-only deployment script.
EOF

exit 2
