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

    legacy_tests = sorted(SCRIPTS.glob("test-*.py"))
    if not legacy_tests:
        failures.append(("legacy-tests:none-found", 1))

    print(f"\n=== Run {len(legacy_tests)} legacy host test scripts ===")
    for test in legacy_tests:
        relative = str(test.relative_to(ROOT))
        print(f"\n--- {relative} ---", flush=True)
        rc = run_command([sys.executable, str(test)])
        if rc != 0:
            failures.append((relative, rc))

    print("\n=== Host test summary ===")
    print(f"discoverable_test_suite={'present' if TESTS.is_dir() else 'missing'}")
    print(f"legacy_test_count={len(legacy_tests)}")
    print(f"host_test_failures={len(failures)}")
    if failures:
        for name, rc in failures:
            print(f"failed_test={name} rc={rc}", file=sys.stderr)
        return 1

    print("host_test_status=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
