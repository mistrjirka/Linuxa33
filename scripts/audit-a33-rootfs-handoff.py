#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import gzip
import hashlib
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from typing import Any

HERE = Path(__file__).resolve().parent
LIB = HERE / "lib"
sys.path.insert(0, str(LIB))

from a33_cpio import Archive, CpioError
from a33_shell import function_span


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Python module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


common = load_module(
    "a33_rootfs_audit_common",
    HERE / "flash-a33-u0i-python-direct-root-v2.py",
)
u0j = load_module(
    "a33_rootfs_audit_u0j",
    HERE / "flash-a33-u0j-root-api-compatible.py",
)

MIB = 1024 * 1024
AUDIT_PREFIX = "u0j-nondestructive-audit-"
READBACK_NAMES = (
    "userdata-first-765MiB.img",
    "userdata-deployment-region.img",
)
FUNCTIONS = (
    "find_root_partition",
    "wait_root_partition",
    "check_filesystem",
    "get_partition_type",
    "resize_root_partition",
    "resize_root_filesystem",
    "mount_root_partition",
    "find_boot_partition",
    "mount_boot_partition",
    "fail_halt_boot",
)


class Refusal(RuntimeError):
    pass


def refuse(message: str) -> None:
    raise Refusal(message)


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_host(
    args: list[str],
    *,
    timeout: float | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        completed = subprocess.CompletedProcess(
            args=args,
            returncode=124,
            stdout=stdout,
            stderr=stderr + f"\ncommand_timeout_seconds={timeout}\n",
        )
    if check and completed.returncode != 0:
        refuse(
            f"host command failed rc={completed.returncode}: {args!r}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def capture_command(args: list[str], path: Path, *, timeout: float | None = None) -> int:
    completed = run_host(args, timeout=timeout)
    write_text(
        path,
        f"command={args!r}\nreturncode={completed.returncode}\n"
        f"=== stdout ===\n{completed.stdout}"
        f"=== stderr ===\n{completed.stderr}",
    )
    return completed.returncode


def require_commands(names: tuple[str, ...]) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in names:
        resolved = shutil.which(name)
        if not resolved:
            refuse(f"missing required host command: {name}")
        found[name] = resolved
    return found


def choose_audit_dir(root: Path, explicit: Path | None, resume_latest: bool) -> Path:
    if explicit is not None and resume_latest:
        refuse("--audit-dir and --resume-latest are mutually exclusive")
    build = root / "build"
    build.mkdir(parents=True, exist_ok=True)
    if explicit is not None:
        out = explicit.expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        return out
    if resume_latest:
        candidates = [path for path in build.glob(f"{AUDIT_PREFIX}*") if path.is_dir()]
        if not candidates:
            refuse(f"no existing {AUDIT_PREFIX}* directory found under {build}")
        return max(candidates, key=lambda path: path.stat().st_mtime)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = build / f"{AUDIT_PREFIX}{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    return out


def choose_readback(out: Path, expected_size: int) -> tuple[Path, bool]:
    for name in READBACK_NAMES:
        path = out / name
        if path.is_file():
            if path.stat().st_size != expected_size:
                refuse(
                    f"existing readback has wrong size: {path} "
                    f"actual={path.stat().st_size} expected={expected_size}"
                )
            return path, True
    return out / "userdata-deployment-region.img", False


def compare_files(before: Path, after: Path, chunk_size: int = MIB) -> dict[str, str]:
    if before.stat().st_size != after.stat().st_size:
        refuse("comparison inputs differ in size")
    changed_chunks = 0
    exact_different_bytes = 0
    exact_count_available = True
    first_difference: int | None = None
    last_difference: int | None = None
    offset = 0
    with before.open("rb") as left, after.open("rb") as right:
        while True:
            a = left.read(chunk_size)
            b = right.read(chunk_size)
            if not a and not b:
                break
            if len(a) != len(b):
                refuse("comparison inputs changed size during reading")
            if a != b:
                changed_chunks += 1
                first_local = next(index for index, pair in enumerate(zip(a, b)) if pair[0] != pair[1])
                last_local = len(a) - 1 - next(
                    index for index, pair in enumerate(zip(reversed(a), reversed(b))) if pair[0] != pair[1]
                )
                if first_difference is None:
                    first_difference = offset + first_local
                last_difference = offset + last_local
                if changed_chunks <= 64:
                    exact_different_bytes += sum(1 for x, y in zip(a, b) if x != y)
                else:
                    exact_count_available = False
            offset += len(a)
    return {
        "changed_1MiB_chunks": str(changed_chunks),
        "different_bytes": (
            str(exact_different_bytes)
            if exact_count_available
            else "not-counted-more-than-64-changed-chunks"
        ),
        "first_difference_offset": "none" if first_difference is None else str(first_difference),
        "last_difference_offset": "none" if last_difference is None else str(last_difference),
    }


def validate_twrp_state(adb: str, serial: str, local: dict[str, object], out: Path) -> None:
    values, sections = common.live_state(adb, serial)
    expected = {
        "recovery_sha": common.KNOWN_TWRP_SHA256,
        "userdata_resolved": common.EXPECTED_USERDATA,
        "userdata_bytes": str(common.EXPECTED_USERDATA_BYTES),
        "userdata_readonly": "0",
    }
    mismatches = [
        f"{key}: actual={values.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if values.get(key) != wanted
    ]
    for section in ("swap_users", "dm_users"):
        if sections.get(section):
            mismatches.append(f"{section}: active={sections[section]!r}")
    if mismatches:
        refuse("unsafe TWRP/userdata state:\n" + "\n".join(mismatches))

    for _ in range(3):
        mounts = sections.get("mount_users", [])
        if not mounts:
            break
        for mountpoint in sorted(
            mounts,
            key=lambda value: (value.count("/"), len(value)),
            reverse=True,
        ):
            common.adb_shell(adb, serial, 'umount "$1" 2>/dev/null || true\n', mountpoint)
        values, sections = common.live_state(adb, serial)
    if sections.get("mount_users") or sections.get("swap_users") or sections.get("dm_users"):
        refuse(f"userdata remains active after exact unmount attempts: {sections}")

    root_uuid, root_label = common.ext4_identity(adb, serial)
    if root_uuid != local["root_uuid"] or root_label != "pmOS_root":
        refuse(
            f"installed rootfs identity mismatch: uuid={root_uuid!r} "
            f"label={root_label!r}"
        )
    state = {
        **values,
        "root_uuid": root_uuid,
        "root_label": root_label,
        "mount_users": sections.get("mount_users", []),
        "swap_users": sections.get("swap_users", []),
        "dm_users": sections.get("dm_users", []),
        "twrp_state": "passed",
    }
    write_text(
        out / "twrp-preaudit-state.txt",
        "".join(f"{key}={value}\n" for key, value in state.items()),
    )


def read_userdata_region(
    adb: str,
    serial: str,
    destination: Path,
    expected_size: int,
    out: Path,
) -> None:
    if expected_size % MIB != 0:
        refuse(f"deployment image size is not an exact MiB multiple: {expected_size}")
    count = expected_size // MIB
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = (
        f"dd if='{common.USERDATA}' bs={MIB} count={count} 2>/dev/null"
    )
    with destination.open("wb") as stream:
        completed = subprocess.run(
            [adb, "-s", serial, "exec-out", "sh", "-c", command],
            stdout=stream,
            stderr=subprocess.PIPE,
            check=False,
        )
    stderr = completed.stderr.decode(errors="replace")
    write_text(
        out / "readback-command.txt",
        f"command={command}\nreturncode={completed.returncode}\nstderr={stderr}\n",
    )
    if completed.returncode != 0:
        refuse(f"userdata readback failed rc={completed.returncode}: {stderr}")
    if destination.stat().st_size != expected_size:
        refuse(
            f"userdata readback has wrong size: actual={destination.stat().st_size} "
            f"expected={expected_size}"
        )


def inspect_host_filesystem(commands: dict[str, str], readback: Path, out: Path) -> int:
    capture_command([commands["file"], str(readback)], out / "readback-file-type.txt")
    capture_command([commands["dumpe2fs"], "-h", str(readback)], out / "dumpe2fs-header.txt")
    capture_command([commands["tune2fs"], "-l", str(readback)], out / "tune2fs-list.txt")
    capture_command(
        [commands["debugfs"], "-R", "stats", str(readback)],
        out / "debugfs-stats.txt",
    )
    capture_command(
        [commands["resize2fs"], "-P", str(readback)],
        out / "resize2fs-minimum-size.txt",
    )
    rc = capture_command(
        [commands["e2fsck"], "-f", "-n", "-v", str(readback)],
        out / "host-e2fsck-fnv.txt",
    )
    write_text(out / "host-e2fsck-result.txt", f"host_e2fsck_rc={rc}\n")
    return rc


TWRP_FILESYSTEM_SCRIPT = r'''set +e
target=/dev/block/by-name/userdata

echo "=== tool locations ==="
for command in e2fsck fsck.ext4 tune2fs dumpe2fs resize2fs blkid; do
    printf '%s=' "$command"
    command -v "$command" 2>/dev/null || echo missing
done

echo "=== versions ==="
e2fsck -V 2>&1
echo "e2fsck_version_rc=$?"
resize2fs -V 2>&1
echo "resize2fs_version_rc=$?"

echo "=== blkid ==="
blkid "$target" 2>&1
echo "blkid_rc=$?"

echo "=== e2fsck -fnv ==="
e2fsck -f -n -v "$target" 2>&1
echo "twrp_e2fsck_rc=$?"

echo "=== resize2fs -P ==="
resize2fs -P "$target" 2>&1
echo "twrp_resize2fs_P_rc=$?"
exit 0
'''


def inspect_twrp_filesystem(adb: str, serial: str, out: Path) -> None:
    output = common.adb_shell(adb, serial, TWRP_FILESYSTEM_SCRIPT)
    write_text(out / "twrp-filesystem-tools.txt", output)


def extract_initramfs_artifacts(initramfs: Path, out: Path) -> dict[str, Path]:
    try:
        archive = Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, CpioError) as exc:
        refuse(f"cannot parse U0j initramfs: {exc}")

    texts: dict[str, str] = {}
    for name in ("init_functions.sh", "init_functions_2nd.sh", "init_2nd.sh"):
        entry = archive.one(name)
        text = entry.data.decode("utf-8", "strict")
        texts[name] = text
        write_text(out / name, text)

    rows: list[str] = []
    find_body: str | None = None
    for target in FUNCTIONS:
        found = 0
        for filename, text in texts.items():
            try:
                start, end, body = function_span(text, target)
            except Exception:
                continue
            found += 1
            digest = hashlib.sha256(body.encode()).hexdigest()
            rows.append(
                f"=== function={target} file={filename} "
                f"lines={start + 1}-{end} sha256={digest} ===\n"
                f"{body.rstrip()}\n"
            )
            if target == "find_root_partition":
                find_body = body
        rows.append(f"definition_count={target}:{found}\n")
    if find_body is None:
        refuse("U0j initramfs has no find_root_partition definition")
    write_text(out / "root-handoff-functions.txt", "\n".join(rows))
    write_text(out / "u0j-find_root_partition.sh", find_body)

    binaries: dict[str, Path] = {}
    for archive_name, output_name in (
        ("bin/busybox", "busybox"),
        ("bin/busybox-extras", "busybox-extras"),
    ):
        try:
            entry = archive.one(archive_name)
        except CpioError:
            continue
        destination = out / output_name
        destination.write_bytes(entry.data)
        destination.chmod(0o755)
        binaries[output_name] = destination
    if "busybox" not in binaries:
        refuse("U0j initramfs does not contain bin/busybox")

    binary_pattern = re.compile(
        r"(?:^|/)(?:e2fsck|fsck(?:\.ext4)?|resize2fs|"
        r"tune2fs|dumpe2fs|blkid|mount|findfs)$"
    )
    binary_rows: list[str] = []
    for entry in archive.entries:
        if binary_pattern.search(entry.normalized):
            binary_rows.append(
                f"path={entry.normalized} size={len(entry.data)} "
                f"sha256={hashlib.sha256(entry.data).hexdigest()}"
            )
    write_text(out / "filesystem-tool-entries.txt", "\n".join(binary_rows) + "\n")
    return binaries


RUNTIME_TEST = r'''set -eu
PATH=/tmp/a33-u0j-tools:/sbin:/system/bin:/system/xbin
export PATH
. /tmp/a33-u0j-find_root_partition.sh

echo "direct_blkid=$(blkid /dev/block/sda36 2>/dev/null || true)"
stdout_value="$(find_root_partition)"
echo "stdout_rc=$?"
echo "stdout_value=$stdout_value"

consumer() {
    local partition
    partition=BEFORE
    find_root_partition partition
    rc=$?
    echo "output_variable_rc=$rc"
    echo "output_variable_value=$partition"
    [ "$rc" -eq 0 ]
    [ "$partition" = /dev/block/sda36 ]
}

consumer
echo "exact_u0j_dual_api_runtime=passed"
'''


def test_exact_busybox_runtime(
    adb: str,
    serial: str,
    binaries: dict[str, Path],
    out: Path,
) -> None:
    local_test = out / "u0j-find-root-runtime-test.sh"
    write_text(local_test, RUNTIME_TEST)

    remote_files: list[str] = []
    mapping = {
        "busybox": "/tmp/a33-u0j-busybox",
        "busybox-extras": "/tmp/a33-u0j-busybox-extras",
    }
    for name, path in binaries.items():
        remote = mapping[name]
        common.run([adb, "-s", serial, "push", str(path), remote])
        remote_files.append(remote)
    common.run(
        [
            adb,
            "-s",
            serial,
            "push",
            str(out / "u0j-find_root_partition.sh"),
            "/tmp/a33-u0j-find_root_partition.sh",
        ]
    )
    common.run(
        [
            adb,
            "-s",
            serial,
            "push",
            str(local_test),
            "/tmp/a33-u0j-find-root-runtime-test.sh",
        ]
    )

    remote_script = r'''set -eu
chmod 755 /tmp/a33-u0j-busybox /tmp/a33-u0j-find-root-runtime-test.sh
[ ! -e /tmp/a33-u0j-busybox-extras ] || chmod 755 /tmp/a33-u0j-busybox-extras
rm -rf /tmp/a33-u0j-tools
mkdir -p /tmp/a33-u0j-tools
provider=""
for candidate in /tmp/a33-u0j-busybox /tmp/a33-u0j-busybox-extras; do
    [ -x "$candidate" ] || continue
    if "$candidate" --list | grep -qx blkid; then
        provider="$candidate"
        break
    fi
done
[ -n "$provider" ] || { echo blkid_provider=missing; exit 90; }
ln -s "$provider" /tmp/a33-u0j-tools/blkid
echo "blkid_provider=$provider"
/tmp/a33-u0j-busybox sh /tmp/a33-u0j-find-root-runtime-test.sh
'''
    output = common.adb_shell(adb, serial, remote_script)
    write_text(out / "exact-u0j-find-root-runtime.txt", output)
    if output.count("exact_u0j_dual_api_runtime=passed") != 1:
        refuse("exact embedded BusyBox dual-API runtime test failed")

    cleanup = "rm -rf /tmp/a33-u0j-tools /tmp/a33-u0j-* 2>/dev/null || true\n"
    common.adb_shell(adb, serial, cleanup)


def create_archive(out: Path, readback: Path) -> tuple[Path, str]:
    archive = Path(str(out) + ".tar.gz")

    def filter_member(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if Path(info.name).name == readback.name:
            return None
        return info

    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name, filter=filter_member)
    digest = sha_file(archive)
    write_text(Path(str(archive) + ".sha256"), f"{digest}  {archive}\n")
    return archive, digest


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Resume or create a non-destructive A33 rootfs handoff audit in exact TWRP"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--audit-dir", type=Path)
    parser.add_argument("--resume-latest", action="store_true")
    parser.add_argument("--refresh-readback", action="store_true")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    out = choose_audit_dir(root, args.audit_dir, args.resume_latest)
    commands = require_commands(
        ("file", "dumpe2fs", "tune2fs", "debugfs", "resize2fs", "e2fsck")
    )

    local = u0j.validate_local(root, repo)
    deploy = common.kv(Path(local["deploy_path"]))
    original = Path(deploy.get("deployment_image", ""))
    try:
        expected_size = int(deploy.get("deployment_size", ""))
    except ValueError:
        refuse("invalid deployment_size in deployment report")
    expected_sha = deploy.get("deployment_sha256", "")
    if (
        not original.is_file()
        or original.stat().st_size != expected_size
        or sha_file(original) != expected_sha
    ):
        refuse("original deployment image no longer matches its report")

    manifest = common.kv(Path(local["manifest_path"]))
    initramfs = Path(manifest.get("u0j_initramfs", ""))
    if (
        not initramfs.is_file()
        or sha_file(initramfs) != manifest.get("u0j_initramfs_sha256")
    ):
        refuse("U0j initramfs no longer matches its manifest")

    adb = shutil.which(args.adb) or args.adb
    serial = common.select_recovery(adb, 30)
    validate_twrp_state(adb, serial, local, out)

    readback, reused = choose_readback(out, expected_size)
    if args.refresh_readback and readback.exists():
        readback.unlink()
        reused = False
    if not reused:
        read_userdata_region(adb, serial, readback, expected_size, out)

    original_sha = sha_file(original)
    readback_sha = sha_file(readback)
    comparison = compare_files(original, readback)
    comparison_text = [
        f"original_image={original}",
        f"readback_image={readback}",
        f"original_sha256={original_sha}",
        f"readback_sha256={readback_sha}",
        f"original_and_readback_identical={'yes' if original_sha == readback_sha else 'no'}",
        f"readback_reused={'yes' if reused else 'no'}",
    ] + [f"{key}={value}" for key, value in comparison.items()]
    write_text(out / "rootfs-byte-comparison.txt", "\n".join(comparison_text) + "\n")

    host_e2fsck_rc = inspect_host_filesystem(commands, readback, out)
    inspect_twrp_filesystem(adb, serial, out)
    binaries = extract_initramfs_artifacts(initramfs, out)
    test_exact_busybox_runtime(adb, serial, binaries, out)

    summary = [
        ("created", datetime.now().astimezone().isoformat(timespec="microseconds")),
        ("operation", "nondestructive-a33-rootfs-handoff-audit"),
        ("candidate", "U0j-root-api-compatible"),
        ("phone_partition_writes", "no"),
        ("phone_volatile_tmp_writes", "yes"),
        ("audit_directory", out),
        ("original_image", original),
        ("readback_image", readback),
        ("readback_in_archive", "no"),
        ("original_sha256", original_sha),
        ("readback_sha256", readback_sha),
        ("host_e2fsck_rc", host_e2fsck_rc),
        ("exact_u0j_dual_api_runtime", "passed"),
        ("audit_status", "passed"),
    ]
    write_text(out / "summary.txt", "".join(f"{key}={value}\n" for key, value in summary))
    archive, archive_sha = create_archive(out, readback)

    for key, value in summary:
        print(f"{key}={value}")
    print(f"observation_archive={archive}")
    print(f"observation_archive_sha256={archive_sha}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Refusal, common.Refusal, CpioError, UnicodeDecodeError) as exc:
        print(f"REFUSING ROOTFS AUDIT: {exc}", file=sys.stderr)
        raise SystemExit(1)
