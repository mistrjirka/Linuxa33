#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "inspect-a33-ems-module.py"
spec = importlib.util.spec_from_file_location("a33_ems_inspection_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.normalize_module("kernel/sched/ems.ko") == "ems"
assert module.normalize_module("foo-bar.ko.xz") == "foo_bar"

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    modules_dep = root / "modules.dep"
    modules_dep.write_text(
        "kernel/ems.ko:\n"
        "kernel/exynos-dm.ko: kernel/ems.ko\n"
        "kernel/usb.ko: kernel/exynos-dm.ko\n"
        "kernel/independent.ko:\n",
        encoding="utf-8",
    )
    dependencies = module.parse_modules_dep(modules_dep)
    aliases = module.build_aliases(dependencies)
    target = module.resolve_module("ems", aliases)
    usb = module.resolve_module("usb", aliases)
    independent = module.resolve_module("independent", aliases)

    assert target == "kernel/ems.ko"
    assert module.dependency_path(dependencies, usb, target) == (
        "kernel/usb.ko",
        "kernel/exynos-dm.ko",
        "kernel/ems.ko",
    )
    assert module.dependency_path(dependencies, independent, target) is None
    assert module.reverse_dependencies(dependencies, target) == [
        "kernel/exynos-dm.ko",
        "kernel/usb.ko",
    ]
    assert module.format_path((usb, "kernel/exynos-dm.ko", target)) == (
        "usb -> exynos_dm -> ems"
    )

    duplicate = root / "duplicate.dep"
    duplicate.write_text("a/ems.ko:\nb/ems.ko:\n", encoding="utf-8")
    duplicate_dependencies = module.parse_modules_dep(duplicate)
    try:
        module.build_aliases(duplicate_dependencies)
    except module.InspectionError:
        pass
    else:
        raise AssertionError("ambiguous EMS aliases were accepted")

print("a33_ems_inspection_self_test=passed")
print("module_name_normalization=passed")
print("modules_dep_parser=passed")
print("transitive_dependency_path=passed")
print("reverse_dependency_set=passed")
print("ambiguous_alias_refusal=passed")
