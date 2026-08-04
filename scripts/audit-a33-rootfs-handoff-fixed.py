#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import importlib.util
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
LIB = HERE / "lib"
sys.path.insert(0, str(LIB))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load("a33_rootfs_handoff_audit_base", HERE / "audit-a33-rootfs-handoff.py")
from a33_rootfs_busybox import BusyBoxResolutionError, resolve_verified_busyboxes

_original_extract = base.extract_initramfs_artifacts
RUNTIME_DIR = "/tmp/a33-u0j-runtime"


def extract_initramfs_artifacts(initramfs: Path, out: Path):
    """Run the existing extraction, with a SHA-bound BusyBox fallback."""

    try:
        return _original_extract(initramfs, out)
    except base.Refusal as exc:
        if "does not contain bin/busybox" not in str(exc):
            raise
    except base.CpioError as exc:
        if "busybox" not in str(exc).lower():
            raise

    archive = base.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    root = initramfs.parent.parent.resolve()
    u0h_report_path = root / "build/u0h-userdata-root-node.txt"
    if not u0h_report_path.is_file():
        raise base.Refusal(f"missing U0h report for BusyBox binding: {u0h_report_path}")
    u0h_report = base.common.kv(u0h_report_path)

    try:
        binaries, evidence = resolve_verified_busyboxes(
            archive=archive,
            root=root,
            home=Path.home(),
            report_values=u0h_report,
            output_dir=out,
        )
    except BusyBoxResolutionError as exc:
        raise base.Refusal(str(exc)) from exc

    base.write_text(out / "busybox-resolution.txt", "\n".join(evidence) + "\n")

    binary_pattern = re.compile(
        r"(?:^|/)(?:e2fsck|fsck(?:\.ext4)?|resize2fs|"
        r"tune2fs|dumpe2fs|blkid|mount|findfs)$"
    )
    rows: list[str] = []
    for entry in archive.entries:
        if binary_pattern.search(entry.normalized):
            rows.append(
                f"path={entry.normalized} size={len(entry.data)} "
                f"sha256={hashlib.sha256(entry.data).hexdigest()}"
            )
    base.write_text(out / "filesystem-tool-entries.txt", "\n".join(rows) + "\n")
    return binaries


BUSYBOX_SCOPE_FIXTURE = r'''set -eu
find_fixture() {
    a33x_root=/dev/block/sda36
    case "$#" in
        0) printf '%s\n' "$a33x_root" ;;
        1)
            [ "$1" = partition ] || return 2
            partition="$a33x_root"
            ;;
        *) return 2 ;;
    esac
    unset a33x_root
}
consumer() {
    local partition
    partition=BEFORE
    find_fixture partition
    [ "$partition" = /dev/block/sda36 ]
}
consumer
[ "$(find_fixture)" = /dev/block/sda36 ]
echo exact_initramfs_busybox_dynamic_scope=passed
'''


TWRP_FUNCTION_TEST = r'''set -eu
function_file="$1"
PATH=/sbin:/system/bin:/system/xbin
export PATH
. "$function_file"

echo "direct_blkid=$(blkid /dev/block/sda36 2>/dev/null || true)"
stdout_value="$(find_root_partition)"
echo "stdout_rc=$?"
echo "stdout_value=$stdout_value"

consumer() {
    local partition
    partition=BEFORE
    find_root_partition partition
    rc=$?
    echo "output_variable_rc=$rc"
    echo "output_variable_value=$partition"
    [ "$rc" -eq 0 ]
    [ "$partition" = /dev/block/sda36 ]
}
consumer
echo exact_u0j_dual_api_runtime=passed
'''


def _run_exact_busybox_scope_test(binaries: dict[str, Path], out: Path) -> None:
    if set(binaries) != {"busybox", "busybox-extras"}:
        raise base.Refusal(
            f"resolved BusyBox set is incomplete: {sorted(binaries)}"
        )
    pmbootstrap = shutil.which("pmbootstrap")
    if not pmbootstrap:
        raise base.Refusal("pmbootstrap is required for exact BusyBox shell testing")

    completed = base.run_host(
        [pmbootstrap, "chroot", "-r", "--", "/bin/busybox", "sh", "-c", BUSYBOX_SCOPE_FIXTURE],
        timeout=30,
    )
    output = (
        f"command=pmbootstrap chroot -r -- /bin/busybox sh -c <fixture>\n"
        f"returncode={completed.returncode}\n"
        f"resolved_busybox={binaries['busybox']}\n"
        f"resolved_busybox_sha256={base.sha_file(binaries['busybox'])}\n"
        f"=== stdout ===\n{completed.stdout}"
        f"=== stderr ===\n{completed.stderr}"
    )
    base.write_text(out / "exact-initramfs-busybox-shell-semantics.txt", output)
    if completed.returncode != 0 or completed.stdout.count(
        "exact_initramfs_busybox_dynamic_scope=passed"
    ) != 1:
        raise base.Refusal("exact initramfs BusyBox dynamic-scope test failed")


def _remote_metadata(adb: str, serial: str, remote: str) -> tuple[int, str]:
    output = base.common.adb_shell(
        adb,
        serial,
        'set -eu\nstat -c "%s" "$1"\nsha256sum "$1"\n',
        remote,
    ).splitlines()
    if len(output) < 2:
        raise base.Refusal(f"remote upload metadata is incomplete for {remote}: {output!r}")
    try:
        size = int(output[0])
    except ValueError as exc:
        raise base.Refusal(f"invalid remote size for {remote}: {output[0]!r}") from exc
    digest = output[1].split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise base.Refusal(f"invalid remote SHA256 for {remote}: {digest!r}")
    return size, digest


def test_exact_busybox_runtime(
    adb: str,
    serial: str,
    binaries: dict[str, Path],
    out: Path,
) -> None:
    """Prove shell semantics and the exact U0j function without ELF-loader ambiguity."""

    _run_exact_busybox_scope_test(binaries, out)

    local_test = out / "u0j-find-root-runtime-test.sh"
    base.write_text(local_test, TWRP_FUNCTION_TEST)
    find_root = out / "u0j-find_root_partition.sh"
    if not find_root.is_file() or find_root.stat().st_size <= 0:
        raise base.Refusal(f"missing exact U0j function script: {find_root}")

    plan = (
        (find_root, f"{RUNTIME_DIR}/find_root_partition.sh"),
        (local_test, f"{RUNTIME_DIR}/runtime-test.sh"),
    )
    base.common.adb_shell(
        adb,
        serial,
        'set -eu\nrm -rf "$1"\nmkdir -p "$1"\n',
        RUNTIME_DIR,
    )

    evidence: list[str] = []
    try:
        for local, remote in plan:
            base.common.run([adb, "-s", serial, "push", str(local), remote])
            remote_size, remote_sha = _remote_metadata(adb, serial, remote)
            local_size = local.stat().st_size
            local_sha = base.sha_file(local)
            if remote_size != local_size or remote_sha != local_sha:
                raise base.Refusal(
                    f"volatile runtime upload mismatch: local={local} remote={remote} "
                    f"local_size={local_size} remote_size={remote_size} "
                    f"local_sha={local_sha} remote_sha={remote_sha}"
                )
            evidence.append(
                f"runtime_upload={local.name} remote={remote} size={local_size} "
                f"sha256={local_sha} status=verified"
            )

        remote_script = r'''set -eu
dir="$1"
command -v blkid >/dev/null 2>&1 || { echo twrp_blkid=missing; exit 90; }
chmod 755 "$dir/runtime-test.sh"
sh "$dir/runtime-test.sh" "$dir/find_root_partition.sh"
'''
        output = base.common.adb_shell(adb, serial, remote_script, RUNTIME_DIR)
        base.write_text(out / "runtime-upload-verification.txt", "\n".join(evidence) + "\n")
        base.write_text(out / "exact-u0j-find-root-runtime.txt", output)
        if output.count("exact_u0j_dual_api_runtime=passed") != 1:
            raise base.Refusal("exact U0j function test against TWRP sda36 failed")
    finally:
        base.common.adb_shell(
            adb,
            serial,
            'rm -rf "$1" 2>/dev/null || true\n',
            RUNTIME_DIR,
        )


base.extract_initramfs_artifacts = extract_initramfs_artifacts
base.test_exact_busybox_runtime = test_exact_busybox_runtime

if __name__ == "__main__":
    try:
        raise SystemExit(base.main())
    except (base.Refusal, base.common.Refusal, base.CpioError, UnicodeDecodeError) as exc:
        print(f"REFUSING ROOTFS AUDIT: {exc}", file=sys.stderr)
        raise SystemExit(1)
