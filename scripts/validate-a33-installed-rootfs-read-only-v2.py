#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "validate-a33-installed-rootfs-read-only.py"
HELPER = HERE / "lib/a33_exact_block_node.py"
EXT4_IDENTITY = HERE / "lib/a33_ext4_identity_text.py"
EXPECTED_BASE_BLOB = "d3c15477af1bb53e0890637f16eafc865a2d0368"
EXPECTED_HELPER_BLOB = "2232f92bbf2782aed88acd9246ed063148ca63a8"
EXPECTED_EXT4_IDENTITY_BLOB = "547aa185c56cfdefe09efab2ba1fbe1e63950de0"
EXACT_USERDATA = "/dev/block/sda36"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load("a33_installed_rootfs_readonly_v2_base", BASE)
helper = load("a33_installed_rootfs_readonly_v2_exact_node", HELPER)
ext4_identity = load("a33_installed_rootfs_readonly_v2_ext4_identity", EXT4_IDENTITY)


def adb_argument(argv: list[str]) -> str:
    for index, value in enumerate(argv):
        if value == "--adb":
            if index + 1 >= len(argv):
                raise base.ValidationError("--adb requires a value")
            return argv[index + 1]
        if value.startswith("--adb="):
            candidate = value.split("=", 1)[1]
            if not candidate:
                raise base.ValidationError("--adb requires a non-empty value")
            return candidate
    return "adb"


def configure_exact_userdata() -> None:
    if base.common.EXPECTED_USERDATA != EXACT_USERDATA:
        raise base.ValidationError(
            "installed-rootfs validator expected userdata node changed: "
            f"actual={base.common.EXPECTED_USERDATA!r} expected={EXACT_USERDATA!r}"
        )
    if helper.EXACT_NODE != EXACT_USERDATA:
        raise base.ValidationError(
            "exact block-node helper target changed: "
            f"actual={helper.EXACT_NODE!r} expected={EXACT_USERDATA!r}"
        )
    base.common.USERDATA = EXACT_USERDATA
    base.common.ext4_identity = lambda adb, serial: ext4_identity.ext4_identity(
        base.common, adb, serial
    )


def main() -> int:
    repo = Path.home() / "Linuxa33"
    for path, expected in (
        (BASE, EXPECTED_BASE_BLOB),
        (HELPER, EXPECTED_HELPER_BLOB),
        (EXT4_IDENTITY, EXPECTED_EXT4_IDENTITY_BLOB),
    ):
        actual = base.git_blob(repo, path)
        if actual != expected:
            raise base.ValidationError(
                f"checked-in dependency changed: {path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    configure_exact_userdata()
    adb_value = adb_argument(sys.argv[1:])
    adb = shutil.which(adb_value) or adb_value
    serial = base.common.select_recovery(adb, 30)
    state = helper.prepare(base.common, adb, serial)
    print("exact_block_node_preparation=passed")
    print(f"exact_block_node_created={'yes' if state.created else 'no'}")
    print(f"exact_block_node_kernel_dev={state.kernel_dev}")
    print("ephemeral_device_node_write=/dev-tmpfs-only")
    print("ext4_identity_transport=adb-shell-base64")
    try:
        return base.main()
    finally:
        cleanup_output = helper.cleanup(base.common, adb, serial, state)
        if cleanup_output.count("exact_block_node_cleanup_status=passed") != 1:
            raise helper.ExactBlockNodeError(
                "exact block-node cleanup did not pass exactly once"
            )
        print("exact_block_node_cleanup=passed")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        base.ValidationError,
        base.cleanup.CleanupV2Error,
        base.common.Refusal,
        helper.ExactBlockNodeError,
        ext4_identity.Ext4IdentityError,
        OSError,
        ValueError,
    ) as exc:
        print(
            f"A33 INSTALLED ROOTFS READ-ONLY VALIDATION V2 FAILED: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
