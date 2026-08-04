#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from typing import Iterable

DEFAULT_IMAGE_RELATIVE = Path(
    "build/userdata-rootfs-images/20260803-193947/"
    "a33x-userdata-pmos-root.img"
)
SEARCH_ROOTS = (
    PurePosixPath("/lib/rc"),
    PurePosixPath("/etc/init.d"),
    PurePosixPath("/etc/conf.d"),
)
PRESERVE_FILES = (
    PurePosixPath("/etc/os-release"),
    PurePosixPath("/etc/rc.conf"),
    PurePosixPath("/lib/rc/sh/openrc-run.sh"),
    PurePosixPath("/lib/rc/sh/rc-cgroup.sh"),
    PurePosixPath("/etc/init.d/cgroups"),
)
APK_INSTALLED = PurePosixPath("/lib/apk/db/installed")
CGROUP_PATTERN = re.compile(
    r"cgroup_add_service|cgroup\.procs|rc_cgroup_mode|rc_cgroup_",
    re.IGNORECASE,
)
MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024
MAX_REQUIRED_FILE_BYTES = 16 * 1024 * 1024
MAX_DIRECTORIES = 2048
MAX_FILES = 8192
LS_ENTRY = re.compile(
    r"^/(?P<inode>\d+)/(?P<mode>[0-7]+)/(?P<uid>\d+)/(?P<gid>\d+)/"
    r"(?P<name>.*)/(?P<size>\d*)/$"
)
MISSING_MARKERS = (
    "file not found by ext2_lookup",
    "file not found",
    "ext2 inode is not a directory",
)


class InspectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    mode: int
    size: int

    @property
    def is_directory(self) -> bool:
        return stat.S_ISDIR(self.mode)

    @property
    def is_regular(self) -> bool:
        return stat.S_ISREG(self.mode)


@dataclass(frozen=True)
class InspectionResult:
    output_dir: Path
    report: Path
    archive: Path
    stable_report: Path


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize(value: object) -> str:
    return str(value).replace("\n", " ").replace("\r", " ")


def parse_debugfs_ls(output: str) -> list[DirectoryEntry]:
    entries: list[DirectoryEntry] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = LS_ENTRY.fullmatch(line)
        if match is None:
            raise InspectionError(f"unparseable debugfs ls -p line: {raw!r}")
        name = match.group("name")
        if name in {".", ".."}:
            continue
        entries.append(
            DirectoryEntry(
                name=name,
                mode=int(match.group("mode"), 8),
                size=int(match.group("size") or "0"),
            )
        )
    return entries


def parse_apk_installed(text: str, package: str) -> list[str]:
    versions: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        values: dict[str, str] = {}
        for raw in block.splitlines():
            if len(raw) >= 3 and raw[1] == ":":
                values.setdefault(raw[0], raw[2:])
        if values.get("P") == package and values.get("V"):
            versions.append(values["V"])
    return versions


def matching_lines(path: PurePosixPath, text: str) -> list[str]:
    result: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if CGROUP_PATTERN.search(raw):
            result.append(f"{path.as_posix()}:{number}:{raw.strip()}")
    return result


class DebugfsReader:
    def __init__(self, executable: Path, image: Path):
        self.executable = executable
        self.image = image

    @staticmethod
    def _diagnostics(stderr: bytes) -> list[str]:
        lines = stderr.decode("utf-8", errors="replace").splitlines()
        return [
            line.strip()
            for line in lines
            if line.strip() and not line.lower().startswith("debugfs ")
        ]

    def request(self, command: str, *, allow_missing: bool = False) -> bytes | None:
        completed = subprocess.run(
            [str(self.executable), "-R", command, str(self.image)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        diagnostics = self._diagnostics(completed.stderr)
        combined = "\n".join(diagnostics).lower()
        if allow_missing and any(marker in combined for marker in MISSING_MARKERS):
            return None
        if completed.returncode != 0 or diagnostics:
            detail = "; ".join(diagnostics) or f"returncode={completed.returncode}"
            raise InspectionError(f"debugfs command failed: {command!r}: {detail}")
        return completed.stdout

    def probe(self) -> str:
        output = self.request("stats")
        assert output is not None
        text = output.decode("utf-8", errors="replace")
        if not text.strip():
            raise InspectionError("debugfs stats returned empty output")
        return text

    def list_directory(self, path: PurePosixPath) -> list[DirectoryEntry] | None:
        output = self.request(f"ls -p {path.as_posix()}", allow_missing=True)
        if output is None:
            return None
        return parse_debugfs_ls(output.decode("utf-8", errors="strict"))

    def read_file(
        self,
        path: PurePosixPath,
        *,
        allow_missing: bool = False,
        maximum_bytes: int = MAX_REQUIRED_FILE_BYTES,
    ) -> bytes | None:
        output = self.request(f"cat {path.as_posix()}", allow_missing=allow_missing)
        if output is None:
            return None
        if len(output) > maximum_bytes:
            raise InspectionError(
                f"rootfs file exceeds safe inspection limit: {path} "
                f"size={len(output)} limit={maximum_bytes}"
            )
        return output


def debugfs_version(executable: Path) -> str:
    completed = subprocess.run(
        [str(executable), "-V"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise InspectionError(
            f"debugfs -V failed: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    value = " ".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if part.strip()
    )
    return value or "unknown"


def walk_regular_files(
    reader: DebugfsReader,
    roots: Iterable[PurePosixPath],
) -> list[tuple[PurePosixPath, int]]:
    queue = list(roots)
    visited: set[PurePosixPath] = set()
    files: list[tuple[PurePosixPath, int]] = []
    while queue:
        directory = queue.pop(0)
        if directory in visited:
            continue
        visited.add(directory)
        if len(visited) > MAX_DIRECTORIES:
            raise InspectionError(
                f"rootfs directory traversal exceeded limit {MAX_DIRECTORIES}"
            )
        entries = reader.list_directory(directory)
        if entries is None:
            continue
        for entry in entries:
            child = directory / entry.name
            if entry.is_directory:
                queue.append(child)
            elif entry.is_regular:
                files.append((child, entry.size))
                if len(files) > MAX_FILES:
                    raise InspectionError(
                        f"rootfs file traversal exceeded limit {MAX_FILES}"
                    )
    return sorted(files, key=lambda item: item[0].as_posix())


def write_captured_file(root: Path, path: PurePosixPath, data: bytes) -> Path:
    destination = root / path.relative_to("/").as_posix()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return destination


def write_sha256s(root: Path) -> Path:
    destination = root / "SHA256SUMS"
    rows: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path == destination:
            continue
        rows.append(f"{sha_file(path)}  {path.relative_to(root).as_posix()}")
    destination.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return destination


def archive_directory(root: Path, destination: Path) -> None:
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(root, arcname=root.name, recursive=True)


def inspect(
    *,
    root: Path,
    image: Path,
    output_dir: Path,
    debugfs: Path,
) -> InspectionResult:
    root = root.expanduser().resolve()
    image = image.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    debugfs = debugfs.expanduser().resolve()

    if not image.is_file():
        raise InspectionError(f"rootfs image is missing: {image}")
    if not debugfs.is_file() or not os.access(debugfs, os.X_OK):
        raise InspectionError(f"debugfs executable is unavailable: {debugfs}")
    if output_dir.exists():
        raise InspectionError(f"refusing to replace existing output directory: {output_dir}")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(mode=0o700)
    report = output_dir / "summary.txt"
    archive = output_dir.with_suffix(".tar.gz")
    stable_report = root / "build/a33-openrc-cgroup-inspection.txt"
    if archive.exists():
        raise InspectionError(f"refusing to replace existing archive: {archive}")

    image_size_before = image.stat().st_size
    image_sha_before = sha_file(image)
    reader = DebugfsReader(debugfs, image)
    filesystem_stats = reader.probe()
    (output_dir / "debugfs-stats.txt").write_text(
        filesystem_stats, encoding="utf-8"
    )

    captured: dict[PurePosixPath, bytes] = {}
    missing_preserved: list[str] = []
    for path in PRESERVE_FILES:
        data = reader.read_file(path, allow_missing=True)
        if data is None:
            missing_preserved.append(path.as_posix())
            continue
        captured[path] = data

    installed_data = reader.read_file(APK_INSTALLED, allow_missing=False)
    assert installed_data is not None
    installed_text = installed_data.decode("utf-8", errors="replace")
    openrc_versions = parse_apk_installed(installed_text, "openrc")

    callsites: list[str] = []
    scanned_files = walk_regular_files(reader, SEARCH_ROOTS)
    skipped_large: list[str] = []
    for path, listed_size in scanned_files:
        if listed_size > MAX_SEARCH_FILE_BYTES:
            skipped_large.append(f"{path.as_posix()}:{listed_size}")
            continue
        data = reader.read_file(
            path,
            allow_missing=False,
            maximum_bytes=MAX_SEARCH_FILE_BYTES,
        )
        assert data is not None
        if b"\x00" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        matches = matching_lines(path, text)
        if matches:
            callsites.extend(matches)
            captured.setdefault(path, data)

    rc_conf_data = captured.get(PurePosixPath("/etc/rc.conf"), b"")
    rc_conf_matches = matching_lines(
        PurePosixPath("/etc/rc.conf"),
        rc_conf_data.decode("utf-8", errors="replace"),
    )

    extracted_root = output_dir / "extracted"
    for path, data in sorted(captured.items(), key=lambda item: item[0].as_posix()):
        write_captured_file(extracted_root, path, data)

    package_report = output_dir / "openrc-package.txt"
    package_report.write_text(
        "\n".join(f"openrc_version={version}" for version in openrc_versions)
        + ("\n" if openrc_versions else "openrc_version=not-found\n"),
        encoding="utf-8",
    )

    image_size_after = image.stat().st_size
    image_sha_after = sha_file(image)
    if image_size_after != image_size_before or image_sha_after != image_sha_before:
        raise InspectionError(
            "rootfs image changed during read-only inspection: "
            f"size_before={image_size_before} size_after={image_size_after} "
            f"sha_before={image_sha_before} sha_after={image_sha_after}"
        )

    version = debugfs_version(debugfs)
    lines = [
        "operation=inspect-a33-openrc-cgroups-read-only",
        f"image={image}",
        f"image_size={image_size_before}",
        f"image_sha256_before={image_sha_before}",
        f"image_sha256_after={image_sha_after}",
        "image_unchanged=yes",
        f"debugfs={debugfs}",
        f"debugfs_version={sanitize(version)}",
        "debugfs_open_mode=read-only-no-w-flag",
        "sudo_used=no",
        "image_mounts=no",
        "phone_partition_writes=no",
        f"openrc_package_count={len(openrc_versions)}",
        *[f"openrc_package_version={sanitize(version)}" for version in openrc_versions],
        f"preserved_file_count={len(captured)}",
        f"missing_preserved_file_count={len(missing_preserved)}",
        f"missing_preserved_files={','.join(missing_preserved)}",
        f"scanned_file_count={len(scanned_files)}",
        f"skipped_large_file_count={len(skipped_large)}",
        *[f"skipped_large_file={sanitize(item)}" for item in skipped_large],
        f"rc_conf_cgroup_match_count={len(rc_conf_matches)}",
        *[f"rc_conf_cgroup_match={sanitize(item)}" for item in rc_conf_matches],
        f"cgroup_callsite_count={len(callsites)}",
        *[f"cgroup_callsite={sanitize(item)}" for item in callsites],
        f"output_dir={output_dir}",
        f"archive={archive}",
        "inspection_status=passed",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_sha256s(output_dir)
    archive_directory(output_dir, archive)
    stable_report.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(report, stable_report)

    print("\n".join(lines))
    print(f"report={report}")
    print(f"stable_report={stable_report}")
    print(f"archive_sha256={sha_file(archive)}")
    return InspectionResult(output_dir, report, archive, stable_report)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect OpenRC CPU-cgroup configuration inside the A33 ext4 rootfs "
            "without sudo, mounting, or modifying the image"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--debugfs", type=Path)
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    image = (
        args.image.expanduser().resolve()
        if args.image is not None
        else root / DEFAULT_IMAGE_RELATIVE
    )
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else root / f"build/a33-openrc-cgroup-inspection-{timestamp}"
    )
    debugfs_value = args.debugfs or (
        Path(found) if (found := shutil.which("debugfs")) else None
    )
    if debugfs_value is None:
        raise InspectionError("debugfs is unavailable; install e2fsprogs")

    inspect(
        root=root,
        image=image,
        output_dir=output_dir,
        debugfs=debugfs_value,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InspectionError, OSError, UnicodeError, ValueError, tarfile.TarError) as exc:
        print(f"OPENRC CGROUP INSPECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
