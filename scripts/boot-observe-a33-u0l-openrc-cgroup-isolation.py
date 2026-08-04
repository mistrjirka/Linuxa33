#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


common = load(
    "a33_u0l_observer_common",
    HERE / "flash-a33-u0i-python-direct-root-v2.py",
)
u0l = load(
    "a33_u0l_flash_profile",
    HERE / "flash-a33-u0l-openrc-cgroup-isolation.py",
)

sys.path.insert(0, str(HERE / "lib"))
from a33_rootfs_boot_observer import ObserverProfile, execute_observer

PROFILE = ObserverProfile(
    expected_flash_operation="flash-exact-u0l-openrc-cgroup-isolation",
    flash_report_name="a33-first-rootfs-u0l-openrc-cgroup-isolation-flash.txt",
    output_prefix="u0l-openrc-cgroup-isolation-observation",
    observation_operation="observe-u0l-openrc-cgroup-isolation",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Controlled observer for the U0l OpenRC cgroup-isolation boot"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default=os.environ.get("ADB", "adb"))
    parser.add_argument("--max-seconds", type=int, default=300)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    return execute_observer(
        common,
        u0l.validate_local,
        PROFILE,
        root=args.root.expanduser().resolve(),
        repo=args.repo.expanduser().resolve(),
        adb_argument=args.adb,
        max_seconds=args.max_seconds,
        preflight_only=args.preflight_only,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.Refusal as exc:
        print(f"REFUSING U0l OBSERVER: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except RuntimeError as exc:
        print(f"REFUSING U0l OBSERVER: {exc}", file=sys.stderr)
        raise SystemExit(1)
