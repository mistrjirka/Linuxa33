#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "make-u0q-emergency-ssh.py"
EXPECTED_BASE_BLOB = "fa662b03cf3a4e4c9166ebc9fa0a177dc12dbdb4"
RUNTIME_REVISION = "2"
PRIVSEP_PATH = "/run/sshd"
NETWORK_READY_PATH = "/run/a33x-u0q-network-ready"
READY_TIMEOUT_SECONDS = 150
FIREWALL_COMMENT = "a33x-u0q-emergency-2222"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load("a33_u0q_v2_base", BASE_PATH)
ORIGINAL_NETWORK_SCRIPT = base.network_script
ORIGINAL_EMERGENCY_BLOCK = base.emergency_block


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


def selected_paths() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args, _ = parser.parse_known_args()
    return args.root.expanduser().resolve(), args.repo.expanduser().resolve()


def network_script() -> str:
    return rf'''set -u
exec 8>>/var/log/a33x-u0q-emergency-ssh.log
u0q_net_log()
{{
    u0q_now="$(cut -d' ' -f1 /proc/uptime 2>/dev/null || true)"
    printf 'uptime=%s source=emergency-network %s\n' "${{u0q_now:-unknown}}" "$*" >&8
    sync 2>/dev/null || true
}}
u0q_net_log "event=network-helper-started pid=$$ revision={RUNTIME_REVISION}"
u0q_wait=0
u0q_iface=""
while [ "$u0q_wait" -le {READY_TIMEOUT_SECONDS} ]; do
    for u0q_name in usb0 rndis0 eth0; do
        if [ -e "/sys/class/net/$u0q_name" ]; then
            u0q_iface="$u0q_name"
            break
        fi
    done
    if [ -z "$u0q_iface" ]; then
        for u0q_path in /sys/class/net/*; do
            [ -e "$u0q_path" ] || continue
            u0q_name="${{u0q_path##*/}}"
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
            printf '%s\n' "$u0q_iface" > {NETWORK_READY_PATH} || {{
                u0q_net_log "error=network-ready-marker-write-failed path={NETWORK_READY_PATH}"
                exit 1
            }}
            /bin/busybox chmod 0600 {NETWORK_READY_PATH} 2>/dev/null || true
            sync 2>/dev/null || true
            u0q_net_log "event=network-ready-marker-written path={NETWORK_READY_PATH} interface=$u0q_iface"
            break
        fi
        u0q_net_log "event=network-config-failed interface=$u0q_iface wait=$u0q_wait"
        u0q_iface=""
    elif [ $((u0q_wait % 10)) -eq 0 ]; then
        u0q_net_log "event=network-wait wait=$u0q_wait interfaces=$(/bin/busybox ls /sys/class/net 2>/dev/null | /bin/busybox tr '\n' ',' || true)"
    fi
    /bin/busybox sleep 1
    u0q_wait=$((u0q_wait + 1))
done
if [ -z "$u0q_iface" ]; then
    u0q_net_log "error=network-interface-timeout seconds={READY_TIMEOUT_SECONDS}"
    exit 1
fi

# OpenRC's nftables policy is drop-by-default and its persistent SSH rule covers
# only port 22. Keep an explicitly diagnostic port-2222 rule present in the
# live ruleset without editing /etc/nftables* or any other persistent file.
u0q_firewall_iteration=0
while true; do
    if command -v nft >/dev/null 2>&1 && \
       nft list chain inet filter input >/dev/null 2>&1; then
        if ! nft -a list chain inet filter input 2>/dev/null | \
             /bin/busybox grep -Fq '{FIREWALL_COMMENT}'; then
            if nft insert rule inet filter input tcp dport 2222 accept \
                comment '{FIREWALL_COMMENT}' >&8 2>&1; then
                u0q_net_log "event=runtime-firewall-rule-added family=inet table=filter chain=input port=2222"
            else
                u0q_net_log "event=runtime-firewall-rule-add-failed port=2222"
            fi
        elif [ $((u0q_firewall_iteration % 30)) -eq 0 ]; then
            u0q_net_log "event=runtime-firewall-rule-present port=2222"
        fi
    elif [ $((u0q_firewall_iteration % 10)) -eq 0 ]; then
        u0q_net_log "event=runtime-firewall-table-wait iteration=$u0q_firewall_iteration"
    fi
    u0q_firewall_iteration=$((u0q_firewall_iteration + 1))
    /bin/busybox sleep 1
done
'''


def emergency_block(public_key: str) -> str:
    original_network = base.network_script
    try:
        base.network_script = network_script
        block = ORIGINAL_EMERGENCY_BLOCK(public_key)
    finally:
        base.network_script = original_network

    preparation_anchor = "sync 2>/dev/null || true\n\n(\n    exec 8>>\"$U0Q_TRACE\""
    if block.count(preparation_anchor) != 1:
        refuse("U0q v2 runtime-preparation anchor is absent or duplicated")
    preparation = rf'''sync 2>/dev/null || true

# sshd normally receives this volatile privilege-separation directory from its
# OpenRC service setup. U0q starts before OpenRC, so require /run to already be
# a distinct mount and create only volatile state beneath it.
if ! /bin/busybox awk '$2 == "/sysroot/run" {{ found=1 }} END {{ exit found ? 0 : 1 }}' /proc/mounts; then
    u0q_refuse run-is-not-a-mounted-runtime-filesystem
fi
/bin/busybox mkdir -p /sysroot{PRIVSEP_PATH} || u0q_refuse run-sshd-create-failed
/bin/busybox chmod 0755 /sysroot{PRIVSEP_PATH} || u0q_refuse run-sshd-chmod-failed
/bin/busybox chown 0:0 /sysroot{PRIVSEP_PATH} || u0q_refuse run-sshd-chown-failed
/bin/busybox rm -f /sysroot{NETWORK_READY_PATH} || u0q_refuse stale-network-marker-remove-failed
printf 'uptime=%s source=initramfs event=runtime-directory-ready path={PRIVSEP_PATH} backing=mounted-run revision={RUNTIME_REVISION}\n' \
    "$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)" >> "$U0Q_TRACE"
sync 2>/dev/null || true

(
    exec 8>>"$U0Q_TRACE"'''
    block = block.replace(preparation_anchor, preparation, 1)

    readiness_anchor = (
        "sync 2>/dev/null || true\n"
        f"printf '<6>{base.MARKER_PREFIX}: stage=helpers-spawned"
    )
    if block.count(readiness_anchor) != 1:
        refuse("U0q v2 readiness-gate anchor is absent or duplicated")
    readiness = rf'''sync 2>/dev/null || true

# U0o proved that the NCM path can appear while initramfs remains alive, whereas
# U0p switched roots before the interface became reachable. Do not switch roots
# until the independent network helper has configured the address and sshd is
# confirmed alive and listening on port 2222.
U0Q_READY_WAIT=0
U0Q_READY=no
while [ "$U0Q_READY_WAIT" -le {READY_TIMEOUT_SECONDS} ]; do
    /bin/busybox kill -0 "$U0Q_SSHD_PID" 2>/dev/null || \
        u0q_refuse emergency-sshd-exited-before-ready
    U0Q_NETWORK_READY=no
    [ -s /sysroot{NETWORK_READY_PATH} ] && U0Q_NETWORK_READY=yes
    U0Q_LISTENER_READY="$(
        /bin/busybox awk '$2 ~ /:08AE$/ && $4 == "0A" {{ found=1 }} END {{ print found ? "yes" : "no" }}' \
            /proc/net/tcp /proc/net/tcp6 2>/dev/null || true
    )"
    if [ "$U0Q_NETWORK_READY" = yes ] && [ "$U0Q_LISTENER_READY" = yes ]; then
        U0Q_READY=yes
        U0Q_READY_INTERFACE="$(/bin/busybox cat /sysroot{NETWORK_READY_PATH} 2>/dev/null || true)"
        printf 'uptime=%s source=initramfs event=pre-switch-root-ready interface=%s listener=yes port=2222 wait=%s\n' \
            "$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)" \
            "${{U0Q_READY_INTERFACE:-unknown}}" "$U0Q_READY_WAIT" >> "$U0Q_TRACE"
        sync 2>/dev/null || true
        break
    fi
    if [ $((U0Q_READY_WAIT % 10)) -eq 0 ]; then
        printf 'uptime=%s source=initramfs event=pre-switch-root-wait network=%s listener=%s wait=%s\n' \
            "$(/bin/busybox cut -d' ' -f1 /proc/uptime 2>/dev/null || true)" \
            "$U0Q_NETWORK_READY" "${{U0Q_LISTENER_READY:-unknown}}" "$U0Q_READY_WAIT" >> "$U0Q_TRACE"
        sync 2>/dev/null || true
    fi
    /bin/busybox sleep 1
    U0Q_READY_WAIT=$((U0Q_READY_WAIT + 1))
done
[ "$U0Q_READY" = yes ] || u0q_refuse emergency-channel-readiness-timeout

printf '<6>{base.MARKER_PREFIX}: stage=helpers-spawned'''
    block = block.replace(readiness_anchor, readiness, 1)

    required = (
        'awk \'$2 == "/sysroot/run"',
        f"mkdir -p /sysroot{PRIVSEP_PATH}",
        f"event=runtime-directory-ready path={PRIVSEP_PATH}",
        f"event=network-ready-marker-written path={NETWORK_READY_PATH}",
        "event=pre-switch-root-wait",
        "event=pre-switch-root-ready",
        "emergency-channel-readiness-timeout",
        "event=runtime-firewall-rule-added",
        FIREWALL_COMMENT,
        "nft insert rule inet filter input tcp dport 2222 accept",
    )
    for token in required:
        if token not in block:
            refuse(f"U0q v2 runtime token missing: {token}")
    forbidden = (
        "/etc/nftables.d/",
        "/etc/nftables.nft",
        "mount -o remount,rw",
        "umount -l",
        "sed -i",
        "rm -rf /sysroot",
    )
    for token in forbidden:
        if token in block:
            refuse(f"persistent or unsafe U0q v2 operation entered payload: {token}")
    return block


def append_unique(path: Path, pairs: list[tuple[str, str]]) -> None:
    text = path.read_text(encoding="utf-8")
    for key, value in pairs:
        if re.search(rf"(?m)^{re.escape(key)}=", text):
            refuse(f"U0q v2 field already exists in {path.name}: {key}")
        text += f"{key}={value}\n"
    path.write_text(text, encoding="utf-8")


def replace_single_field(path: Path, key: str, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = rf"(?m)^{re.escape(key)}=.*$"
    if len(re.findall(pattern, text)) != 1:
        refuse(f"expected one {key} field in {path}")
    path.write_text(re.sub(pattern, f"{key}={value}", text), encoding="utf-8")


def validate_generated_payload(root: Path) -> None:
    initramfs = root / "export-u0q-emergency-ssh/initramfs"
    if not initramfs.is_file():
        refuse(f"missing U0q v2 initramfs: {initramfs}")
    try:
        archive = base.v2.Archive.parse(gzip.decompress(initramfs.read_bytes()))
    except (OSError, base.v2.CpioError) as exc:
        refuse(f"cannot parse generated U0q v2 initramfs: {exc}")
    init_text = archive.one(base.INIT_TARGET).data.decode("utf-8", errors="strict")
    unique_tokens = (
        "run-is-not-a-mounted-runtime-filesystem",
        f"event=runtime-directory-ready path={PRIVSEP_PATH}",
        f"event=network-ready-marker-written path={NETWORK_READY_PATH}",
        "event=pre-switch-root-ready",
        "emergency-channel-readiness-timeout",
        "event=runtime-firewall-rule-added",
        "nft insert rule inet filter input tcp dport 2222 accept",
    )
    for token in unique_tokens:
        if init_text.count(token) != 1:
            refuse(f"generated U0q v2 token missing or duplicated: {token}")
    if init_text.count(FIREWALL_COMMENT) != 2:
        refuse("generated U0q v2 firewall marker must occur in detection and rule")
    order = (
        init_text.index("candidate=U0q-emergency-ssh stage=trace-open"),
        init_text.index(f"event=runtime-directory-ready path={PRIVSEP_PATH}"),
        init_text.index("event=network-helper-spawned"),
        init_text.index("event=sshd-helper-spawned"),
        init_text.index("event=pre-switch-root-ready"),
        init_text.index('exec switch_root /sysroot "$init"'),
    )
    if tuple(sorted(order)) != order:
        refuse("generated U0q v2 readiness gate is not before switch_root")


def main() -> int:
    root, repo = selected_paths()
    if git_blob(repo, BASE_PATH) != EXPECTED_BASE_BLOB:
        refuse("checked-in U0q base builder changed")

    original_network = base.network_script
    original_emergency = base.emergency_block
    try:
        base.network_script = network_script
        base.emergency_block = emergency_block
        result = base.main()
    finally:
        base.network_script = original_network
        base.emergency_block = original_emergency
    if result != 0:
        return result

    validate_generated_payload(root)
    patch = root / "build/u0q-emergency-ssh-patch.txt"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0q-emergency-ssh-manifest.txt"
    fields = [
        ("u0q_runtime_revision", RUNTIME_REVISION),
        ("emergency_runtime_mount_required", "/run"),
        ("emergency_privsep_path", PRIVSEP_PATH),
        ("emergency_privsep_backing", "preexisting-mounted-run"),
        ("emergency_pre_switch_root_gate", "network-address-and-port-2222-listener"),
        ("emergency_pre_switch_root_timeout_seconds", str(READY_TIMEOUT_SECONDS)),
        ("emergency_network_ready_path", NETWORK_READY_PATH),
        ("emergency_firewall_policy", "runtime-nft-monitor"),
        ("emergency_firewall_rule_comment", FIREWALL_COMMENT),
        ("emergency_firewall_persistent_delta", "none"),
    ]
    append_unique(patch, fields)
    replace_single_field(manifest, "patch_report_sha256", base.v2.sha_file(patch))
    append_unique(manifest, fields)
    print(f"u0q_runtime_revision={RUNTIME_REVISION}")
    print(f"emergency_privsep_path={PRIVSEP_PATH}")
    print("emergency_privsep_backing=preexisting-mounted-run")
    print("emergency_pre_switch_root_gate=network-address-and-port-2222-listener")
    print(f"emergency_pre_switch_root_timeout_seconds={READY_TIMEOUT_SECONDS}")
    print(f"emergency_network_ready_path={NETWORK_READY_PATH}")
    print("emergency_firewall_policy=runtime-nft-monitor")
    print(f"emergency_firewall_rule_comment={FIREWALL_COMMENT}")
    print("emergency_firewall_persistent_delta=none")
    print(f"updated_patch_report_sha256={base.v2.sha_file(patch)}")
    print(f"updated_manifest_sha256={base.v2.sha_file(manifest)}")
    print("u0q_v2_build_status=passed")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
