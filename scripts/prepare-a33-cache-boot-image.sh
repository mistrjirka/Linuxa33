#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
REFUSING: the cache/pmOS_boot plan is obsolete for the exact U0g recovery.

The A33 device does not set deviceinfo_create_initfs_extra=true. The exact U0g
ramdisk embeds /init_2nd.sh and enters the second-stage root handoff before any
optional initramfs-extra fallback. Therefore the first real-rootfs test needs:

  userdata -> pmOS_root
  cache    -> untouched

Run:
  bash scripts/verify-a33-u0g-unified-root-handoff.sh

Then use the gated userdata-only deployment script after its report passes.
EOF

exit 2
