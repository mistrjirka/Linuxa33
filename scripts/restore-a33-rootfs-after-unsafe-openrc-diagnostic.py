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
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
FLASH_PATH = HERE / "flash-a33-u0m-watchdog-magic-close-v4.py"
CLEANUP_PATH = HERE / "cleanup-a33-openrc-sshd-chroot-v2.py"
BLOCK_HELPER_PATH = HERE / "lib/a33_exact_block_node.py"
IDENTITY_HELPER_PATH = HERE / "lib/a33_ext4_identity_text.py"
VERIFY_HELPER_PATH = HERE / "lib/a33_rootfs_safe_verify.py"

EXPECTED_FLASH_BLOB = "a4523f358e853026279bc780feeb3c5306c2ea29"
EXPECTED_CLEANUP_BLOB = "51e4d07bac0bfa11d0d32a17b58feb19d7250eda"
EXPECTED_BLOCK_HELPER_BLOB = "2232f92bbf2782aed88acd9246ed063148ca63a8"
EXPECTED_IDENTITY_HELPER_BLOB = "547aa185c56cfdefe09efab2ba1fbe1e63950de0"
EXPECTED_VERIFY_HELPER_BLOB = "3968d9b2a439ac222b652a79306e611d23525579"

CONFIRMATION = "RESTORE-EXACT-A33-ROOTFS"
EXPECTED_IMAGE = Path.home() / "a33-port/build/userdata-rootfs-images/20260803-193947/a33x-userdata-pmos-root.img"
EXPECTED_IMAGE_SIZE = 802160640
EXPECTED_IMAGE_SHA256 = "79c94efe41da14e72e82cfc66c8e6fac6f04482fa2ea2af024f6b1ebb67d3951"
EXPECTED_UUID = "7b056328-bdfb-496b-ac38-2624c43c863a"
EXPECTED_LABEL = "pmOS_root"
REMOTE_IMAGE = "/tmp/a33x-rootfs-restore-exact.img"
READBACK_MIB = EXPECTED_IMAGE_SIZE // 1048576


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flash = load("a33_rootfs_restore_flash", FLASH_PATH)
cleanup = load("a33_rootfs_restore_cleanup", CLEANUP_PATH)
block_helper = load("a33_rootfs_restore_block", BLOCK_HELPER_PATH)
identity_helper = load("a33_rootfs_restore_identity", IDENTITY_HELPER_PATH)
verify_helper = load("a33_rootfs_restore_verify", VERIFY_HELPER_PATH)
common = flash.base.common


class RestoreError(RuntimeError):
    pass


DAMAGE_SCRIPT = r'''set -eu
target="$1"
mountpoint=/tmp/a33x-rootfs-damage-proof
mounted=no
cleanup_mount()
{
    [ "$mounted" = no ] || umount "$mountpoint" 2>/dev/null || true
}
trap cleanup_mount EXIT

mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"
mounted=yes
echo "readonly_damage_mount=passed"

for path in /bin /sbin /usr /etc; do
    if [ -e "$mountpoint$path" ] || [ -L "$mountpoint$path" ]; then
        echo "required_missing_path_state=present path=$path"
        exit 80
    fi
    echo "required_missing_path_state=missing path=$path"
done
for path in /dev /proc /run; do
    [ -d "$mountpoint$path" ] || {
        echo "surviving_mountpoint_state=missing path=$path"
        exit 81
    }
    echo "surviving_mountpoint_state=directory path=$path"
done

echo "root_entries_begin"
find "$mountpoint" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | sort
echo "root_entries_end"

umount "$mountpoint"
mounted=no
echo "readonly_damage_unmount=passed"
echo "damage_signature=unsafe-chroot-rm-rf-after-failed-unmount"
echo "phone_partition_writes=no"
'''


WRITE_SCRIPT = r'''set -eu
source="$1"
target="$2"
expected_size="$3"
expected_sha="$4"
readback_mib="$5"
error=/tmp/a33x-rootfs-restore-dd.err
cleanup_error()
{
    rm -f "$error" 2>/dev/null || true
}
trap cleanup_error EXIT

[ -f "$source" ] || {
    echo "staged_image_state=missing"
    exit 90
}
[ -b "$target" ] || {
    echo "target_state=not-block"
    exit 91
}
source_size="$(stat -c '%s' "$source" 2>/dev/null || true)"
source_sha="$(sha256sum "$source" 2>/dev/null | awk 'NR==1 {print $1}')"
[ "$source_size" = "$expected_size" ] || exit 92
[ "$source_sha" = "$expected_sha" ] || exit 93

: > "$error"
dd if="$source" of="$target" bs=1048576 count="$readback_mib" 2>"$error"
sync

echo "write_dd_error_begin"
cat "$error" 2>/dev/null || true
echo "write_dd_error_end"

readback_sha="$(dd if="$target" bs=1048576 count="$readback_mib" 2>/dev/null | sha256sum | awk 'NR==1 {print $1}')"
echo "readback_sha256=$readback_sha"
[ "$readback_sha" = "$expected_sha" ] || exit 94

echo "userdata_exact_prefix_write=passed"
echo "userdata_written_bytes=$expected_size"
echo "phone_partition_writes=yes-userdata-exact-image-only"
echo "recovery_written=no"
echo "boot_written=no"
echo "super_written=no"
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


def section(text: str, name: str) -> list[str]:
    begin = f"{name}_begin\n"
    end = f"{name}_end\n"
    if text.count(begin) != 1 or text.count(end) != 1:
        return []
    return [
        line
        for line in text.split(begin, 1)[1].split(end, 1)[0].splitlines()
        if line
    ]


def parse_critical_hashes(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        match = re.fullmatch(r"critical_sha=([0-9a-f]{64}) path=(/.+)", line)
        if match:
            result[match.group(2)] = match.group(1)
    return result


def assert_idle(values: dict[str, str], sections: dict[str, list[str]]) -> None:
    expected = {
        "userdata_resolved": block_helper.EXACT_NODE,
        "userdata_bytes": block_helper.EXACT_BYTES,
        "userdata_readonly": "0",
    }
    failures = [
        f"{key}: actual={values.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if values.get(key) != wanted
    ]
    for name in ("mount_users", "swap_users", "dm_users"):
        active = [line for line in sections.get(name, []) if line]
        if active:
            failures.append(f"{name}: active={active!r}")
    if failures:
        raise RestoreError("userdata is not idle and safe:\n" + "\n".join(failures))


def local_evidence(root: Path, repo: Path) -> tuple[dict[str, object], Path]:
    for path, expected in (
        (FLASH_PATH, EXPECTED_FLASH_BLOB),
        (CLEANUP_PATH, EXPECTED_CLEANUP_BLOB),
        (BLOCK_HELPER_PATH, EXPECTED_BLOCK_HELPER_BLOB),
        (IDENTITY_HELPER_PATH, EXPECTED_IDENTITY_HELPER_BLOB),
        (VERIFY_HELPER_PATH, EXPECTED_VERIFY_HELPER_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise RestoreError(
                f"checked-in dependency changed: {path.name} actual={actual!r} expected={expected!r}"
            )

    local = flash.base.validate_local(root, repo)
    deploy = common.kv(Path(local["deploy_path"]))
    image = Path(deploy.get("deployment_image", "")).expanduser().resolve()
    expected_image = EXPECTED_IMAGE.expanduser().resolve()
    failures: list[str] = []
    if image != expected_image:
        failures.append(f"deployment_image: actual={image} expected={expected_image}")
    if deploy.get("deployment_sha256") != EXPECTED_IMAGE_SHA256:
        failures.append("deployment report SHA256 differs from exact restore image")
    if deploy.get("deployment_size") != str(EXPECTED_IMAGE_SIZE):
        failures.append("deployment report size differs from exact restore image")
    if deploy.get("filesystem_uuid") != EXPECTED_UUID:
        failures.append("deployment report UUID differs from exact rootfs UUID")
    if deploy.get("filesystem_label") != EXPECTED_LABEL:
        failures.append("deployment report label differs from exact rootfs label")
    if deploy.get("deployment_status") != "passed":
        failures.append("deployment report status is not passed")
    for key in ("cache_written", "super_written", "boot_written", "recovery_written"):
        if deploy.get(key) != "no":
            failures.append(f"deployment report {key} is not no")
    if not image.is_file():
        failures.append(f"exact restore image is missing: {image}")
    else:
        if image.stat().st_size != EXPECTED_IMAGE_SIZE:
            failures.append("exact restore image size mismatch")
        if sha256_file(image) != EXPECTED_IMAGE_SHA256:
            failures.append("exact restore image SHA256 mismatch")
    if failures:
        raise RestoreError("exact local restore evidence failed:\n" + "\n".join(failures))
    return local, image


def run_remote(
    adb: str,
    serial: str,
    script: str,
    *args: str,
    timeout: int = 120,
) -> str:
    completed = common.run(
        [adb, "-s", serial, "shell", "sh", "-s", "--", *args],
        input_data=script,
        check=False,
        timeout=timeout,
    )
    output = completed.stdout.replace("\r", "")
    stderr = completed.stderr.replace("\r", "")
    if completed.returncode != 0:
        raise RestoreError(
            f"remote command failed rc={completed.returncode}:\n{output}\n{stderr}"
        )
    return output + ("\n=== stderr ===\n" + stderr if stderr else "")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Restore the exact original A33 pmOS userdata rootfs after the disabled "
            "OpenRC diagnostic deleted the mounted filesystem"
        )
    )
    parser.add_argument("confirmation", nargs="?")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()

    if args.preflight_only and args.confirmation is not None:
        raise RestoreError("do not provide a confirmation token with --preflight-only")
    if not args.preflight_only and args.confirmation != CONFIRMATION:
        raise RestoreError(
            f"userdata restoration requires exact confirmation token: {CONFIRMATION}"
        )

    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    local, image = local_evidence(root, repo)
    print("local_exact_restore_evidence=passed")
    print(f"restore_image={image}")
    print(f"restore_image_size={EXPECTED_IMAGE_SIZE}")
    print(f"restore_image_sha256={EXPECTED_IMAGE_SHA256}")

    serial = common.select_recovery(adb, 30)
    fingerprint = cleanup.validate_runtime_fingerprint(adb, serial)
    common.USERDATA = block_helper.EXACT_NODE
    state = block_helper.prepare(common, adb, serial)
    print("exact_block_node_preparation=passed")
    print(f"exact_block_node_created={'yes' if state.created else 'no'}")
    print(f"exact_block_node_kernel_dev={state.kernel_dev}")
    print("ephemeral_device_node_write=/dev-tmpfs-only")

    remote_staged = False
    damage_output = ""
    write_output = ""
    verify_output = ""
    operation_error: BaseException | None = None
    try:
        values, sections = common.live_state(adb, serial)
        assert_idle(values, sections)
        uuid_before, label_before = identity_helper.ext4_identity(common, adb, serial)
        if uuid_before != EXPECTED_UUID or label_before != EXPECTED_LABEL:
            raise RestoreError(
                f"damaged filesystem identity mismatch: uuid={uuid_before!r} label={label_before!r}"
            )

        damage_output = run_remote(
            adb,
            serial,
            DAMAGE_SCRIPT,
            block_helper.EXACT_NODE,
        )
        required_damage_markers = (
            "readonly_damage_mount=passed",
            "required_missing_path_state=missing path=/bin",
            "required_missing_path_state=missing path=/sbin",
            "required_missing_path_state=missing path=/usr",
            "required_missing_path_state=missing path=/etc",
            "surviving_mountpoint_state=directory path=/dev",
            "surviving_mountpoint_state=directory path=/proc",
            "surviving_mountpoint_state=directory path=/run",
            "readonly_damage_unmount=passed",
            "damage_signature=unsafe-chroot-rm-rf-after-failed-unmount",
        )
        for marker in required_damage_markers:
            if damage_output.count(marker) != 1:
                raise RestoreError(f"damage signature marker missing or repeated: {marker}")

        if args.preflight_only:
            print(damage_output, end="" if damage_output.endswith("\n") else "\n")
            print("restore_preflight_status=passed")
            print("userdata_written=no")
            print("phone_partition_writes=no")
            return 0

        common.run([adb, "-s", serial, "push", str(image), REMOTE_IMAGE])
        remote_staged = True
        staged = run_remote(
            adb,
            serial,
            r'''set -eu
file="$1"
stat -c 'remote_size=%s' "$file"
sha256sum "$file" | awk '{print "remote_sha256=" $1}'
echo "remote_stage_status=passed"
''',
            REMOTE_IMAGE,
            timeout=60,
        )
        if f"remote_size={EXPECTED_IMAGE_SIZE}" not in staged:
            raise RestoreError("staged restore image size mismatch")
        if f"remote_sha256={EXPECTED_IMAGE_SHA256}" not in staged:
            raise RestoreError("staged restore image SHA256 mismatch")

        write_output = run_remote(
            adb,
            serial,
            WRITE_SCRIPT,
            REMOTE_IMAGE,
            block_helper.EXACT_NODE,
            str(EXPECTED_IMAGE_SIZE),
            EXPECTED_IMAGE_SHA256,
            str(READBACK_MIB),
            timeout=240,
        )
        for marker in (
            f"readback_sha256={EXPECTED_IMAGE_SHA256}",
            "userdata_exact_prefix_write=passed",
            f"userdata_written_bytes={EXPECTED_IMAGE_SIZE}",
            "phone_partition_writes=yes-userdata-exact-image-only",
            "recovery_written=no",
            "boot_written=no",
            "super_written=no",
        ):
            if write_output.count(marker) != 1:
                raise RestoreError(f"post-write marker missing or repeated: {marker}")

        uuid_after, label_after = identity_helper.ext4_identity(common, adb, serial)
        if uuid_after != EXPECTED_UUID or label_after != EXPECTED_LABEL:
            raise RestoreError(
                f"restored filesystem identity mismatch: uuid={uuid_after!r} label={label_after!r}"
            )

        verify_output = run_remote(
            adb,
            serial,
            verify_helper.ROOTFS_SAFE_VERIFY_SCRIPT,
            block_helper.EXACT_NODE,
            EXPECTED_UUID,
            *common.CRITICAL_PATHS,
        )
        actual_critical = parse_critical_hashes(verify_output)
        if actual_critical != local["critical"]:
            raise RestoreError(
                "restored critical hashes differ from exact deployment image: "
                f"expected={local['critical']} actual={actual_critical}"
            )
        for marker in (
            "readonly_verification=passed",
            "readonly_unmount=passed",
            "rootfs_path_resolution=rootfs-relative-symlink-safe",
            "phone_partition_writes=no",
        ):
            if verify_output.count(marker) != 1:
                raise RestoreError(f"restored read-only verification marker invalid: {marker}")

        final_values, final_sections = common.live_state(adb, serial)
        assert_idle(final_values, final_sections)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = root / "build/runtime-results" / f"a33-rootfs-restore-{timestamp}"
        out.mkdir(parents=True, exist_ok=False)
        (out / "damage-proof.txt").write_text(damage_output, encoding="utf-8")
        (out / "write-and-readback.txt").write_text(write_output, encoding="utf-8")
        (out / "postwrite-verification.txt").write_text(verify_output, encoding="utf-8")
        summary = {
            "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
            "operation": "restore-exact-a33-rootfs-after-unsafe-openrc-diagnostic",
            "implementation_language": "python3",
            "adb_serial": serial,
            "restore_status": "passed",
            "cause": "unsafe-chroot-rm-rf-after-failed-unmount",
            "twrp_kernel_release": fingerprint["kernel_release"],
            "twrp_config_gz_sha256": fingerprint["config_gz_sha256"],
            "userdata_target": block_helper.EXACT_NODE,
            "userdata_kernel_dev": state.kernel_dev,
            "restore_image": str(image),
            "restore_image_size": EXPECTED_IMAGE_SIZE,
            "restore_image_sha256": EXPECTED_IMAGE_SHA256,
            "userdata_filesystem_uuid": uuid_after,
            "userdata_filesystem_label": label_after,
            "critical_path_count": len(actual_critical),
            "critical_hashes": actual_critical,
            "userdata_written": "yes-exact-original-rootfs-image-prefix",
            "userdata_written_bytes": EXPECTED_IMAGE_SIZE,
            "cache_written": "no",
            "super_written": "no",
            "boot_written": "no",
            "recovery_written": "no",
            "phone_reboot_performed": "no",
            "ssh_host_keys_after_restore": "absent-original-image-requires-reprovision",
        }
        summary_path = out / "summary.json"
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        stable = root / "build/a33-rootfs-restore-after-unsafe-diagnostic.json"
        shutil.copy2(summary_path, stable)
        archive = out.with_suffix(".tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(out, arcname=out.name)

        print(damage_output, end="" if damage_output.endswith("\n") else "\n")
        print(write_output, end="" if write_output.endswith("\n") else "\n")
        print(verify_output, end="" if verify_output.endswith("\n") else "\n")
        print(json.dumps(summary, indent=2, sort_keys=True))
        print(f"restore_directory={out}")
        print(f"restore_archive={archive}")
        print(f"restore_archive_sha256={sha256_file(archive)}")
        return 0
    except BaseException as exc:
        operation_error = exc
        raise
    finally:
        cleanup_failures: list[str] = []
        if remote_staged:
            removed = common.run(
                [adb, "-s", serial, "shell", "rm", "-f", REMOTE_IMAGE],
                check=False,
            )
            if removed.returncode != 0:
                cleanup_failures.append("failed to remove staged restore image")
        try:
            cleanup_output = block_helper.cleanup(common, adb, serial, state)
            if cleanup_output.count("exact_block_node_cleanup_status=passed") != 1:
                cleanup_failures.append("temporary block-node cleanup marker invalid")
            else:
                print("exact_block_node_cleanup=passed")
        except BaseException as exc:
            cleanup_failures.append(f"temporary block-node cleanup failed: {exc}")
        if cleanup_failures and operation_error is None:
            raise RestoreError("; ".join(cleanup_failures))
        if cleanup_failures and operation_error is not None:
            print(
                "RESTORE CLEANUP WARNING: " + "; ".join(cleanup_failures),
                file=sys.stderr,
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        RestoreError,
        cleanup.CleanupV2Error,
        block_helper.ExactBlockNodeError,
        identity_helper.Ext4IdentityError,
        common.Refusal,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"A33 ROOTFS RESTORE FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
