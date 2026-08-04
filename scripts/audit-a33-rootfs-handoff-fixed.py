#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import importlib.util
from pathlib import Path
import re
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
from a33_rootfs_busybox import (
    BusyBoxResolutionError,
    RUNTIME_DIR,
    build_runtime_upload_plan,
    resolve_verified_busyboxes,
)

_original_extract = base.extract_initramfs_artifacts


def extract_initramfs_artifacts(initramfs: Path, out: Path):
    """Run the existing extraction, with a hash-bound BusyBox fallback.

    The U0h finalization proved that the initramfs BusyBox binaries and the
    pmbootstrap rootfs copies were byte-identical. U0i/U0j changed only
    init_functions.sh. If the lightweight CPIO parser cannot address the
    BusyBox path/hard-link representation directly, use those already-proven
    rootfs bytes after rechecking their recorded SHA256 values.
    """

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
    """Upload, verify and execute the exact U0j BusyBox runtime fixture.

    Every volatile upload is size/SHA-bound before execution. This prevents a
    resolver-key or remote-path mismatch from silently skipping the binaries.
    """

    local_test = out / "u0j-find-root-runtime-test.sh"
    runtime_text = base.RUNTIME_TEST.replace(
        "/tmp/a33-u0j-tools", f"{RUNTIME_DIR}/tools"
    ).replace(
        "/tmp/a33-u0j-find_root_partition.sh",
        f"{RUNTIME_DIR}/find_root_partition.sh",
    )
    base.write_text(local_test, runtime_text)

    try:
        plan = build_runtime_upload_plan(
            binaries=binaries,
            find_root_script=out / "u0j-find_root_partition.sh",
            runtime_test_script=local_test,
        )
    except BusyBoxResolutionError as exc:
        raise base.Refusal(str(exc)) from exc

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
chmod 755 "$dir/busybox" "$dir/busybox-extras" "$dir/runtime-test.sh"
rm -rf "$dir/tools"
mkdir -p "$dir/tools"
provider=""
for candidate in "$dir/busybox" "$dir/busybox-extras"; do
    if "$candidate" --list | grep -qx blkid; then
        provider="$candidate"
        break
    fi
done
[ -n "$provider" ] || { echo blkid_provider=missing; exit 90; }
ln -s "$provider" "$dir/tools/blkid"
echo "blkid_provider=$provider"
"$dir/busybox" sh "$dir/runtime-test.sh"
'''
        output = base.common.adb_shell(adb, serial, remote_script, RUNTIME_DIR)
        base.write_text(out / "runtime-upload-verification.txt", "\n".join(evidence) + "\n")
        base.write_text(out / "exact-u0j-find-root-runtime.txt", output)
        if output.count("exact_u0j_dual_api_runtime=passed") != 1:
            raise base.Refusal("exact embedded BusyBox dual-API runtime test failed")
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
