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
import tarfile

HERE = Path(__file__).resolve().parent
RESTORE_PATH = HERE / "restore-a33-rootfs-after-unsafe-openrc-diagnostic.py"
EXPECTED_RESTORE_BLOB = "baf157a20617ec70fff8e79381055b34d77b0de8"
CONFIRMATION = "PROVISION-SAFE-A33-SSH-HOST-KEYS"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


restore = load("a33_safe_ssh_restore", RESTORE_PATH)
common = restore.common
block_helper = restore.block_helper
cleanup = restore.cleanup
verify_helper = restore.verify_helper


class ProvisionV3Error(RuntimeError):
    pass


REMOTE_SCRIPT = r'''set -eu
mode="$1"
target="$2"
expected_uuid="$3"
root=/tmp/a33x-safe-ssh-root
overlay=/tmp/a33x-safe-ssh-overlay
work=/tmp/a33x-safe-ssh-work
staging_name=.a33x-safe-hostkeys-staging
success=no
commit_started=no
installed_names=""
root_mounted=no
ssh_bind_mounted=no
dev_mounted=no
proc_mounted=no
sys_mounted=no
run_mounted=no

is_mounted()
{
    point="$1"
    awk -v point="$point" '$2 == point { found=1 } END { exit found ? 0 : 1 }' /proc/mounts
}

mounts_under()
{
    prefix="$1"
    awk -v prefix="$prefix" '$2 == prefix || index($2, prefix "/") == 1 { print }' /proc/mounts
}

unmount_exact()
{
    point="$1"
    if is_mounted "$point"; then
        echo "unmount_attempt=$point"
        umount "$point" || return 1
    fi
    if is_mounted "$point"; then
        echo "unmount_still_active=$point"
        return 1
    fi
    echo "unmount_verified=$point"
    return 0
}

rollback_exact_keys()
{
    [ "$commit_started" = yes ] || return 0
    [ "$success" = no ] || return 0
    [ "$root_mounted" = yes ] || return 0
    for name in $installed_names; do
        case "$name" in
            ssh_host_*_key|ssh_host_*_key.pub)
                rm -f "$root/etc/ssh/$name" 2>/dev/null || true
                ;;
        esac
    done
    rm -rf "$root/etc/ssh/$staging_name" 2>/dev/null || true
    sync 2>/dev/null || true
    echo "rollback_exact_keys=attempted"
}

cleanup_mounts()
{
    failed=no
    unmount_exact "$root/run" || failed=yes
    run_mounted=no
    unmount_exact "$root/sys" || failed=yes
    sys_mounted=no
    unmount_exact "$root/proc" || failed=yes
    proc_mounted=no
    unmount_exact "$root/dev" || failed=yes
    dev_mounted=no
    unmount_exact "$root/etc/ssh" || failed=yes
    ssh_bind_mounted=no
    unmount_exact "$root" || failed=yes
    root_mounted=no
    [ "$failed" = no ] || return 1
    remaining="$(mounts_under "$root" 2>/dev/null || true)"
    [ -z "$remaining" ] || {
        echo "mounts_remain_under_root_begin"
        printf '%s\n' "$remaining"
        echo "mounts_remain_under_root_end"
        return 1
    }
    return 0
}

cleanup()
{
    rollback_exact_keys
    if ! cleanup_mounts; then
        echo "cleanup_status=failed-mounts-preserved-no-recursive-root-delete"
        return 0
    fi
    rmdir "$root" 2>/dev/null || true
    rm -rf "$overlay" "$work" 2>/dev/null || true
    echo "cleanup_status=passed"
}
trap cleanup EXIT

for command in mount umount chroot cp find stat sha256sum cat grep awk sed kill sleep mkdir rmdir rm mv chmod chown sync readlink ps; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "missing_twrp_command=$command"
        exit 20
    }
done
case "$mode" in preflight|commit) ;; *) exit 21 ;; esac

[ -b "$target" ] || exit 22
[ -z "$(mounts_under "$root" 2>/dev/null || true)" ] || exit 23
[ -z "$(mounts_under "$overlay" 2>/dev/null || true)" ] || exit 24
[ -z "$(mounts_under "$work" 2>/dev/null || true)" ] || exit 25
rmdir "$root" 2>/dev/null || true
rm -rf "$overlay" "$work"
mkdir -p "$root" "$overlay" "$work"

mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$root"
root_mounted=yes
echo "readonly_generation_root_mount=passed"

for required in /usr/bin/ssh-keygen /usr/sbin/sshd.pam /etc/ssh/sshd_config /etc/init.d/sshd; do
    [ -e "$root$required" ] || [ -L "$root$required" ] || {
        echo "missing_required_rootfs_path=$required"
        exit 26
    }
done
if find "$root/etc/ssh" -maxdepth 1 -type f \( -name 'ssh_host_*_key' -o -name 'ssh_host_*_key.pub' \) -print -quit 2>/dev/null | grep -q .; then
    echo "existing_host_keys=present"
    exit 27
fi
echo "existing_host_keys=none"

config_manifest="$work/config-before.txt"
: > "$config_manifest"
for config in "$root/etc/ssh/sshd_config" "$root"/etc/ssh/sshd_config.d/*.conf; do
    [ -f "$config" ] || continue
    relative="${config#$root}"
    echo "$relative $(sha256sum "$config" | awk '{print $1}')" >> "$config_manifest"
done
[ -s "$config_manifest" ] || exit 28

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

echo "volatile_key_generation_environment=passed"
chroot "$root" /usr/bin/ssh-keygen -A
chroot "$root" /usr/sbin/sshd.pam -t -f /etc/ssh/sshd_config

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
        *) exit 29 ;;
    esac
    sha="$(sha256sum "$file" | awk '{print $1}')"
    bytes="$(stat -c '%s' "$file")"
    echo "$name $kind $sha $bytes" >> "$generated_manifest"
    echo "generated_key name=$name kind=$kind sha256=$sha bytes=$bytes"
done
[ "$private_count" -ge 3 ] || exit 30
[ "$public_count" -eq "$private_count" ] || exit 31
echo "generated_private_key_count=$private_count"
echo "generated_public_key_count=$public_count"

cleanup_mounts || {
    echo "generation_unmount_status=failed"
    exit 32
}
echo "generation_unmount_status=passed"

if [ "$mode" = preflight ]; then
    success=yes
    echo "volatile_keygen_and_sshd_validation=passed"
    echo "preflight_status=passed"
    echo "userdata_written=no"
    echo "phone_partition_writes=no"
    echo "phone_reboot_performed=no"
    exit 0
fi

mount -t ext4 -o rw,nosuid,nodev,noatime "$target" "$root"
root_mounted=yes
commit_started=yes
echo "writable_commit_root_mount=passed"

if find "$root/etc/ssh" -maxdepth 1 -type f \( -name 'ssh_host_*_key' -o -name 'ssh_host_*_key.pub' \) -print -quit 2>/dev/null | grep -q .; then
    echo "existing_host_keys_appeared=present"
    exit 33
fi
while read -r relative expected_sha; do
    actual_sha="$(sha256sum "$root$relative" 2>/dev/null | awk '{print $1}')"
    [ "$actual_sha" = "$expected_sha" ] || {
        echo "config_changed_before_commit=$relative"
        exit 34
    }
done < "$config_manifest"

staging="$root/etc/ssh/$staging_name"
[ ! -e "$staging" ] || exit 35
mkdir "$staging"
chmod 700 "$staging"
chown 0:0 "$staging"
while read -r name kind expected_sha bytes; do
    source="$overlay/$name"
    destination="$staging/$name"
    cp -p "$source" "$destination"
    chown 0:0 "$destination"
    if [ "$kind" = private ]; then chmod 600 "$destination"; else chmod 644 "$destination"; fi
    [ "$(sha256sum "$destination" | awk '{print $1}')" = "$expected_sha" ] || exit 36
done < "$generated_manifest"
sync

while read -r name kind expected_sha bytes; do
    [ ! -e "$root/etc/ssh/$name" ] || exit 37
    mv "$staging/$name" "$root/etc/ssh/$name"
    installed_names="$installed_names $name"
done < "$generated_manifest"
rmdir "$staging"
sync

cleanup_mounts || {
    echo "commit_unmount_status=failed"
    exit 38
}
echo "commit_unmount_status=passed"

mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$root"
root_mounted=yes
echo "readonly_postcommit_mount=passed"
while read -r name kind expected_sha bytes; do
    file="$root/etc/ssh/$name"
    [ -f "$file" ] || exit 39
    actual_sha="$(sha256sum "$file" | awk '{print $1}')"
    actual_mode="$(stat -c '%a' "$file")"
    actual_uid="$(stat -c '%u' "$file")"
    actual_gid="$(stat -c '%g' "$file")"
    [ "$actual_sha" = "$expected_sha" ] || exit 40
    [ "$actual_uid" = 0 ] && [ "$actual_gid" = 0 ] || exit 41
    if [ "$kind" = private ]; then [ "$actual_mode" = 600 ] || exit 42; else [ "$actual_mode" = 644 ] || exit 43; fi
    echo "installed_key name=$name kind=$kind sha256=$actual_sha bytes=$bytes mode=$actual_mode uid=$actual_uid gid=$actual_gid"
done < "$generated_manifest"
while read -r relative expected_sha; do
    actual_sha="$(sha256sum "$root$relative" | awk '{print $1}')"
    [ "$actual_sha" = "$expected_sha" ] || exit 44
done < "$config_manifest"

mount -o bind /dev "$root/dev"
dev_mounted=yes
mount -t proc proc "$root/proc"
proc_mounted=yes
mount -t sysfs sysfs "$root/sys"
sys_mounted=yes
mount -t tmpfs -o mode=0755,size=4m tmpfs "$root/run"
run_mounted=yes
chroot "$root" /usr/sbin/sshd.pam -t -f /etc/ssh/sshd_config

success=yes
cleanup_mounts || {
    success=no
    echo "postcommit_unmount_status=failed"
    exit 45
}
echo "postcommit_unmount_status=passed"
commit_started=no

echo "persistent_host_key_provision=passed"
echo "userdata_written=yes-etc-ssh-host-keys-only"
echo "phone_partition_writes=yes-userdata-host-keys-only"
echo "recovery_written=no"
echo "boot_written=no"
echo "super_written=no"
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


def parse_key_lines(output: str, prefix: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    pattern = re.compile(
        rf"^{re.escape(prefix)} name=(\S+) kind=(\S+) sha256=([0-9a-f]{{64}}) bytes=(\d+)(?: mode=(\d+) uid=(\d+) gid=(\d+))?$",
        re.MULTILINE,
    )
    for match in pattern.finditer(output):
        result.append(
            {
                "name": match.group(1),
                "kind": match.group(2),
                "sha256": match.group(3),
                "bytes": match.group(4),
                "mode": match.group(5) or "",
                "uid": match.group(6) or "",
                "gid": match.group(7) or "",
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate, validate, and safely provision A33 SSH host keys"
    )
    parser.add_argument("confirmation", nargs="?")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()

    if args.preflight_only and args.confirmation is not None:
        raise ProvisionV3Error("do not provide confirmation with --preflight-only")
    if not args.preflight_only and args.confirmation != CONFIRMATION:
        raise ProvisionV3Error(
            f"persistent host-key write requires exact confirmation: {CONFIRMATION}"
        )

    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb

    actual_restore_blob = git_blob(repo, RESTORE_PATH)
    if actual_restore_blob != EXPECTED_RESTORE_BLOB:
        raise ProvisionV3Error(
            f"checked-in restore dependency changed: actual={actual_restore_blob!r} expected={EXPECTED_RESTORE_BLOB!r}"
        )

    local, _ = restore.local_evidence(root, repo)
    print("local_exact_rootfs_evidence=passed")
    serial = common.select_recovery(adb, 30)
    fingerprint = cleanup.validate_runtime_fingerprint(adb, serial)
    state = block_helper.prepare(common, adb, serial)
    common.USERDATA = block_helper.EXACT_NODE
    print("exact_block_node_preparation=passed")
    print(f"exact_block_node_created={'yes' if state.created else 'no'}")
    print("ephemeral_device_node_write=/dev-tmpfs-only")

    output = ""
    try:
        values, sections = common.live_state(adb, serial)
        restore.assert_idle(values, sections)
        uuid_value, label = restore.identity_helper.ext4_identity(common, adb, serial)
        if uuid_value != restore.EXPECTED_UUID or label != restore.EXPECTED_LABEL:
            raise ProvisionV3Error(
                f"restored rootfs identity mismatch: uuid={uuid_value!r} label={label!r}"
            )
        verify_output = restore.run_remote(
            adb,
            serial,
            verify_helper.ROOTFS_SAFE_VERIFY_SCRIPT,
            common.USERDATA,
            restore.EXPECTED_UUID,
            *common.CRITICAL_PATHS,
            timeout=120,
        )
        actual_critical = restore.parse_critical_hashes(verify_output)
        if actual_critical != local["critical"]:
            raise ProvisionV3Error("restored rootfs critical hashes differ before host-key work")
        print("restored_rootfs_readonly_validation=passed")

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
                restore.EXPECTED_UUID,
            ],
            input_data=REMOTE_SCRIPT,
            check=False,
            timeout=180,
        )
        output = completed.stdout.replace("\r", "")
        stderr = completed.stderr.replace("\r", "")
        if completed.returncode != 0:
            raise ProvisionV3Error(
                f"safe host-key workflow failed rc={completed.returncode}:\n{output}\n{stderr}"
            )
        if output.count("cleanup_status=passed") != 1:
            raise ProvisionV3Error("safe host-key workflow cleanup did not pass exactly once")

        generated = parse_key_lines(output, "generated_key")
        if len(generated) < 6:
            raise ProvisionV3Error(f"too few generated key files: {len(generated)}")

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = root / "build/runtime-results" / f"a33-safe-ssh-host-key-{mode}-{timestamp}"
        out.mkdir(parents=True, exist_ok=False)
        raw = out / "workflow.txt"
        raw.write_text(output + ("\n=== stderr ===\n" + stderr if stderr else ""), encoding="utf-8")
        installed = parse_key_lines(output, "installed_key")
        summary = {
            "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
            "operation": f"safe-a33-ssh-host-key-{mode}",
            "implementation_language": "python3",
            "adb_serial": serial,
            "status": "passed",
            "mode": mode,
            "twrp_kernel_release": fingerprint["kernel_release"],
            "twrp_config_gz_sha256": fingerprint["config_gz_sha256"],
            "userdata_target": common.USERDATA,
            "userdata_filesystem_uuid": uuid_value,
            "userdata_filesystem_label": label,
            "generated_key_files": generated,
            "generated_private_key_count": sum(item["kind"] == "private" for item in generated),
            "generated_public_key_count": sum(item["kind"] == "public" for item in generated),
            "installed_key_files": installed,
            "installed_private_key_count": sum(item["kind"] == "private" for item in installed),
            "installed_public_key_count": sum(item["kind"] == "public" for item in installed),
            "userdata_written": "no" if mode == "preflight" else "yes-etc-ssh-host-keys-only",
            "phone_partition_writes": "no" if mode == "preflight" else "yes-userdata-host-keys-only",
            "recovery_written": "no",
            "boot_written": "no",
            "super_written": "no",
            "phone_reboot_performed": "no",
            "recursive_root_mountpoint_delete": "absent",
            "raw_report": str(raw),
            "raw_report_sha256": sha256_file(raw),
        }
        summary_path = out / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        archive = out.with_suffix(".tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(out, arcname=out.name)
        print(output, end="" if output.endswith("\n") else "\n")
        print(json.dumps(summary, indent=2, sort_keys=True))
        print(f"workflow_archive={archive}")
        print(f"workflow_archive_sha256={sha256_file(archive)}")
        return 0
    finally:
        try:
            block_helper.cleanup(common, adb, serial, state)
            print("exact_block_node_cleanup=passed")
        except Exception as exc:
            print(f"EXACT BLOCK NODE CLEANUP WARNING: {exc}", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        ProvisionV3Error,
        restore.RestoreError,
        cleanup.CleanupV2Error,
        block_helper.ExactBlockNodeError,
        restore.identity_helper.Ext4IdentityError,
        common.Refusal,
        OSError,
        ValueError,
    ) as exc:
        print(f"A33 SAFE SSH HOST-KEY PROVISION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
