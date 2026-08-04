#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-reproducibility.py"
spec = importlib.util.spec_from_file_location("a33_reproducibility_audit_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def write_module(path: Path, release: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ELF-fixture\0vermagic=" + release.encode("ascii") + b" SMP preempt\0")


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)

    values_file = root / "values.txt"
    values_file.write_text(
        "# comment\nalpha=first\nalpha=ignored\nbeta = second value\ninvalid\n",
        encoding="utf-8",
    )
    assert module.kv(values_file) == {"alpha": "first", "beta": "second value"}

    tree = root / "tree"
    (tree / "z").mkdir(parents=True)
    (tree / "a").mkdir()
    (tree / "z/two").write_bytes(b"two")
    (tree / "a/one").write_bytes(b"one")
    first_hash = module.tree_sha256(tree)
    second_hash = module.tree_sha256(tree, [tree / "z/two", tree / "a/one"])
    assert first_hash == second_hash
    (tree / "a/one").write_bytes(b"changed")
    assert module.tree_sha256(tree) != first_hash

    exact = root / "exact.bin"
    exact.write_bytes(b"fixture")
    checks = []
    assert module.verify_exact_file(
        checks,
        name="exact_fixture",
        path=exact,
        expected_size=7,
        expected_sha256=hashlib.sha256(b"fixture").hexdigest(),
    )
    assert checks[-1].status == "passed"

    failed_checks = []
    assert not module.verify_exact_file(
        failed_checks,
        name="wrong_fixture",
        path=exact,
        expected_size=8,
    )
    assert failed_checks[-1].status == "failed"

    source_lock = root / "source.lock"
    ok, detail = module.validate_source_lock(source_lock)
    assert not ok and "missing=" in detail

    incomplete = {
        "source_repository": "https://example.invalid/kernel.git",
        "source_commit": "1" * 40,
    }
    source_lock.write_text(
        "\n".join(f"{key}={value}" for key, value in incomplete.items()) + "\n",
        encoding="utf-8",
    )
    ok, detail = module.validate_source_lock(source_lock)
    assert not ok and "missing_fields=" in detail

    complete = {
        "source_repository": "https://example.invalid/kernel.git",
        "source_commit": "1" * 40,
        "source_tree_sha256": "2" * 64,
        "kernel_config_sha256": "3" * 64,
        "toolchain_identity": "clang test fixture",
        "toolchain_sha256": "4" * 64,
        "unpatched_kernel_sha256": "5" * 64,
        "patched_kernel_sha256": "6" * 64,
    }
    source_lock.write_text(
        "\n".join(f"{key}={value}" for key, value in complete.items()) + "\n",
        encoding="utf-8",
    )
    ok, detail = module.validate_source_lock(source_lock)
    assert ok
    assert "source_commit=" + "1" * 40 in detail

    complete["patched_kernel_sha256"] = "not-a-hash"
    source_lock.write_text(
        "\n".join(f"{key}={value}" for key, value in complete.items()) + "\n",
        encoding="utf-8",
    )
    ok, detail = module.validate_source_lock(source_lock)
    assert not ok and "invalid_hashes=patched_kernel_sha256" in detail

    # The actual third-host reconstruction stores the release contents directly
    # under lib/modules. Validate that flat layout by temporarily using a small
    # fixture module count, then validate the conventional nested layout too.
    original_count = module.EXPECTED_ORIGINAL_MODULES
    module.EXPECTED_ORIGINAL_MODULES = 2
    try:
        flat_root = root / "flat"
        flat_modules = flat_root / "unpacked/twrp-root/lib/modules"
        write_module(flat_modules / "one.ko", module.EXPECTED_KERNEL_RELEASE)
        write_module(flat_modules / "sub/two.ko", module.EXPECTED_KERNEL_RELEASE)
        (flat_modules / "modules.load.recovery").write_text("one.ko\nsub/two.ko\n")
        flat_checks = []
        assert module.audit_modules(
            flat_checks,
            flat_root,
            {
                "module_source": str(flat_modules),
                "module_files": "2",
            },
        )
        assert "layout=flat-release-root" in flat_checks[-1].detail
        assert "vermagic_all=passed" in flat_checks[-1].detail

        nested_root = root / "nested"
        nested_modules = (
            nested_root
            / "unpacked/twrp-root/lib/modules"
            / module.EXPECTED_KERNEL_RELEASE
        )
        write_module(nested_modules / "one.ko", module.EXPECTED_KERNEL_RELEASE)
        write_module(nested_modules / "two.ko", module.EXPECTED_KERNEL_RELEASE)
        (nested_modules / "modules.load.recovery").write_text("one.ko\ntwo.ko\n")
        nested_checks = []
        assert module.audit_modules(nested_checks, nested_root)
        assert "layout=nested-release-directory" in nested_checks[-1].detail

        bad_root = root / "bad-vermagic"
        bad_modules = bad_root / "unpacked/twrp-root/lib/modules"
        write_module(bad_modules / "one.ko", module.EXPECTED_KERNEL_RELEASE)
        write_module(bad_modules / "two.ko", "5.10.66-wrong-release")
        (bad_modules / "modules.load.recovery").write_text("one.ko\ntwo.ko\n")
        bad_checks = []
        assert not module.audit_modules(bad_checks, bad_root)
        assert "vermagic_mismatch_count=1" in bad_checks[-1].detail
    finally:
        module.EXPECTED_ORIGINAL_MODULES = original_count

print("a33_reproducibility_audit_self_test=passed")
print("kv_first_value_contract=passed")
print("deterministic_tree_hash=passed")
print("exact_file_validation=passed")
print("source_lock_missing_refusal=passed")
print("source_lock_complete_acceptance=passed")
print("source_lock_invalid_hash_refusal=passed")
print("flat_module_layout_vermagic=passed")
print("nested_module_layout_vermagic=passed")
print("wrong_module_vermagic_refusal=passed")
