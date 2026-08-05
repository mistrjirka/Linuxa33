#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent
COMMON_PATH = HERE / "flash-a33-u0i-python-direct-root-v2.py"
EXPECTED_TWRP_SHA256 = "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_USERDATA = "/dev/block/sda36"
EXPECTED_USERDATA_BYTES = "114240258048"
EXPECTED_LABEL = "pmOS_root"

spec = importlib.util.spec_from_file_location("a33_installed_ssh_common", COMMON_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load A33 runtime helper: {COMMON_PATH}")
common = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = common
spec.loader.exec_module(common)


class InspectionError(RuntimeError):
    pass


REMOTE_SCRIPT = r'''set -u
target="$1"
expected="$2"
mountpoint=/tmp/a33x-installed-ssh-inspect
mounted=no
cleanup() {
    if [ "$mounted" = yes ]; then
        umount "$mountpoint" 2>/dev/null || true
    fi
}
trap cleanup EXIT

resolved="$(readlink -f "$target" 2>/dev/null || true)"
echo "target=$target"
echo "target_resolved=$resolved"
[ "$resolved" = "$expected" ] || exit 20

mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"
mounted=yes
echo "readonly_mount=passed"

path_state() {
    relative="$1"
    full="$mountpoint$relative"
    if [ -L "$full" ]; then
        echo "path_type=symlink path=$relative target=$(readlink "$full" 2>/dev/null || true)"
    elif [ -f "$full" ]; then
        echo "path_type=file path=$relative bytes=$(stat -c '%s' "$full" 2>/dev/null || true) mode=$(stat -c '%a' "$full" 2>/dev/null || true) uid=$(stat -c '%u' "$full" 2>/dev/null || true) gid=$(stat -c '%g' "$full" 2>/dev/null || true) mtime=$(stat -c '%Y' "$full" 2>/dev/null || true) sha256=$(sha256sum "$full" 2>/dev/null | awk '{print $1}')"
    elif [ -d "$full" ]; then
        echo "path_type=directory path=$relative mode=$(stat -c '%a' "$full" 2>/dev/null || true) uid=$(stat -c '%u' "$full" 2>/dev/null || true) gid=$(stat -c '%g' "$full" 2>/dev/null || true)"
    else
        echo "path_type=missing path=$relative"
    fi
}

for relative in \
    /sbin/init \
    /usr/sbin/sshd \
    /usr/bin/ssh-keygen \
    /etc/init.d/sshd \
    /etc/conf.d/sshd \
    /etc/runlevels/default/sshd \
    /etc/runlevels/default/networkmanager \
    /etc/ssh \
    /etc/ssh/sshd_config \
    /var/empty \
    /lib/ld-musl-aarch64.so.1; do
    path_state "$relative"
done

echo "runlevels_default_begin"
ls -la "$mountpoint/etc/runlevels/default" 2>&1 || true
echo "runlevels_default_end"

echo "ssh_directory_begin"
ls -la "$mountpoint/etc/ssh" 2>&1 || true
echo "ssh_directory_end"

echo "host_keys_begin"
for key in "$mountpoint"/etc/ssh/ssh_host_*; do
    [ -e "$key" ] || continue
    relative="${key#$mountpoint}"
    case "$relative" in
        *.pub) kind=public ;;
        *) kind=private ;;
    esac
    echo "host_key kind=$kind path=$relative bytes=$(stat -c '%s' "$key" 2>/dev/null || true) mode=$(stat -c '%a' "$key" 2>/dev/null || true) uid=$(stat -c '%u' "$key" 2>/dev/null || true) gid=$(stat -c '%g' "$key" 2>/dev/null || true) mtime=$(stat -c '%Y' "$key" 2>/dev/null || true) sha256=$(sha256sum "$key" 2>/dev/null | awk '{print $1}')"
done
echo "host_keys_end"

echo "sshd_config_effective_text_begin"
for config in "$mountpoint/etc/ssh/sshd_config" "$mountpoint"/etc/ssh/sshd_config.d/*.conf; do
    [ -f "$config" ] || continue
    echo "config_file=${config#$mountpoint}"
    sed -n '1,500p' "$config" 2>/dev/null || true
done
echo "sshd_config_effective_text_end"

echo "sshd_config_active_directives_begin"
for config in "$mountpoint/etc/ssh/sshd_config" "$mountpoint"/etc/ssh/sshd_config.d/*.conf; do
    [ -f "$config" ] || continue
    echo "config_file=${config#$mountpoint}"
    sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$config" 2>/dev/null || true
done
echo "sshd_config_active_directives_end"

echo "sshd_init_script_begin"
sed -n '1,500p' "$mountpoint/etc/init.d/sshd" 2>&1 || true
echo "sshd_init_script_end"

echo "sshd_conf_d_begin"
sed -n '1,300p' "$mountpoint/etc/conf.d/sshd" 2>&1 || true
echo "sshd_conf_d_end"

echo "openssh_package_begin"
awk 'BEGIN { RS=""; FS="\n" } $0 ~ /(^|\n)P:openssh($|\n)/ || $0 ~ /(^|\n)P:openssh-server($|\n)/ { print $0 "\n" }' "$mountpoint/lib/apk/db/installed" 2>/dev/null || true
echo "openssh_package_end"

echo "var_log_listing_begin"
find "$mountpoint/var/log" -mindepth 1 -maxdepth 3 -printf '%y %s %T@ %p\n' 2>/dev/null | sed "s#$mountpoint##" | sort || true
echo "var_log_listing_end"

for relative in \
    /var/log/messages \
    /var/log/daemon.log \
    /var/log/auth.log \
    /var/log/secure \
    /var/log/rc.log \
    /var/log/boot \
    /var/log/boot.log \
    /var/log/dmesg; do
    file="$mountpoint$relative"
    if [ -f "$file" ]; then
        echo "log_begin=$relative"
        tail -n 800 "$file" 2>&1 || true
        echo "log_end=$relative"
    fi
done

echo "firewall_runlevel_links_begin"
find "$mountpoint/etc/runlevels" -maxdepth 2 -type l \( -name '*nft*' -o -name '*iptables*' -o -name '*firewall*' \) -printf '%p -> %l\n' 2>/dev/null | sed "s#$mountpoint##" | sort || true
echo "firewall_runlevel_links_end"

echo "filesystem_usage_begin"
df -h "$mountpoint" 2>&1 || true
echo "filesystem_usage_end"

umount "$mountpoint"
mounted=no
echo "readonly_unmount=passed"
'''


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def section(text: str, name: str) -> str:
    start = f"{name}_begin\n"
    end = f"{name}_end\n"
    if text.count(start) != 1 or text.count(end) != 1:
        return ""
    return text.split(start, 1)[1].split(end, 1)[0]


def summarize(text: str) -> dict[str, object]:
    private_keys = re.findall(r"^host_key kind=private .+$", text, re.MULTILINE)
    public_keys = re.findall(r"^host_key kind=public .+$", text, re.MULTILINE)
    directives = section(text, "sshd_config_active_directives")
    runlevels = section(text, "runlevels_default")
    logs = "\n".join(
        match.group(1)
        for match in re.finditer(
            r"log_begin=/[^\n]+\n(.*?)\nlog_end=/[^\n]+",
            text,
            re.DOTALL,
        )
    )
    error_lines = [
        line
        for line in logs.splitlines()
        if re.search(
            r"sshd|ssh-keygen|host key|listen|bind|fatal|error|failed|refus",
            line,
            re.IGNORECASE,
        )
    ]
    ports = re.findall(r"(?im)^\s*Port\s+(\d+)\s*$", directives)
    listen_addresses = re.findall(
        r"(?im)^\s*ListenAddress\s+([^\s#]+)", directives
    )
    return {
        "readonly_mount_passed": "readonly_mount=passed" in text,
        "readonly_unmount_passed": "readonly_unmount=passed" in text,
        "sshd_binary_present": bool(
            re.search(r"^path_type=file path=/usr/sbin/sshd\b", text, re.MULTILINE)
        ),
        "sshd_init_present": bool(
            re.search(r"^path_type=file path=/etc/init.d/sshd\b", text, re.MULTILINE)
        ),
        "sshd_runlevel_enabled": "sshd" in runlevels,
        "private_host_key_count": len(private_keys),
        "public_host_key_count": len(public_keys),
        "configured_ports": ports or ["22-default"],
        "configured_listen_addresses": listen_addresses or ["all-default"],
        "ssh_related_log_line_count": len(error_lines),
        "ssh_related_log_lines": error_lines[-100:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect installed A33 rootfs SSH/OpenRC state read-only from TWRP"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    serial = common.select_recovery(adb, 30)

    values, sections = common.live_state(adb, serial)
    expected = {
        "recovery_sha": EXPECTED_TWRP_SHA256,
        "userdata_resolved": EXPECTED_USERDATA,
        "userdata_bytes": EXPECTED_USERDATA_BYTES,
        "userdata_readonly": "0",
    }
    mismatches = [
        f"{key}: actual={values.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if values.get(key) != wanted
    ]
    for name in ("mount_users", "swap_users", "dm_users"):
        if sections.get(name):
            mismatches.append(f"{name}: active={sections[name]!r}")
    uuid_value, label = common.ext4_identity(adb, serial)
    if label != EXPECTED_LABEL:
        mismatches.append(f"filesystem_label: actual={label!r} expected={EXPECTED_LABEL!r}")
    if mismatches:
        raise InspectionError("unsafe TWRP/userdata state:\n" + "\n".join(mismatches))

    completed = common.run(
        [adb, "-s", serial, "shell", "sh", "-s", "--", common.USERDATA, EXPECTED_USERDATA],
        input_data=REMOTE_SCRIPT,
        check=False,
    )
    output = completed.stdout.replace("\r", "")
    stderr = completed.stderr.replace("\r", "")
    if completed.returncode != 0:
        raise InspectionError(
            f"read-only rootfs inspection failed rc={completed.returncode}:\n{output}\n{stderr}"
        )
    if "readonly_mount=passed" not in output or "readonly_unmount=passed" not in output:
        raise InspectionError("read-only mount lifecycle did not complete")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / "build" / f"a33-installed-ssh-inspection-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    raw = out / "rootfs-ssh-state.txt"
    raw.write_text(output + ("\n=== stderr ===\n" + stderr if stderr else ""), encoding="utf-8")
    summary_values = summarize(output)
    summary_values.update(
        {
            "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
            "operation": "inspect-a33-installed-ssh-read-only",
            "implementation_language": "python3",
            "adb_serial": serial,
            "twrp_recovery_sha256": values["recovery_sha"],
            "userdata_resolved": values["userdata_resolved"],
            "userdata_filesystem_uuid": uuid_value,
            "userdata_filesystem_label": label,
            "raw_report": str(raw),
            "raw_report_sha256": sha256_file(raw),
            "phone_partition_writes": "no",
            "inspection_status": "passed",
        }
    )
    summary = out / "summary.json"
    summary.write_text(json.dumps(summary_values, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    stable = root / "build/a33-installed-ssh-inspection.json"
    shutil.copy2(summary, stable)

    archive = Path(str(out) + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)
    archive_sha = sha256_file(archive)
    Path(str(archive) + ".sha256").write_text(
        f"{archive_sha}  {archive}\n", encoding="utf-8"
    )

    print(json.dumps(summary_values, indent=2, sort_keys=True))
    print(f"inspection_directory={out}")
    print(f"inspection_archive={archive}")
    print(f"inspection_archive_sha256={archive_sha}")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InspectionError, common.Refusal, OSError, ValueError) as exc:
        print(f"INSTALLED SSH INSPECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
