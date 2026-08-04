#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "audit-a33-rootfs-handoff.py"
spec = importlib.util.spec_from_file_location("a33_rootfs_handoff_audit", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    build = root / "build"
    build.mkdir()

    older = build / "u0j-nondestructive-audit-20260804-120000"
    newer = build / "u0j-nondestructive-audit-20260804-130000"
    older.mkdir()
    newer.mkdir()
    older.touch()
    newer.touch()
    assert module.choose_audit_dir(root, None, True) == newer

    original = newer / "original.bin"
    readback = newer / "readback.bin"
    chunk = module.MIB
    original.write_bytes(b"A" * chunk + b"B" * chunk)
    changed = bytearray(b"A" * chunk + b"B" * chunk)
    changed[7] = ord("Z")
    changed[chunk + 123] = ord("Y")
    readback.write_bytes(changed)

    result = module.compare_files(original, readback)
    assert result["changed_1MiB_chunks"] == "2"
    assert result["different_bytes"] == "2"
    assert result["first_difference_offset"] == "7"
    assert result["last_difference_offset"] == str(chunk + 123)

    legacy = newer / "userdata-first-765MiB.img"
    legacy.write_bytes(b"x" * 32)
    selected, reused = module.choose_readback(newer, 32)
    assert selected == legacy
    assert reused

    report = newer / "summary.txt"
    report.write_text("audit_status=passed\n", encoding="utf-8")
    archive, digest = module.create_archive(newer, legacy)
    assert archive.is_file()
    assert len(digest) == 64
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert any(name.endswith("summary.txt") for name in names)
    assert not any(name.endswith(legacy.name) for name in names)

try:
    module.choose_audit_dir(Path("/tmp"), Path("/tmp/a"), True)
except module.Refusal:
    pass
else:
    raise AssertionError("conflicting audit-directory modes were accepted")

print("rootfs_handoff_audit_self_test=passed")
print("resume_latest_selection=passed")
print("chunked_comparison=passed")
print("legacy_readback_reuse=passed")
print("readback_archive_exclusion=passed")
print("conflicting_mode_refusal=passed")
