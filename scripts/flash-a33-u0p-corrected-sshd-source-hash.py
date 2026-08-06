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
U0O_FLASH_PATH = HERE / "flash-a33-u0o-persistent-sshd-trace.py"
U0P_BUILDER_PATH = HERE / "make-u0p-corrected-sshd-source-hash.py"
U0P_AUDIT_PATH = HERE / "audit-a33-u0p-candidate.py"
EXPECTED_U0O_FLASH_BLOB = "441f3c055ca25aa06cd195f1f28b78365817949c"
EXPECTED_U0P_BUILDER_BLOB = "2a5eb4957424fe81212e762ed2225f86ec890ca4"
EXPECTED_U0P_AUDIT_BLOB = "abc5ac0901a0ca09bbac896d257d0ff40d9a0c66"

CONFIRMATION = "FLASH-EXACT-U0P-RECOVERY"
EXPECTED_CANDIDATE_SHA256 = "59f22a3d27eb63cd8d616e7e55e0ecd16fe91a16fbe8e68759d724d2405d5264"
EXPECTED_CANDIDATE_SIZE = 100663296
EXPECTED_MANIFEST_SHA256 = "a2dd0ec55a08002b3336d46c0bf5c3757ec05b7b221748dfe586937cf53a5059"
EXPECTED_PATCH_SHA256 = "ce14c12d55c6c6297dce1f52355adc915d3601ddb207feaeec012536a53ce17b"
EXPECTED_AUDIT_SHA256 = "a89fef6091a5c6ec9c390d73b8ac74f4ff64cad7a98d04321ef7cc3eaba36fe8"
CORRECTED_SSHD_SHA256 = "52ddad2085f6364b8a94f21dfd1d092f24c808a43b2fd28c16386c284bf94ea6"
TRACE_PATH = "/var/log/a33x-u0o-real-boot-sshd.log"
KNOWN_U0O_FAILURE_TRACE_SHA256 = "4adac80415ca89c6cb8d4642b0372428cdd5a577bb457c9e4daa7b86bed9a895"
KNOWN_U0O_FAILURE_TRACE_BYTES = 267
REMOTE_CANDIDATE = "/tmp/a33x-u0p-corrected-sshd-source-hash-recovery.img"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0o_flash = load("a33_u0p_flash_u0o", U0O_FLASH_PATH)
base = u0o_flash.base
common = u0o_flash.common


class U0pFlashError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def local_evidence(root: Path, repo: Path) -> dict[str, object]:
    for path, expected in (
        (U0O_FLASH_PATH, EXPECTED_U0O_FLASH_BLOB),
        (U0P_BUILDER_PATH, EXPECTED_U0P_BUILDER_BLOB),
        (U0P_AUDIT_PATH, EXPECTED_U0P_AUDIT_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0pFlashError(
                f"checked-in U0p dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    restored, image = base.restore.local_evidence(root, repo)
    manifest_path = (
        root
        / "build/candidates/a33x-h1-usbpd-u0p-corrected-sshd-source-hash-manifest.txt"
    )
    patch_path = root / "build/u0p-corrected-sshd-source-hash-patch.txt"
    audit_path = root / "build/a33-u0p-candidate-audit.txt"
    candidate = (
        root
        / "build/candidates/a33x-h1-usbpd-u0p-corrected-sshd-source-hash-recovery.img"
    )
    for path in (manifest_path, patch_path, audit_path, candidate):
        if not path.is_file():
            raise U0pFlashError(f"missing U0p evidence: {path}")
    for path, expected in (
        (manifest_path, EXPECTED_MANIFEST_SHA256),
        (patch_path, EXPECTED_PATCH_SHA256),
        (audit_path, EXPECTED_AUDIT_SHA256),
    ):
        actual = common.sha_file(path)
        if actual != expected:
            raise U0pFlashError(
                f"U0p evidence hash mismatch: path={path} actual={actual} expected={expected}"
            )
    if candidate.stat().st_size != EXPECTED_CANDIDATE_SIZE:
        raise U0pFlashError("U0p candidate size mismatch")
    if common.sha_file(candidate) != EXPECTED_CANDIDATE_SHA256:
        raise U0pFlashError("U0p candidate hash mismatch")

    manifest = common.kv(manifest_path)
    common.require(
        manifest,
        {
            "candidate": "U0p-corrected-sshd-source-hash",
            "functional_base": "U0o-persistent-sshd-trace",
            "runtime_failure_fixed": "instrumented-source-hash-mismatch",
            "corrected_instrumented_sshd_sha256": CORRECTED_SSHD_SHA256,
            "embedded_instrumented_sshd_bytes_preserved": "yes",
            "sshd_behavior_delta_from_u0o": "none",
            "persistent_trace_path": TRACE_PATH,
            "persistent_trace_write_scope": "unchanged-from-u0o",
            "rootfs_persistent_delta": TRACE_PATH,
            "u0o_watchdog_hook_preserved": "yes",
            "recovery_sha256": EXPECTED_CANDIDATE_SHA256,
            "phone_partition_writes": "no",
            "build_status": "passed",
        },
        "U0p manifest",
    )
    audit = common.kv(audit_path)
    common.require(
        audit,
        {
            "operation": "host-only-audit-u0p-corrected-sshd-source-hash",
            "candidate_sha256": EXPECTED_CANDIDATE_SHA256,
            "embedded_instrumented_sshd_bytes_identical": "yes",
            "exact_embedded_sshd_sha256": CORRECTED_SSHD_SHA256,
            "before_declared_hash_matches_embedded": "no",
            "after_declared_hash_matches_embedded": "yes",
            "runtime_source_hash_contract": "passed",
            "runtime_failure_fixed": "instrumented-source-hash-mismatch",
            "sshd_behavior_delta_from_u0o": "none",
            "persistent_trace_path": TRACE_PATH,
            "persistent_trace_scope_unchanged": "yes",
            "kernel_unchanged": "yes",
            "dtb_unchanged": "yes",
            "recovery_dtbo_unchanged": "yes",
            "kernel_cmdline_unchanged": "yes",
            "phone_partition_writes": "no",
            "audit_status": "passed",
        },
        "U0p audit",
    )
    commit = manifest.get("linuxa33_commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise U0pFlashError("invalid U0p manifest commit")
    ancestor = common.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, "HEAD"],
        check=False,
    )
    if ancestor.returncode != 0:
        raise U0pFlashError("U0p manifest commit is not an ancestor of current HEAD")

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


KNOWN_TRACE_SCRIPT = rf'''set -eu
target="$1"
expected_sha="$2"
expected_bytes="$3"
mountpoint=/tmp/a33x-u0p-parent-trace
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
[ -f "$trace" ] || exit 90
actual_sha="$(sha256sum "$trace" | awk 'NR==1 {{print $1}}')"
actual_bytes="$(stat -c '%s' "$trace")"
actual_mode="$(stat -c '%a' "$trace")"
actual_uid="$(stat -c '%u' "$trace")"
actual_gid="$(stat -c '%g' "$trace")"
[ "$actual_sha" = "$expected_sha" ] || exit 91
[ "$actual_bytes" = "$expected_bytes" ] || exit 92
[ "$actual_mode" = 600 ] || exit 93
[ "$actual_uid" = 0 ] && [ "$actual_gid" = 0 ] || exit 94
grep -Fq 'candidate=U0o-persistent-sshd-trace stage=trace-open' "$trace" || exit 95
grep -Fq 'error=instrumented-source-hash-mismatch' "$trace" || exit 96
[ "$(wc -l < "$trace" | tr -d ' ')" = 3 ] || exit 97
umount "$mountpoint"
mounted=no
echo "u0p_parent_trace_state=known-u0o-instrumented-source-hash-mismatch"
echo "u0p_parent_trace_sha256=$actual_sha"
echo "u0p_parent_trace_bytes=$actual_bytes"
echo "u0p_parent_trace_metadata=600:0:0"
echo "u0p_parent_trace_readonly_unmount=passed"
echo "userdata_persistent_writes=no"
'''


def validate_phone_rootfs(adb: str, serial: str, local: dict[str, object]) -> None:
    # Reuse the exact shell-safe rootfs, critical-file, key, PAM and runlevel checks.
    u0o_flash.u0n_flash_v2.validate_phone_rootfs(adb, serial, local)

    state = base.block_helper.prepare(common, adb, serial)
    common.USERDATA = state.node
    print("exact_userdata_node_u0p_parent_trace_preparation=passed")
    try:
        output = common.adb_shell(
            adb,
            serial,
            KNOWN_TRACE_SCRIPT,
            state.node,
            KNOWN_U0O_FAILURE_TRACE_SHA256,
            str(KNOWN_U0O_FAILURE_TRACE_BYTES),
        )
        required = (
            "u0p_parent_trace_state=known-u0o-instrumented-source-hash-mismatch",
            f"u0p_parent_trace_sha256={KNOWN_U0O_FAILURE_TRACE_SHA256}",
            f"u0p_parent_trace_bytes={KNOWN_U0O_FAILURE_TRACE_BYTES}",
            "u0p_parent_trace_metadata=600:0:0",
            "u0p_parent_trace_readonly_unmount=passed",
            "userdata_persistent_writes=no",
        )
        for token in required:
            if output.count(token) != 1:
                raise U0pFlashError(f"U0p parent trace marker missing: {token}")
        final_values, final_sections = common.live_state(adb, serial)
        base.restore.assert_idle(final_values, final_sections)
        print("u0p_known_u0o_failure_trace_baseline=passed")
    finally:
        cleanup_output = base.block_helper.cleanup(common, adb, serial, state)
        if cleanup_output.count("exact_block_node_cleanup_status=passed") != 1:
            raise U0pFlashError("U0p trace-baseline temporary node cleanup failed")
        print("exact_userdata_node_u0p_parent_trace_cleanup=passed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Flash exact audited U0p recovery after fail-closed rootfs, SSH, "
            "known failed-U0o trace and recovery validation"
        )
    )
    parser.add_argument("confirmation", nargs="?", default="")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    if args.preflight_only and args.confirmation:
        raise U0pFlashError("do not provide a confirmation token with --preflight-only")
    if not args.preflight_only and args.confirmation != CONFIRMATION:
        raise U0pFlashError(
            f"recovery write requires exact confirmation token: {CONFIRMATION}"
        )

    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    local = local_evidence(root, repo)
    print("u0p_local_candidate_and_rescue_evidence=passed")
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
            print("u0p_flash_preflight_status=passed")
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
            raise U0pFlashError(f"staged U0p identity mismatch: {remote!r}")
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
            raise U0pFlashError("U0p recovery write did not report success")
        recovery_written = True

        report = root / "build/a33-u0p-corrected-sshd-source-hash-flash.txt"
        pairs = [
            ("created", datetime.now().astimezone().isoformat(timespec="microseconds")),
            ("operation", "flash-exact-u0p-corrected-sshd-source-hash"),
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
                "identity-critical-hashes-exact-host-keys-and-known-u0o-failure-trace-passed",
            ),
            ("persistent_trace_path", TRACE_PATH),
            ("persistent_trace_baseline", "known-u0o-instrumented-source-hash-mismatch"),
            ("persistent_trace_baseline_sha256", KNOWN_U0O_FAILURE_TRACE_SHA256),
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
                message += "; U0p may already be installed"
            raise U0pFlashError(message)
        print("exact_recovery_node_cleanup=passed")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0pFlashError,
        u0o_flash.U0oFlashError,
        u0o_flash.u0n_flash_v2.U0nFlashV2Error,
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
        print(f"REFUSING U0p FLASH: {exc}", file=sys.stderr)
        raise SystemExit(1)
