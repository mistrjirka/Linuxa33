#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0q-emergency-ssh-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0q_v2_builder_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_BLOB == "fa662b03cf3a4e4c9166ebc9fa0a177dc12dbdb4"
assert module.RUNTIME_REVISION == "2"
assert module.PRIVSEP_PATH == "/run/sshd"
assert module.NETWORK_READY_PATH == "/run/a33x-u0q-network-ready"
assert module.READY_TIMEOUT_SECONDS == 150
assert module.FIREWALL_COMMENT == "a33x-u0q-emergency-2222"

public_key = "ssh-ed25519 " + ("A" * 68) + " a33x-u0q-emergency"
network = module.network_script()
for required in (
    "event=network-helper-started",
    "event=network-configured",
    "event=network-ready-marker-written path=/run/a33x-u0q-network-ready",
    "nft list chain inet filter input",
    "nft insert rule inet filter input tcp dport 2222 accept",
    "a33x-u0q-emergency-2222",
    "event=runtime-firewall-rule-added",
    "event=runtime-firewall-rule-present",
    "event=runtime-firewall-table-wait",
):
    assert required in network, required
assert network.count("a33x-u0q-emergency-2222") == 2
assert "exit 0" not in network

block = module.emergency_block(public_key)
for required in (
    'awk \'$2 == "/sysroot/run"',
    "run-is-not-a-mounted-runtime-filesystem",
    "mkdir -p /sysroot/run/sshd",
    "chmod 0755 /sysroot/run/sshd",
    "chown 0:0 /sysroot/run/sshd",
    "rm -f /sysroot/run/a33x-u0q-network-ready",
    "event=runtime-directory-ready path=/run/sshd backing=mounted-run revision=2",
    "event=network-ready-marker-written path=/run/a33x-u0q-network-ready",
    "event=pre-switch-root-wait",
    "event=pre-switch-root-ready",
    "emergency-channel-readiness-timeout",
    "kill -0 \"$U0Q_SSHD_PID\"",
    "/proc/net/tcp /proc/net/tcp6",
    ":08AE$",
    "nft insert rule inet filter input tcp dport 2222 accept",
    "exec /bin/busybox chroot /sysroot /usr/sbin/sshd",
    "exec /bin/busybox chroot /sysroot /bin/sh -s",
):
    assert required in block, required

order = (
    block.index("event=runtime-directory-ready path=/run/sshd"),
    block.index("event=network-helper-spawned"),
    block.index("event=sshd-helper-spawned"),
    block.index("event=pre-switch-root-ready"),
)
assert tuple(sorted(order)) == order

for forbidden in (
    "/etc/nftables.d/",
    "/etc/nftables.nft",
    "mount -o remount,rw",
    "umount -l",
    "sed -i",
    "rm -rf /sysroot",
    "PasswordAuthentication=yes",
    "UsePAM=yes",
):
    assert forbidden not in block, forbidden

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "u0q-v2-generated-fragment.sh"
    path.write_text(
        "#!/bin/sh\n"
        "u0q_refuse() { return 1; }\n"
        "U0Q_TRACE=/tmp/u0q-trace\n"
        + block
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(["sh", "-n", str(path)], check=True)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "selected_paths()",
    "base.network_script = network_script",
    "base.emergency_block = emergency_block",
    "validate_generated_payload(root)",
    "emergency_privsep_backing",
    "emergency_pre_switch_root_gate",
    "emergency_pre_switch_root_timeout_seconds",
    "emergency_network_ready_path",
    "emergency_firewall_policy",
    "emergency_firewall_persistent_delta",
    "replace_single_field(manifest, \"patch_report_sha256\"",
    "init_text.count(FIREWALL_COMMENT) != 2",
    "generated U0q v2 readiness gate is not before switch_root",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4",
    "dd if=",
    "mkfs",
    "wipefs",
):
    assert forbidden not in source, forbidden

print("a33_u0q_v2_builder_self_test=passed")
print("exact_u0q_base_builder_blob_pin=passed")
print("mounted_run_privsep_directory_contract=passed")
print("pre_switch_root_network_and_listener_gate_contract=passed")
print("runtime_only_nft_port_2222_monitor_contract=passed")
print("normal_openrc_sshd_path_preserved=passed")
print("generated_shell_syntax_validation=passed")
print("persistent_firewall_mutation_absence=passed")
print("host_only_and_phone_write_absence=passed")
