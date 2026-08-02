#!/usr/bin/env python3
"""Generate a postmarketOS modules-initfs file from TWRP modules.load.recovery."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--module-root",
        type=Path,
        required=True,
        help="Directory containing the extracted TWRP .ko files",
    )
    parser.add_argument(
        "--load-list",
        type=Path,
        required=True,
        help="TWRP modules.load.recovery file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination modules-initfs file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    modinfo = shutil.which("modinfo")
    if modinfo is None:
        raise SystemExit("modinfo was not found; install kmod first")

    names: list[str] = []
    seen: set[str] = set()

    for raw_line in args.load_list.read_text().splitlines():
        filename = raw_line.strip()
        if not filename:
            continue

        module_file = args.module_root / filename
        if not module_file.is_file():
            raise SystemExit(f"Missing module: {module_file}")

        result = subprocess.run(
            [modinfo, "-F", "name", str(module_file)],
            check=True,
            capture_output=True,
            text=True,
        )
        module_name = result.stdout.strip()
        if module_name and module_name not in seen:
            seen.add(module_name)
            names.append(module_name)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(names) + "\n")
    print(f"Wrote {len(names)} modules to {args.output}")


if __name__ == "__main__":
    main()
