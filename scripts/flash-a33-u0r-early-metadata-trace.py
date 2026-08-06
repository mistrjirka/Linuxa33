#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PARENT_FLASH_PATH = HERE / "flash-a33-u0q-emergency-ssh-v2.py"
BUILDER_PATH = HERE / "make-u0r-early-metadata-trace.py"
EXPECTED_PARENT_FLASH_BLOB = "333036c0bd13e68b17cbb83c0e978dd07ae308a6"
EXPECTED_BUILDER_BLOB = "da593c22deae41e184e656dfb26ac61ccfbafe8c"

CONFIRMATION = "FLASH-EXACT-U0R-RECOVERY"
EXPECTED_CANDIDATE_SIZE = 100663296
REMOTE_CANDIDATE = "/tmp/a33x-u0r-early-metadata-trace-recovery.img"
REPORT_NAME = "a33-u0r-early-metadata-trace-flash.txt"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


parent = load("a33_u0r_flash_parent", PARENT_FLASH_PATH)
builder = load("a33_u0r_flash_builder", BUILDER_PATH)
base = parent.base
common = parent.common


class U0rFlashError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def local_evidence(root: Path, repo: Path) -> dict[str, object]:
    for path, expected in (
        (PARENT_FLASH_PATH, EXPECTED_PARENT_FLASH_BLOB),
        (BUILDER_PATH, EXPECTED_BUILDER_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0rFlashError(
                f"checked-in U0r dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    inherited = parent.u0p_flash.local_evidence(root, repo)
    manifest_path = (
        root / "build/candidates/a33x-h1-usbpd-u0r-early-metadata-trace-manifest.txt"
    )
    patch_path = root / "build/u0r-early-metadata-trace-patch.txt"
    candidate = (
        root / "build/candidates/a33x-h1-usbpd-u0r-early-metadata-trace-recovery.img"
    )
    for path in (manifest_path, patch_path, candidate):
        if not path.is_file():
            raise U0rFlashError(f"missing U0r evidence: {path}")
    if candidate.stat().st_size != EXPECTED_CANDIDATE_SIZE:
        raise U0rFlashError(
            f"U0r candidate size mismatch: {candidate.stat().st_size}"
        )

    candidate_sha = common.sha_file(candidate)
    manifest_sha = common.sha_file(manifest_path)
    patch_sha = common.sha_file(patch_path)
    manifest = common.kv(manifest_path)
    common.require(
        manifest,
        {
            "candidate": builder.CANDIDATE,
            "functional_base": "U0p-corrected-sshd-source-hash",
            "functional_delta": "metadata-only-stage-trace-before-rootfs-debugging",
            "cpio_payload_delta": ",".join(
                (builder.HOOK04_TARGET, builder.HOOK05_TARGET, builder.INIT_TARGET)
            ),
            "watchdog_hook_preserved": "yes",
            "normal_openrc_sshd_instrumentation_preserved": "yes",
            "metadata_trace_path": f"/{builder.TRACE_RELATIVE}",
            "metadata_hook04_path": f"/{builder.HOOK04_RELATIVE}",
            "metadata_hook05_path": f"/{builder.HOOK05_RELATIVE}",
            "runtime_persistent_write_partition": "metadata",
            "runtime_persistent_write_scope": "three-u0r-diagnostic-files",
            "userdata_runtime_delta": "none-added-by-u0r",
            "kernel_cmdline_delta": "none",
            "module_delta": "none",
            "patch_report_sha256": patch_sha,
            "recovery_sha256": candidate_sha,
            "recovery_size": str(EXPECTED_CANDIDATE_SIZE),
            "phone_partition_writes": "no",
            "build_status": "passed",
        },
        "U0r manifest",
    )
    patch = common.kv(patch_path)
    common.require(
        patch,
        {
            "operation": "build-u0r-early-metadata-trace",
            "functional_base": "U0p-corrected-sshd-source-hash",
            "metadata_trace_path": f"/{builder.TRACE_RELATIVE}",
            "metadata_hook04_path": f"/{builder.HOOK04_RELATIVE}",
            "metadata_hook05_path": f"/{builder.HOOK05_RELATIVE}",
            "syntax_validation": "passed",
            "watchdog_hook_preserved": "yes",
            "normal_openrc_sshd_instrumentation_preserved": "yes",
            "patch_status": "passed",
            "phone_partition_writes": "no",
        },
        "U0r patch report",
    )

    commit = manifest.get("linuxa33_commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise U0rFlashError("invalid U0r manifest commit")
    ancestor = common.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, "HEAD"],
        check=False,
    )
    if ancestor.returncode != 0:
        raise U0rFlashError("U0r manifest commit is not an ancestor of HEAD")

    return {
        **inherited,
        "manifest_path": manifest_path,
        "patch_path": patch_path,
        "candidate": candidate,
        "candidate_sha": candidate_sha,
        "candidate_size": EXPECTED_CANDIDATE_SIZE,
        "manifest_sha": manifest_sha,
        "patch_sha": patch_sha,
    }


METADATA_BASELINE_SCRIPT = r'''set -eu
metadata=/dev/block/by-name/metadata
expected="$1"
mountpoint=/tmp/a33x-u0r-metadata-baseline
shift
[ -b "$metadata" ] || exit 80
resolved="$(readlink -f "$metadata" 2>/dev/null || true)"
[ "$resolved" = "$expected" ] || exit 81
mounted=no
cleanup()
{
    if [ "$mounted" = yes ]; then
        umount "$mountpoint" 2>/dev/null || true
    fi
    if ! awk -v point="$mountpoint" '$2 == point { found=1 } END { exit found ? 0 : 1 }' /proc/mounts; then
        rmdir "$mountpoint" 2>/dev/null || true
    fi
}
trap cleanup EXIT
mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$resolved" "$mountpoint"
mounted=yes
for relative in "$@"; do
    [ ! -e "$mountpoint/$relative" ] || {
        echo "unexpected_u0r_metadata_file=$relative"
        exit 82
    }
done
umount "$mountpoint"
mounted=no
echo "u0r_metadata_baseline=all-three-files-absent"
echo "metadata_resolved=$resolved"
echo "metadata_readonly_unmount=passed"
echo "metadata_partition_writes=no"
'''


def validate_metadata_baseline(adb: str, serial: str) -> None:
    output = common.adb_shell(
        adb,
        serial,
        METADATA_BASELINE_SCRIPT,
        "/dev/block/sda26",
        builder.TRACE_RELATIVE,
        builder.HOOK04_RELATIVE,
        builder.HOOK05_RELATIVE,
    )
    for token in (
        "u0r_metadata_baseline=all-three-files-absent",
        "metadata_resolved=/dev/block/sda26",
        "metadata_readonly_unmount=passed",
        "metadata_partition_writes=no",
    ):
        if output.count(token) != 1:
            raise U0rFlashError(f"U0r metadata baseline marker missing: {token}")
    print("u0r_metadata_trace_baseline=passed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flash exact U0r recovery; runtime writes only three metadata diagnostics"
    )
    parser.add_argument("confirmation", nargs="?", default="")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    if args.preflight_only and args.confirmation:
        raise U0rFlashError("do not provide confirmation with --preflight-only")
    if not args.preflight_only and args.confirmation != CONFIRMATION:
        raise U0rFlashError(
            f"recovery write requires exact confirmation token: {CONFIRMATION}"
        )

    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    local = local_evidence(root, repo)
    print("u0r_local_candidate_and_rescue_evidence=passed")
    print(f"candidate_sha256={local['candidate_sha']}")
    print(f"manifest_sha256={local['manifest_sha']}")
    print(f"patch_report_sha256={local['patch_sha']}")

    serial = common.select_recovery(adb, 30)
    fingerprint = base.restore.cleanup.validate_runtime_fingerprint(adb, serial)
    parent.validate_phone_rootfs(adb, serial, local)
    validate_metadata_baseline(adb, serial)
    recovery_state = base.recovery_helper.prepare(
        common, adb, serial, common.KNOWN_TWRP_SHA256
    )
    print("exact_twrp_recovery_partition=passed")
    print(f"recovery_kernel_name={recovery_state.kernel_name}")
    print(f"recovery_kernel_dev={recovery_state.kernel_dev}")

    remote_staged = False
    recovery_written = False
    try:
        if args.preflight_only:
            print("u0r_flash_preflight_status=passed")
            print("recovery_written=no")
            print("phone_partition_writes=no")
            return 0

        common.run(
            [adb, "-s", serial, "push", str(local["candidate"]), REMOTE_CANDIDATE]
        )
        remote_staged = True
        remote = common.adb_shell(
            adb,
            serial,
            'set -eu\nstat -c "%s" "$1"\nsha256sum "$1"\n',
            REMOTE_CANDIDATE,
        ).splitlines()
        if (
            len(remote) < 2
            or remote[0] != str(local["candidate_size"])
            or remote[1].split()[0] != local["candidate_sha"]
        ):
            raise U0rFlashError(f"staged U0r identity mismatch: {remote!r}")

        write_output = common.adb_shell(
            adb,
            serial,
            base.WRITE_SCRIPT,
            REMOTE_CANDIDATE,
            recovery_state.node,
            str(local["candidate_size"]),
            str(local["candidate_sha"]),
        )
        if write_output.count("recovery_exact_write=passed") != 1:
            raise U0rFlashError("U0r recovery write did not report success")
        recovery_written = True

        report = root / "build" / REPORT_NAME
        pairs = [
            ("created", datetime.now().astimezone().isoformat(timespec="microseconds")),
            ("operation", "flash-exact-u0r-early-metadata-trace"),
            ("implementation_language", "python3"),
            ("adb_serial", serial),
            ("candidate", local["candidate"]),
            ("candidate_sha256", local["candidate_sha"]),
            ("candidate_size", local["candidate_size"]),
            ("manifest", local["manifest_path"]),
            ("manifest_sha256", local["manifest_sha"]),
            ("patch_report", local["patch_path"]),
            ("patch_report_sha256", local["patch_sha"]),
            ("twrp_kernel_release", fingerprint["kernel_release"]),
            ("twrp_config_gz_sha256", fingerprint["config_gz_sha256"]),
            ("recovery_partname", recovery_state.partname),
            ("recovery_kernel_name", recovery_state.kernel_name),
            ("recovery_kernel_dev", recovery_state.kernel_dev),
            ("recovery_previous_sha256", common.KNOWN_TWRP_SHA256),
            ("recovery_partition_sha256", local["candidate_sha"]),
            (
                "rootfs_validation",
                "identity-critical-hashes-exact-host-keys-and-known-u0p-trace-passed",
            ),
            ("metadata_trace_baseline", "all-three-u0r-files-absent"),
            ("metadata_trace_path", f"/{builder.TRACE_RELATIVE}"),
            ("metadata_hook04_path", f"/{builder.HOOK04_RELATIVE}"),
            ("metadata_hook05_path", f"/{builder.HOOK05_RELATIVE}"),
            ("runtime_metadata_writes_expected", "yes-three-diagnostic-files"),
            ("userdata_written", "no"),
            ("metadata_written_by_flash", "no"),
            ("cache_written", "no"),
            ("super_written", "no"),
            ("boot_written", "no"),
            ("recovery_written", "yes"),
            ("reboot_performed", "no"),
            ("flash_status", "passed"),
        ]
        report.write_text(
            "".join(f"{key}={value}\n" for key, value in pairs), encoding="utf-8"
        )
        for key, value in pairs:
            print(f"{key}={value}")
        print(f"report={report}")
        print("Phone remains in the currently running TWRP RAM environment.")
        return 0
    finally:
        if remote_staged:
            common.adb_shell(
                adb, serial, 'rm -f "$1" 2>/dev/null || true\n', REMOTE_CANDIDATE
            )
        cleanup_output = base.recovery_helper.cleanup(
            common, adb, serial, recovery_state
        )
        if cleanup_output.count("exact_recovery_node_cleanup_status=passed") != 1:
            message = "temporary recovery node cleanup failed"
            if recovery_written:
                message += "; U0r may already be installed"
            raise U0rFlashError(message)
        print("exact_recovery_node_cleanup=passed")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0rFlashError,
        parent.U0qV2FlashError,
        parent.u0p_flash.U0pFlashError,
        base.U0nFlashError,
        base.restore.RestoreError,
        base.restore.cleanup.CleanupV2Error,
        base.restore.block_helper.ExactBlockNodeError,
        base.restore.identity_helper.Ext4IdentityError,
        base.recovery_helper.ExactRecoveryNodeError,
        base.rescue.RescueError,
        builder.Refusal,
        common.Refusal,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"REFUSING U0r FLASH: {exc}", file=sys.stderr)
        raise SystemExit(1)
