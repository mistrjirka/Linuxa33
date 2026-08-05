#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import inspect
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "flash-a33-u0n-real-boot-sshd-trace-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0n_flash_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_BLOB == "35caa92b0271c2d0b01460db62c30ecfb0208ddc"
assert module.SAFE_DELIMITER == ":"
assert module.base.validate_phone_rootfs is module.validate_phone_rootfs
assert "IFS=':'" in module.KEY_CHECK_SCRIPT
assert "IFS='|'" not in module.KEY_CHECK_SCRIPT

contracts = module.key_contract_arguments()
assert len(contracts) == 8
assert all(contract.count(":") == 3 for contract in contracts)
assert all("|" not in contract for contract in contracts)
assert contracts[0].startswith("ssh_host_ecdsa_key:private:")
assert contracts[-1].startswith("ssh_host_rsa_key.pub:public:")

source = MODULE.read_text(encoding="utf-8")
for required in (
    "key_contract_arguments()",
    "SAFE_DELIMITER.join",
    "ssh_key_contract_transport=colon-delimited-shell-safe",
    "base.validate_phone_rootfs = validate_phone_rootfs",
    "return base.main()",
):
    assert required in source, required
for forbidden in (
    'f"{name}|{kind}|{sha}|{mode}"',
    "adb reboot",
    "mkfs",
    "wipefs",
    "fastboot",
    "mount -o remount,rw",
):
    assert forbidden not in source, forbidden

validate_source = inspect.getsource(module.validate_phone_rootfs)
assert "*key_contract_arguments()" in validate_source
assert "base.KEY_CHECK_SCRIPT" not in validate_source

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "key-check.sh"
    path.write_text(module.KEY_CHECK_SCRIPT, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

print("a33_u0n_flash_v2_self_test=passed")
print("exact_base_flash_blob_pin=passed")
print("colon_delimited_shell_safe_key_contract=passed")
print("pipe_metacharacter_transport_absence=passed")
print("phone_validation_routed_through_corrected_contract=passed")
print("phone_write_scope_unchanged=passed")
print("shell_syntax_validation=passed")
