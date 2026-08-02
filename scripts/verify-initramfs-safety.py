#!/usr/bin/env python3
"""Fail closed when an A33 debug initramfs contains known unsafe modules."""

from __future__ import annotations

import argparse
import fnmatch
from pathlib import Path
import shutil
import subprocess
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_BLOCKLIST = REPO_ROOT / "config/modules-initfs-blocklist.glob"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initramfs", type=Path, required=True)
    parser.add_argument(
        "--blocklist",
        type=Path,
        default=DEFAULT_BLOCKLIST,
    )
    parser.add_argument(
        "--max-modules",
        type=int,
        default=128,
        help="Fail when more than this many .ko files are embedded",
    )
    return parser.parse_args()


def read_patterns(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"Blocklist not found: {path}")
    patterns: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            patterns.append(line)
    return patterns


def strip_module_suffix(filename: str) -> str:
    name = Path(filename).name
    for suffix in (".zst", ".xz", ".gz"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if name.endswith(".ko"):
        name = name[:-3]
    return name


def normalized(value: str) -> str:
    return value.replace("-", "_")


def match_pattern(path: str, patterns: list[str]) -> str | None:
    basename = Path(path).name
    stem = strip_module_suffix(basename)
    candidates = {
        path,
        basename,
        stem,
        normalized(stem),
    }
    for pattern in patterns:
        normalized_pattern = normalized(pattern)
        for candidate in candidates:
            if fnmatch.fnmatch(candidate, pattern) or fnmatch.fnmatch(
                normalized(candidate), normalized_pattern
            ):
                return pattern
    return None


def list_archive(initramfs: Path) -> list[str]:
    gzip = shutil.which("gzip")
    cpio = shutil.which("cpio")
    if gzip is None or cpio is None:
        raise SystemExit("gzip and cpio are required for initramfs inspection")

    gzip_process = subprocess.Popen(
        [gzip, "-dc", str(initramfs)],
        stdout=subprocess.PIPE,
    )
    assert gzip_process.stdout is not None
    cpio_process = subprocess.run(
        [cpio, "-it"],
        stdin=gzip_process.stdout,
        capture_output=True,
        text=True,
    )
    gzip_process.stdout.close()
    gzip_status = gzip_process.wait()

    if gzip_status != 0:
        raise SystemExit(f"gzip failed while reading {initramfs}")
    if cpio_process.returncode != 0:
        print(cpio_process.stderr, file=sys.stderr)
        raise SystemExit(f"cpio failed while reading {initramfs}")

    return [line.strip() for line in cpio_process.stdout.splitlines() if line.strip()]


def main() -> None:
    args = parse_args()
    initramfs = args.initramfs.resolve()
    if not initramfs.is_file():
        raise SystemExit(f"Initramfs not found: {initramfs}")

    patterns = read_patterns(args.blocklist)
    entries = list_archive(initramfs)
    modules = [
        entry
        for entry in entries
        if ".ko" in Path(entry).name
        and any(
            Path(entry).name.endswith(suffix)
            for suffix in (".ko", ".ko.gz", ".ko.xz", ".ko.zst")
        )
    ]

    violations: list[tuple[str, str]] = []
    for module in modules:
        pattern = match_pattern(module, patterns)
        if pattern is not None:
            violations.append((module, pattern))

    print(f"Initramfs: {initramfs}")
    print(f"Embedded kernel modules: {len(modules)}")
    print(f"Maximum permitted: {args.max_modules}")

    if len(modules) > args.max_modules:
        raise SystemExit(
            f"REFUSING IMAGE: {len(modules)} modules exceed the guarded limit "
            f"of {args.max_modules}. The failed image contained 315 modules."
        )

    if violations:
        lines = ["REFUSING IMAGE: blocked modules are embedded:"]
        lines.extend(
            f"  {module}  (matched {pattern})"
            for module, pattern in violations
        )
        raise SystemExit("\n".join(lines))

    print("Safety check passed: no blocked MIPI/display/camera modules found")


if __name__ == "__main__":
    main()
