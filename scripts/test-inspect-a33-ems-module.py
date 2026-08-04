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

assert module.PACKAGE_KREL != module.EXPECTED_MODULE_VERMAGIC
assert module.normalize_module("kernel/sched/ems.ko") == "ems"
assert module.normalize_module("foo-bar.ko.xz") == "foo_bar"
assert module.parse_modinfo_depends("") == ()
assert module.parse_modinfo_depends("cmupmucal, ect_parser") == (
    "cmupmucal",
    "ect_parser",
)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    modules_dep = root / "modules.dep"
    # modules.dep is deliberately a load closure: usb lists both its direct
    # dependency and that dependency's EMS closure. It must not be used as the
    # direct graph.
    modules_dep.write_text(
        "kernel/ems.ko: kernel/cmupmucal.ko kernel/ect_parser.ko\n"
        "kernel/cmupmucal.ko:\n"
        "kernel/ect_parser.ko:\n"
        "kernel/exynos-dm.ko: kernel/ems.ko kernel/cmupmucal.ko kernel/ect_parser.ko\n"
        "kernel/usb.ko: kernel/exynos-dm.ko kernel/ems.ko kernel/cmupmucal.ko kernel/ect_parser.ko\n"
        "kernel/independent.ko:\n",
        encoding="utf-8",
    )
    closure = module.parse_modules_dep(modules_dep)
    aliases = module.build_aliases(closure)
    direct = module.build_direct_dependency_graph_from_fields(
        {
            "kernel/ems.ko": "cmupmucal,ect_parser",
            "kernel/cmupmucal.ko": "",
            "kernel/ect_parser.ko": "",
            "kernel/exynos-dm.ko": "ems",
            "kernel/usb.ko": "exynos_dm",
            "kernel/independent.ko": "",
        },
        aliases,
    )
    target = module.resolve_module("ems", aliases)
    usb = module.resolve_module("usb", aliases)
    independent = module.resolve_module("independent", aliases)

    assert target == "kernel/ems.ko"
    assert closure[usb] == (
        "kernel/exynos-dm.ko",
        "kernel/ems.ko",
        "kernel/cmupmucal.ko",
        "kernel/ect_parser.ko",
    )
    assert direct[target] == (
        "kernel/cmupmucal.ko",
        "kernel/ect_parser.ko",
    )
    assert direct[usb] == ("kernel/exynos-dm.ko",)
    assert module.dependency_path(direct, usb, target) == (
        "kernel/usb.ko",
        "kernel/exynos-dm.ko",
        "kernel/ems.ko",
    )
    assert module.dependency_path(direct, independent, target) is None
    assert module.reverse_direct_dependencies(direct, target) == [
        "kernel/exynos-dm.ko",
    ]
    assert module.reverse_dependencies(direct, target) == [
        "kernel/exynos-dm.ko",
        "kernel/usb.ko",
    ]
    assert module.format_path((usb, "kernel/exynos-dm.ko", target)) == (
        "usb -> exynos_dm -> ems"
    )

    try:
        module.build_direct_dependency_graph_from_fields(
            {"kernel/usb.ko": "missing-module"}, aliases
        )
    except module.InspectionError:
        pass
    else:
        raise AssertionError("unresolved direct modinfo dependency was accepted")

    classification, action = module.classify_omission(
        target_selected=True,
        target_seeded=True,
        selected_dependents=[],
        initramfs_target_entries=["lib/modules/ems.ko"],
    )
    assert classification == "yes-remove-explicit-seed"
    assert "regenerate-initramfs" in action

    classification, action = module.classify_omission(
        target_selected=True,
        target_seeded=False,
        selected_dependents=["kernel/exynos-dm.ko"],
        initramfs_target_entries=["lib/modules/ems.ko"],
    )
    assert classification == "no-independent-omission"
    assert "dependent-closure" in action

    classification, action = module.classify_omission(
        target_selected=False,
        target_seeded=False,
        selected_dependents=[],
        initramfs_target_entries=[],
    )
    assert classification == "yes-not-required-by-safe-initramfs-closure"
    assert "rootfs-autoload" in action

    rootfs = root / "rootfs"
    (rootfs / "etc/modules-load.d").mkdir(parents=True)
    (rootfs / "etc/modules-load.d/vendor.conf").write_text(
        "# comment\nems\nother\n", encoding="utf-8"
    )
    status, references = module.rootfs_ems_references(rootfs)
    assert status == "inspected"
    assert references == ["etc/modules-load.d/vendor.conf:2:ems"]
    assert module.rootfs_ems_references(None) == ("unresolved", [])

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
print("package_label_module_abi_distinction=passed")
print("modules_dep_load_closure_distinction=passed")
print("modinfo_direct_dependency_graph=passed")
print("exact_transitive_dependency_path=passed")
print("direct_reverse_dependency_set=passed")
print("transitive_reverse_dependency_set=passed")
print("unresolved_direct_dependency_refusal=passed")
print("independent_omission_classification=passed")
print("dependent_closure_classification=passed")
print("rootfs_autoload_reference_scan=passed")
print("ambiguous_alias_refusal=passed")
