#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
U0J_BUILDER = HERE / "make-u0j-python-root-api-compatible.py"
EXPECTED_U0J_BUILDER_BLOB = "6ee8f4c933bf15d7a239292455e53b1a1e357948"

spec = importlib.util.spec_from_file_location("a33_u0j_builder", U0J_BUILDER)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0j builder: {U0J_BUILDER}")
u0j = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = u0j
spec.loader.exec_module(u0j)
v2 = u0j.v2

MODULES = 67
TARGET = "init_2nd.sh"
MARKER_PREFIX = "a33x-u0k-direct-mount"
SKIPPED_CALLS = (
    "delete_old_install_partition",
    "resize_root_partition",
    "unlock_root_partition",
    "resize_root_filesystem",
    "wait_boot_partition",
    "mount_boot_partition",
)
MARKERS = (
    "root-ready",
    "skip-delete-resize-unlock",
    "mount-root-begin",
    "mount-root-success",
    "after-mount-resize-hook",
    "skip-legacy-boot-mount",
    "cleanup-hooks-begin",
    "cleanup-hooks-done",
    "switch-root-begin",
)

ORIGINAL_HANDOFF_BLOCK = '''wait_root_partition
delete_old_install_partition
resize_root_partition
unlock_root_partition
resize_root_filesystem
mount_root_partition
resize_filesystem_after_mount /sysroot

# Mount boot partition into sysroot if needed since some
# old installations don't have a proper /etc/fstab file. See #2800
if [ -z "$(cat /sysroot/etc/fstab | grep -v "#" | tr -d '[:space:]')" ]; then
\twait_boot_partition
\tmount_boot_partition /sysroot/boot "rw"
fi
'''

REPLACEMENT_HANDOFF_BLOCK = '''wait_root_partition
printf '<6>a33x-u0k-direct-mount: stage=root-ready\\n' > /dev/kmsg 2>/dev/null || true
printf '<6>a33x-u0k-direct-mount: stage=skip-delete-resize-unlock\\n' > /dev/kmsg 2>/dev/null || true
printf '<6>a33x-u0k-direct-mount: stage=mount-root-begin\\n' > /dev/kmsg 2>/dev/null || true
mount_root_partition
printf '<6>a33x-u0k-direct-mount: stage=mount-root-success\\n' > /dev/kmsg 2>/dev/null || true
resize_filesystem_after_mount /sysroot
printf '<6>a33x-u0k-direct-mount: stage=after-mount-resize-hook\\n' > /dev/kmsg 2>/dev/null || true
printf '<6>a33x-u0k-direct-mount: stage=skip-legacy-boot-mount\\n' > /dev/kmsg 2>/dev/null || true
'''

ORIGINAL_SWITCH_BLOCK = '''# Switch root
run_hooks /hooks-cleanup

echo "Switching root"
'''

REPLACEMENT_SWITCH_BLOCK = '''# Switch root
printf '<6>a33x-u0k-direct-mount: stage=cleanup-hooks-begin\\n' > /dev/kmsg 2>/dev/null || true
run_hooks /hooks-cleanup
printf '<6>a33x-u0k-direct-mount: stage=cleanup-hooks-done\\n' > /dev/kmsg 2>/dev/null || true
printf '<6>a33x-u0k-direct-mount: stage=switch-root-begin\\n' > /dev/kmsg 2>/dev/null || true

echo "Switching root"
'''


class Refusal(RuntimeError):
    pass


def refuse(message: str) -> None:
    raise Refusal(message)


def executable_calls(text: str, command: str) -> list[str]:
    calls: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == command or stripped.startswith(command + " "):
            calls.append(stripped)
    return calls


def patch_second_stage(text: str) -> str:
    if text.count(ORIGINAL_HANDOFF_BLOCK) != 1:
        refuse("U0j second-stage handoff block does not match exactly once")
    if text.count(ORIGINAL_SWITCH_BLOCK) != 1:
        refuse("U0j switch-root block does not match exactly once")

    patched = text.replace(ORIGINAL_HANDOFF_BLOCK, REPLACEMENT_HANDOFF_BLOCK)
    patched = patched.replace(ORIGINAL_SWITCH_BLOCK, REPLACEMENT_SWITCH_BLOCK)

    for command in SKIPPED_CALLS:
        calls = executable_calls(patched, command)
        if calls:
            refuse(f"skipped second-stage call remains executable: {command}: {calls}")
    if executable_calls(patched, "wait_root_partition") != ["wait_root_partition"]:
        refuse("wait_root_partition is not retained exactly once")
    if executable_calls(patched, "mount_root_partition") != ["mount_root_partition"]:
        refuse("mount_root_partition is not retained exactly once")
    if executable_calls(patched, "resize_filesystem_after_mount") != [
        "resize_filesystem_after_mount /sysroot"
    ]:
        refuse("post-mount resize hook is not retained exactly once")
    if patched.count('exec switch_root /sysroot "$init"') != 1:
        refuse("switch_root execution is not retained exactly once")

    for marker in MARKERS:
        token = f"{MARKER_PREFIX}: stage={marker}"
        if patched.count(token) != 1:
            refuse(f"stage marker is missing or duplicated: {token}")

    order = (
        patched.index(f"{MARKER_PREFIX}: stage=root-ready"),
        patched.index(f"{MARKER_PREFIX}: stage=mount-root-begin"),
        patched.index("\nmount_root_partition\n"),
        patched.index(f"{MARKER_PREFIX}: stage=mount-root-success"),
        patched.index(f"{MARKER_PREFIX}: stage=switch-root-begin"),
        patched.index('exec switch_root /sysroot "$init"'),
    )
    if tuple(sorted(order)) != order:
        refuse("U0k root mount and switch_root marker order is invalid")
    return patched


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build U0k direct-mount isolation from exact U0j initramfs"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()

    if u0j.git_blob(repo, U0J_BUILDER) != EXPECTED_U0J_BUILDER_BLOB:
        refuse("U0j builder changed unexpectedly; review U0k ancestry")

    u0j_manifest_path = root / "build/candidates/a33x-h1-usbpd-u0j-root-api-compatible-manifest.txt"
    u0j_initramfs = root / "export-u0j-root-api-compatible/initramfs"
    u0h_report_path = root / "build/u0h-userdata-root-node.txt"
    u0g_report_path = root / "build/u0g-muic-dynamic.txt"
    output_image = root / "export-u0k-direct-mount-isolation/initramfs"
    inspect = root / "build/u0k-direct-mount-isolation-inspection"
    patch_report = root / "build/u0k-direct-mount-isolation-patch.txt"
    recovery_output = root / "build/pmos-debug-recovery-u0k-direct-mount-isolation"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0k-direct-mount-isolation-recovery.img"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0k-direct-mount-isolation-manifest.txt"

    for path in (u0j_manifest_path, u0j_initramfs, u0h_report_path, u0g_report_path):
        if not path.is_file():
            refuse(f"missing U0k input: {path}")

    u0j_manifest = v2.kv(u0j_manifest_path)
    v2.require(
        u0j_manifest,
        {
            "candidate": "U0j-root-api-compatible",
            "implementation_language": "python3",
            "functional_delta": "make-find-root-partition-support-stdout-and-output-variable",
            "cpio_payload_delta": "init_functions.sh",
            "shell_delta": "find_root_partition",
            "find_root_stdout_api": "passed",
            "find_root_output_variable_api": "partition",
            "caller_local_partition_contract": "passed",
            "embedded_modules": str(MODULES),
            "preparation_status": "passed",
            "phone_partition_writes": "no",
            "build_status": "passed",
        },
        "U0j manifest",
    )
    if v2.sha_file(u0j_initramfs) != u0j_manifest.get("u0j_initramfs_sha256"):
        refuse("U0j initramfs differs from its manifest")

    compressed = u0j_initramfs.read_bytes()
    try:
        base = v2.Archive.parse(gzip.decompress(compressed))
    except (OSError, v2.CpioError) as exc:
        refuse(f"cannot parse U0j initramfs: {exc}")

    init2_entry = base.one(TARGET)
    init2 = init2_entry.data.decode("utf-8", "strict")
    patched_init2 = patch_second_stage(init2)

    inspect.mkdir(parents=True, exist_ok=True)
    (inspect / "original-init_2nd.sh").write_text(init2, encoding="utf-8")
    syntax_file = inspect / "patched-init_2nd.sh"
    syntax_file.write_text(patched_init2, encoding="utf-8")
    subprocess.run(["sh", "-n", str(syntax_file)], check=True)
    (inspect / "removed-second-stage-calls.txt").write_text(
        "\n".join(SKIPPED_CALLS) + "\n", encoding="utf-8"
    )
    (inspect / "stage-markers.txt").write_text(
        "\n".join(f"{MARKER_PREFIX}: stage={marker}" for marker in MARKERS) + "\n",
        encoding="utf-8",
    )

    patched_payload = base.replace(TARGET, patched_init2.encode())
    patched = v2.Archive.parse(patched_payload)
    base.assert_only_payload_changed(patched, TARGET)
    if v2.count_modules(base) != MODULES or v2.count_modules(patched) != MODULES:
        refuse("module count changed or is not 67")

    u0h, u0g = v2.kv(u0h_report_path), v2.kv(u0g_report_path)
    v2.one_hash(
        patched,
        "usr/libexec/a33x-muic-switch-dynamic",
        u0g.get("dynamic_helper_sha256", ""),
    )
    v2.one_hash(
        patched,
        "hooks/03-a33x-muic-switch-dynamic.sh",
        u0g.get("dynamic_hook03_sha256", ""),
    )
    v2.one_hash(
        patched,
        "hooks/04-a33x-muic-persist-dynamic.sh",
        u0g.get("dynamic_hook04_sha256", ""),
    )
    v2.one_hash(
        patched,
        "hooks/05-a33x-userdata-root-node.sh",
        u0h.get("root_node_hook_sha256", ""),
    )
    if patched.one("init_functions.sh").data != base.one("init_functions.sh").data:
        refuse("U0j root API function payload changed unexpectedly")
    if patched.one("init_functions_2nd.sh").data != base.one("init_functions_2nd.sh").data:
        refuse("root filesystem helper functions changed unexpectedly")

    output_image.parent.mkdir(parents=True, exist_ok=True)
    output_image.write_bytes(gzip.compress(patched_payload, compresslevel=9, mtime=0))
    roundtrip = v2.Archive.parse(gzip.decompress(output_image.read_bytes()))
    if roundtrip.one(TARGET).data != patched_init2.encode() or roundtrip.tail != base.tail:
        refuse("written U0k initramfs did not round-trip")

    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        refuse("cannot resolve repository commit")
    created = subprocess.run(
        ["date", "-Ins"], check=True, text=True, stdout=subprocess.PIPE
    ).stdout.strip()

    common_pairs: list[tuple[str, object]] = [
        ("created", created),
        ("linuxa33_commit", commit),
        ("implementation_language", "python3"),
        ("functional_base", "U0j-root-api-compatible"),
        ("u0j_initramfs", u0j_initramfs),
        ("u0j_initramfs_sha256", v2.sha_bytes(compressed)),
        ("u0k_initramfs", output_image),
        ("u0k_initramfs_sha256", v2.sha_file(output_image)),
        ("cpio_entry_count", len(base.entries)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_metadata_preserved_except_target_size_and_crc", "yes"),
        ("cpio_payload_delta", TARGET),
        ("shell_delta", "second-stage-root-handoff-sequence-only"),
        ("shell_text_outside_two_exact_blocks_preserved", "yes"),
        ("root_discovery_preserved", "yes"),
        ("rootfs_check_skipped", "yes"),
        ("rootfs_partition_resize_skipped", "yes"),
        ("rootfs_filesystem_resize_skipped", "yes"),
        ("rootfs_unlock_skipped", "yes"),
        ("old_install_partition_delete_skipped", "yes"),
        ("legacy_boot_partition_mount_skipped", "yes"),
        ("mount_root_partition_retained", "yes"),
        ("post_mount_resize_hook_retained", "yes"),
        ("switch_root_retained", "yes"),
        ("skipped_second_stage_calls", ",".join(SKIPPED_CALLS)),
        ("stage_markers", ",".join(MARKERS)),
        ("original_init_2nd_sha256", v2.sha_bytes(init2_entry.data)),
        ("patched_init_2nd_sha256", v2.sha_bytes(patched_init2.encode())),
        ("embedded_modules", MODULES),
        ("phone_partition_writes", "no"),
    ]
    v2.write_report(
        patch_report,
        [("operation", "python-byte-preserving-direct-mount-isolation")]
        + common_pairs
        + [("patch_status", "passed")],
    )

    recovery = v2.build_recovery(root, repo, output_image, recovery_output)
    info = recovery_output / "final-boot-info.txt"
    if not info.is_file() or re.search(r"(?:^|\s)pmos_root=\S+", info.read_text(errors="replace")):
        refuse("recovery command line validation failed")
    if recovery.stat().st_size != 100663296:
        refuse(f"unexpected recovery size: {recovery.stat().st_size}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(recovery, candidate)

    manifest_pairs: list[tuple[str, object]] = [
        ("candidate", "U0k-direct-mount-isolation"),
        (
            "functional_delta",
            "skip-first-boot-resize-and-legacy-boot-mount-then-directly-mount-root",
        ),
        ("kernel_cmdline_delta", "none"),
        ("module_delta", "none"),
        ("patch_report", patch_report),
        ("patch_report_sha256", v2.sha_file(patch_report)),
    ] + common_pairs + [
        ("preparation_status", "passed"),
        ("recovery", candidate),
        ("recovery_size", candidate.stat().st_size),
        ("recovery_sha256", v2.sha_file(candidate)),
        ("build_status", "passed"),
    ]
    v2.write_report(manifest, manifest_pairs)
    print(f"\nCandidate: {candidate}\nManifest: {manifest}\nNo phone partition was written.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        Refusal,
        u0j.Refusal,
        v2.Refusal,
        v2.CpioError,
        v2.ShellContractError,
        UnicodeDecodeError,
    ) as exc:
        print(f"REFUSING U0k: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        print(f"REFUSING U0k: command failed rc={exc.returncode}: {exc.cmd}", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
