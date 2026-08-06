#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "collect-a33-current-persistent-state.py"
EXPECTED_BASE_BLOB = "936bc2411268c6338ecf4220731367d58e2f2a27"


def load_base():
    spec = importlib.util.spec_from_file_location(
        "a33_current_persistent_state_v2_base", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load base collector: {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = load_base()


class CollectorV2Error(RuntimeError):
    pass


def git_blob(path: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(HERE.parent), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.strip()


def direct_exec_file_read(
    self,
    script: bytes,
    *args: str,
    timeout: float,
    check: bool = True,
) -> bytes:
    """Read one absolute remote file without `exec-out sh -s --` argv transport.

    TWRP accepts stdin scripts through `adb shell sh -s --`, but its `adb
    exec-out sh -s -- <arg>` path does not reliably populate `$1`. The base
    collector uses exec_script only for READ_FILE_SCRIPT. Route that exact call
    to the already-proven direct form `adb exec-out cat /absolute/path`.
    """
    if script != base.READ_FILE_SCRIPT:
        raise CollectorV2Error("unexpected exec_script payload in v2 collector")
    if len(args) != 1:
        raise CollectorV2Error(
            f"expected one absolute remote path, received {len(args)} arguments"
        )
    remote_path = args[0]
    if not remote_path.startswith("/") or "\x00" in remote_path:
        raise CollectorV2Error(f"unsafe remote file path: {remote_path!r}")
    completed = self.run(
        ["exec-out", "cat", remote_path],
        timeout=timeout,
        text=False,
        check=check,
    )
    return completed.stdout


def main() -> int:
    actual = git_blob(BASE_PATH)
    if actual != EXPECTED_BASE_BLOB:
        raise CollectorV2Error(
            "base persistent-state collector changed: "
            f"actual={actual} expected={EXPECTED_BASE_BLOB}"
        )

    original = base.Adb.exec_script
    try:
        base.Adb.exec_script = direct_exec_file_read
        return base.main()
    finally:
        base.Adb.exec_script = original


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("COLLECTION INTERRUPTED", file=sys.stderr)
        raise SystemExit(130)
    except (
        CollectorV2Error,
        base.CollectionError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"COLLECTION V2 FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
