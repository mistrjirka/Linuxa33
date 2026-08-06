#!/usr/bin/env python3
from __future__ import annotations

import gzip
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_AUDIT_PATH = HERE / "audit-a33-u0q-candidate.py"
BUILDER_V3_PATH = HERE / "make-u0q-emergency-ssh-v3.py"
EXPECTED_BASE_AUDIT_BLOB = "f52f01d8c878ed24aaae3f508f6e8e82663971e3"
EXPECTED_BUILDER_V3_BLOB = "295f1979a5a411dfec5456b5929f50d4286b0e6f"
REPORT_NAME = "a33-u0q-candidate-audit-v3.txt"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base_audit = load("a33_u0q_v3_base_audit", BASE_AUDIT_PATH)
builder_v3 = load("a33_u0q_v3_builder", BUILDER_V3_PATH)
v2 = base_audit.v2


class AuditV3Error(RuntimeError):
    pass


def fail(message: str) -> None:
    raise AuditV3Error(message)


def git_blob(repo: Path, path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


V3_FIELDS = {
    "u0q_runtime_revision": "3",
    "emergency_runtime_mount_required": "/run",
    "emergency_privsep_path": "/run/sshd",
    "emergency_privsep_backing": "verified-or-created-tmpfs-run",
    "emergency_pre_switch_root_gate": "network-address-and-port-2222-listener",
    "emergency_pre_switch_root_timeout_seconds": "150",
    "emergency_network_ready_path": "/run/a33x-u0q-network-ready",
    "emergency_firewall_policy": "runtime-nft-monitor",
    "emergency_firewall_rule_comment": "a33x-u0q-emergency-2222",
    "emergency_firewall_persistent_delta": "none",
    "emergency_runtime_mount_policy": builder_v3.MOUNT_POLICY,
    "emergency_proc_backing": "verified-or-created-proc",
    "emergency_sys_backing": "verified-or-created-sysfs",
    "emergency_dev_backing": "verified-or-created-bind-dev",
    "emergency_devpts_backing": "verified-or-created-devpts",
    "emergency_run_backing": "verified-or-created-tmpfs",
    "emergency_persistent_mount_config_delta": "none",
}


def verify_payload_text(init_text: str) -> None:
    unique = (
        "event=runtime-mounts-ready",
        f"policy={builder_v3.MOUNT_POLICY}",
        "mount -t proc proc /sysroot/proc",
        "mount -t sysfs sysfs /sysroot/sys",
        "mount -o bind /dev /sysroot/dev",
        "mount -t devpts",
        "mount -t tmpfs",
        "event=runtime-directory-ready",
        "event=network-helper-spawned",
        "event=sshd-helper-spawned",
        "event=network-ready-marker-written",
        "event=pre-switch-root-ready",
        "emergency-channel-readiness-timeout",
        "nft insert rule inet filter input tcp dport 2222 accept",
    )
    for token in unique:
        if init_text.count(token) != 1:
            fail(f"U0q v3 token missing or duplicated: {token}")
    if init_text.count(builder_v3.v2.FIREWALL_COMMENT) != 2:
        fail("U0q v3 firewall marker count mismatch")
    if "run-is-not-a-mounted-runtime-filesystem" in init_text:
        fail("U0q v3 retained the unproven pre-mounted-run requirement")

    order = (
        init_text.index("candidate=U0q-emergency-ssh stage=trace-open"),
        init_text.index("event=runtime-mounts-ready"),
        init_text.index("event=runtime-directory-ready"),
        init_text.index("event=network-helper-spawned"),
        init_text.index("event=sshd-helper-spawned"),
        init_text.index("event=pre-switch-root-ready"),
        init_text.index('exec switch_root /sysroot "$init"'),
    )
    if tuple(sorted(order)) != order:
        fail("U0q v3 runtime mounts and live-channel gate are not before switch_root")

    for token in (
        "/etc/fstab",
        "/etc/nftables.d/",
        "/etc/nftables.nft",
        "mount -o remount,rw",
        "umount -l",
        "sed -i",
        "rm -rf /sysroot",
        "PasswordAuthentication=yes",
        "UsePAM=yes",
    ):
        if token in init_text:
            fail(f"persistent or unsafe U0q v3 token entered initramfs: {token}")


def main() -> int:
    root, repo = builder_v3.v2.selected_paths()
    for path, expected in (
        (BASE_AUDIT_PATH, EXPECTED_BASE_AUDIT_BLOB),
        (BUILDER_V3_PATH, EXPECTED_BUILDER_V3_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            fail(
                f"checked-in U0q v3 audit dependency changed: "
                f"path={path.name} actual={actual!r} expected={expected!r}"
            )

    old_network = base_audit.builder.network_script
    old_emergency = base_audit.builder.emergency_block
    try:
        base_audit.builder.network_script = builder_v3.v2.network_script
        base_audit.builder.emergency_block = builder_v3.emergency_block
        result = base_audit.main()
    finally:
        base_audit.builder.network_script = old_network
        base_audit.builder.emergency_block = old_emergency
    if result != 0:
        return result

    manifest_path = (
        root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-manifest.txt"
    )
    patch_path = root / "build/u0q-emergency-ssh-patch.txt"
    initramfs = root / "export-u0q-emergency-ssh/initramfs"
    candidate = (
        root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-recovery.img"
    )
    base_report = root / "build/a33-u0q-candidate-audit.txt"
    for path in (manifest_path, patch_path, initramfs, candidate, base_report):
        if not path.is_file():
            fail(f"missing U0q v3 audit input: {path}")

    manifest = v2.kv(manifest_path)
    patch = v2.kv(patch_path)
    v2.require(manifest, V3_FIELDS, "U0q v3 manifest")
    v2.require(patch, V3_FIELDS, "U0q v3 patch report")
    if v2.sha_file(patch_path) != manifest.get("patch_report_sha256"):
        fail("U0q v3 patch report differs from manifest")
    if v2.sha_file(candidate) != manifest.get("recovery_sha256"):
        fail("U0q v3 recovery differs from manifest")
    if candidate.stat().st_size != 100663296:
        fail(f"unexpected U0q v3 recovery size: {candidate.stat().st_size}")

    try:
        archive = v2.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        fail(f"cannot parse U0q v3 initramfs: {exc}")
    init_text = archive.one(base_audit.builder.INIT_TARGET).data.decode(
        "utf-8", errors="strict"
    )
    verify_payload_text(init_text)

    base_values = v2.kv(base_report)
    candidate_sha = v2.sha_file(candidate)
    v2.require(
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
        "base U0q v3 exact-delta audit",
    )

    report = root / "build" / REPORT_NAME
    rows: list[tuple[str, object]] = [
        ("operation", "host-only-audit-u0q-emergency-ssh-v3"),
        ("functional_base", "U0q-emergency-ssh-v2"),
        ("candidate", candidate),
        ("candidate_size", candidate.stat().st_size),
        ("candidate_sha256", candidate_sha),
        ("manifest", manifest_path),
        ("manifest_sha256", v2.sha_file(manifest_path)),
        ("patch_report", patch_path),
        ("patch_report_sha256", v2.sha_file(patch_path)),
        ("base_audit_report", base_report),
        ("base_audit_report_sha256", v2.sha_file(base_report)),
        *V3_FIELDS.items(),
        ("runtime_mount_order_verified", "yes"),
        ("pre_switch_root_live_channel_gate_verified", "yes"),
        ("persistent_mount_configuration_delta", "none"),
        ("persistent_firewall_file_delta", "none"),
        ("normal_openrc_sshd_instrumentation_byte_identical", "yes"),
        ("u0p_watchdog_hook_byte_identical", "yes"),
        ("kernel_unchanged", "yes"),
        ("dtb_unchanged", "yes"),
        ("recovery_dtbo_unchanged", "yes"),
        ("kernel_cmdline_unchanged", "yes"),
        ("phone_partition_writes", "no"),
        ("audit_v3_status", "passed"),
    ]
    v2.write_report(report, rows)
    print(f"report={report}")
    print(f"candidate_sha256={candidate_sha}")
    print(f"manifest_sha256={v2.sha_file(manifest_path)}")
    print(f"patch_report_sha256={v2.sha_file(patch_path)}")
    print(f"base_audit_report_sha256={v2.sha_file(base_report)}")
    print("runtime_mount_order_verified=yes")
    print("pre_switch_root_live_channel_gate_verified=yes")
    print("persistent_mount_configuration_delta=none")
    print("audit_v3_status=passed")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AuditV3Error,
        builder_v3.Refusal,
        builder_v3.v2.Refusal,
        base_audit.AuditError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"U0q V3 AUDIT FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
