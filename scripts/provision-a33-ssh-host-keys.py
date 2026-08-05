#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
COMMON_PATH = HERE / "flash-a33-u0i-python-direct-root-v2.py"
EXPECTED_TWRP_SHA256 = "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_USERDATA = "/dev/block/sda36"
EXPECTED_USERDATA_BYTES = "114240258048"
EXPECTED_UUID = "7b056328-bdfb-496b-ac38-2624c43c863a"
EXPECTED_LABEL = "pmOS_root"
CONFIRMATION = "PROVISION-EXACT-SSH-HOST-KEYS"
DIAGNOSTIC_NAME = "a33-installed-ssh-keygen-tmpfs.json"

spec = importlib.util.spec_from_file_location("a33_ssh_key_provision_common", COMMON_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load A33 recovery helper: {COMMON_PATH}")
common = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = common
spec.loader.exec_module(common)


class ProvisionError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_diagnostic(root: Path) -> tuple[Path, dict[str, object]]:
    path = root / "build" / DIAGNOSTIC_NAME
    if not path.is_file():
        raise ProvisionError(f"missing successful volatile diagnostic: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProvisionError(f"invalid volatile diagnostic: {exc}") from exc
    expected = {
        "diagnosis": "volatile-keygen-and-sshd-config-validation-passed",
        "diagnostic_status": "passed",
        "twrp_recovery_sha256": EXPECTED_TWRP_SHA256,
        "userdata_resolved": EXPECTED_USERDATA,
        "userdata_filesystem_uuid": EXPECTED_UUID,
        "userdata_filesystem_label": EXPECTED_LABEL,
        "userdata_persistent_writes": "no",
        "phone_partition_writes": "no",
        "ssh_keygen_rc": "0",
        "ssh_keygen_timed_out": "no",
        "sshd_config_rc": "0",
        "sshd_config_timed_out": "no",
    }
    mismatches = [
        f"{key}: actual={data.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if data.get(key) != wanted
    ]
    if int(data.get("generated_private_key_count", 0)) < 3:
        mismatches.append("generated_private_key_count is below 3")
    if int(data.get("generated_public_key_count", 0)) < 3:
        mismatches.append("generated_public_key_count is below 3")
    raw = Path(str(data.get("raw_report", "")))
    expected_raw_sha = str(data.get("raw_report_sha256", ""))
    if not raw.is_file() or sha256_file(raw) != expected_raw_sha:
        mismatches.append("volatile diagnostic raw report is missing or changed")
    if mismatches:
        raise ProvisionError("volatile diagnostic contract failed:\n" + "\n".join(mismatches))
    return path, data


REMOTE_SCRIPT = r'''set -eu
mode="$1"
target="$2"
expected="$3"
root=/tmp/a33x-ssh-provision-root
overlay=/tmp/a33x-ssh-provision-overlay
work=/tmp/a33x-ssh-provision-work
staging_name=.a33x-hostkeys-staging
root_mounted=no
overlay_mounted=no
ssh_bind_mounted=no
dev_mounted=no
proc_mounted=no
sys_mounted=no
run_mounted=no
rw_phase=no
success=no
installed_names=""

cleanup_mounts() {
    [ "$run_mounted" = no ] || umount "$root/run" 2>/dev/null || true
    run_mounted=no
    [ "$sys_mounted" = no ] || umount "$root/sys" 2>/dev/null || true
    sys_mounted=no
    [ "$proc_mounted" = no ] || umount "$root/proc" 2>/dev/null || true
    proc_mounted=no
    [ "$dev_mounted" = no ] || umount "$root/dev" 2>/dev/null || true
    dev_mounted=no
    [ "$ssh_bind_mounted" = no ] || umount "$root/etc/ssh" 2>/dev/null || true
    ssh_bind_mounted=no
    [ "$root_mounted" = no ] || umount "$root" 2>/dev/null || true
    root_mounted=no
}

rollback_generated_keys() {
    [ "$rw_phase" = yes ] || return 0
    [ "$success" = no ] || return 0
    for name in $installed_names; do
        case "$name" in
            ssh_host_*_key|ssh_host_*_key.pub)
                rm -f "$root/etc/ssh/$name" 2>/dev/null || true
                ;;
        esac
    done
    rm -rf "$root/etc/ssh/$staging_name" 2>/dev/null || true
    sync 2>/dev/null || true
    echo "rollback_generated_keys=attempted"
}

cleanup() {
    rollback_generated_keys
    cleanup_mounts
    [ "$overlay_mounted" = no ] || umount "$overlay" 2>/dev/null || true
    overlay_mounted=no
    rm -rf "$root" "$overlay" "$work" 2>/dev/null || true
}
trap cleanup EXIT

resolved="$(readlink -f "$target" 2>/dev/null || true)"
echo "target=$target"
echo "target_resolved=$resolved"
[ "$resolved" = "$expected" ] || exit 20

for command in mount umount chroot cp find stat sha256sum cat grep awk sed kill sleep mkdir rm mv chmod chown sync readlink; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "missing_twrp_command=$command"
        exit 21
    }
done

rm -rf "$root" "$overlay" "$work"
mkdir -p "$root" "$overlay" "$work"
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$root"
root_mounted=yes
echo "readonly_preflight_mount=passed"

if find "$root/etc/ssh" -maxdepth 1 -type f \( -name 'ssh_host_*_key' -o -name 'ssh_host_*_key.pub' \) -print -quit 2>/dev/null | grep -q .; then
    echo "existing_host_keys=present"
    exit 22
fi
echo "existing_host_keys=none"

config_manifest="$work/config-before.txt"
: > "$config_manifest"
for config in "$root/etc/ssh/sshd_config" "$root"/etc/ssh/sshd_config.d/*.conf; do
    [ -f "$config" ] || continue
    relative="${config#$root}"
    echo "$relative $(sha256sum "$config" | awk '{print $1}')" >> "$config_manifest"
done
cat "$config_manifest" | sed 's/^/config_before=/'

if [ "$mode" = preflight ]; then
    success=yes
    cleanup_mounts
    echo "preflight_status=passed"
    echo "userdata_written=no"
    echo "phone_partition_writes=no"
    exit 0
fi
[ "$mode" = commit ] || exit 23

mount -t tmpfs -o mode=0755,size=16m tmpfs "$overlay"
overlay_mounted=yes
cp -a "$root/etc/ssh/." "$overlay/"
mount -o bind "$overlay" "$root/etc/ssh"
ssh_bind_mounted=yes
mount -o bind /dev "$root/dev"
dev_mounted=yes
mount -t proc proc "$root/proc"
proc_mounted=yes
mount -t sysfs sysfs "$root/sys"
sys_mounted=yes
mount -t tmpfs -o mode=0755,size=4m tmpfs "$root/run"
run_mounted=yes

echo "volatile_generation_overlay=passed"
chroot "$root" /usr/bin/ssh-keygen -A
chroot "$root" /usr/sbin/sshd -t -f /etc/ssh/sshd_config

generated_manifest="$work/generated.txt"
: > "$generated_manifest"
private_count=0
public_count=0
for file in "$overlay"/ssh_host_*_key "$overlay"/ssh_host_*_key.pub; do
    [ -f "$file" ] || continue
    name="${file##*/}"
    case "$name" in
        ssh_host_*_key)
            kind=private
            private_count=$((private_count + 1))
            ;;
        ssh_host_*_key.pub)
            kind=public
            public_count=$((public_count + 1))
            ;;
        *)
            echo "unsafe_generated_filename=$name"
            exit 24
            ;;
    esac
    sha="$(sha256sum "$file" | awk '{print $1}')"
    bytes="$(stat -c '%s' "$file")"
    echo "$name $kind $sha $bytes" >> "$generated_manifest"
done
[ "$private_count" -ge 3 ] && [ "$public_count" -eq "$private_count" ] || {
    echo "generated_key_count_invalid private=$private_count public=$public_count"
    exit 25
}
echo "generated_private_key_count=$private_count"
echo "generated_public_key_count=$public_count"
cat "$generated_manifest" | sed 's/^/generated_key=/'

cleanup_mounts
mount -t ext4 -o rw,nosuid,nodev,noatime "$target" "$root"
root_mounted=yes
rw_phase=yes
echo "writable_commit_mount=passed"

if find "$root/etc/ssh" -maxdepth 1 -type f \( -name 'ssh_host_*_key' -o -name 'ssh_host_*_key.pub' \) -print -quit 2>/dev/null | grep -q .; then
    echo "existing_host_keys_appeared=present"
    exit 26
fi
while read -r relative expected_sha; do
    [ -n "$relative" ] || continue
    actual_sha="$(sha256sum "$root$relative" 2>/dev/null | awk '{print $1}')"
    [ "$actual_sha" = "$expected_sha" ] || {
        echo "config_changed_before_commit=$relative actual=$actual_sha expected=$expected_sha"
        exit 27
    }
done < "$config_manifest"

staging="$root/etc/ssh/$staging_name"
[ ! -e "$staging" ] || exit 28
mkdir "$staging"
chmod 700 "$staging"
chown 0:0 "$staging"

while read -r name kind expected_sha bytes; do
    source="$overlay/$name"
    destination="$staging/$name"
    cp -p "$source" "$destination"
    chown 0:0 "$destination"
    if [ "$kind" = private ]; then
        chmod 600 "$destination"
    else
        chmod 644 "$destination"
    fi
    actual_sha="$(sha256sum "$destination" | awk '{print $1}')"
    [ "$actual_sha" = "$expected_sha" ] || {
        echo "staging_hash_mismatch=$name"
        exit 29
    }
done < "$generated_manifest"
sync

while read -r name kind expected_sha bytes; do
    [ ! -e "$root/etc/ssh/$name" ] || exit 30
    mv "$staging/$name" "$root/etc/ssh/$name"
    installed_names="$installed_names $name"
done < "$generated_manifest"
rmdir "$staging"
sync

mount -o bind /dev "$root/dev"
dev_mounted=yes
mount -t proc proc "$root/proc"
proc_mounted=yes
mount -t sysfs sysfs "$root/sys"
sys_mounted=yes
mount -t tmpfs -o mode=0755,size=4m tmpfs "$root/run"
run_mounted=yes
chroot "$root" /usr/sbin/sshd -t -f /etc/ssh/sshd_config

while read -r name kind expected_sha bytes; do
    file="$root/etc/ssh/$name"
    actual_sha="$(sha256sum "$file" | awk '{print $1}')"
    actual_mode="$(stat -c '%a' "$file")"
    actual_uid="$(stat -c '%u' "$file")"
    actual_gid="$(stat -c '%g' "$file")"
    [ "$actual_sha" = "$expected_sha" ] || exit 31
    [ "$actual_uid" = 0 ] && [ "$actual_gid" = 0 ] || exit 32
    if [ "$kind" = private ]; then
        [ "$actual_mode" = 600 ] || exit 33
    else
        [ "$actual_mode" = 644 ] || exit 34
    fi
    echo "installed_key name=$name kind=$kind sha256=$actual_sha bytes=$bytes mode=$actual_mode uid=$actual_uid gid=$actual_gid"
done < "$generated_manifest"
while read -r relative expected_sha; do
    [ -n "$relative" ] || continue
    actual_sha="$(sha256sum "$root$relative" | awk '{print $1}')"
    [ "$actual_sha" = "$expected_sha" ] || exit 35
    echo "config_after=$relative $actual_sha"
done < "$config_manifest"

sync
success=yes
cleanup_mounts
umount "$overlay"
overlay_mounted=no
rw_phase=no

echo "persistent_host_key_provision=passed"
echo "userdata_written=yes-etc-ssh-host-keys-only"
echo "recovery_written=no"
echo "boot_written=no"
echo "super_written=no"
echo "phone_partition_writes=yes-userdata-host-keys-only"
'''


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision validated SSH host keys into the installed A33 rootfs"
    )
    parser.add_argument("confirmation", nargs="?")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    diagnostic_path, diagnostic = load_diagnostic(root)

    if not args.preflight_only and args.confirmation != CONFIRMATION:
        raise ProvisionError(
            f"persistent userdata write requires exact confirmation: {CONFIRMATION}"
        )
    if args.preflight_only and args.confirmation is not None:
        raise ProvisionError("do not provide a confirmation token with --preflight-only")

    serial = common.select_recovery(adb, 30)
    values, sections = common.live_state(adb, serial)
    expected = {
        "recovery_sha": EXPECTED_TWRP_SHA256,
        "userdata_resolved": EXPECTED_USERDATA,
        "userdata_bytes": EXPECTED_USERDATA_BYTES,
        "userdata_readonly": "0",
    }
    mismatches = [
        f"{key}: actual={values.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if values.get(key) != wanted
    ]
    for name in ("mount_users", "swap_users", "dm_users"):
        if sections.get(name):
            mismatches.append(f"{name}: active={sections[name]!r}")
    uuid_value, label = common.ext4_identity(adb, serial)
    if uuid_value != EXPECTED_UUID:
        mismatches.append(f"filesystem_uuid: actual={uuid_value!r} expected={EXPECTED_UUID!r}")
    if label != EXPECTED_LABEL:
        mismatches.append(f"filesystem_label: actual={label!r} expected={EXPECTED_LABEL!r}")
    if mismatches:
        raise ProvisionError("unsafe TWRP/userdata state:\n" + "\n".join(mismatches))

    mode = "preflight" if args.preflight_only else "commit"
    completed = common.run(
        [
            adb,
            "-s",
            serial,
            "shell",
            "sh",
            "-s",
            "--",
            mode,
            common.USERDATA,
            EXPECTED_USERDATA,
        ],
        input_data=REMOTE_SCRIPT,
        check=False,
        timeout=180,
    )
    output = completed.stdout.replace("\r", "")
    stderr = completed.stderr.replace("\r", "")
    if completed.returncode != 0:
        raise ProvisionError(
            f"SSH host-key provisioning failed rc={completed.returncode}:\n{output}\n{stderr}"
        )

    if args.preflight_only:
        required = (
            "readonly_preflight_mount=passed",
            "existing_host_keys=none",
            "preflight_status=passed",
            "userdata_written=no",
            "phone_partition_writes=no",
        )
    else:
        required = (
            "volatile_generation_overlay=passed",
            "writable_commit_mount=passed",
            "persistent_host_key_provision=passed",
            "userdata_written=yes-etc-ssh-host-keys-only",
            "recovery_written=no",
            "boot_written=no",
            "super_written=no",
            "phone_partition_writes=yes-userdata-host-keys-only",
        )
    for marker in required:
        if marker not in output:
            raise ProvisionError(f"missing provisioning safety marker: {marker}")

    report = root / "build/a33-ssh-host-key-provision.txt"
    rows = [
        ("created", datetime.now().astimezone().isoformat(timespec="microseconds")),
        ("operation", "provision-a33-ssh-host-keys"),
        ("implementation_language", "python3"),
        ("mode", mode),
        ("adb_serial", serial),
        ("twrp_recovery_sha256", values["recovery_sha"]),
        ("userdata_resolved", values["userdata_resolved"]),
        ("userdata_filesystem_uuid", uuid_value),
        ("userdata_filesystem_label", label),
        ("diagnostic", diagnostic_path),
        ("diagnostic_sha256", sha256_file(diagnostic_path)),
        ("diagnosis", diagnostic["diagnosis"]),
        ("persistent_scope", "none" if args.preflight_only else "/etc/ssh/ssh_host_*_key* only"),
        ("recovery_written", "no"),
        ("boot_written", "no"),
        ("super_written", "no"),
        ("userdata_written", "no" if args.preflight_only else "yes-host-keys-only"),
        ("status", "passed"),
    ]
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "".join(f"{key}={value}\n" for key, value in rows)
        + "\n=== device output ===\n"
        + output
        + ("\n=== stderr ===\n" + stderr if stderr else ""),
        encoding="utf-8",
    )
    print(output, end="" if output.endswith("\n") else "\n")
    print(f"report={report}")
    print(f"report_sha256={sha256_file(report)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProvisionError, common.Refusal, OSError, ValueError) as exc:
        print(f"SSH HOST-KEY PROVISION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
