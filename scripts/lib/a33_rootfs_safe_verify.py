from __future__ import annotations


ROOTFS_SAFE_VERIFY_SCRIPT = r'''set -eu
target="$1"
expected_uuid="$2"
shift 2
mountpoint=/tmp/a33x-rootfs-readonly-verify
mounted=no
cleanup()
{
    [ "$mounted" = no ] || umount "$mountpoint" 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"
mounted=yes

rootfs_resolve()
{
    relative="$1"
    case "$relative" in
        /*) pending="${relative#/}" ;;
        *) pending="$relative" ;;
    esac
    resolved=""
    depth=0

    while [ -n "$pending" ]; do
        case "$pending" in
            */*)
                component="${pending%%/*}"
                pending="${pending#*/}"
                ;;
            *)
                component="$pending"
                pending=""
                ;;
        esac

        [ -n "$component" ] || continue
        case "$component" in
            .) continue ;;
            ..)
                case "$resolved" in
                    */*) resolved="${resolved%/*}" ;;
                    *) resolved="" ;;
                esac
                continue
                ;;
        esac

        if [ -n "$resolved" ]; then
            candidate="$mountpoint/$resolved/$component"
        else
            candidate="$mountpoint/$component"
        fi

        if [ -L "$candidate" ]; then
            depth=$((depth + 1))
            [ "$depth" -le 40 ] || {
                echo "rootfs_symlink_depth_exceeded path=$relative"
                return 1
            }
            link_target="$(readlink "$candidate" 2>/dev/null || true)"
            [ -n "$link_target" ] || {
                echo "rootfs_empty_symlink path=$relative component=$component"
                return 1
            }
            case "$link_target" in
                /*)
                    resolved=""
                    link_target="${link_target#/}"
                    ;;
            esac
            if [ -n "$pending" ]; then
                pending="$link_target/$pending"
            else
                pending="$link_target"
            fi
        else
            if [ -n "$resolved" ]; then
                resolved="$resolved/$component"
            else
                resolved="$component"
            fi
        fi
    done

    if [ -n "$resolved" ]; then
        printf '%s/%s\n' "$mountpoint" "$resolved"
    else
        printf '%s\n' "$mountpoint"
    fi
}

for path in "$@"; do
    resolved_path="$(rootfs_resolve "$path")" || exit 20
    [ -f "$resolved_path" ] || {
        echo "critical_missing=$path resolved=${resolved_path#$mountpoint}"
        exit 20
    }
    echo "critical_resolution path=$path resolved=${resolved_path#$mountpoint}"
    echo "critical_sha=$(sha256sum "$resolved_path" | awk '{print $1}') path=$path"
done

for path in /sbin/init /etc/os-release; do
    resolved_path="$(rootfs_resolve "$path")" || exit 21
    [ -e "$resolved_path" ] || [ -L "$resolved_path" ] || {
        echo "root_path_missing=$path resolved=${resolved_path#$mountpoint}"
        exit 22
    }
    echo "root_path_resolution path=$path resolved=${resolved_path#$mountpoint}"
done

for pair in \
    /etc/runlevels/default/sshd:/etc/init.d/sshd \
    /etc/runlevels/default/networkmanager:/etc/init.d/networkmanager; do
    path="${pair%%:*}"
    expected="${pair#*:}"
    link_path="$mountpoint$path"
    [ -L "$link_path" ] || exit 23
    [ "$(readlink "$link_path")" = "$expected" ] || exit 24
    expected_path="$(rootfs_resolve "$expected")" || exit 25
    [ -e "$expected_path" ] || [ -L "$expected_path" ] || exit 25
done

fstab_path="$(rootfs_resolve /etc/fstab)" || exit 26
active="$(grep -Ev '^[[:space:]]*(#|$)' "$fstab_path" || true)"
[ "$active" = "UUID=$expected_uuid / ext4 defaults 0 1" ] || {
    echo "fstab_active=$active"
    exit 26
}

target_path="$(rootfs_resolve /etc/a33x-rootfs-target)" || exit 27
grep -Fqx "root_uuid=$expected_uuid" "$target_path" || exit 27
grep -Fqx 'target=android-userdata' "$target_path" || exit 28

umount "$mountpoint"
mounted=no
echo readonly_verification=passed
echo readonly_unmount=passed
echo rootfs_path_resolution=rootfs-relative-symlink-safe
echo phone_partition_writes=no
'''
