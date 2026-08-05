#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "provision-a33-ssh-host-keys-v2.py"
spec = importlib.util.spec_from_file_location("a33_ssh_host_key_provision_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_BLOB == "535bfd2bb920e6ee1c6d82e756e327bb0b7f58a5"
assert module.base.CONFIRMATION == "PROVISION-EXACT-SSH-HOST-KEYS"
assert 'rmdir "$staging"' not in module.base.REMOTE_SCRIPT
assert 'rm -rf "$staging"' in module.base.REMOTE_SCRIPT
assert "rollback_generated_keys" in module.base.REMOTE_SCRIPT
assert "persistent_host_key_provision=passed" in module.base.REMOTE_SCRIPT
assert "userdata_written=yes-etc-ssh-host-keys-only" in module.base.REMOTE_SCRIPT

with tempfile.TemporaryDirectory() as temporary:
    script = Path(temporary) / "remote.sh"
    script.write_text(module.base.REMOTE_SCRIPT, encoding="utf-8")
    subprocess.run(["sh", "-n", str(script)], check=True)

print("a33_ssh_host_key_provision_v2_self_test=passed")
print("exact_base_blob_pinned=passed")
print("staging_cleanup_command_availability=passed")
print("rollback_contract_preserved=passed")
print("generated_host_key_only_scope_preserved=passed")
print("shell_syntax_validation=passed")
