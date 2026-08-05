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
BUILDER_PATH = HERE / "make-u0o-persistent-sshd-trace.py"
U0N_AUDIT_PATH = HERE / "audit-a33-u0n-candidate.py"
EXPECTED_BUILDER_BLOB = "56bee8bbf637fea7d0a077e1be2aed460dc85b7e"
EXPECTED_U0N_AUDIT_BLOB = "3152f2bbd504f842acd809156177b3c45cb7f800"
COMPONENTS_UNCHANGED = ("kernel", "dtb", "recovery_dtbo")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load("a33_u0o_audit_builder", BUILDER_PATH)
u0n_audit = load("a33_u0o_audit_parent", U0N_AUDIT_PATH)
v2 = builder.v2


class AuditError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise AuditError(message)


def compare_components(before: dict[str, Path], after: dict[str, Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in COMPONENTS_UNCHANGED:
        before_sha = v2.sha_file(before[name])
        after_sha = v2.sha_file(after[name])
        if before_sha != after_sha:
            fail(f"unexpected U0o recovery component delta: {name} before={before_sha} after={after_sha}")
        result[f"{name}_sha256"] = before_sha
    before_ramdisk = v2.sha_file(before["ramdisk"])
    after_ramdisk = v2.sha_file(after["ramdisk"])
    if before_ramdisk == after_ramdisk:
        fail("U0o ramdisk is identical to U0n")
    result.update(
        {
            "u0n_ramdisk_sha256": before_ramdisk,
            "u0o_ramdisk_sha256": after_ramdisk,
            "u0n_ramdisk_size": str(before["ramdisk"].stat().st_size),
            "u0o_ramdisk_size": str(after["ramdisk"].stat().st_size),
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Host-only exact-delta audit for U0o persistent SSH trace")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    for path, expected in (
        (BUILDER_PATH, EXPECTED_BUILDER_BLOB),
        (U0N_AUDIT_PATH, EXPECTED_U0N_AUDIT_BLOB),
    ):
        actual = builder.git_blob(repo, path)
        if actual != expected:
            fail(f"checked-in U0o audit dependency changed: {path.name} actual={actual!r} expected={expected!r}")

    parent_manifest_path, parent_initramfs, _ = builder.validate_parent(root, repo)
    parent_candidate = root / "build/candidates/a33x-h1-usbpd-u0n-real-boot-sshd-trace-recovery.img"
    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0o-persistent-sshd-trace-manifest.txt"
    patch_path = root / "build/u0o-persistent-sshd-trace-patch.txt"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0o-persistent-sshd-trace-recovery.img"
    initramfs = root / "export-u0o-persistent-sshd-trace/initramfs"
    for path in (parent_candidate, manifest_path, patch_path, candidate, initramfs):
        if not path.is_file():
            fail(f"missing U0o audit input: {path}")

    manifest = v2.kv(manifest_path)
    patch = v2.kv(patch_path)
    expected_common = {
        "implementation_language": "python3",
        "functional_base": "U0n-real-boot-sshd-trace",
        "cpio_entry_order_preserved": "yes",
        "cpio_payload_delta": builder.INIT_TARGET,
        "shell_delta": "duplicate-u0n-trace-to-one-persistent-file",
        "sshd_behavior_delta_from_u0n": "none",
        "snapshot_schedule_seconds": "0,1,2,5,10,20,30,60",
        "persistent_trace_path": builder.TRACE_PATH,
        "persistent_trace_mode": "0600",
        "persistent_trace_write_scope": "truncate-on-u0o-boot-and-append-u0n-events-only",
        "rootfs_persistent_delta": builder.TRACE_PATH,
        "u0n_watchdog_hook_preserved": "yes",
        "embedded_modules": "67",
        "kernel_cmdline_delta": "none",
        "module_delta": "none",
        "kernel_delta": "none",
        "dtb_delta": "none",
        "recovery_dtbo_delta": "none",
        "phone_partition_writes": "no",
    }
    v2.require(
        manifest,
        {
            "candidate": "U0o-persistent-sshd-trace",
            "functional_delta": "one-scoped-persistent-real-boot-trace-file",
            **expected_common,
            "preparation_status": "passed",
            "build_status": "passed",
        },
        "U0o manifest",
    )
    v2.require(
        patch,
        {
            "operation": "python-u0o-one-file-persistent-sshd-trace",
            **expected_common,
            "patch_status": "passed",
        },
        "U0o patch report",
    )
    if Path(manifest.get("recovery", "")).resolve() != candidate.resolve():
        fail("U0o manifest references an unexpected recovery")
    if Path(manifest.get("u0o_initramfs", "")).resolve() != initramfs.resolve():
        fail("U0o manifest references an unexpected initramfs")
    if candidate.stat().st_size != 100663296:
        fail(f"unexpected U0o recovery size: {candidate.stat().st_size}")
    if v2.sha_file(candidate) != manifest.get("recovery_sha256"):
        fail("U0o recovery differs from its manifest")
    if v2.sha_file(initramfs) != manifest.get("u0o_initramfs_sha256"):
        fail("U0o initramfs differs from its manifest")
    if v2.sha_file(patch_path) != manifest.get("patch_report_sha256"):
        fail("U0o patch report differs from its manifest")
    if v2.sha_file(parent_manifest_path) != manifest.get("u0n_manifest_sha256"):
        fail("U0o ancestry does not match exact U0n manifest")
    if manifest.get("u0n_initramfs_sha256") != builder.EXPECTED_U0N_INITRAMFS_SHA256:
        fail("U0o ancestry does not match exact U0n initramfs")

    try:
        before = v2.Archive.parse(gzip.decompress(parent_initramfs.read_bytes()))
        after = v2.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        fail(f"cannot parse U0n/U0o initramfs: {exc}")
    builder.assert_only_init_changed(before, after)
    if before.one(builder.WATCHDOG_TARGET).data != after.one(builder.WATCHDOG_TARGET).data:
        fail("U0o changed the proven U0n watchdog hook")
    original_init = before.one(builder.INIT_TARGET).data.decode("utf-8", errors="strict")
    expected_init = builder.patch_init_second(original_init)
    if after.one(builder.INIT_TARGET).data.decode("utf-8", errors="strict") != expected_init:
        fail("U0o init_2nd.sh is not the checked-in transformation")

    unpacker = root / "aosp-mkbootimg/unpack_bootimg.py"
    if not unpacker.is_file():
        fail(f"missing pinned recovery unpacker: {unpacker}")
    with tempfile.TemporaryDirectory(prefix="a33-u0o-audit-") as temporary:
        temp = Path(temporary)
        before_components = u0n_audit.u0m_audit.u0l_audit.unpack_recovery(
            unpacker, parent_candidate, temp / "u0n"
        )
        after_components = u0n_audit.u0m_audit.u0l_audit.unpack_recovery(
            unpacker, candidate, temp / "u0o"
        )
        component_hashes = compare_components(before_components, after_components)
        layout = u0n_audit.u0m_audit.u0l_audit.validate_boot_info_delta(
            (root / "build/pmos-debug-recovery-u0n-real-boot-sshd-trace/final-boot-info.txt").read_text(errors="strict"),
            (root / "build/pmos-debug-recovery-u0o-persistent-sshd-trace/final-boot-info.txt").read_text(errors="strict"),
            before_ramdisk_size=before_components["ramdisk"].stat().st_size,
            after_ramdisk_size=after_components["ramdisk"].stat().st_size,
        )

    output = root / "build/pmos-debug-recovery-u0o-persistent-sshd-trace"
    for path in (output / "avb-verify.txt", output / "avb-info.txt"):
        if not path.is_file() or not path.read_bytes():
            fail(f"missing U0o AVB evidence: {path}")

    report = root / "build/a33-u0o-candidate-audit.txt"
    rows: list[tuple[str, object]] = [
        ("operation", "host-only-audit-u0o-persistent-sshd-trace"),
        ("functional_base", "U0n-real-boot-sshd-trace"),
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
        ("u0n_watchdog_hook_byte_identical", "yes"),
        ("u0n_openrc_behavior_preserved", "yes"),
        ("persistent_trace_transformation_recomputed", "yes"),
        ("persistent_trace_path", builder.TRACE_PATH),
        ("persistent_trace_file_count", 1),
        ("persistent_trace_scope_verified", "yes"),
        ("kernel_unchanged", "yes"),
        ("dtb_unchanged", "yes"),
        ("recovery_dtbo_unchanged", "yes"),
        ("kernel_cmdline_unchanged", "yes"),
        ("recovery_size_exact", "yes"),
        ("rootfs_persistent_delta", builder.TRACE_PATH),
        ("phone_partition_writes", "no"),
        ("audit_status", "passed"),
    ]
    v2.write_report(report, rows)
    print(f"report={report}")
    print(f"candidate_sha256={v2.sha_file(candidate)}")
    print(f"persistent_trace_path={builder.TRACE_PATH}")
    print("audit_status=passed")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AuditError,
        builder.Refusal,
        u0n_audit.AuditError,
        v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"REFUSING U0o AUDIT: {exc}", file=sys.stderr)
        raise SystemExit(1)
