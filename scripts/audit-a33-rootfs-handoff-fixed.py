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
from a33_rootfs_busybox import BusyBoxResolutionError, resolve_verified_busyboxes

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


base.extract_initramfs_artifacts = extract_initramfs_artifacts

if __name__ == "__main__":
    try:
        raise SystemExit(base.main())
    except (base.Refusal, base.common.Refusal, base.CpioError, UnicodeDecodeError) as exc:
        print(f"REFUSING ROOTFS AUDIT: {exc}", file=sys.stderr)
        raise SystemExit(1)
