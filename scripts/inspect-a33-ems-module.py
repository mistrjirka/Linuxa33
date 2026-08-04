#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Iterable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
from a33_cpio import Archive, CpioError

PACKAGE_KREL = "5.10.66-Gabriel260BR-TWRP-ga0103aac9499"
EXPECTED_MODULE_VERMAGIC = (
    "5.10.66-android12-9-24537318-abA336BXXU2AVG2 "
    "SMP preempt mod_unload modversions aarch64"
)
TARGET_NAME = "ems"


class InspectionError(RuntimeError):
    pass


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_module(value: str) -> str:
    name = Path(value.strip()).name
    for suffix in (".zst", ".xz", ".gz"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if name.endswith(".ko"):
        name = name[:-3]
    return name.replace("-", "_")


def read_items(path: Path) -> list[str]:
    if not path.is_file():
        return []
    result: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        value = raw.split("#", 1)[0].strip()
        if value:
            result.append(value)
    return result


def parse_modules_dep(path: Path) -> dict[str, tuple[str, ...]]:
    if not path.is_file():
        raise InspectionError(f"missing modules.dep: {path}")
    result: dict[str, tuple[str, ...]] = {}
    for raw in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if not raw.strip():
            continue
        left, separator, right = raw.partition(":")
        if not separator:
            raise InspectionError(f"malformed modules.dep line: {raw!r}")
        module = left.strip()
        if module in result:
            raise InspectionError(f"duplicate modules.dep entry: {module}")
        result[module] = tuple(right.split())
    return result


def build_aliases(dependencies: dict[str, tuple[str, ...]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for module in dependencies:
        for key in {
            module,
            Path(module).name,
            normalize_module(module),
            normalize_module(Path(module).name),
        }:
            previous = aliases.get(key)
            if previous is not None and previous != module:
                raise InspectionError(
                    f"ambiguous module alias {key!r}: {previous!r} and {module!r}"
                )
            aliases[key] = module
    return aliases


def resolve_module(value: str, aliases: dict[str, str]) -> str:
    for key in (value, Path(value).name, normalize_module(value)):
        if key in aliases:
            return aliases[key]
    raise InspectionError(f"module is absent from modules.dep: {value}")


def dependency_path(
    dependencies: dict[str, tuple[str, ...]], start: str, target: str
) -> tuple[str, ...] | None:
    queue: list[tuple[str, tuple[str, ...]]] = [(start, (start,))]
    visited: set[str] = set()
    while queue:
        current, path = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if current == target:
            return path
        for dependency in dependencies.get(current, ()):
            queue.append((dependency, (*path, dependency)))
    return None


def reverse_dependencies(
    dependencies: dict[str, tuple[str, ...]], target: str
) -> list[str]:
    result: list[str] = []
    for module in sorted(dependencies):
        if module == target:
            continue
        if dependency_path(dependencies, module, target) is not None:
            result.append(module)
    return result


def find_unique_module(root: Path, name: str) -> Path:
    matches = sorted(
        path
        for path in root.rglob("*.ko")
        if normalize_module(path.name) == normalize_module(name)
    )
    if len(matches) != 1:
        raise InspectionError(
            f"expected one {name}.ko under {root}, found {len(matches)}: {matches}"
        )
    return matches[0]


def modinfo(path: Path, field: str) -> str:
    executable = shutil.which("modinfo")
    if executable is None:
        raise InspectionError("modinfo is unavailable; install kmod")
    completed = subprocess.run(
        [executable, "-F", field, str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise InspectionError(
            f"modinfo -F {field} failed for {path}: {completed.stderr.strip()}"
        )
    value = completed.stdout.strip()
    if not value:
        raise InspectionError(f"modinfo -F {field} returned empty output for {path}")
    return value


def resolve_aports(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit.expanduser().resolve()
    executable = shutil.which("pmbootstrap")
    if executable is None:
        return None
    completed = subprocess.run(
        [executable, "config", "aports"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return Path(value).expanduser().resolve() if value else None


def inspect_initramfs(path: Path, target_sha: str) -> tuple[int, list[str], list[str]]:
    if not path.is_file():
        return 0, [], []
    try:
        archive = Archive.parse(gzip.decompress(path.read_bytes()))
    except (OSError, CpioError) as exc:
        raise InspectionError(f"cannot parse initramfs {path}: {exc}") from exc

    target_entries: list[str] = []
    matching_hash_entries: list[str] = []
    for entry in archive.entries:
        if normalize_module(entry.normalized) != TARGET_NAME:
            continue
        target_entries.append(entry.normalized)
        try:
            data = archive.resolved_data(entry.normalized)
        except (AttributeError, TypeError):
            data = entry.data
        if data and hashlib.sha256(data).hexdigest() == target_sha:
            matching_hash_entries.append(entry.normalized)
    return len(archive.entries), target_entries, matching_hash_entries


def format_path(path: Iterable[str]) -> str:
    return " -> ".join(normalize_module(item) for item in path)


def classify_omission(
    *,
    target_selected: bool,
    target_seeded: bool,
    selected_dependents: Iterable[str],
    initramfs_target_entries: Iterable[str],
) -> tuple[str, str]:
    dependents = tuple(selected_dependents)
    embedded = tuple(initramfs_target_entries)
    if dependents:
        return (
            "no-independent-omission",
            "remove-smallest-optional-dependent-closure-or-preserve-ems",
        )
    if target_seeded:
        return (
            "yes-remove-explicit-seed",
            "remove-only-ems-selection-and-regenerate-initramfs",
        )
    if target_selected:
        return (
            "yes-remove-generated-direct-selection",
            "remove-only-ems-selection-and-regenerate-initramfs",
        )
    if embedded:
        return (
            "unexplained-initramfs-embedding",
            "trace-generator-before-removal",
        )
    return (
        "yes-not-required-by-safe-initramfs-closure",
        "trace-rootfs-autoload-before-blacklist-or-rootfs-removal",
    )


def rootfs_ems_references(rootfs: Path | None) -> tuple[str, list[str]]:
    if rootfs is None:
        return "unresolved", []
    rootfs = rootfs.expanduser().resolve()
    if not rootfs.is_dir():
        raise InspectionError(f"rootfs tree is not a directory: {rootfs}")
    relative_files = (
        "etc/modules",
        "etc/modules-load.d",
        "usr/lib/modules-load.d",
        "etc/modprobe.d",
        "usr/lib/modprobe.d",
        "lib/udev/rules.d",
        "usr/lib/udev/rules.d",
    )
    candidates: list[Path] = []
    for relative in relative_files:
        path = rootfs / relative
        if path.is_file():
            candidates.append(path)
        elif path.is_dir():
            candidates.extend(sorted(item for item in path.rglob("*") if item.is_file()))
    references: list[str] = []
    for path in candidates:
        for number, raw in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            tokens = [normalize_module(token) for token in line.replace("=", " ").split()]
            if TARGET_NAME in tokens or "ems.ko" in line:
                references.append(f"{path.relative_to(rootfs)}:{number}:{line}")
    return "inspected", references


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect exact EMS module selection, dependencies and U0k embedding"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--aports", type=Path)
    parser.add_argument(
        "--rootfs-tree",
        type=Path,
        help="optional extracted/mounted rootfs directory to inspect read-only for EMS autoload references",
    )
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    source_root = root / "unpacked/twrp-root/lib/modules"
    stage_root = root / f"build/modules-stage-safe/usr/lib/modules/{PACKAGE_KREL}"
    modules_dep = stage_root / "modules.dep"
    seeds_file = repo / "config/modules-initfs-safe-debug.seeds"
    original_load = source_root / "modules.load.recovery"
    initramfs = root / "export-u0k-direct-mount-isolation/initramfs"
    report = root / "build/a33-ems-module-inspection.txt"

    for required in (source_root, stage_root, modules_dep, seeds_file, initramfs):
        if not required.exists():
            raise InspectionError(f"missing required input: {required}")

    source_ems = find_unique_module(source_root, TARGET_NAME)
    staged_ems = find_unique_module(stage_root, TARGET_NAME)
    source_sha = sha_file(source_ems)
    staged_sha = sha_file(staged_ems)
    if source_sha != staged_sha:
        raise InspectionError(
            f"source/staged EMS SHA mismatch: source={source_sha} staged={staged_sha}"
        )

    name = modinfo(staged_ems, "name")
    vermagic = modinfo(staged_ems, "vermagic")
    depends_field = modinfo(staged_ems, "depends")
    if normalize_module(name) != TARGET_NAME:
        raise InspectionError(f"unexpected EMS module name: {name!r}")
    if vermagic != EXPECTED_MODULE_VERMAGIC:
        raise InspectionError(
            f"EMS vermagic mismatch: actual={vermagic!r} expected={EXPECTED_MODULE_VERMAGIC!r}"
        )

    dependencies = parse_modules_dep(modules_dep)
    aliases = build_aliases(dependencies)
    target = resolve_module(name, aliases)
    declared_dependencies = dependencies.get(target, ())
    reverse_all = reverse_dependencies(dependencies, target)

    seeds = read_items(seeds_file)
    seed_paths = [(seed, resolve_module(seed, aliases)) for seed in seeds]
    seed_chains: list[tuple[str, tuple[str, ...]]] = []
    for seed, seed_path in seed_paths:
        chain = dependency_path(dependencies, seed_path, target)
        if chain is not None:
            seed_chains.append((seed, chain))

    aports = resolve_aports(args.aports)
    modules_initfs = (
        aports / "device/downstream/device-samsung-a33x/modules-initfs"
        if aports is not None
        else None
    )
    selected_names = read_items(modules_initfs) if modules_initfs is not None else []
    selected_paths: list[str] = []
    unresolved_selected: list[str] = []
    for selected in selected_names:
        try:
            selected_paths.append(resolve_module(selected, aliases))
        except InspectionError:
            unresolved_selected.append(selected)

    selected_dependents = sorted(
        module
        for module in selected_paths
        if module != target and dependency_path(dependencies, module, target) is not None
    )
    target_selected = target in selected_paths
    target_seeded = any(path == target for _, path in seed_paths)
    original_direct_load = any(normalize_module(item) == TARGET_NAME for item in read_items(original_load))

    entry_count, initramfs_entries, matching_initramfs_entries = inspect_initramfs(
        initramfs, source_sha
    )
    omission_class, omission_action = classify_omission(
        target_selected=target_selected,
        target_seeded=target_seeded,
        selected_dependents=selected_dependents,
        initramfs_target_entries=initramfs_entries,
    )
    rootfs_status, rootfs_references = rootfs_ems_references(args.rootfs_tree)

    lines = [
        "operation=inspect-exact-a33-ems-module",
        f"package_krel={PACKAGE_KREL}",
        f"expected_module_vermagic={EXPECTED_MODULE_VERMAGIC}",
        f"source_module={source_ems}",
        f"staged_module={staged_ems}",
        f"ems_sha256={source_sha}",
        f"ems_modinfo_name={name}",
        f"ems_vermagic={vermagic}",
        f"ems_modinfo_depends={depends_field}",
        f"ems_modules_dep_path={target}",
        f"ems_declared_dependency_count={len(declared_dependencies)}",
        f"ems_declared_dependencies={','.join(declared_dependencies)}",
        f"ems_reverse_dependency_count_all={len(reverse_all)}",
        f"ems_reverse_dependencies_all={','.join(reverse_all)}",
        f"safe_seed_count={len(seeds)}",
        f"ems_is_explicit_seed={'yes' if target_seeded else 'no'}",
        f"seed_chain_count={len(seed_chains)}",
        *[f"seed_chain={seed}:{format_path(chain)}" for seed, chain in seed_chains],
        f"aports={aports if aports is not None else 'unresolved'}",
        f"modules_initfs={modules_initfs if modules_initfs is not None else 'unresolved'}",
        f"modules_initfs_entry_count={len(selected_names)}",
        f"modules_initfs_unresolved_count={len(unresolved_selected)}",
        f"modules_initfs_unresolved={','.join(unresolved_selected)}",
        f"ems_selected_by_modules_initfs={'yes' if target_selected else 'no'}",
        f"selected_ems_dependent_count={len(selected_dependents)}",
        f"selected_ems_dependents={','.join(selected_dependents)}",
        f"ems_in_original_twrp_load_list={'yes' if original_direct_load else 'no'}",
        f"u0k_initramfs={initramfs}",
        f"u0k_initramfs_entry_count={entry_count}",
        f"u0k_ems_entry_count={len(initramfs_entries)}",
        f"u0k_ems_entries={','.join(initramfs_entries)}",
        f"u0k_exact_ems_hash_entry_count={len(matching_initramfs_entries)}",
        f"u0k_exact_ems_hash_entries={','.join(matching_initramfs_entries)}",
        f"rootfs_autoload_config_status={rootfs_status}",
        f"rootfs_ems_reference_count={len(rootfs_references)}",
        *[f"rootfs_ems_reference={reference}" for reference in rootfs_references],
        f"ems_independent_omission={omission_class}",
        f"ems_omission_next_action={omission_action}",
        "ems_module_only_replacement=conditional-yes-preserve-name-vermagic-dependencies-exported-symbol-crcs",
        "whole_kernel_rebuild_required_by_current_evidence=no",
        f"ems_removal_classification={omission_class}",
        "phone_partition_writes=no",
        "inspection_status=passed",
    ]
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"report={report}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InspectionError, OSError, UnicodeError, ValueError) as exc:
        print(f"EMS INSPECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
