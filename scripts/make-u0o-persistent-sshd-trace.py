#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
U0N_BUILDER_PATH = HERE / "make-u0n-real-boot-sshd-trace.py"
U0N_AUDIT_PATH = HERE / "audit-a33-u0n-candidate.py"
EXPECTED_U0N_BUILDER_BLOB = "9b72b0ee3252f90d33f2cb6000210edfd35dd9cd"
EXPECTED_U0N_AUDIT_BLOB = "3152f2bbd504f842acd809156177b3c45cb7f800"
EXPECTED_U0N_MANIFEST_SHA256 = "ee9c238ba3d509c8216ce4457f20cfaa6eecf7dfc38feba03c4343a0641d20df"
EXPECTED_U0N_INITRAMFS_SHA256 = "d0b4b75be3a7cadde1708a5e891001ec2b453e773068436ef645fff080631ef9"
EXPECTED_U0N_CANDIDATE_SHA256 = "9196109cba6a6e13f314b2aba28de21580c8b434c74e075c451d84b48da1bc2d"
EXPECTED_U0N_INIT2_SHA256 = "ab7de4b24dd4f47a4c5b45d9a3779ca7b27d917742875b1ef0eebd9f01447d6c"
TRACE_PATH = "/var/log/a33x-u0o-real-boot-sshd.log"
INIT_TARGET = "init_2nd.sh"
WATCHDOG_TARGET = "hooks/01-a33x-watchdog.sh"
MODULES = 67
MARKER_PREFIX = "a33x-u0o-persistent-sshd"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0n = load("a33_u0o_parent_builder", U0N_BUILDER_PATH)
u0n_audit = load("a33_u0o_parent_audit", U0N_AUDIT_PATH)
v2 = u0n.v2


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


ORIGINAL_KMSG = r'''u0n_kmsg()
{
    u0n_level="$1"
    shift
    printf '<%s>a33x-u0n-real-boot-sshd: %s\n' "$u0n_level" "$*" > /dev/kmsg 2>/dev/null || true
}
'''

PERSISTENT_KMSG = rf'''U0O_TRACE={TRACE_PATH}
u0n_kmsg()
{{
    u0n_level="$1"
    shift
    u0n_message="$*"
    printf '<%s>a33x-u0n-real-boot-sshd: %s\n' "$u0n_level" "$u0n_message" > /dev/kmsg 2>/dev/null || true
    u0n_uptime="$(cut -d' ' -f1 /proc/uptime 2>/dev/null || true)"
    printf 'uptime=%s source=openrc level=%s %s\n' "${{u0n_uptime:-unknown}}" "$u0n_level" "$u0n_message" >> "$U0O_TRACE" 2>/dev/null || true
    case "$u0n_message" in
        event=snapshot*|event=monitor-complete*|event=start-pre-exit*|event=start_post-exit*|event=stop_pre-exit*|event=stop_post-exit*)
            sync 2>/dev/null || true
            ;;
    esac
}}
'''

SETUP_PREFIX = '''U0N_SSHD_SOURCE=/run/a33x-u0n-sshd.initd
U0N_SSHD_TARGET=/sysroot/etc/init.d/sshd
U0N_SSHD_ORIGINAL_SHA=f8a44c910422f471ec21318c51e42f6f804f4fa569e8fa174690a1a0d8500760
U0N_SSHD_INSTRUMENTED_SHA=a6774be5b01375be9847ae7d548f47f3fa25b251a99144f4006ed6774d353ffc
printf '<6>a33x-u0n-real-boot-sshd: stage=setup-begin\\n' > /dev/kmsg 2>/dev/null || true
'''

PERSISTENT_SETUP_PREFIX = rf'''U0N_SSHD_SOURCE=/run/a33x-u0n-sshd.initd
U0N_SSHD_TARGET=/sysroot/etc/init.d/sshd
U0N_SSHD_ORIGINAL_SHA=f8a44c910422f471ec21318c51e42f6f804f4fa569e8fa174690a1a0d8500760
U0N_SSHD_INSTRUMENTED_SHA=a6774be5b01375be9847ae7d548f47f3fa25b251a99144f4006ed6774d353ffc
U0O_TRACE=/sysroot{TRACE_PATH}

u0o_pre_trace()
{{
    u0o_level="$1"
    shift
    u0o_message="$*"
    u0o_uptime="$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)"
    printf '<%s>a33x-u0n-real-boot-sshd: %s\n' "$u0o_level" "$u0o_message" > /dev/kmsg 2>/dev/null || true
    printf 'uptime=%s source=initramfs level=%s %s\n' "${{u0o_uptime:-unknown}}" "$u0o_level" "$u0o_message" >> "$U0O_TRACE" 2>/dev/null || true
    sync 2>/dev/null || true
}}

if [ ! -d /sysroot/var/log ]; then
    printf '<3>{MARKER_PREFIX}: error=missing-var-log\n' > /dev/kmsg 2>/dev/null || true
    echo "U0o refusal: missing /sysroot/var/log"
    while true; do sleep 3600; done
fi
if ! : > "$U0O_TRACE"; then
    printf '<3>{MARKER_PREFIX}: error=trace-create-failed\n' > /dev/kmsg 2>/dev/null || true
    echo "U0o refusal: cannot create $U0O_TRACE"
    while true; do sleep 3600; done
fi
/bin/busybox chmod 0600 "$U0O_TRACE" || {{
    printf '<3>{MARKER_PREFIX}: error=trace-chmod-failed\n' > /dev/kmsg 2>/dev/null || true
    while true; do sleep 3600; done
}}
u0o_pre_trace 6 "candidate=U0o-persistent-sshd-trace stage=trace-open path={TRACE_PATH}"
u0o_pre_trace 6 "stage=setup-begin"
'''

ORIGINAL_REFUSE_LINE = "    printf '<3>a33x-u0n-real-boot-sshd: error=%s\\n' \"$1\" > /dev/kmsg 2>/dev/null || true\n"
PERSISTENT_REFUSE_LINE = "    u0o_pre_trace 3 \"error=$1\"\n"

ADDITIONAL_TRACE_INSERTIONS = (
    (
        "printf '<6>a33x-u0n-real-boot-sshd: stage=setup-success original=%s instrumented=%s\\n' \"$u0n_original_sha\" \"$u0n_target_sha\" > /dev/kmsg 2>/dev/null || true\n",
        "u0o_pre_trace 6 \"stage=setup-success original=$u0n_original_sha instrumented=$u0n_target_sha\"\n",
    ),
    (
        "            printf '<6>a33x-u0n-real-boot-sshd: stage=splash-attempted method=show_splash\\n' > /dev/kmsg 2>/dev/null || true\n",
        "            u0o_pre_trace 6 \"stage=splash-attempted method=show_splash\"\n",
    ),
    (
        "            printf '<6>a33x-u0n-real-boot-sshd: stage=splash-attempted method=fbsplash\\n' > /dev/kmsg 2>/dev/null || true\n",
        "            u0o_pre_trace 6 \"stage=splash-attempted method=fbsplash\"\n",
    ),
    (
        "            printf '<4>a33x-u0n-real-boot-sshd: stage=splash-unavailable\\n' > /dev/kmsg 2>/dev/null || true\n",
        "            u0o_pre_trace 4 \"stage=splash-unavailable\"\n",
    ),
    (
        "printf '<6>a33x-u0n-real-boot-sshd: stage=switch-root-ready\\n' > /dev/kmsg 2>/dev/null || true\n",
        "u0o_pre_trace 6 \"stage=switch-root-ready\"\n",
    ),
)


def patch_init_second(original: str) -> str:
    if v2.sha_bytes(original.encode()) != EXPECTED_U0N_INIT2_SHA256:
        refuse("exact U0n init_2nd.sh hash mismatch")
    if MARKER_PREFIX in original or TRACE_PATH in original:
        refuse("U0o persistent trace is already present")
    if original.count(ORIGINAL_KMSG) != 1:
        refuse("exact U0n kmsg helper is absent or duplicated")
    if original.count(SETUP_PREFIX) != 1:
        refuse("exact U0n setup prefix is absent or duplicated")
    if original.count(ORIGINAL_REFUSE_LINE) != 1:
        refuse("exact U0n refusal logging line is absent or duplicated")

    patched = original.replace(ORIGINAL_KMSG, PERSISTENT_KMSG)
    patched = patched.replace(SETUP_PREFIX, PERSISTENT_SETUP_PREFIX)
    patched = patched.replace(ORIGINAL_REFUSE_LINE, PERSISTENT_REFUSE_LINE)
    for anchor, addition in ADDITIONAL_TRACE_INSERTIONS:
        if patched.count(anchor) != 1:
            refuse(f"U0n persistent trace insertion anchor changed: {anchor[:80]!r}")
        patched = patched.replace(anchor, anchor + addition)

    required_counts = (
        (TRACE_PATH, 3),
        ("candidate=U0o-persistent-sshd-trace", 1),
        ("source=initramfs", 1),
        ("source=openrc", 1),
        ("stage=trace-open", 1),
        ("u0o_pre_trace 3 \"error=$1\"", 1),
        ("event=monitor-complete", 1),
        ("schedule=0,1,2,5,10,20,30,60", 2),
    )
    for token, expected in required_counts:
        actual = patched.count(token)
        if actual != expected:
            refuse(
                f"U0o persistent trace token count mismatch: token={token!r} actual={actual} expected={expected}"
            )

    allowed_sysroot_write = f': > "$U0O_TRACE"'
    forbidden = (
        'rm -rf "/sysroot"',
        "mount -o remount,rw /sysroot",
        "sed -i /sysroot",
        "> /sysroot/etc/",
        "dd if=",
        "mkfs",
        "wipefs",
    )
    for token in forbidden:
        if token in patched:
            refuse(f"unsafe persistent operation entered U0o: {token}")
    if patched.count(allowed_sysroot_write) != 1:
        refuse("U0o trace-file truncation is missing or duplicated")
    return patched


def assert_only_init_changed(before, after) -> None:
    if len(before.entries) != len(after.entries) or before.tail != after.tail:
        refuse("U0o changed CPIO entry count or trailer tail")
    changed: set[str] = set()
    for old, new in zip(before.entries, after.entries, strict=True):
        old_meta = (old.name, old.mode, old.nlink, old.ino, old.devmajor, old.devminor)
        new_meta = (new.name, new.mode, new.nlink, new.ino, new.devmajor, new.devminor)
        if old_meta != new_meta:
            refuse(f"U0o changed CPIO metadata for {old.name}")
        if v2.sha_bytes(old.data) != v2.sha_bytes(new.data):
            changed.add(old.normalized)
    if changed != {INIT_TARGET}:
        refuse(f"unexpected U0o initramfs payload delta: {sorted(changed)}")


def validate_parent(root: Path, repo: Path) -> tuple[Path, Path, dict[str, str]]:
    for path, expected in (
        (U0N_BUILDER_PATH, EXPECTED_U0N_BUILDER_BLOB),
        (U0N_AUDIT_PATH, EXPECTED_U0N_AUDIT_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            refuse(f"checked-in U0n dependency changed: {path.name} actual={actual!r} expected={expected!r}")

    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0n-real-boot-sshd-trace-manifest.txt"
    audit_path = root / "build/a33-u0n-candidate-audit.txt"
    if not manifest_path.is_file() or not audit_path.is_file():
        refuse("missing exact U0n manifest or audit")
    if v2.sha_file(manifest_path) != EXPECTED_U0N_MANIFEST_SHA256:
        refuse("exact U0n manifest hash mismatch")
    manifest = v2.kv(manifest_path)
    v2.require(
        manifest,
        {
            "candidate": "U0n-real-boot-sshd-trace",
            "recovery_sha256": EXPECTED_U0N_CANDIDATE_SHA256,
            "u0n_initramfs_sha256": EXPECTED_U0N_INITRAMFS_SHA256,
            "cpio_payload_delta": "init_2nd.sh",
            "u0m_watchdog_hook_preserved": "yes",
            "rootfs_persistent_delta": "none",
            "build_status": "passed",
        },
        "U0n parent manifest",
    )
    audit = v2.kv(audit_path)
    v2.require(
        audit,
        {
            "candidate_sha256": EXPECTED_U0N_CANDIDATE_SHA256,
            "u0m_watchdog_hook_byte_identical": "yes",
            "openrc_default_start_stop_semantics_preserved": "yes",
            "audit_status": "passed",
        },
        "U0n parent audit",
    )
    initramfs = Path(manifest.get("u0n_initramfs", ""))
    candidate = Path(manifest.get("recovery", ""))
    if not initramfs.is_file() or v2.sha_file(initramfs) != EXPECTED_U0N_INITRAMFS_SHA256:
        refuse("exact U0n initramfs is missing or changed")
    if not candidate.is_file() or v2.sha_file(candidate) != EXPECTED_U0N_CANDIDATE_SHA256:
        refuse("exact U0n recovery is missing or changed")
    return manifest_path, initramfs, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build U0o from exact U0n with one scoped persistent real-boot trace file")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    parent_manifest_path, parent_initramfs, parent_manifest = validate_parent(root, repo)
    try:
        before = v2.Archive.parse(gzip.decompress(parent_initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        refuse(f"cannot parse exact U0n initramfs: {exc}")
    original_init = before.one(INIT_TARGET).data.decode("utf-8", errors="strict")
    patched_init = patch_init_second(original_init)
    payload = before.replace(INIT_TARGET, patched_init.encode())
    after = v2.Archive.parse(payload)
    assert_only_init_changed(before, after)
    if before.one(WATCHDOG_TARGET).data != after.one(WATCHDOG_TARGET).data:
        refuse("U0o changed the proven U0m/U0n watchdog hook")
    if v2.count_modules(before) != MODULES or v2.count_modules(after) != MODULES:
        refuse("U0o module count changed or is not 67")

    output_initramfs = root / "export-u0o-persistent-sshd-trace/initramfs"
    inspect_dir = root / "build/u0o-persistent-sshd-trace-inspection"
    patch_report = root / "build/u0o-persistent-sshd-trace-patch.txt"
    recovery_output = root / "build/pmos-debug-recovery-u0o-persistent-sshd-trace"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0o-persistent-sshd-trace-recovery.img"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0o-persistent-sshd-trace-manifest.txt"
    for path in (output_initramfs, patch_report, candidate, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    inspect_dir.mkdir(parents=True, exist_ok=True)
    (inspect_dir / "u0n-init_2nd.sh").write_text(original_init, encoding="utf-8")
    syntax = inspect_dir / "u0o-init_2nd.sh"
    syntax.write_text(patched_init, encoding="utf-8")
    subprocess.run(["sh", "-n", str(syntax)], check=True)

    output_initramfs.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))
    roundtrip = v2.Archive.parse(gzip.decompress(output_initramfs.read_bytes()))
    if roundtrip.one(INIT_TARGET).data != patched_init.encode() or roundtrip.tail != before.tail:
        refuse("written U0o initramfs did not round-trip")

    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        refuse("cannot resolve repository commit")
    created = subprocess.run(
        ["date", "-Ins"], text=True, stdout=subprocess.PIPE, check=True
    ).stdout.strip()

    common: list[tuple[str, object]] = [
        ("created", created),
        ("linuxa33_commit", commit),
        ("implementation_language", "python3"),
        ("functional_base", "U0n-real-boot-sshd-trace"),
        ("u0n_manifest", parent_manifest_path),
        ("u0n_manifest_sha256", v2.sha_file(parent_manifest_path)),
        ("u0n_initramfs", parent_initramfs),
        ("u0n_initramfs_sha256", v2.sha_file(parent_initramfs)),
        ("u0o_initramfs", output_initramfs),
        ("u0o_initramfs_sha256", v2.sha_file(output_initramfs)),
        ("cpio_entry_count", len(before.entries)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_payload_delta", INIT_TARGET),
        ("shell_delta", "duplicate-u0n-trace-to-one-persistent-file"),
        ("sshd_behavior_delta_from_u0n", "none"),
        ("snapshot_schedule_seconds", "0,1,2,5,10,20,30,60"),
        ("persistent_trace_path", TRACE_PATH),
        ("persistent_trace_mode", "0600"),
        ("persistent_trace_write_scope", "truncate-on-u0o-boot-and-append-u0n-events-only"),
        ("rootfs_persistent_delta", TRACE_PATH),
        ("original_init_2nd_sha256", v2.sha_bytes(before.one(INIT_TARGET).data)),
        ("patched_init_2nd_sha256", v2.sha_bytes(patched_init.encode())),
        ("u0n_watchdog_hook_preserved", "yes"),
        ("embedded_modules", MODULES),
        ("kernel_cmdline_delta", "none"),
        ("module_delta", "none"),
        ("kernel_delta", "none"),
        ("dtb_delta", "none"),
        ("recovery_dtbo_delta", "none"),
        ("phone_partition_writes", "no"),
    ]
    v2.write_report(
        patch_report,
        [("operation", "python-u0o-one-file-persistent-sshd-trace")]
        + common
        + [("patch_status", "passed")],
    )

    recovery = v2.build_recovery(root, repo, output_initramfs, recovery_output)
    shutil.copy2(recovery, candidate)
    if candidate.stat().st_size != 100663296:
        refuse(f"unexpected U0o recovery size: {candidate.stat().st_size}")
    v2.write_report(
        manifest,
        [
            ("candidate", "U0o-persistent-sshd-trace"),
            ("functional_delta", "one-scoped-persistent-real-boot-trace-file"),
            *common,
            ("patch_report", patch_report),
            ("patch_report_sha256", v2.sha_file(patch_report)),
            ("recovery", candidate),
            ("recovery_size", candidate.stat().st_size),
            ("recovery_sha256", v2.sha_file(candidate)),
            ("preparation_status", "passed"),
            ("build_status", "passed"),
        ],
    )
    print(f"candidate={candidate}")
    print(f"candidate_sha256={v2.sha_file(candidate)}")
    print(f"manifest={manifest}")
    print(f"persistent_trace_path={TRACE_PATH}")
    print(f"rootfs_persistent_delta={TRACE_PATH}")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        Refusal,
        u0n.Refusal,
        u0n.u0m_core.Refusal,
        v2.Refusal,
        v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"REFUSING U0o: {exc}", file=sys.stderr)
        raise SystemExit(1)
