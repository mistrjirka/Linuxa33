#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
RESTORE_PATH = HERE / "restore-a33-rootfs-after-unsafe-openrc-diagnostic.py"
RECOVERY_HELPER_PATH = HERE / "lib/a33_exact_recovery_node.py"
RESCUE_PATH = HERE / "verify-a33-twrp-rescue-assets.py"
EXPECTED_RESTORE_BLOB = "baf157a20617ec70fff8e79381055b34d77b0de8"
EXPECTED_RECOVERY_HELPER_BLOB = "854603db2be5d8fe47ae886277d8972f7c48ddee"
EXPECTED_RESCUE_BLOB = "bcd0fb3a9df34857ad072b94a0be7d01f8f73479"

CONFIRMATION = "FLASH-EXACT-U0N-RECOVERY"
EXPECTED_CANDIDATE_SHA256 = "9196109cba6a6e13f314b2aba28de21580c8b434c74e075c451d84b48da1bc2d"
EXPECTED_CANDIDATE_SIZE = 100663296
EXPECTED_MANIFEST_SHA256 = "ee9c238ba3d509c8216ce4457f20cfaa6eecf7dfc38feba03c4343a0641d20df"
EXPECTED_PATCH_SHA256 = "cf9eef5628f6d4a81197a5f82162afcdcb87c02cf0652a43b52f3cfa1e1bc7a7"
REMOTE_CANDIDATE = "/tmp/a33x-u0n-real-boot-sshd-trace-recovery.img"

EXPECTED_KEYS = {
    "ssh_host_ecdsa_key": ("private", "f8a73c4e5693ac7ff67520ecc8d9547ddda6627ded17b44356e03cc47af18d4a", "600"),
    "ssh_host_ed25519_key": ("private", "759eee32a41fb2ab1d71ca7bd4ca64a5bab1878bd4888094a36f581559117012", "600"),
    "ssh_host_mldsa44_ed25519_key": ("private", "da1fb8d69612b8815e521ba8ac1e715375d52371ec22564eac9785dc9b08622e", "600"),
    "ssh_host_rsa_key": ("private", "cb1bb2e20791bbdda1242e48519e1631451e077a5a8725e87cc795bd310f2ed6", "600"),
    "ssh_host_ecdsa_key.pub": ("public", "34e5aa8cfbeeac2d52b14698cdd11ac7eef2b7fc21aa861f71a0ab5aa1a03e15", "644"),
    "ssh_host_ed25519_key.pub": ("public", "d0d5232f09d14b35e9eab9d007bca8cc74d4b59b756ad00a180e9efb2d863ae5", "644"),
    "ssh_host_mldsa44_ed25519_key.pub": ("public", "6c5f10adcaf68a21cbd9f4bd048f1635a1932a5e5810677a96ef3137499aed5b", "644"),
    "ssh_host_rsa_key.pub": ("public", "1c395ec727aecd2d80d3b00a049da5c712f59d7fd09faf9f39c28e5f67954cc7", "644"),
}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


restore = load("a33_u0n_flash_restore", RESTORE_PATH)
recovery_helper = load("a33_u0n_flash_recovery", RECOVERY_HELPER_PATH)
rescue = load("a33_u0n_flash_rescue", RESCUE_PATH)
common = restore.common
block_helper = restore.block_helper
identity_helper = restore.identity_helper
verify_helper = restore.verify_helper


class U0nFlashError(RuntimeError):
    pass


KEY_CHECK_SCRIPT = r'''set -eu
target="$1"
shift
mountpoint=/tmp/a33x-u0n-key-preflight
mounted=no
cleanup_mount()
{
    [ "$mounted" = no ] || umount "$mountpoint" 2>/dev/null || true
}
trap cleanup_mount EXIT
mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"
mounted=yes
private_count=0
public_count=0
for contract in "$@"; do
    oldifs="$IFS"
    IFS='|'
    set -- $contract
    IFS="$oldifs"
    name="$1"
    kind="$2"
    expected_sha="$3"
    expected_mode="$4"
    file="$mountpoint/etc/ssh/$name"
    [ -f "$file" ] || { echo "host_key_missing=$name"; exit 70; }
    actual_sha="$(sha256sum "$file" | awk 'NR==1 {print $1}')"
    actual_mode="$(stat -c '%a' "$file")"
    actual_uid="$(stat -c '%u' "$file")"
    actual_gid="$(stat -c '%g' "$file")"
    [ "$actual_sha" = "$expected_sha" ] || exit 71
    [ "$actual_mode" = "$expected_mode" ] || exit 72
    [ "$actual_uid" = 0 ] && [ "$actual_gid" = 0 ] || exit 73
    case "$kind" in
        private) private_count=$((private_count + 1)) ;;
        public) public_count=$((public_count + 1)) ;;
        *) exit 74 ;;
    esac
    echo "host_key_verified name=$name kind=$kind sha256=$actual_sha mode=$actual_mode uid=$actual_uid gid=$actual_gid"
done
[ "$private_count" -eq 4 ] && [ "$public_count" -eq 4 ] || exit 75
[ -x "$mountpoint/usr/sbin/sshd.pam" ] || exit 76
[ -f "$mountpoint/etc/ssh/sshd_config" ] || exit 77
[ -L "$mountpoint/etc/runlevels/default/sshd" ] || exit 78
[ "$(readlink "$mountpoint/etc/runlevels/default/sshd")" = /etc/init.d/sshd ] || exit 79
umount "$mountpoint"
mounted=no
echo "host_key_private_count=$private_count"
echo "host_key_public_count=$public_count"
echo "sshd_pam_binary=present-executable"
echo "sshd_default_runlevel=enabled"
echo "readonly_key_preflight_unmount=passed"
echo "userdata_persistent_writes=no"
'''


WRITE_SCRIPT = r'''set -eu
source="$1"
target="$2"
expected_size="$3"
expected_sha="$4"
[ -f "$source" ] || exit 80
[ -b "$target" ] || exit 81
[ "$(stat -c '%s' "$source")" = "$expected_size" ] || exit 82
[ "$(sha256sum "$source" | awk 'NR==1 {print $1}')" = "$expected_sha" ] || exit 83
dd if="$source" of="$target" bs=4194304 count=24
sync
readback="$(sha256sum "$target" | awk 'NR==1 {print $1}')"
echo "recovery_readback_sha256=$readback"
[ "$readback" = "$expected_sha" ] || exit 84
echo "recovery_exact_write=passed"
echo "recovery_written=yes"
echo "userdata_written=no"
echo "cache_written=no"
echo "super_written=no"
echo "boot_written=no"
echo "phone_reboot_performed=no"
'''


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def local_evidence(root: Path, repo: Path) -> dict[str, object]:
    for path, expected in (
        (RESTORE_PATH, EXPECTED_RESTORE_BLOB),
        (RECOVERY_HELPER_PATH, EXPECTED_RECOVERY_HELPER_BLOB),
        (RESCUE_PATH, EXPECTED_RESCUE_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0nFlashError(
                f"checked-in dependency changed: {path.name} actual={actual!r} expected={expected!r}"
            )

    restored, image = restore.local_evidence(root, repo)
    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0n-real-boot-sshd-trace-manifest.txt"
    patch_path = root / "build/u0n-real-boot-sshd-trace-patch.txt"
    audit_path = root / "build/a33-u0n-candidate-audit.txt"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0n-real-boot-sshd-trace-recovery.img"
    for path in (manifest_path, patch_path, audit_path, candidate):
        if not path.is_file():
            raise U0nFlashError(f"missing U0n evidence: {path}")
    if common.sha_file(manifest_path) != EXPECTED_MANIFEST_SHA256:
        raise U0nFlashError("U0n manifest hash differs from audited result")
    if common.sha_file(patch_path) != EXPECTED_PATCH_SHA256:
        raise U0nFlashError("U0n patch report hash differs from audited result")
    if candidate.stat().st_size != EXPECTED_CANDIDATE_SIZE:
        raise U0nFlashError("U0n candidate size mismatch")
    if common.sha_file(candidate) != EXPECTED_CANDIDATE_SHA256:
        raise U0nFlashError("U0n candidate hash mismatch")

    manifest = common.kv(manifest_path)
    common.require(
        manifest,
        {
            "candidate": "U0n-real-boot-sshd-trace",
            "functional_base": "U0m-watchdog-magic-close",
            "cpio_payload_delta": "init_2nd.sh",
            "rootfs_persistent_delta": "none",
            "u0m_watchdog_hook_preserved": "yes",
            "recovery_sha256": EXPECTED_CANDIDATE_SHA256,
            "phone_partition_writes": "no",
            "build_status": "passed",
        },
        "U0n manifest",
    )
    audit = common.kv(audit_path)
    common.require(
        audit,
        {
            "operation": "host-only-audit-u0n-real-boot-sshd-trace",
            "candidate_sha256": EXPECTED_CANDIDATE_SHA256,
            "initramfs_payload_delta": "init_2nd-only",
            "u0m_watchdog_hook_byte_identical": "yes",
            "openrc_default_start_stop_semantics_preserved": "yes",
            "instrumented_sshd_transformation_recomputed": "yes",
            "kernel_unchanged": "yes",
            "dtb_unchanged": "yes",
            "recovery_dtbo_unchanged": "yes",
            "kernel_cmdline_unchanged": "yes",
            "rootfs_persistent_delta": "none",
            "phone_partition_writes": "no",
            "audit_status": "passed",
        },
        "U0n audit",
    )
    commit = manifest.get("linuxa33_commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise U0nFlashError("invalid U0n manifest commit")
    ancestor = common.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, "HEAD"],
        check=False,
    )
    if ancestor.returncode != 0:
        raise U0nFlashError("U0n manifest commit is not an ancestor of current HEAD")

    odin = root / "tools/odin4"
    rescue_tar = root / "build/rescue/twrp-a33x-restore.img.tar"
    rescue_assets = rescue.verify_assets(root=root, odin=odin, rescue_tar=rescue_tar)
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


def validate_phone_rootfs(adb: str, serial: str, local: dict[str, object]) -> None:
    state = block_helper.prepare(common, adb, serial)
    common.USERDATA = state.node
    print("exact_userdata_node_preparation=passed")
    try:
        values, sections = common.live_state(adb, serial)
        restore.assert_idle(values, sections)
        uuid_value, label = identity_helper.ext4_identity(common, adb, serial)
        if uuid_value != local["root_uuid"] or label != restore.EXPECTED_LABEL:
            raise U0nFlashError(
                f"rootfs identity mismatch uuid={uuid_value!r} label={label!r}"
            )
        verify_output = common.adb_shell(
            adb,
            serial,
            verify_helper.ROOTFS_SAFE_VERIFY_SCRIPT,
            state.node,
            str(local["root_uuid"]),
            *common.CRITICAL_PATHS,
        )
        actual = restore.parse_critical_hashes(verify_output)
        if actual != local["critical"]:
            raise U0nFlashError("installed rootfs critical hashes differ from exact image")
        contracts = [
            f"{name}|{kind}|{sha}|{mode}"
            for name, (kind, sha, mode) in EXPECTED_KEYS.items()
        ]
        key_output = common.adb_shell(
            adb, serial, KEY_CHECK_SCRIPT, state.node, *contracts
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
                raise U0nFlashError(f"phone SSH preflight marker missing: {token}")
        final_values, final_sections = common.live_state(adb, serial)
        restore.assert_idle(final_values, final_sections)
        print("restored_rootfs_and_exact_ssh_keys=passed")
    finally:
        output = block_helper.cleanup(common, adb, serial, state)
        if output.count("exact_block_node_cleanup_status=passed") != 1:
            raise U0nFlashError("userdata temporary node cleanup failed")
        print("exact_userdata_node_cleanup=passed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flash exact audited U0n recovery after fail-closed phone validation"
    )
    parser.add_argument("confirmation", nargs="?", default="")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    if args.preflight_only and args.confirmation:
        raise U0nFlashError("do not provide a confirmation token with --preflight-only")
    if not args.preflight_only and args.confirmation != CONFIRMATION:
        raise U0nFlashError(
            f"recovery write requires exact confirmation token: {CONFIRMATION}"
        )

    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    local = local_evidence(root, repo)
    print("u0n_local_candidate_and_rescue_evidence=passed")
    print(f"candidate_sha256={local['candidate_sha']}")

    serial = common.select_recovery(adb, 30)
    fingerprint = restore.cleanup.validate_runtime_fingerprint(adb, serial)
    validate_phone_rootfs(adb, serial, local)
    recovery_state = recovery_helper.prepare(
        common, adb, serial, common.KNOWN_TWRP_SHA256
    )
    print("exact_twrp_recovery_partition=passed")
    print(f"recovery_kernel_name={recovery_state.kernel_name}")
    print(f"recovery_kernel_dev={recovery_state.kernel_dev}")

    remote_staged = False
    recovery_written = False
    try:
        if args.preflight_only:
            print("u0n_flash_preflight_status=passed")
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
            raise U0nFlashError(f"staged U0n identity mismatch: {remote!r}")
        write_output = common.adb_shell(
            adb,
            serial,
            WRITE_SCRIPT,
            REMOTE_CANDIDATE,
            recovery_state.node,
            str(local["candidate_size"]),
            str(local["candidate_sha"]),
        )
        if write_output.count("recovery_exact_write=passed") != 1:
            raise U0nFlashError("U0n recovery write did not report success")
        recovery_written = True

        report = root / "build/a33-u0n-real-boot-sshd-trace-flash.txt"
        pairs = [
            ("created", datetime.now().astimezone().isoformat(timespec="microseconds")),
            ("operation", "flash-exact-u0n-real-boot-sshd-trace"),
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
            ("rootfs_validation", "identity-critical-hashes-and-exact-host-keys-passed"),
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
        cleanup_output = recovery_helper.cleanup(common, adb, serial, recovery_state)
        if cleanup_output.count("exact_recovery_node_cleanup_status=passed") != 1:
            message = "temporary recovery node cleanup failed"
            if recovery_written:
                message += "; U0n may already be installed"
            raise U0nFlashError(message)
        print("exact_recovery_node_cleanup=passed")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0nFlashError,
        restore.RestoreError,
        restore.cleanup.CleanupV2Error,
        restore.block_helper.ExactBlockNodeError,
        restore.identity_helper.Ext4IdentityError,
        recovery_helper.ExactRecoveryNodeError,
        rescue.RescueError,
        common.Refusal,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"REFUSING U0n FLASH: {exc}", file=sys.stderr)
        raise SystemExit(1)
