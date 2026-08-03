#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
PMBOOTSTRAP_ROOT="${PMBOOTSTRAP_ROOT:-$HOME/.local/var/pmbootstrap}"
ROOTFS="${ROOTFS:-$PMBOOTSTRAP_ROOT/chroot_rootfs_samsung-a33x}"
PMAPORTS="${PMAPORTS:-$PMBOOTSTRAP_ROOT/cache_git/pmaports}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-debug}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$PORT_ROOT/build/a33-next-stage-audit-$TIMESTAMP"
ARCHIVE="$OUT.tar.gz"

for command in bash find grep sed awk tar sha256sum stat file gzip cpio; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

mkdir -p "$OUT"/{host,rootfs,boot,initramfs,pmaports,u0g}

capture() {
    local output="$1"
    shift
    {
        printf 'command:'
        printf ' %q' "$@"
        printf '\n\n'
        "$@"
    } > "$output" 2>&1 || true
}

copy_safe() {
    local source="$1"
    local destination="$2"
    if [[ -f "$source" && -r "$source" ]]; then
        mkdir -p "$(dirname "$destination")"
        cp -a "$source" "$destination"
    fi
}

hash_small_files() {
    local root="$1"
    local output="$2"
    if [[ ! -d "$root" ]]; then
        echo "missing=$root" > "$output"
        return
    fi
    find -L "$root" -type f -size -200M -print0 2>/dev/null \
        | sort -z \
        | xargs -0 -r sha256sum > "$output" 2>&1 || true
}

{
    echo "created=$(date -Ins)"
    echo "repo=$REPO_ROOT"
    echo "repo_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "port_root=$PORT_ROOT"
    echo "pmbootstrap_root=$PMBOOTSTRAP_ROOT"
    echo "rootfs=$ROOTFS"
    echo "pmaports=$PMAPORTS"
    echo "export_dir=$EXPORT_DIR"
    echo "collection_policy=read-only"
    echo "excluded_secrets=/etc/shadow,SSH-private-keys,authorized_keys,NetworkManager-connection-contents,wpa_supplicant-credentials"
} | tee "$OUT/manifest.txt"

capture "$OUT/host/git-state.txt" git -C "$REPO_ROOT" status --short --branch
capture "$OUT/host/git-log.txt" git -C "$REPO_ROOT" log -n 20 --oneline --decorate
capture "$OUT/host/kernel-and-tools.txt" bash -lc '
    uname -a
    printf "\npmbootstrap: "; pmbootstrap --version 2>&1 || true
    printf "\npython: "; python3 --version 2>&1 || true
    printf "\napk: "; apk --version 2>&1 || true
'
capture "$OUT/host/pmbootstrap-status.txt" pmbootstrap status
capture "$OUT/host/pmbootstrap-config-safe.txt" bash -lc '
    for key in device ui extra_packages hostname user timezone locale keymap kernel; do
        printf "%s=" "$key"
        pmbootstrap config "$key" 2>/dev/null || echo unavailable
    done
'
capture "$OUT/host/rootfs-mounts.txt" findmnt -R "$ROOTFS"

if [[ ! -d "$ROOTFS" ]]; then
    echo "REFUSING: rootfs directory is missing: $ROOTFS" >&2
    exit 1
fi

copy_safe "$ROOTFS/etc/os-release" "$OUT/rootfs/etc-os-release.txt"
copy_safe "$ROOTFS/etc/deviceinfo" "$OUT/rootfs/deviceinfo.txt"
copy_safe "$ROOTFS/etc/fstab" "$OUT/rootfs/fstab.txt"
copy_safe "$ROOTFS/etc/inittab" "$OUT/rootfs/inittab.txt"
copy_safe "$ROOTFS/etc/securetty" "$OUT/rootfs/securetty.txt"
copy_safe "$ROOTFS/etc/passwd" "$OUT/rootfs/passwd.txt"
copy_safe "$ROOTFS/etc/group" "$OUT/rootfs/group.txt"
copy_safe "$ROOTFS/etc/network/interfaces" "$OUT/rootfs/network-interfaces.txt"
copy_safe "$ROOTFS/etc/NetworkManager/NetworkManager.conf" "$OUT/rootfs/NetworkManager.conf"
copy_safe "$ROOTFS/etc/ssh/sshd_config" "$OUT/rootfs/sshd_config.txt"
copy_safe "$ROOTFS/etc/conf.d/sshd" "$OUT/rootfs/conf.d-sshd.txt"
copy_safe "$ROOTFS/etc/conf.d/dropbear" "$OUT/rootfs/conf.d-dropbear.txt"
copy_safe "$ROOTFS/etc/conf.d/networking" "$OUT/rootfs/conf.d-networking.txt"
copy_safe "$ROOTFS/etc/conf.d/agetty" "$OUT/rootfs/conf.d-agetty.txt"

for service in sshd dropbear networking NetworkManager networkmanager wpa_supplicant local; do
    copy_safe "$ROOTFS/etc/init.d/$service" "$OUT/rootfs/init.d-$service"
done

capture "$OUT/rootfs/top-level.txt" find "$ROOTFS" -maxdepth 2 -printf '%M %u:%g %s %p -> %l\n'
capture "$OUT/rootfs/runlevels.txt" bash -lc "find '$ROOTFS/etc/runlevels' -maxdepth 3 -printf '%M %u:%g %p -> %l\\n' 2>/dev/null | sort"
capture "$OUT/rootfs/network-secret-file-inventory.txt" bash -lc "
    for path in \
      '$ROOTFS/etc/NetworkManager/system-connections' \
      '$ROOTFS/etc/wpa_supplicant' \
      '$ROOTFS/root/.ssh' \
      '$ROOTFS/home'; do
        if [ -e \"\$path\" ]; then
            echo \"=== \$path ===\"
            find \"\$path\" -maxdepth 3 -printf '%M %u:%g %s %p\\n' 2>/dev/null | sort
        fi
    done
    echo
    echo 'NOTE: contents intentionally excluded.'
"

capture "$OUT/rootfs/packages.txt" pmbootstrap chroot -r -- apk info -vv
capture "$OUT/rootfs/packages-relevant.txt" bash -lc "
    pmbootstrap chroot -r -- apk info -vv 2>/dev/null \
      | grep -Ei 'openssh|dropbear|networkmanager|wpa|wireless|wifi|firmware|linux-firmware|postmarketos-base|mkinitfs|debug-shell|phosh|plasma|sxmo|weston|wayland|xorg|mesa|mali|desktop|console|agetty|busybox' \
      || true
"
capture "$OUT/rootfs/openrc-update.txt" pmbootstrap chroot -r -- rc-update show -v
capture "$OUT/rootfs/openrc-status.txt" pmbootstrap chroot -r -- rc-status -a
capture "$OUT/rootfs/listening-config-evidence.txt" bash -lc "
    grep -RInE '(^|[^#])(sshd|dropbear|agetty|ttyGS0|ttyACM|172\\.16\\.42\\.1|usb0|ncm|NetworkManager|wpa_supplicant)' \
      '$ROOTFS/etc' 2>/dev/null \
      | grep -vE '/shadow|ssh_host_|authorized_keys|system-connections|wpa_supplicant.*conf' \
      | head -n 2000 || true
"

capture "$OUT/rootfs/kernel-modules-relevant.txt" bash -lc "
    find '$ROOTFS/usr/lib/modules' '$ROOTFS/lib/modules' -type f \
      \( -name '*.ko' -o -name '*.ko.gz' -o -name '*.ko.xz' -o -name '*.ko.zst' \) 2>/dev/null \
      | sort \
      | grep -Ei 'wlan|wifi|slsi|scsc|bcmdhd|cfg80211|mac80211|mali|gpu|drm|decon|dpu|panel|display|mipi|touch|input|goodix|focal|fts' \
      || true
"
capture "$OUT/rootfs/firmware-relevant.txt" bash -lc "
    find '$ROOTFS/lib/firmware' '$ROOTFS/usr/lib/firmware' '$ROOTFS/vendor/firmware' -type f 2>/dev/null \
      | sort \
      | grep -Ei 'wlan|wifi|slsi|scsc|bt|bluetooth|mali|gpu|display|panel' \
      | head -n 5000 || true
"

capture "$OUT/boot/rootfs-boot-tree.txt" bash -lc "find -L '$ROOTFS/boot' -maxdepth 3 -printf '%M %u:%g %s %p -> %l\\n' 2>/dev/null | sort"
hash_small_files "$ROOTFS/boot" "$OUT/boot/rootfs-boot-sha256.txt"
capture "$OUT/boot/export-tree.txt" bash -lc "find -L '$EXPORT_DIR' -maxdepth 3 -printf '%M %u:%g %s %p -> %l\\n' 2>/dev/null | sort"
hash_small_files "$EXPORT_DIR" "$OUT/boot/export-small-files-sha256.txt"

INITRAMFS="$EXPORT_DIR/initramfs"
if [[ ! -f "$INITRAMFS" ]]; then
    INITRAMFS="$ROOTFS/boot/initramfs"
fi

if [[ -f "$INITRAMFS" ]]; then
    echo "initramfs=$INITRAMFS" > "$OUT/initramfs/identity.txt"
    stat -Lc 'size=%s' "$INITRAMFS" >> "$OUT/initramfs/identity.txt"
    sha256sum "$INITRAMFS" >> "$OUT/initramfs/identity.txt"
    file "$INITRAMFS" >> "$OUT/initramfs/identity.txt"

    extract="$OUT/initramfs/extracted"
    mkdir -p "$extract"
    gzip -dc "$INITRAMFS" > "$OUT/initramfs/initramfs.cpio"
    cpio -it < "$OUT/initramfs/initramfs.cpio" > "$OUT/initramfs/entries.txt" 2> "$OUT/initramfs/cpio-list.stderr"
    (
        cd "$extract"
        cpio -idmu --quiet < "$OUT/initramfs/initramfs.cpio" 2> "$OUT/initramfs/cpio-extract.stderr"
    )
    rm -f "$OUT/initramfs/initramfs.cpio"

    capture "$OUT/initramfs/management-search.txt" bash -lc "
        grep -aRInE 'pmos_continue_boot|telnet|dropbear|sshd|agetty|getty|ttyGS0|ttyACM|172\\.16\\.42\\.1|usb0|ncm|acm|udhc|dnsmasq|busybox.*telnetd' \
          '$extract' 2>/dev/null \
          | head -n 5000 || true
    "

    relevant="$OUT/initramfs/relevant-files"
    mkdir -p "$relevant"
    while IFS= read -r path; do
        rel="${path#$extract/}"
        mkdir -p "$relevant/$(dirname "$rel")"
        cp -a "$path" "$relevant/$rel"
    done < <(
        find "$extract" -type f -size -1M 2>/dev/null \
          | grep -Ei '/(init|init_2nd\.sh|hooks/|files/|etc/|usr/libexec/|debug|telnet|dropbear|getty|usb|network)' \
          | grep -Ev '/(shadow|ssh_host_|authorized_keys|system-connections|wpa_supplicant.*conf)$' \
          | sort -u
    )
fi

capture "$OUT/pmaports/debug-shell-source-locations.txt" bash -lc "
    find '$PMAPORTS' -type f 2>/dev/null \
      | grep -Ei 'mkinitfs.*debug|debug.*shell|initramfs.*network|usb.*gadget|ttyGS|getty|dropbear' \
      | sort \
      | head -n 5000 || true
"

if [[ -d "$PMAPORTS" ]]; then
    while IFS= read -r source; do
        rel="${source#$PMAPORTS/}"
        destination="$OUT/pmaports/source/$rel"
        mkdir -p "$(dirname "$destination")"
        cp -a "$source" "$destination"
    done < <(
        find "$PMAPORTS" -type f -size -1M 2>/dev/null \
          | grep -Ei 'mkinitfs.*debug|debug.*shell|initramfs.*network|usb.*gadget|ttyGS|getty|dropbear' \
          | head -n 1000
    )
fi

for source in \
    "$PORT_ROOT/build/u0g-muic-dynamic.txt" \
    "$PORT_ROOT/build/u0g-third-host-prepare.txt" \
    "$PORT_ROOT/build/u0g-third-host-recovery-build.txt" \
    "$PORT_ROOT/build/u0g-host-kernel-live.txt" \
    "$PORT_ROOT/build/u0g-host-lsusb-live.txt" \
    "$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0g-muic-dynamic-manifest.txt" \
    "$PORT_ROOT/build/runtime-results/u0g-result-20260803-155749/u0g-metadata-result.txt" \
    "$PORT_ROOT/build/runtime-results/u0g-result-20260803-155749/u0f-metadata-result.txt"
do
    if [[ -f "$source" ]]; then
        cp -a "$source" "$OUT/u0g/"
    fi
done

PACKAGES="$OUT/rootfs/packages.txt"
RUNLEVELS="$OUT/rootfs/runlevels.txt"
{
    echo "rootfs_exists=yes"
    echo "rootfs_os=$(awk -F= '$1==\"PRETTY_NAME\" {gsub(/^\"|\"$/,\"\",$2); print $2}' "$ROOTFS/etc/os-release" 2>/dev/null || true)"
    echo "openssh_installed=$(grep -Eiq '^openssh($|-)' "$PACKAGES" && echo yes || echo no)"
    echo "dropbear_installed=$(grep -Eiq '^dropbear($|-)' "$PACKAGES" && echo yes || echo no)"
    echo "networkmanager_installed=$(grep -Eiq '^networkmanager($|-)' "$PACKAGES" && echo yes || echo no)"
    echo "sshd_enabled=$(grep -Eq '/sshd ->|/sshd$' "$RUNLEVELS" && echo yes || echo no)"
    echo "dropbear_enabled=$(grep -Eq '/dropbear ->|/dropbear$' "$RUNLEVELS" && echo yes || echo no)"
    echo "networkmanager_enabled=$(grep -Eiq '/NetworkManager ->|/networkmanager ->|/NetworkManager$|/networkmanager$' "$RUNLEVELS" && echo yes || echo no)"
    echo "initramfs_present=$([[ -f "$INITRAMFS" ]] && echo yes || echo no)"
    echo "u0g_physical_usb_status=confirmed-working"
    echo "next_decision=normal-rootfs-boot-plus-persistent-ssh-over-usb"
} | tee "$OUT/summary.txt"

tar -C "$(dirname "$OUT")" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "A33 next-stage audit collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
echo "Checksum:  $ARCHIVE.sha256"
echo "Upload the .tar.gz archive only."
