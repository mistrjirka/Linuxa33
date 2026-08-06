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
BUILDER_V2_PATH = HERE / "make-u0q-emergency-ssh-v2.py"
EXPECTED_BASE_AUDIT_BLOB = "f52f01d8c878ed24aaae3f508f6e8e82663971e3"
EXPECTED_BUILDER_V2_BLOB = "63d3d9c548847b6ad710f29844265359e401185d"
REPORT_NAME = "a33-u0q-candidate-audit-v2.txt"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base_audit = load("a33_u0q_v2_base_audit", BASE_AUDIT_PATH)
builder_v2 = load("a33_u0q_v2_builder", BUILDER_V2_PATH)
v2 = base_audit.v2


class AuditV2Error(RuntimeError):
    pass


def fail(message: str) -> None:
    raise AuditV2Error(message)


def git_blob(repo: Path, path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


V2_FIELDS = {
    "u0q_runtime_revision": builder_v2.RUNTIME_REVISION,
    "emergency_runtime_mount_required": "/run",
    "emergency_privsep_path": builder_v2.PRIVSEP_PATH,
    "emergency_privsep_backing": "preexisting-mounted-run",
    "emergency_pre_switch_root_gate": "network-address-and-port-2222-listener",
    "emergency_pre_switch_root_timeout_seconds": str(
        builder_v2.READY_TIMEOUT_SECONDS
    ),
    "emergency_network_ready_path": builder_v2.NETWORK_READY_PATH,
    "emergency_firewall_policy": "runtime-nft-monitor",
    "emergency_firewall_rule_comment": builder_v2.FIREWALL_COMMENT,
    "emergency_firewall_persistent_delta": "none",
}


def require_v2_fields(values: dict[str, str], label: str) -> None:
    v2.require(values, V2_FIELDS, label)


def verify_payload_text(init_text: str) -> None:
    unique = (
        "run-is-not-a-mounted-runtime-filesystem",
        f"event=runtime-directory-ready path={builder_v2.PRIVSEP_PATH}",
        f"event=network-ready-marker-written path={builder_v2.NETWORK_READY_PATH}",
        "event=pre-switch-root-ready",
        "emergency-channel-readiness-timeout",
        "nft insert rule inet filter input tcp dport 2222 accept",
        "event=runtime-firewall-rule-added",
    )
    for token in unique:
        if init_text.count(token) != 1:
            fail(f"U0q v2 generated token missing or duplicated: {token}")
    if init_text.count(builder_v2.FIREWALL_COMMENT) != 2:
        fail("U0q v2 firewall marker must occur in detection and inserted rule")

    order = (
        init_text.index("candidate=U0q-emergency-ssh stage=trace-open"),
        init_text.index("event=runtime-directory-ready path=/run/sshd"),
        init_text.index("event=network-helper-spawned"),
        init_text.index("event=config-test-start port=2222"),
        init_text.index("event=sshd-helper-spawned"),
        init_text.index("event=pre-switch-root-ready"),
        init_text.index('exec switch_root /sysroot "$init"'),
    )
    if tuple(sorted(order)) != order:
        fail("U0q v2 readiness gate is not ordered before switch_root")

    forbidden = (
        "/etc/nftables.d/",
        "/etc/nftables.nft",
        "mount -o remount,rw",
        "umount -l",
        "sed -i",
        "rm -rf /sysroot",
        "PasswordAuthentication=yes",
        "KbdInteractiveAuthentication=yes",
        "UsePAM=yes",
    )
    for token in forbidden:
        if token in init_text:
            fail(f"persistent or unsafe U0q v2 token entered initramfs: {token}")


def main() -> int:
    root, repo = builder_v2.selected_paths()
    for path, expected in (
        (BASE_AUDIT_PATH, EXPECTED_BASE_AUDIT_BLOB),
        (BUILDER_V2_PATH, EXPECTED_BUILDER_V2_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            fail(
                f"checked-in U0q v2 audit dependency changed: "
                f"path={path.name} actual={actual!r} expected={expected!r}"
            )

    original_network = base_audit.builder.network_script
    original_emergency = base_audit.builder.emergency_block
    try:
        base_audit.builder.network_script = builder_v2.network_script
        base_audit.builder.emergency_block = builder_v2.emergency_block
        result = base_audit.main()
    finally:
        base_audit.builder.network_script = original_network
        base_audit.builder.emergency_block = original_emergency
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
            fail(f"missing U0q v2 audit input: {path}")

    manifest = v2.kv(manifest_path)
    patch = v2.kv(patch_path)
    require_v2_fields(manifest, "U0q v2 manifest")
    require_v2_fields(patch, "U0q v2 patch report")
    if v2.sha_file(patch_path) != manifest.get("patch_report_sha256"):
        fail("U0q v2 patch report differs from the updated manifest")

    try:
        archive = v2.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        fail(f"cannot parse U0q v2 initramfs: {exc}")
    init_text = archive.one(base_audit.builder.INIT_TARGET).data.decode(
        "utf-8", errors="strict"
    )
    verify_payload_text(init_text)

    base_values = v2.kv(base_report)
    v2.require(
        base_values,
        {
            "candidate_sha256": v2.sha_file(candidate),
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
        "base U0q exact-delta audit",
    )

    report = root / "build" / REPORT_NAME
    rows: list[tuple[str, object]] = [
        ("operation", "host-only-audit-u0q-emergency-ssh-v2"),
        ("functional_base", "U0q-emergency-ssh"),
        ("candidate", candidate),
        ("candidate_size", candidate.stat().st_size),
        ("candidate_sha256", v2.sha_file(candidate)),
        ("manifest", manifest_path),
        ("manifest_sha256", v2.sha_file(manifest_path)),
        ("patch_report", patch_path),
        ("patch_report_sha256", v2.sha_file(patch_path)),
        ("base_audit_report", base_report),
        ("base_audit_report_sha256", v2.sha_file(base_report)),
        *V2_FIELDS.items(),
        ("runtime_directory_order_verified", "yes"),
        ("pre_switch_root_live_channel_gate_verified", "yes"),
        ("runtime_firewall_rule_count", "1"),
        ("runtime_firewall_marker_count", "2"),
        ("persistent_firewall_file_delta", "none"),
        ("normal_openrc_sshd_instrumentation_byte_identical", "yes"),
        ("u0p_watchdog_hook_byte_identical", "yes"),
        ("kernel_unchanged", "yes"),
        ("dtb_unchanged", "yes"),
        ("recovery_dtbo_unchanged", "yes"),
        ("kernel_cmdline_unchanged", "yes"),
        ("phone_partition_writes", "no"),
        ("audit_v2_status", "passed"),
    ]
    v2.write_report(report, rows)
    print(f"report={report}")
    print(f"candidate_sha256={v2.sha_file(candidate)}")
    print(f"manifest_sha256={v2.sha_file(manifest_path)}")
    print(f"patch_report_sha256={v2.sha_file(patch_path)}")
    print(f"base_audit_report_sha256={v2.sha_file(base_report)}")
    print(f"u0q_runtime_revision={builder_v2.RUNTIME_REVISION}")
    print("runtime_directory_order_verified=yes")
    print("pre_switch_root_live_channel_gate_verified=yes")
    print("runtime_firewall_policy=runtime-nft-monitor")
    print("persistent_firewall_file_delta=none")
    print("audit_v2_status=passed")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AuditV2Error,
        builder_v2.Refusal,
        base_audit.AuditError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"U0q V2 AUDIT FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
