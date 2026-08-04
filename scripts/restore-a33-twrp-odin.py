#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
VERIFY_PATH = HERE / "verify-a33-twrp-rescue-assets.py"
REQUIRED_CONFIRMATION = "RESTORE-EXACT-TWRP"

spec = importlib.util.spec_from_file_location("a33_twrp_rescue_verify", VERIFY_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load TWRP rescue verifier: {VERIFY_PATH}")
verify = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verify
spec.loader.exec_module(verify)


class RestoreError(RuntimeError):
    pass


def require_confirmation(value: str) -> None:
    if value != REQUIRED_CONFIRMATION:
        raise RestoreError(
            "this operation writes only the recovery partition through Samsung "
            f"Download Mode; pass the exact token {REQUIRED_CONFIRMATION!r}"
        )


def run_logged(args: list[str], log: Path) -> str:
    log.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    lines: list[str] = []
    assert process.stdout is not None
    with log.open("w", encoding="utf-8") as stream:
        for line in process.stdout:
            print(line, end="")
            stream.write(line)
            stream.flush()
            lines.append(line)
    returncode = process.wait()
    if returncode != 0:
        raise RestoreError(
            f"command failed rc={returncode}: {args!r}; log={log}"
        )
    return "".join(lines)


def report_pairs(assets: verify.RescueAssets) -> list[tuple[str, object]]:
    return [
        ("created", datetime.now().astimezone().isoformat(timespec="microseconds")),
        ("operation", "restore-exact-twrp-through-odin-python"),
        ("implementation_language", "python3"),
        ("odin", assets.odin),
        ("odin_sha256", assets.odin_sha256),
        ("rescue_tar", assets.rescue_tar),
        ("rescue_tar_sha256", assets.rescue_tar_sha256),
        ("twrp_sha256", assets.twrp_sha256),
        ("userdata_written", "no"),
        ("cache_written", "no"),
        ("super_written", "no"),
        ("boot_written", "no"),
        ("recovery_written", "yes"),
        ("reboot_performed", "no"),
        ("odin_command_status", "passed"),
        ("next_action", "boot-twrp-directly-before-android"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore exact known-good TWRP through Samsung Download Mode"
    )
    parser.add_argument("confirmation", nargs="?", default="")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--odin", type=Path)
    parser.add_argument("--rescue-tar", type=Path)
    parser.add_argument("--sudo", default="sudo")
    args = parser.parse_args()
    require_confirmation(args.confirmation)

    root = args.root.expanduser().resolve()
    odin = (
        args.odin.expanduser().resolve()
        if args.odin is not None
        else root / "tools/odin4"
    )
    rescue_tar = (
        args.rescue_tar.expanduser().resolve()
        if args.rescue_tar is not None
        else root / "build/rescue/twrp-a33x-restore.img.tar"
    )
    sudo = shutil.which(args.sudo)
    if sudo is None:
        raise RestoreError(f"sudo executable is unavailable: {args.sudo}")

    assets = verify.verify_assets(root=root, odin=odin, rescue_tar=rescue_tar)
    build = root / "build"
    print("=== Confirm Download Mode device is visible to Odin ===")
    run_logged([sudo, str(assets.odin), "-l"], build / "a33-odin-list.txt")
    print("=== Restore exact known-good TWRP recovery ===")
    run_logged(
        [sudo, str(assets.odin), "-a", str(assets.rescue_tar)],
        build / "a33-odin-restore-output.txt",
    )

    report = build / "a33-twrp-odin-restore.txt"
    pairs = report_pairs(assets)
    report.write_text(
        "".join(f"{key}={value}\n" for key, value in pairs),
        encoding="utf-8",
    )
    for key, value in pairs:
        print(f"{key}={value}")
    print(f"report={report}")
    print("Immediately boot TWRP directly; do not boot Android first.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RestoreError, verify.RescueError, OSError, ValueError) as exc:
        print(f"REFUSING TWRP RESTORE: {exc}", file=sys.stderr)
        raise SystemExit(2 if "exact token" in str(exc) else 1)
