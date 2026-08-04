#!/usr/bin/env python3
from __future__ import annotations

import sys

print(
    "REFUSING: this U0i builder assumed that wait_root_partition directly "
    "substituted find_root_partition. The exact generated initramfs disproved "
    "that assumption. Use scripts/make-u0i-python-direct-root-v2.py.",
    file=sys.stderr,
)
raise SystemExit(2)
