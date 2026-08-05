#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "validate-a33-installed-rootfs-read-only.py"
EXPECTED_BASE_BLOB = "d3c15477af1bb53e0890637f16eafc865a2d0368"
EXACT_USERDATA = "/dev/block/sda36"

spec = importlib.util.spec_from_file_location("a33_installed_rootfs_readonly_v2_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load installed-rootfs validator: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


def configure_exact_userdata() -> None:
    if base.common.EXPECTED_USERDATA != EXACT_USERDATA:
        raise base.ValidationError(
            "installed-rootfs validator expected userdata node changed: "
            f"actual={base.common.EXPECTED_USERDATA!r} expected={EXACT_USERDATA!r}"
        )
    base.common.USERDATA = EXACT_USERDATA


def main() -> int:
    repo = Path.home() / "Linuxa33"
    actual = base.git_blob(repo, BASE)
    if actual != EXPECTED_BASE_BLOB:
        raise base.ValidationError(
            "checked-in installed-rootfs validator changed: "
            f"actual={actual!r} expected={EXPECTED_BASE_BLOB!r}"
        )
    configure_exact_userdata()
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        base.ValidationError,
        base.cleanup.CleanupV2Error,
        base.common.Refusal,
        OSError,
        ValueError,
    ) as exc:
        print(
            f"A33 INSTALLED ROOTFS READ-ONLY VALIDATION V2 FAILED: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
