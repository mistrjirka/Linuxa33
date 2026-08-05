#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "inspect-a33-installed-ssh-v2.py"
EXPECTED_BASE_BLOB = "ed5f4050809305171fa2e85a868249ee28e2b633"
EXACT_USERDATA = "/dev/block/sda36"

spec = importlib.util.spec_from_file_location("a33_installed_ssh_v3_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load installed SSH inspector: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


def configure_exact_userdata() -> None:
    common = base.base.common
    if base.base.EXPECTED_USERDATA != EXACT_USERDATA:
        raise common.Refusal(
            "installed SSH inspector expected userdata node changed: "
            f"actual={base.base.EXPECTED_USERDATA!r} expected={EXACT_USERDATA!r}"
        )
    if common.EXPECTED_USERDATA != EXACT_USERDATA:
        raise common.Refusal(
            "shared recovery helper expected userdata node changed: "
            f"actual={common.EXPECTED_USERDATA!r} expected={EXACT_USERDATA!r}"
        )
    common.USERDATA = EXACT_USERDATA


def main() -> int:
    repo = Path.home() / "Linuxa33"
    actual = base.base.common.run(
        ["git", "-C", str(repo), "hash-object", str(BASE)],
        check=False,
    ).stdout.strip()
    if actual != EXPECTED_BASE_BLOB:
        raise base.base.common.Refusal(
            "checked-in installed SSH v2 inspector changed: "
            f"actual={actual!r} expected={EXPECTED_BASE_BLOB!r}"
        )
    configure_exact_userdata()
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
    except (base.base.common.Refusal, OSError, ValueError) as exc:
        print(f"A33 INSTALLED SSH V3 INSPECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
