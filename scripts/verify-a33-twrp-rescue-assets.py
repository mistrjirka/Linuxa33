#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

EXPECTED_ODIN_SHA256 = "6754aa54f2abe6e99ece32414cd34c8b23b28dbddde537a33203036813637c3b"
EXPECTED_TWRP_SHA256 = "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_TWRP_SIZE = 100663296
EXPECTED_MEMBER = PurePosixPath("recovery.img")


class RescueError(RuntimeError):
    pass


@dataclass(frozen=True)
class RescueAssets:
    odin: Path
    odin_sha256: str
    rescue_tar: Path
    rescue_tar_sha256: str
    twrp_size: int
    twrp_sha256: str
    report: Path


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_member_name(value: str) -> PurePosixPath:
    if any(character in value for character in ("\x00", "\n", "\r")):
        raise RescueError(f"unsafe rescue tar member name: {value!r}")
    while value.startswith("./"):
        value = value[2:]
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise RescueError(f"unsafe rescue tar member path: {value!r}")
    return path


def hash_stream(stream) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def write_report(path: Path, pairs: list[tuple[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in pairs),
        encoding="utf-8",
    )
    for key, value in pairs:
        print(f"{key}={value}")


def verify_assets(
    *,
    root: Path,
    odin: Path,
    rescue_tar: Path,
) -> RescueAssets:
    root = root.expanduser().resolve()
    odin = odin.expanduser().resolve()
    rescue_tar = rescue_tar.expanduser().resolve()
    if not odin.is_file() or not os.access(odin, os.X_OK):
        raise RescueError(f"Odin binary is missing or not executable: {odin}")
    if not rescue_tar.is_file():
        raise RescueError(f"TWRP rescue archive is missing: {rescue_tar}")

    odin_sha = sha_file(odin)
    if odin_sha != EXPECTED_ODIN_SHA256:
        raise RescueError(
            f"Odin SHA256 mismatch: actual={odin_sha} expected={EXPECTED_ODIN_SHA256}"
        )

    try:
        with tarfile.open(rescue_tar, "r:*") as archive:
            members = archive.getmembers()
            if len(members) != 1:
                raise RescueError(
                    f"rescue archive must contain one member, found {len(members)}"
                )
            member = members[0]
            name = normalized_member_name(member.name)
            if name != EXPECTED_MEMBER or not member.isfile() or member.issym() or member.islnk():
                raise RescueError(
                    "rescue archive must contain exactly one regular recovery.img"
                )
            stream = archive.extractfile(member)
            if stream is None:
                raise RescueError("cannot read recovery.img from rescue archive")
            with stream:
                twrp_size, twrp_sha = hash_stream(stream)
    except (tarfile.TarError, OSError) as exc:
        raise RescueError(f"cannot inspect rescue archive: {exc}") from exc

    if twrp_size != EXPECTED_TWRP_SIZE or twrp_sha != EXPECTED_TWRP_SHA256:
        raise RescueError(
            "rescue TWRP identity mismatch: "
            f"size={twrp_size}/{EXPECTED_TWRP_SIZE} "
            f"sha256={twrp_sha}/{EXPECTED_TWRP_SHA256}"
        )

    rescue_sha = sha_file(rescue_tar)
    report = root / "build/a33-twrp-rescue-assets.txt"
    write_report(
        report,
        [
            ("created", datetime.now().astimezone().isoformat(timespec="microseconds")),
            ("operation", "verify-exact-twrp-rescue-assets-python"),
            ("implementation_language", "python3"),
            ("odin", odin),
            ("odin_sha256", odin_sha),
            ("rescue_tar", rescue_tar),
            ("rescue_tar_sha256", rescue_sha),
            ("tar_entries", 1),
            ("tar_member", EXPECTED_MEMBER),
            ("twrp_size", twrp_size),
            ("twrp_sha256", twrp_sha),
            ("phone_partition_writes", "no"),
            ("verification_status", "passed"),
        ],
    )
    return RescueAssets(
        odin=odin,
        odin_sha256=odin_sha,
        rescue_tar=rescue_tar,
        rescue_tar_sha256=rescue_sha,
        twrp_size=twrp_size,
        twrp_sha256=twrp_sha,
        report=report,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify exact Odin and TWRP rescue assets without touching the phone"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--odin", type=Path)
    parser.add_argument("--rescue-tar", type=Path)
    args = parser.parse_args()
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
    assets = verify_assets(root=root, odin=odin, rescue_tar=rescue_tar)
    print(f"report={assets.report}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RescueError, OSError, UnicodeError, ValueError) as exc:
        print(f"TWRP RESCUE ASSET VERIFICATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
