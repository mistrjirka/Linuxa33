#!/usr/bin/env python3
from __future__ import annotations

import compileall
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def main() -> int:
    print("=== Compile Python sources ===")
    if not compileall.compile_dir(SCRIPTS, quiet=1, force=True):
        print("Python compilation failed", file=sys.stderr)
        return 1

    tests = sorted(SCRIPTS.glob("test-*.py"))
    if not tests:
        print("No host test scripts found", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    failures: list[tuple[Path, int]] = []

    print(f"=== Run {len(tests)} host test scripts ===")
    for test in tests:
        print(f"\n--- {test.relative_to(ROOT)} ---", flush=True)
        try:
            completed = subprocess.run(
                [sys.executable, str(test)],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
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
            print(f"TIMEOUT: {test} exceeded 120 seconds", file=sys.stderr)
            failures.append((test, 124))
            continue

        print(completed.stdout, end="")
        print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode != 0:
            failures.append((test, completed.returncode))

    print("\n=== Host test summary ===")
    print(f"host_test_count={len(tests)}")
    print(f"host_test_failures={len(failures)}")
    if failures:
        for path, rc in failures:
            print(f"failed_test={path.relative_to(ROOT)} rc={rc}", file=sys.stderr)
        return 1

    print("host_test_status=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
