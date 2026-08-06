#!/usr/bin/env python3
from __future__ import annotations

import gzip
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
V2_PATH = HERE / "make-u0q-emergency-ssh-v2.py"
EXPECTED_V2_BLOB = "63d3d9c548847b6ad710f29844265359e401185d"
RUNTIME_REVISION = "3"
MOUNT_POLICY = "verify-or-create-proc-sys-dev-devpts-run"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v2 = load("a33_u0q_v3_parent", V2_PATH)
base = v2.base
ORIGINAL_V2_EMERGENCY_BLOCK = v2.emergency_block


class Refusal(RuntimeError):
    pass


def refuse(message: str) -> None:
    raise Refusal(message)


def git_blob(repo: Path, path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


OLD_PREPARATION = rf'''# sshd normally receives this volatile privilege-separation directory from its
# OpenRC service setup. U0q starts before OpenRC, so require /run to already be
# a distinct mount and create only volatile state beneath it.
if ! /bin/busybox awk '$2 == "/sysroot/run" {{ found=1 }} END {{ exit found ? 0 : 1 }}' /proc/mounts; then
    u0q_refuse run-is-not-a-mounted-runtime-filesystem
fi
/bin/busybox mkdir -p /sysroot{v2.PRIVSEP_PATH} || u0q_refuse run-sshd-create-failed
/bin/busybox chmod 0755 /sysroot{v2.PRIVSEP_PATH} || u0q_refuse run-sshd-chmod-failed
/bin/busybox chown 0:0 /sysroot{v2.PRIVSEP_PATH} || u0q_refuse run-sshd-chown-failed
/bin/busybox rm -f /sysroot{v2.NETWORK_READY_PATH} || u0q_refuse stale-network-marker-remove-failed
printf 'uptime=%s source=initramfs event=runtime-directory-ready path={v2.PRIVSEP_PATH} backing=mounted-run revision={v2.RUNTIME_REVISION}\n' \
    "$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)" >> "$U0Q_TRACE"
'''

NEW_PREPARATION = rf'''# U0q v3 runs after initramfs cleanup hooks. Make the chroot's volatile runtime
# mounts explicit before starting long-lived processes. These are mount-namespace
# changes only; no fstab, nftables or other persistent configuration is changed.
u0q_mount_present()
{{
    /bin/busybox awk -v point="$1" '$2 == point {{ found=1 }} END {{ exit found ? 0 : 1 }}' /proc/mounts
}}
u0q_mount_fstype()
{{
    /bin/busybox awk -v point="$1" '$2 == point {{ print $3; exit }}' /proc/mounts
}}
u0q_require_directory()
{{
    [ -d "$1" ] || u0q_refuse "missing-runtime-directory-${{1#/sysroot/}}"
}}
u0q_verify_fstype()
{{
    point="$1"
    expected="$2"
    actual="$(u0q_mount_fstype "$point")"
    case ":$expected:" in
        *":$actual:"*) ;;
        *) u0q_refuse "unexpected-runtime-fstype-${{point#/sysroot/}}-${{actual:-missing}}" ;;
    esac
}}

u0q_require_directory /sysroot/proc
if ! u0q_mount_present /sysroot/proc; then
    /bin/busybox mount -t proc proc /sysroot/proc || u0q_refuse mount-proc-failed
fi
u0q_verify_fstype /sysroot/proc proc

u0q_require_directory /sysroot/sys
if ! u0q_mount_present /sysroot/sys; then
    /bin/busybox mount -t sysfs sysfs /sysroot/sys || u0q_refuse mount-sys-failed
fi
u0q_verify_fstype /sysroot/sys sysfs

u0q_require_directory /sysroot/dev
if ! u0q_mount_present /sysroot/dev; then
    /bin/busybox mount -o bind /dev /sysroot/dev || u0q_refuse bind-dev-failed
fi
u0q_verify_fstype /sysroot/dev devtmpfs:tmpfs

u0q_require_directory /sysroot/dev/pts
if ! u0q_mount_present /sysroot/dev/pts; then
    /bin/busybox mount -t devpts -o mode=0620,gid=5,ptmxmode=0666 devpts /sysroot/dev/pts || \
        u0q_refuse mount-devpts-failed
fi
u0q_verify_fstype /sysroot/dev/pts devpts

u0q_require_directory /sysroot/run
U0Q_RUN_BACKING=preexisting
if ! u0q_mount_present /sysroot/run; then
    /bin/busybox mount -t tmpfs -o mode=0755,nosuid,nodev,size=8m tmpfs /sysroot/run || \
        u0q_refuse mount-run-tmpfs-failed
    U0Q_RUN_BACKING=created-tmpfs
fi
u0q_verify_fstype /sysroot/run tmpfs:ramfs
U0Q_RUN_FSTYPE="$(u0q_mount_fstype /sysroot/run)"

/bin/busybox mkdir -p /sysroot{v2.PRIVSEP_PATH} || u0q_refuse run-sshd-create-failed
/bin/busybox chmod 0755 /sysroot{v2.PRIVSEP_PATH} || u0q_refuse run-sshd-chmod-failed
/bin/busybox chown 0:0 /sysroot{v2.PRIVSEP_PATH} || u0q_refuse run-sshd-chown-failed
/bin/busybox rm -f /sysroot{v2.NETWORK_READY_PATH} || u0q_refuse stale-network-marker-remove-failed
printf 'uptime=%s source=initramfs event=runtime-mounts-ready policy={MOUNT_POLICY} proc=proc sys=sysfs dev=%s devpts=devpts run=%s run_backing=%s revision={RUNTIME_REVISION}\n' \
    "$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)" \
    "$(u0q_mount_fstype /sysroot/dev)" "$U0Q_RUN_FSTYPE" "$U0Q_RUN_BACKING" >> "$U0Q_TRACE"
printf 'uptime=%s source=initramfs event=runtime-directory-ready path={v2.PRIVSEP_PATH} backing=%s fstype=%s revision={RUNTIME_REVISION}\n' \
    "$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)" \
    "$U0Q_RUN_BACKING" "$U0Q_RUN_FSTYPE" >> "$U0Q_TRACE"
'''


def emergency_block(public_key: str) -> str:
    block = ORIGINAL_V2_EMERGENCY_BLOCK(public_key)
    if block.count(OLD_PREPARATION) != 1:
        refuse("exact U0q v2 runtime preparation is absent or duplicated")
    patched = block.replace(OLD_PREPARATION, NEW_PREPARATION, 1)
    required = (
        "event=runtime-mounts-ready",
        f"policy={MOUNT_POLICY}",
        "mount -t proc proc /sysroot/proc",
        "mount -t sysfs sysfs /sysroot/sys",
        "mount -o bind /dev /sysroot/dev",
        "mount -t devpts",
        "mount -t tmpfs",
        "event=runtime-directory-ready",
        "event=pre-switch-root-ready",
        "exec /bin/busybox chroot /sysroot /usr/sbin/sshd",
        "exec /bin/busybox chroot /sysroot /bin/sh -s",
    )
    for token in required:
        if token not in patched:
            refuse(f"U0q v3 runtime token missing: {token}")
    if "run-is-not-a-mounted-runtime-filesystem" in patched:
        refuse("U0q v3 retained the unproven pre-mounted-run assumption")
    for token in (
        "/etc/fstab",
        "/etc/nftables.d/",
        "/etc/nftables.nft",
        "mount -o remount,rw",
        "umount -l",
        "sed -i",
        "rm -rf /sysroot",
    ):
        if token in patched:
            refuse(f"persistent or unsafe U0q v3 operation entered payload: {token}")
    return patched


def validate_generated_payload(root: Path) -> None:
    initramfs = root / "export-u0q-emergency-ssh/initramfs"
    if not initramfs.is_file():
        refuse(f"missing U0q v3 initramfs: {initramfs}")
    try:
        archive = base.v2.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, base.v2.CpioError) as exc:
        refuse(f"cannot parse generated U0q v3 initramfs: {exc}")
    init_text = archive.one(base.INIT_TARGET).data.decode("utf-8", errors="strict")
    unique = (
        "event=runtime-mounts-ready",
        f"policy={MOUNT_POLICY}",
        "mount -t proc proc /sysroot/proc",
        "mount -t sysfs sysfs /sysroot/sys",
        "mount -o bind /dev /sysroot/dev",
        "mount -t devpts",
        "mount -t tmpfs",
        "event=runtime-directory-ready",
        "event=network-ready-marker-written",
        "event=pre-switch-root-ready",
        "emergency-channel-readiness-timeout",
        "nft insert rule inet filter input tcp dport 2222 accept",
    )
    for token in unique:
        if init_text.count(token) != 1:
            refuse(f"generated U0q v3 token missing or duplicated: {token}")
    if init_text.count(v2.FIREWALL_COMMENT) != 2:
        refuse("U0q v3 firewall marker count mismatch")
    order = (
        init_text.index("candidate=U0q-emergency-ssh stage=trace-open"),
        init_text.index("event=runtime-mounts-ready"),
        init_text.index("event=runtime-directory-ready"),
        init_text.index("event=network-helper-spawned"),
        init_text.index("event=sshd-helper-spawned"),
        init_text.index("event=pre-switch-root-ready"),
        init_text.index('exec switch_root /sysroot "$init"'),
    )
    if tuple(sorted(order)) != order:
        refuse("generated U0q v3 runtime mounts/readiness are not before switch_root")


def replace_field(path: Path, key: str, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = rf"(?m)^{re.escape(key)}=.*$"
    if len(re.findall(pattern, text)) != 1:
        refuse(f"expected one field in {path.name}: {key}")
    path.write_text(re.sub(pattern, f"{key}={value}", text), encoding="utf-8")


def append_fields(path: Path, pairs: list[tuple[str, str]]) -> None:
    text = path.read_text(encoding="utf-8")
    for key, value in pairs:
        if re.search(rf"(?m)^{re.escape(key)}=", text):
            refuse(f"U0q v3 field already exists in {path.name}: {key}")
        text += f"{key}={value}\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    root, repo = v2.selected_paths()
    if git_blob(repo, V2_PATH) != EXPECTED_V2_BLOB:
        refuse("checked-in U0q v2 builder changed")

    old_emergency = v2.emergency_block
    old_validator = v2.validate_generated_payload
    try:
        v2.emergency_block = emergency_block
        v2.validate_generated_payload = validate_generated_payload
        result = v2.main()
    finally:
        v2.emergency_block = old_emergency
        v2.validate_generated_payload = old_validator
    if result != 0:
        return result

    patch = root / "build/u0q-emergency-ssh-patch.txt"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-manifest.txt"
    for path in (patch, manifest):
        if not path.is_file():
            refuse(f"missing U0q v3 evidence file: {path}")

    for path in (patch, manifest):
        replace_field(path, "u0q_runtime_revision", RUNTIME_REVISION)
        replace_field(
            path,
            "emergency_privsep_backing",
            "verified-or-created-tmpfs-run",
        )
    fields = [
        ("emergency_runtime_mount_policy", MOUNT_POLICY),
        ("emergency_proc_backing", "verified-or-created-proc"),
        ("emergency_sys_backing", "verified-or-created-sysfs"),
        ("emergency_dev_backing", "verified-or-created-bind-dev"),
        ("emergency_devpts_backing", "verified-or-created-devpts"),
        ("emergency_run_backing", "verified-or-created-tmpfs"),
        ("emergency_persistent_mount_config_delta", "none"),
    ]
    append_fields(patch, fields)
    replace_field(manifest, "patch_report_sha256", base.v2.sha_file(patch))
    append_fields(manifest, fields)

    validate_generated_payload(root)
    print(f"u0q_runtime_revision={RUNTIME_REVISION}")
    print(f"emergency_runtime_mount_policy={MOUNT_POLICY}")
    print("emergency_privsep_backing=verified-or-created-tmpfs-run")
    print("emergency_persistent_mount_config_delta=none")
    print(f"updated_patch_report_sha256={base.v2.sha_file(patch)}")
    print(f"updated_manifest_sha256={base.v2.sha_file(manifest)}")
    print("u0q_v3_build_status=passed")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        Refusal,
        v2.Refusal,
        base.Refusal,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"U0q V3 BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
