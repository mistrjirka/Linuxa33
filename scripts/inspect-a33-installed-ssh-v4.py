#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "inspect-a33-installed-ssh-v3.py"
HELPER = HERE / "lib/a33_exact_block_node.py"
EXT4_IDENTITY = HERE / "lib/a33_ext4_identity_text.py"
EXPECTED_BASE_BLOB = "78a7e93678f34cb2a038a76b7bf8716bb6b6a64c"
EXPECTED_HELPER_BLOB = "2232f92bbf2782aed88acd9246ed063148ca63a8"
EXPECTED_EXT4_IDENTITY_BLOB = "547aa185c56cfdefe09efab2ba1fbe1e63950de0"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load("a33_installed_ssh_v4_base", BASE)
helper = load("a33_installed_ssh_v4_exact_node", HELPER)
ext4_identity = load("a33_installed_ssh_v4_ext4_identity", EXT4_IDENTITY)
common = base.common


class InspectionV4Error(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        check=False,
    ).stdout.strip()


def adb_argument(argv: list[str]) -> str:
    for index, value in enumerate(argv):
        if value == "--adb":
            if index + 1 >= len(argv):
                raise InspectionV4Error("--adb requires a value")
            return argv[index + 1]
        if value.startswith("--adb="):
            candidate = value.split("=", 1)[1]
            if not candidate:
                raise InspectionV4Error("--adb requires a non-empty value")
            return candidate
    return "adb"


def main() -> int:
    repo = Path.home() / "Linuxa33"
    for path, expected in (
        (BASE, EXPECTED_BASE_BLOB),
        (HELPER, EXPECTED_HELPER_BLOB),
        (EXT4_IDENTITY, EXPECTED_EXT4_IDENTITY_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise InspectionV4Error(
                f"checked-in dependency changed: {path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    if helper.EXACT_NODE != base.EXACT_USERDATA:
        raise InspectionV4Error(
            "SSH inspector and exact-node helper disagree: "
            f"inspector={base.EXACT_USERDATA!r} helper={helper.EXACT_NODE!r}"
        )

    common.ext4_identity = lambda adb, serial: ext4_identity.ext4_identity(
        common, adb, serial
    )
    adb_value = adb_argument(sys.argv[1:])
    adb = shutil.which(adb_value) or adb_value
    serial = common.select_recovery(adb, 30)
    state = helper.prepare(common, adb, serial)
    print("exact_block_node_preparation=passed")
    print(f"exact_block_node_created={'yes' if state.created else 'no'}")
    print(f"exact_block_node_kernel_dev={state.kernel_dev}")
    print("ephemeral_device_node_write=/dev-tmpfs-only")
    print("ext4_identity_transport=adb-shell-base64")
    try:
        return base.main()
    finally:
        cleanup_output = helper.cleanup(common, adb, serial, state)
        if cleanup_output.count("exact_block_node_cleanup_status=passed") != 1:
            raise helper.ExactBlockNodeError(
                "exact block-node cleanup did not pass exactly once"
            )
        print("exact_block_node_cleanup=passed")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
    except (
        InspectionV4Error,
        base.InspectionV3Error,
        base.cleanup.CleanupV2Error,
        base.base.base.InspectionError,
        common.Refusal,
        helper.ExactBlockNodeError,
        ext4_identity.Ext4IdentityError,
        OSError,
        ValueError,
    ) as exc:
        print(f"A33 INSTALLED SSH V4 INSPECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
