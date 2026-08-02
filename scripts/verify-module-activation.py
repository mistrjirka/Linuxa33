#!/usr/bin/env python3
"""Fail closed when an initramfs module is packaged but cannot be activated.

Contracts are TSV rows:

    module  method  source_hook  initramfs_hook

Only contracts whose module is selected or embedded are enforced. The
``explicit-insmod`` method is intended for vendor modules that cannot be
cold-plugged reliably and whose module soft dependencies must be bypassed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


MODULE_SUFFIX_RE = re.compile(r"\.ko(?:\.(?:gz|xz|zst))?$")
CONTRACT_MARKER = "activation-contract: {module} explicit-insmod"


class VerificationError(RuntimeError):
    pass


def normalize_module(value: str) -> str:
    name = Path(value.strip()).name
    for suffix in (".zst", ".xz", ".gz"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if name.endswith(".ko"):
        name = name[:-3]
    return name.replace("-", "_")


def read_selected_modules(path: Path) -> set[str]:
    selected: set[str] = set()
    for raw_line in path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            selected.add(normalize_module(line))
    return selected


def parse_contracts(path: Path) -> list[tuple[str, str, Path, str]]:
    contracts: list[tuple[str, str, Path, str]] = []
    for line_number, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            raise VerificationError(
                f"{path}:{line_number}: expected four tab-separated fields"
            )
        module, method, source_hook, initramfs_hook = fields
        module = normalize_module(module)
        if method != "explicit-insmod":
            raise VerificationError(
                f"{path}:{line_number}: unsupported activation method {method!r}"
            )
        contracts.append(
            (module, method, Path(source_hook), initramfs_hook.lstrip("/"))
        )
    return contracts


def list_initramfs_entries(initramfs: Path) -> list[str]:
    result = subprocess.run(
        ["sh", "-c", 'gzip -dc "$1" | cpio -it 2>/dev/null', "sh", str(initramfs)],
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        line.strip().lstrip("./")
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def extract_initramfs_file(initramfs: Path, entry: str) -> str:
    result = subprocess.run(
        [
            "sh",
            "-c",
            'gzip -dc "$1" | cpio -i --to-stdout "$2" 2>/dev/null',
            "sh",
            str(initramfs),
            entry,
        ],
        check=True,
        capture_output=True,
    )
    return result.stdout.decode("utf-8", errors="replace")


def find_module_file(module_root: Path, module: str) -> Path:
    candidates = [
        path
        for path in module_root.rglob("*.ko*")
        if MODULE_SUFFIX_RE.search(path.name) and normalize_module(path.name) == module
    ]
    if len(candidates) != 1:
        raise VerificationError(
            f"expected exactly one module file for {module}, found {len(candidates)}"
        )
    return candidates[0]


def modinfo_field(module_file: Path, field: str) -> list[str]:
    result = subprocess.run(
        ["modinfo", "-F", field, str(module_file)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def softdep_modules(values: list[str]) -> set[str]:
    modules: set[str] = set()
    for value in values:
        tokens = value.replace("pre:", " ").replace("post:", " ").split()
        modules.update(normalize_module(token) for token in tokens if token)
    return modules


def validate_explicit_loader(text: str, module: str, source: str) -> None:
    marker = CONTRACT_MARKER.format(module=module)
    if marker not in text:
        raise VerificationError(f"{source}: missing marker: {marker}")
    if not re.search(r"(?m)^[^#\n]*\binsmod\b", text):
        raise VerificationError(
            f"{source}: explicit-insmod contract has no insmod command"
        )
    module_spellings = {module, module.replace("_", "-")}
    if not any(spelling in text for spelling in module_spellings):
        raise VerificationError(f"{source}: loader does not reference {module}")
    if re.search(
        rf"(?m)^\s*modprobe\b[^\n]*(?:{re.escape(module)}|"
        rf"{re.escape(module.replace('_', '-'))})",
        text,
    ):
        raise VerificationError(
            f"{source}: loader uses modprobe for {module}; "
            "this would honor vendor softdeps"
        )


def verify(args: argparse.Namespace) -> None:
    contracts = parse_contracts(args.contracts)
    selected = (
        read_selected_modules(args.selected_modules)
        if args.selected_modules is not None
        else set()
    )
    entries = (
        list_initramfs_entries(args.initramfs)
        if args.initramfs is not None
        else []
    )
    embedded_modules = {
        normalize_module(entry)
        for entry in entries
        if MODULE_SUFFIX_RE.search(Path(entry).name)
    }

    active_count = 0
    for module, method, source_hook_rel, initramfs_hook in contracts:
        active = module in selected or module in embedded_modules
        if not active:
            continue
        active_count += 1

        source_hook = args.repo_root / source_hook_rel
        if not source_hook.is_file():
            raise VerificationError(
                f"{module}: selected/embedded but explicit loader source is missing: "
                f"{source_hook_rel}"
            )
        source_text = source_hook.read_text()
        validate_explicit_loader(source_text, module, str(source_hook_rel))

        if args.module_root is not None:
            module_file = find_module_file(args.module_root, module)
            softdeps = softdep_modules(modinfo_field(module_file, "softdep"))
            missing_softdeps = sorted(softdeps - selected)
            if softdeps:
                print(
                    f"{module}: vendor softdeps={','.join(sorted(softdeps))}; "
                    "explicit insmod required"
                )
            if missing_softdeps:
                print(
                    f"{module}: intentionally bypassing unselected softdeps="
                    f"{','.join(missing_softdeps)}"
                )

        if args.initramfs is not None:
            if module not in embedded_modules:
                raise VerificationError(
                    f"{module}: activation contract active but module is absent "
                    "from initramfs"
                )
            normalized_entries = {entry.lstrip("./") for entry in entries}
            if initramfs_hook not in normalized_entries:
                raise VerificationError(
                    f"{module}: module is embedded but loader hook is absent: "
                    f"{initramfs_hook}"
                )
            embedded_text = extract_initramfs_file(args.initramfs, initramfs_hook)
            validate_explicit_loader(
                embedded_text, module, f"{args.initramfs}:{initramfs_hook}"
            )

        print(
            f"Activation contract passed: {module} via {method} "
            f"({initramfs_hook})"
        )

    print(f"Module activation contracts checked: {active_count}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--selected-modules", type=Path)
    parser.add_argument("--module-root", type=Path)
    parser.add_argument("--initramfs", type=Path)
    args = parser.parse_args()

    if args.selected_modules is None and args.initramfs is None:
        parser.error("provide --selected-modules and/or --initramfs")
    for path in (args.contracts, args.repo_root):
        if not path.exists():
            parser.error(f"path does not exist: {path}")
    if args.selected_modules is not None and not args.selected_modules.is_file():
        parser.error(f"selected module list does not exist: {args.selected_modules}")
    if args.module_root is not None and not args.module_root.is_dir():
        parser.error(f"module root does not exist: {args.module_root}")
    if args.initramfs is not None and not args.initramfs.is_file():
        parser.error(f"initramfs does not exist: {args.initramfs}")
    return args


def main() -> int:
    try:
        verify(parse_args())
    except (VerificationError, subprocess.CalledProcessError) as error:
        print(f"REFUSING: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
