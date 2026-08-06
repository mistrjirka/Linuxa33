#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0q-emergency-ssh.py"

spec = importlib.util.spec_from_file_location("a33_u0q_builder_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_U0P_BUILDER_BLOB == "2a5eb4957424fe81212e762ed2225f86ec890ca4"
assert module.EXPECTED_U0P_AUDIT_BLOB == "abc5ac0901a0ca09bbac896d257d0ff40d9a0c66"
assert module.EXPECTED_U0P_INITRAMFS_SHA256 == "10dead55576115f626ff174f01aa28474e05305427e401235f09639deba56e4a"
assert module.EXPECTED_U0P_CANDIDATE_SHA256 == "59f22a3d27eb63cd8d616e7e55e0ecd16fe91a16fbe8e68759d724d2405d5264"
assert module.PORT == 2222
assert module.PHONE_ADDRESS == "172.16.42.1/24"
assert module.TRACE_PATH == "/var/log/a33x-u0q-emergency-ssh.log"

public_key = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIB8uG8vP0B1z/yw+4fy+uE8wqqOBQisF0mD9oP2aU0qx "
    "a33x-u0q-emergency"
)
embedded = "#!/sbin/openrc-run\necho inherited-openrc-sshd\n"
fixture = f'''#!/bin/sh
/bin/busybox cat > /run/a33x-u0n-sshd.initd <<'{module.u0p.HEREDOC}'
{embedded.rstrip()}
{module.u0p.HEREDOC}
u0o_pre_trace 6 "stage=switch-root-ready"
exec switch_root /sysroot "$init"
'''
old_init_hash = module.EXPECTED_U0P_INIT2_SHA256
old_embedded_hash = module.EXPECTED_U0P_EMBEDDED_SSHD_SHA256
module.EXPECTED_U0P_INIT2_SHA256 = module.v2.sha_bytes(fixture.encode())
module.EXPECTED_U0P_EMBEDDED_SSHD_SHA256 = module.v2.sha_bytes(
    module.u0p.embedded_sshd_bytes(fixture)
)
try:
    patched = module.patch_init_second(fixture, public_key)
finally:
    module.EXPECTED_U0P_INIT2_SHA256 = old_init_hash
    module.EXPECTED_U0P_EMBEDDED_SSHD_SHA256 = old_embedded_hash

assert module.u0p.embedded_sshd_bytes(patched) == embedded.encode()
assert patched.count(public_key) == 2
assert patched.count("Port=2222") == 2
assert "Port=22 " not in patched
assert "candidate=U0q-emergency-ssh stage=trace-open" in patched
assert "exec /bin/busybox chroot /sysroot /usr/sbin/sshd" in patched
assert "exec /bin/busybox chroot /sysroot /bin/sh -s" in patched
assert "AuthorizedKeysCommand=/bin/echo" in patched
assert "AuthorizedKeysCommandUser=root" in patched
assert "AuthenticationMethods=publickey" in patched
assert "PermitRootLogin=prohibit-password" in patched
assert "PasswordAuthentication=no" in patched
assert "KbdInteractiveAuthentication=no" in patched
assert "UsePAM=no" in patched
assert "PidFile=none" in patched
assert "172.16.42.1/24" in patched
assert "PRIVATE KEY" not in patched
assert patched.index("candidate=U0q-emergency-ssh stage=trace-open") < patched.index(
    'exec switch_root /sysroot "$init"'
)

addition = module.emergency_block(public_key)
for forbidden in (
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
    "AuthorizedKeysFile=/root",
):
    assert forbidden not in addition, forbidden

assert module.normalize_public_key(public_key + " extra-comment") == public_key
network = module.network_script()
for required in (
    "event=network-helper-started",
    "usb0 rndis0 eth0",
    "ip address replace 172.16.42.1/24",
    "event=network-configured",
    "error=network-interface-timeout",
):
    assert required in network, required

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    init = root / "u0q-init_2nd.sh"
    helper = root / "u0q-network-helper.sh"
    init.write_text(patched, encoding="utf-8")
    helper.write_text(network, encoding="utf-8")
    subprocess.run(["sh", "-n", str(init)], check=True)
    subprocess.run(["sh", "-n", str(helper)], check=True)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "ensure_client_key(root, args.client_key)",
    "assert_only_init_changed(before, after)",
    "before.one(WATCHDOG_TARGET).data != after.one(WATCHDOG_TARGET).data",
    "normal_openrc_sshd_instrumentation_preserved",
    "rootfs_persistent_delta_from_u0p",
    "client_private_key_sha256",
    "embedded_public_key_sha256",
    "phone_partition_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4 -a",
):
    assert forbidden not in source, forbidden

print("a33_u0q_builder_self_test=passed")
print("exact_u0p_parent_artifact_and_blob_pins=passed")
print("normal_openrc_sshd_instrumentation_identity_contract=passed")
print("chrooted_long_lived_process_contract=passed")
print("dedicated_public_key_only_root_auth_contract=passed")
print("independent_usb_network_address_helper_contract=passed")
print("one_new_persistent_trace_file_contract=passed")
print("generated_payload_denylist_contract=passed")
print("host_only_and_phone_write_absence=passed")
print("shell_syntax_validation=passed")
