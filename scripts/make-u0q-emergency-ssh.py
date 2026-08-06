#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
U0P_BUILDER_PATH = HERE / "make-u0p-corrected-sshd-source-hash.py"
U0P_AUDIT_PATH = HERE / "audit-a33-u0p-candidate.py"
EXPECTED_U0P_BUILDER_BLOB = "2a5eb4957424fe81212e762ed2225f86ec890ca4"
EXPECTED_U0P_AUDIT_BLOB = "abc5ac0901a0ca09bbac896d257d0ff40d9a0c66"
EXPECTED_U0P_MANIFEST_SHA256 = "a2dd0ec55a08002b3336d46c0bf5c3757ec05b7b221748dfe586937cf53a5059"
EXPECTED_U0P_PATCH_SHA256 = "ce14c12d55c6c6297dce1f52355adc915d3601ddb207feaeec012536a53ce17b"
EXPECTED_U0P_AUDIT_SHA256 = "a89fef6091a5c6ec9c390d73b8ac74f4ff64cad7a98d04321ef7cc3eaba36fe8"
EXPECTED_U0P_INITRAMFS_SHA256 = "10dead55576115f626ff174f01aa28474e05305427e401235f09639deba56e4a"
EXPECTED_U0P_CANDIDATE_SHA256 = "59f22a3d27eb63cd8d616e7e55e0ecd16fe91a16fbe8e68759d724d2405d5264"
EXPECTED_U0P_INIT2_SHA256 = "f4e9433e97320eec4572a611d83afef70fd5866e9b61c319158a434b20bf8c93"
EXPECTED_U0P_EMBEDDED_SSHD_SHA256 = "52ddad2085f6364b8a94f21dfd1d092f24c808a43b2fd28c16386c284bf94ea6"
INIT_TARGET = "init_2nd.sh"
WATCHDOG_TARGET = "hooks/01-a33x-watchdog.sh"
MODULES = 67
PORT = 2222
PHONE_ADDRESS = "172.16.42.1/24"
TRACE_PATH = "/var/log/a33x-u0q-emergency-ssh.log"
INHERITED_TRACE_PATH = "/var/log/a33x-u0o-real-boot-sshd.log"
KEY_RELATIVE = Path("build/keys/a33x-u0q-emergency-ed25519")
MARKER_PREFIX = "a33x-u0q-emergency-ssh"
ANCHOR = 'u0o_pre_trace 6 "stage=switch-root-ready"\n'


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0p = load("a33_u0q_parent_builder", U0P_BUILDER_PATH)
u0p_audit = load("a33_u0q_parent_audit", U0P_AUDIT_PATH)
v2 = u0p.v2


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


def run_text(args: list[str]) -> str:
    completed = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        refuse(
            f"command failed rc={completed.returncode}: {args!r}: "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def normalize_public_key(text: str) -> str:
    fields = text.strip().split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        refuse("emergency public key must be one ssh-ed25519 key")
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,3}", fields[1]):
        refuse("emergency public key payload is malformed")
    return f"{fields[0]} {fields[1]} a33x-u0q-emergency"


def ensure_client_key(root: Path, explicit: Path | None) -> dict[str, object]:
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        refuse("ssh-keygen is unavailable")
    private = (
        explicit.expanduser().resolve()
        if explicit is not None
        else (root / KEY_RELATIVE).resolve()
    )
    public = Path(str(private) + ".pub")
    private.parent.mkdir(parents=True, exist_ok=True)
    if private.exists() != public.exists():
        refuse("emergency client keypair is incomplete")
    if not private.exists():
        subprocess.run(
            [
                ssh_keygen,
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "a33x-u0q-emergency",
                "-f",
                str(private),
            ],
            check=True,
        )
    private.chmod(0o600)
    public.chmod(0o644)
    derived = normalize_public_key(run_text([ssh_keygen, "-y", "-f", str(private)]))
    declared = normalize_public_key(public.read_text(encoding="utf-8"))
    if derived.split()[:2] != declared.split()[:2]:
        refuse("emergency public key does not match its private key")
    public.write_text(declared + "\n", encoding="utf-8")
    fingerprint = run_text([ssh_keygen, "-lf", str(public), "-E", "sha256"])
    return {
        "private": private,
        "public": public,
        "public_text": declared,
        "private_sha256": v2.sha_file(private),
        "public_sha256": v2.sha_file(public),
        "fingerprint": fingerprint,
    }


def network_script() -> str:
    return r'''set -u
exec 8>>/var/log/a33x-u0q-emergency-ssh.log
u0q_net_log()
{
    u0q_now="$(cut -d' ' -f1 /proc/uptime 2>/dev/null || true)"
    printf 'uptime=%s source=emergency-network %s\n' "${u0q_now:-unknown}" "$*" >&8
    sync 2>/dev/null || true
}
u0q_net_log "event=network-helper-started pid=$$"
u0q_wait=0
while [ "$u0q_wait" -le 150 ]; do
    u0q_iface=""
    for u0q_name in usb0 rndis0 eth0; do
        if [ -e "/sys/class/net/$u0q_name" ]; then
            u0q_iface="$u0q_name"
            break
        fi
    done
    if [ -z "$u0q_iface" ]; then
        for u0q_path in /sys/class/net/*; do
            [ -e "$u0q_path" ] || continue
            u0q_name="${u0q_path##*/}"
            [ "$u0q_name" != lo ] || continue
            u0q_device="$(/bin/busybox readlink -f "$u0q_path/device" 2>/dev/null || true)"
            case "$u0q_name:$u0q_device" in
                *usb*|*USB*|*rndis*|*RNDIS*|*gadget*|*dwc3*)
                    u0q_iface="$u0q_name"
                    break
                    ;;
            esac
        done
    fi
    if [ -n "$u0q_iface" ]; then
        /bin/busybox ip link set dev "$u0q_iface" up >&8 2>&1 || true
        /bin/busybox ip address replace 172.16.42.1/24 dev "$u0q_iface" >&8 2>&1 || \
            /bin/busybox ip address add 172.16.42.1/24 dev "$u0q_iface" >&8 2>&1 || true
        if /bin/busybox ip -o -4 address show dev "$u0q_iface" 2>/dev/null | \
            /bin/busybox grep -Fq '172.16.42.1/24'; then
            u0q_net_log "event=network-configured interface=$u0q_iface address=172.16.42.1/24 wait=$u0q_wait"
            /bin/busybox ip -o address show dev "$u0q_iface" >&8 2>&1 || true
            exit 0
        fi
        u0q_net_log "event=network-config-failed interface=$u0q_iface wait=$u0q_wait"
    elif [ $((u0q_wait % 10)) -eq 0 ]; then
        u0q_net_log "event=network-wait wait=$u0q_wait interfaces=$(/bin/busybox ls /sys/class/net 2>/dev/null | /bin/busybox tr '\n' ',' || true)"
    fi
    /bin/busybox sleep 1
    u0q_wait=$((u0q_wait + 1))
done
u0q_net_log "error=network-interface-timeout seconds=150"
exit 1
'''


def sshd_args() -> list[str]:
    return [
        "/usr/sbin/sshd",
        "-D",
        "-e",
        "-f",
        "/dev/null",
        "-h",
        "/etc/ssh/ssh_host_ed25519_key",
        "-o",
        f"Port={PORT}",
        "-o",
        "ListenAddress=0.0.0.0",
        "-o",
        "UsePAM=no",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "PubkeyAuthentication=yes",
        "-o",
        "AuthenticationMethods=publickey",
        "-o",
        "PermitRootLogin=prohibit-password",
        "-o",
        "AllowUsers=root",
        "-o",
        "AuthorizedKeysFile=none",
        "-o",
        "AuthorizedKeysCommand=/bin/echo __U0Q_PUBLIC_KEY__",
        "-o",
        "AuthorizedKeysCommandUser=root",
        "-o",
        "PidFile=none",
        "-o",
        "StrictModes=no",
        "-o",
        "PrintMotd=no",
        "-o",
        "PrintLastLog=no",
        "-o",
        "LogLevel=DEBUG3",
    ]


def shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(value) for value in args)


def emergency_block(public_key: str) -> str:
    network_delimiter = "__A33X_U0Q_NETWORK_HELPER_EOF__"
    net = network_script()
    if network_delimiter in net:
        refuse("network helper collides with heredoc delimiter")
    daemon = [value.replace("__U0Q_PUBLIC_KEY__", public_key) for value in sshd_args()]
    validate = daemon.copy()
    validate[1] = "-t"
    daemon_command = shell_join(daemon)
    validate_command = shell_join(validate)
    return f'''\n# U0q emergency SSH. Both long-lived children exec through chroot immediately,
# so they do not retain the old initramfs root across switch_root.
U0Q_TRACE=/sysroot{TRACE_PATH}
U0Q_PORT={PORT}

u0q_refuse()
{{
    u0q_now="$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)"
    printf 'uptime=%s source=initramfs candidate=U0q-emergency-ssh error=%s\\n' \
        "${{u0q_now:-unknown}}" "$1" >> "$U0Q_TRACE" 2>/dev/null || true
    sync 2>/dev/null || true
    printf '<3>{MARKER_PREFIX}: error=%s\\n' "$1" > /dev/kmsg 2>/dev/null || true
    while true; do sleep 3600; done
}}

[ -d /sysroot/var/log ] || u0q_refuse missing-var-log
[ -x /sysroot/usr/sbin/sshd ] || u0q_refuse missing-rootfs-sshd
[ -x /sysroot/bin/sh ] || u0q_refuse missing-rootfs-shell
[ -f /sysroot/etc/ssh/ssh_host_ed25519_key ] || u0q_refuse missing-ed25519-host-key

: > "$U0Q_TRACE" || u0q_refuse trace-create-failed
/bin/busybox chmod 0600 "$U0Q_TRACE" || u0q_refuse trace-chmod-failed
/bin/busybox chown 0:0 "$U0Q_TRACE" || u0q_refuse trace-chown-failed
u0q_now="$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)"
printf 'uptime=%s source=initramfs candidate=U0q-emergency-ssh stage=trace-open port=%s\\n' \
    "${{u0q_now:-unknown}}" "$U0Q_PORT" >> "$U0Q_TRACE"
sync 2>/dev/null || true

(
    exec 8>>"$U0Q_TRACE"
    exec /bin/busybox chroot /sysroot /bin/sh -s <<'{network_delimiter}'
{net.rstrip()}
{network_delimiter}
) &
U0Q_NETWORK_PID=$!
printf 'uptime=%s source=initramfs event=network-helper-spawned pid=%s\\n' \
    "${{u0q_now:-unknown}}" "$U0Q_NETWORK_PID" >> "$U0Q_TRACE"

printf 'uptime=%s source=emergency-sshd event=config-test-start port={PORT}\\n' \
    "$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)" >> "$U0Q_TRACE"
/bin/busybox chroot /sysroot {validate_command} >> "$U0Q_TRACE" 2>&1 || \
    u0q_refuse emergency-sshd-config-test-failed
printf 'uptime=%s source=emergency-sshd event=config-test-passed port={PORT}\\n' \
    "$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)" >> "$U0Q_TRACE"
sync 2>/dev/null || true
(
    exec 9>>"$U0Q_TRACE"
    exec /bin/busybox chroot /sysroot {daemon_command} >&9 2>&1
) &
U0Q_SSHD_PID=$!
printf 'uptime=%s source=initramfs event=sshd-helper-spawned pid=%s port=%s\\n' \
    "${{u0q_now:-unknown}}" "$U0Q_SSHD_PID" "$U0Q_PORT" >> "$U0Q_TRACE"
sync 2>/dev/null || true
printf '<6>{MARKER_PREFIX}: stage=helpers-spawned sshd=%s network=%s port=%s\\n' \
    "$U0Q_SSHD_PID" "$U0Q_NETWORK_PID" "$U0Q_PORT" > /dev/kmsg 2>/dev/null || true
unset U0Q_TRACE U0Q_PORT U0Q_NETWORK_PID U0Q_SSHD_PID u0q_now
'''


def patch_init_second(original: str, public_key: str) -> str:
    if v2.sha_bytes(original.encode()) != EXPECTED_U0P_INIT2_SHA256:
        refuse("exact U0p init_2nd.sh hash mismatch")
    if MARKER_PREFIX in original or TRACE_PATH in original:
        refuse("U0q emergency SSH is already present")
    if original.count(ANCHOR) != 1:
        refuse("exact U0p switch-root-ready anchor is absent or duplicated")
    if original.count('exec switch_root /sysroot "$init"') != 1:
        refuse("exact switch_root execution is absent or duplicated")
    before_embedded = u0p.embedded_sshd_bytes(original)
    if v2.sha_bytes(before_embedded) != EXPECTED_U0P_EMBEDDED_SSHD_SHA256:
        refuse("exact U0p embedded OpenRC sshd instrumentation changed")

    addition = emergency_block(public_key)
    patched = original.replace(ANCHOR, ANCHOR + addition, 1)
    if u0p.embedded_sshd_bytes(patched) != before_embedded:
        refuse("U0q changed inherited OpenRC sshd instrumentation")
    if patched.count(f"Port={PORT}") != 2:
        refuse("U0q emergency SSH port must appear in test and daemon commands")
    if re.search(r"(?:Port=|port=| -p )22(?:\D|$)", addition):
        refuse("U0q emergency channel must not claim the normal SSH port")
    order = (
        patched.index(ANCHOR.strip()),
        patched.index("candidate=U0q-emergency-ssh stage=trace-open"),
        patched.index("event=sshd-helper-spawned"),
        patched.index('exec switch_root /sysroot "$init"'),
    )
    if tuple(sorted(order)) != order:
        refuse("U0q helpers are not ordered before switch_root")
    forbidden = (
        'rm -rf "/sysroot"',
        "mount -o remount,rw /sysroot",
        "sed -i /sysroot",
        "> /sysroot/etc/",
        "dd if=",
        "mkfs",
        "wipefs",
        "PasswordAuthentication=yes",
        "KbdInteractiveAuthentication=yes",
        "UsePAM=yes",
        "PermitRootLogin=yes",
    )
    for token in forbidden:
        if token in addition:
            refuse(f"unsafe U0q operation entered generated payload: {token}")
    return patched


def assert_only_init_changed(before, after) -> None:
    if len(before.entries) != len(after.entries) or before.tail != after.tail:
        refuse("U0q changed CPIO entry count or trailer tail")
    changed: set[str] = set()
    for old, new in zip(before.entries, after.entries, strict=True):
        old_meta = (old.name, old.mode, old.nlink, old.ino, old.devmajor, old.devminor)
        new_meta = (new.name, new.mode, new.nlink, new.ino, new.devmajor, new.devminor)
        if old_meta != new_meta:
            refuse(f"U0q changed CPIO metadata for {old.name}")
        if v2.sha_bytes(old.data) != v2.sha_bytes(new.data):
            changed.add(old.normalized)
    if changed != {INIT_TARGET}:
        refuse(f"unexpected U0q initramfs payload delta: {sorted(changed)}")


def validate_parent(root: Path, repo: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    for path, expected in (
        (U0P_BUILDER_PATH, EXPECTED_U0P_BUILDER_BLOB),
        (U0P_AUDIT_PATH, EXPECTED_U0P_AUDIT_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            refuse(
                f"checked-in U0q dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )
    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0p-corrected-sshd-source-hash-manifest.txt"
    patch_path = root / "build/u0p-corrected-sshd-source-hash-patch.txt"
    audit_path = root / "build/a33-u0p-candidate-audit.txt"
    initramfs = root / "export-u0p-corrected-sshd-source-hash/initramfs"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0p-corrected-sshd-source-hash-recovery.img"
    for path in (manifest_path, patch_path, audit_path, initramfs, candidate):
        if not path.is_file():
            refuse(f"missing exact U0p parent evidence: {path}")
    for path, expected in (
        (manifest_path, EXPECTED_U0P_MANIFEST_SHA256),
        (patch_path, EXPECTED_U0P_PATCH_SHA256),
        (audit_path, EXPECTED_U0P_AUDIT_SHA256),
        (initramfs, EXPECTED_U0P_INITRAMFS_SHA256),
        (candidate, EXPECTED_U0P_CANDIDATE_SHA256),
    ):
        actual = v2.sha_file(path)
        if actual != expected:
            refuse(
                f"exact U0p parent artifact changed: path={path} actual={actual} expected={expected}"
            )
    manifest = v2.kv(manifest_path)
    v2.require(
        manifest,
        {
            "candidate": "U0p-corrected-sshd-source-hash",
            "u0p_initramfs_sha256": EXPECTED_U0P_INITRAMFS_SHA256,
            "recovery_sha256": EXPECTED_U0P_CANDIDATE_SHA256,
            "corrected_instrumented_sshd_sha256": EXPECTED_U0P_EMBEDDED_SSHD_SHA256,
            "u0o_watchdog_hook_preserved": "yes",
            "build_status": "passed",
        },
        "U0p parent manifest",
    )
    audit = v2.kv(audit_path)
    v2.require(
        audit,
        {
            "candidate_sha256": EXPECTED_U0P_CANDIDATE_SHA256,
            "u0o_watchdog_hook_byte_identical": "yes",
            "embedded_instrumented_sshd_bytes_identical": "yes",
            "runtime_source_hash_contract": "passed",
            "audit_status": "passed",
        },
        "U0p parent audit",
    )
    return manifest_path, initramfs, candidate, manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build U0q from exact U0p with a chrooted emergency OpenSSH server "
            "on port 2222 and independent USB-network address helper"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--client-key", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    parent_manifest_path, parent_initramfs, _, _ = validate_parent(root, repo)
    key = ensure_client_key(root, args.client_key)
    try:
        before = v2.Archive.parse(gzip.decompress(parent_initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        refuse(f"cannot parse exact U0p initramfs: {exc}")
    original_init = before.one(INIT_TARGET).data.decode("utf-8", errors="strict")
    patched_init = patch_init_second(original_init, str(key["public_text"]))
    payload = before.replace(INIT_TARGET, patched_init.encode())
    after = v2.Archive.parse(payload)
    assert_only_init_changed(before, after)
    if before.one(WATCHDOG_TARGET).data != after.one(WATCHDOG_TARGET).data:
        refuse("U0q changed the proven U0p watchdog hook")
    if v2.count_modules(before) != MODULES or v2.count_modules(after) != MODULES:
        refuse("U0q module count changed or is not 67")

    output_initramfs = root / "export-u0q-emergency-ssh/initramfs"
    inspect_dir = root / "build/u0q-emergency-ssh-inspection"
    patch_report = root / "build/u0q-emergency-ssh-patch.txt"
    recovery_output = root / "build/pmos-debug-recovery-u0q-emergency-ssh"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-recovery.img"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-manifest.txt"
    for path in (output_initramfs, patch_report, candidate, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    inspect_dir.mkdir(parents=True, exist_ok=True)
    (inspect_dir / "u0p-init_2nd.sh").write_text(original_init, encoding="utf-8")
    syntax_path = inspect_dir / "u0q-init_2nd.sh"
    syntax_path.write_text(patched_init, encoding="utf-8")
    subprocess.run(["sh", "-n", str(syntax_path)], check=True)
    (inspect_dir / "embedded-emergency-public-key.pub").write_text(
        str(key["public_text"]) + "\n", encoding="utf-8"
    )

    output_initramfs.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))
    roundtrip = v2.Archive.parse(gzip.decompress(output_initramfs.read_bytes()))
    if roundtrip.one(INIT_TARGET).data != patched_init.encode() or roundtrip.tail != before.tail:
        refuse("written U0q initramfs did not round-trip")

    commit = run_text(["git", "-C", str(repo), "rev-parse", "HEAD"])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        refuse("cannot resolve repository commit")
    created = run_text(["date", "-Ins"])
    common: list[tuple[str, object]] = [
        ("created", created),
        ("linuxa33_commit", commit),
        ("implementation_language", "python3"),
        ("functional_base", "U0p-corrected-sshd-source-hash"),
        ("u0p_manifest", parent_manifest_path),
        ("u0p_manifest_sha256", v2.sha_file(parent_manifest_path)),
        ("u0p_initramfs", parent_initramfs),
        ("u0p_initramfs_sha256", v2.sha_file(parent_initramfs)),
        ("u0q_initramfs", output_initramfs),
        ("u0q_initramfs_sha256", v2.sha_file(output_initramfs)),
        ("cpio_entry_count", len(before.entries)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_payload_delta", INIT_TARGET),
        ("shell_delta", "chrooted-emergency-sshd-and-usb-network-helper-before-switch-root"),
        ("normal_openrc_sshd_instrumentation_preserved", "yes"),
        ("emergency_sshd_port", PORT),
        ("emergency_sshd_user", "root"),
        ("emergency_sshd_auth", "dedicated-ed25519-public-key-only"),
        ("emergency_sshd_pam", "disabled"),
        ("emergency_sshd_password_auth", "disabled"),
        ("emergency_sshd_process_root", "chroot-/sysroot"),
        ("emergency_network_address", PHONE_ADDRESS),
        ("emergency_network_wait_seconds", 150),
        ("emergency_trace_path", TRACE_PATH),
        ("emergency_trace_mode", "0600"),
        ("inherited_trace_path", INHERITED_TRACE_PATH),
        ("rootfs_persistent_delta_from_u0p", TRACE_PATH),
        ("client_private_key", key["private"]),
        ("client_private_key_sha256", key["private_sha256"]),
        ("client_public_key", key["public"]),
        ("client_public_key_sha256", key["public_sha256"]),
        ("client_public_key_fingerprint", key["fingerprint"]),
        ("embedded_public_key_sha256", v2.sha_bytes((str(key["public_text"]) + "\n").encode())),
        ("original_init_2nd_sha256", v2.sha_bytes(before.one(INIT_TARGET).data)),
        ("patched_init_2nd_sha256", v2.sha_bytes(patched_init.encode())),
        ("u0p_watchdog_hook_preserved", "yes"),
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
        [("operation", "python-u0q-emergency-ssh")]
        + common
        + [("patch_status", "passed")],
    )

    recovery = v2.build_recovery(root, repo, output_initramfs, recovery_output)
    shutil.copy2(recovery, candidate)
    if candidate.stat().st_size != 100663296:
        refuse(f"unexpected U0q recovery size: {candidate.stat().st_size}")
    v2.write_report(
        manifest,
        [
            ("candidate", "U0q-emergency-ssh"),
            ("functional_delta", "independent-live-root-shell-on-port-2222"),
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
    print(f"emergency_sshd_port={PORT}")
    print(f"emergency_client_private_key={key['private']}")
    print(f"emergency_client_public_key_fingerprint={key['fingerprint']}")
    print(f"emergency_trace_path={TRACE_PATH}")
    print(f"rootfs_persistent_delta_from_u0p={TRACE_PATH}")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        Refusal,
        u0p.Refusal,
        u0p.u0o_v2.base.Refusal,
        v2.Refusal,
        v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"REFUSING U0q: {exc}", file=sys.stderr)
        raise SystemExit(1)
