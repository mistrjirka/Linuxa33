#!/usr/bin/env python3
from __future__ import annotations

import sys

MESSAGE = (
    "DISABLED: this diagnostic was proven unsafe on the A33. It allowed OpenRC "
    "dependency startup to remount the test root writable, its /dev/null bind "
    "mask failed under TWRP, and cleanup could leave chroot mounts busy. Run "
    "cleanup-a33-openrc-sshd-chroot.py instead and do not reuse this diagnostic."
)


def main() -> int:
    print(MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
