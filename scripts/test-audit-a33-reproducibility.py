#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import os
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


def write_module(path: Path, vermagic: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(vermagic + "\n", encoding="utf-8")


def install_fake_modinfo(root: Path) -> Path:
    executable = root / "modinfo"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "field = sys.argv[2]\n"
        "path = pathlib.Path(sys.argv[3])\n"
        "if path.name.startswith('failure'):\n"
        "    print('fixture failure', file=sys.stderr); raise SystemExit(7)\n"
        "if field == 'vermagic':\n"
        "    print(path.read_text().strip())\n"
        "else:\n"
        "    print('fixture')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    fake_bin = root / "bin"
    fake_bin.mkdir()
    install_fake_modinfo(fake_bin)
    original_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(fake_bin) + os.pathsep + original_path
    try:
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

        original_count = module.EXPECTED_ORIGINAL_MODULES
        module.EXPECTED_ORIGINAL_MODULES = 2
        try:
            flat_root = root / "flat"
            flat_modules = flat_root / "unpacked/twrp-root/lib/modules"
            write_module(flat_modules / "one.ko", module.EXPECTED_MODULE_VERMAGIC)
            write_module(flat_modules / "sub/two.ko", module.EXPECTED_MODULE_VERMAGIC)
            (flat_modules / "modules.load.recovery").write_text("one.ko\nsub/two.ko\n")
            flat_checks = []
            assert module.audit_modules(
                flat_checks,
                flat_root,
                {"module_source": str(flat_modules), "module_files": "2"},
            )
            assert "layout=flat-package-root" in flat_checks[-1].detail
            assert f"package_krel={module.PACKAGE_KREL}" in flat_checks[-1].detail
            assert module.EXPECTED_MODULE_VERMAGIC in flat_checks[-1].detail

            nested_root = root / "nested"
            nested_modules = (
                nested_root / "unpacked/twrp-root/lib/modules" / module.PACKAGE_KREL
            )
            write_module(nested_modules / "one.ko", module.EXPECTED_MODULE_VERMAGIC)
            write_module(nested_modules / "two.ko", module.EXPECTED_MODULE_VERMAGIC)
            (nested_modules / "modules.load.recovery").write_text("one.ko\ntwo.ko\n")
            nested_checks = []
            assert module.audit_modules(nested_checks, nested_root)
            assert "layout=nested-package-directory" in nested_checks[-1].detail

            bad_root = root / "bad-vermagic"
            bad_modules = bad_root / "unpacked/twrp-root/lib/modules"
            write_module(bad_modules / "one.ko", module.EXPECTED_MODULE_VERMAGIC)
            write_module(bad_modules / "two.ko", "5.10.66-wrong-release SMP")
            (bad_modules / "modules.load.recovery").write_text("one.ko\ntwo.ko\n")
            bad_checks = []
            assert not module.audit_modules(bad_checks, bad_root)
            assert "multiple module vermagic values" in bad_checks[-1].detail

            failure_root = root / "modinfo-failure"
            failure_modules = failure_root / "unpacked/twrp-root/lib/modules"
            write_module(failure_modules / "one.ko", module.EXPECTED_MODULE_VERMAGIC)
            write_module(failure_modules / "failure.ko", module.EXPECTED_MODULE_VERMAGIC)
            (failure_modules / "modules.load.recovery").write_text("one.ko\nfailure.ko\n")
            failure_checks = []
            assert not module.audit_modules(failure_checks, failure_root)
            assert "modinfo -F vermagic failed" in failure_checks[-1].detail

            empty_root = root / "modinfo-empty"
            empty_modules = empty_root / "unpacked/twrp-root/lib/modules"
            write_module(empty_modules / "one.ko", module.EXPECTED_MODULE_VERMAGIC)
            write_module(empty_modules / "two.ko", "")
            (empty_modules / "modules.load.recovery").write_text("one.ko\ntwo.ko\n")
            empty_checks = []
            assert not module.audit_modules(empty_checks, empty_root)
            assert "returned empty output" in empty_checks[-1].detail
        finally:
            module.EXPECTED_ORIGINAL_MODULES = original_count
    finally:
        os.environ["PATH"] = original_path

print("a33_reproducibility_audit_self_test=passed")
print("package_label_module_abi_distinction=passed")
print("flat_module_layout_modinfo=passed")
print("nested_module_layout_modinfo=passed")
print("mixed_module_vermagic_refusal=passed")
print("modinfo_failure_refusal=passed")
print("empty_modinfo_refusal=passed")
