#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
U0M_BUILDER = HERE / "make-u0m-watchdog-magic-close.py"
U0L_FLASH = HERE / "flash-a33-u0l-openrc-cgroup-isolation.py"
U0L_AUDIT = HERE / "audit-a33-u0l-candidate.py"
EXPECTED_U0M_BUILDER_BLOB = "4ca8535ec430c171906b581f1e5f34073b852ba9"
EXPECTED_U0L_FLASH_BLOB = "0c8ed99e7d1e75b42cf54921f7f217cad6c4f845"
EXPECTED_U0L_AUDIT_BLOB = "030c6313f133d5e1b7fef0be59ff1e54f65bc420"
COMPONENTS_UNCHANGED = ("kernel", "dtb", "recovery_dtbo")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0m = load("a33_u0m_audit_builder", U0M_BUILDER)
u0l_flash = load("a33_u0m_audit_parent", U0L_FLASH)
u0l_audit = load("a33_u0m_audit_layout", U0L_AUDIT)
v2 = u0m.v2


class AuditError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise AuditError(message)


def compare_component_sets(
    before: dict[str, Path], after: dict[str, Path]
) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in COMPONENTS_UNCHANGED:
        before_sha = v2.sha_file(before[name])
        after_sha = v2.sha_file(after[name])
        if before_sha != after_sha:
            fail(
                f"unexpected U0m recovery component delta: {name} "
                f"before={before_sha} after={after_sha}"
            )
        result[f"{name}_sha256"] = before_sha
    before_ramdisk = v2.sha_file(before["ramdisk"])
    after_ramdisk = v2.sha_file(after["ramdisk"])
    if before_ramdisk == after_ramdisk:
        fail("U0m ramdisk is identical to U0l")
    result["u0l_ramdisk_sha256"] = before_ramdisk
    result["u0m_ramdisk_sha256"] = after_ramdisk
    result["u0l_ramdisk_size"] = str(before["ramdisk"].stat().st_size)
    result["u0m_ramdisk_size"] = str(after["ramdisk"].stat().st_size)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Host-only exact-delta audit for U0m watchdog magic-close recovery"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    for path, expected in (
        (U0M_BUILDER, EXPECTED_U0M_BUILDER_BLOB),
        (U0L_FLASH, EXPECTED_U0L_FLASH_BLOB),
        (U0L_AUDIT, EXPECTED_U0L_AUDIT_BLOB),
    ):
        if u0m.u0l.u0k.u0j.git_blob(repo, path) != expected:
            fail(f"checked-in U0m audit dependency changed: {path.name}")

    parent = u0l_flash.validate_local(root, repo)
    u0l_candidate = Path(parent["candidate"])
    u0l_manifest_path = Path(parent["manifest_path"])
    u0l_manifest = v2.kv(u0l_manifest_path)

    manifest_path = (
        root / "build/candidates/a33x-h1-usbpd-u0m-watchdog-magic-close-manifest.txt"
    )
    patch_path = root / "build/u0m-watchdog-magic-close-patch.txt"
    expected_candidate = (
        root / "build/candidates/a33x-h1-usbpd-u0m-watchdog-magic-close-recovery.img"
    )
    expected_initramfs = root / "export-u0m-watchdog-magic-close/initramfs"
    for path in (manifest_path, patch_path, expected_candidate, expected_initramfs):
        if not path.is_file():
            fail(f"missing U0m build evidence: {path}")

    manifest = v2.kv(manifest_path)
    patch = v2.kv(patch_path)
    expected_contract = {
        "candidate": "U0m-watchdog-magic-close",
        "functional_base": "U0l-openrc-cgroup-isolation",
        "functional_delta": (
            "verified-watchdog-magic-close-before-switch-root-with-fail-closed-feeder"
        ),
        "implementation_language": "python3",
        "cpio_entry_order_preserved": "yes",
        "cpio_payload_delta": "hooks/01-a33x-watchdog.sh,init_2nd.sh",
        "shell_delta": "verified-watchdog-magic-close-before-switch-root",
        "watchdog_device": "/dev/watchdog0",
        "watchdog_magic_close_byte": "V",
        "watchdog_nowayout_required": "0",
        "watchdog_state_before_required": "active",
        "watchdog_state_after_required": "inactive",
        "watchdog_failure_behavior": "continue-feeding-and-refuse-switch-root",
        "rootfs_persistent_delta": "none",
        "runtime_mount_delta": "retain-u0l-openrc-cgroup-mask",
        "embedded_modules": "67",
        "kernel_cmdline_delta": "none",
        "module_delta": "none",
        "kernel_delta": "none",
        "dtb_delta": "none",
        "recovery_dtbo_delta": "none",
        "userdata_write": "none",
        "phone_partition_writes": "no",
        "preparation_status": "passed",
        "build_status": "passed",
    }
    v2.require(manifest, expected_contract, "U0m manifest")
    if patch.get("operation") != "python-u0m-verified-watchdog-magic-close":
        fail("unexpected U0m patch operation")
    if patch.get("patch_status") != "passed":
        fail("U0m patch report did not pass")

    candidate = Path(manifest.get("recovery", ""))
    initramfs = Path(manifest.get("u0m_initramfs", ""))
    if candidate.resolve() != expected_candidate.resolve():
        fail("U0m manifest references an unexpected recovery path")
    if initramfs.resolve() != expected_initramfs.resolve():
        fail("U0m manifest references an unexpected initramfs path")
    if not candidate.is_file() or candidate.stat().st_size != 100663296:
        fail("U0m recovery is missing or has unexpected size")
    if v2.sha_file(candidate) != manifest.get("recovery_sha256"):
        fail("U0m recovery differs from its manifest")
    if not initramfs.is_file() or v2.sha_file(initramfs) != manifest.get(
        "u0m_initramfs_sha256"
    ):
        fail("U0m initramfs differs from its manifest")
    if v2.sha_file(u0l_manifest_path) != manifest.get("u0l_manifest_sha256"):
        fail("U0m ancestry does not match exact U0l manifest")
    if manifest.get("u0l_initramfs_sha256") != u0l_manifest.get(
        "u0l_initramfs_sha256"
    ):
        fail("U0m ancestry does not match exact U0l initramfs")

    for key in (
        "u0l_manifest_sha256",
        "u0l_initramfs_sha256",
        "u0m_initramfs_sha256",
        "original_watchdog_hook_sha256",
        "patched_watchdog_hook_sha256",
        "original_init_2nd_sha256",
        "patched_init_2nd_sha256",
    ):
        value = manifest.get(key, "")
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            fail(f"invalid U0m SHA256 field: {key}={value!r}")
        if patch.get(key) != value:
            fail(f"U0m manifest and patch report disagree on {key}")

    u0l_initramfs = Path(u0l_manifest["u0l_initramfs"])
    try:
        before_archive = v2.Archive.parse(gzip.decompress(u0l_initramfs.read_bytes()))
        after_archive = v2.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        fail(f"cannot parse U0l/U0m initramfs: {exc}")
    u0m.assert_only_payloads_changed(
        before_archive,
        after_archive,
        {u0m.WATCHDOG_TARGET, u0m.INIT_TARGET},
    )
    original_hook = before_archive.one(u0m.WATCHDOG_TARGET).data.decode(
        "utf-8", errors="strict"
    )
    original_init = before_archive.one(u0m.INIT_TARGET).data.decode(
        "utf-8", errors="strict"
    )
    if after_archive.one(u0m.WATCHDOG_TARGET).data.decode(
        "utf-8", errors="strict"
    ) != u0m.patch_watchdog_hook(original_hook):
        fail("U0m watchdog hook is not the exact checked-in transformation")
    if after_archive.one(u0m.INIT_TARGET).data.decode(
        "utf-8", errors="strict"
    ) != u0m.patch_init_second(original_init):
        fail("U0m init_2nd.sh is not the exact checked-in transformation")

    unpacker = root / "aosp-mkbootimg/unpack_bootimg.py"
    if not unpacker.is_file():
        fail(f"missing pinned recovery unpacker: {unpacker}")
    with tempfile.TemporaryDirectory(prefix="a33-u0m-audit-") as temporary:
        temporary_path = Path(temporary)
        before_components = u0l_audit.unpack_recovery(
            unpacker, u0l_candidate, temporary_path / "u0l"
        )
        after_components = u0l_audit.unpack_recovery(
            unpacker, candidate, temporary_path / "u0m"
        )
        component_hashes = compare_component_sets(before_components, after_components)
        layout = u0l_audit.validate_boot_info_delta(
            (
                root
                / "build/pmos-debug-recovery-u0l-openrc-cgroup-isolation/final-boot-info.txt"
            ).read_text(errors="strict"),
            (
                root
                / "build/pmos-debug-recovery-u0m-watchdog-magic-close/final-boot-info.txt"
            ).read_text(errors="strict"),
            before_ramdisk_size=before_components["ramdisk"].stat().st_size,
            after_ramdisk_size=after_components["ramdisk"].stat().st_size,
        )

    u0m_output = root / "build/pmos-debug-recovery-u0m-watchdog-magic-close"
    avb_verify = u0m_output / "avb-verify.txt"
    avb_info = u0m_output / "avb-info.txt"
    for path in (avb_verify, avb_info):
        if not path.is_file() or not path.read_bytes():
            fail(f"missing U0m AVB evidence: {path}")

    report = root / "build/a33-u0m-candidate-audit.txt"
    pairs: list[tuple[str, object]] = [
        ("operation", "host-only-audit-u0m-exact-delta"),
        ("functional_base", "U0l-openrc-cgroup-isolation"),
        ("candidate", candidate),
        ("candidate_size", candidate.stat().st_size),
        ("candidate_sha256", v2.sha_file(candidate)),
        ("manifest", manifest_path),
        ("manifest_sha256", v2.sha_file(manifest_path)),
        ("patch_report", patch_path),
        ("patch_report_sha256", v2.sha_file(patch_path)),
        ("initramfs_payload_delta", "watchdog-hook-and-init_2nd-only"),
        ("recovery_component_delta", "ramdisk-and-avb-authentication-only"),
        *[(key, value) for key, value in sorted(component_hashes.items())],
        *[(key, value) for key, value in sorted(layout.items())],
        ("avb_verify_sha256", v2.sha_file(avb_verify)),
        ("avb_info_sha256", v2.sha_file(avb_info)),
        ("kernel_unchanged", "yes"),
        ("dtb_unchanged", "yes"),
        ("recovery_dtbo_unchanged", "yes"),
        ("kernel_cmdline_unchanged", "yes"),
        (
            "boot_header_unchanged_except_ramdisk_size_and_recovery_dtbo_offset",
            "yes",
        ),
        ("watchdog_magic_close_contract", "passed"),
        ("watchdog_fail_closed_contract", "passed"),
        ("recovery_size_exact", "yes"),
        ("rootfs_persistent_delta", "none"),
        ("phone_partition_writes", "no"),
        ("audit_status", "passed"),
    ]
    v2.write_report(report, pairs)
    print(f"report={report}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AuditError,
        u0m.Refusal,
        u0m.u0l.Refusal,
        u0m.u0l.u0k.Refusal,
        u0m.u0l.u0k.u0j.Refusal,
        u0l_audit.AuditError,
        v2.Refusal,
        v2.CpioError,
        UnicodeDecodeError,
    ) as exc:
        print(f"U0m CANDIDATE AUDIT FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
