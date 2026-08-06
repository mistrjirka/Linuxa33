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
BUILDER_PATH = HERE / "make-u0p-corrected-sshd-source-hash.py"
U0O_AUDIT_V2_PATH = HERE / "audit-a33-u0o-candidate-v2.py"
EXPECTED_BUILDER_BLOB = "2a5eb4957424fe81212e762ed2225f86ec890ca4"
EXPECTED_U0O_AUDIT_V2_BLOB = "25a3ab194093b7b082477caba5c554481f37bf1a"
COMPONENTS_UNCHANGED = ("kernel", "dtb", "recovery_dtbo")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load("a33_u0p_audit_builder", BUILDER_PATH)
u0o_audit_v2 = load("a33_u0p_audit_parent", U0O_AUDIT_V2_PATH)
u0o_audit = u0o_audit_v2.base
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
                f"unexpected U0p recovery component delta: {name} "
                f"before={before_sha} after={after_sha}"
            )
        result[f"{name}_sha256"] = before_sha
    before_ramdisk = v2.sha_file(before["ramdisk"])
    after_ramdisk = v2.sha_file(after["ramdisk"])
    if before_ramdisk == after_ramdisk:
        fail("U0p ramdisk is identical to failed U0o")
    result.update(
        {
            "u0o_ramdisk_sha256": before_ramdisk,
            "u0p_ramdisk_sha256": after_ramdisk,
            "u0o_ramdisk_size": str(before["ramdisk"].stat().st_size),
            "u0p_ramdisk_size": str(after["ramdisk"].stat().st_size),
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Host-only exact-delta audit for U0p corrected instrumented sshd source hash"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    for path, expected in (
        (BUILDER_PATH, EXPECTED_BUILDER_BLOB),
        (U0O_AUDIT_V2_PATH, EXPECTED_U0O_AUDIT_V2_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            fail(
                f"checked-in U0p audit dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    parent_manifest_path, parent_initramfs, parent_candidate, _ = builder.validate_parent(
        root, repo
    )
    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0p-corrected-sshd-source-hash-manifest.txt"
    patch_path = root / "build/u0p-corrected-sshd-source-hash-patch.txt"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0p-corrected-sshd-source-hash-recovery.img"
    initramfs = root / "export-u0p-corrected-sshd-source-hash/initramfs"
    for path in (manifest_path, patch_path, candidate, initramfs):
        if not path.is_file():
            fail(f"missing U0p audit input: {path}")

    manifest = v2.kv(manifest_path)
    patch = v2.kv(patch_path)
    corrected_sha = manifest.get("corrected_instrumented_sshd_sha256", "")
    require_sha(corrected_sha, "corrected_instrumented_sshd_sha256")
    expected_common = {
        "implementation_language": "python3",
        "functional_base": "U0o-persistent-sshd-trace",
        "cpio_entry_order_preserved": "yes",
        "cpio_payload_delta": builder.INIT_TARGET,
        "shell_delta": "correct-stale-instrumented-sshd-source-sha-and-candidate-label",
        "runtime_failure_fixed": "instrumented-source-hash-mismatch",
        "stale_declared_instrumented_sshd_sha256": builder.STALE_U0N_INSTRUMENTED_SHA256,
        "corrected_instrumented_sshd_sha256": corrected_sha,
        "embedded_instrumented_sshd_bytes_preserved": "yes",
        "sshd_behavior_delta_from_u0o": "none",
        "persistent_trace_path": builder.TRACE_PATH,
        "persistent_trace_write_scope": "unchanged-from-u0o",
        "rootfs_persistent_delta": builder.TRACE_PATH,
        "u0o_watchdog_hook_preserved": "yes",
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
            "candidate": "U0p-corrected-sshd-source-hash",
            "functional_delta": "correct-runtime-hash-for-persistent-logging-sshd-heredoc",
            **expected_common,
            "preparation_status": "passed",
            "build_status": "passed",
        },
        "U0p manifest",
    )
    v2.require(
        patch,
        {
            "operation": "python-u0p-correct-stale-instrumented-sshd-source-sha",
            **expected_common,
            "patch_status": "passed",
        },
        "U0p patch report",
    )
    if Path(manifest.get("recovery", "")).resolve() != candidate.resolve():
        fail("U0p manifest references an unexpected recovery")
    if Path(manifest.get("u0p_initramfs", "")).resolve() != initramfs.resolve():
        fail("U0p manifest references an unexpected initramfs")
    if candidate.stat().st_size != 100663296:
        fail(f"unexpected U0p recovery size: {candidate.stat().st_size}")
    if v2.sha_file(candidate) != manifest.get("recovery_sha256"):
        fail("U0p recovery differs from its manifest")
    if v2.sha_file(initramfs) != manifest.get("u0p_initramfs_sha256"):
        fail("U0p initramfs differs from its manifest")
    if v2.sha_file(patch_path) != manifest.get("patch_report_sha256"):
        fail("U0p patch report differs from its manifest")
    if v2.sha_file(parent_manifest_path) != manifest.get("u0o_manifest_sha256"):
        fail("U0p ancestry does not match exact U0o manifest")
    if manifest.get("u0o_initramfs_sha256") != builder.EXPECTED_U0O_INITRAMFS_SHA256:
        fail("U0p ancestry does not match exact U0o initramfs")

    try:
        before = v2.Archive.parse(gzip.decompress(parent_initramfs.read_bytes()))
        after = v2.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        fail(f"cannot parse U0o/U0p initramfs: {exc}")
    builder.assert_only_init_changed(before, after)
    if before.one(builder.WATCHDOG_TARGET).data != after.one(builder.WATCHDOG_TARGET).data:
        fail("U0p changed the proven U0o watchdog hook")

    before_init = before.one(builder.INIT_TARGET).data.decode("utf-8", errors="strict")
    after_init = after.one(builder.INIT_TARGET).data.decode("utf-8", errors="strict")
    expected_init, recomputed_sha = builder.patch_init_second(before_init)
    if after_init != expected_init:
        fail("U0p init_2nd.sh is not the checked-in transformation")
    before_embedded = builder.embedded_sshd_bytes(before_init)
    after_embedded = builder.embedded_sshd_bytes(after_init)
    if before_embedded != after_embedded:
        fail("U0p changed embedded instrumented sshd bytes")
    embedded_sha = v2.sha_bytes(after_embedded)
    if recomputed_sha != embedded_sha or corrected_sha != embedded_sha:
        fail(
            "U0p corrected hash does not equal exact embedded sshd bytes: "
            f"recomputed={recomputed_sha} manifest={corrected_sha} embedded={embedded_sha}"
        )
    if builder.declared_instrumented_sha(before_init) != builder.STALE_U0N_INSTRUMENTED_SHA256:
        fail("failed U0o does not contain the expected stale declaration")
    if builder.declared_instrumented_sha(after_init) != embedded_sha:
        fail("U0p declaration does not match embedded sshd bytes")
    if builder.STALE_U0N_INSTRUMENTED_SHA256 == embedded_sha:
        fail("stale U0n hash unexpectedly matches persistent-logging script")
    if before_init.count("candidate=U0o-persistent-sshd-trace stage=trace-open") != 1:
        fail("failed U0o trace label is absent or duplicated")
    if after_init.count("candidate=U0p-corrected-sshd-source-hash stage=trace-open") != 1:
        fail("U0p trace label is absent or duplicated")

    unpacker = root / "aosp-mkbootimg/unpack_bootimg.py"
    if not unpacker.is_file():
        fail(f"missing pinned recovery unpacker: {unpacker}")
    with tempfile.TemporaryDirectory(prefix="a33-u0p-audit-") as temporary:
        temp = Path(temporary)
        before_components = u0o_audit.u0n_audit.u0m_audit.u0l_audit.unpack_recovery(
            unpacker, parent_candidate, temp / "u0o"
        )
        after_components = u0o_audit.u0n_audit.u0m_audit.u0l_audit.unpack_recovery(
            unpacker, candidate, temp / "u0p"
        )
        component_hashes = compare_components(before_components, after_components)
        layout = u0o_audit.u0n_audit.u0m_audit.u0l_audit.validate_boot_info_delta(
            (root / "build/pmos-debug-recovery-u0o-persistent-sshd-trace/final-boot-info.txt").read_text(errors="strict"),
            (root / "build/pmos-debug-recovery-u0p-corrected-sshd-source-hash/final-boot-info.txt").read_text(errors="strict"),
            before_ramdisk_size=before_components["ramdisk"].stat().st_size,
            after_ramdisk_size=after_components["ramdisk"].stat().st_size,
        )

    output = root / "build/pmos-debug-recovery-u0p-corrected-sshd-source-hash"
    for path in (output / "avb-verify.txt", output / "avb-info.txt"):
        if not path.is_file() or not path.read_bytes():
            fail(f"missing U0p AVB evidence: {path}")

    report = root / "build/a33-u0p-candidate-audit.txt"
    rows: list[tuple[str, object]] = [
        ("operation", "host-only-audit-u0p-corrected-sshd-source-hash"),
        ("functional_base", "U0o-persistent-sshd-trace"),
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
        ("u0o_watchdog_hook_byte_identical", "yes"),
        ("embedded_instrumented_sshd_bytes_identical", "yes"),
        ("stale_declared_sha256", builder.STALE_U0N_INSTRUMENTED_SHA256),
        ("exact_embedded_sshd_sha256", embedded_sha),
        ("before_declared_hash_matches_embedded", "no"),
        ("after_declared_hash_matches_embedded", "yes"),
        ("runtime_source_hash_contract", "passed"),
        ("runtime_failure_fixed", "instrumented-source-hash-mismatch"),
        ("sshd_behavior_delta_from_u0o", "none"),
        ("persistent_trace_path", builder.TRACE_PATH),
        ("persistent_trace_scope_unchanged", "yes"),
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
    print(f"stale_declared_sha256={builder.STALE_U0N_INSTRUMENTED_SHA256}")
    print(f"exact_embedded_sshd_sha256={embedded_sha}")
    print("before_declared_hash_matches_embedded=no")
    print("after_declared_hash_matches_embedded=yes")
    print("runtime_source_hash_contract=passed")
    print("audit_status=passed")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AuditError,
        builder.Refusal,
        u0o_audit.AuditError,
        v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"REFUSING U0p AUDIT: {exc}", file=sys.stderr)
        raise SystemExit(1)
