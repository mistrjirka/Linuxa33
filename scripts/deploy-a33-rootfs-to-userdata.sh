#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
REFUSING: userdata-only deployment is obsolete and cannot boot the proven U0g initramfs.

U0g mounts a filesystem labeled pmOS_boot and extracts initramfs-extra before it
searches for pmOS_root. The safe internal layout therefore requires both:

  cache    -> pmOS_boot
  userdata -> pmOS_root

Use the current cache+userdata preparation and preflight scripts. Do not write
userdata by itself.
EOF

exit 2
