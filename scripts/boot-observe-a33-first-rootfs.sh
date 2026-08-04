#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/a33-adb-runtime.sh
source "$SCRIPT_DIR/lib/a33-adb-runtime.sh"
FLASH_REPORT="${FLASH_REPORT:-$PORT_ROOT/build/a33-first-rootfs-u0g-flash.txt}"
RESULT_ROOT="${RESULT_ROOT:-$PORT_ROOT/build/runtime-results}"
PHONE_IP="${PHONE_IP:-172.16.42.1}"
HOST_CIDR="${HOST_CIDR:-172.16.42.2/24}"
MAX_SECONDS="${MAX_SECONDS:-180}"
EXPECTED_RECOVERY_SHA256="e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$RESULT_ROOT/a33-first-rootfs-observation-$TIMESTAMP"
ARCHIVE="$OUT.tar.gz"

for command in "$ADB" sha256sum awk grep date mkdir tar ip ping timeout bash lsusb sleep; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
if [[ ! "$MAX_SECONDS" =~ ^[0-9]+$ || "$MAX_SECONDS" -lt 1 || "$MAX_SECONDS" -gt 900 ]]; then
    echo "REFUSING: MAX_SECONDS must be an integer from 1 through 900" >&2
    exit 1
fi

value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}

if [[ ! -f "$FLASH_REPORT" || "$(value "$FLASH_REPORT" flash_status)" != passed ]]; then
    echo "REFUSING: successful U0g flash report is missing" >&2
    exit 1
fi
if [[ "$(value "$FLASH_REPORT" recovery_partition_sha256)" != "$EXPECTED_RECOVERY_SHA256" || \
      "$(value "$FLASH_REPORT" reboot_performed)" != no || \
      "$(value "$FLASH_REPORT" userdata_written)" != no || \
      "$(value "$FLASH_REPORT" cache_written)" != no || \
      "$(value "$FLASH_REPORT" super_written)" != no || \
      "$(value "$FLASH_REPORT" boot_written)" != no ]]; then
    echo "REFUSING: flash report does not match the approved first-rootfs state" >&2
    cat "$FLASH_REPORT" >&2
    exit 1
fi

DEPLOYMENT_REPORT="$(value "$FLASH_REPORT" deployment_report)"
if [[ ! -f "$DEPLOYMENT_REPORT" || "$(value "$DEPLOYMENT_REPORT" deployment_status)" != passed ]]; then
    echo "REFUSING: deployment report referenced by flash report is invalid" >&2
    exit 1
fi
if [[ "$(sha256sum "$DEPLOYMENT_REPORT" | awk '{print $1}')" != \
      "$(value "$FLASH_REPORT" deployment_report_sha256)" ]]; then
    echo "REFUSING: deployment report changed after the recovery flash" >&2
    exit 1
fi
ROOT_UUID="$(value "$DEPLOYMENT_REPORT" filesystem_uuid)"
[[ -n "$ROOT_UUID" ]] || {
    echo "REFUSING: deployment report has no root UUID" >&2
    exit 1
}

mkdir -p "$OUT"
{
    echo "created=$(date -Ins)"
    echo "operation=first-rootfs-boot-observation"
    echo "phone_ip=$PHONE_IP"
    echo "host_cidr=$HOST_CIDR"
    echo "max_seconds=$MAX_SECONDS"
    echo "flash_report=$FLASH_REPORT"
    echo "flash_report_sha256=$(sha256sum "$FLASH_REPORT" | awk '{print $1}')"
    echo "phone_partition_writes=no"
    echo "reboot_target=recovery"
} | tee "$OUT/manifest.txt"

a33_init_recovery_adb 30

PREBOOT="$(
    "$ADB" shell sh -s -- "$ROOT_UUID" 2>/dev/null <<'SH' | tr -d '\r'
set -eu
expected_uuid="$1"
target=/dev/block/by-name/userdata
resolved="$(readlink -f "$target")"
echo "recovery_sha=$(sha256sum /dev/block/by-name/recovery | awk 'NR==1 {print $1}')"
echo "root_resolved=$resolved"
echo "root_readonly=$(blockdev --getro "$target" 2>/dev/null || true)"
echo "mount_users_begin"
awk '{print $1, $2}' /proc/mounts 2>/dev/null | while read -r source mountpoint; do
    source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
    if [ "$source" = "$target" ] || [ "$source" = "$resolved" ] || [ "$source_resolved" = "$resolved" ]; then
        echo "$source $mountpoint"
    fi
done
echo "mount_users_end"
echo "swap_users_begin"
if [ -r /proc/swaps ]; then
    tail -n +2 /proc/swaps 2>/dev/null | while read -r source rest; do
        source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
        if [ "$source" = "$target" ] || [ "$source" = "$resolved" ] || [ "$source_resolved" = "$resolved" ]; then
            echo "$source"
        fi
    done
fi
echo "swap_users_end"
echo "dm_users_begin"
for dm in /sys/block/dm-*; do
    [ -e "$dm" ] || continue
    if find "$dm/slaves" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | grep -qx "${resolved##*/}"; then
        echo "${dm##*/}:$(cat "$dm/dm/name" 2>/dev/null || true)"
    fi
done
echo "dm_users_end"
SH
)"
ROOT_IDENTITY="$(a33_ext4_identity /dev/block/by-name/userdata)"
PREBOOT="${PREBOOT}"$'\n'"root_type=$(awk -F= '$1=="type" {print $2; exit}' <<<"$ROOT_IDENTITY")"
PREBOOT="${PREBOOT}"$'\n'"root_label=$(awk -F= '$1=="label" {print $2; exit}' <<<"$ROOT_IDENTITY")"
PREBOOT="${PREBOOT}"$'\n'"root_uuid=$(awk -F= '$1=="uuid" {print $2; exit}' <<<"$ROOT_IDENTITY")"

preboot_value() {
    local key="$1"
    printf '%s\n' "$PREBOOT" | awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}'
}
preboot_section() {
    local name="$1"
    printf '%s\n' "$PREBOOT" | awk -v begin="${name}_begin" -v end="${name}_end" '
        $0==begin {inside=1; next}
        $0==end {inside=0}
        inside && NF {print}
    '
}
if [[ "$(preboot_value recovery_sha)" != "$EXPECTED_RECOVERY_SHA256" || \
      "$(preboot_value root_resolved)" != /dev/block/sda36 || \
      "$(preboot_value root_readonly)" != 0 || \
      "$(preboot_value root_type)" != ext4 || \
      "$(preboot_value root_label)" != pmOS_root || \
      "$(preboot_value root_uuid)" != "$ROOT_UUID" || \
      -n "$(preboot_section mount_users)" || \
      -n "$(preboot_section swap_users)" || \
      -n "$(preboot_section dm_users)" ]]; then
    echo "REFUSING: preboot recovery/rootfs state is not exact or rootfs is in use" >&2
    printf '%s\n' "$PREBOOT" >&2
    exit 1
fi
printf '%s\n' "$PREBOOT" > "$OUT/preboot-state.txt"

{
    echo "=== baseline $(date -Ins) ==="
    lsusb 2>&1 || true
    echo "--- addresses ---"
    ip -br addr 2>&1 || true
    echo "--- routes ---"
    ip route 2>&1 || true
} > "$OUT/host-baseline.txt"

START_ISO="$(date -Ins)"
START_EPOCH="$(date +%s)"
echo "=== Reboot exact U0g recovery into first real-rootfs test ==="
"$ADB" reboot recovery

USB_ENUM=no
HOST_INTERFACE=no
PING_OK=no
SSH_PORT_OPEN=no
SUCCESS_SECOND=""

: > "$OUT/observation-loop.txt"
for ((second = 0; second <= MAX_SECONDS; second++)); do
    {
        echo "=== second=$second time=$(date -Ins) ==="
        usb_line="$(lsusb -d 04e8:6860 2>/dev/null || true)"
        if [[ -n "$usb_line" ]]; then
            USB_ENUM=yes
            echo "usb_enum=yes"
            echo "$usb_line"
        else
            echo "usb_enum=no"
        fi

        interface_line="$(ip -o -4 addr show 2>/dev/null | awk -v cidr="$HOST_CIDR" '$4==cidr {print; exit}')"
        if [[ -n "$interface_line" ]]; then
            HOST_INTERFACE=yes
            echo "host_interface=yes"
            echo "$interface_line"
        else
            echo "host_interface=no"
        fi

        if ping -c 1 -W 1 "$PHONE_IP" >/dev/null 2>&1; then
            PING_OK=yes
            echo "ping=yes"
        else
            echo "ping=no"
        fi

        if a33_tcp_port_open "$PHONE_IP" 22 1 >/dev/null 2>&1; then
            SSH_PORT_OPEN=yes
            echo "ssh_port_22=yes"
        else
            echo "ssh_port_22=no"
        fi

        ip -br addr 2>&1 || true
        ip route 2>&1 || true
    } >> "$OUT/observation-loop.txt"

    if [[ "$USB_ENUM" == yes && "$HOST_INTERFACE" == yes &&
          "$PING_OK" == yes && "$SSH_PORT_OPEN" == yes ]]; then
        SUCCESS_SECOND="$second"
        break
    fi
    sleep 1
done

END_ISO="$(date -Ins)"
END_EPOCH="$(date +%s)"

{
    echo "=== final $(date -Ins) ==="
    lsusb 2>&1 || true
    echo "--- addresses ---"
    ip -br addr 2>&1 || true
    echo "--- routes ---"
    ip route 2>&1 || true
    echo "--- neighbor ---"
    ip neigh 2>&1 || true
} > "$OUT/host-final.txt"

if command -v journalctl >/dev/null 2>&1; then
    journalctl -k --since "$START_ISO" --until "$END_ISO" \
        > "$OUT/host-kernel-journal.txt" 2>&1 || true
fi

OBSERVATION_STATUS=failed-no-ssh
if [[ "$USB_ENUM" == yes && "$HOST_INTERFACE" == yes &&
      "$PING_OK" == yes && "$SSH_PORT_OPEN" == yes ]]; then
    OBSERVATION_STATUS=passed-rootfs-network-and-ssh-ready
fi

{
    echo "started=$START_ISO"
    echo "finished=$END_ISO"
    echo "elapsed_seconds=$((END_EPOCH - START_EPOCH))"
    echo "success_second=${SUCCESS_SECOND:-none}"
    echo "usb_enumeration=$USB_ENUM"
    echo "host_usb_network_interface=$HOST_INTERFACE"
    echo "ping_172_16_42_1=$PING_OK"
    echo "ssh_port_22=$SSH_PORT_OPEN"
    echo "phone_partition_writes=no"
    echo "observation_status=$OBSERVATION_STATUS"
} | tee "$OUT/summary.txt"

tar -C "$RESULT_ROOT" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "First rootfs boot observation completed."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
cat "$OUT/summary.txt"

if [[ "$OBSERVATION_STATUS" != passed-rootfs-network-and-ssh-ready ]]; then
    exit 3
fi
