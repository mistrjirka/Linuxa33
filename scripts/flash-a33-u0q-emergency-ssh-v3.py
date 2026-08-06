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
V2_FLASH_PATH = HERE / "flash-a33-u0q-emergency-ssh-v2.py"
BUILDER_V3_PATH = HERE / "make-u0q-emergency-ssh-v3.py"
AUDIT_V3_PATH = HERE / "audit-a33-u0q-candidate-v3.py"
EXPECTED_V2_FLASH_BLOB = "333036c0bd13e68b17cbb83c0e978dd07ae308a6"
EXPECTED_BUILDER_V3_BLOB = "295f1979a5a411dfec5456b5929f50d4286b0e6f"
EXPECTED_AUDIT_V3_BLOB = "4fd86baa144355e7d8aae75a8bd5975873916eda"

CONFIRMATION = "FLASH-EXACT-U0Q-V3-RECOVERY"
EXPECTED_CANDIDATE_SIZE = 100663296
REMOTE_CANDIDATE = "/tmp/a33x-u0q-v3-emergency-ssh-recovery.img"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v2_flash = load("a33_u0q_v3_flash_parent", V2_FLASH_PATH)
builder_v3 = load("a33_u0q_v3_flash_builder", BUILDER_V3_PATH)
audit_v3 = load("a33_u0q_v3_flash_audit", AUDIT_V3_PATH)
base = v2_flash.base
common = v2_flash.common


class U0qV3FlashError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def local_evidence(root: Path, repo: Path) -> dict[str, object]:
    for path, expected in (
        (V2_FLASH_PATH, EXPECTED_V2_FLASH_BLOB),
        (BUILDER_V3_PATH, EXPECTED_BUILDER_V3_BLOB),
        (AUDIT_V3_PATH, EXPECTED_AUDIT_V3_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0qV3FlashError(
                f"checked-in U0q v3 dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    inherited = v2_flash.u0p_flash.local_evidence(root, repo)
    manifest_path = (
        root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-manifest.txt"
    )
    patch_path = root / "build/u0q-emergency-ssh-patch.txt"
    base_audit_path = root / "build/a33-u0q-candidate-audit.txt"
    audit_v3_path = root / "build/a33-u0q-candidate-audit-v3.txt"
    candidate = (
        root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-recovery.img"
    )
    for path in (
        manifest_path,
        patch_path,
        base_audit_path,
        audit_v3_path,
        candidate,
    ):
        if not path.is_file():
            raise U0qV3FlashError(f"missing U0q v3 evidence: {path}")

    if candidate.stat().st_size != EXPECTED_CANDIDATE_SIZE:
        raise U0qV3FlashError(
            f"U0q v3 candidate size mismatch: {candidate.stat().st_size}"
        )
    candidate_sha = common.sha_file(candidate)
    manifest_sha = common.sha_file(manifest_path)
    patch_sha = common.sha_file(patch_path)
    base_audit_sha = common.sha_file(base_audit_path)
    audit_v3_sha = common.sha_file(audit_v3_path)

    manifest = common.kv(manifest_path)
    common.require(
        manifest,
        {
            "candidate": "U0q-emergency-ssh",
            "functional_base": "U0p-corrected-sshd-source-hash",
            "functional_delta": "independent-live-root-shell-on-port-2222",
            "normal_openrc_sshd_instrumentation_preserved": "yes",
            "emergency_sshd_port": "2222",
            "emergency_sshd_auth": "dedicated-ed25519-public-key-only",
            "emergency_sshd_pam": "disabled",
            "emergency_sshd_password_auth": "disabled",
            "emergency_sshd_process_root": "chroot-/sysroot",
            "emergency_network_address": "172.16.42.1/24",
            "emergency_trace_path": v2_flash.EMERGENCY_TRACE_PATH,
            "rootfs_persistent_delta_from_u0p": v2_flash.EMERGENCY_TRACE_PATH,
            "u0p_watchdog_hook_preserved": "yes",
            "u0q_runtime_revision": "3",
            "emergency_privsep_path": "/run/sshd",
            "emergency_privsep_backing": "verified-or-created-tmpfs-run",
            "emergency_pre_switch_root_gate": (
                "network-address-and-port-2222-listener"
            ),
            "emergency_runtime_mount_policy": builder_v3.MOUNT_POLICY,
            "emergency_proc_backing": "verified-or-created-proc",
            "emergency_sys_backing": "verified-or-created-sysfs",
            "emergency_dev_backing": "verified-or-created-bind-dev",
            "emergency_devpts_backing": "verified-or-created-devpts",
            "emergency_run_backing": "verified-or-created-tmpfs",
            "emergency_persistent_mount_config_delta": "none",
            "emergency_firewall_policy": "runtime-nft-monitor",
            "emergency_firewall_persistent_delta": "none",
            "recovery_sha256": candidate_sha,
            "patch_report_sha256": patch_sha,
            "phone_partition_writes": "no",
            "build_status": "passed",
        },
        "U0q v3 manifest",
    )

    base_values = common.kv(base_audit_path)
    common.require(
        base_values,
        {
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
        "base U0q v3 audit",
    )

    audit_values = common.kv(audit_v3_path)
    common.require(
        audit_values,
        {
            "operation": "host-only-audit-u0q-emergency-ssh-v3",
            "candidate_sha256": candidate_sha,
            "manifest_sha256": manifest_sha,
            "patch_report_sha256": patch_sha,
            "base_audit_report_sha256": base_audit_sha,
            "u0q_runtime_revision": "3",
            "emergency_runtime_mount_policy": builder_v3.MOUNT_POLICY,
            "emergency_privsep_backing": "verified-or-created-tmpfs-run",
            "runtime_mount_order_verified": "yes",
            "pre_switch_root_live_channel_gate_verified": "yes",
            "persistent_mount_configuration_delta": "none",
            "persistent_firewall_file_delta": "none",
            "normal_openrc_sshd_instrumentation_byte_identical": "yes",
            "u0p_watchdog_hook_byte_identical": "yes",
            "kernel_unchanged": "yes",
            "dtb_unchanged": "yes",
            "recovery_dtbo_unchanged": "yes",
            "kernel_cmdline_unchanged": "yes",
            "phone_partition_writes": "no",
            "audit_v3_status": "passed",
        },
        "U0q v3 audit",
    )

    private_key = Path(manifest.get("client_private_key", ""))
    public_key = Path(manifest.get("client_public_key", ""))
    if not private_key.is_file() or not public_key.is_file():
        raise U0qV3FlashError("U0q v3 emergency keypair is missing")
    private_sha = common.sha_file(private_key)
    public_sha = common.sha_file(public_key)
    if private_sha != manifest.get("client_private_key_sha256"):
        raise U0qV3FlashError("U0q v3 private key hash differs from manifest")
    if public_sha != manifest.get("client_public_key_sha256"):
        raise U0qV3FlashError("U0q v3 public key hash differs from manifest")
    if stat.S_IMODE(private_key.stat().st_mode) != 0o600:
        raise U0qV3FlashError("U0q v3 private key mode must be 0600")

    commit = manifest.get("linuxa33_commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise U0qV3FlashError("invalid U0q v3 manifest commit")
    ancestor = common.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, "HEAD"],
        check=False,
    )
    if ancestor.returncode != 0:
        raise U0qV3FlashError("U0q v3 manifest commit is not an ancestor of HEAD")

    return {
        **inherited,
        "manifest_path": manifest_path,
        "patch_path": patch_path,
        "base_audit_path": base_audit_path,
        "audit_v3_path": audit_v3_path,
        "candidate": candidate,
        "candidate_sha": candidate_sha,
        "candidate_size": EXPECTED_CANDIDATE_SIZE,
        "manifest_sha": manifest_sha,
        "patch_sha": patch_sha,
        "base_audit_sha": base_audit_sha,
        "audit_v3_sha": audit_v3_sha,
        "private_key": private_key,
        "public_key": public_key,
        "private_key_sha": private_sha,
        "public_key_sha": public_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flash exact audited U0q v3 recovery, recovery partition only"
    )
    parser.add_argument("confirmation", nargs="?", default="")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    if args.preflight_only and args.confirmation:
        raise U0qV3FlashError("do not provide confirmation with --preflight-only")
    if not args.preflight_only and args.confirmation != CONFIRMATION:
        raise U0qV3FlashError(
            f"recovery write requires exact confirmation token: {CONFIRMATION}"
        )

    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    local = local_evidence(root, repo)
    print("u0q_v3_local_candidate_and_rescue_evidence=passed")
    print(f"candidate_sha256={local['candidate_sha']}")
    print(f"manifest_sha256={local['manifest_sha']}")
    print(f"patch_report_sha256={local['patch_sha']}")
    print(f"base_audit_sha256={local['base_audit_sha']}")
    print(f"audit_v3_sha256={local['audit_v3_sha']}")
    print(f"emergency_client_private_key_sha256={local['private_key_sha']}")
    print(f"emergency_client_public_key_sha256={local['public_key_sha']}")

    serial = common.select_recovery(adb, 30)
    fingerprint = base.restore.cleanup.validate_runtime_fingerprint(adb, serial)
    v2_flash.validate_phone_rootfs(adb, serial, local)
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
            print("u0q_v3_flash_preflight_status=passed")
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
            raise U0qV3FlashError(f"staged U0q v3 identity mismatch: {remote!r}")

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
            raise U0qV3FlashError("U0q v3 recovery write did not report success")
        recovery_written = True

        report = root / "build/a33-u0q-v3-emergency-ssh-flash.txt"
        pairs = [
            ("created", datetime.now().astimezone().isoformat(timespec="microseconds")),
            ("operation", "flash-exact-u0q-v3-emergency-ssh"),
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
            ("audit_v3", local["audit_v3_path"]),
            ("audit_v3_sha256", local["audit_v3_sha"]),
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
            ("parent_trace_path", v2_flash.PARENT_TRACE_PATH),
            ("parent_trace_baseline", "known-u0p-openrc-script-loaded-boundary"),
            ("parent_trace_baseline_sha256", v2_flash.KNOWN_U0P_TRACE_SHA256),
            ("emergency_trace_path", v2_flash.EMERGENCY_TRACE_PATH),
            ("emergency_trace_baseline", "absent"),
            ("emergency_sshd_port", "2222"),
            ("runtime_mount_policy", builder_v3.MOUNT_POLICY),
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
                message += "; U0q v3 may already be installed"
            raise U0qV3FlashError(message)
        print("exact_recovery_node_cleanup=passed")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0qV3FlashError,
        v2_flash.U0qV2FlashError,
        v2_flash.u0p_flash.U0pFlashError,
        v2_flash.u0p_flash.u0o_flash.U0oFlashError,
        v2_flash.u0p_flash.u0o_flash.u0n_flash_v2.U0nFlashV2Error,
        base.U0nFlashError,
        base.restore.RestoreError,
        base.restore.cleanup.CleanupV2Error,
        base.restore.block_helper.ExactBlockNodeError,
        base.restore.identity_helper.Ext4IdentityError,
        base.recovery_helper.ExactRecoveryNodeError,
        base.rescue.RescueError,
        audit_v3.AuditV3Error,
        builder_v3.Refusal,
        common.Refusal,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"REFUSING U0q V3 FLASH: {exc}", file=sys.stderr)
        raise SystemExit(1)
