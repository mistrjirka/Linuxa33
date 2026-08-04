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

print("a33_reproducibility_audit_self_test=passed")
print("kv_first_value_contract=passed")
print("deterministic_tree_hash=passed")
print("exact_file_validation=passed")
print("source_lock_missing_refusal=passed")
print("source_lock_complete_acceptance=passed")
print("source_lock_invalid_hash_refusal=passed")
