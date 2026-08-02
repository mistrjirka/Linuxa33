#!/usr/bin/env python3
"""Generate a guarded, dependency-closed postmarketOS modules-initfs list.

The default mode is intentionally conservative. The previous port attempt copied
all 315 entries from TWRP's modules.load.recovery into the initramfs. udev then
autoloaded phy_exynos_mipi and the kernel panicked in exynos_mipi_phy_probe.

This tool therefore:
* starts from an explicit safe-debug seed list;
* follows modules.dep dependencies;
* rejects blocked display/camera/MIPI modules;
* refuses unexpectedly large closures;
* requires a loud opt-in to reproduce the unsafe full-TWRP behavior.
"""

from __future__ import annotations

import argparse
import fnmatch
from pathlib import Path
import shutil
import subprocess
from typing import Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_SEEDS = REPO_ROOT / "config/modules-initfs-safe-debug.seeds"
DEFAULT_BLOCKLIST = REPO_ROOT / "config/modules-initfs-blocklist.glob"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--module-root",
        type=Path,
        required=True,
        help="Directory containing the extracted TWRP .ko files and modules.dep",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination postmarketOS modules-initfs file",
    )
    parser.add_argument(
        "--modules-dep",
        type=Path,
        help="Dependency index; defaults to <module-root>/modules.dep",
    )
    parser.add_argument(
        "--seeds",
        type=Path,
        default=DEFAULT_SEEDS,
        help=f"Safe-debug seed list (default: {DEFAULT_SEEDS})",
    )
    parser.add_argument(
        "--blocklist",
        type=Path,
        default=DEFAULT_BLOCKLIST,
        help=f"Blocked module globs (default: {DEFAULT_BLOCKLIST})",
    )
    parser.add_argument(
        "--max-modules",
        type=int,
        default=128,
        help="Refuse a larger dependency closure (default: 128)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Write a detailed selection report",
    )
    parser.add_argument(
        "--load-list",
        type=Path,
        help="Legacy TWRP modules.load.recovery input; unsafe unless explicitly allowed",
    )
    parser.add_argument(
        "--unsafe-use-full-twrp-list",
        action="store_true",
        help="DANGEROUS: reproduce the old all-TWRP-module behavior",
    )
    return parser.parse_args()


def read_items(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"Required list does not exist: {path}")
    values: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            values.append(line)
    return values


def strip_module_suffix(filename: str) -> str:
    name = Path(filename).name
    for suffix in (".zst", ".xz", ".gz"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if name.endswith(".ko"):
        name = name[:-3]
    return name


def normalize_name(name: str) -> str:
    return strip_module_suffix(name).replace("-", "_")


def is_blocked(module_path: str, module_name: str, patterns: Iterable[str]) -> str | None:
    candidates = {
        module_path,
        Path(module_path).name,
        strip_module_suffix(module_path),
        module_name,
        normalize_name(module_name),
    }
    for pattern in patterns:
        normalized_pattern = pattern.replace("-", "_")
        for candidate in candidates:
            if fnmatch.fnmatch(candidate, pattern) or fnmatch.fnmatch(
                candidate.replace("-", "_"), normalized_pattern
            ):
                return pattern
    return None


def parse_modules_dep(
    module_root: Path, modules_dep: Path
) -> tuple[dict[str, list[str]], dict[str, str]]:
    if not modules_dep.is_file():
        raise SystemExit(
            f"modules.dep not found: {modules_dep}\n"
            "Generate it first with depmod against the staged module directory."
        )

    dependencies: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}

    for raw_line in modules_dep.read_text().splitlines():
        if not raw_line.strip():
            continue
        left, separator, right = raw_line.partition(":")
        if not separator:
            raise SystemExit(f"Malformed modules.dep line: {raw_line!r}")

        module_path = left.strip()
        module_file = module_root / module_path
        if not module_file.is_file():
            raise SystemExit(
                f"modules.dep references a missing module: {module_file}"
            )

        dependencies[module_path] = right.split()
        keys = {
            module_path,
            Path(module_path).name,
            strip_module_suffix(module_path),
            normalize_name(module_path),
        }
        for key in keys:
            old = aliases.get(key)
            if old is not None and old != module_path:
                raise SystemExit(
                    f"Ambiguous module key {key!r}: {old!r} and {module_path!r}"
                )
            aliases[key] = module_path

    return dependencies, aliases


def resolve_seed(seed: str, aliases: dict[str, str]) -> str:
    for key in (seed, Path(seed).name, strip_module_suffix(seed), normalize_name(seed)):
        if key in aliases:
            return aliases[key]
    raise SystemExit(f"Seed module not found in modules.dep: {seed}")


def module_name(modinfo: str, module_file: Path) -> str:
    result = subprocess.run(
        [modinfo, "-F", "name", str(module_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    name = result.stdout.strip()
    if not name:
        raise SystemExit(f"modinfo returned an empty name for {module_file}")
    return name


def safe_dependency_closure(
    module_root: Path,
    dependencies: dict[str, list[str]],
    aliases: dict[str, str],
    seeds: list[str],
    patterns: list[str],
    modinfo: str,
) -> tuple[list[str], dict[str, str]]:
    selected: list[str] = []
    state: dict[str, int] = {}
    names: dict[str, str] = {}

    def visit(module_path: str, chain: tuple[str, ...]) -> None:
        status = state.get(module_path, 0)
        if status == 2:
            return
        if status == 1:
            raise SystemExit(
                "Dependency cycle detected: " + " -> ".join((*chain, module_path))
            )

        state[module_path] = 1
        name = names.setdefault(
            module_path, module_name(modinfo, module_root / module_path)
        )
        blocked_by = is_blocked(module_path, name, patterns)
        if blocked_by is not None:
            raise SystemExit(
                "Blocked module entered the safe dependency closure:\n"
                f"  module:  {module_path} ({name})\n"
                f"  pattern: {blocked_by}\n"
                f"  chain:   {' -> '.join((*chain, module_path))}"
            )

        for dependency in dependencies.get(module_path, []):
            dependency_path = resolve_seed(dependency, aliases)
            visit(dependency_path, (*chain, module_path))

        state[module_path] = 2
        selected.append(module_path)

    for seed in seeds:
        visit(resolve_seed(seed, aliases), (f"seed:{seed}",))

    return selected, names


def unsafe_full_list(
    module_root: Path,
    load_list: Path,
    modinfo: str,
) -> tuple[list[str], dict[str, str]]:
    selected: list[str] = []
    names: dict[str, str] = {}
    seen: set[str] = set()

    for filename in read_items(load_list):
        module_file = module_root / filename
        if not module_file.is_file():
            raise SystemExit(f"Missing module from TWRP load list: {module_file}")
        name = module_name(modinfo, module_file)
        if name not in seen:
            seen.add(name)
            selected.append(filename)
            names[filename] = name

    return selected, names


def main() -> None:
    args = parse_args()
    modinfo = shutil.which("modinfo")
    if modinfo is None:
        raise SystemExit("modinfo was not found; install kmod first")

    module_root = args.module_root.resolve()
    modules_dep = (args.modules_dep or module_root / "modules.dep").resolve()

    if args.load_list is not None and not args.unsafe_use_full_twrp_list:
        raise SystemExit(
            "Refusing the legacy full TWRP list. That configuration loaded 315 "
            "modules and caused a repeatable kernel panic in phy_exynos_mipi.\n"
            "Use the default safe-debug seed/dependency mode instead. The "
            "--unsafe-use-full-twrp-list switch exists only for controlled "
            "reproduction and must never be used for the next recovery test."
        )

    patterns = read_items(args.blocklist)

    if args.unsafe_use_full_twrp_list:
        if args.load_list is None:
            raise SystemExit(
                "--unsafe-use-full-twrp-list requires --load-list"
            )
        selected, names = unsafe_full_list(module_root, args.load_list, modinfo)
        mode = "UNSAFE full TWRP list"
        seeds: list[str] = []
    else:
        dependencies, aliases = parse_modules_dep(module_root, modules_dep)
        seeds = read_items(args.seeds)
        selected, names = safe_dependency_closure(
            module_root,
            dependencies,
            aliases,
            seeds,
            patterns,
            modinfo,
        )
        mode = "safe-debug dependency closure"

        if len(selected) > args.max_modules:
            raise SystemExit(
                f"Safe closure unexpectedly contains {len(selected)} modules, "
                f"above the limit of {args.max_modules}. Review the dependency "
                "report rather than increasing the limit blindly."
            )

    output_names: list[str] = []
    seen_names: set[str] = set()
    for module_path in selected:
        name = names[module_path]
        blocked_by = is_blocked(module_path, name, patterns)
        if blocked_by is not None and not args.unsafe_use_full_twrp_list:
            raise SystemExit(
                f"Internal safety failure: {module_path} matched {blocked_by}"
            )
        if name not in seen_names:
            seen_names.add(name)
            output_names.append(name)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(output_names) + "\n")

    report = args.report or args.output.with_suffix(args.output.suffix + ".report.txt")
    report.parent.mkdir(parents=True, exist_ok=True)
    report_lines = [
        f"mode: {mode}",
        f"module_root: {module_root}",
        f"modules_dep: {modules_dep}",
        f"module_count: {len(output_names)}",
        f"max_modules: {args.max_modules}",
        "",
        "seeds:",
        *[f"  {seed}" for seed in seeds],
        "",
        "selected dependency-first:",
        *[
            f"  {names[module_path]} <- {module_path}"
            for module_path in selected
        ],
        "",
        "blocked patterns:",
        *[f"  {pattern}" for pattern in patterns],
        "",
    ]
    report.write_text("\n".join(report_lines))

    print(f"Wrote {len(output_names)} modules to {args.output}")
    print(f"Selection report: {report}")
    if args.unsafe_use_full_twrp_list:
        print("WARNING: unsafe full-TWRP mode was explicitly enabled")


if __name__ == "__main__":
    main()
