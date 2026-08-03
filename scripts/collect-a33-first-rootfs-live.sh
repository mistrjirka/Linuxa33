#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
RESULT_ROOT="${RESULT_ROOT:-$PORT_ROOT/build/runtime-results}"
SSH_TARGET="${SSH_TARGET:-jirka@172.16.42.1}"
OBSERVATION_DIR="${OBSERVATION_DIR:-}"
DEPLOYMENT_REPORT="$PORT_ROOT/build/a33-userdata-rootfs-deployment.txt"
EXPECTED_ROOT_DEVNAME="${EXPECTED_ROOT_DEVNAME:-sda36}"
EXPECTED_MARKER_RESOLVED="/dev/block/sda36"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$RESULT_ROOT/a33-first-rootfs-live-$TIMESTAMP"
ARCHIVE="$OUT.tar.gz"

for command in ssh sha256sum awk grep find sort tar date mkdir cp mktemp; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}

if [[ ! -f "$DEPLOYMENT_REPORT" || "$(value "$DEPLOYMENT_REPORT" deployment_status)" != passed ]]; then
    echo "REFUSING: successful userdata deployment report is missing" >&2
    exit 1
fi
EXPECTED_ROOT_UUID="$(value "$DEPLOYMENT_REPORT" filesystem_uuid)"
[[ -n "$EXPECTED_ROOT_UUID" ]] || {
    echo "REFUSING: deployment report has no filesystem UUID" >&2
    exit 1
}

if [[ -z "$OBSERVATION_DIR" ]]; then
    OBSERVATION_DIR="$(
        find "$RESULT_ROOT" -mindepth 1 -maxdepth 1 -type d \
            -name 'a33-first-rootfs-observation-*' -printf '%T@ %p\n' 2>/dev/null \
        | sort -nr \
        | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
    )"
fi
if [[ -z "$OBSERVATION_DIR" || ! -f "$OBSERVATION_DIR/summary.txt" ]]; then
    echo "REFUSING: first-rootfs observation result is missing" >&2
    exit 1
fi
if [[ "$(value "$OBSERVATION_DIR/summary.txt" observation_status)" != passed-rootfs-network-and-ssh-ready ]]; then
    echo "REFUSING: observation did not prove network and SSH readiness" >&2
    cat "$OBSERVATION_DIR/summary.txt" >&2
    exit 1
fi

mkdir -p "$OUT"
REMOTE_SCRIPT="$(mktemp)"
cleanup() { rm -f "$REMOTE_SCRIPT"; }
trap cleanup EXIT
cat > "$REMOTE_SCRIPT" <<'SH'
#!/bin/sh
set +e

section() {
    echo
    echo "===== $1 ====="
}

root_source="$(findmnt -n -o SOURCE / 2>/dev/null || awk '$2=="/" {print $1; exit}' /proc/mounts)"
root_fstype="$(findmnt -n -o FSTYPE / 2>/dev/null || true)"
root_majmin="$(findmnt -n -o MAJ:MIN / 2>/dev/null || true)"
root_sysfs=""
root_devname=""
if [ -n "$root_majmin" ] && [ -e "/sys/dev/block/$root_majmin" ]; then
    root_sysfs="$(readlink -f "/sys/dev/block/$root_majmin" 2>/dev/null || true)"
    root_devname="$(awk -F= '$1=="DEVNAME" {print $2; exit}' "/sys/dev/block/$root_majmin/uevent" 2>/dev/null || true)"
fi
root_resolved="$(readlink -f "$root_source" 2>/dev/null || true)"
root_probe="$root_source"
if [ -n "$root_devname" ] && [ -b "/dev/$root_devname" ]; then
    root_probe="/dev/$root_devname"
elif [ -b "$root_resolved" ]; then
    root_probe="$root_resolved"
fi

root_type="$root_fstype"
root_label="$(findmnt -n -o LABEL / 2>/dev/null || true)"
root_uuid="$(findmnt -n -o UUID / 2>/dev/null || true)"
if command -v lsblk >/dev/null 2>&1; then
    [ -n "$root_type" ] || root_type="$(lsblk -ndo FSTYPE "$root_probe" 2>/dev/null | head -n 1)"
    [ -n "$root_label" ] || root_label="$(lsblk -ndo LABEL "$root_probe" 2>/dev/null | head -n 1)"
    [ -n "$root_uuid" ] || root_uuid="$(lsblk -ndo UUID "$root_probe" 2>/dev/null | head -n 1)"
fi
if command -v blkid >/dev/null 2>&1; then
    [ -n "$root_type" ] || root_type="$(blkid -s TYPE -o value "$root_probe" 2>/dev/null || true)"
    [ -n "$root_label" ] || root_label="$(blkid -s LABEL -o value "$root_probe" 2>/dev/null || true)"
    [ -n "$root_uuid" ] || root_uuid="$(blkid -s UUID -o value "$root_probe" 2>/dev/null || true)"
fi

pid1_comm="$(cat /proc/1/comm 2>/dev/null || true)"
pid1_exe="$(readlink -f /proc/1/exe 2>/dev/null || true)"
sshd_status="stopped-or-unknown"
rc-service sshd status >/dev/null 2>&1 && sshd_status=started
networkmanager_status="stopped-or-unknown"
rc-service networkmanager status >/dev/null 2>&1 && networkmanager_status=started
usb_ipv4="$(ip -o -4 addr show 2>/dev/null | awk '$4 ~ /^172\.16\.42\.1\// {print $4; exit}')"
root_block_bytes="$(blockdev --getsize64 "$root_probe" 2>/dev/null || true)"
root_df_bytes="$(df -P -k / 2>/dev/null | awk 'NR==2 {print $2 * 1024; exit}')"
marker_target="$(awk -F= '$1=="target" {print $2; exit}' /etc/a33x-rootfs-target 2>/dev/null || true)"
marker_block="$(awk -F= '$1=="expected_block" {print $2; exit}' /etc/a33x-rootfs-target 2>/dev/null || true)"
marker_resolved="$(awk -F= '$1=="expected_resolved" {print $2; exit}' /etc/a33x-rootfs-target 2>/dev/null || true)"
marker_uuid="$(awk -F= '$1=="root_uuid" {print $2; exit}' /etc/a33x-rootfs-target 2>/dev/null || true)"

cat <<EOF
marker_pid1_comm=$pid1_comm
marker_pid1_exe=$pid1_exe
marker_root_source=$root_source
marker_root_resolved=$root_resolved
marker_root_majmin=$root_majmin
marker_root_sysfs=$root_sysfs
marker_root_devname=$root_devname
marker_root_type=$root_type
marker_root_label=$root_label
marker_root_uuid=$root_uuid
marker_root_block_bytes=$root_block_bytes
marker_root_df_bytes=$root_df_bytes
marker_sshd_status=$sshd_status
marker_networkmanager_status=$networkmanager_status
marker_usb_ipv4=$usb_ipv4
marker_deployment_target=$marker_target
marker_deployment_block=$marker_block
marker_deployment_resolved=$marker_resolved
marker_deployment_uuid=$marker_uuid
EOF

section identity
id
uname -a
cat /etc/os-release 2>/dev/null
cat /etc/a33x-rootfs-target 2>/dev/null

section pid1
ps -p 1 -o pid,ppid,user,stat,comm,args 2>&1 || true
cat /proc/1/status 2>&1 || true
cat /proc/cmdline 2>&1 || true

section mounts
cat /proc/mounts 2>&1 || true
findmnt 2>&1 || true
lsblk -o NAME,KNAME,MAJ:MIN,SIZE,RO,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS 2>&1 || lsblk 2>&1 || true
blkid 2>&1 || true
df -h 2>&1 || true

section openrc
rc-status -a 2>&1 || true
rc-update show -v 2>&1 || true
rc-service sshd status 2>&1 || true
rc-service networkmanager status 2>&1 || true

section networking
ip -br link 2>&1 || true
ip -br addr 2>&1 || true
ip route 2>&1 || true
ip neigh 2>&1 || true
ss -lntup 2>&1 || netstat -lntup 2>&1 || true
nmcli general status 2>&1 || true
nmcli device status 2>&1 || true
nmcli radio 2>&1 || true

section kernel
cat /proc/version 2>&1 || true
lsmod 2>&1 || true
dmesg 2>&1 || true

section usb
find /sys/class/udc -maxdepth 2 -type f -print -exec cat {} \; 2>&1 || true
find /sys/kernel/config/usb_gadget -maxdepth 4 -type f -print 2>&1 || true

section privacy
printf '%s\n' \
    'user_files=not-read' \
    'shadow=not-read' \
    'ssh_private_keys=not-read' \
    'authorized_keys=not-read' \
    'networkmanager_connection_contents=not-read' \
    'wifi_credentials=not-read'
SH
chmod 700 "$REMOTE_SCRIPT"

{
    echo "created=$(date -Ins)"
    echo "operation=collect-first-rootfs-live-over-ssh"
    echo "ssh_target=$SSH_TARGET"
    echo "observation_dir=$OBSERVATION_DIR"
    echo "deployment_report=$DEPLOYMENT_REPORT"
    echo "deployment_report_sha256=$(sha256sum "$DEPLOYMENT_REPORT" | awk '{print $1}')"
    echo "expected_root_uuid=$EXPECTED_ROOT_UUID"
    echo "expected_root_devname=$EXPECTED_ROOT_DEVNAME"
    echo "expected_marker_resolved=$EXPECTED_MARKER_RESOLVED"
    echo "privacy=user-content-and-credentials-excluded"
} | tee "$OUT/manifest.txt"

SSH_OPTIONS=(
    -o ConnectTimeout=15
    -o ServerAliveInterval=5
    -o ServerAliveCountMax=3
    -o StrictHostKeyChecking=accept-new
    -o UserKnownHostsFile="$PORT_ROOT/build/a33-first-rootfs-known-hosts"
)

ssh "${SSH_OPTIONS[@]}" "$SSH_TARGET" 'sh -s' \
    < "$REMOTE_SCRIPT" \
    > "$OUT/phone-live.txt" 2> "$OUT/ssh.stderr"

marker() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$OUT/phone-live.txt"
}

PID1_COMM="$(marker marker_pid1_comm)"
PID1_EXE="$(marker marker_pid1_exe)"
ROOT_SOURCE="$(marker marker_root_source)"
ROOT_RESOLVED="$(marker marker_root_resolved)"
ROOT_MAJMIN="$(marker marker_root_majmin)"
ROOT_SYSFS="$(marker marker_root_sysfs)"
ROOT_DEVNAME="$(marker marker_root_devname)"
ROOT_TYPE="$(marker marker_root_type)"
ROOT_LABEL="$(marker marker_root_label)"
ROOT_UUID="$(marker marker_root_uuid)"
ROOT_BLOCK_BYTES="$(marker marker_root_block_bytes)"
ROOT_DF_BYTES="$(marker marker_root_df_bytes)"
SSHD_STATUS="$(marker marker_sshd_status)"
NETWORKMANAGER_STATUS="$(marker marker_networkmanager_status)"
USB_IPV4="$(marker marker_usb_ipv4)"
MARKER_TARGET="$(marker marker_deployment_target)"
MARKER_BLOCK="$(marker marker_deployment_block)"
MARKER_RESOLVED="$(marker marker_deployment_resolved)"
MARKER_UUID="$(marker marker_deployment_uuid)"

CORE_STATUS=requires-manual-review
if [[ "$PID1_COMM" == init && \
      "$ROOT_DEVNAME" == "$EXPECTED_ROOT_DEVNAME" && \
      "$ROOT_TYPE" == ext4 && \
      "$ROOT_LABEL" == pmOS_root && \
      "$ROOT_UUID" == "$EXPECTED_ROOT_UUID" && \
      "$MARKER_TARGET" == android-userdata && \
      "$MARKER_BLOCK" == /dev/block/by-name/userdata && \
      "$MARKER_RESOLVED" == "$EXPECTED_MARKER_RESOLVED" && \
      "$MARKER_UUID" == "$EXPECTED_ROOT_UUID" && \
      "$SSHD_STATUS" == started && \
      "$USB_IPV4" == 172.16.42.1/* ]]; then
    CORE_STATUS=passed-real-rootfs-and-ssh
fi

{
    echo "pid1_comm=${PID1_COMM:-unknown}"
    echo "pid1_exe=${PID1_EXE:-unknown}"
    echo "root_source=${ROOT_SOURCE:-unknown}"
    echo "root_resolved=${ROOT_RESOLVED:-unknown}"
    echo "root_majmin=${ROOT_MAJMIN:-unknown}"
    echo "root_sysfs=${ROOT_SYSFS:-unknown}"
    echo "root_devname=${ROOT_DEVNAME:-unknown}"
    echo "root_type=${ROOT_TYPE:-unknown}"
    echo "root_label=${ROOT_LABEL:-unknown}"
    echo "root_uuid=${ROOT_UUID:-unknown}"
    echo "root_block_bytes=${ROOT_BLOCK_BYTES:-unknown}"
    echo "root_filesystem_bytes=${ROOT_DF_BYTES:-unknown}"
    echo "deployment_marker_target=${MARKER_TARGET:-unknown}"
    echo "deployment_marker_block=${MARKER_BLOCK:-unknown}"
    echo "deployment_marker_resolved=${MARKER_RESOLVED:-unknown}"
    echo "deployment_marker_uuid=${MARKER_UUID:-unknown}"
    echo "sshd_status=${SSHD_STATUS:-unknown}"
    echo "networkmanager_status=${NETWORKMANAGER_STATUS:-unknown}"
    echo "usb_ipv4=${USB_IPV4:-unknown}"
    echo "privacy=user-content-and-credentials-excluded"
    echo "live_collection_status=$CORE_STATUS"
} | tee "$OUT/summary.txt"

for source in \
    "$OBSERVATION_DIR/summary.txt" \
    "$PORT_ROOT/build/a33-userdata-rootfs-deployment.txt" \
    "$PORT_ROOT/build/a33-first-rootfs-u0g-flash.txt" \
    "$PORT_ROOT/build/a33-u0g-unified-root-handoff.txt" \
    "$PORT_ROOT/build/a33-first-rootfs-chain-final-audit.txt" \
    "$PORT_ROOT/build/a33-userdata-rootfs-image.txt"; do
    [[ -f "$source" ]] && cp -a "$source" "$OUT/"
done

tar -C "$RESULT_ROOT" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "First real-rootfs live evidence collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
cat "$OUT/summary.txt"

if [[ "$CORE_STATUS" != passed-real-rootfs-and-ssh ]]; then
    exit 4
fi
