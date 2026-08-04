#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
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
    # Let the existing implementation create the shell-function evidence first.
    # It may fail only at its rigid bin/busybox lookup; reconstruct the archive
    # and resolve the exact binaries through the verified generic resolver.
    try:
        return _original_extract(initramfs, out)
    except base.Refusal as exc:
        if "does not contain bin/busybox" not in str(exc):
            raise
    except base.CpioError as exc:
        if "busybox" not in str(exc):
            raise

    import gzip
    archive = base.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    u0h_report = base.common.kv(
        Path.home() / "a33-port/build/u0h-userdata-root-node.txt"
    )
    try:
        binaries, evidence = resolve_verified_busyboxes(
            archive=archive,
            root=Path.home() / "a33-port",
            home=Path.home(),
            report_values=u0h_report,
            output_dir=out,
        )
    except BusyBoxResolutionError as exc:
        raise base.Refusal(str(exc)) from exc
    base.write_text(out / "busybox-resolution.txt", "\n".join(evidence) + "\n")
    return binaries


base.extract_initramfs_artifacts = extract_initramfs_artifacts

if __name__ == "__main__":
    try:
        raise SystemExit(base.main())
    except (base.Refusal, base.common.Refusal, base.CpioError, UnicodeDecodeError) as exc:
        print(f"REFUSING ROOTFS AUDIT: {exc}", file=sys.stderr)
        raise SystemExit(1)
