#!/usr/bin/env python3
from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

HERE = Path(__file__).resolve().parent
MODULE = HERE / "lib/a33_ext4_identity_text.py"
spec = importlib.util.spec_from_file_location("a33_ext4_identity_text_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

expected_uuid = uuid.UUID("7b056328-bdfb-496b-ac38-2624c43c863a")
payload = bytearray(2048)
superblock = memoryview(payload)[1024:2048]
superblock[56:58] = b"\x53\xef"
superblock[104:120] = expected_uuid.bytes
superblock[120:129] = b"pmOS_root"
encoded = base64.b64encode(payload).decode()
fixture = f"""
dd_rc=0
payload_bytes=2048
error_b64_begin

error_b64_end
payload_b64_begin
{encoded}
payload_b64_end
phone_partition_writes=no
temporary_files=/tmp-only
"""
assert module.parse_read_output(fixture) == (str(expected_uuid), "pmOS_root")

failed = """
dd_rc=1
payload_bytes=0
error_b64_begin
ZGRkOiByZWFkIGVycm9yCg==
error_b64_end
payload_b64_begin
payload_b64_end
phone_partition_writes=no
temporary_files=/tmp-only
"""
try:
    module.parse_read_output(failed)
except module.Ext4IdentityError as exc:
    assert "dd_rc='1'" in str(exc)
    assert "read error" in str(exc)
else:
    raise AssertionError("failed dd read was accepted")

script = module.READ_SCRIPT
for required in (
    'dd if="$target" of="$payload" bs=2048 count=1',
    'base64 "$payload"',
    'base64 "$error"',
    'trap cleanup EXIT',
    'phone_partition_writes=no',
    'temporary_files=/tmp-only',
):
    assert required in script
for forbidden in (
    'of="$target"',
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "mkfs",
    "wipefs",
    "adb reboot",
    "odin4",
):
    assert forbidden not in script

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "read.sh"
    path.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

print("a33_ext4_identity_text_self_test=passed")
print("text_safe_base64_transport=passed")
print("byte_level_ext4_magic_uuid_label_parser=passed")
print("remote_dd_error_surface_contract=passed")
print("temporary_file_cleanup_contract=passed")
print("phone_partition_write_and_reboot_absence=passed")
print("shell_syntax_validation=passed")
