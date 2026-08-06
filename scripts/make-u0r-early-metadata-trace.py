#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
U0Q_BASE_PATH = HERE / "make-u0q-emergency-ssh.py"
EXPECTED_U0Q_BASE_BLOB = "fa662b03cf3a4e4c9166ebc9fa0a177dc12dbdb4"

CANDIDATE = "U0r-early-metadata-trace"
INIT_TARGET = "init_2nd.sh"
HOOK04_TARGET = "hooks/04-a33x-muic-persist-dynamic.sh"
HOOK05_TARGET = "hooks/05-a33x-userdata-root-node.sh"
WATCHDOG_TARGET = "hooks/01-a33x-watchdog.sh"
MODULES = 67
TRACE_RELATIVE = "a33x-bringup/u0r-init2-trace.txt"
HOOK04_RELATIVE = "a33x-bringup/u0r-hook04-muic-result.txt"
HOOK05_RELATIVE = "a33x-bringup/u0r-hook05-root-node-result.txt"
MARKER_PREFIX = "a33x-u0r-early-trace"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0q = load("a33_u0r_parent_u0q", U0Q_BASE_PATH)
v2 = u0q.v2


class Refusal(RuntimeError):
    pass


def refuse(message: str) -> None:
    raise Refusal(message)


def git_blob(repo: Path, path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


TRACE_HELPER = r'''
# U0r metadata-only early trace. Every call mounts Android metadata briefly,
# appends one bounded stage record, syncs, and unmounts. Failures are reported
# to kmsg but never change the inherited U0p boot control flow.
U0R_TRACE_RELATIVE=a33x-bringup/u0r-init2-trace.txt
U0R_METADATA_MOUNT=/run/a33x-u0r-init2-metadata

u0r_kmsg()
{
    printf '<6>a33x-u0r-early-trace: %s\n' "$*" > /dev/kmsg 2>/dev/null || true
}

u0r_valid_devnum()
{
    case "$1" in
        ''|*[!0-9:]*|:*|*:)
            return 1
            ;;
    esac
    [ "${1#*:}" != "$1" ]
}

u0r_create_metadata_node()
{
    [ -r /sys/class/block/sda26/dev ] || return 1
    u0r_devnum="$(/bin/busybox cat /sys/class/block/sda26/dev 2>/dev/null || true)"
    u0r_valid_devnum "$u0r_devnum" || return 1
    u0r_major="${u0r_devnum%%:*}"
    u0r_minor="${u0r_devnum##*:}"
    /bin/busybox mkdir -p /dev/block
    if [ ! -e /dev/block/sda26 ]; then
        /bin/busybox mknod /dev/block/sda26 b "$u0r_major" "$u0r_minor" 2>/dev/null || true
    fi
    [ -b /dev/block/sda26 ]
}

u0r_trace()
{
    u0r_mode="$1"
    u0r_stage="$2"
    shift 2
    u0r_detail="$*"
    u0r_boot_id="$(/bin/busybox cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)"
    u0r_uptime="$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)"
    u0r_kmsg "boot_id=${u0r_boot_id:-unknown} uptime=${u0r_uptime:-unknown} stage=$u0r_stage detail=$u0r_detail"

    u0r_metadata_device=""
    u0r_attempt=0
    while [ "$u0r_attempt" -lt 30 ]; do
        for u0r_candidate in /dev/block/by-name/metadata /dev/block/sda26 /dev/sda26; do
            if [ -b "$u0r_candidate" ]; then
                u0r_metadata_device="$u0r_candidate"
                break
            fi
        done
        [ -n "$u0r_metadata_device" ] && break
        if u0r_create_metadata_node; then
            u0r_metadata_device=/dev/block/sda26
            break
        fi
        u0r_attempt=$((u0r_attempt + 1))
        /bin/busybox sleep 0.1
    done
    if [ -z "$u0r_metadata_device" ]; then
        u0r_kmsg "error=metadata-device-missing stage=$u0r_stage"
        return 0
    fi

    u0r_resolved="$(/bin/busybox readlink -f "$u0r_metadata_device" 2>/dev/null || true)"
    u0r_existing="$(/bin/busybox awk -v a="$u0r_metadata_device" -v b="$u0r_resolved" '$1==a || $1==b {print $2; exit}' /proc/mounts 2>/dev/null || true)"
    u0r_mounted_here=no
    if [ -n "$u0r_existing" ]; then
        u0r_root="$u0r_existing"
    else
        /bin/busybox mkdir -p "$U0R_METADATA_MOUNT"
        /bin/busybox umount "$U0R_METADATA_MOUNT" 2>/dev/null || true
        if ! /bin/busybox mount -t ext4 -o rw,nosuid,nodev,noatime "$u0r_metadata_device" "$U0R_METADATA_MOUNT"; then
            u0r_kmsg "error=metadata-mount-failed stage=$u0r_stage device=$u0r_metadata_device"
            return 0
        fi
        u0r_root="$U0R_METADATA_MOUNT"
        u0r_mounted_here=yes
    fi

    u0r_trace_path="$u0r_root/$U0R_TRACE_RELATIVE"
    u0r_trace_dir="${u0r_trace_path%/*}"
    if ! /bin/busybox mkdir -p "$u0r_trace_dir"; then
        u0r_kmsg "error=trace-directory-create-failed stage=$u0r_stage"
    elif [ "$u0r_mode" = reset ]; then
        u0r_temporary="$u0r_trace_path.tmp"
        {
            printf 'candidate=U0r-early-metadata-trace\n'
            printf 'trace_version=1\n'
            printf 'boot_id=%s\n' "${u0r_boot_id:-unknown}"
            printf 'metadata_device=%s\n' "$u0r_metadata_device"
            printf 'metadata_resolved=%s\n' "${u0r_resolved:-unknown}"
            printf 'persistent_write_scope=metadata-trace-only\n'
            printf 'event boot_id=%s uptime=%s stage=%s detail=%s\n' \
                "${u0r_boot_id:-unknown}" "${u0r_uptime:-unknown}" "$u0r_stage" "$u0r_detail"
        } > "$u0r_temporary" 2>/dev/null || true
        /bin/busybox chmod 0600 "$u0r_temporary" 2>/dev/null || true
        /bin/busybox sync 2>/dev/null || true
        /bin/busybox mv -f "$u0r_temporary" "$u0r_trace_path" 2>/dev/null || \
            u0r_kmsg "error=trace-reset-rename-failed stage=$u0r_stage"
        /bin/busybox sync 2>/dev/null || true
    else
        if [ ! -f "$u0r_trace_path" ]; then
            {
                printf 'candidate=U0r-early-metadata-trace\n'
                printf 'trace_version=1\n'
                printf 'boot_id=%s\n' "${u0r_boot_id:-unknown}"
                printf 'trace_recovered_without_reset=yes\n'
            } > "$u0r_trace_path" 2>/dev/null || true
            /bin/busybox chmod 0600 "$u0r_trace_path" 2>/dev/null || true
        fi
        printf 'event boot_id=%s uptime=%s stage=%s detail=%s\n' \
            "${u0r_boot_id:-unknown}" "${u0r_uptime:-unknown}" "$u0r_stage" "$u0r_detail" \
            >> "$u0r_trace_path" 2>/dev/null || \
            u0r_kmsg "error=trace-append-failed stage=$u0r_stage"
        /bin/busybox sync 2>/dev/null || true
    fi

    if [ "$u0r_mounted_here" = yes ]; then
        /bin/busybox umount "$U0R_METADATA_MOUNT" 2>/dev/null || \
            u0r_kmsg "error=metadata-unmount-failed stage=$u0r_stage"
    fi
    return 0
}

u0r_trace reset init2-entry "pid=$$"
'''


def patch_hook04(original: str) -> str:
    replacements = (
        (
            "relative_file=u0g-muic-result.txt",
            "relative_file=u0r-hook04-muic-result.txt",
        ),
        (
            '    echo "candidate=U0g-muic-dynamic"',
            '    echo "candidate=U0r-early-metadata-trace"\n'
            '    echo "experiment_role=hook04-after-dynamic-muic"',
        ),
    )
    patched = original
    for before, after in replacements:
        if patched.count(before) != 1:
            refuse(f"U0r hook04 anchor missing or duplicated: {before!r}")
        patched = patched.replace(before, after, 1)
    if "u0g-muic-result.txt" in patched:
        refuse("U0r hook04 retained the historical U0g result filename")
    return patched


def patch_hook05(original: str) -> str:
    replacements = (
        (
            "metadata_relative=a33x-bringup/u0h-root-node-result.txt",
            "metadata_relative=a33x-bringup/u0r-hook05-root-node-result.txt",
        ),
        (
            "record candidate U0h-userdata-root-node",
            "record candidate U0r-early-metadata-trace\n"
            "record experiment_role hook05-root-node",
        ),
    )
    patched = original
    for before, after in replacements:
        if patched.count(before) != 1:
            refuse(f"U0r hook05 anchor missing or duplicated: {before!r}")
        patched = patched.replace(before, after, 1)
    if "u0h-root-node-result.txt" in patched:
        refuse("U0r hook05 retained the historical U0h result filename")
    return patched


def replace_once(text: str, anchor: str, replacement: str, label: str) -> str:
    if text.count(anchor) != 1:
        refuse(f"U0r init_2nd anchor missing or duplicated: {label}")
    return text.replace(anchor, replacement, 1)


def patch_init_second(original: str) -> str:
    if v2.sha_bytes(original.encode()) != u0q.EXPECTED_U0P_INIT2_SHA256:
        refuse("exact U0p init_2nd.sh hash mismatch")
    if MARKER_PREFIX in original or TRACE_RELATIVE in original:
        refuse("U0r metadata trace is already present")
    if not original.startswith("#!/bin/sh\n"):
        refuse("U0p init_2nd.sh has an unexpected shebang")
    inherited_sshd = u0q.u0p.embedded_sshd_bytes(original)

    patched = original.replace("#!/bin/sh\n", "#!/bin/sh\n" + TRACE_HELPER + "\n", 1)
    patched = replace_once(
        patched,
        "\nwait_root_partition\n",
        "\nu0r_trace append wait-root-begin \"\"\n"
        "wait_root_partition\n"
        "u0r_trace append wait-root-done \"\"\n",
        "wait-root",
    )
    mount_block = (
        "printf '<6>a33x-u0k-direct-mount: stage=mount-root-begin\\n' > /dev/kmsg 2>/dev/null || true\n"
        "mount_root_partition\n"
        "printf '<6>a33x-u0k-direct-mount: stage=mount-root-success\\n' > /dev/kmsg 2>/dev/null || true\n"
    )
    patched = replace_once(
        patched,
        mount_block,
        "printf '<6>a33x-u0k-direct-mount: stage=mount-root-begin\\n' > /dev/kmsg 2>/dev/null || true\n"
        "u0r_trace append mount-root-begin \"\"\n"
        "mount_root_partition\n"
        "printf '<6>a33x-u0k-direct-mount: stage=mount-root-success\\n' > /dev/kmsg 2>/dev/null || true\n"
        "u0r_trace append mount-root-success \"\"\n",
        "mount-root",
    )
    cleanup_block = (
        "printf '<6>a33x-u0k-direct-mount: stage=cleanup-hooks-begin\\n' > /dev/kmsg 2>/dev/null || true\n"
        "run_hooks /hooks-cleanup\n"
        "printf '<6>a33x-u0k-direct-mount: stage=cleanup-hooks-done\\n' > /dev/kmsg 2>/dev/null || true\n"
        "printf '<6>a33x-u0k-direct-mount: stage=switch-root-begin\\n' > /dev/kmsg 2>/dev/null || true\n"
    )
    patched = replace_once(
        patched,
        cleanup_block,
        "printf '<6>a33x-u0k-direct-mount: stage=cleanup-hooks-begin\\n' > /dev/kmsg 2>/dev/null || true\n"
        "u0r_trace append cleanup-hooks-begin \"\"\n"
        "run_hooks /hooks-cleanup\n"
        "printf '<6>a33x-u0k-direct-mount: stage=cleanup-hooks-done\\n' > /dev/kmsg 2>/dev/null || true\n"
        "u0r_trace append cleanup-hooks-done \"\"\n"
        "printf '<6>a33x-u0k-direct-mount: stage=switch-root-begin\\n' > /dev/kmsg 2>/dev/null || true\n"
        "u0r_trace append switch-root-begin \"\"\n",
        "cleanup-switch",
    )
    setup_anchor = "U0N_SSHD_SOURCE=/run/a33x-u0n-sshd.initd\n"
    patched = replace_once(
        patched,
        setup_anchor,
        "u0r_trace append u0p-setup-prefix-reached \"\"\n" + setup_anchor,
        "u0p-setup-prefix",
    )
    setup_success = (
        'u0o_pre_trace 6 "stage=setup-success original=$u0n_original_sha instrumented=$u0n_target_sha"\n'
    )
    patched = replace_once(
        patched,
        setup_success,
        setup_success + 'u0r_trace append u0p-setup-success ""\n',
        "u0p-setup-success",
    )
    switch_ready = 'u0o_pre_trace 6 "stage=switch-root-ready"\n'
    patched = replace_once(
        patched,
        switch_ready,
        switch_ready + 'u0r_trace append u0p-switch-root-ready ""\n',
        "u0p-switch-root-ready",
    )
    switch_exec = 'exec switch_root /sysroot "$init"'
    patched = replace_once(
        patched,
        switch_exec,
        'u0r_trace append exec-switch-root "init=$init"\n' + switch_exec,
        "switch-root-exec",
    )

    if u0q.u0p.embedded_sshd_bytes(patched) != inherited_sshd:
        refuse("U0r changed inherited OpenRC sshd instrumentation bytes")
    required = (
        "u0r_trace reset init2-entry",
        "wait-root-begin",
        "wait-root-done",
        "mount-root-begin",
        "mount-root-success",
        "cleanup-hooks-begin",
        "cleanup-hooks-done",
        "u0p-setup-prefix-reached",
        "u0p-setup-success",
        "u0p-switch-root-ready",
        "exec-switch-root",
    )
    for token in required:
        if token not in patched:
            refuse(f"U0r generated stage token missing: {token}")
    return patched


def assert_exact_payload_changes(before, after, expected: set[str]) -> None:
    if len(before.entries) != len(after.entries) or before.tail != after.tail:
        refuse("U0r changed CPIO entry count or trailer tail")
    changed: set[str] = set()
    for old, new in zip(before.entries, after.entries, strict=True):
        old_meta = (old.name, old.mode, old.nlink, old.ino, old.devmajor, old.devminor)
        new_meta = (new.name, new.mode, new.nlink, new.ino, new.devmajor, new.devminor)
        if old_meta != new_meta:
            refuse(f"U0r changed CPIO metadata for {old.name}")
        if v2.sha_bytes(old.data) != v2.sha_bytes(new.data):
            changed.add(old.normalized)
    if changed != expected:
        refuse(f"unexpected U0r payload delta: {sorted(changed)}")


def run_text(args: list[str]) -> str:
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build U0r from exact U0p with metadata-only early boot tracing"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    if git_blob(repo, U0Q_BASE_PATH) != EXPECTED_U0Q_BASE_BLOB:
        refuse("checked-in U0q base builder changed")
    parent_manifest_path, parent_initramfs, _, _ = u0q.validate_parent(root, repo)
    try:
        before = v2.Archive.parse(gzip.decompress(parent_initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        refuse(f"cannot parse exact U0p initramfs: {exc}")

    original_init = before.one(INIT_TARGET).data.decode("utf-8", errors="strict")
    original_hook04 = before.one(HOOK04_TARGET).data.decode("utf-8", errors="strict")
    original_hook05 = before.one(HOOK05_TARGET).data.decode("utf-8", errors="strict")
    patched_init = patch_init_second(original_init)
    patched_hook04 = patch_hook04(original_hook04)
    patched_hook05 = patch_hook05(original_hook05)

    payload = before.replace(HOOK04_TARGET, patched_hook04.encode())
    interim = v2.Archive.parse(payload)
    payload = interim.replace(HOOK05_TARGET, patched_hook05.encode())
    interim = v2.Archive.parse(payload)
    payload = interim.replace(INIT_TARGET, patched_init.encode())
    after = v2.Archive.parse(payload)
    assert_exact_payload_changes(
        before, after, {HOOK04_TARGET, HOOK05_TARGET, INIT_TARGET}
    )
    if before.one(WATCHDOG_TARGET).data != after.one(WATCHDOG_TARGET).data:
        refuse("U0r changed the proven watchdog hook")
    if v2.count_modules(before) != MODULES or v2.count_modules(after) != MODULES:
        refuse("U0r module count changed or is not 67")

    output_initramfs = root / "export-u0r-early-metadata-trace/initramfs"
    inspect_dir = root / "build/u0r-early-metadata-trace-inspection"
    patch_report = root / "build/u0r-early-metadata-trace-patch.txt"
    recovery_output = root / "build/pmos-debug-recovery-u0r-early-metadata-trace"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0r-early-metadata-trace-recovery.img"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0r-early-metadata-trace-manifest.txt"
    for path in (output_initramfs, patch_report, candidate, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    inspect_dir.mkdir(parents=True, exist_ok=True)

    syntax_files = (
        ("u0r-init_2nd.sh", patched_init),
        ("u0r-hook04.sh", patched_hook04),
        ("u0r-hook05.sh", patched_hook05),
    )
    for name, text in syntax_files:
        path = inspect_dir / name
        path.write_text(text, encoding="utf-8")
        subprocess.run(["sh", "-n", str(path)], check=True)

    output_initramfs.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))
    roundtrip = v2.Archive.parse(gzip.decompress(output_initramfs.read_bytes()))
    for target, expected in (
        (INIT_TARGET, patched_init.encode()),
        (HOOK04_TARGET, patched_hook04.encode()),
        (HOOK05_TARGET, patched_hook05.encode()),
    ):
        if roundtrip.one(target).data != expected:
            refuse(f"written U0r initramfs did not round-trip: {target}")
    if roundtrip.tail != before.tail:
        refuse("U0r changed initramfs trailer tail")

    commit = run_text(["git", "-C", str(repo), "rev-parse", "HEAD"])
    created = run_text(["date", "-Ins"])
    common: list[tuple[str, object]] = [
        ("created", created),
        ("linuxa33_commit", commit),
        ("implementation_language", "python3"),
        ("functional_base", "U0p-corrected-sshd-source-hash"),
        ("u0p_manifest", parent_manifest_path),
        ("u0p_manifest_sha256", v2.sha_file(parent_manifest_path)),
        ("u0p_initramfs", parent_initramfs),
        ("u0p_initramfs_sha256", v2.sha_file(parent_initramfs)),
        ("u0r_initramfs", output_initramfs),
        ("u0r_initramfs_sha256", v2.sha_file(output_initramfs)),
        ("cpio_entry_count", len(before.entries)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_payload_delta", ",".join((HOOK04_TARGET, HOOK05_TARGET, INIT_TARGET))),
        ("shell_delta", "unique-hook-results-and-metadata-only-init2-stage-trace"),
        ("watchdog_hook_preserved", "yes"),
        ("normal_openrc_sshd_instrumentation_preserved", "yes"),
        ("metadata_trace_path", f"/{TRACE_RELATIVE}"),
        ("metadata_hook04_path", f"/{HOOK04_RELATIVE}"),
        ("metadata_hook05_path", f"/{HOOK05_RELATIVE}"),
        ("runtime_persistent_write_partition", "metadata"),
        ("runtime_persistent_write_scope", "three-u0r-diagnostic-files"),
        ("userdata_runtime_delta", "none-added-by-u0r"),
        ("embedded_modules", MODULES),
        ("phone_partition_writes", "no"),
    ]
    v2.write_report(
        patch_report,
        [("operation", "build-u0r-early-metadata-trace")]
        + common
        + [
            ("original_init_2nd_sha256", v2.sha_bytes(original_init.encode())),
            ("patched_init_2nd_sha256", v2.sha_bytes(patched_init.encode())),
            ("original_hook04_sha256", v2.sha_bytes(original_hook04.encode())),
            ("patched_hook04_sha256", v2.sha_bytes(patched_hook04.encode())),
            ("original_hook05_sha256", v2.sha_bytes(original_hook05.encode())),
            ("patched_hook05_sha256", v2.sha_bytes(patched_hook05.encode())),
            ("syntax_validation", "passed"),
            ("patch_status", "passed"),
        ],
    )

    recovery = v2.build_recovery(root, repo, output_initramfs, recovery_output)
    if recovery.stat().st_size != 100663296:
        refuse(f"unexpected recovery size: {recovery.stat().st_size}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(recovery, candidate)

    v2.write_report(
        manifest,
        [
            ("candidate", CANDIDATE),
            ("functional_delta", "metadata-only-stage-trace-before-rootfs-debugging"),
            ("kernel_cmdline_delta", "none"),
            ("module_delta", "none"),
            ("patch_report", patch_report),
            ("patch_report_sha256", v2.sha_file(patch_report)),
        ]
        + common
        + [
            ("recovery", candidate),
            ("recovery_size", candidate.stat().st_size),
            ("recovery_sha256", v2.sha_file(candidate)),
            ("build_status", "passed"),
        ],
    )

    print(f"candidate={candidate}")
    print(f"candidate_sha256={v2.sha_file(candidate)}")
    print(f"manifest={manifest}")
    print(f"manifest_sha256={v2.sha_file(manifest)}")
    print(f"patch_report={patch_report}")
    print(f"patch_report_sha256={v2.sha_file(patch_report)}")
    print(f"metadata_trace_path=/{TRACE_RELATIVE}")
    print(f"metadata_hook04_path=/{HOOK04_RELATIVE}")
    print(f"metadata_hook05_path=/{HOOK05_RELATIVE}")
    print("runtime_persistent_write_partition=metadata")
    print("u0r_build_status=passed")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        Refusal,
        u0q.Refusal,
        u0q.u0p.Refusal,
        v2.CpioError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"U0r BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
