from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil


@dataclass(frozen=True)
class FlashProfile:
    operation: str
    report_name: str
    remote_candidate: str
    success_label: str

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*\.txt", self.report_name):
            raise ValueError(f"unsafe report name: {self.report_name!r}")
        if not re.fullmatch(r"/tmp/[A-Za-z0-9._-]+\.img", self.remote_candidate):
            raise ValueError(f"unsafe remote candidate path: {self.remote_candidate!r}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]+", self.operation):
            raise ValueError(f"unsafe operation name: {self.operation!r}")
        if not self.success_label or "\n" in self.success_label:
            raise ValueError("invalid success label")


def report_pairs(
    common,
    profile: FlashProfile,
    local: dict[str, object],
    candidate: Path,
    recovery_sha: str,
    created: str,
) -> list[tuple[str, object]]:
    return [
        ("created", created),
        ("operation", profile.operation),
        ("implementation_language", "python3"),
        ("deployment_report", local["deploy_path"]),
        ("deployment_report_sha256", common.sha_file(Path(local["deploy_path"]))),
        ("userdata_validation", "identity-and-critical-content-passed"),
        ("userdata_filesystem_uuid", local["root_uuid"]),
        ("userdata_critical_manifest_sha256", local["critical_manifest_sha"]),
        ("candidate_manifest", local["manifest_path"]),
        ("candidate_manifest_sha256", common.sha_file(Path(local["manifest_path"]))),
        ("candidate", candidate),
        ("candidate_size", local["candidate_size"]),
        ("candidate_sha256", local["candidate_sha"]),
        ("recovery_target", common.RECOVERY),
        ("recovery_partition_sha256", recovery_sha),
        ("userdata_written", "no"),
        ("cache_written", "no"),
        ("super_written", "no"),
        ("boot_written", "no"),
        ("recovery_written", "yes"),
        ("reboot_performed", "no"),
        ("flash_status", "passed"),
    ]


def execute_flash(
    common,
    profile: FlashProfile,
    *,
    root: Path,
    adb_argument: str,
    preflight_only: bool,
    local: dict[str, object],
) -> int:
    try:
        profile.validate()
    except ValueError as exc:
        common.refuse(str(exc))

    print("local_artifact_preflight=passed")
    print(f"candidate_sha256={local['candidate_sha']}")
    print("phone_partition_writes=no")
    if preflight_only:
        return 0

    adb = shutil.which(adb_argument) or adb_argument
    serial = common.select_recovery(adb, 30)
    print(f"adb_serial={serial}")
    values, sections = common.live_state(adb, serial)
    if (
        values.get("recovery_sha") != common.KNOWN_TWRP_SHA256
        or values.get("userdata_resolved") != common.EXPECTED_USERDATA
        or values.get("userdata_bytes") != str(common.EXPECTED_USERDATA_BYTES)
        or values.get("userdata_readonly") != "0"
        or sections.get("swap_users")
        or sections.get("dm_users")
    ):
        common.refuse(f"TWRP or userdata state is unsafe: values={values} sections={sections}")

    for _ in range(3):
        mounts = sections.get("mount_users", [])
        if not mounts:
            break
        for mountpoint in sorted(
            mounts,
            key=lambda value: (value.count("/"), len(value)),
            reverse=True,
        ):
            common.adb_shell(
                adb,
                serial,
                'umount "$1" 2>/dev/null || true\n',
                mountpoint,
            )
        values, sections = common.live_state(adb, serial)
    if sections.get("mount_users") or sections.get("swap_users") or sections.get("dm_users"):
        common.refuse(f"userdata remains in use: values={values} sections={sections}")

    actual_uuid, actual_label = common.ext4_identity(adb, serial)
    if actual_uuid != local["root_uuid"] or actual_label != "pmOS_root":
        common.refuse(
            f"installed rootfs identity mismatch: uuid={actual_uuid} label={actual_label}"
        )

    output = common.adb_shell(
        adb,
        serial,
        common.VERIFY_SCRIPT,
        common.USERDATA,
        str(local["root_uuid"]),
        *common.CRITICAL_PATHS,
    )
    print(output, end="")
    actual: dict[str, str] = {}
    for line in output.splitlines():
        match = re.fullmatch(r"critical_sha=([0-9a-f]{64}) path=(/.+)", line)
        if match:
            actual[match.group(2)] = match.group(1)
    if actual != local["critical"]:
        common.refuse(
            f"installed rootfs critical content mismatch: expected={local['critical']} actual={actual}"
        )
    if output.count("readonly_verification=passed") != 1 or output.count(
        "readonly_unmount=passed"
    ) != 1:
        common.refuse("read-only rootfs verification did not finish cleanly")
    _, sections = common.live_state(adb, serial)
    if sections.get("mount_users") or sections.get("swap_users") or sections.get("dm_users"):
        common.refuse("userdata became active after verification")

    candidate = Path(local["candidate"])
    common.run([adb, "-s", serial, "push", str(candidate), profile.remote_candidate])
    remote = common.adb_shell(
        adb,
        serial,
        'set -eu\nstat -c "%s" "$1"\nsha256sum "$1"\n',
        profile.remote_candidate,
    ).splitlines()
    if (
        len(remote) < 2
        or remote[0] != str(local["candidate_size"])
        or remote[1].split()[0] != local["candidate_sha"]
    ):
        common.adb_shell(adb, serial, 'rm -f "$1"\n', profile.remote_candidate)
        common.refuse(f"uploaded candidate identity mismatch: {remote}")

    common.adb_shell(
        adb,
        serial,
        'set -eu\ndd if="$1" of="$2" bs=4194304\nsync\n',
        profile.remote_candidate,
        common.RECOVERY,
    )
    recovery_sha = common.adb_shell(
        adb,
        serial,
        'sha256sum "$1"\n',
        common.RECOVERY,
    ).split()[0]
    common.adb_shell(adb, serial, 'rm -f "$1"\n', profile.remote_candidate)
    if recovery_sha != local["candidate_sha"]:
        common.refuse(
            f"recovery readback mismatch: expected={local['candidate_sha']} actual={recovery_sha}"
        )

    report = root / "build" / profile.report_name
    created = common.run(["date", "-Ins"]).stdout.strip()
    pairs = report_pairs(common, profile, local, candidate, recovery_sha, created)
    report.write_text(
        "".join(f"{key}={value}\n" for key, value in pairs),
        encoding="utf-8",
    )
    for key, value in pairs:
        print(f"{key}={value}")
    print(
        f"\nExact {profile.success_label} recovery flashed and verified."
        f"\nReport: {report}\nPhone remains in TWRP."
    )
    return 0
