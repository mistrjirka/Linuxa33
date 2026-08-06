#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
BUILDER_PATH = HERE / "make-u0q-emergency-ssh.py"
U0P_AUDIT_PATH = HERE / "audit-a33-u0p-candidate.py"
EXPECTED_BUILDER_BLOB = "fa662b03cf3a4e4c9166ebc9fa0a177dc12dbdb4"
EXPECTED_U0P_AUDIT_BLOB = "abc5ac0901a0ca09bbac896d257d0ff40d9a0c66"
COMPONENTS_UNCHANGED = ("kernel", "dtb", "recovery_dtbo")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load("a33_u0q_audit_builder", BUILDER_PATH)
u0p_audit = load("a33_u0q_audit_parent", U0P_AUDIT_PATH)
v2 = builder.v2


class AuditError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise AuditError(message)


def git_blob(repo: Path, path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def require_sha(value: str, label: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        fail(f"invalid SHA256 for {label}: {value!r}")


def compare_components(before: dict[str, Path], after: dict[str, Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in COMPONENTS_UNCHANGED:
        before_sha = v2.sha_file(before[name])
        after_sha = v2.sha_file(after[name])
        if before_sha != after_sha:
            fail(
                f"unexpected U0q recovery component delta: {name} "
                f"before={before_sha} after={after_sha}"
            )
        result[f"{name}_sha256"] = before_sha
    before_ramdisk = v2.sha_file(before["ramdisk"])
    after_ramdisk = v2.sha_file(after["ramdisk"])
    if before_ramdisk == after_ramdisk:
        fail("U0q ramdisk is identical to U0p")
    result.update(
        {
            "u0p_ramdisk_sha256": before_ramdisk,
            "u0q_ramdisk_sha256": after_ramdisk,
            "u0p_ramdisk_size": str(before["ramdisk"].stat().st_size),
            "u0q_ramdisk_size": str(after["ramdisk"].stat().st_size),
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Host-only exact-delta audit for U0q emergency SSH"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    for path, expected in (
        (BUILDER_PATH, EXPECTED_BUILDER_BLOB),
        (U0P_AUDIT_PATH, EXPECTED_U0P_AUDIT_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            fail(
                f"checked-in U0q audit dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    parent_manifest_path, parent_initramfs, parent_candidate, _ = builder.validate_parent(
        root, repo
    )
    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-manifest.txt"
    patch_path = root / "build/u0q-emergency-ssh-patch.txt"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-recovery.img"
    initramfs = root / "export-u0q-emergency-ssh/initramfs"
    for path in (manifest_path, patch_path, candidate, initramfs):
        if not path.is_file():
            fail(f"missing U0q audit input: {path}")

    manifest = v2.kv(manifest_path)
    patch = v2.kv(patch_path)
    public_path = Path(manifest.get("client_public_key", ""))
    private_path = Path(manifest.get("client_private_key", ""))
    if not public_path.is_file() or not private_path.is_file():
        fail("U0q emergency client keypair is missing")
    public_text = builder.normalize_public_key(public_path.read_text(encoding="utf-8"))
    for path, field in (
        (public_path, "client_public_key_sha256"),
        (private_path, "client_private_key_sha256"),
    ):
        if v2.sha_file(path) != manifest.get(field):
            fail(f"U0q key artifact differs from manifest: {path}")
    if private_path.stat().st_mode & 0o077:
        fail("U0q emergency private key permissions are too broad")
    embedded_key_sha = v2.sha_bytes((public_text + "\n").encode())
    if manifest.get("embedded_public_key_sha256") != embedded_key_sha:
        fail("U0q embedded public-key hash differs from exact key")

    expected_common = {
        "implementation_language": "python3",
        "functional_base": "U0p-corrected-sshd-source-hash",
        "cpio_entry_order_preserved": "yes",
        "cpio_payload_delta": builder.INIT_TARGET,
        "shell_delta": "chrooted-emergency-sshd-and-usb-network-helper-before-switch-root",
        "normal_openrc_sshd_instrumentation_preserved": "yes",
        "emergency_sshd_port": str(builder.PORT),
        "emergency_sshd_user": "root",
        "emergency_sshd_auth": "dedicated-ed25519-public-key-only",
        "emergency_sshd_pam": "disabled",
        "emergency_sshd_password_auth": "disabled",
        "emergency_sshd_process_root": "chroot-/sysroot",
        "emergency_network_address": builder.PHONE_ADDRESS,
        "emergency_network_wait_seconds": "150",
        "emergency_trace_path": builder.TRACE_PATH,
        "emergency_trace_mode": "0600",
        "inherited_trace_path": builder.INHERITED_TRACE_PATH,
        "rootfs_persistent_delta_from_u0p": builder.TRACE_PATH,
        "client_private_key_sha256": v2.sha_file(private_path),
        "client_public_key_sha256": v2.sha_file(public_path),
        "embedded_public_key_sha256": embedded_key_sha,
        "u0p_watchdog_hook_preserved": "yes",
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
            "candidate": "U0q-emergency-ssh",
            "functional_delta": "independent-live-root-shell-on-port-2222",
            **expected_common,
            "preparation_status": "passed",
            "build_status": "passed",
        },
        "U0q manifest",
    )
    v2.require(
        patch,
        {
            "operation": "python-u0q-emergency-ssh",
            **expected_common,
            "patch_status": "passed",
        },
        "U0q patch report",
    )
    if Path(manifest.get("recovery", "")).resolve() != candidate.resolve():
        fail("U0q manifest references an unexpected recovery")
    if Path(manifest.get("u0q_initramfs", "")).resolve() != initramfs.resolve():
        fail("U0q manifest references an unexpected initramfs")
    if candidate.stat().st_size != 100663296:
        fail(f"unexpected U0q recovery size: {candidate.stat().st_size}")
    if v2.sha_file(candidate) != manifest.get("recovery_sha256"):
        fail("U0q recovery differs from its manifest")
    if v2.sha_file(initramfs) != manifest.get("u0q_initramfs_sha256"):
        fail("U0q initramfs differs from its manifest")
    if v2.sha_file(patch_path) != manifest.get("patch_report_sha256"):
        fail("U0q patch report differs from its manifest")
    if v2.sha_file(parent_manifest_path) != manifest.get("u0p_manifest_sha256"):
        fail("U0q ancestry does not match exact U0p manifest")
    if manifest.get("u0p_initramfs_sha256") != builder.EXPECTED_U0P_INITRAMFS_SHA256:
        fail("U0q ancestry does not match exact U0p initramfs")

    try:
        before = v2.Archive.parse(gzip.decompress(parent_initramfs.read_bytes()))
        after = v2.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        fail(f"cannot parse U0p/U0q initramfs: {exc}")
    builder.assert_only_init_changed(before, after)
    if before.one(builder.WATCHDOG_TARGET).data != after.one(builder.WATCHDOG_TARGET).data:
        fail("U0q changed the proven U0p watchdog hook")

    before_init = before.one(builder.INIT_TARGET).data.decode("utf-8", errors="strict")
    after_init = after.one(builder.INIT_TARGET).data.decode("utf-8", errors="strict")
    expected_init = builder.patch_init_second(before_init, public_text)
    if after_init != expected_init:
        fail("U0q init_2nd.sh is not the checked-in transformation")
    before_embedded = builder.u0p.embedded_sshd_bytes(before_init)
    after_embedded = builder.u0p.embedded_sshd_bytes(after_init)
    if before_embedded != after_embedded:
        fail("U0q changed inherited OpenRC sshd instrumentation")
    if v2.sha_bytes(after_embedded) != builder.EXPECTED_U0P_EMBEDDED_SSHD_SHA256:
        fail("U0q inherited OpenRC sshd instrumentation hash changed")
    if after_init.count(public_text) != 2:
        fail("U0q public key is not embedded exactly in config-test and daemon argv")
    if "BEGIN OPENSSH PRIVATE KEY" in after_init:
        fail("U0q embedded private key material")
    emergency = builder.emergency_block(public_text)
    required_runtime = (
        "exec /bin/busybox chroot /sysroot /usr/sbin/sshd",
        "exec /bin/busybox chroot /sysroot /bin/sh -s",
        "AuthorizedKeysCommand=/bin/echo",
        "AuthorizedKeysCommandUser=root",
        "AuthenticationMethods=publickey",
        "PermitRootLogin=prohibit-password",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "UsePAM=no",
        "PidFile=none",
        "Port=2222",
        "172.16.42.1/24",
        "event=network-configured",
    )
    for token in required_runtime:
        if token not in emergency:
            fail(f"U0q runtime contract token missing: {token}")

    unpacker = root / "aosp-mkbootimg/unpack_bootimg.py"
    if not unpacker.is_file():
        fail(f"missing pinned recovery unpacker: {unpacker}")
    with tempfile.TemporaryDirectory(prefix="a33-u0q-audit-") as temporary:
        temp = Path(temporary)
        unpack = u0p_audit.u0o_audit.u0n_audit.u0m_audit.u0l_audit.unpack_recovery
        layout_check = (
            u0p_audit.u0o_audit.u0n_audit.u0m_audit.u0l_audit.validate_boot_info_delta
        )
        before_components = unpack(unpacker, parent_candidate, temp / "u0p")
        after_components = unpack(unpacker, candidate, temp / "u0q")
        component_hashes = compare_components(before_components, after_components)
        layout = layout_check(
            (root / "build/pmos-debug-recovery-u0p-corrected-sshd-source-hash/final-boot-info.txt").read_text(errors="strict"),
            (root / "build/pmos-debug-recovery-u0q-emergency-ssh/final-boot-info.txt").read_text(errors="strict"),
            before_ramdisk_size=before_components["ramdisk"].stat().st_size,
            after_ramdisk_size=after_components["ramdisk"].stat().st_size,
        )

    output = root / "build/pmos-debug-recovery-u0q-emergency-ssh"
    for path in (output / "avb-verify.txt", output / "avb-info.txt"):
        if not path.is_file() or not path.read_bytes():
            fail(f"missing U0q AVB evidence: {path}")

    report = root / "build/a33-u0q-candidate-audit.txt"
    rows: list[tuple[str, object]] = [
        ("operation", "host-only-audit-u0q-emergency-ssh"),
        ("functional_base", "U0p-corrected-sshd-source-hash"),
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
        ("u0p_watchdog_hook_byte_identical", "yes"),
        ("normal_openrc_sshd_instrumentation_byte_identical", "yes"),
        ("emergency_sshd_port", builder.PORT),
        ("normal_sshd_port_untouched", "yes"),
        ("emergency_sshd_chroot_contract", "passed"),
        ("long_lived_old_initramfs_root_reference", "no"),
        ("emergency_auth_public_key_only", "yes"),
        ("private_key_embedded", "no"),
        ("emergency_public_key_sha256", embedded_key_sha),
        ("emergency_client_private_key", private_path),
        ("emergency_network_address", builder.PHONE_ADDRESS),
        ("emergency_network_helper_independent_of_openrc", "yes"),
        ("emergency_trace_path", builder.TRACE_PATH),
        ("rootfs_persistent_delta_from_u0p", builder.TRACE_PATH),
        ("kernel_unchanged", "yes"),
        ("dtb_unchanged", "yes"),
        ("recovery_dtbo_unchanged", "yes"),
        ("kernel_cmdline_unchanged", "yes"),
        ("recovery_size_exact", "yes"),
        ("phone_partition_writes", "no"),
        ("audit_status", "passed"),
    ]
    v2.write_report(report, rows)
    print(f"report={report}")
    print(f"candidate_sha256={v2.sha_file(candidate)}")
    print(f"emergency_sshd_port={builder.PORT}")
    print(f"emergency_client_private_key={private_path}")
    print(f"emergency_public_key_sha256={embedded_key_sha}")
    print("normal_openrc_sshd_instrumentation_byte_identical=yes")
    print("emergency_sshd_chroot_contract=passed")
    print("emergency_network_helper_independent_of_openrc=yes")
    print("audit_status=passed")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AuditError,
        builder.Refusal,
        u0p_audit.AuditError,
        v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"REFUSING U0q AUDIT: {exc}", file=sys.stderr)
        raise SystemExit(1)
