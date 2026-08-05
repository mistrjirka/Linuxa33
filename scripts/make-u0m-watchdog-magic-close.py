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
U0L_BUILDER = HERE / "make-u0l-openrc-cgroup-isolation.py"
U0L_FLASH = HERE / "flash-a33-u0l-openrc-cgroup-isolation.py"
WATCHDOG_SOURCE = (
    HERE.parent
    / "pmaports/main/postmarketos-mkinitfs-hook-a33x-watchdog/01-a33x-watchdog.sh"
)
EXPECTED_U0L_BUILDER_BLOB = "6c3133d5efbbdf08c3197eae3693d215fbf1b642"
EXPECTED_U0L_FLASH_BLOB = "0c8ed99e7d1e75b42cf54921f7f217cad6c4f845"
EXPECTED_WATCHDOG_SOURCE_BLOB = "ed779bb8ee90a9f64438a679923a852829bc5fb0"
INIT_TARGET = "init_2nd.sh"
WATCHDOG_TARGET = "hooks/01-a33x-watchdog.sh"
MODULES = 67
MARKER_PREFIX = "a33x-u0m-watchdog-handoff"
MARKERS = ("shutdown-request", "shutdown-success")
NOWAYOUT_PARAMETER = "/sys/module/s3c2410_wdt/parameters/nowayout"
STOP_LOG = "Watchdog cluster 0 stop done"
DID_NOT_STOP_LOG = "watchdog0: watchdog did not stop!"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0l = load("a33_u0m_u0l_builder", U0L_BUILDER)
u0l_flash = load("a33_u0m_u0l_flash", U0L_FLASH)
v2 = u0l.v2


class Refusal(RuntimeError):
    pass


def refuse(message: str) -> None:
    raise Refusal(message)


ORIGINAL_FEEDER_BLOCK = '''(
\tif ! exec 3>"$watchdog_device"; then
\t\tlog_a33x_watchdog "ERROR: failed to open $watchdog_device"
\t\texit 1
\tfi

\tlog_a33x_watchdog "opened $watchdog_device; feeding every 8 seconds"

\tping_count=0
\twhile printf 'K' >&3; do
\t\tping_count=$((ping_count + 1))
\t\tlog_a33x_watchdog "ping=$ping_count device=$watchdog_device"
\t\tsleep 8
\tdone

\tlog_a33x_watchdog "ERROR: watchdog write failed"
) &

a33x_watchdog_pid=$!
printf '%s\\n' "$a33x_watchdog_pid" > /run/a33x-watchdog.pid
log_a33x_watchdog "feeder pid=$a33x_watchdog_pid device=$watchdog_device"
'''

REPLACEMENT_FEEDER_BLOCK = f'''WATCHDOG_SHUTDOWN_REQUEST=/run/a33x-watchdog.shutdown-request
WATCHDOG_SHUTDOWN_STATUS=/run/a33x-watchdog.shutdown-status
WATCHDOG_NOWAYOUT_PARAMETER={NOWAYOUT_PARAMETER}
rm -f "$WATCHDOG_SHUTDOWN_REQUEST" "$WATCHDOG_SHUTDOWN_STATUS"

read_watchdog_nowayout()
{{
\traw_nowayout="$(cat "$WATCHDOG_NOWAYOUT_PARAMETER" 2>/dev/null || true)"
\tcase "$raw_nowayout" in
\t\tN|n|0) printf '%s\\n' 0 ;;
\t\tY|y|1) printf '%s\\n' 1 ;;
\t\t*) printf '%s\\n' missing ;;
\tesac
}}

watchdog_log_count()
{{
\t/bin/busybox dmesg 2>/dev/null |
\t\t/bin/busybox grep -F -c "$1" 2>/dev/null || true
}}

(
\tif ! exec 3>"$watchdog_device"; then
\t\tlog_a33x_watchdog "ERROR: failed to open $watchdog_device"
\t\texit 1
\tfi

\tlog_a33x_watchdog "opened $watchdog_device; feeding every second; logging every 8 pings"

\tping_count=0
\twhile true; do
\t\tif [ -f "$WATCHDOG_SHUTDOWN_REQUEST" ]; then
\t\t\tnowayout="$(read_watchdog_nowayout)"
\t\t\tstop_before="$(watchdog_log_count '{STOP_LOG}')"
\t\t\tdid_not_stop_before="$(watchdog_log_count '{DID_NOT_STOP_LOG}')"
\t\t\tlog_a33x_watchdog "shutdown requested nowayout=${{nowayout:-missing}} stop_before=${{stop_before:-missing}} did_not_stop_before=${{did_not_stop_before:-missing}}"

\t\t\tif [ "$nowayout" != "0" ]; then
\t\t\t\tprintf '%s\\n' "refused-nowayout-${{nowayout:-missing}}" > "$WATCHDOG_SHUTDOWN_STATUS"
\t\t\t\trm -f "$WATCHDOG_SHUTDOWN_REQUEST"
\t\t\t\tlog_a33x_watchdog "ERROR: refusing magic close nowayout=${{nowayout:-missing}} source=$WATCHDOG_NOWAYOUT_PARAMETER"
\t\t\telse
\t\t\t\tif ! printf 'V' >&3; then
\t\t\t\t\tprintf '%s\\n' "failed-magic-write" > "$WATCHDOG_SHUTDOWN_STATUS"
\t\t\t\t\trm -f "$WATCHDOG_SHUTDOWN_REQUEST"
\t\t\t\t\tlog_a33x_watchdog "ERROR: magic-close write failed"
\t\t\t\telse
\t\t\t\t\texec 3>&-
\t\t\t\t\tsleep 1
\t\t\t\t\tstop_after="$(watchdog_log_count '{STOP_LOG}')"
\t\t\t\t\tdid_not_stop_after="$(watchdog_log_count '{DID_NOT_STOP_LOG}')"
\t\t\t\t\tlog_a33x_watchdog "magic close observed stop_after=${{stop_after:-missing}} did_not_stop_after=${{did_not_stop_after:-missing}}"

\t\t\t\t\tverified=no
\t\t\t\t\tif [ -n "$stop_before" ] && [ -n "$stop_after" ] &&
\t\t\t\t\t   [ -n "$did_not_stop_before" ] && [ -n "$did_not_stop_after" ] &&
\t\t\t\t\t   [ "$stop_after" -gt "$stop_before" ] 2>/dev/null &&
\t\t\t\t\t   [ "$did_not_stop_after" -eq "$did_not_stop_before" ] 2>/dev/null; then
\t\t\t\t\t\tverified=yes
\t\t\t\t\tfi

\t\t\t\t\tif [ "$verified" = yes ]; then
\t\t\t\t\t\tprintf '%s\\n' "stopped" > "$WATCHDOG_SHUTDOWN_STATUS"
\t\t\t\t\t\tlog_a33x_watchdog "watchdog stopped for rootfs handoff; driver stop log verified"
\t\t\t\t\t\texit 0
\t\t\t\t\tfi

\t\t\t\t\tlog_a33x_watchdog "ERROR: watchdog stop was not proven; reopening and continuing to feed"
\t\t\t\t\tif ! exec 3>"$watchdog_device"; then
\t\t\t\t\t\tprintf '%s\\n' "failed-reopen" > "$WATCHDOG_SHUTDOWN_STATUS"
\t\t\t\t\t\texit 1
\t\t\t\t\tfi
\t\t\t\t\tprintf 'K' >&3 || true
\t\t\t\t\tprintf '%s\\n' "failed-unverified-stop" > "$WATCHDOG_SHUTDOWN_STATUS"
\t\t\t\t\trm -f "$WATCHDOG_SHUTDOWN_REQUEST"
\t\t\t\tfi
\t\t\tfi
\t\tfi

\t\tif ! printf 'K' >&3; then
\t\t\tlog_a33x_watchdog "ERROR: watchdog write failed"
\t\t\texit 1
\t\tfi
\t\tping_count=$((ping_count + 1))
\t\tif [ $((ping_count % 8)) -eq 0 ]; then
\t\t\tlog_a33x_watchdog "ping=$ping_count device=$watchdog_device"
\t\tfi
\t\tsleep 1
\tdone
) &

a33x_watchdog_pid=$!
printf '%s\\n' "$a33x_watchdog_pid" > /run/a33x-watchdog.pid
log_a33x_watchdog "feeder pid=$a33x_watchdog_pid device=$watchdog_device"
'''

INIT_ANCHOR = (
    "printf '<6>a33x-u0l-openrc-cgroup-isolation: stage=mask-success\\n' "
    "> /dev/kmsg 2>/dev/null || true\n"
)

HANDOFF_BLOCK = '''WATCHDOG_SHUTDOWN_REQUEST=/run/a33x-watchdog.shutdown-request
WATCHDOG_SHUTDOWN_STATUS=/run/a33x-watchdog.shutdown-status
WATCHDOG_PID_FILE=/run/a33x-watchdog.pid
rm -f "$WATCHDOG_SHUTDOWN_STATUS"
printf '<6>a33x-u0m-watchdog-handoff: stage=shutdown-request\\n' > /dev/kmsg 2>/dev/null || true
printf '%s\\n' "shutdown" > "$WATCHDOG_SHUTDOWN_REQUEST"

watchdog_attempt=0
while [ "$watchdog_attempt" -lt 20 ] && [ ! -s "$WATCHDOG_SHUTDOWN_STATUS" ]; do
\twatchdog_attempt=$((watchdog_attempt + 1))
\tsleep 1
done
watchdog_status="$(cat "$WATCHDOG_SHUTDOWN_STATUS" 2>/dev/null || true)"
watchdog_pid="$(cat "$WATCHDOG_PID_FILE" 2>/dev/null || true)"
watchdog_exit_attempt=0
while [ "$watchdog_status" = "stopped" ] &&
      [ "$watchdog_exit_attempt" -lt 5 ] &&
      [ -n "$watchdog_pid" ] &&
      /bin/busybox kill -0 "$watchdog_pid" 2>/dev/null; do
\twatchdog_exit_attempt=$((watchdog_exit_attempt + 1))
\tsleep 1
done
watchdog_alive=no
if [ -n "$watchdog_pid" ] && /bin/busybox kill -0 "$watchdog_pid" 2>/dev/null; then
\twatchdog_alive=yes
fi
if [ "$watchdog_status" != "stopped" ] || [ "$watchdog_alive" != "no" ]; then
\tprintf '<3>a33x-u0m-watchdog-handoff: error=shutdown-failed status=%s alive=%s\\n' "${watchdog_status:-missing}" "$watchdog_alive" > /dev/kmsg 2>/dev/null || true
\techo "U0m refusal: watchdog shutdown failed status=${watchdog_status:-missing} alive=$watchdog_alive"
\twhile true; do sleep 3600; done
fi
printf '<6>a33x-u0m-watchdog-handoff: stage=shutdown-success\\n' > /dev/kmsg 2>/dev/null || true
unset WATCHDOG_SHUTDOWN_REQUEST WATCHDOG_SHUTDOWN_STATUS WATCHDOG_PID_FILE
unset watchdog_attempt watchdog_status watchdog_pid watchdog_exit_attempt watchdog_alive
'''


def patch_watchdog_hook(text: str) -> str:
    if text.count(ORIGINAL_FEEDER_BLOCK) != 1:
        refuse("U0l watchdog feeder block does not match exactly once")
    if "WATCHDOG_SHUTDOWN_REQUEST" in text or MARKER_PREFIX in text:
        refuse("watchdog handoff logic already exists in base hook")
    patched = text.replace(ORIGINAL_FEEDER_BLOCK, REPLACEMENT_FEEDER_BLOCK)
    required_counts = (
        (f"WATCHDOG_NOWAYOUT_PARAMETER={NOWAYOUT_PARAMETER}", 1),
        ("read_watchdog_nowayout()", 1),
        ("N|n|0", 1),
        ("Y|y|1", 1),
        ("watchdog_log_count()", 1),
        (STOP_LOG, 1),
        (DID_NOT_STOP_LOG, 1),
        ("printf 'V' >&3", 1),
        ("exec 3>&-", 1),
        ('printf \'%s\\n\' "stopped" > "$WATCHDOG_SHUTDOWN_STATUS"', 1),
        ("driver stop log verified", 1),
        ("failed-unverified-stop", 1),
    )
    for token, expected in required_counts:
        actual = patched.count(token)
        if actual != expected:
            refuse(
                "patched watchdog hook contract is missing or duplicated: "
                f"token={token!r} actual={actual} expected={expected}"
            )
    return patched


def patch_init_second(text: str) -> str:
    if text.count(INIT_ANCHOR) != 1:
        refuse("U0l watchdog handoff insertion anchor does not occur exactly once")
    if MARKER_PREFIX in text:
        refuse("U0m marker already exists in base init_2nd.sh")
    patched = text.replace(INIT_ANCHOR, INIT_ANCHOR + HANDOFF_BLOCK)
    for marker in MARKERS:
        token = f"{MARKER_PREFIX}: stage={marker}"
        if patched.count(token) != 1:
            refuse(f"U0m marker is missing or duplicated: {token}")
    order = (
        patched.index("a33x-u0l-openrc-cgroup-isolation: stage=mask-success"),
        patched.index(f"{MARKER_PREFIX}: stage=shutdown-request"),
        patched.index('printf \'%s\\n\' "shutdown" > "$WATCHDOG_SHUTDOWN_REQUEST"'),
        patched.index(f"{MARKER_PREFIX}: stage=shutdown-success"),
        patched.index("a33x-u0k-direct-mount: stage=switch-root-begin"),
        patched.index('exec switch_root /sysroot "$init"'),
    )
    if tuple(sorted(order)) != order:
        refuse("U0m shutdown is not ordered after U0l mask and before switch_root")
    return patched


def assert_only_payloads_changed(before, after, expected: set[str]) -> None:
    if len(before.entries) != len(after.entries) or before.tail != after.tail:
        refuse("U0m changed CPIO entry count or trailer tail")
    changed: set[str] = set()
    for old, new in zip(before.entries, after.entries, strict=True):
        old_meta = (
            old.name,
            old.mode,
            old.nlink,
            old.ino,
            old.devmajor,
            old.devminor,
        )
        new_meta = (
            new.name,
            new.mode,
            new.nlink,
            new.ino,
            new.devmajor,
            new.devminor,
        )
        if old_meta != new_meta:
            refuse(f"U0m changed CPIO metadata for {old.name}")
        if v2.sha_bytes(old.data) != v2.sha_bytes(new.data):
            changed.add(old.normalized)
    if changed != expected:
        refuse(f"unexpected U0m initramfs payload delta: {sorted(changed)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build U0m from exact U0l by performing a watchdog magic close "
            "verified from the exact driver log immediately before switch_root"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    for path, expected in (
        (U0L_BUILDER, EXPECTED_U0L_BUILDER_BLOB),
        (U0L_FLASH, EXPECTED_U0L_FLASH_BLOB),
        (WATCHDOG_SOURCE, EXPECTED_WATCHDOG_SOURCE_BLOB),
    ):
        if u0l.u0k.u0j.git_blob(repo, path) != expected:
            refuse(f"checked-in U0m dependency changed unexpectedly: {path}")

    parent = u0l_flash.validate_local(root, repo)
    u0l_manifest_path = Path(parent["manifest_path"])
    u0l_manifest = v2.kv(u0l_manifest_path)
    u0l_initramfs = Path(u0l_manifest.get("u0l_initramfs", ""))
    if not u0l_initramfs.is_file():
        refuse(f"missing exact U0l initramfs: {u0l_initramfs}")
    if v2.sha_file(u0l_initramfs) != u0l_manifest.get("u0l_initramfs_sha256"):
        refuse("U0l initramfs differs from its validated manifest")

    try:
        base = v2.Archive.parse(gzip.decompress(u0l_initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        refuse(f"cannot parse U0l initramfs: {exc}")

    original_hook_entry = base.one(WATCHDOG_TARGET)
    original_init_entry = base.one(INIT_TARGET)
    if original_hook_entry.data != WATCHDOG_SOURCE.read_bytes():
        refuse("embedded U0l watchdog hook differs from the pinned source hook")

    original_hook = original_hook_entry.data.decode("utf-8", errors="strict")
    original_init = original_init_entry.data.decode("utf-8", errors="strict")
    patched_hook = patch_watchdog_hook(original_hook)
    patched_init = patch_init_second(original_init)

    output_image = root / "export-u0m-watchdog-magic-close/initramfs"
    inspect_dir = root / "build/u0m-watchdog-magic-close-inspection"
    patch_report = root / "build/u0m-watchdog-magic-close-patch.txt"
    recovery_output = root / "build/pmos-debug-recovery-u0m-watchdog-magic-close"
    candidate = (
        root
        / "build/candidates/a33x-h1-usbpd-u0m-watchdog-magic-close-recovery.img"
    )
    manifest = (
        root
        / "build/candidates/a33x-h1-usbpd-u0m-watchdog-magic-close-manifest.txt"
    )

    inspect_dir.mkdir(parents=True, exist_ok=True)
    (inspect_dir / "original-watchdog-hook.sh").write_text(
        original_hook, encoding="utf-8"
    )
    hook_syntax = inspect_dir / "patched-watchdog-hook.sh"
    hook_syntax.write_text(patched_hook, encoding="utf-8")
    (inspect_dir / "original-init_2nd.sh").write_text(
        original_init, encoding="utf-8"
    )
    init_syntax = inspect_dir / "patched-init_2nd.sh"
    init_syntax.write_text(patched_init, encoding="utf-8")
    subprocess.run(["sh", "-n", str(hook_syntax)], check=True)
    subprocess.run(["sh", "-n", str(init_syntax)], check=True)

    once = v2.Archive.parse(base.replace(WATCHDOG_TARGET, patched_hook.encode()))
    patched_payload = once.replace(INIT_TARGET, patched_init.encode())
    after = v2.Archive.parse(patched_payload)
    assert_only_payloads_changed(base, after, {WATCHDOG_TARGET, INIT_TARGET})
    if v2.count_modules(base) != MODULES or v2.count_modules(after) != MODULES:
        refuse("U0m module count changed or is not 67")

    output_image.parent.mkdir(parents=True, exist_ok=True)
    output_image.write_bytes(gzip.compress(patched_payload, compresslevel=9, mtime=0))
    roundtrip = v2.Archive.parse(gzip.decompress(output_image.read_bytes()))
    if (
        roundtrip.one(WATCHDOG_TARGET).data != patched_hook.encode()
        or roundtrip.one(INIT_TARGET).data != patched_init.encode()
        or roundtrip.tail != base.tail
    ):
        refuse("written U0m initramfs did not round-trip")

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
        ("functional_base", "U0l-openrc-cgroup-isolation"),
        ("u0l_manifest", u0l_manifest_path),
        ("u0l_manifest_sha256", v2.sha_file(u0l_manifest_path)),
        ("u0l_initramfs", u0l_initramfs),
        ("u0l_initramfs_sha256", v2.sha_file(u0l_initramfs)),
        ("u0m_initramfs", output_image),
        ("u0m_initramfs_sha256", v2.sha_file(output_image)),
        ("cpio_entry_count", len(base.entries)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_payload_delta", f"{WATCHDOG_TARGET},{INIT_TARGET}"),
        ("shell_delta", "verified-watchdog-magic-close-before-switch-root"),
        ("watchdog_device", "/dev/watchdog0"),
        ("watchdog_magic_close_byte", "V"),
        ("watchdog_nowayout_required", "0"),
        ("watchdog_state_before_required", "active"),
        ("watchdog_state_after_required", "inactive"),
        ("watchdog_nowayout_source", NOWAYOUT_PARAMETER),
        (
            "watchdog_stop_verification",
            "driver-stop-log-increment-and-no-did-not-stop-increment",
        ),
        ("watchdog_sysfs_class_state_required", "no"),
        ("watchdog_feeder_exit_required", "yes"),
        ("watchdog_failure_behavior", "continue-feeding-and-refuse-switch-root"),
        ("rootfs_persistent_delta", "none"),
        ("runtime_mount_delta", "retain-u0l-openrc-cgroup-mask"),
        ("original_watchdog_hook_sha256", v2.sha_bytes(original_hook_entry.data)),
        ("patched_watchdog_hook_sha256", v2.sha_bytes(patched_hook.encode())),
        ("original_init_2nd_sha256", v2.sha_bytes(original_init_entry.data)),
        ("patched_init_2nd_sha256", v2.sha_bytes(patched_init.encode())),
        ("stage_markers", ",".join(MARKERS)),
        ("embedded_modules", MODULES),
        ("kernel_cmdline_delta", "none"),
        ("module_delta", "none"),
        ("kernel_delta", "none"),
        ("dtb_delta", "none"),
        ("recovery_dtbo_delta", "none"),
        ("userdata_write", "none"),
        ("phone_partition_writes", "no"),
    ]
    v2.write_report(
        patch_report,
        [("operation", "python-u0m-verified-watchdog-magic-close")]
        + common
        + [("patch_status", "passed")],
    )

    recovery = v2.build_recovery(root, repo, output_image, recovery_output)
    info = recovery_output / "final-boot-info.txt"
    if not info.is_file() or re.search(
        r"(?:^|\s)pmos_root=\S+", info.read_text(errors="replace")
    ):
        refuse("U0m recovery command-line validation failed")
    if recovery.stat().st_size != 100663296:
        refuse(f"unexpected U0m recovery size: {recovery.stat().st_size}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(recovery, candidate)

    manifest_pairs: list[tuple[str, object]] = [
        ("candidate", "U0m-watchdog-magic-close"),
        (
            "functional_delta",
            "verified-watchdog-magic-close-before-switch-root-with-fail-closed-feeder",
        ),
        ("patch_report", patch_report),
        ("patch_report_sha256", v2.sha_file(patch_report)),
    ] + common + [
        ("preparation_status", "passed"),
        ("recovery", candidate),
        ("recovery_size", candidate.stat().st_size),
        ("recovery_sha256", v2.sha_file(candidate)),
        ("build_status", "passed"),
    ]
    v2.write_report(manifest, manifest_pairs)
    print(f"Candidate: {candidate}")
    print(f"Manifest: {manifest}")
    print("No phone partition was written.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        Refusal,
        u0l.Refusal,
        u0l.u0k.Refusal,
        u0l.u0k.u0j.Refusal,
        v2.Refusal,
        v2.CpioError,
        UnicodeDecodeError,
    ) as exc:
        print(f"REFUSING U0m: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        print(
            f"REFUSING U0m: command failed rc={exc.returncode}: {exc.cmd}",
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode or 1)
