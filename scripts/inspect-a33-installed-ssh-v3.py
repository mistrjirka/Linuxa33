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
BASE = HERE / "inspect-a33-installed-ssh-v2.py"
CLEANUP = HERE / "cleanup-a33-openrc-sshd-chroot-v2.py"
EXPECTED_BASE_BLOB = "ed5f4050809305171fa2e85a868249ee28e2b633"
EXPECTED_CLEANUP_BLOB = "51e4d07bac0bfa11d0d32a17b58feb19d7250eda"
EXACT_USERDATA = "/dev/block/sda36"
EXPECTED_USERDATA_BYTES = "114240258048"
EXPECTED_UUID = "7b056328-bdfb-496b-ac38-2624c43c863a"
EXPECTED_LABEL = "pmOS_root"
EXPECTED_TWRP_SHA256 = "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load("a33_installed_ssh_v3_base", BASE)
cleanup = load("a33_installed_ssh_v3_cleanup", CLEANUP)
common = base.base.common


class InspectionV3Error(RuntimeError):
    pass


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


def recovery_gate(observed_sha: str) -> str:
    if observed_sha == EXPECTED_TWRP_SHA256:
        return "recovery-partition-sha256-and-runtime-fingerprint"
    if observed_sha == "":
        return "runtime-fingerprint-recovery-path-unreadable"
    raise InspectionV3Error(
        "recovery partition is readable but does not match exact TWRP: "
        f"actual={observed_sha!r} expected={EXPECTED_TWRP_SHA256!r}"
    )


def assert_userdata_idle(
    values: dict[str, str], sections: dict[str, list[str]]
) -> None:
    expected = {
        "userdata_resolved": EXACT_USERDATA,
        "userdata_bytes": EXPECTED_USERDATA_BYTES,
        "userdata_readonly": "0",
    }
    mismatches = [
        f"{key}: actual={values.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if values.get(key) != wanted
    ]
    for name in ("mount_users", "swap_users", "dm_users"):
        active = [item for item in sections.get(name, []) if item]
        if active:
            mismatches.append(f"{name}: active={active!r}")
    if mismatches:
        raise InspectionV3Error(
            "userdata is not idle and safe:\n" + "\n".join(mismatches)
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect installed A33 SSH/OpenRC state read-only through the exact "
            "userdata block node, with TWRP runtime-fingerprint fallback"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb

    for path, expected in (
        (BASE, EXPECTED_BASE_BLOB),
        (CLEANUP, EXPECTED_CLEANUP_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise InspectionV3Error(
                f"checked-in dependency changed: {path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    if base.base.EXPECTED_USERDATA != EXACT_USERDATA:
        raise InspectionV3Error(
            "installed SSH base expected userdata node changed: "
            f"actual={base.base.EXPECTED_USERDATA!r} expected={EXACT_USERDATA!r}"
        )
    if common.EXPECTED_USERDATA != EXACT_USERDATA:
        raise InspectionV3Error(
            "shared recovery helper expected userdata node changed: "
            f"actual={common.EXPECTED_USERDATA!r} expected={EXACT_USERDATA!r}"
        )
    common.USERDATA = EXACT_USERDATA

    serial = common.select_recovery(adb, 30)
    values, sections = common.live_state(adb, serial)
    fingerprint = cleanup.validate_runtime_fingerprint(adb, serial)
    gate_source = recovery_gate(values.get("recovery_sha", ""))
    assert_userdata_idle(values, sections)

    uuid_value, label = common.ext4_identity(adb, serial)
    if uuid_value != EXPECTED_UUID or label != EXPECTED_LABEL:
        raise InspectionV3Error(
            "installed rootfs identity mismatch: "
            f"uuid={uuid_value!r} label={label!r} "
            f"expected_uuid={EXPECTED_UUID!r} expected_label={EXPECTED_LABEL!r}"
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
            EXACT_USERDATA,
            EXACT_USERDATA,
        ],
        input_data=base.base.REMOTE_SCRIPT,
        check=False,
        timeout=120,
    )
    output = completed.stdout.replace("\r", "")
    stderr = completed.stderr.replace("\r", "")
    if completed.returncode != 0:
        raise InspectionV3Error(
            f"read-only rootfs inspection failed rc={completed.returncode}:\n"
            f"{output}\n{stderr}"
        )
    if output.count("readonly_mount=passed") != 1:
        raise InspectionV3Error("read-only rootfs mount did not pass exactly once")
    if output.count("readonly_unmount=passed") != 1:
        raise InspectionV3Error("read-only rootfs unmount did not pass exactly once")

    final_values, final_sections = common.live_state(adb, serial)
    assert_userdata_idle(final_values, final_sections)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / "build" / f"a33-installed-ssh-inspection-v3-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    raw = out / "rootfs-ssh-state.txt"
    raw.write_text(
        output + ("\n=== stderr ===\n" + stderr if stderr else ""),
        encoding="utf-8",
    )

    summary_values = base.summarize(output)
    summary_values.update(
        {
            "created": datetime.now().astimezone().isoformat(
                timespec="microseconds"
            ),
            "operation": "inspect-a33-installed-ssh-read-only-v3-exact-node",
            "implementation_language": "python3",
            "adb_serial": serial,
            "inspection_status": "passed",
            "twrp_gate_source": gate_source,
            "observed_recovery_partition_sha256": values.get("recovery_sha", ""),
            "twrp_kernel_release": fingerprint["kernel_release"],
            "twrp_config_gz_sha256": fingerprint["config_gz_sha256"],
            "userdata_target": EXACT_USERDATA,
            "userdata_resolved": values["userdata_resolved"],
            "userdata_filesystem_uuid": uuid_value,
            "userdata_filesystem_label": label,
            "userdata_idle_before": True,
            "userdata_idle_after": True,
            "raw_report": str(raw),
            "raw_report_sha256": sha256_file(raw),
            "userdata_persistent_writes": "no",
            "phone_partition_writes": "no",
            "recovery_written": "no",
            "boot_written": "no",
            "super_written": "no",
            "phone_reboot_performed": "no",
        }
    )
    summary = out / "summary.json"
    summary.write_text(
        json.dumps(summary_values, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    stable = root / "build/a33-installed-ssh-inspection.json"
    shutil.copy2(summary, stable)

    archive = Path(str(out) + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)
    archive_sha = sha256_file(archive)
    Path(str(archive) + ".sha256").write_text(
        f"{archive_sha}  {archive}\n", encoding="utf-8"
    )

    print(json.dumps(summary_values, indent=2, sort_keys=True))
    print(f"inspection_directory={out}")
    print(f"inspection_archive={archive}")
    print(f"inspection_archive_sha256={archive_sha}")
    print("userdata_persistent_writes=no")
    print("phone_partition_writes=no")
    print("phone_reboot_performed=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
    except (
        InspectionV3Error,
        cleanup.CleanupV2Error,
        base.base.InspectionError,
        common.Refusal,
        OSError,
        ValueError,
    ) as exc:
        print(f"A33 INSTALLED SSH V3 INSPECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
