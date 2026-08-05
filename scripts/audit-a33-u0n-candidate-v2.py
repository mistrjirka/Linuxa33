#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_AUDIT_PATH = HERE / "audit-a33-u0n-candidate.py"
BUILDER_V2_PATH = HERE / "make-u0n-real-boot-sshd-trace-v2.py"
EXPECTED_BASE_AUDIT_BLOB = "3152f2bbd504f842acd809156177b3c45cb7f800"
EXPECTED_BUILDER_V2_BLOB = "bbe8b22df2acc2dba3bbd79f30e1ef1165164799"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder_v2 = load("a33_u0n_v2_audit_builder", BUILDER_V2_PATH)
base = load("a33_u0n_v2_audit_base", BASE_AUDIT_PATH)

# The original audit remains authoritative for all layout, ancestry, AVB and
# component checks. Only its recomputation of the instrumented sshd script is
# redirected to the corrected, pinned v2 transformation.
base.builder.instrument_sshd_init = builder_v2.instrument_sshd_init


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
                f"checked-in U0n v2 audit dependency changed: "
                f"path={path.name} actual={actual!r} expected={expected!r}"
            )
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        base.AuditError,
        base.builder.Refusal,
        base.u0m_audit.AuditError,
        base.v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        print(f"REFUSING U0n v2 AUDIT: {exc}", file=sys.stderr)
        raise SystemExit(1)
