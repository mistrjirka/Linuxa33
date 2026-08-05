#!/usr/bin/env python3
from __future__ import annotations

import sys

MESSAGE = (
    "DISABLED: the OpenRC SSH chroot diagnostic was proven unsafe. It could "
    "remount userdata writable through dependency startup and leave mounts or "
    "chrooted services active. Run cleanup-a33-openrc-sshd-chroot.py instead."
)


def main() -> int:
    print(MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
