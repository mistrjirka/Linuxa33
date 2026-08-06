#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

HERE = Path(__file__).resolve().parent
U0P_FLASH_PATH = HERE / "flash-a33-u0p-corrected-sshd-source-hash.py"
U0Q_BUILDER_V2_PATH = HERE / "make-u0q-emergency-ssh-v2.py"
U0Q_AUDIT_V2_PATH = HERE / "audit-a33-u0q-candidate-v2.py"
EXPECTED_U0P_FLASH_BLOB = "793b82e81247654c7a2eb7200e130df56268fd83"
EXPECTED_U0Q_BUILDER_V2_BLOB = "63d3d9c548847b6ad710f29844265359e401185d"
EXPECTED_U0Q_AUDIT_V2_BLOB = "1b6deba17e05d95ed18b605c83356e069075da89"

CONFIRMATION = "FLASH-EXACT-U0Q-V2-RECOVERY"
EXPECTED_CANDIDATE_SIZE = 100663296
PARENT_TRACE_PATH = "/var/log/a33x-u0o-real-boot-sshd.log"
EMERGENCY_TRACE_PATH = "/var/log/a33x-u0q-emergency-ssh.log"
KNOWN_U0P_TRACE_SHA256 = "8f39d87de43796fec970ee7116f83d46dfa39f21e913bc6764c0d6e568574392"
KNOWN_U0P_TRACE_BYTES = 673
KNOWN_U0P_TRACE_LINES = 6
REMOTE_CANDIDATE = "/tmp/a33x-u0q-v2-emergency-ssh-recovery.img"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0p_flash = load("a33_u0q_v2_flash_u0p", U0P_FLASH_PATH)
builder_v2 = load("a33_u0q_v2_flash_builder", U0Q_BUILDER_V2_PATH)
audit_v2 = load("a33_u0q_v2_flash_audit", U0Q_AUDIT_V2_PATH)
base = u0p_flash.base
common = u0p_flash.common


class U0qV2FlashError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def require_source_blobs(repo: Path) -> None:
    for path, expected in (
        (U0P_FLASH_PATH, EXPECTED_U0P_FLASH_BLOB),
        (U0Q_BUILDER_V2_PATH, EXPECTED_U0Q_BUILDER_V2_BLOB),
        (U0Q_AUDIT_V2_PATH, EXPECTED_U0Q_AUDIT_V2_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0qV2FlashError(
                f"checked-in U0q v2 dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )


def local_evidence(root: Path, repo: Path) -> dict[str, object]:
    require_source_blobs(repo)
    inherited = u0p_flash.local_evidence(root, repo)

    manifest_path = (
        root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-manifest.txt"
    )
    patch_path = root / "build/u0q-emergency-ssh-patch.txt"
    base_audit_path = root / "build/a33-u0q-candidate-audit.txt"
    audit_v2_path = root / "build/a33-u0q-candidate-audit-v2.txt"
    candidate = (
        root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-recovery.img"
    )
    for path in (
        manifest_path,
        patch_path,
        base_audit_path,
        audit_v2_path,
        candidate,
    ):
        if not path.is_file():
            raise U0qV2FlashError(f"missing U0q v2 evidence: {path}")

    if candidate.stat().st_size != EXPECTED_CANDIDATE_SIZE:
        raise U0qV2FlashError(
            f"U0q v2 candidate size mismatch: {candidate.stat().st_size}"
        )
    candidate_sha = common.sha_file(candidate)
    manifest_sha = common.sha_file(manifest_path)
    patch_sha = common.sha_file(patch_path)
    base_audit_sha = common.sha_file(base_audit_path)
    audit_v2_sha = common.sha_file(audit_v2_path)

    manifest = common.kv(manifest_path)
    common.require(
        manifest,
        {
            "candidate": "U0q-emergency-ssh",
            "functional_base": "U0p-corrected-sshd-source-hash",
            "functional_delta": "independent-live-root-shell-on-port-2222",
            "normal_openrc_sshd_instrumentation_preserved": "yes",
            "emergency_sshd_port": "2222",
            "emergency_sshd_user": "root",
            "emergency_sshd_auth": "dedicated-ed25519-public-key-only",
            "emergency_sshd_pam": "disabled",
            "emergency_sshd_password_auth": "disabled",
            "emergency_sshd_process_root": "chroot-/sysroot",
            "emergency_network_address": "172.16.42.1/24",
            "emergency_trace_path": EMERGENCY_TRACE_PATH,
            "rootfs_persistent_delta_from_u0p": EMERGENCY_TRACE_PATH,
            "u0p_watchdog_hook_preserved": "yes",
            "u0q_runtime_revision": "2",
            "emergency_privsep_path": "/run/sshd",
            "emergency_privsep_backing": "preexisting-mounted-run",
            "emergency_pre_switch_root_gate": (
                "network-address-and-port-2222-listener"
            ),
            "emergency_pre_switch_root_timeout_seconds": "150",
            "emergency_network_ready_path": "/run/a33x-u0q-network-ready",
            "emergency_firewall_policy": "runtime-nft-monitor",
            "emergency_firewall_rule_comment": "a33x-u0q-emergency-2222",
            "emergency_firewall_persistent_delta": "none",
            "recovery_sha256": candidate_sha,
            "patch_report_sha256": patch_sha,
            "phone_partition_writes": "no",
            "build_status": "passed",
        },
        "U0q v2 manifest",
    )

    base_audit_values = common.kv(base_audit_path)
    common.require(
        base_audit_values,
        {
            "operation": "host-only-audit-u0q-emergency-ssh",
            "candidate_sha256": candidate_sha,
            "u0p_watchdog_hook_byte_identical": "yes",
            "normal_openrc_sshd_instrumentation_byte_identical": "yes",
            "emergency_sshd_chroot_contract": "passed",
            "long_lived_old_initramfs_root_reference": "no",
            "emergency_auth_public_key_only": "yes",
            "private_key_embedded": "no",
            "kernel_unchanged": "yes",
            "dtb_unchanged": "yes",
            "recovery_dtbo_unchanged": "yes",
            "kernel_cmdline_unchanged": "yes",
            "recovery_size_exact": "yes",
            "phone_partition_writes": "no",
            "audit_status": "passed",
        },
        "base U0q audit",
    )

    audit_values = common.kv(audit_v2_path)
    common.require(
        audit_values,
        {
            "operation": "host-only-audit-u0q-emergency-ssh-v2",
            "candidate_sha256": candidate_sha,
            "manifest_sha256": manifest_sha,
            "patch_report_sha256": patch_sha,
            "base_audit_report_sha256": base_audit_sha,
            "u0q_runtime_revision": "2",
            "emergency_pre_switch_root_gate": (
                "network-address-and-port-2222-listener"
            ),
            "emergency_pre_switch_root_timeout_seconds": "150",
            "runtime_directory_order_verified": "yes",
            "pre_switch_root_live_channel_gate_verified": "yes",
            "runtime_firewall_policy": "runtime-nft-monitor",
            "persistent_firewall_file_delta": "none",
            "normal_openrc_sshd_instrumentation_byte_identical": "yes",
            "u0p_watchdog_hook_byte_identical": "yes",
            "kernel_unchanged": "yes",
            "dtb_unchanged": "yes",
            "recovery_dtbo_unchanged": "yes",
            "kernel_cmdline_unchanged": "yes",
            "phone_partition_writes": "no",
            "audit_v2_status": "passed",
        },
        "U0q v2 audit",
    )

    private_key = Path(manifest.get("client_private_key", ""))
    public_key = Path(manifest.get("client_public_key", ""))
    if not private_key.is_file() or not public_key.is_file():
        raise U0qV2FlashError("U0q v2 emergency client keypair is missing")
    if common.sha_file(private_key) != manifest.get("client_private_key_sha256"):
        raise U0qV2FlashError("U0q v2 private key hash differs from manifest")
    if common.sha_file(public_key) != manifest.get("client_public_key_sha256"):
        raise U0qV2FlashError("U0q v2 public key hash differs from manifest")
    if stat.S_IMODE(private_key.stat().st_mode) != 0o600:
        raise U0qV2FlashError("U0q v2 private key mode must be 0600")

    commit = manifest.get("linuxa33_commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise U0qV2FlashError("invalid U0q manifest commit")
    ancestor = common.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, "HEAD"],
        check=False,
    )
    if ancestor.returncode != 0:
        raise U0qV2FlashError("U0q manifest commit is not an ancestor of HEAD")

    return {
        **inherited,
        "manifest_path": manifest_path,
        "patch_path": patch_path,
        "base_audit_path": base_audit_path,
        "audit_v2_path": audit_v2_path,
        "candidate": candidate,
        "candidate_sha": candidate_sha,
        "candidate_size": EXPECTED_CANDIDATE_SIZE,
        "manifest_sha": manifest_sha,
        "patch_sha": patch_sha,
        "base_audit_sha": base_audit_sha,
        "audit_v2_sha": audit_v2_sha,
        "private_key": private_key,
        "public_key": public_key,
        "private_key_sha": common.sha_file(private_key),
        "public_key_sha": common.sha_file(public_key),
    }


TRACE_BASELINE_SCRIPT = rf'''set -eu
target="$1"
expected_sha="$2"
expected_bytes="$3"
expected_lines="$4"
mountpoint=/tmp/a33x-u0q-v2-parent-trace
parent="$mountpoint{PARENT_TRACE_PATH}"
emergency="$mountpoint{EMERGENCY_TRACE_PATH}"
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
[ -f "$parent" ] || exit 90
[ ! -e "$emergency" ] || exit 91
actual_sha="$(sha256sum "$parent" | awk 'NR==1 {{print $1}}')"
actual_bytes="$(stat -c '%s' "$parent")"
actual_mode="$(stat -c '%a' "$parent")"
actual_uid="$(stat -c '%u' "$parent")"
actual_gid="$(stat -c '%g' "$parent")"
actual_lines="$(wc -l < "$parent" | tr -d ' ')"
[ "$actual_sha" = "$expected_sha" ] || exit 92
[ "$actual_bytes" = "$expected_bytes" ] || exit 93
[ "$actual_lines" = "$expected_lines" ] || exit 94
[ "$actual_mode" = 600 ] || exit 95
[ "$actual_uid" = 0 ] && [ "$actual_gid" = 0 ] || exit 96
grep -Fq 'candidate=U0p-corrected-sshd-source-hash stage=trace-open' "$parent" || exit 97
grep -Fq 'stage=setup-success' "$parent" || exit 98
grep -Fq 'stage=switch-root-ready' "$parent" || exit 99
grep -Fq 'event=script-loaded' "$parent" || exit 100
! grep -Fq 'error=' "$parent" || exit 101
umount "$mountpoint"
mounted=no
echo "u0q_parent_trace_state=known-u0p-openrc-script-loaded-boundary"
echo "u0q_parent_trace_sha256=$actual_sha"
echo "u0q_parent_trace_bytes=$actual_bytes"
echo "u0q_parent_trace_lines=$actual_lines"
echo "u0q_parent_trace_metadata=600:0:0"
echo "u0q_emergency_trace_baseline=absent"
echo "u0q_parent_trace_readonly_unmount=passed"
echo "userdata_persistent_writes=no"
'''


def validate_phone_rootfs(adb: str, serial: str, local: dict[str, object]) -> None:
    # Reuse exact rootfs, critical-file, PAM, runlevel and eight host-key checks,
    # but replace U0p's old U0o-failure trace requirement with the exact U0p trace.
    u0p_flash.u0o_flash.u0n_flash_v2.validate_phone_rootfs(adb, serial, local)

    state = base.block_helper.prepare(common, adb, serial)
    common.USERDATA = state.node
    print("exact_userdata_node_u0q_parent_trace_preparation=passed")
    try:
        output = common.adb_shell(
            adb,
            serial,
            TRACE_BASELINE_SCRIPT,
            state.node,
            KNOWN_U0P_TRACE_SHA256,
            str(KNOWN_U0P_TRACE_BYTES),
            str(KNOWN_U0P_TRACE_LINES),
        )
        required = (
            "u0q_parent_trace_state=known-u0p-openrc-script-loaded-boundary",
            f"u0q_parent_trace_sha256={KNOWN_U0P_TRACE_SHA256}",
            f"u0q_parent_trace_bytes={KNOWN_U0P_TRACE_BYTES}",
            f"u0q_parent_trace_lines={KNOWN_U0P_TRACE_LINES}",
            "u0q_parent_trace_metadata=600:0:0",
            "u0q_emergency_trace_baseline=absent",
            "u0q_parent_trace_readonly_unmount=passed",
            "userdata_persistent_writes=no",
        )
        for token in required:
            if output.count(token) != 1:
                raise U0qV2FlashError(f"U0q trace-baseline marker missing: {token}")
        final_values, final_sections = common.live_state(adb, serial)
        base.restore.assert_idle(final_values, final_sections)
        print("u0q_known_u0p_trace_baseline=passed")
    finally:
        cleanup_output = base.block_helper.cleanup(common, adb, serial, state)
        if cleanup_output.count("exact_block_node_cleanup_status=passed") != 1:
            raise U0qV2FlashError("U0q trace-baseline node cleanup failed")
        print("exact_userdata_node_u0q_parent_trace_cleanup=passed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Flash exact audited U0q v2 recovery after fail-closed rootfs, SSH, "
            "known U0p trace, emergency-key, audit and exact-TWRP validation"
        )
    )
    parser.add_argument("confirmation", nargs="?", default="")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    if args.preflight_only and args.confirmation:
        raise U0qV2FlashError("do not provide confirmation with --preflight-only")
    if not args.preflight_only and args.confirmation != CONFIRMATION:
        raise U0qV2FlashError(
            f"recovery write requires exact confirmation token: {CONFIRMATION}"
        )

    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    local = local_evidence(root, repo)
    print("u0q_v2_local_candidate_and_rescue_evidence=passed")
    for key in (
        "candidate_sha",
        "manifest_sha",
        "patch_sha",
        "base_audit_sha",
        "audit_v2_sha",
        "private_key_sha",
        "public_key_sha",
    ):
        print(f"{key}256={local[key]}" if key.endswith("_sha") else f"{key}={local[key]}")

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
            print("u0q_v2_flash_preflight_status=passed")
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
            raise U0qV2FlashError(f"staged U0q v2 identity mismatch: {remote!r}")

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
            raise U0qV2FlashError("U0q v2 recovery write did not report success")
        recovery_written = True

        report = root / "build/a33-u0q-v2-emergency-ssh-flash.txt"
        pairs = [
            ("created", datetime.now().astimezone().isoformat(timespec="microseconds")),
            ("operation", "flash-exact-u0q-v2-emergency-ssh"),
            ("implementation_language", "python3"),
            ("adb_serial", serial),
            ("candidate", local["candidate"]),
            ("candidate_sha256", local["candidate_sha"]),
            ("candidate_size", local["candidate_size"]),
            ("manifest", local["manifest_path"]),
            ("manifest_sha256", local["manifest_sha"]),
            ("patch_report", local["patch_path"]),
            ("patch_report_sha256", local["patch_sha"]),
            ("base_audit", local["base_audit_path"]),
            ("base_audit_sha256", local["base_audit_sha"]),
            ("audit_v2", local["audit_v2_path"]),
            ("audit_v2_sha256", local["audit_v2_sha"]),
            ("emergency_client_private_key", local["private_key"]),
            ("emergency_client_private_key_sha256", local["private_key_sha"]),
            ("emergency_client_public_key", local["public_key"]),
            ("emergency_client_public_key_sha256", local["public_key_sha"]),
            ("twrp_kernel_release", fingerprint["kernel_release"]),
            ("twrp_config_gz_sha256", fingerprint["config_gz_sha256"]),
            ("recovery_partname", recovery_state.partname),
            ("recovery_kernel_name", recovery_state.kernel_name),
            ("recovery_kernel_dev", recovery_state.kernel_dev),
            ("recovery_previous_sha256", common.KNOWN_TWRP_SHA256),
            ("recovery_partition_sha256", local["candidate_sha"]),
            (
                "rootfs_validation",
                "identity-critical-hashes-exact-host-keys-and-known-u0p-trace-passed",
            ),
            ("parent_trace_path", PARENT_TRACE_PATH),
            ("parent_trace_baseline", "known-u0p-openrc-script-loaded-boundary"),
            ("parent_trace_baseline_sha256", KNOWN_U0P_TRACE_SHA256),
            ("emergency_trace_path", EMERGENCY_TRACE_PATH),
            ("emergency_trace_baseline", "absent"),
            ("emergency_sshd_port", "2222"),
            ("pre_switch_root_gate", "network-address-and-port-2222-listener"),
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
                message += "; U0q v2 may already be installed"
            raise U0qV2FlashError(message)
        print("exact_recovery_node_cleanup=passed")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0qV2FlashError,
        u0p_flash.U0pFlashError,
        u0p_flash.u0o_flash.U0oFlashError,
        u0p_flash.u0o_flash.u0n_flash_v2.U0nFlashV2Error,
        base.U0nFlashError,
        base.restore.RestoreError,
        base.restore.cleanup.CleanupV2Error,
        base.restore.block_helper.ExactBlockNodeError,
        base.restore.identity_helper.Ext4IdentityError,
        base.recovery_helper.ExactRecoveryNodeError,
        base.rescue.RescueError,
        audit_v2.AuditV2Error,
        builder_v2.Refusal,
        common.Refusal,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"REFUSING U0q V2 FLASH: {exc}", file=sys.stderr)
        raise SystemExit(1)
