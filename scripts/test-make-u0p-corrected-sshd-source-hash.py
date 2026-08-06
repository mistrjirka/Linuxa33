#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0p-corrected-sshd-source-hash.py"

spec = importlib.util.spec_from_file_location("a33_u0p_builder_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_U0O_BUILDER_V2_BLOB == "88cd0b9b3446314c04ad0c4b20583c2e6facf449"
assert module.EXPECTED_U0O_AUDIT_V2_BLOB == "25a3ab194093b7b082477caba5c554481f37bf1a"
assert module.EXPECTED_U0O_INITRAMFS_SHA256 == "db1b76d1cb9da64272a7e42033bc72a8f9d7900e98fd3acea76c7edb1dd4d49e"
assert module.EXPECTED_U0O_CANDIDATE_SHA256 == "d98bb291f56fc8cb2f595c915d146c3b951333f04435dfb4e2839b95ddc5da0b"
assert module.EXPECTED_U0O_INIT2_SHA256 == "14930c5ab6cda0056b881cffd25c8272b0cf4f01704313384c133fac734c7e98"

embedded = """#!/sbin/openrc-run
u0n_kmsg()
{
    printf '%s\\n' "$*"
}
"""
fixture = f'''#!/bin/sh
U0N_SSHD_INSTRUMENTED_SHA={module.STALE_U0N_INSTRUMENTED_SHA256}
/bin/busybox cat > /run/a33x-u0n-sshd.initd <<'{module.HEREDOC}'
{embedded.rstrip()}
{module.HEREDOC}
printf '%s\\n' "candidate=U0o-persistent-sshd-trace stage=trace-open path={module.TRACE_PATH}"
'''
old_expected = module.EXPECTED_U0O_INIT2_SHA256
module.EXPECTED_U0O_INIT2_SHA256 = module.v2.sha_bytes(fixture.encode())
try:
    patched, corrected_sha = module.patch_init_second(fixture)
finally:
    module.EXPECTED_U0O_INIT2_SHA256 = old_expected

expected_payload = embedded.encode()
assert module.embedded_sshd_bytes(fixture) == expected_payload
assert module.embedded_sshd_bytes(patched) == expected_payload
assert corrected_sha == module.v2.sha_bytes(expected_payload)
assert corrected_sha != module.STALE_U0N_INSTRUMENTED_SHA256
assert module.declared_instrumented_sha(fixture) == module.STALE_U0N_INSTRUMENTED_SHA256
assert module.declared_instrumented_sha(patched) == corrected_sha
assert patched.count("candidate=U0p-corrected-sshd-source-hash stage=trace-open") == 1
assert "candidate=U0o-persistent-sshd-trace stage=trace-open" not in patched

# Safety tokens are intentionally present in the Python builder's fail-closed
# denylist. Validate that they are absent from the generated shell payload,
# rather than incorrectly rejecting the source code for mentioning them.
for forbidden in (
    'mount -o remount,rw /sysroot',
    'rm -rf "/sysroot"',
    "rm -rf /sysroot",
    "> /sysroot/etc/",
    "dd if=",
    "mkfs",
    "wipefs",
):
    assert forbidden not in patched, forbidden

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    init = root / "init_2nd.sh"
    sshd = root / "embedded-sshd.initd"
    init.write_text(patched, encoding="utf-8")
    sshd.write_bytes(module.embedded_sshd_bytes(patched))
    subprocess.run(["sh", "-n", str(init)], check=True)
    subprocess.run(["sh", "-n", str(sshd)], check=True)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "embedded_sshd_bytes(original)",
    "actual_embedded_sha = v2.sha_bytes(embedded)",
    "U0N_SSHD_INSTRUMENTED_SHA=",
    "embedded_sshd_bytes(patched) != embedded",
    "runtime_failure_fixed",
    "instrumented-source-hash-mismatch",
    "embedded_instrumented_sshd_bytes_preserved",
    "phone_partition_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4 -a",
):
    assert forbidden not in source, forbidden

print("a33_u0p_builder_self_test=passed")
print("exact_u0o_parent_artifact_and_blob_pins=passed")
print("stale_u0n_hash_mismatch_reproduction=passed")
print("embedded_heredoc_bytes_preserved=passed")
print("declared_runtime_hash_corrected_to_embedded_bytes=passed")
print("trace_candidate_label_updated=passed")
print("generated_payload_denylist_contract=passed")
print("host_only_and_phone_write_absence=passed")
print("shell_syntax_validation=passed")
