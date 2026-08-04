#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
U0L_BUILDER = HERE / "make-u0l-openrc-cgroup-isolation.py"
U0K_FLASH = HERE / "flash-a33-u0k-direct-mount-isolation.py"
EXPECTED_U0L_BUILDER_BLOB = "c976721153b43e4507478597bb6680972b4cc8dc"
EXPECTED_U0K_FLASH_BLOB = "404308fa0e439ea00224ef6f58647fc3cca63778"
COMPONENTS_UNCHANGED = ("kernel", "dtb", "recovery_dtbo")
IGNORED_BOOT_INFO_PREFIXES = ("ramdisk size:", "ramdisk_size:")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0l = load("a33_u0l_candidate_audit_builder", U0L_BUILDER)
u0k_flash = load("a33_u0l_candidate_audit_parent", U0K_FLASH)
v2 = u0l.v2


class AuditError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise AuditError(message)


def normalize_boot_info(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        lowered = line.lower()
        if any(lowered.startswith(prefix) for prefix in IGNORED_BOOT_INFO_PREFIXES):
            continue
        lines.append(line)
    return "\n".join(lines) + "\n"


def unpack_recovery(unpacker: Path, image: Path, output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=False)
    completed = subprocess.run(
        [sys.executable, str(unpacker), "--boot_img", str(image), "--out", str(output)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        fail(
            f"cannot unpack recovery {image}: rc={completed.returncode} "
            f"stderr={completed.stderr.strip()}"
        )
    components = {
        name: output / name
        for name in (*COMPONENTS_UNCHANGED, "ramdisk")
    }
    missing = [name for name, path in components.items() if not path.is_file()]
    if missing:
        fail(f"unpacked recovery is missing components: {missing}")
    return components


def compare_component_sets(
    before: dict[str, Path], after: dict[str, Path]
) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in COMPONENTS_UNCHANGED:
        before_sha = v2.sha_file(before[name])
        after_sha = v2.sha_file(after[name])
        if before_sha != after_sha:
            fail(
                f"unexpected recovery component delta: {name} "
                f"before={before_sha} after={after_sha}"
            )
        result[f"{name}_sha256"] = before_sha
    before_ramdisk = v2.sha_file(before["ramdisk"])
    after_ramdisk = v2.sha_file(after["ramdisk"])
    if before_ramdisk == after_ramdisk:
        fail("U0l ramdisk is identical to U0k; expected exact initramfs delta is absent")
    result["u0k_ramdisk_sha256"] = before_ramdisk
    result["u0l_ramdisk_sha256"] = after_ramdisk
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Host-only acceptance audit for exact U0k-to-U0l recovery delta"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    for path, expected in (
        (U0L_BUILDER, EXPECTED_U0L_BUILDER_BLOB),
        (U0K_FLASH, EXPECTED_U0K_FLASH_BLOB),
    ):
        if u0l.u0k.u0j.git_blob(repo, path) != expected:
            fail(f"checked-in audit dependency changed unexpectedly: {path.name}")

    parent = u0k_flash.validate_local(root, repo)
    u0k_candidate = Path(parent["candidate"])
    u0k_manifest_path = Path(parent["manifest_path"])
    u0k_manifest = v2.kv(u0k_manifest_path)

    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0l-openrc-cgroup-isolation-manifest.txt"
    patch_path = root / "build/u0l-openrc-cgroup-isolation-patch.txt"
    expected_candidate = root / "build/candidates/a33x-h1-usbpd-u0l-openrc-cgroup-isolation-recovery.img"
    expected_initramfs = root / "export-u0l-openrc-cgroup-isolation/initramfs"
    if not manifest_path.is_file() or not patch_path.is_file():
        fail("U0l manifest or patch report is missing; run the U0l builder first")
    manifest = v2.kv(manifest_path)
    patch = v2.kv(patch_path)
    expected_contract = {
        "candidate": "U0l-openrc-cgroup-isolation",
        "functional_base": "U0k-direct-mount-isolation",
        "functional_delta": "bind-mask-openrc-service-cgroup-helper-without-persistent-rootfs-write",
        "implementation_language": "python3",
        "cpio_payload_delta": "init_2nd.sh",
        "shell_delta": "runtime-bind-mask-openrc-rc-cgroup-after-cleanup-before-switch-root",
        "rootfs_persistent_delta": "none",
        "runtime_mount_delta": "bind-/dev/null-over-/usr/libexec/rc/sh/rc-cgroup.sh",
        "openrc_cgroup_target": "/usr/libexec/rc/sh/rc-cgroup.sh",
        "openrc_package_version": "0.63.2-r0",
        "rootfs_image_sha256": u0l.EXPECTED_ROOTFS_SHA256,
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
    v2.require(manifest, expected_contract, "U0l manifest")
    if patch.get("operation") != "python-u0l-openrc-cgroup-runtime-bind-mask":
        fail("unexpected U0l patch operation")
    if patch.get("patch_status") != "passed":
        fail("U0l patch report did not pass")
    for key in (
        "u0k_initramfs_sha256",
        "u0l_initramfs_sha256",
        "original_init_2nd_sha256",
        "patched_init_2nd_sha256",
        "openrc_cgroup_target_sha256",
    ):
        if patch.get(key) != manifest.get(key):
            fail(f"U0l manifest and patch report disagree on {key}")

    candidate = Path(manifest.get("recovery", ""))
    initramfs = Path(manifest.get("u0l_initramfs", ""))
    if candidate.resolve() != expected_candidate.resolve():
        fail("U0l manifest references an unexpected recovery path")
    if initramfs.resolve() != expected_initramfs.resolve():
        fail("U0l manifest references an unexpected initramfs path")
    if not candidate.is_file() or candidate.stat().st_size != 100663296:
        fail("U0l recovery is missing or has unexpected size")
    if v2.sha_file(candidate) != manifest.get("recovery_sha256"):
        fail("U0l recovery differs from its manifest")
    if not initramfs.is_file() or v2.sha_file(initramfs) != manifest.get("u0l_initramfs_sha256"):
        fail("U0l initramfs differs from its manifest")
    if v2.sha_file(u0k_manifest_path) != manifest.get("u0k_manifest_sha256"):
        fail("U0l ancestry does not match exact U0k manifest")
    if manifest.get("u0k_initramfs_sha256") != u0k_manifest.get("u0k_initramfs_sha256"):
        fail("U0l ancestry does not match exact U0k initramfs")

    u0k_initramfs = Path(u0k_manifest["u0k_initramfs"])
    try:
        before_archive = v2.Archive.parse(gzip.decompress(u0k_initramfs.read_bytes()))
        after_archive = v2.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        fail(f"cannot parse U0k/U0l initramfs: {exc}")
    before_archive.assert_only_payload_changed(after_archive, u0l.TARGET)
    original = before_archive.one(u0l.TARGET).data.decode("utf-8", errors="strict")
    expected_patched = u0l.patch_init_second(original)
    actual_patched = after_archive.one(u0l.TARGET).data.decode("utf-8", errors="strict")
    if actual_patched != expected_patched:
        fail("U0l init_2nd.sh is not the exact checked-in transformation of U0k")

    unpacker = root / "aosp-mkbootimg/unpack_bootimg.py"
    if not unpacker.is_file():
        fail(f"missing pinned recovery unpacker: {unpacker}")
    with tempfile.TemporaryDirectory(prefix="a33-u0l-audit-") as temporary:
        temporary_path = Path(temporary)
        before_components = unpack_recovery(
            unpacker, u0k_candidate, temporary_path / "u0k"
        )
        after_components = unpack_recovery(
            unpacker, candidate, temporary_path / "u0l"
        )
        component_hashes = compare_component_sets(before_components, after_components)

    u0k_info = root / "build/pmos-debug-recovery-u0k-direct-mount-isolation/final-boot-info.txt"
    u0l_output = root / "build/pmos-debug-recovery-u0l-openrc-cgroup-isolation"
    u0l_info = u0l_output / "final-boot-info.txt"
    avb_verify = u0l_output / "avb-verify.txt"
    avb_info = u0l_output / "avb-info.txt"
    for path in (u0k_info, u0l_info, avb_verify, avb_info):
        if not path.is_file() or not path.read_bytes():
            fail(f"missing generated recovery evidence: {path}")
    u0k_normalized_info = normalize_boot_info(u0k_info.read_text(errors="strict"))
    u0l_normalized_info = normalize_boot_info(u0l_info.read_text(errors="strict"))
    if u0k_normalized_info != u0l_normalized_info:
        fail("U0l boot header/command-line information differs from U0k beyond ramdisk size")

    report = root / "build/a33-u0l-candidate-audit.txt"
    pairs: list[tuple[str, object]] = [
        ("operation", "host-only-audit-u0l-exact-delta"),
        ("functional_base", "U0k-direct-mount-isolation"),
        ("candidate", candidate),
        ("candidate_size", candidate.stat().st_size),
        ("candidate_sha256", v2.sha_file(candidate)),
        ("manifest", manifest_path),
        ("manifest_sha256", v2.sha_file(manifest_path)),
        ("patch_report", patch_path),
        ("patch_report_sha256", v2.sha_file(patch_path)),
        ("initramfs_payload_delta", "init_2nd.sh-only"),
        ("recovery_component_delta", "ramdisk-and-avb-authentication-only"),
        *[(key, value) for key, value in sorted(component_hashes.items())],
        ("normalized_boot_info_sha256", v2.sha_bytes(u0l_normalized_info.encode())),
        ("avb_verify_sha256", v2.sha_file(avb_verify)),
        ("avb_info_sha256", v2.sha_file(avb_info)),
        ("kernel_unchanged", "yes"),
        ("dtb_unchanged", "yes"),
        ("recovery_dtbo_unchanged", "yes"),
        ("kernel_cmdline_unchanged", "yes"),
        ("boot_header_unchanged_except_ramdisk_size", "yes"),
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
        u0l.Refusal,
        u0l.u0k.Refusal,
        u0l.u0k.u0j.Refusal,
        v2.Refusal,
        v2.CpioError,
        UnicodeDecodeError,
    ) as exc:
        print(f"U0l CANDIDATE AUDIT FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
