#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
BUILDER = HERE / "make-u0m-watchdog-magic-close-v3.py"
U0L_FLASH = HERE / "flash-a33-u0l-openrc-cgroup-isolation.py"
U0L_AUDIT = HERE / "audit-a33-u0l-candidate.py"
INSPECTOR = HERE / "inspect-a33-watchdog-kernel-contract.py"
EXPECTED_BUILDER_BLOB = "1e48bdd42905845046fc95e28e3cd597ae350df1"
EXPECTED_U0L_FLASH_BLOB = "0c8ed99e7d1e75b42cf54921f7f217cad6c4f845"
EXPECTED_U0L_AUDIT_BLOB = "030c6313f133d5e1b7fef0be59ff1e54f65bc420"
EXPECTED_INSPECTOR_BLOB = "ea17562fba369bba3da81c291e22a15c663c929d"
COMPONENTS_UNCHANGED = ("kernel", "dtb", "recovery_dtbo")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load("a33_u0m_v3_audit_builder", BUILDER)
u0l_flash = load("a33_u0m_v3_audit_parent", U0L_FLASH)
u0l_audit = load("a33_u0m_v3_audit_layout", U0L_AUDIT)
inspector = load("a33_u0m_v3_audit_contract", INSPECTOR)
v2 = builder.base.v2


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
                f"unexpected U0m v3 recovery component delta: {name} "
                f"before={before_sha} after={after_sha}"
            )
        result[f"{name}_sha256"] = before_sha
    before_ramdisk = v2.sha_file(before["ramdisk"])
    after_ramdisk = v2.sha_file(after["ramdisk"])
    if before_ramdisk == after_ramdisk:
        fail("U0m v3 ramdisk is identical to U0l")
    result.update(
        {
            "u0l_ramdisk_sha256": before_ramdisk,
            "u0m_ramdisk_sha256": after_ramdisk,
            "u0l_ramdisk_size": str(before["ramdisk"].stat().st_size),
            "u0m_ramdisk_size": str(after["ramdisk"].stat().st_size),
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Host-only exact-delta audit for U0m v3 watchdog handoff"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    for path, expected in (
        (BUILDER, EXPECTED_BUILDER_BLOB),
        (U0L_FLASH, EXPECTED_U0L_FLASH_BLOB),
        (U0L_AUDIT, EXPECTED_U0L_AUDIT_BLOB),
        (INSPECTOR, EXPECTED_INSPECTOR_BLOB),
    ):
        if builder.base.u0l.u0j.git_blob(repo, path) != expected:
            fail(f"checked-in U0m v3 audit dependency changed: {path.name}")

    parent = u0l_flash.validate_local(root, repo)
    u0l_candidate = Path(parent["candidate"])
    u0l_manifest_path = Path(parent["manifest_path"])
    u0l_manifest = v2.kv(u0l_manifest_path)

    manifest_path = (
        root / "build/candidates/a33x-h1-usbpd-u0m-watchdog-magic-close-manifest.txt"
    )
    patch_path = root / "build/u0m-watchdog-magic-close-patch.txt"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0m-watchdog-magic-close-recovery.img"
    initramfs = root / "export-u0m-watchdog-magic-close/initramfs"
    contract_report = root / "build/a33-watchdog-kernel-contract.txt"
    for path in (manifest_path, patch_path, candidate, initramfs, contract_report):
        if not path.is_file():
            fail(f"missing U0m v3 evidence: {path}")

    manifest = v2.kv(manifest_path)
    patch = v2.kv(patch_path)
    expected_common = {
        "implementation_language": "python3",
        "functional_base": "U0l-openrc-cgroup-isolation",
        "cpio_entry_order_preserved": "yes",
        "cpio_payload_delta": "hooks/01-a33x-watchdog.sh,init_2nd.sh",
        "shell_delta": "driver-log-verified-watchdog-magic-close-before-switch-root",
        "watchdog_device": "/dev/watchdog0",
        "watchdog_magic_close_byte": "V",
        "watchdog_failure_behavior": "continue-feeding-and-refuse-switch-root",
        "watchdog_config_source": "/proc/config.gz",
        "watchdog_config_gz_sha256": inspector.EXPECTED_CONFIG_SHA256,
        "watchdog_config_nowayout": "explicitly-not-set",
        "watchdog_config_handle_boot_enabled": "y",
        "watchdog_config_open_timeout": "0",
        "watchdog_config_s3c2410_watchdog": "m",
        "watchdog_runtime_parameter_required": "no",
        "watchdog_class_state_required": "no",
        "watchdog_stop_verification": (
            "driver-stop-log-increment-and-no-did-not-stop-increment"
        ),
        "watchdog_stop_log": builder.base.STOP_LOG,
        "watchdog_did_not_stop_log": builder.base.DID_NOT_STOP_LOG,
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
    }
    v2.require(
        manifest,
        {
            "candidate": "U0m-watchdog-magic-close",
            "functional_delta": (
                "host-pinned-nowayout-disabled-and-driver-log-verified-"
                "magic-close-before-switch-root"
            ),
            **expected_common,
            "preparation_status": "passed",
            "build_status": "passed",
        },
        "U0m v3 manifest",
    )
    v2.require(
        patch,
        {
            "operation": "python-u0m-v3-host-config-pinned-watchdog-magic-close",
            **expected_common,
            "patch_status": "passed",
        },
        "U0m v3 patch report",
    )

    if Path(manifest.get("recovery", "")).resolve() != candidate.resolve():
        fail("U0m v3 manifest references an unexpected recovery")
    if Path(manifest.get("u0m_initramfs", "")).resolve() != initramfs.resolve():
        fail("U0m v3 manifest references an unexpected initramfs")
    if candidate.stat().st_size != 100663296:
        fail(f"unexpected U0m v3 recovery size: {candidate.stat().st_size}")
    if v2.sha_file(candidate) != manifest.get("recovery_sha256"):
        fail("U0m v3 recovery differs from its manifest")
    if v2.sha_file(initramfs) != manifest.get("u0m_initramfs_sha256"):
        fail("U0m v3 initramfs differs from its manifest")
    if v2.sha_file(patch_path) != manifest.get("patch_report_sha256"):
        fail("U0m v3 patch report differs from its manifest")
    if v2.sha_file(u0l_manifest_path) != manifest.get("u0l_manifest_sha256"):
        fail("U0m v3 ancestry does not match exact U0l manifest")
    if manifest.get("u0l_initramfs_sha256") != u0l_manifest.get(
        "u0l_initramfs_sha256"
    ):
        fail("U0m v3 ancestry does not match exact U0l initramfs")

    config_path = Path(manifest.get("watchdog_config_gz", ""))
    report_path = Path(manifest.get("watchdog_config_contract_report", ""))
    contract = inspector.inspect_config(config_path)
    if report_path.resolve() != contract_report.resolve():
        fail("U0m v3 references an unexpected watchdog contract report")
    if v2.sha_file(report_path) != manifest.get(
        "watchdog_config_contract_report_sha256"
    ):
        fail("watchdog contract report differs from U0m v3 manifest")
    if contract.config_sha256 != manifest.get("watchdog_config_gz_sha256"):
        fail("watchdog config differs from U0m v3 manifest")

    for key in (
        "u0l_manifest_sha256",
        "u0l_initramfs_sha256",
        "u0m_initramfs_sha256",
        "original_watchdog_hook_sha256",
        "patched_watchdog_hook_sha256",
        "original_init_2nd_sha256",
        "patched_init_2nd_sha256",
        "watchdog_config_gz_sha256",
        "watchdog_config_contract_report_sha256",
    ):
        require_sha(manifest.get(key, ""), key)
        if patch.get(key) != manifest.get(key):
            fail(f"U0m v3 manifest and patch report disagree on {key}")

    u0l_initramfs = Path(u0l_manifest["u0l_initramfs"])
    try:
        before_archive = v2.Archive.parse(gzip.decompress(u0l_initramfs.read_bytes()))
        after_archive = v2.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        fail(f"cannot parse U0l/U0m v3 initramfs: {exc}")
    builder.base.assert_only_payloads_changed(
        before_archive,
        after_archive,
        {builder.base.WATCHDOG_TARGET, builder.base.INIT_TARGET},
    )
    original_hook = before_archive.one(builder.base.WATCHDOG_TARGET).data.decode(
        "utf-8", errors="strict"
    )
    original_init = before_archive.one(builder.base.INIT_TARGET).data.decode(
        "utf-8", errors="strict"
    )
    if after_archive.one(builder.base.WATCHDOG_TARGET).data.decode(
        "utf-8", errors="strict"
    ) != builder.patch_watchdog_hook(original_hook):
        fail("U0m v3 watchdog hook is not the checked-in transformation")
    if after_archive.one(builder.base.INIT_TARGET).data.decode(
        "utf-8", errors="strict"
    ) != builder.base.patch_init_second(original_init):
        fail("U0m v3 init_2nd.sh is not the checked-in transformation")

    unpacker = root / "aosp-mkbootimg/unpack_bootimg.py"
    if not unpacker.is_file():
        fail(f"missing pinned recovery unpacker: {unpacker}")
    with tempfile.TemporaryDirectory(prefix="a33-u0m-v3-audit-") as temporary:
        temp = Path(temporary)
        before_components = u0l_audit.unpack_recovery(
            unpacker, u0l_candidate, temp / "u0l"
        )
        after_components = u0l_audit.unpack_recovery(
            unpacker, candidate, temp / "u0m-v3"
        )
        component_hashes = compare_components(before_components, after_components)
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

    output = root / "build/pmos-debug-recovery-u0m-watchdog-magic-close"
    avb_verify = output / "avb-verify.txt"
    avb_info = output / "avb-info.txt"
    for path in (avb_verify, avb_info):
        if not path.is_file() or not path.read_bytes():
            fail(f"missing U0m v3 AVB evidence: {path}")

    report = root / "build/a33-u0m-v3-candidate-audit.txt"
    rows: list[tuple[str, object]] = [
        ("operation", "host-only-audit-u0m-v3-exact-delta"),
        ("functional_base", "U0l-openrc-cgroup-isolation"),
        ("candidate", candidate),
        ("candidate_size", candidate.stat().st_size),
        ("candidate_sha256", v2.sha_file(candidate)),
        ("manifest", manifest_path),
        ("manifest_sha256", v2.sha_file(manifest_path)),
        ("patch_report", patch_path),
        ("patch_report_sha256", v2.sha_file(patch_path)),
        ("watchdog_config_contract_report", contract_report),
        ("watchdog_config_contract_report_sha256", v2.sha_file(contract_report)),
        ("initramfs_payload_delta", "watchdog-hook-and-init_2nd-only"),
        ("recovery_component_delta", "ramdisk-and-avb-authentication-only"),
        *sorted(component_hashes.items()),
        *sorted(layout.items()),
        ("watchdog_config_identity_pinned", "yes"),
        ("watchdog_nowayout_explicitly_disabled", "yes"),
        ("watchdog_runtime_parameter_required", "no"),
        ("watchdog_class_state_required", "no"),
        ("watchdog_driver_stop_log_required", "yes"),
        ("watchdog_magic_close_contract", "passed"),
        ("watchdog_fail_closed_contract", "passed"),
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
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AuditError,
        builder.base.Refusal,
        builder.base.u0l.Refusal,
        builder.base.u0l.u0k.Refusal,
        builder.base.u0l.u0k.u0j.Refusal,
        u0l_audit.AuditError,
        inspector.ContractError,
        v2.Refusal,
        v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        print(f"U0m v3 CANDIDATE AUDIT FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
