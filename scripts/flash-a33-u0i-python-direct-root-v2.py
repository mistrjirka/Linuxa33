#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

KNOWN_TWRP_SHA256 = "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_USERDATA = "/dev/block/sda36"
EXPECTED_USERDATA_BYTES = 114240258048
USERDATA = "/dev/block/by-name/userdata"
RECOVERY = "/dev/block/by-name/recovery"
REMOTE_CANDIDATE = "/tmp/a33x-u0i-python-direct-root-v2-recovery.img"
EXPECTED_CANDIDATE = "U0i-python-direct-root-v2"
CRITICAL_PATHS = (
    "/bin/busybox",
    "/usr/sbin/sshd",
    "/etc/init.d/sshd",
    "/etc/init.d/networkmanager",
    "/usr/libexec/a33x-muic-switch-dynamic",
    "/etc/fstab",
    "/etc/a33x-rootfs-target",
)


class Refusal(RuntimeError):
    pass


def refuse(message: str) -> None:
    raise Refusal(message)


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def kv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result.setdefault(key, value)
    return result


def require(values: dict[str, str], expected: dict[str, str], label: str) -> None:
    mismatches = [
        f"{key}: actual={values.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if values.get(key) != wanted
    ]
    if mismatches:
        refuse(label + " contract failed:\n" + "\n".join(mismatches))


def run(
    args: list[str],
    *,
    input_data: str | bytes | None = None,
    text: bool = True,
    timeout: float | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        args,
        input=input_data,
        text=text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        stdout = completed.stdout if isinstance(completed.stdout, str) else completed.stdout.decode(errors="replace")
        stderr = completed.stderr if isinstance(completed.stderr, str) else completed.stderr.decode(errors="replace")
        refuse(
            f"command failed rc={completed.returncode}: {args!r}\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        )
    return completed


def git_commit_available(repo: Path, commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        refuse(f"invalid manifest commit: {commit!r}")
    run(["git", "-C", str(repo), "cat-file", "-e", f"{commit}^{{commit}}"])
    head = run(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()
    ancestor = run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, head],
        check=False,
    )
    if ancestor.returncode != 0:
        refuse(f"manifest commit {commit} is not an ancestor of current HEAD {head}")


def validate_local(root: Path, repo: Path) -> dict[str, object]:
    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0i-python-direct-root-v2-manifest.txt"
    deploy_path = root / "build/a33-userdata-rootfs-deployment.txt"
    u0h_report_path = root / "build/u0h-userdata-root-node.txt"
    for path in (manifest_path, deploy_path, u0h_report_path):
        if not path.is_file():
            refuse(f"missing required input: {path}")

    manifest = kv(manifest_path)
    require(
        manifest,
        {
            "candidate": EXPECTED_CANDIDATE,
            "implementation_language": "python3",
            "functional_base": "U0h-userdata-root-node",
            "functional_delta": "replace-find-and-wait-root-functions-only",
            "kernel_cmdline_delta": "none",
            "module_delta": "none",
            "forced_root": EXPECTED_USERDATA,
            "cpio_entry_order_preserved": "yes",
            "cpio_metadata_preserved_except_target_size_and_crc": "yes",
            "cpio_payload_delta": "init_functions.sh",
            "shell_delta": "find_root_partition,wait_root_partition",
            "shell_text_outside_two_functions_preserved": "yes",
            "embedded_modules": "67",
            "patched_wait_directly_consumes_patched_find": "yes",
            "direct_root_identity_recheck": "yes",
            "second_stage_order_validation": "passed",
            "preparation_status": "passed",
            "phone_partition_writes": "no",
            "build_status": "passed",
        },
        "U0i manifest",
    )
    git_commit_available(repo, manifest.get("linuxa33_commit", ""))

    candidate = Path(manifest.get("recovery", ""))
    candidate_sha = manifest.get("recovery_sha256", "")
    try:
        candidate_size = int(manifest.get("recovery_size", ""))
    except ValueError:
        refuse("invalid recovery_size in U0i manifest")
    if candidate_size != 100663296 or not re.fullmatch(r"[0-9a-f]{64}", candidate_sha):
        refuse("invalid U0i recovery size or hash contract")
    if not candidate.is_file() or candidate.stat().st_size != candidate_size or sha_file(candidate) != candidate_sha:
        refuse("U0i candidate differs from its manifest")

    patch_report = Path(manifest.get("patch_report", ""))
    patch_sha = manifest.get("patch_report_sha256", "")
    if not patch_report.is_file() or sha_file(patch_report) != patch_sha:
        refuse("U0i patch report differs from its manifest")
    patch = kv(patch_report)
    require(
        patch,
        {
            "operation": "python-byte-preserving-replace-two-root-functions",
            "implementation_language": "python3",
            "cpio_entry_order_preserved": "yes",
            "cpio_metadata_preserved_except_target_size_and_crc": "yes",
            "cpio_payload_delta": "init_functions.sh",
            "shell_delta": "find_root_partition,wait_root_partition",
            "shell_text_outside_two_functions_preserved": "yes",
            "patched_wait_directly_consumes_patched_find": "yes",
            "direct_root_identity_recheck": "yes",
            "forced_root": EXPECTED_USERDATA,
            "embedded_modules": "67",
            "patch_status": "passed",
            "phone_partition_writes": "no",
            "second_stage_order_validation": "passed",
        },
        "U0i patch report",
    )
    if patch.get("u0i_initramfs_sha256") != manifest.get("u0i_initramfs_sha256"):
        refuse("U0i patch report and manifest disagree on initramfs hash")

    u0h = kv(u0h_report_path)
    require(
        u0h,
        {
            "preparation_status": "passed",
            "embedded_modules": "67",
            "phone_partition_writes": "no",
        },
        "U0h report",
    )
    u0h_image = root / "export-u0h-root-node/initramfs"
    if not u0h_image.is_file() or sha_file(u0h_image) != manifest.get("u0h_initramfs_sha256"):
        refuse("local U0h initramfs does not match U0i ancestry")

    deploy = kv(deploy_path)
    require(
        deploy,
        {
            "deployment_status": "passed",
            "filesystem_type": "ext4",
            "filesystem_label": "pmOS_root",
            "cache_written": "no",
            "super_written": "no",
            "boot_written": "no",
            "recovery_written": "no",
        },
        "userdata deployment",
    )
    root_uuid = deploy.get("filesystem_uuid", "")
    try:
        uuid.UUID(root_uuid)
    except ValueError:
        refuse(f"invalid deployed filesystem UUID: {root_uuid!r}")
    image = Path(deploy.get("deployment_image", ""))
    image_sha = deploy.get("deployment_sha256", "")
    try:
        image_size = int(deploy.get("deployment_size", ""))
    except ValueError:
        refuse("invalid deployment image size")
    if not image.is_file() or image.stat().st_size != image_size or sha_file(image) != image_sha:
        refuse("local deployment image no longer matches its report")

    debugfs = shutil.which("debugfs")
    if not debugfs:
        refuse("debugfs is required")
    critical: dict[str, str] = {}
    for path in CRITICAL_PATHS:
        completed = run([debugfs, "-R", f"cat {path}", str(image)], text=False)
        payload = completed.stdout
        if not payload:
            refuse(f"critical path is empty in deployment image: {path}")
        critical[path] = hashlib.sha256(payload).hexdigest()
    critical_manifest = "".join(f"{critical[path]} {path}\n" for path in sorted(critical))

    return {
        "manifest_path": manifest_path,
        "candidate": candidate,
        "candidate_sha": candidate_sha,
        "candidate_size": candidate_size,
        "deploy_path": deploy_path,
        "root_uuid": root_uuid,
        "critical": critical,
        "critical_manifest_sha": hashlib.sha256(critical_manifest.encode()).hexdigest(),
    }


def parse_adb_devices(output: str) -> list[tuple[str, str]]:
    in_table = False
    rows: list[tuple[str, str]] = []
    for raw in output.replace("\r", "").splitlines():
        if raw == "List of devices attached":
            in_table = True
            continue
        if in_table and raw.strip():
            fields = raw.split()
            if len(fields) >= 2:
                rows.append((fields[0], fields[1]))
    return rows


def select_recovery(adb: str, timeout_seconds: int) -> str:
    deadline = time.monotonic() + timeout_seconds
    last = ""
    while time.monotonic() < deadline:
        completed = run([adb, "devices", "-l"], check=False)
        last = completed.stdout + completed.stderr
        rows = parse_adb_devices(last)
        if len(rows) > 1:
            refuse("multiple ADB transports are attached:\n" + last)
        if len(rows) == 1:
            serial, state = rows[0]
            if state == "unauthorized":
                refuse("ADB transport is unauthorized")
            if state not in ("offline", "recovery"):
                refuse(f"ADB transport is in state {state!r}, expected recovery")
            if state == "recovery":
                probe = run([adb, "-s", serial, "shell", "echo ADB_OK"], timeout=5, check=False)
                if probe.returncode == 0 and probe.stdout.replace("\r", "").strip() == "ADB_OK":
                    if run([adb, "-s", serial, "get-state"]).stdout.replace("\r", "").strip() != "recovery":
                        refuse("selected ADB transport stopped reporting recovery")
                    return serial
        time.sleep(1)
    refuse(f"one responsive recovery transport did not appear within {timeout_seconds}s:\n{last}")


def adb_shell(adb: str, serial: str, script: str, *args: str) -> str:
    completed = run([adb, "-s", serial, "shell", "sh", "-s", "--", *args], input_data=script)
    return completed.stdout.replace("\r", "")


def parse_sections(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    values: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    active: str | None = None
    for line in text.splitlines():
        if line.endswith("_begin"):
            active = line[:-6]
            sections.setdefault(active, [])
        elif line.endswith("_end") and active == line[:-4]:
            active = None
        elif active is not None:
            if line:
                sections[active].append(line)
        elif "=" in line:
            key, value = line.split("=", 1)
            values.setdefault(key, value)
    return values, sections


LIVE_SCRIPT = r'''set -u
target="$1"
resolved="$(readlink -f "$target" 2>/dev/null || true)"
echo "recovery_sha=$(sha256sum /dev/block/by-name/recovery 2>/dev/null | awk 'NR==1 {print $1}')"
echo "userdata_resolved=$resolved"
echo "userdata_bytes=$(blockdev --getsize64 "$target" 2>/dev/null || true)"
echo "userdata_readonly=$(blockdev --getro "$target" 2>/dev/null || true)"
echo "mount_users_begin"
awk '{print $1, $2}' /proc/mounts 2>/dev/null | while read -r source mountpoint; do
    source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
    if [ "$source" = "$target" ] || [ "$source" = "$resolved" ] || [ "$source_resolved" = "$resolved" ]; then
        echo "$mountpoint"
    fi
done
echo "mount_users_end"
echo "swap_users_begin"
if [ -r /proc/swaps ]; then
    tail -n +2 /proc/swaps 2>/dev/null | while read -r source rest; do
        source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
        if [ "$source" = "$target" ] || [ "$source" = "$resolved" ] || [ "$source_resolved" = "$resolved" ]; then
            echo "$source"
        fi
    done
fi
echo "swap_users_end"
echo "dm_users_begin"
for dm in /sys/block/dm-*; do
    [ -e "$dm" ] || continue
    if find "$dm/slaves" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | grep -qx "${resolved##*/}"; then
        echo "${dm##*/}:$(cat "$dm/dm/name" 2>/dev/null || true)"
    fi
done
echo "dm_users_end"
'''


def live_state(adb: str, serial: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    return parse_sections(adb_shell(adb, serial, LIVE_SCRIPT, USERDATA))


def ext4_identity(adb: str, serial: str) -> tuple[str, str]:
    completed = run(
        [adb, "-s", serial, "exec-out", "sh", "-c", f"dd if='{USERDATA}' bs=2048 count=1 2>/dev/null"],
        text=False,
    )
    data = completed.stdout
    if len(data) != 2048:
        refuse(f"expected 2048 superblock bytes, received {len(data)}")
    superblock = data[1024:2048]
    if superblock[56:58] != b"\x53\xef":
        refuse("ext4 superblock magic mismatch")
    label_raw = superblock[120:136].split(b"\0", 1)[0]
    if any(byte < 32 or byte > 126 for byte in label_raw):
        refuse("filesystem label contains unsupported bytes")
    return str(uuid.UUID(bytes=bytes(superblock[104:120]))), label_raw.decode("ascii")


VERIFY_SCRIPT = r'''set -eu
target="$1"
expected_uuid="$2"
shift 2
mountpoint=/tmp/a33x-u0i-root-verify
mounted=no
cleanup() { [ "$mounted" = no ] || umount "$mountpoint" 2>/dev/null || true; }
trap cleanup EXIT
mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"
mounted=yes
for path in "$@"; do
    [ -f "$mountpoint$path" ] || { echo "critical_missing=$path"; exit 20; }
    echo "critical_sha=$(sha256sum "$mountpoint$path" | awk '{print $1}') path=$path"
done
for path in /sbin/init /etc/os-release; do
    full="$mountpoint$path"
    if [ -e "$full" ]; then :
    elif [ -L "$full" ]; then
        link="$(readlink "$full")"
        case "$link" in /*) rooted="$mountpoint$link" ;; *) parent="${path%/*}"; rooted="$mountpoint$parent/$link" ;; esac
        [ -e "$rooted" ] || [ -L "$rooted" ] || { echo "root_symlink_target_missing=$path target=$link"; exit 21; }
    else echo "root_path_missing=$path"; exit 22
    fi
done
for pair in /etc/runlevels/default/sshd:/etc/init.d/sshd /etc/runlevels/default/networkmanager:/etc/init.d/networkmanager; do
    path="${pair%%:*}"; expected="${pair#*:}"
    [ -L "$mountpoint$path" ] || exit 23
    [ "$(readlink "$mountpoint$path")" = "$expected" ] || exit 24
    [ -e "$mountpoint$expected" ] || [ -L "$mountpoint$expected" ] || exit 25
done
active="$(grep -Ev '^[[:space:]]*(#|$)' "$mountpoint/etc/fstab" || true)"
[ "$active" = "UUID=$expected_uuid / ext4 defaults 0 1" ] || { echo "fstab_active=$active"; exit 26; }
grep -Fqx "root_uuid=$expected_uuid" "$mountpoint/etc/a33x-rootfs-target" || exit 27
grep -Fqx 'target=android-userdata' "$mountpoint/etc/a33x-rootfs-target" || exit 28
umount "$mountpoint"
mounted=no
echo readonly_verification=passed
echo readonly_unmount=passed
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Flash exact U0i v2 after full read-only rootfs validation")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default=os.environ.get("ADB", "adb"))
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()
    local = validate_local(root, repo)
    print("local_artifact_preflight=passed")
    print(f"candidate_sha256={local['candidate_sha']}")
    print("phone_partition_writes=no")
    if args.preflight_only:
        return 0

    adb = shutil.which(args.adb) or args.adb
    serial = select_recovery(adb, 30)
    print(f"adb_serial={serial}")
    values, sections = live_state(adb, serial)
    if (
        values.get("recovery_sha") != KNOWN_TWRP_SHA256
        or values.get("userdata_resolved") != EXPECTED_USERDATA
        or values.get("userdata_bytes") != str(EXPECTED_USERDATA_BYTES)
        or values.get("userdata_readonly") != "0"
        or sections.get("swap_users")
        or sections.get("dm_users")
    ):
        refuse(f"TWRP or userdata state is unsafe: values={values} sections={sections}")

    for _ in range(3):
        mounts = sections.get("mount_users", [])
        if not mounts:
            break
        for mountpoint in sorted(mounts, key=lambda value: (value.count("/"), len(value)), reverse=True):
            adb_shell(adb, serial, 'umount "$1" 2>/dev/null || true\n', mountpoint)
        values, sections = live_state(adb, serial)
    if sections.get("mount_users") or sections.get("swap_users") or sections.get("dm_users"):
        refuse(f"userdata remains in use: values={values} sections={sections}")

    actual_uuid, actual_label = ext4_identity(adb, serial)
    if actual_uuid != local["root_uuid"] or actual_label != "pmOS_root":
        refuse(f"installed rootfs identity mismatch: uuid={actual_uuid} label={actual_label}")

    output = adb_shell(adb, serial, VERIFY_SCRIPT, USERDATA, str(local["root_uuid"]), *CRITICAL_PATHS)
    print(output, end="")
    actual: dict[str, str] = {}
    for line in output.splitlines():
        match = re.fullmatch(r"critical_sha=([0-9a-f]{64}) path=(/.+)", line)
        if match:
            actual[match.group(2)] = match.group(1)
    if actual != local["critical"]:
        refuse(f"installed rootfs critical content mismatch: expected={local['critical']} actual={actual}")
    if output.count("readonly_verification=passed") != 1 or output.count("readonly_unmount=passed") != 1:
        refuse("read-only rootfs verification did not finish cleanly")
    _, sections = live_state(adb, serial)
    if sections.get("mount_users") or sections.get("swap_users") or sections.get("dm_users"):
        refuse("userdata became active after verification")

    candidate = local["candidate"]
    run([adb, "-s", serial, "push", str(candidate), REMOTE_CANDIDATE])
    remote = adb_shell(
        adb,
        serial,
        'set -eu\nstat -c "%s" "$1"\nsha256sum "$1"\n',
        REMOTE_CANDIDATE,
    ).splitlines()
    if len(remote) < 2 or remote[0] != str(local["candidate_size"]) or remote[1].split()[0] != local["candidate_sha"]:
        adb_shell(adb, serial, 'rm -f "$1"\n', REMOTE_CANDIDATE)
        refuse(f"uploaded candidate identity mismatch: {remote}")

    adb_shell(
        adb,
        serial,
        'set -eu\ndd if="$1" of="$2" bs=4194304\nsync\n',
        REMOTE_CANDIDATE,
        RECOVERY,
    )
    recovery_sha = adb_shell(adb, serial, 'sha256sum "$1"\n', RECOVERY).split()[0]
    adb_shell(adb, serial, 'rm -f "$1"\n', REMOTE_CANDIDATE)
    if recovery_sha != local["candidate_sha"]:
        refuse(f"recovery readback mismatch: expected={local['candidate_sha']} actual={recovery_sha}")

    report = root / "build/a33-first-rootfs-u0i-python-direct-root-v2-flash.txt"
    created = run(["date", "-Ins"]).stdout.strip()
    pairs = [
        ("created", created),
        ("operation", "flash-exact-u0i-python-direct-root-v2"),
        ("implementation_language", "python3"),
        ("deployment_report", local["deploy_path"]),
        ("deployment_report_sha256", sha_file(local["deploy_path"])),
        ("userdata_validation", "identity-and-critical-content-passed"),
        ("userdata_filesystem_uuid", local["root_uuid"]),
        ("userdata_critical_manifest_sha256", local["critical_manifest_sha"]),
        ("candidate_manifest", local["manifest_path"]),
        ("candidate_manifest_sha256", sha_file(local["manifest_path"])),
        ("candidate", candidate),
        ("candidate_size", local["candidate_size"]),
        ("candidate_sha256", local["candidate_sha"]),
        ("recovery_target", RECOVERY),
        ("recovery_partition_sha256", recovery_sha),
        ("userdata_written", "no"),
        ("cache_written", "no"),
        ("super_written", "no"),
        ("boot_written", "no"),
        ("recovery_written", "yes"),
        ("reboot_performed", "no"),
        ("flash_status", "passed"),
    ]
    report.write_text("".join(f"{key}={value}\n" for key, value in pairs), encoding="utf-8")
    for key, value in pairs:
        print(f"{key}={value}")
    print(f"\nExact U0i v2 recovery flashed and verified.\nReport: {report}\nPhone remains in TWRP.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refusal as exc:
        print(f"REFUSING U0i flash: {exc}", file=sys.stderr)
        raise SystemExit(1)
