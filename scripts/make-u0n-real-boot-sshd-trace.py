#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import gzip
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
U0M_BUILDER = HERE / "make-u0m-watchdog-magic-close-v4.py"
U0M_FLASH = HERE / "flash-a33-u0m-watchdog-magic-close-v4.py"
EXPECTED_U0M_BUILDER_BLOB = "42d175aa59cd408fdc62e71d71acec8b63788acf"
EXPECTED_U0M_FLASH_BLOB = "a4523f358e853026279bc780feeb3c5306c2ea29"
ROOTFS_IMAGE = Path(
    "build/userdata-rootfs-images/20260803-193947/"
    "a33x-userdata-pmos-root.img"
)
EXPECTED_ROOTFS_SHA256 = (
    "79c94efe41da14e72e82cfc66c8e6fac6f04482fa2ea2af024f6b1ebb67d3951"
)
SSHD_INIT_PATH = "/etc/init.d/sshd"
EXPECTED_SSHD_INIT_SHA256 = (
    "f8a44c910422f471ec21318c51e42f6f804f4fa569e8fa174690a1a0d8500760"
)
INIT_TARGET = "init_2nd.sh"
WATCHDOG_TARGET = "hooks/01-a33x-watchdog.sh"
MODULES = 67
MARKER_PREFIX = "a33x-u0n-real-boot-sshd"
SNAPSHOT_SCHEDULE = (0, 1, 2, 5, 10, 20, 30, 60)
HEREDOC = "__A33X_U0N_INSTRUMENTED_SSHD_EOF__"
SPLASH_HEREDOC = "__A33X_U0N_SPLASH_GZIP_BASE64_EOF__"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0m_builder = load("a33_u0n_parent_builder", U0M_BUILDER)
u0m_flash = load("a33_u0n_parent_flash", U0M_FLASH)
u0m_v3 = u0m_builder.base
u0m_core = u0m_v3.base
v2 = u0m_core.v2


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


def read_debugfs_file(debugfs: Path, image: Path, path: str) -> bytes:
    completed = subprocess.run(
        [str(debugfs), "-R", f"cat {path}", str(image)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise Refusal(
            f"debugfs could not read {path}: rc={completed.returncode} "
            f"stderr={completed.stderr.decode(errors='replace').strip()}"
        )
    return completed.stdout


def _rename_required_function(text: str, name: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(name)}\(\)[ \t]*\{{")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        refuse(f"expected exactly one {name}() function in installed sshd init; found {len(matches)}")
    return pattern.sub(f"u0n_original_{name}() {{", text, count=1)


def _rename_optional_function(text: str, name: str) -> tuple[str, bool]:
    pattern = re.compile(rf"(?m)^{re.escape(name)}\(\)[ \t]*\{{")
    matches = list(pattern.finditer(text))
    if len(matches) > 1:
        refuse(f"optional {name}() occurs more than once in installed sshd init")
    if not matches:
        return text, False
    return pattern.sub(f"u0n_original_{name}() {{", text, count=1), True


TRACE_FUNCTIONS = r'''

# U0n real-boot instrumentation. This block wraps the existing OpenRC hooks and
# delegates to their original bodies. It does not replace start-stop-daemon or
# alter the configured daemon command.
u0n_kmsg()
{
    u0n_level="$1"
    shift
    printf '<%s>a33x-u0n-real-boot-sshd: %s\n' "$u0n_level" "$*" > /dev/kmsg 2>/dev/null || true
}

u0n_service_states()
{
    u0n_states=""
    for u0n_state in started starting stopping inactive failed crashed; do
        if [ -e "/run/openrc/$u0n_state/sshd" ]; then
            if [ -n "$u0n_states" ]; then
                u0n_states="$u0n_states,$u0n_state"
            else
                u0n_states="$u0n_state"
            fi
        fi
    done
    printf '%s\n' "${u0n_states:-none}"
}

u0n_port22_listener()
{
    awk '$2 ~ /:0016$/ && $4 == "0A" { found=1 } END { print found ? "yes" : "no" }' \
        /proc/net/tcp /proc/net/tcp6 2>/dev/null || printf '%s\n' unknown
}

u0n_snapshot()
{
    u0n_tag="$1"
    u0n_pidfile="${pidfile:-/run/sshd.pid}"
    u0n_pid="$(cat "$u0n_pidfile" 2>/dev/null || true)"
    u0n_alive=no
    u0n_cmdline=none
    u0n_wchan=none
    u0n_proc_state=none
    if [ -n "$u0n_pid" ] && kill -0 "$u0n_pid" 2>/dev/null; then
        u0n_alive=yes
        u0n_cmdline="$(tr '\000\r\n' '   ' < "/proc/$u0n_pid/cmdline" 2>/dev/null || true)"
        u0n_wchan="$(cat "/proc/$u0n_pid/wchan" 2>/dev/null || true)"
        u0n_proc_state="$(awk '$1 == "State:" { print $2 }' "/proc/$u0n_pid/status" 2>/dev/null || true)"
    fi
    u0n_listener="$(u0n_port22_listener)"
    u0n_openrc="$(u0n_service_states)"
    u0n_args="$(printf '%s' "${command_args:-}" | tr '\r\n' '  ')"
    u0n_kmsg 6 "event=snapshot tag=$u0n_tag shell_pid=$$ ppid=$PPID pidfile=$u0n_pidfile pid=${u0n_pid:-missing} alive=$u0n_alive listener=$u0n_listener openrc=$u0n_openrc selected=${command:-unset} args=${u0n_args:-none} wchan=${u0n_wchan:-none} proc_state=${u0n_proc_state:-none} cmdline=${u0n_cmdline:-none}"

    u0n_nft="$(command -v nft 2>/dev/null || true)"
    if [ -n "$u0n_nft" ]; then
        "$u0n_nft" list ruleset 2>&1 |
            grep -Ei 'hook input|policy|dport[[:space:]]+22|ssh' |
            head -n 16 |
            while IFS= read -r u0n_line; do
                u0n_kmsg 6 "event=nft tag=$u0n_tag line=$u0n_line"
            done
    else
        u0n_kmsg 6 "event=nft tag=$u0n_tag state=command-missing"
    fi
}

u0n_monitor_body()
{
    u0n_snapshot t0
    sleep 1
    u0n_snapshot t1
    sleep 1
    u0n_snapshot t2
    sleep 3
    u0n_snapshot t5
    sleep 5
    u0n_snapshot t10
    sleep 10
    u0n_snapshot t20
    sleep 10
    u0n_snapshot t30
    sleep 30
    u0n_snapshot t60
    u0n_kmsg 6 "event=monitor-complete schedule=0,1,2,5,10,20,30,60"
}

u0n_start_monitor_once()
{
    u0n_monitor_pidfile=/run/a33x-u0n-sshd-monitor.pid
    u0n_existing="$(cat "$u0n_monitor_pidfile" 2>/dev/null || true)"
    if [ -n "$u0n_existing" ] && kill -0 "$u0n_existing" 2>/dev/null; then
        u0n_kmsg 6 "event=monitor-already-running pid=$u0n_existing"
        return 0
    fi
    ( u0n_monitor_body ) </dev/null >/dev/null 2>&1 &
    u0n_monitor_pid=$!
    printf '%s\n' "$u0n_monitor_pid" > "$u0n_monitor_pidfile"
    u0n_kmsg 6 "event=monitor-started pid=$u0n_monitor_pid schedule=0,1,2,5,10,20,30,60"
}

update_command()
{
    if u0n_original_update_command "$@"; then
        u0n_rc=0
    else
        u0n_rc=$?
    fi
    u0n_kmsg 6 "event=update-command rc=$u0n_rc selected=${command:-unset} args=${command_args:-none} pidfile=${pidfile:-unset}"
    return "$u0n_rc"
}

checkconfig()
{
    u0n_kmsg 6 "event=checkconfig-enter shell_pid=$$ ppid=$PPID"
    if u0n_original_checkconfig "$@"; then
        u0n_rc=0
    else
        u0n_rc=$?
    fi
    u0n_kmsg 6 "event=checkconfig-exit rc=$u0n_rc selected=${command:-unset} args=${command_args:-none} pidfile=${pidfile:-unset}"
    return "$u0n_rc"
}

start_pre()
{
    u0n_kmsg 6 "event=start-pre-enter shell_pid=$$ ppid=$PPID parent_cmdline=$(tr '\000\r\n' '   ' < /proc/$PPID/cmdline 2>/dev/null || true)"
    if u0n_original_start_pre "$@"; then
        u0n_rc=0
    else
        u0n_rc=$?
    fi
    u0n_kmsg 6 "event=start-pre-exit rc=$u0n_rc selected=${command:-unset} args=${command_args:-none} pidfile=${pidfile:-unset}"
    u0n_start_monitor_once
    return "$u0n_rc"
}
'''


def _optional_wrapper(name: str, existed: bool, *, snapshot_before: bool) -> str:
    original_call = ""
    if existed:
        original_call = f'''    if u0n_original_{name} "$@"; then
        u0n_rc=0
    else
        u0n_rc=$?
    fi
'''
    else:
        original_call = "    u0n_rc=0\n"
    before = f'    u0n_snapshot {name}-before\n' if snapshot_before else ""
    after = f'    u0n_snapshot {name}-after\n' if not snapshot_before else ""
    return f'''
{name}()
{{
    u0n_kmsg 6 "event={name}-enter shell_pid=$$ ppid=$PPID parent_cmdline=$(tr '\\000\\r\\n' '   ' < /proc/$PPID/cmdline 2>/dev/null || true)"
{before}{original_call}{after}    u0n_kmsg 6 "event={name}-exit rc=$u0n_rc"
    return "$u0n_rc"
}}
'''


def instrument_sshd_init(original: str) -> str:
    if v2.sha_bytes(original.encode()) != EXPECTED_SSHD_INIT_SHA256:
        refuse("installed sshd init script differs from the exact restored rootfs")
    if MARKER_PREFIX in original:
        refuse("U0n instrumentation already exists in installed sshd init script")
    if HEREDOC in original or SPLASH_HEREDOC in original:
        refuse("installed sshd init script collides with U0n heredoc delimiters")

    patched = original
    for required in ("update_command", "checkconfig", "start_pre"):
        patched = _rename_required_function(patched, required)

    optional: dict[str, bool] = {}
    for name in ("start_post", "stop_pre", "stop_post"):
        patched, optional[name] = _rename_optional_function(patched, name)

    if re.search(r"(?m)^start\(\)[ \t]*\{", patched):
        refuse("installed sshd init unexpectedly defines start(); refusing to alter start semantics")
    if re.search(r"(?m)^stop\(\)[ \t]*\{", patched):
        refuse("installed sshd init unexpectedly defines stop(); refusing to alter stop semantics")

    patched = patched.rstrip() + TRACE_FUNCTIONS
    patched += _optional_wrapper("start_post", optional["start_post"], snapshot_before=False)
    patched += _optional_wrapper("stop_pre", optional["stop_pre"], snapshot_before=True)
    patched += _optional_wrapper("stop_post", optional["stop_post"], snapshot_before=False)
    patched += (
        '\nu0n_kmsg 6 "event=script-loaded shell_pid=$$ ppid=$PPID '
        'service=${RC_SVCNAME:-unset} action=${RC_CMD:-unset} selected=${command:-unset}"\n'
    )

    required_tokens = (
        "u0n_original_update_command()",
        "u0n_original_checkconfig()",
        "u0n_original_start_pre()",
        "event=monitor-started",
        "schedule=0,1,2,5,10,20,30,60",
        "event=start-pre-enter",
        "event=checkconfig-exit",
        "event=update-command",
        "event=start_post-enter",
        "event=stop_pre-enter",
        "event=stop_post-enter",
        "default_start",
    )
    # The instrumentation must not introduce a custom start() or call the default
    # implementation directly. The token check above intentionally expects zero.
    if "default_start" in patched[len(original):]:
        refuse("U0n instrumentation must not call default_start directly")
    for token in required_tokens[:-1]:
        if patched.count(token) != 1:
            refuse(f"instrumented sshd contract token missing or duplicated: {token}")
    return patched + ("\n" if not patched.endswith("\n") else "")


FONT: dict[str, tuple[str, ...]] = {
    " ": ("00000",) * 7,
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "N": ("10001", "11001", "11001", "10101", "10011", "10011", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
}


def splash_gzip_base64() -> str:
    width, height = 720, 1280
    background = (8, 12, 20)
    foreground = (235, 241, 255)
    accent = (72, 112, 180)
    pixels = bytearray(background * (width * height))
    for y in range(0, 18):
        for x in range(width):
            offset = (y * width + x) * 3
            pixels[offset:offset + 3] = bytes(accent)

    def draw_line(text: str, y0: int, scale: int) -> None:
        char_width = 6 * scale
        total = len(text) * char_width - scale
        x0 = max(0, (width - total) // 2)
        for index, character in enumerate(text):
            glyph = FONT.get(character)
            if glyph is None:
                refuse(f"splash font lacks character: {character!r}")
            gx = x0 + index * char_width
            for row, bits in enumerate(glyph):
                for column, bit in enumerate(bits):
                    if bit != "1":
                        continue
                    for dy in range(scale):
                        y = y0 + row * scale + dy
                        if not 0 <= y < height:
                            continue
                        for dx in range(scale):
                            x = gx + column * scale + dx
                            if not 0 <= x < width:
                                continue
                            offset = (y * width + x) * 3
                            pixels[offset:offset + 3] = bytes(foreground)

    draw_line("U0N SSH TRACE", 520, 8)
    draw_line("STARTING OPENRC", 620, 7)
    ppm = f"P6\n{width} {height}\n255\n".encode() + bytes(pixels)
    packed = gzip.compress(ppm, compresslevel=9, mtime=0)
    return base64.b64encode(packed).decode("ascii")


def setup_block(instrumented: str) -> str:
    instrumented_sha = v2.sha_bytes(instrumented.encode())
    splash = splash_gzip_base64()
    if HEREDOC in instrumented or SPLASH_HEREDOC in splash:
        refuse("U0n generated payload collides with heredoc delimiter")
    return f'''U0N_SSHD_SOURCE=/run/a33x-u0n-sshd.initd
U0N_SSHD_TARGET=/sysroot/etc/init.d/sshd
U0N_SSHD_ORIGINAL_SHA={EXPECTED_SSHD_INIT_SHA256}
U0N_SSHD_INSTRUMENTED_SHA={instrumented_sha}
printf '<6>{MARKER_PREFIX}: stage=setup-begin\\n' > /dev/kmsg 2>/dev/null || true

u0n_refuse()
{{
    printf '<3>{MARKER_PREFIX}: error=%s\\n' "$1" > /dev/kmsg 2>/dev/null || true
    echo "U0n refusal: $1"
    while true; do sleep 3600; done
}}

[ -f "$U0N_SSHD_TARGET" ] || u0n_refuse missing-sshd-init
u0n_original_sha="$(/bin/busybox sha256sum "$U0N_SSHD_TARGET" 2>/dev/null | /bin/busybox awk '{{print $1}}')"
[ "$u0n_original_sha" = "$U0N_SSHD_ORIGINAL_SHA" ] || u0n_refuse original-sshd-hash-mismatch
/bin/busybox cat > "$U0N_SSHD_SOURCE" <<'{HEREDOC}'
{instrumented.rstrip()}
{HEREDOC}
/bin/busybox chmod 0755 "$U0N_SSHD_SOURCE" || u0n_refuse chmod-instrumented-sshd-failed
u0n_source_sha="$(/bin/busybox sha256sum "$U0N_SSHD_SOURCE" 2>/dev/null | /bin/busybox awk '{{print $1}}')"
[ "$u0n_source_sha" = "$U0N_SSHD_INSTRUMENTED_SHA" ] || u0n_refuse instrumented-source-hash-mismatch
mount -o bind "$U0N_SSHD_SOURCE" "$U0N_SSHD_TARGET" || u0n_refuse bind-instrumented-sshd-failed
/bin/busybox grep -q " $U0N_SSHD_TARGET " /proc/self/mountinfo || u0n_refuse bind-instrumented-sshd-unverified
u0n_target_sha="$(/bin/busybox sha256sum "$U0N_SSHD_TARGET" 2>/dev/null | /bin/busybox awk '{{print $1}}')"
[ "$u0n_target_sha" = "$U0N_SSHD_INSTRUMENTED_SHA" ] || u0n_refuse instrumented-target-hash-mismatch
printf '<6>{MARKER_PREFIX}: stage=setup-success original=%s instrumented=%s\\n' "$u0n_original_sha" "$u0n_target_sha" > /dev/kmsg 2>/dev/null || true

U0N_SPLASH_GZ=/run/a33x-u0n-sshd-trace.ppm.gz
U0N_SPLASH_PPM=/run/a33x-u0n-sshd-trace.ppm
if /bin/busybox base64 -d > "$U0N_SPLASH_GZ" <<'{SPLASH_HEREDOC}'
{splash}
{SPLASH_HEREDOC}
then
    if /bin/busybox gzip -dc "$U0N_SPLASH_GZ" > "$U0N_SPLASH_PPM" 2>/dev/null; then
        if command -v show_splash >/dev/null 2>&1; then
            show_splash "$U0N_SPLASH_PPM" 2>/dev/null || true
            printf '<6>{MARKER_PREFIX}: stage=splash-attempted method=show_splash\\n' > /dev/kmsg 2>/dev/null || true
        elif [ -x /sbin/fbsplash ]; then
            /sbin/fbsplash -s "$U0N_SPLASH_PPM" 2>/dev/null || true
            printf '<6>{MARKER_PREFIX}: stage=splash-attempted method=fbsplash\\n' > /dev/kmsg 2>/dev/null || true
        else
            printf '<4>{MARKER_PREFIX}: stage=splash-unavailable\\n' > /dev/kmsg 2>/dev/null || true
        fi
    fi
fi
/bin/busybox rm -f "$U0N_SPLASH_GZ" "$U0N_SPLASH_PPM" 2>/dev/null || true
printf '<6>{MARKER_PREFIX}: stage=switch-root-ready\\n' > /dev/kmsg 2>/dev/null || true
unset U0N_SSHD_SOURCE U0N_SSHD_TARGET U0N_SSHD_ORIGINAL_SHA U0N_SSHD_INSTRUMENTED_SHA
unset U0N_SPLASH_GZ U0N_SPLASH_PPM u0n_original_sha u0n_source_sha u0n_target_sha
'''


def patch_init_second(original: str, instrumented: str) -> str:
    anchor = u0m_core.HANDOFF_BLOCK
    if original.count(anchor) != 1:
        refuse("exact U0m watchdog handoff block is absent or duplicated")
    if MARKER_PREFIX in original:
        refuse("U0n marker already exists in U0m init_2nd.sh")
    patched = original.replace(anchor, anchor + setup_block(instrumented))
    for marker in ("setup-begin", "setup-success", "switch-root-ready"):
        token = f"{MARKER_PREFIX}: stage={marker}"
        if patched.count(token) != 1:
            refuse(f"U0n init marker missing or duplicated: {token}")
    order = (
        patched.index("a33x-u0m-watchdog-handoff: stage=shutdown-success"),
        patched.index(f"{MARKER_PREFIX}: stage=setup-begin"),
        patched.index('mount -o bind "$U0N_SSHD_SOURCE" "$U0N_SSHD_TARGET"'),
        patched.index(f"{MARKER_PREFIX}: stage=setup-success"),
        patched.index(f"{MARKER_PREFIX}: stage=switch-root-ready"),
        patched.index("a33x-u0k-direct-mount: stage=switch-root-begin"),
        patched.index('exec switch_root /sysroot "$init"'),
    )
    if tuple(sorted(order)) != order:
        refuse("U0n instrumentation is not ordered after watchdog stop and before switch_root")
    forbidden = (
        'rm -rf "/sysroot"',
        "mount -o remount,rw /sysroot",
        "sed -i /sysroot",
        "> /sysroot/etc/init.d/sshd",
    )
    for token in forbidden:
        if token in patched:
            refuse(f"persistent or unsafe rootfs mutation entered U0n init patch: {token}")
    return patched


def assert_only_init_changed(before, after) -> None:
    if len(before.entries) != len(after.entries) or before.tail != after.tail:
        refuse("U0n changed CPIO entry count or trailer tail")
    changed: set[str] = set()
    for old, new in zip(before.entries, after.entries, strict=True):
        old_meta = (old.name, old.mode, old.nlink, old.ino, old.devmajor, old.devminor)
        new_meta = (new.name, new.mode, new.nlink, new.ino, new.devmajor, new.devminor)
        if old_meta != new_meta:
            refuse(f"U0n changed CPIO metadata for {old.name}")
        if v2.sha_bytes(old.data) != v2.sha_bytes(new.data):
            changed.add(old.normalized)
    if changed != {INIT_TARGET}:
        refuse(f"unexpected U0n initramfs payload delta: {sorted(changed)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build U0n real-boot OpenRC/sshd instrumentation from exact U0m"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--debugfs", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    debugfs = (
        args.debugfs.expanduser().resolve()
        if args.debugfs is not None
        else Path(shutil.which("debugfs") or "")
    )
    if not debugfs.is_file():
        refuse("debugfs is unavailable; install e2fsprogs")

    for path, expected in (
        (U0M_BUILDER, EXPECTED_U0M_BUILDER_BLOB),
        (U0M_FLASH, EXPECTED_U0M_FLASH_BLOB),
    ):
        if git_blob(repo, path) != expected:
            refuse(f"checked-in U0n dependency changed: {path.name}")

    parent = u0m_flash.base.validate_local(root, repo)
    parent_manifest_path = Path(parent["manifest_path"])
    parent_manifest = v2.kv(parent_manifest_path)
    parent_initramfs = Path(parent_manifest.get("u0m_initramfs", ""))
    if not parent_initramfs.is_file():
        refuse(f"missing exact U0m initramfs: {parent_initramfs}")
    if v2.sha_file(parent_initramfs) != parent_manifest.get("u0m_initramfs_sha256"):
        refuse("exact U0m initramfs differs from its manifest")

    rootfs_image = root / ROOTFS_IMAGE
    if not rootfs_image.is_file() or v2.sha_file(rootfs_image) != EXPECTED_ROOTFS_SHA256:
        refuse("exact restored rootfs image is missing or changed")
    sshd_bytes = read_debugfs_file(debugfs, rootfs_image, SSHD_INIT_PATH)
    if v2.sha_bytes(sshd_bytes) != EXPECTED_SSHD_INIT_SHA256:
        refuse("exact rootfs sshd init script hash mismatch")
    sshd_original = sshd_bytes.decode("utf-8", errors="strict")
    sshd_instrumented = instrument_sshd_init(sshd_original)

    try:
        before = v2.Archive.parse(gzip.decompress(parent_initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        refuse(f"cannot parse exact U0m initramfs: {exc}")
    original_init_entry = before.one(INIT_TARGET)
    original_init = original_init_entry.data.decode("utf-8", errors="strict")
    patched_init = patch_init_second(original_init, sshd_instrumented)
    patched_payload = before.replace(INIT_TARGET, patched_init.encode())
    after = v2.Archive.parse(patched_payload)
    assert_only_init_changed(before, after)
    if before.one(WATCHDOG_TARGET).data != after.one(WATCHDOG_TARGET).data:
        refuse("U0n changed the proven U0m watchdog hook")
    if v2.count_modules(before) != MODULES or v2.count_modules(after) != MODULES:
        refuse("U0n module count changed or is not 67")

    output_initramfs = root / "export-u0n-real-boot-sshd-trace/initramfs"
    inspect_dir = root / "build/u0n-real-boot-sshd-trace-inspection"
    patch_report = root / "build/u0n-real-boot-sshd-trace-patch.txt"
    recovery_output = root / "build/pmos-debug-recovery-u0n-real-boot-sshd-trace"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0n-real-boot-sshd-trace-recovery.img"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0n-real-boot-sshd-trace-manifest.txt"

    for path in (output_initramfs, patch_report, candidate, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    inspect_dir.mkdir(parents=True, exist_ok=True)
    (inspect_dir / "original-init_2nd.sh").write_text(original_init, encoding="utf-8")
    patched_init_path = inspect_dir / "patched-init_2nd.sh"
    patched_init_path.write_text(patched_init, encoding="utf-8")
    (inspect_dir / "original-sshd.initd").write_text(sshd_original, encoding="utf-8")
    instrumented_path = inspect_dir / "instrumented-sshd.initd"
    instrumented_path.write_text(sshd_instrumented, encoding="utf-8")
    subprocess.run(["sh", "-n", str(patched_init_path)], check=True)
    subprocess.run(["sh", "-n", str(instrumented_path)], check=True)

    output_initramfs.write_bytes(gzip.compress(patched_payload, compresslevel=9, mtime=0))
    roundtrip = v2.Archive.parse(gzip.decompress(output_initramfs.read_bytes()))
    assert_only_init_changed(before, roundtrip)
    if roundtrip.one(INIT_TARGET).data != patched_init.encode():
        refuse("written U0n initramfs did not round-trip")

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
        ("functional_base", "U0m-watchdog-magic-close"),
        ("u0m_manifest", parent_manifest_path),
        ("u0m_manifest_sha256", v2.sha_file(parent_manifest_path)),
        ("u0m_initramfs", parent_initramfs),
        ("u0m_initramfs_sha256", v2.sha_file(parent_initramfs)),
        ("u0n_initramfs", output_initramfs),
        ("u0n_initramfs_sha256", v2.sha_file(output_initramfs)),
        ("cpio_entry_count", len(before.entries)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_payload_delta", INIT_TARGET),
        ("shell_delta", "bind-instrument-exact-sshd-openrc-script-before-switch-root"),
        ("rootfs_persistent_delta", "none"),
        ("runtime_mount_delta", "retain-u0l-cgroup-mask-and-bind-instrumented-sshd-init"),
        ("sshd_init_path", SSHD_INIT_PATH),
        ("sshd_init_original_sha256", EXPECTED_SSHD_INIT_SHA256),
        ("sshd_init_instrumented_sha256", v2.sha_bytes(sshd_instrumented.encode())),
        ("sshd_behavior_delta", "logging-wrappers-and-detached-snapshot-monitor-only"),
        ("snapshot_schedule_seconds", ",".join(str(value) for value in SNAPSHOT_SCHEDULE)),
        ("snapshot_outputs", "kmsg-pid-listener-openrc-process-nft"),
        ("splash_mode", "best-effort-initramfs-ppm-before-switch-root"),
        ("splash_failure_behavior", "continue-boot"),
        ("original_init_2nd_sha256", v2.sha_bytes(original_init_entry.data)),
        ("patched_init_2nd_sha256", v2.sha_bytes(patched_init.encode())),
        ("u0m_watchdog_hook_preserved", "yes"),
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
        [("operation", "python-u0n-real-boot-sshd-trace")]
        + common
        + [("patch_status", "passed")],
    )

    recovery = v2.build_recovery(root, repo, output_initramfs, recovery_output)
    shutil.copy2(recovery, candidate)
    if candidate.stat().st_size != 100663296:
        refuse(f"unexpected U0n recovery size: {candidate.stat().st_size}")
    v2.write_report(
        manifest,
        [
            ("candidate", "U0n-real-boot-sshd-trace"),
            ("functional_delta", "real-default-runlevel-sshd-openrc-kmsg-instrumentation"),
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
    print(f"instrumented_sshd_sha256={v2.sha_bytes(sshd_instrumented.encode())}")
    print("rootfs_persistent_delta=none")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        Refusal,
        u0m_core.Refusal,
        u0m_core.u0l.Refusal,
        u0m_core.u0l.u0k.Refusal,
        u0m_core.u0l.u0k.u0j.Refusal,
        v2.Refusal,
        v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"REFUSING U0n: {exc}", file=sys.stderr)
        raise SystemExit(1)
