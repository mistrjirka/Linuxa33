#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_AUDIT_PATH = HERE / "audit-a33-u0o-candidate.py"
BUILDER_V2_PATH = HERE / "make-u0o-persistent-sshd-trace-v2.py"
EXPECTED_BASE_AUDIT_BLOB = "2784ab9d46c39d49dc87802e09b30b30635e3407"
EXPECTED_BUILDER_V2_BLOB = "88cd0b9b3446314c04ad0c4b20583c2e6facf449"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder_v2 = load("a33_u0o_v2_audit_builder", BUILDER_V2_PATH)
base = load("a33_u0o_v2_audit_base", BASE_AUDIT_PATH)
base.builder.patch_init_second = builder_v2.patch_init_second


def git_blob(repo: Path, path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def main() -> int:
    repo = Path.home() / "Linuxa33"
    for path, expected in (
        (BASE_AUDIT_PATH, EXPECTED_BASE_AUDIT_BLOB),
        (BUILDER_V2_PATH, EXPECTED_BUILDER_V2_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise base.AuditError(
                f"checked-in U0o v2 audit dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        base.AuditError,
        base.builder.Refusal,
        base.u0n_audit.AuditError,
        base.v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        print(f"REFUSING U0o v2 AUDIT: {exc}", file=sys.stderr)
        raise SystemExit(1)
