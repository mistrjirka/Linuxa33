#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import base64
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import time

EXPECTED_SERIAL = "RFCTA00V43L"
EXPECTED_TWRP_SHA256 = (
    "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
)
EXPECTED_TWRP_BYTES = 100663296
EXPECTED_TWRP_SECTORS = EXPECTED_TWRP_BYTES // 512
EXPECTED_TWRP_KERNEL = "5.10.66-Gabriel260BR-TWRP-ga0103aac9499"
EXPECTED_TWRP_CONFIG_SHA256 = (
    "7dd732d5b653571497e3e77d286705efc5b4247dcdc937afffc54827b4f3997c"
)
EXPECTED_RECOVERY_NODE = "/dev/block/sda16"
EXPECTED_METADATA_NODE = "/dev/block/sda26"
EXPECTED_USERDATA_NODE = "/dev/block/sda36"
EXPECTED_USERDATA_SECTORS = 223125504

METADATA_MOUNT = "/tmp/a33-current-metadata-ro"
USERDATA_MOUNT = "/tmp/a33-current-userdata-ro"
METADATA_TREE = f"{METADATA_MOUNT}/a33x-bringup"
ROOTFS_LOG_TREE = f"{USERDATA_MOUNT}/var/log"

DEFAULT_MAX_FILE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_TREE_BYTES = 64 * 1024 * 1024


class CollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int
    mode: str
    uid: int
    gid: int
    mtime: int


def stage(message: str) -> None:
    print(message, flush=True)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Adb:
    def __init__(self, executable: str, serial: str) -> None:
        self.executable = executable
        self.serial = serial

    def _base(self) -> list[str]:
        return [self.executable, "-s", self.serial]

    def run(
        self,
        args: list[str],
        *,
        timeout: float,
        text: bool = True,
        input_data: str | bytes | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        command = [*self._base(), *args]
        completed = subprocess.run(
            command,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            timeout=timeout,
            check=False,
        )
        if check and completed.returncode != 0:
            stdout = completed.stdout if text else completed.stdout.decode(errors="replace")
            stderr = completed.stderr if text else completed.stderr.decode(errors="replace")
            raise CollectionError(
                f"ADB command failed rc={completed.returncode}: {command!r}\n"
                f"stdout={stdout[-4000:]}\nstderr={stderr[-4000:]}"
            )
        return completed

    def shell_script(
        self,
        script: str,
        *args: str,
        timeout: float,
        check: bool = True,
    ) -> str:
        completed = self.run(
            ["shell", "sh", "-s", "--", *args],
            timeout=timeout,
            text=True,
            input_data=script,
            check=check,
        )
        return completed.stdout.replace("\r\n", "\n").replace("\r", "")

    def exec_script(
        self,
        script: bytes,
        *args: str,
        timeout: float,
        check: bool = True,
    ) -> bytes:
        completed = self.run(
            ["exec-out", "sh", "-s", "--", *args],
            timeout=timeout,
            text=False,
            input_data=script,
            check=check,
        )
        return completed.stdout

    def probe(self, attempts: int = 30) -> None:
        probe_script = "set -eu\nprintf 'a33_twrp_adb_ready\\n'\n"
        for attempt in range(1, attempts + 1):
            try:
                output = self.shell_script(probe_script, timeout=3)
            except (CollectionError, subprocess.TimeoutExpired):
                output = ""
            if output.strip() == "a33_twrp_adb_ready":
                stage(f"twrp_adb_ready=passed attempt={attempt}")
                return
            time.sleep(1)
        raise CollectionError("TWRP adb shell did not answer within 30 bounded probes")


VERIFY_TWRP_SCRIPT = r'''set -eu
recovery=/dev/block/by-name/recovery
[ -b "$recovery" ] || { echo "error=recovery-block-device-missing"; exit 20; }
resolved="$(readlink -f "$recovery" 2>/dev/null || true)"
name="${resolved##*/}"
[ "$resolved" = "$1" ] || { echo "error=recovery-node-mismatch actual=$resolved expected=$1"; exit 21; }
[ -r "/sys/class/block/$name/size" ] || { echo "error=recovery-size-unreadable"; exit 22; }
sectors="$(cat "/sys/class/block/$name/size")"
[ "$sectors" = "$2" ] || { echo "error=recovery-sector-mismatch actual=$sectors expected=$2"; exit 23; }
sha="$(sha256sum "$recovery" | awk 'NR == 1 {print $1}')"
[ "$sha" = "$3" ] || { echo "error=recovery-sha-mismatch actual=$sha expected=$3"; exit 24; }
kernel="$(uname -r)"
[ "$kernel" = "$4" ] || { echo "error=twrp-kernel-mismatch actual=$kernel expected=$4"; exit 25; }
config_sha="$(sha256sum /proc/config.gz | awk 'NR == 1 {print $1}')"
[ "$config_sha" = "$5" ] || { echo "error=twrp-config-mismatch actual=$config_sha expected=$5"; exit 26; }
echo "recovery_resolved=$resolved"
echo "recovery_sectors=$sectors"
echo "recovery_sha256=$sha"
echo "twrp_kernel=$kernel"
echo "twrp_config_gz_sha256=$config_sha"
echo "exact_twrp_runtime=passed"
'''


MOUNT_SCRIPT = r'''set -eu
metadata_link=/dev/block/by-name/metadata
userdata_link=/dev/block/by-name/userdata
metadata_expected="$1"
userdata_expected="$2"
userdata_sectors_expected="$3"
metadata_mount="$4"
userdata_mount="$5"

for point in /data /sdcard; do
    if awk -v point="$point" '$2 == point { found=1 } END { exit found ? 0 : 1 }' /proc/mounts; then
        echo "error=required-twrp-mount-still-active point=$point"
        exit 30
    fi
done

for point in "$metadata_mount" "$userdata_mount"; do
    if awk -v point="$point" '$2 == point { found=1 } END { exit found ? 0 : 1 }' /proc/mounts; then
        echo "error=temporary-mountpoint-already-active point=$point"
        exit 31
    fi
done

[ -b "$metadata_link" ] || { echo "error=metadata-block-device-missing"; exit 32; }
[ -b "$userdata_link" ] || { echo "error=userdata-block-device-missing"; exit 33; }
metadata_resolved="$(readlink -f "$metadata_link" 2>/dev/null || true)"
userdata_resolved="$(readlink -f "$userdata_link" 2>/dev/null || true)"
[ "$metadata_resolved" = "$metadata_expected" ] || {
    echo "error=metadata-node-mismatch actual=$metadata_resolved expected=$metadata_expected"
    exit 34
}
[ "$userdata_resolved" = "$userdata_expected" ] || {
    echo "error=userdata-node-mismatch actual=$userdata_resolved expected=$userdata_expected"
    exit 35
}
userdata_name="${userdata_resolved##*/}"
userdata_sectors="$(cat "/sys/class/block/$userdata_name/size")"
[ "$userdata_sectors" = "$userdata_sectors_expected" ] || {
    echo "error=userdata-sector-mismatch actual=$userdata_sectors expected=$userdata_sectors_expected"
    exit 36
}

while read -r source point rest; do
    source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
    if [ "$source" = "$metadata_link" ] || [ "$source" = "$metadata_resolved" ] || \
       [ "$source_resolved" = "$metadata_resolved" ] || \
       [ "$source" = "$userdata_link" ] || [ "$source" = "$userdata_resolved" ] || \
       [ "$source_resolved" = "$userdata_resolved" ]; then
        echo "error=target-device-already-mounted source=$source point=$point resolved=$source_resolved"
        exit 37
    fi
done < /proc/mounts

mkdir -p "$metadata_mount" "$userdata_mount"
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$metadata_resolved" "$metadata_mount"
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$userdata_resolved" "$userdata_mount" || {
    umount "$metadata_mount" 2>/dev/null || true
    exit 38
}

echo "metadata_resolved=$metadata_resolved"
echo "userdata_resolved=$userdata_resolved"
echo "userdata_sectors=$userdata_sectors"
echo "persistent_readonly_mounts=passed"
'''


CLEANUP_SCRIPT = r'''set -u
metadata_mount="$1"
userdata_mount="$2"
umount "$userdata_mount" 2>/dev/null || true
umount "$metadata_mount" 2>/dev/null || true
rmdir "$userdata_mount" 2>/dev/null || true
rmdir "$metadata_mount" 2>/dev/null || true
remaining="$(awk -v a="$metadata_mount" -v b="$userdata_mount" '$2 == a || $2 == b {print $0}' /proc/mounts 2>/dev/null || true)"
if [ -n "$remaining" ]; then
    echo "cleanup_remaining_begin"
    printf '%s\n' "$remaining"
    echo "cleanup_remaining_end"
    exit 40
fi
echo "cleanup_verified=yes"
echo "phone_partition_writes=no"
'''


LIST_FILES_SCRIPT = r'''set -eu
root="$1"
if [ ! -d "$root" ]; then
    echo "tree_state=missing"
    exit 0
fi
echo "tree_state=present"
find "$root" -type f 2>/dev/null | sort | while IFS= read -r path; do
    [ -n "$path" ] || continue
    encoded="$(printf '%s' "$path" | base64 | tr -d '\n')"
    printf 'FILE\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$encoded" \
        "$(stat -c '%s' "$path")" \
        "$(stat -c '%a' "$path")" \
        "$(stat -c '%u' "$path")" \
        "$(stat -c '%g' "$path")" \
        "$(stat -c '%Y' "$path")"
done
'''


READ_FILE_SCRIPT = b'''set -eu
path="$1"
[ -f "$path" ]
cat "$path"
'''


REMOTE_SHA_SCRIPT = r'''set -eu
path="$1"
[ -f "$path" ]
sha256sum "$path" | awk 'NR == 1 {print $1}'
'''


SNAPSHOTS: tuple[tuple[str, str], ...] = (
    ("twrp-dmesg.txt", "dmesg"),
    ("twrp-getprop.txt", "getprop"),
    ("twrp-cmdline.txt", "cat /proc/cmdline"),
    ("twrp-mounts.txt", "cat /proc/mounts"),
    (
        "twrp-pstore-state.txt",
        "ls -la /sys/fs/pstore 2>&1; "
        "find /sys/fs/pstore -maxdepth 1 -type f -print 2>/dev/null",
    ),
    ("last_kmsg.bin", "cat /proc/last_kmsg"),
)


def parse_remote_files(text: str) -> tuple[str, list[RemoteFile]]:
    state = "missing-marker"
    files: list[RemoteFile] = []
    for raw_line in text.splitlines():
        if raw_line.startswith("tree_state="):
            state = raw_line.split("=", 1)[1]
            continue
        if not raw_line.startswith("FILE\t"):
            if raw_line.startswith("error="):
                raise CollectionError(f"remote file listing failed: {raw_line}")
            continue
        fields = raw_line.split("\t")
        if len(fields) != 7:
            raise CollectionError(f"malformed remote file record: {raw_line!r}")
        try:
            path = base64.b64decode(fields[1], validate=True).decode("utf-8")
            files.append(
                RemoteFile(
                    path=path,
                    size=int(fields[2]),
                    mode=fields[3],
                    uid=int(fields[4]),
                    gid=int(fields[5]),
                    mtime=int(fields[6]),
                )
            )
        except (ValueError, UnicodeError) as exc:
            raise CollectionError(f"invalid remote file record: {raw_line!r}") from exc
    return state, files


def safe_relative(remote_path: str, remote_root: str) -> Path:
    path = PurePosixPath(remote_path)
    root = PurePosixPath(remote_root)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise CollectionError(
            f"remote path escaped expected tree: path={remote_path} root={remote_root}"
        ) from exc
    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        raise CollectionError(f"unsafe relative path: {relative}")
    return Path(*relative.parts)


def collect_tree(
    adb: Adb,
    *,
    remote_root: str,
    local_root: Path,
    label: str,
    max_file_bytes: int,
    max_tree_bytes: int,
    required: bool,
) -> list[dict[str, object]]:
    stage(f"stage=listing-{label}")
    listing = adb.shell_script(LIST_FILES_SCRIPT, remote_root, timeout=30)
    tree_state, files = parse_remote_files(listing)
    stage(f"{label}_tree_state={tree_state}")
    stage(f"{label}_remote_file_count={len(files)}")
    if tree_state != "present":
        if required:
            raise CollectionError(f"required remote tree is unavailable: {remote_root}")
        return []

    records: list[dict[str, object]] = []
    selected_total = 0
    for remote in files:
        relative = safe_relative(remote.path, remote_root)
        record: dict[str, object] = {
            "label": label,
            "remote_path": remote.path,
            "relative_path": relative.as_posix(),
            "remote_bytes": remote.size,
            "remote_mode": remote.mode,
            "remote_uid": remote.uid,
            "remote_gid": remote.gid,
            "remote_mtime": remote.mtime,
        }
        if remote.size > max_file_bytes:
            record["collection_state"] = "skipped-file-size-limit"
            records.append(record)
            stage(
                f"file_state=skipped-file-size-limit label={label} "
                f"bytes={remote.size} path={relative.as_posix()}"
            )
            continue
        if selected_total + remote.size > max_tree_bytes:
            record["collection_state"] = "skipped-tree-size-limit"
            records.append(record)
            stage(
                f"file_state=skipped-tree-size-limit label={label} "
                f"bytes={remote.size} path={relative.as_posix()}"
            )
            continue

        destination = local_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage(
            f"stage=pulling-file label={label} bytes={remote.size} "
            f"path={relative.as_posix()}"
        )
        try:
            remote_sha = adb.shell_script(
                REMOTE_SHA_SCRIPT, remote.path, timeout=20
            ).strip()
            payload = adb.exec_script(
                READ_FILE_SCRIPT,
                remote.path,
                timeout=max(30, min(120, remote.size / (256 * 1024) + 15)),
            )
        except (CollectionError, subprocess.TimeoutExpired) as exc:
            record["collection_state"] = "failed"
            record["error"] = str(exc)
            records.append(record)
            stage(f"file_state=failed label={label} path={relative.as_posix()}")
            if required:
                raise
            continue

        actual_sha = sha256_bytes(payload)
        if len(payload) != remote.size:
            raise CollectionError(
                f"transport size mismatch for {remote.path}: "
                f"received={len(payload)} expected={remote.size}"
            )
        if actual_sha != remote_sha:
            raise CollectionError(
                f"transport SHA256 mismatch for {remote.path}: "
                f"received={actual_sha} expected={remote_sha}"
            )
        destination.write_bytes(payload)
        selected_total += remote.size
        record["collection_state"] = "collected"
        record["sha256"] = actual_sha
        record["local_path"] = str(destination)
        records.append(record)
        stage(
            f"file_state=collected label={label} bytes={remote.size} "
            f"sha256={actual_sha} path={relative.as_posix()}"
        )
    stage(f"{label}_collected_bytes={selected_total}")
    return records


def capture_snapshots(adb: Adb, out: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    snapshot_dir = out / "twrp-snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for name, command in SNAPSHOTS:
        stage(f"stage=capturing-snapshot name={name}")
        try:
            completed = adb.run(
                ["exec-out", "sh", "-s"],
                timeout=30,
                text=False,
                input_data=(command + "\n").encode("utf-8"),
                check=False,
            )
            payload = completed.stdout
            destination = snapshot_dir / name
            destination.write_bytes(payload)
            records.append(
                {
                    "name": name,
                    "command": command,
                    "returncode": completed.returncode,
                    "bytes": len(payload),
                    "sha256": sha256_bytes(payload),
                    "stderr": completed.stderr.decode(errors="replace")[-4000:],
                }
            )
        except subprocess.TimeoutExpired:
            records.append(
                {
                    "name": name,
                    "command": command,
                    "returncode": "timeout",
                    "bytes": 0,
                    "sha256": "",
                    "stderr": "timeout",
                }
            )
    return records


def cleanup(adb: Adb) -> str:
    try:
        return adb.shell_script(
            CLEANUP_SCRIPT,
            METADATA_MOUNT,
            USERDATA_MOUNT,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "cleanup_timeout=yes\n"


def write_manifest(out: Path) -> None:
    lines: list[str] = []
    for path in sorted(item for item in out.rglob("*") if item.is_file()):
        relative = path.relative_to(out).as_posix()
        lines.append(
            f"bytes={path.stat().st_size} sha256={sha256_file(path)} path={relative}"
        )
    (out / "manifest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_archive(out: Path) -> tuple[Path, str]:
    archive = out.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        tar.add(out, arcname=out.name, recursive=True)
    return archive, sha256_file(archive)


def write_json(path: Path, value: object) -> None:
    import json

    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect bounded persistent A33 metadata and rootfs logs from exact "
            "TWRP using read-only mounts and bounded direct adb shell probes"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", default=EXPECTED_SERIAL)
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--max-tree-bytes", type=int, default=DEFAULT_MAX_TREE_BYTES)
    args = parser.parse_args()

    if args.serial != EXPECTED_SERIAL:
        raise CollectionError(
            f"refusing unexpected serial: actual={args.serial} expected={EXPECTED_SERIAL}"
        )
    if args.max_file_bytes <= 0 or args.max_tree_bytes <= 0:
        raise CollectionError("size bounds must be positive")
    if args.max_file_bytes > args.max_tree_bytes:
        raise CollectionError("max-file-bytes cannot exceed max-tree-bytes")

    adb_executable = shutil.which(args.adb)
    if not adb_executable:
        raise CollectionError(f"adb executable not found: {args.adb}")
    adb = Adb(adb_executable, args.serial)

    root = args.root.expanduser().resolve()
    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"a33-current-persistent-state-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)

    mounted = False
    summary: dict[str, object] = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "collect-a33-current-persistent-state-read-only",
        "implementation_language": "python3",
        "adb_serial": args.serial,
        "max_file_bytes": args.max_file_bytes,
        "max_tree_bytes": args.max_tree_bytes,
        "phone_partition_writes": "no",
    }

    try:
        stage("stage=probing-twrp-adb")
        adb.probe()

        stage("stage=verifying-exact-twrp")
        twrp_output = adb.shell_script(
            VERIFY_TWRP_SCRIPT,
            EXPECTED_RECOVERY_NODE,
            str(EXPECTED_TWRP_SECTORS),
            EXPECTED_TWRP_SHA256,
            EXPECTED_TWRP_KERNEL,
            EXPECTED_TWRP_CONFIG_SHA256,
            timeout=120,
        )
        print(twrp_output, end="", flush=True)
        (out / "exact-twrp-verification.txt").write_text(twrp_output, encoding="utf-8")

        stage("stage=mounting-persistent-filesystems-read-only")
        mount_output = adb.shell_script(
            MOUNT_SCRIPT,
            EXPECTED_METADATA_NODE,
            EXPECTED_USERDATA_NODE,
            str(EXPECTED_USERDATA_SECTORS),
            METADATA_MOUNT,
            USERDATA_MOUNT,
            timeout=30,
        )
        print(mount_output, end="", flush=True)
        (out / "readonly-mount-report.txt").write_text(mount_output, encoding="utf-8")
        if "persistent_readonly_mounts=passed" not in mount_output:
            raise CollectionError("read-only mount script did not report success")
        mounted = True

        metadata_records = collect_tree(
            adb,
            remote_root=METADATA_TREE,
            local_root=out / "metadata-a33x-bringup",
            label="metadata",
            max_file_bytes=args.max_file_bytes,
            max_tree_bytes=args.max_tree_bytes,
            required=False,
        )
        log_records = collect_tree(
            adb,
            remote_root=ROOTFS_LOG_TREE,
            local_root=out / "rootfs-var-log",
            label="rootfs-log",
            max_file_bytes=args.max_file_bytes,
            max_tree_bytes=args.max_tree_bytes,
            required=False,
        )
        snapshot_records = capture_snapshots(adb, out)

        write_json(out / "metadata-files.json", metadata_records)
        write_json(out / "rootfs-log-files.json", log_records)
        write_json(out / "twrp-snapshots.json", snapshot_records)
        summary.update(
            {
                "metadata_remote_file_count": len(metadata_records),
                "metadata_collected_file_count": sum(
                    item.get("collection_state") == "collected"
                    for item in metadata_records
                ),
                "rootfs_log_remote_file_count": len(log_records),
                "rootfs_log_collected_file_count": sum(
                    item.get("collection_state") == "collected" for item in log_records
                ),
                "snapshot_count": len(snapshot_records),
                "collection_status": "passed",
            }
        )
    finally:
        stage("stage=cleaning-up-read-only-mounts")
        cleanup_output = cleanup(adb)
        print(cleanup_output, end="", flush=True)
        (out / "cleanup-report.txt").write_text(cleanup_output, encoding="utf-8")
        if mounted and "cleanup_verified=yes" not in cleanup_output:
            summary["cleanup_status"] = "failed-or-unverified"
        else:
            summary["cleanup_status"] = "passed"

    if summary.get("collection_status") != "passed":
        raise CollectionError("collection did not complete")
    if summary.get("cleanup_status") != "passed":
        raise CollectionError("temporary read-only mount cleanup was not verified")

    write_json(out / "summary.json", summary)
    write_manifest(out)
    stage("stage=creating-archive")
    archive, archive_sha = make_archive(out)
    stage(f"observation_directory={out}")
    stage(f"observation_archive={archive}")
    stage(f"observation_archive_sha256={archive_sha}")
    stage("phone_partition_writes=no")
    stage("collection_status=passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("COLLECTION INTERRUPTED", file=sys.stderr)
        raise SystemExit(130)
    except (
        CollectionError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"COLLECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
