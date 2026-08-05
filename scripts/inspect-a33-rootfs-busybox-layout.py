#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent
COMMON_PATH = HERE / "flash-a33-u0i-python-direct-root-v2.py"
BLOCK_HELPER_PATH = HERE / "lib/a33_exact_block_node.py"
IDENTITY_HELPER_PATH = HERE / "lib/a33_ext4_identity_text.py"
EXPECTED_COMMON_BLOB = "84b47e4f75d2d9622e1fd081000f1e387d7dd6cd"
EXPECTED_BLOCK_HELPER_BLOB = "2232f92bbf2782aed88acd9246ed063148ca63a8"
EXPECTED_IDENTITY_HELPER_BLOB = "547aa185c56cfdefe09efab2ba1fbe1e63950de0"
EXPECTED_UUID = "7b056328-bdfb-496b-ac38-2624c43c863a"
EXPECTED_LABEL = "pmOS_root"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


common = load("a33_busybox_layout_common", COMMON_PATH)
block_helper = load("a33_busybox_layout_block", BLOCK_HELPER_PATH)
identity_helper = load("a33_busybox_layout_identity", IDENTITY_HELPER_PATH)


class LayoutError(RuntimeError):
    pass


REMOTE_SCRIPT = r'''set -eu
target="$1"
mountpoint=/tmp/a33x-busybox-layout
mounted=no
cleanup()
{
    [ "$mounted" = no ] || umount "$mountpoint" 2>/dev/null || true
}
trap cleanup EXIT

for command in mount umount mkdir rm ls stat readlink sha256sum find sed sort; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "missing_command=$command"
        exit 70
    }
done

mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"
mounted=yes
echo "readonly_mount=passed"

inspect_path()
{
    relative="$1"
    full="$mountpoint$relative"
    if [ -L "$full" ]; then
        target_value="$(readlink "$full" 2>/dev/null || true)"
        echo "layout path=$relative type=symlink target=$target_value"
    elif [ -f "$full" ]; then
        echo "layout path=$relative type=file mode=$(stat -c '%a' "$full" 2>/dev/null || true) uid=$(stat -c '%u' "$full" 2>/dev/null || true) gid=$(stat -c '%g' "$full" 2>/dev/null || true) bytes=$(stat -c '%s' "$full" 2>/dev/null || true) inode=$(stat -c '%i' "$full" 2>/dev/null || true) links=$(stat -c '%h' "$full" 2>/dev/null || true) sha256=$(sha256sum "$full" 2>/dev/null | sed 's/[[:space:]].*$//')"
    elif [ -d "$full" ]; then
        echo "layout path=$relative type=directory mode=$(stat -c '%a' "$full" 2>/dev/null || true) uid=$(stat -c '%u' "$full" 2>/dev/null || true) gid=$(stat -c '%g' "$full" 2>/dev/null || true) inode=$(stat -c '%i' "$full" 2>/dev/null || true)"
    elif [ -e "$full" ]; then
        echo "layout path=$relative type=other mode=$(stat -c '%f' "$full" 2>/dev/null || true) inode=$(stat -c '%i' "$full" 2>/dev/null || true)"
    else
        echo "layout path=$relative type=missing"
    fi
}

for relative in \
    /bin \
    /bin/busybox \
    /bin/sh \
    /usr \
    /usr/bin \
    /usr/bin/busybox \
    /usr/bin/sh \
    /sbin \
    /sbin/busybox \
    /usr/sbin \
    /usr/sbin/busybox \
    /busybox; do
    inspect_path "$relative"
done

echo "root_listing_begin"
ls -la "$mountpoint" 2>&1 || true
echo "root_listing_end"

echo "bin_listing_begin"
ls -la "$mountpoint/bin" 2>&1 || true
echo "bin_listing_end"

echo "usr_bin_listing_begin"
ls -la "$mountpoint/usr/bin" 2>&1 || true
echo "usr_bin_listing_end"

echo "busybox_find_begin"
find "$mountpoint" -xdev -maxdepth 5 \( -name busybox -o -name 'busybox.*' \) -print 2>/dev/null |
    sed "s#^$mountpoint##" |
    sort || true
echo "busybox_find_end"

umount "$mountpoint"
mounted=no
echo "readonly_unmount=passed"
echo "userdata_persistent_writes=no"
echo "phone_partition_writes=no"
echo "phone_reboot_performed=no"
'''


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        check=False,
    ).stdout.strip()


def parse_layout(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if not line.startswith("layout path="):
            continue
        fields = line.split()
        row: dict[str, str] = {}
        for field in fields[1:]:
            if "=" in field:
                key, value = field.split("=", 1)
                row[key] = value
        path = row.pop("path", "")
        if path:
            result[path] = row
    return result


def section(text: str, name: str) -> list[str]:
    begin = f"{name}_begin\n"
    end = f"{name}_end\n"
    if text.count(begin) != 1 or text.count(end) != 1:
        return []
    return [line for line in text.split(begin, 1)[1].split(end, 1)[0].splitlines() if line]


def diagnose(layout: dict[str, dict[str, str]], found: list[str]) -> str:
    bin_busybox = layout.get("/bin/busybox", {}).get("type", "missing")
    usr_busybox = layout.get("/usr/bin/busybox", {}).get("type", "missing")
    bin_type = layout.get("/bin", {}).get("type", "missing")
    if bin_busybox == "file":
        return "bin-busybox-present-verifier-runtime-mismatch"
    if usr_busybox == "file" and bin_type == "symlink":
        return "usr-bin-busybox-present-bin-symlink-resolution-mismatch"
    if usr_busybox == "file":
        return "busybox-present-only-under-usr-bin"
    if found:
        return "busybox-present-at-unexpected-path"
    return "busybox-not-found-in-mounted-rootfs"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the mounted A33 rootfs BusyBox/bin layout read-only"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb

    for path, expected in (
        (COMMON_PATH, EXPECTED_COMMON_BLOB),
        (BLOCK_HELPER_PATH, EXPECTED_BLOCK_HELPER_BLOB),
        (IDENTITY_HELPER_PATH, EXPECTED_IDENTITY_HELPER_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise LayoutError(
                f"checked-in dependency changed: {path.name} actual={actual!r} expected={expected!r}"
            )

    common.USERDATA = block_helper.EXACT_NODE
    serial = common.select_recovery(adb, 30)
    state = block_helper.prepare(common, adb, serial)
    print("exact_block_node_preparation=passed")
    print(f"exact_block_node_created={'yes' if state.created else 'no'}")
    print("ephemeral_device_node_write=/dev-tmpfs-only")
    try:
        uuid_value, label = identity_helper.ext4_identity(common, adb, serial)
        if uuid_value != EXPECTED_UUID or label != EXPECTED_LABEL:
            raise LayoutError(
                f"rootfs identity mismatch: uuid={uuid_value!r} label={label!r}"
            )
        completed = common.run(
            [
                adb,
                "-s",
                serial,
                "shell",
                "sh",
                "-s",
                "--",
                block_helper.EXACT_NODE,
            ],
            input_data=REMOTE_SCRIPT,
            check=False,
            timeout=90,
        )
        output = completed.stdout.replace("\r", "")
        stderr = completed.stderr.replace("\r", "")
        if completed.returncode != 0:
            raise LayoutError(
                f"BusyBox layout inspection failed rc={completed.returncode}:\n{output}\n{stderr}"
            )
        if output.count("readonly_mount=passed") != 1 or output.count(
            "readonly_unmount=passed"
        ) != 1:
            raise LayoutError("read-only mount lifecycle did not complete exactly once")

        layout = parse_layout(output)
        found = section(output, "busybox_find")
        diagnosis = diagnose(layout, found)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = root / "build/runtime-results" / f"a33-rootfs-busybox-layout-{timestamp}"
        out.mkdir(parents=True, exist_ok=False)
        raw = out / "layout.txt"
        raw.write_text(
            output + ("\n=== stderr ===\n" + stderr if stderr else ""),
            encoding="utf-8",
        )
        summary = {
            "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
            "operation": "inspect-a33-rootfs-busybox-layout-read-only",
            "implementation_language": "python3",
            "adb_serial": serial,
            "diagnostic_status": "passed",
            "diagnosis": diagnosis,
            "userdata_target": block_helper.EXACT_NODE,
            "userdata_filesystem_uuid": uuid_value,
            "userdata_filesystem_label": label,
            "layout": layout,
            "busybox_candidates": found,
            "userdata_persistent_writes": "no",
            "phone_partition_writes": "no",
            "phone_reboot_performed": "no",
            "raw_report": str(raw),
            "raw_report_sha256": sha256_file(raw),
        }
        summary_path = out / "summary.json"
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        archive = out.with_suffix(".tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(out, arcname=out.name)

        print(json.dumps(summary, indent=2, sort_keys=True))
        print(f"diagnostic_directory={out}")
        print(f"diagnostic_archive={archive}")
        print(f"diagnostic_archive_sha256={sha256_file(archive)}")
        return 0
    finally:
        cleanup_output = block_helper.cleanup(common, adb, serial, state)
        if cleanup_output.count("exact_block_node_cleanup_status=passed") != 1:
            raise block_helper.ExactBlockNodeError(
                "exact block-node cleanup did not pass exactly once"
            )
        print("exact_block_node_cleanup=passed")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        LayoutError,
        common.Refusal,
        block_helper.ExactBlockNodeError,
        identity_helper.Ext4IdentityError,
        OSError,
        ValueError,
    ) as exc:
        print(f"A33 ROOTFS BUSYBOX LAYOUT INSPECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
