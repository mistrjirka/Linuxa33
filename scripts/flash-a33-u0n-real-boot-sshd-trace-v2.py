#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "flash-a33-u0n-real-boot-sshd-trace.py"
EXPECTED_BASE_BLOB = "35caa92b0271c2d0b01460db62c30ecfb0208ddc"
SAFE_DELIMITER = ":"

spec = importlib.util.spec_from_file_location("a33_u0n_flash_v2_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0n flash path: {BASE_PATH}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


class U0nFlashV2Error(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def key_contract_arguments() -> list[str]:
    contracts = [
        SAFE_DELIMITER.join((name, kind, sha, mode))
        for name, (kind, sha, mode) in base.EXPECTED_KEYS.items()
    ]
    unsafe = re.compile(r"[|;&<>$`()\n\r\t ]")
    for contract in contracts:
        if contract.count(SAFE_DELIMITER) != 3 or unsafe.search(contract):
            raise U0nFlashV2Error(
                f"unsafe SSH host-key transport contract: {contract!r}"
            )
    return contracts


if base.KEY_CHECK_SCRIPT.count("IFS='|'") != 1:
    raise SystemExit("base U0n key-check delimiter anchor changed")
KEY_CHECK_SCRIPT = base.KEY_CHECK_SCRIPT.replace("IFS='|'", "IFS=':'")
if "IFS='|'" in KEY_CHECK_SCRIPT or KEY_CHECK_SCRIPT.count("IFS=':'") != 1:
    raise SystemExit("safe U0n key-check delimiter transformation failed")
base.KEY_CHECK_SCRIPT = KEY_CHECK_SCRIPT


def validate_phone_rootfs(adb: str, serial: str, local: dict[str, object]) -> None:
    state = base.block_helper.prepare(base.common, adb, serial)
    base.common.USERDATA = state.node
    print("exact_userdata_node_preparation=passed")
    try:
        values, sections = base.common.live_state(adb, serial)
        base.restore.assert_idle(values, sections)
        uuid_value, label = base.identity_helper.ext4_identity(base.common, adb, serial)
        if uuid_value != local["root_uuid"] or label != base.restore.EXPECTED_LABEL:
            raise base.U0nFlashError(
                f"rootfs identity mismatch uuid={uuid_value!r} label={label!r}"
            )
        verify_output = base.common.adb_shell(
            adb,
            serial,
            base.verify_helper.ROOTFS_SAFE_VERIFY_SCRIPT,
            state.node,
            str(local["root_uuid"]),
            *base.common.CRITICAL_PATHS,
        )
        actual = base.restore.parse_critical_hashes(verify_output)
        if actual != local["critical"]:
            raise base.U0nFlashError(
                "installed rootfs critical hashes differ from exact image"
            )
        key_output = base.common.adb_shell(
            adb,
            serial,
            KEY_CHECK_SCRIPT,
            state.node,
            *key_contract_arguments(),
        )
        required = (
            "host_key_private_count=4",
            "host_key_public_count=4",
            "sshd_pam_binary=present-executable",
            "sshd_default_runlevel=enabled",
            "readonly_key_preflight_unmount=passed",
            "userdata_persistent_writes=no",
        )
        for token in required:
            if key_output.count(token) != 1:
                raise base.U0nFlashError(
                    f"phone SSH preflight marker missing: {token}"
                )
        final_values, final_sections = base.common.live_state(adb, serial)
        base.restore.assert_idle(final_values, final_sections)
        print("restored_rootfs_and_exact_ssh_keys=passed")
        print("ssh_key_contract_transport=colon-delimited-shell-safe")
    finally:
        output = base.block_helper.cleanup(base.common, adb, serial, state)
        if output.count("exact_block_node_cleanup_status=passed") != 1:
            raise base.U0nFlashError("userdata temporary node cleanup failed")
        print("exact_userdata_node_cleanup=passed")


base.validate_phone_rootfs = validate_phone_rootfs


def main() -> int:
    repo = Path.home() / "Linuxa33"
    actual = git_blob(repo, BASE_PATH)
    if actual != EXPECTED_BASE_BLOB:
        raise U0nFlashV2Error(
            f"checked-in U0n flash base changed: actual={actual!r} "
            f"expected={EXPECTED_BASE_BLOB!r}"
        )
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0nFlashV2Error,
        base.U0nFlashError,
        base.restore.RestoreError,
        base.restore.cleanup.CleanupV2Error,
        base.restore.block_helper.ExactBlockNodeError,
        base.restore.identity_helper.Ext4IdentityError,
        base.recovery_helper.ExactRecoveryNodeError,
        base.rescue.RescueError,
        base.common.Refusal,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"REFUSING U0n FLASH V2: {exc}", file=sys.stderr)
        raise SystemExit(1)
