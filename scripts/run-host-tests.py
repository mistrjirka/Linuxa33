#!/usr/bin/env python3
from __future__ import annotations

import compileall
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"

# These are executable runtime diagnostics whose replacement imports the exact
# base implementation and applies a pinned compatibility correction. Running
# both would execute the obsolete phone-facing path as a second independent
# test and can produce a known false failure before the corrected replacement
# runs. The replacement itself remains part of the legacy test suite.
SUPERSEDED_LEGACY_TESTS: dict[str, str] = {
    "test-a33-installed-ssh-keygen-tmpfs.py": (
        "test-a33-installed-ssh-keygen-tmpfs-v2.py"
    ),
}


def run_command(args: list[str], *, timeout: int = 120) -> int:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(
            args,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        print(stdout, end="")
        print(stderr, end="", file=sys.stderr)
        print(f"TIMEOUT after {timeout}s: {args!r}", file=sys.stderr)
        return 124

    print(completed.stdout, end="")
    print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode


def discover_legacy_tests(scripts: Path = SCRIPTS) -> tuple[list[Path], list[tuple[str, str]]]:
    discovered = sorted(scripts.glob("test-*.py"))
    names = {path.name for path in discovered}
    skipped: list[tuple[str, str]] = []
    selected: list[Path] = []

    for test in discovered:
        replacement = SUPERSEDED_LEGACY_TESTS.get(test.name)
        if replacement is not None and replacement in names:
            skipped.append((test.name, replacement))
            continue
        selected.append(test)

    return selected, skipped


def main() -> int:
    print("=== Compile Python sources ===")
    compile_targets = [SCRIPTS]
    if TESTS.is_dir():
        compile_targets.append(TESTS)
    for target in compile_targets:
        if not compileall.compile_dir(target, quiet=1, force=True):
            print(f"Python compilation failed under {target}", file=sys.stderr)
            return 1

    failures: list[tuple[str, int]] = []

    print("\n=== Run discoverable unittest suite ===")
    if not TESTS.is_dir():
        failures.append(("unittest-discovery:no-tests-directory", 1))
    else:
        rc = run_command(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-t",
                ".",
                "-v",
            ]
        )
        if rc != 0:
            failures.append(("unittest-discovery", rc))

    legacy_tests, skipped_tests = discover_legacy_tests()
    if not legacy_tests:
        failures.append(("legacy-tests:none-found", 1))

    print(f"\n=== Run {len(legacy_tests)} legacy host test scripts ===")
    for old_name, replacement in skipped_tests:
        print(
            "superseded_legacy_test_skipped="
            f"{old_name} replacement={replacement}"
        )

    for test in legacy_tests:
        relative = str(test.relative_to(ROOT))
        print(f"\n--- {relative} ---", flush=True)
        rc = run_command([sys.executable, str(test)])
        if rc != 0:
            failures.append((relative, rc))

    print("\n=== Host test summary ===")
    print(f"discoverable_test_suite={'present' if TESTS.is_dir() else 'missing'}")
    print(f"legacy_test_count={len(legacy_tests)}")
    print(f"superseded_legacy_test_count={len(skipped_tests)}")
    print(f"host_test_failures={len(failures)}")
    if failures:
        for name, rc in failures:
            print(f"failed_test={name} rc={rc}", file=sys.stderr)
        return 1

    print("host_test_status=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
