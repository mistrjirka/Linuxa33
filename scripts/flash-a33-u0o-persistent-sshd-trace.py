#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
U0N_FLASH_V2_PATH = HERE / "flash-a33-u0n-real-boot-sshd-trace-v2.py"
U0O_BUILDER_V2_PATH = HERE / "make-u0o-persistent-sshd-trace-v2.py"
U0O_AUDIT_V2_PATH = HERE / "audit-a33-u0o-candidate-v2.py"
EXPECTED_U0N_FLASH_V2_BLOB = "337807470888e0d00a6afb40a5a7ce7bcd8875c3"
EXPECTED_U0O_BUILDER_V2_BLOB = "88cd0b9b3446314c04ad0c4b20583c2e6facf449"
EXPECTED_U0O_AUDIT_V2_BLOB = "25a3ab194093b7b082477caba5c554481f37bf1a"

CONFIRMATION = "FLASH-EXACT-U0O-RECOVERY"
EXPECTED_CANDIDATE_SHA256 = "d98bb291f56fc8cb2f595c915d146c3b951333f04435dfb4e2839b95ddc5da0b"
EXPECTED_CANDIDATE_SIZE = 100663296
EXPECTED_MANIFEST_SHA256 = "486387c863f55c28dec19128eff2a46d377d86762ae543aa2f1978292845b728"
EXPECTED_PATCH_SHA256 = "f68c4dc7e605f8659553e7645db4f7e3cdfe47426bbf27906f740895671aea3a"
TRACE_PATH = "/var/log/a33x-u0o-real-boot-sshd.log"
REMOTE_CANDIDATE = "/tmp/a33x-u0o-persistent-sshd-trace-recovery.img"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0n_flash_v2 = load("a33_u0o_flash_u0n_v2", U0N_FLASH_V2_PATH)
base = u0n_flash_v2.base
common = base.common


class U0oFlashError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def local_evidence(root: Path, repo: Path) -> dict[str, object]:
    for path, expected in (
        (U0N_FLASH_V2_PATH, EXPECTED_U0N_FLASH_V2_BLOB),
        (U0O_BUILDER_V2_PATH, EXPECTED_U0O_BUILDER_V2_BLOB),
        (U0O_AUDIT_V2_PATH, EXPECTED_U0O_AUDIT_V2_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0oFlashError(
                f"checked-in U0o dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    restored, image = base.restore.local_evidence(root, repo)
    manifest_path = (
        root
        / "build/candidates/a33x-h1-usbpd-u0o-persistent-sshd-trace-manifest.txt"
    )
    patch_path = root / "build/u0o-persistent-sshd-trace-patch.txt"
    audit_path = root / "build/a33-u0o-candidate-audit.txt"
    candidate = (
        root
        / "build/candidates/a33x-h1-usbpd-u0o-persistent-sshd-trace-recovery.img"
    )
    for path in (manifest_path, patch_path, audit_path, candidate):
        if not path.is_file():
            raise U0oFlashError(f"missing U0o evidence: {path}")
    if common.sha_file(manifest_path) != EXPECTED_MANIFEST_SHA256:
        raise U0oFlashError("U0o manifest hash differs from the audited result")
    if common.sha_file(patch_path) != EXPECTED_PATCH_SHA256:
        raise U0oFlashError("U0o patch report hash differs from the audited result")
    if candidate.stat().st_size != EXPECTED_CANDIDATE_SIZE:
        raise U0oFlashError("U0o candidate size mismatch")
    if common.sha_file(candidate) != EXPECTED_CANDIDATE_SHA256:
        raise U0oFlashError("U0o candidate hash mismatch")

    manifest = common.kv(manifest_path)
    common.require(
        manifest,
        {
            "candidate": "U0o-persistent-sshd-trace",
            "functional_base": "U0n-real-boot-sshd-trace",
            "cpio_payload_delta": "init_2nd.sh",
            "sshd_behavior_delta_from_u0n": "none",
            "persistent_trace_path": TRACE_PATH,
            "persistent_trace_mode": "0600",
            "persistent_trace_write_scope": (
                "truncate-on-u0o-boot-and-append-u0n-events-only"
            ),
            "rootfs_persistent_delta": TRACE_PATH,
            "u0n_watchdog_hook_preserved": "yes",
            "recovery_sha256": EXPECTED_CANDIDATE_SHA256,
            "phone_partition_writes": "no",
            "build_status": "passed",
        },
        "U0o manifest",
    )
    audit = common.kv(audit_path)
    common.require(
        audit,
        {
            "operation": "host-only-audit-u0o-persistent-sshd-trace",
            "candidate_sha256": EXPECTED_CANDIDATE_SHA256,
            "initramfs_payload_delta": "init_2nd-only",
            "u0n_watchdog_hook_byte_identical": "yes",
            "u0n_openrc_behavior_preserved": "yes",
            "persistent_trace_transformation_recomputed": "yes",
            "persistent_trace_path": TRACE_PATH,
            "persistent_trace_file_count": "1",
            "persistent_trace_scope_verified": "yes",
            "kernel_unchanged": "yes",
            "dtb_unchanged": "yes",
            "recovery_dtbo_unchanged": "yes",
            "kernel_cmdline_unchanged": "yes",
            "rootfs_persistent_delta": TRACE_PATH,
            "phone_partition_writes": "no",
            "audit_status": "passed",
        },
        "U0o audit",
    )
    commit = manifest.get("linuxa33_commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise U0oFlashError("invalid U0o manifest commit")
    ancestor = common.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, "HEAD"],
        check=False,
    )
    if ancestor.returncode != 0:
        raise U0oFlashError("U0o manifest commit is not an ancestor of current HEAD")

    rescue_assets = base.rescue.verify_assets(
        root=root,
        odin=root / "tools/odin4",
        rescue_tar=root / "build/rescue/twrp-a33x-restore.img.tar",
    )
    return {
        **restored,
        "rootfs_image": image,
        "manifest_path": manifest_path,
        "patch_path": patch_path,
        "audit_path": audit_path,
        "candidate": candidate,
        "candidate_sha": EXPECTED_CANDIDATE_SHA256,
        "candidate_size": EXPECTED_CANDIDATE_SIZE,
        "rescue_assets": rescue_assets,
    }


TRACE_ABSENCE_SCRIPT = rf'''set -eu
target="$1"
mountpoint=/tmp/a33x-u0o-trace-baseline
trace="$mountpoint{TRACE_PATH}"
mounted=no
cleanup()
{{
    if [ "$mounted" = yes ]; then
        umount "$mountpoint" 2>/dev/null || true
    fi
    if ! awk -v point="$mountpoint" '$2 == point {{ found=1 }} END {{ exit found ? 0 : 1 }}' /proc/mounts; then
        rmdir "$mountpoint" 2>/dev/null || true
    fi
}}
trap cleanup EXIT
mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"
mounted=yes
if [ -e "$trace" ]; then
    echo "u0o_trace_baseline=unexpected-present"
    stat -c 'trace_mode=%a trace_uid=%u trace_gid=%g trace_bytes=%s' "$trace" 2>/dev/null || true
    exit 90
fi
umount "$mountpoint"
mounted=no
echo "u0o_trace_baseline=absent"
echo "u0o_trace_baseline_unmount=passed"
echo "userdata_persistent_writes=no"
'''


def validate_phone_rootfs(adb: str, serial: str, local: dict[str, object]) -> None:
    # Reuse the shell-safe exact rootfs, critical-file, key, PAM and runlevel checks.
    u0n_flash_v2.validate_phone_rootfs(adb, serial, local)

    state = base.block_helper.prepare(common, adb, serial)
    common.USERDATA = state.node
    print("exact_userdata_node_trace_baseline_preparation=passed")
    try:
        output = common.adb_shell(
            adb, serial, TRACE_ABSENCE_SCRIPT, state.node
        )
        for token in (
            "u0o_trace_baseline=absent",
            "u0o_trace_baseline_unmount=passed",
            "userdata_persistent_writes=no",
        ):
            if output.count(token) != 1:
                raise U0oFlashError(f"U0o trace baseline marker missing: {token}")
        final_values, final_sections = common.live_state(adb, serial)
        base.restore.assert_idle(final_values, final_sections)
        print("u0o_trace_file_baseline_absent=passed")
    finally:
        cleanup_output = base.block_helper.cleanup(common, adb, serial, state)
        if cleanup_output.count("exact_block_node_cleanup_status=passed") != 1:
            raise U0oFlashError("U0o trace-baseline temporary node cleanup failed")
        print("exact_userdata_node_trace_baseline_cleanup=passed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Flash exact audited U0o recovery after fail-closed rootfs, SSH, "
            "trace-baseline and recovery validation"
        )
    )
    parser.add_argument("confirmation", nargs="?", default="")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    if args.preflight_only and args.confirmation:
        raise U0oFlashError("do not provide a confirmation token with --preflight-only")
    if not args.preflight_only and args.confirmation != CONFIRMATION:
        raise U0oFlashError(
            f"recovery write requires exact confirmation token: {CONFIRMATION}"
        )

    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    local = local_evidence(root, repo)
    print("u0o_local_candidate_and_rescue_evidence=passed")
    print(f"candidate_sha256={local['candidate_sha']}")

    serial = common.select_recovery(adb, 30)
    fingerprint = base.restore.cleanup.validate_runtime_fingerprint(adb, serial)
    validate_phone_rootfs(adb, serial, local)
    recovery_state = base.recovery_helper.prepare(
        common, adb, serial, common.KNOWN_TWRP_SHA256
    )
    print("exact_twrp_recovery_partition=passed")
    print(f"recovery_kernel_name={recovery_state.kernel_name}")
    print(f"recovery_kernel_dev={recovery_state.kernel_dev}")

    remote_staged = False
    recovery_written = False
    try:
        if args.preflight_only:
            print("u0o_flash_preflight_status=passed")
            print("recovery_written=no")
            print("phone_partition_writes=no")
            return 0

        common.run(
            [adb, "-s", serial, "push", str(local["candidate"]), REMOTE_CANDIDATE]
        )
        remote_staged = True
        remote = common.adb_shell(
            adb,
            serial,
            'set -eu\nstat -c "%s" "$1"\nsha256sum "$1"\n',
            REMOTE_CANDIDATE,
        ).splitlines()
        if (
            len(remote) < 2
            or remote[0] != str(local["candidate_size"])
            or remote[1].split()[0] != local["candidate_sha"]
        ):
            raise U0oFlashError(f"staged U0o identity mismatch: {remote!r}")
        write_output = common.adb_shell(
            adb,
            serial,
            base.WRITE_SCRIPT,
            REMOTE_CANDIDATE,
            recovery_state.node,
            str(local["candidate_size"]),
            str(local["candidate_sha"]),
        )
        if write_output.count("recovery_exact_write=passed") != 1:
            raise U0oFlashError("U0o recovery write did not report success")
        recovery_written = True

        report = root / "build/a33-u0o-persistent-sshd-trace-flash.txt"
        pairs = [
            ("created", datetime.now().astimezone().isoformat(timespec="microseconds")),
            ("operation", "flash-exact-u0o-persistent-sshd-trace"),
            ("implementation_language", "python3"),
            ("adb_serial", serial),
            ("candidate", local["candidate"]),
            ("candidate_sha256", local["candidate_sha"]),
            ("candidate_size", local["candidate_size"]),
            ("manifest", local["manifest_path"]),
            ("manifest_sha256", common.sha_file(Path(local["manifest_path"]))),
            ("audit", local["audit_path"]),
            ("audit_sha256", common.sha_file(Path(local["audit_path"]))),
            ("twrp_kernel_release", fingerprint["kernel_release"]),
            ("twrp_config_gz_sha256", fingerprint["config_gz_sha256"]),
            ("recovery_partname", recovery_state.partname),
            ("recovery_kernel_name", recovery_state.kernel_name),
            ("recovery_kernel_dev", recovery_state.kernel_dev),
            ("recovery_previous_sha256", common.KNOWN_TWRP_SHA256),
            ("recovery_partition_sha256", local["candidate_sha"]),
            (
                "rootfs_validation",
                "identity-critical-hashes-exact-host-keys-and-trace-absent-passed",
            ),
            ("persistent_trace_path", TRACE_PATH),
            ("persistent_trace_baseline", "absent"),
            ("userdata_written", "no"),
            ("cache_written", "no"),
            ("super_written", "no"),
            ("boot_written", "no"),
            ("recovery_written", "yes"),
            ("reboot_performed", "no"),
            ("flash_status", "passed"),
        ]
        report.write_text(
            "".join(f"{key}={value}\n" for key, value in pairs), encoding="utf-8"
        )
        for key, value in pairs:
            print(f"{key}={value}")
        print(f"report={report}")
        print("Phone remains in the currently running TWRP RAM environment.")
        return 0
    finally:
        if remote_staged:
            common.adb_shell(
                adb, serial, 'rm -f "$1" 2>/dev/null || true\n', REMOTE_CANDIDATE
            )
        cleanup_output = base.recovery_helper.cleanup(
            common, adb, serial, recovery_state
        )
        if cleanup_output.count("exact_recovery_node_cleanup_status=passed") != 1:
            message = "temporary recovery node cleanup failed"
            if recovery_written:
                message += "; U0o may already be installed"
            raise U0oFlashError(message)
        print("exact_recovery_node_cleanup=passed")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0oFlashError,
        u0n_flash_v2.U0nFlashV2Error,
        base.U0nFlashError,
        base.restore.RestoreError,
        base.restore.cleanup.CleanupV2Error,
        base.restore.block_helper.ExactBlockNodeError,
        base.restore.identity_helper.Ext4IdentityError,
        base.recovery_helper.ExactRecoveryNodeError,
        base.rescue.RescueError,
        common.Refusal,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"REFUSING U0o FLASH: {exc}", file=sys.stderr)
        raise SystemExit(1)
