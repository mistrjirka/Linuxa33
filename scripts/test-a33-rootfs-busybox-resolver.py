#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

from lib.a33_rootfs_busybox import (
    BusyBoxResolutionError,
    RUNTIME_DIR,
    build_runtime_upload_plan,
    resolve_verified_busyboxes,
)

HERE = Path(__file__).resolve().parent
fixed_spec = importlib.util.spec_from_file_location(
    "a33_rootfs_handoff_fixed_test",
    HERE / "audit-a33-rootfs-handoff-fixed.py",
)
assert fixed_spec and fixed_spec.loader
fixed = importlib.util.module_from_spec(fixed_spec)
sys.modules[fixed_spec.name] = fixed
fixed_spec.loader.exec_module(fixed)


class Entry:
    def __init__(self, name: str, data: bytes):
        self.normalized = name
        self.data = data


class Archive:
    def __init__(self, entries):
        self.entries = entries


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    home = root / "home"
    output = root / "out"
    rootfs_bin = home / ".local/var/pmbootstrap/chroot_rootfs_samsung-a33x/bin"
    rootfs_bin.mkdir(parents=True)
    output.mkdir()

    busybox = b"busybox-fixture"
    extras = b"busybox-extras-fixture"
    (rootfs_bin / "busybox").write_bytes(busybox)
    (rootfs_bin / "busybox-extras").write_bytes(extras)

    import hashlib
    report = {
        "busybox_binary_sha256": hashlib.sha256(busybox).hexdigest(),
        "busybox_extras_binary_sha256": hashlib.sha256(extras).hexdigest(),
    }

    binaries, evidence = resolve_verified_busyboxes(
        archive=Archive([Entry("odd/path/busybox", b"")]),
        root=root,
        home=home,
        report_values=report,
        output_dir=output,
    )
    assert binaries["busybox"].read_bytes() == busybox
    assert binaries["busybox-extras"].read_bytes() == extras
    assert any("rootfs-verified-by-u0h" in line for line in evidence)

    direct_out = root / "direct"
    direct_out.mkdir()
    binaries, evidence = resolve_verified_busyboxes(
        archive=Archive([
            Entry("unusual/hardlink-name/busybox", busybox),
            Entry("another/location/busybox-extras", extras),
        ]),
        root=root,
        home=home,
        report_values=report,
        output_dir=direct_out,
    )
    assert binaries["busybox"].read_bytes() == busybox
    selected_busybox = [
        line
        for line in evidence
        if line.startswith("busybox_selected=busybox ")
    ]
    assert len(selected_busybox) == 1
    assert "source=cpio-" in selected_busybox[0]
    assert "rootfs-verified-by-u0h" not in selected_busybox[0]

    find_root = root / "find_root_partition.sh"
    runtime_test = root / "runtime-test.sh"
    find_root.write_text("find_root_partition() { :; }\n", encoding="utf-8")
    runtime_test.write_text("exit 0\n", encoding="utf-8")
    plan = build_runtime_upload_plan(
        binaries=binaries,
        find_root_script=find_root,
        runtime_test_script=runtime_test,
    )
    assert [remote for _, remote in plan] == [
        f"{RUNTIME_DIR}/busybox",
        f"{RUNTIME_DIR}/busybox-extras",
        f"{RUNTIME_DIR}/find_root_partition.sh",
        f"{RUNTIME_DIR}/runtime-test.sh",
    ]
    assert all(local.is_file() and local.stat().st_size > 0 for local, _ in plan)

    try:
        build_runtime_upload_plan(
            binaries={"busybox": binaries["busybox"]},
            find_root_script=find_root,
            runtime_test_script=runtime_test,
        )
    except BusyBoxResolutionError:
        pass
    else:
        raise AssertionError("missing BusyBox runtime upload was accepted")

    root_uuid = "7b056328-bdfb-496b-ac38-2624c43c863a"
    shim = root / "blkid"
    shim.write_text(fixed.build_blkid_shim(root_uuid), encoding="utf-8")
    shim.chmod(0o755)
    correct = subprocess.run(
        ["sh", str(shim), "/dev/block/sda36"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert correct.returncode == 0
    assert f'UUID="{root_uuid}"' in correct.stdout
    assert 'LABEL="pmOS_root"' in correct.stdout
    assert 'TYPE="ext4"' in correct.stdout
    wrong = subprocess.run(
        ["sh", str(shim), "/dev/block/sda35"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert wrong.returncode == 2

    # Execute the generated runtime script instead of asserting on its source
    # text. This verifies that the runtime_dir argument really controls PATH,
    # that the shim is discovered, and that both root lookup API modes work.
    runtime_dir = root / "simulated-twrp-runtime"
    tools = runtime_dir / "tools"
    tools.mkdir(parents=True)
    simulated_blkid = tools / "blkid"
    simulated_blkid.write_text(fixed.build_blkid_shim(root_uuid), encoding="utf-8")
    simulated_blkid.chmod(0o755)

    function_file = root / "simulated-find-root.sh"
    function_file.write_text(
        r'''find_root_partition() {
    a33x_root=/dev/block/sda36
    a33x_identity="$(blkid "$a33x_root" 2>/dev/null || true)"
    case "$a33x_identity" in
        *'TYPE="ext4"'*) ;;
        *) unset a33x_root a33x_identity; return 0 ;;
    esac
    case "$a33x_identity" in
        *'LABEL="pmOS_root"'*) ;;
        *) unset a33x_root a33x_identity; return 0 ;;
    esac
    case "$#" in
        0) printf '%s\n' "$a33x_root" ;;
        1)
            [ "$1" = partition ] || return 2
            partition="$a33x_root"
            ;;
        *) return 2 ;;
    esac
    unset a33x_root a33x_identity
}
''',
        encoding="utf-8",
    )
    generated_runtime_test = root / "generated-runtime-test.sh"
    generated_runtime_test.write_text(fixed.TWRP_FUNCTION_TEST, encoding="utf-8")
    executed = subprocess.run(
        [
            "sh",
            str(generated_runtime_test),
            str(function_file),
            str(runtime_dir),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert executed.returncode == 0, (
        f"generated TWRP runtime test failed\nstdout:\n{executed.stdout}\n"
        f"stderr:\n{executed.stderr}"
    )
    assert f"blkid_command={simulated_blkid}" in executed.stdout
    assert "stdout_value=/dev/block/sda36" in executed.stdout
    assert "output_variable_value=/dev/block/sda36" in executed.stdout
    assert "exact_u0j_dual_api_runtime=passed" in executed.stdout

    try:
        fixed.build_blkid_shim("not-a-uuid")
    except fixed.base.Refusal:
        pass
    else:
        raise AssertionError("invalid UUID was accepted for blkid shim")

    bad_report = dict(report)
    bad_report["busybox_binary_sha256"] = "0" * 64
    try:
        resolve_verified_busyboxes(
            archive=Archive([]),
            root=root,
            home=home,
            report_values=bad_report,
            output_dir=root / "bad",
        )
    except (BusyBoxResolutionError, FileNotFoundError):
        pass
    else:
        raise AssertionError("mismatching fallback hash was accepted")

print("busybox_resolver_self_test=passed")
print("cpio_basename_discovery=passed")
print("u0h_hash_bound_rootfs_fallback=passed")
print("runtime_upload_plan=passed")
print("missing_runtime_upload_refusal=passed")
print("superblock_bound_blkid_shim=passed")
print("blkid_shim_wrong_target_refusal=passed")
print("generated_twrp_runtime_execution=passed")
print("invalid_blkid_shim_uuid_refusal=passed")
print("mismatching_hash_refusal=passed")
