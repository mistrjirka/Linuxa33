#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "inspect-a33-installed-ssh.py"
EXPECTED_BASE_BLOB = "f6ccfeb32b1167acce48cea00f78f883a185f254"

spec = importlib.util.spec_from_file_location("a33_installed_ssh_v2_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load installed SSH inspector: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

EXTRA_BLOCK = r'''
echo "ssh_parent_permissions_begin"
for relative in /etc /etc/ssh /var /var/empty /run; do
    full="$mountpoint$relative"
    if [ -d "$full" ]; then
        echo "directory path=$relative mode=$(stat -c '%a' "$full" 2>/dev/null || true) uid=$(stat -c '%u' "$full" 2>/dev/null || true) gid=$(stat -c '%g' "$full" 2>/dev/null || true)"
    else
        echo "directory_missing path=$relative"
    fi
done
echo "ssh_parent_permissions_end"

echo "sshd_keygen_contract_begin"
for source in "$mountpoint/etc/init.d/sshd" "$mountpoint/etc/conf.d/sshd"; do
    [ -f "$source" ] || continue
    echo "source=${source#$mountpoint}"
    grep -nE 'ssh-keygen|generate_host_key|sshd_disable_keygen|SSHD_DISABLE_KEYGEN|start_pre|checkconfig|after[[:space:]]+entropy|command=.*sshd|command_args|sshd.*-t|\"\$command\"[[:space:]]+-t' "$source" 2>/dev/null || true
done
echo "sshd_keygen_contract_end"

echo "expected_host_keys_begin"
expected_keys=""
for config in "$mountpoint/etc/ssh/sshd_config" "$mountpoint"/etc/ssh/sshd_config.d/*.conf; do
    [ -f "$config" ] || continue
    configured="$(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$config" 2>/dev/null | awk 'tolower($1)=="hostkey" {print $2}')"
    if [ -n "$configured" ]; then
        expected_keys="$expected_keys $configured"
    fi
done
if [ -z "$expected_keys" ]; then
    expected_keys="/etc/ssh/ssh_host_rsa_key /etc/ssh/ssh_host_ecdsa_key /etc/ssh/ssh_host_ed25519_key"
fi
for relative in $expected_keys; do
    case "$relative" in
        /*) full="$mountpoint$relative" ;;
        *) full="$mountpoint/etc/ssh/$relative"; relative="/etc/ssh/$relative" ;;
    esac
    if [ -s "$full" ]; then
        echo "expected_host_key path=$relative state=present bytes=$(stat -c '%s' "$full" 2>/dev/null || true) mode=$(stat -c '%a' "$full" 2>/dev/null || true)"
    elif [ -e "$full" ]; then
        echo "expected_host_key path=$relative state=empty bytes=$(stat -c '%s' "$full" 2>/dev/null || true) mode=$(stat -c '%a' "$full" 2>/dev/null || true)"
    else
        echo "expected_host_key path=$relative state=missing"
    fi
done
echo "expected_host_keys_end"

echo "ssh_targeted_persistent_logs_begin"
find "$mountpoint/var/log" -type f -size -2097152c 2>/dev/null | sort | while read -r file; do
    matches="$(grep -ainE 'sshd|ssh-keygen|openssh|host key|hostkey|server listening|no hostkeys|could not load host key|generating .*ssh host key|service .*sshd|entropy|crng' "$file" 2>/dev/null || true)"
    if [ -n "$matches" ]; then
        echo "targeted_log_file=${file#$mountpoint}"
        printf '%s\n' "$matches"
    fi
done
echo "ssh_targeted_persistent_logs_end"

echo "apk_script_entries_begin"
if [ -f "$mountpoint/lib/apk/db/scripts.tar" ]; then
    tar -tf "$mountpoint/lib/apk/db/scripts.tar" 2>/dev/null | grep -iE 'openssh|sshd' || true
else
    echo "scripts_tar=missing"
fi
echo "apk_script_entries_end"
'''

ANCHOR = 'echo "filesystem_usage_begin"\n'
if base.REMOTE_SCRIPT.count(ANCHOR) != 1:
    raise SystemExit("installed SSH base inspector anchor changed")
base.REMOTE_SCRIPT = base.REMOTE_SCRIPT.replace(ANCHOR, EXTRA_BLOCK + "\n" + ANCHOR)

_base_summarize = base.summarize


def _section_lines(text: str, name: str) -> list[str]:
    return [line for line in base.section(text, name).splitlines() if line.strip()]


def _directory_state(text: str, path: str) -> dict[str, str]:
    pattern = re.compile(
        rf"^directory path={re.escape(path)} mode=(\S*) uid=(\S*) gid=(\S*)$",
        re.MULTILINE,
    )
    match = pattern.search(base.section(text, "ssh_parent_permissions"))
    if not match:
        return {"state": "missing", "mode": "", "uid": "", "gid": ""}
    return {
        "state": "present",
        "mode": match.group(1),
        "uid": match.group(2),
        "gid": match.group(3),
    }


def summarize(text: str) -> dict[str, object]:
    result = _base_summarize(text)
    contract_lines = _section_lines(text, "sshd_keygen_contract")
    targeted_logs = _section_lines(text, "ssh_targeted_persistent_logs")
    expected_lines = _section_lines(text, "expected_host_keys")

    expected_keys: list[dict[str, str]] = []
    for line in expected_lines:
        match = re.match(
            r"^expected_host_key path=(\S+) state=(\S+)(?: bytes=(\S+) mode=(\S+))?$",
            line,
        )
        if match:
            expected_keys.append(
                {
                    "path": match.group(1),
                    "state": match.group(2),
                    "bytes": match.group(3) or "",
                    "mode": match.group(4) or "",
                }
            )

    contract_text = "\n".join(contract_lines)
    init_generates_keys = bool(
        re.search(r"ssh-keygen|generate_host_key", contract_text, re.IGNORECASE)
    )
    init_runs_checkconfig = bool(
        re.search(r"start_pre|checkconfig|sshd.*-t|\"\$command\"\s+-t", contract_text)
    )
    waits_after_entropy = bool(re.search(r"after\s+entropy", contract_text))

    conf_text = base.section(text, "sshd_conf_d")
    disable_match = re.search(
        r"(?im)^\s*(?:sshd_disable_keygen|SSHD_DISABLE_KEYGEN)\s*=\s*[\"']?([^\s\"']+)",
        conf_text,
    )
    disable_value = disable_match.group(1) if disable_match else "default-no"

    etc_ssh = _directory_state(text, "/etc/ssh")
    var_empty = _directory_state(text, "/var/empty")
    missing_expected = [item["path"] for item in expected_keys if item["state"] != "present"]

    if result["private_host_key_count"] == 0:
        if init_generates_keys and disable_value.lower() not in {"yes", "true", "1"}:
            diagnosis = "host-key-generation-did-not-complete-before-sshd-listen"
        else:
            diagnosis = "all-required-host-keys-missing"
    else:
        diagnosis = "host-keys-present-runtime-listener-diagnosis-required"

    result.update(
        {
            "etc_ssh_directory": etc_ssh,
            "var_empty_directory": var_empty,
            "sshd_init_keygen_lines": contract_lines,
            "sshd_init_generates_host_keys": init_generates_keys,
            "sshd_init_runs_config_validation": init_runs_checkconfig,
            "sshd_init_waits_after_entropy": waits_after_entropy,
            "sshd_disable_keygen_effective": disable_value,
            "expected_host_keys": expected_keys,
            "missing_expected_host_keys": missing_expected,
            "targeted_ssh_log_line_count": len(targeted_logs),
            "targeted_ssh_log_lines": targeted_logs[-200:],
            "ssh_startup_diagnosis": diagnosis,
        }
    )
    return result


base.summarize = summarize


def main() -> int:
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
