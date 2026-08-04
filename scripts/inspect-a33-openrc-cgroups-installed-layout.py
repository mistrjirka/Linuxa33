#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path, PurePosixPath
import sys
import tarfile

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "inspect-a33-openrc-cgroups.py"
spec = importlib.util.spec_from_file_location("a33_openrc_cgroup_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load base inspector: {BASE_PATH}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

# postmarketOS/Alpine OpenRC 0.63 installs its implementation under
# /usr/libexec/rc/sh. Retain the legacy /lib/rc path for older images.
base.SEARCH_ROOTS = (
    PurePosixPath("/usr/libexec/rc"),
    PurePosixPath("/lib/rc"),
    PurePosixPath("/etc/init.d"),
    PurePosixPath("/etc/conf.d"),
)
base.PRESERVE_FILES = (
    PurePosixPath("/etc/os-release"),
    PurePosixPath("/etc/rc.conf"),
    PurePosixPath("/usr/libexec/rc/sh/openrc-run.sh"),
    PurePosixPath("/usr/libexec/rc/sh/rc-cgroup.sh"),
    PurePosixPath("/usr/libexec/rc/sh/rc-functions.sh"),
    PurePosixPath("/usr/libexec/rc/sh/functions.sh"),
    PurePosixPath("/lib/rc/sh/openrc-run.sh"),
    PurePosixPath("/lib/rc/sh/rc-cgroup.sh"),
    PurePosixPath("/etc/init.d/cgroups"),
)


def main() -> int:
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        base.InspectionError,
        OSError,
        UnicodeError,
        ValueError,
        tarfile.TarError,
    ) as exc:
        print(f"OPENRC INSTALLED-LAYOUT INSPECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
