#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent

common_spec = importlib.util.spec_from_file_location(
    "a33_u0i_flash_common", HERE / "flash-a33-u0i-python-direct-root-v2.py"
)
if common_spec is None or common_spec.loader is None:
    raise SystemExit("cannot load common flash implementation")
common = importlib.util.module_from_spec(common_spec)
common_spec.loader.exec_module(common)

u0j_spec = importlib.util.spec_from_file_location(
    "a33_u0j_flash_profile", HERE / "flash-a33-u0j-root-api-compatible.py"
)
if u0j_spec is None or u0j_spec.loader is None:
    raise SystemExit("cannot load U0j flash profile")
u0j = importlib.util.module_from_spec(u0j_spec)
u0j_spec.loader.exec_module(u0j)

sys.path.insert(0, str(HERE / "lib"))
from a33_rootfs_boot_observer import ObserverProfile, execute_observer

PROFILE = ObserverProfile(
    expected_flash_operation="flash-exact-u0j-root-api-compatible",
    flash_report_name="a33-first-rootfs-u0j-root-api-compatible-flash.txt",
    output_prefix="u0j-root-api-compatible-observation",
    observation_operation="observe-u0j-root-api-compatible",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Controlled Python observer for the U0j root-API-compatible boot"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default=os.environ.get("ADB", "adb"))
    parser.add_argument("--max-seconds", type=int, default=300)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    return execute_observer(
        common,
        u0j.validate_local,
        PROFILE,
        root=args.root.resolve(),
        repo=args.repo.resolve(),
        adb_argument=args.adb,
        max_seconds=args.max_seconds,
        preflight_only=args.preflight_only,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.Refusal as exc:
        print(f"REFUSING U0j OBSERVER: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except RuntimeError as exc:
        print(f"REFUSING U0j OBSERVER: {exc}", file=sys.stderr)
        raise SystemExit(1)
