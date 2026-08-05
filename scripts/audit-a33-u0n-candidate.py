#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
BUILDER = HERE / "make-u0n-real-boot-sshd-trace.py"
U0M_FLASH = HERE / "flash-a33-u0m-watchdog-magic-close-v4.py"
U0M_AUDIT = HERE / "audit-a33-u0m-candidate-v4.py"
EXPECTED_BUILDER_BLOB = "9b72b0ee3252f90d33f2cb6000210edfd35dd9cd"
EXPECTED_U0M_FLASH_BLOB = "a4523f358e853026279bc780feeb3c5306c2ea29"
EXPECTED_U0M_AUDIT_BLOB = "b58d76df2681df7a23e589eb50760d8f26e99d59"
COMPONENTS_UNCHANGED = ("kernel", "dtb", "recovery_dtbo")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load("a33_u0n_audit_builder", BUILDER)
u0m_flash = load("a33_u0n_audit_parent_flash", U0M_FLASH)
u0m_audit_v4 = load("a33_u0n_audit_parent_audit", U0M_AUDIT)
u0m_audit = u0m_audit_v4.base
v2 = builder.v2


class AuditError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise AuditError(message)


def require_sha(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        fail(f"invalid SHA256 for {label}: {value!r}")


def compare_components(before: dict[str, Path], after: dict[str, Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in COMPONENTS_UNCHANGED:
        before_sha = v2.sha_file(before[name])
        after_sha = v2.sha_file(after[name])
        if before_sha != after_sha:
            fail(
                f"unexpected U0n recovery component delta: {name} "
                f"before={before_sha} after={after_sha}"
            )
        result[f"{name}_sha256"] = before_sha
    before_ramdisk = v2.sha_file(before["ramdisk"])
    after_ramdisk = v2.sha_file(after["ramdisk"])
    if before_ramdisk == after_ramdisk:
        fail("U0n ramdisk is identical to U0m")
    result.update(
        {
            "u0m_ramdisk_sha256": before_ramdisk,
            "u0n_ramdisk_sha256": after_ramdisk,
            "u0m_ramdisk_size": str(before["ramdisk"].stat().st_size),
            "u0n_ramdisk_size": str(after["ramdisk"].stat().st_size),
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Host-only exact-delta audit for U0n real-boot sshd trace"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--debugfs", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    debugfs = (
        args.debugfs.expanduser().resolve()
        if args.debugfs is not None
        else Path(shutil.which("debugfs") or "")
    )
    if not debugfs.is_file():
        fail("debugfs is unavailable")

    for path, expected in (
        (BUILDER, EXPECTED_BUILDER_BLOB),
        (U0M_FLASH, EXPECTED_U0M_FLASH_BLOB),
        (U0M_AUDIT, EXPECTED_U0M_AUDIT_BLOB),
    ):
        if builder.git_blob(repo, path) != expected:
            fail(f"checked-in U0n audit dependency changed: {path.name}")

    parent = u0m_flash.base.validate_local(root, repo)
    parent_candidate = Path(parent["candidate"])
    parent_manifest_path = Path(parent["manifest_path"])
    parent_manifest = v2.kv(parent_manifest_path)
    parent_initramfs = Path(parent_manifest.get("u0m_initramfs", ""))

    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0n-real-boot-sshd-trace-manifest.txt"
    patch_path = root / "build/u0n-real-boot-sshd-trace-patch.txt"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0n-real-boot-sshd-trace-recovery.img"
    initramfs = root / "export-u0n-real-boot-sshd-trace/initramfs"
    output = root / "build/pmos-debug-recovery-u0n-real-boot-sshd-trace"
    for path in (manifest_path, patch_path, candidate, initramfs):
        if not path.is_file():
            fail(f"missing U0n evidence: {path}")

    manifest = v2.kv(manifest_path)
    patch = v2.kv(patch_path)
    expected_common = {
        "implementation_language": "python3",
        "functional_base": "U0m-watchdog-magic-close",
        "cpio_entry_order_preserved": "yes",
        "cpio_payload_delta": builder.INIT_TARGET,
        "shell_delta": "bind-instrument-exact-sshd-openrc-script-before-switch-root",
        "rootfs_persistent_delta": "none",
        "runtime_mount_delta": "retain-u0l-cgroup-mask-and-bind-instrumented-sshd-init",
        "sshd_init_path": builder.SSHD_INIT_PATH,
        "sshd_init_original_sha256": builder.EXPECTED_SSHD_INIT_SHA256,
        "sshd_behavior_delta": "logging-wrappers-and-detached-snapshot-monitor-only",
        "snapshot_schedule_seconds": "0,1,2,5,10,20,30,60",
        "snapshot_outputs": "kmsg-pid-listener-openrc-process-nft",
        "splash_mode": "best-effort-initramfs-ppm-before-switch-root",
        "splash_failure_behavior": "continue-boot",
        "u0m_watchdog_hook_preserved": "yes",
        "embedded_modules": "67",
        "kernel_cmdline_delta": "none",
        "module_delta": "none",
        "kernel_delta": "none",
        "dtb_delta": "none",
        "recovery_dtbo_delta": "none",
        "userdata_write": "none",
        "phone_partition_writes": "no",
    }
    v2.require(
        manifest,
        {
            "candidate": "U0n-real-boot-sshd-trace",
            "functional_delta": "real-default-runlevel-sshd-openrc-kmsg-instrumentation",
            **expected_common,
            "preparation_status": "passed",
            "build_status": "passed",
        },
        "U0n manifest",
    )
    v2.require(
        patch,
        {
            "operation": "python-u0n-real-boot-sshd-trace",
            **expected_common,
            "patch_status": "passed",
        },
        "U0n patch report",
    )

    if Path(manifest.get("recovery", "")).resolve() != candidate.resolve():
        fail("U0n manifest references an unexpected recovery")
    if Path(manifest.get("u0n_initramfs", "")).resolve() != initramfs.resolve():
        fail("U0n manifest references an unexpected initramfs")
    if Path(manifest.get("u0m_manifest", "")).resolve() != parent_manifest_path.resolve():
        fail("U0n manifest references an unexpected U0m manifest")
    if candidate.stat().st_size != 100663296:
        fail(f"unexpected U0n recovery size: {candidate.stat().st_size}")
    if v2.sha_file(candidate) != manifest.get("recovery_sha256"):
        fail("U0n recovery differs from its manifest")
    if v2.sha_file(initramfs) != manifest.get("u0n_initramfs_sha256"):
        fail("U0n initramfs differs from its manifest")
    if v2.sha_file(patch_path) != manifest.get("patch_report_sha256"):
        fail("U0n patch report differs from its manifest")
    if v2.sha_file(parent_manifest_path) != manifest.get("u0m_manifest_sha256"):
        fail("U0n parent manifest identity mismatch")
    if v2.sha_file(parent_initramfs) != manifest.get("u0m_initramfs_sha256"):
        fail("U0n parent initramfs identity mismatch")

    for key in (
        "u0m_manifest_sha256",
        "u0m_initramfs_sha256",
        "u0n_initramfs_sha256",
        "sshd_init_original_sha256",
        "sshd_init_instrumented_sha256",
        "original_init_2nd_sha256",
        "patched_init_2nd_sha256",
    ):
        require_sha(manifest.get(key, ""), key)
        if patch.get(key) != manifest.get(key):
            fail(f"U0n manifest and patch report disagree on {key}")

    rootfs_image = root / builder.ROOTFS_IMAGE
    sshd_original_bytes = builder.read_debugfs_file(debugfs, rootfs_image, builder.SSHD_INIT_PATH)
    sshd_original = sshd_original_bytes.decode("utf-8", errors="strict")
    sshd_instrumented = builder.instrument_sshd_init(sshd_original)
    if v2.sha_bytes(sshd_instrumented.encode()) != manifest.get("sshd_init_instrumented_sha256"):
        fail("U0n instrumented sshd hash is not the checked-in transformation")

    try:
        before = v2.Archive.parse(gzip.decompress(parent_initramfs.read_bytes()))
        after = v2.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        fail(f"cannot parse U0m/U0n initramfs: {exc}")
    builder.assert_only_init_changed(before, after)
    if before.one(builder.WATCHDOG_TARGET).data != after.one(builder.WATCHDOG_TARGET).data:
        fail("U0n changed the U0m watchdog hook")
    original_init = before.one(builder.INIT_TARGET).data.decode("utf-8", errors="strict")
    expected_init = builder.patch_init_second(original_init, sshd_instrumented)
    if after.one(builder.INIT_TARGET).data != expected_init.encode():
        fail("U0n init_2nd.sh is not the checked-in transformation")

    unpacker = root / "aosp-mkbootimg/unpack_bootimg.py"
    if not unpacker.is_file():
        fail(f"missing pinned recovery unpacker: {unpacker}")
    with tempfile.TemporaryDirectory(prefix="a33-u0n-audit-") as temporary:
        temp = Path(temporary)
        before_components = u0m_audit.u0l_audit.unpack_recovery(
            unpacker, parent_candidate, temp / "u0m"
        )
        after_components = u0m_audit.u0l_audit.unpack_recovery(
            unpacker, candidate, temp / "u0n"
        )
        component_hashes = compare_components(before_components, after_components)
        layout = u0m_audit.u0l_audit.validate_boot_info_delta(
            (
                root
                / "build/pmos-debug-recovery-u0m-watchdog-magic-close/final-boot-info.txt"
            ).read_text(errors="strict"),
            (output / "final-boot-info.txt").read_text(errors="strict"),
            before_ramdisk_size=before_components["ramdisk"].stat().st_size,
            after_ramdisk_size=after_components["ramdisk"].stat().st_size,
        )

    for path in (output / "avb-verify.txt", output / "avb-info.txt"):
        if not path.is_file() or not path.read_bytes():
            fail(f"missing U0n AVB evidence: {path}")

    report = root / "build/a33-u0n-candidate-audit.txt"
    rows: list[tuple[str, object]] = [
        ("operation", "host-only-audit-u0n-real-boot-sshd-trace"),
        ("functional_base", "U0m-watchdog-magic-close"),
        ("candidate", candidate),
        ("candidate_size", candidate.stat().st_size),
        ("candidate_sha256", v2.sha_file(candidate)),
        ("manifest", manifest_path),
        ("manifest_sha256", v2.sha_file(manifest_path)),
        ("patch_report", patch_path),
        ("patch_report_sha256", v2.sha_file(patch_path)),
        ("initramfs_payload_delta", "init_2nd-only"),
        ("recovery_component_delta", "ramdisk-and-avb-authentication-only"),
        *sorted(component_hashes.items()),
        *sorted(layout.items()),
        ("u0m_watchdog_hook_byte_identical", "yes"),
        ("openrc_default_start_stop_semantics_preserved", "yes"),
        ("instrumented_sshd_transformation_recomputed", "yes"),
        ("snapshot_schedule_verified", "yes"),
        ("splash_best_effort_verified", "yes"),
        ("kernel_unchanged", "yes"),
        ("dtb_unchanged", "yes"),
        ("recovery_dtbo_unchanged", "yes"),
        ("kernel_cmdline_unchanged", "yes"),
        ("recovery_size_exact", "yes"),
        ("rootfs_persistent_delta", "none"),
        ("phone_partition_writes", "no"),
        ("audit_status", "passed"),
    ]
    v2.write_report(report, rows)
    print(f"report={report}")
    print(f"candidate_sha256={v2.sha_file(candidate)}")
    print("audit_status=passed")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AuditError,
        builder.Refusal,
        builder.u0m_core.Refusal,
        builder.u0m_core.u0l.Refusal,
        builder.u0m_core.u0l.u0k.Refusal,
        builder.u0m_core.u0l.u0k.u0j.Refusal,
        u0m_audit.AuditError,
        v2.Refusal,
        v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"REFUSING U0n AUDIT: {exc}", file=sys.stderr)
        raise SystemExit(1)
