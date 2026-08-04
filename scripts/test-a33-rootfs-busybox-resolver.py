#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import tempfile

from lib.a33_rootfs_busybox import BusyBoxResolutionError, resolve_verified_busyboxes


class Entry:
    def __init__(self, name: str, data: bytes):
        self.normalized = name
        self.data = data


class Archive:
    def __init__(self, entries):
        self.entries = entries


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    home = root / "home"
    output = root / "out"
    rootfs_bin = home / ".local/var/pmbootstrap/chroot_rootfs_samsung-a33x/bin"
    rootfs_bin.mkdir(parents=True)
    output.mkdir()

    busybox = b"busybox-fixture"
    extras = b"busybox-extras-fixture"
    (rootfs_bin / "busybox").write_bytes(busybox)
    (rootfs_bin / "busybox-extras").write_bytes(extras)

    import hashlib
    report = {
        "busybox_binary_sha256": hashlib.sha256(busybox).hexdigest(),
        "busybox_extras_binary_sha256": hashlib.sha256(extras).hexdigest(),
    }

    binaries, evidence = resolve_verified_busyboxes(
        archive=Archive([Entry("odd/path/busybox", b"")]),
        root=root,
        home=home,
        report_values=report,
        output_dir=output,
    )
    assert binaries["busybox"].read_bytes() == busybox
    assert binaries["busybox-extras"].read_bytes() == extras
    assert any("rootfs-verified-by-u0h" in line for line in evidence)

    direct_out = root / "direct"
    direct_out.mkdir()
    binaries, evidence = resolve_verified_busyboxes(
        archive=Archive([
            Entry("unusual/hardlink-name/busybox", busybox),
            Entry("another/location/busybox-extras", extras),
        ]),
        root=root,
        home=home,
        report_values=report,
        output_dir=direct_out,
    )
    assert binaries["busybox"].read_bytes() == busybox
    assert any("source=cpio:" in line for line in evidence)

    bad_report = dict(report)
    bad_report["busybox_binary_sha256"] = "0" * 64
    try:
        resolve_verified_busyboxes(
            archive=Archive([]),
            root=root,
            home=home,
            report_values=bad_report,
            output_dir=root / "bad",
        )
    except (BusyBoxResolutionError, FileNotFoundError):
        pass
    else:
        raise AssertionError("mismatching fallback hash was accepted")

print("busybox_resolver_self_test=passed")
print("cpio_basename_discovery=passed")
print("u0h_hash_bound_rootfs_fallback=passed")
print("mismatching_hash_refusal=passed")
