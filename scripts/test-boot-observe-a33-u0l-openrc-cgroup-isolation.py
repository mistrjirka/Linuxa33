#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import a33_rootfs_boot_observer as helper

spec = importlib.util.spec_from_file_location(
    "u0l_observer", HERE / "boot-observe-a33-u0l-openrc-cgroup-isolation.py"
)
assert spec and spec.loader
observer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = observer
spec.loader.exec_module(observer)

observer.PROFILE.validate()
assert (
    observer.PROFILE.expected_flash_operation
    == "flash-exact-u0l-openrc-cgroup-isolation"
)
assert (
    observer.PROFILE.flash_report_name
    == "a33-first-rootfs-u0l-openrc-cgroup-isolation-flash.txt"
)
assert observer.PROFILE.output_prefix == "u0l-openrc-cgroup-isolation-observation"
assert observer.PROFILE.observation_operation == "observe-u0l-openrc-cgroup-isolation"

assert helper.parse_interface_for_cidr(
    "7: enx0 inet 172.16.42.2/24 scope global enx0\n",
    "172.16.42.2/24",
)
assert helper.parse_interface_for_cidr("", "172.16.42.2/24") is None
assert helper.valid_ssh_banner(b"SSH-2.0-OpenSSH_10.0\r\n")
assert not helper.valid_ssh_banner(b"HTTP/1.1 200 OK\r\n")

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    build = root / "build"
    build.mkdir()
    manifest = build / "manifest.txt"
    deploy = build / "deploy.txt"
    report = build / observer.PROFILE.flash_report_name
    manifest.write_text("candidate=fixture\n", encoding="utf-8")
    deploy.write_text("deployment_status=passed\n", encoding="utf-8")

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    expected_sha = "7" * 64
    report.write_text(
        "\n".join(
            [
                f"operation={observer.PROFILE.expected_flash_operation}",
                "implementation_language=python3",
                "userdata_validation=identity-and-critical-content-passed",
                f"candidate_sha256={expected_sha}",
                f"recovery_partition_sha256={expected_sha}",
                "userdata_written=no",
                "cache_written=no",
                "super_written=no",
                "boot_written=no",
                "recovery_written=yes",
                "reboot_performed=no",
                "flash_status=passed",
                f"candidate_manifest={manifest}",
                f"candidate_manifest_sha256={sha(manifest)}",
                f"deployment_report={deploy}",
                f"deployment_report_sha256={sha(deploy)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    local = helper.validate_flash_report(
        observer.common,
        lambda _root, _repo: {
            "manifest_path": manifest,
            "candidate_sha": expected_sha,
        },
        observer.PROFILE,
        root,
        root,
    )
    assert local["expected_recovery_sha"] == expected_sha
    assert local["flash_report_path"] == report

    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "reboot_performed=no", "reboot_performed=yes"
        ),
        encoding="utf-8",
    )
    try:
        helper.validate_flash_report(
            observer.common,
            lambda _root, _repo: {
                "manifest_path": manifest,
                "candidate_sha": expected_sha,
            },
            observer.PROFILE,
            root,
            root,
        )
    except observer.common.Refusal:
        pass
    else:
        raise AssertionError("unsafe U0l flash report was accepted")

try:
    helper.ObserverProfile(
        expected_flash_operation="bad operation",
        flash_report_name="../bad.txt",
        output_prefix="bad/path",
        observation_operation="bad operation",
    ).validate()
except ValueError:
    pass
else:
    raise AssertionError("unsafe U0l observer profile was accepted")

print("u0l_observer_profile_self_test=passed")
print("u0l_flash_report_contract=passed")
print("host_interface_parser=passed")
print("ssh_banner_parser=passed")
print("unsafe_flash_report_refusal=passed")
print("unsafe_observer_profile_refusal=passed")
print("shared_observer_contract=passed")
