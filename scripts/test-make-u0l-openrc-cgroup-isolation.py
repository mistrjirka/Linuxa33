#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0l-openrc-cgroup-isolation.py"
spec = importlib.util.spec_from_file_location("a33_u0l_builder_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

fixture = f'''#!/bin/sh
{module.ANCHOR}printf '<6>a33x-u0k-direct-mount: stage=cleanup-hooks-begin\\n' > /dev/kmsg 2>/dev/null || true
exec switch_root /sysroot "$init"
'''
patched = module.patch_init_second(fixture)
assert patched.startswith("#!/bin/sh\n")
assert patched.count(module.ANCHOR) == 1
assert patched.count('mount -o bind /dev/null "$OPENRC_CGROUP_SH"') == 1
assert patched.count("/sysroot/usr/libexec/rc/sh/rc-cgroup.sh") == 1
assert patched.count("while true; do sleep 3600; done") == 3
assert "sed -i" not in patched
assert 'rm "$OPENRC_CGROUP_SH"' not in patched
assert 'cp /dev/null "$OPENRC_CGROUP_SH"' not in patched
assert '> "$OPENRC_CGROUP_SH"' not in patched
assert patched.index("stage=mask-success") < patched.index("stage=cleanup-hooks-begin")
assert patched.index("stage=cleanup-hooks-begin") < patched.index("exec switch_root")

with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "init_2nd.sh"
    path.write_text(patched, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

for bad in (
    fixture.replace(module.ANCHOR, ""),
    fixture.replace(module.ANCHOR, module.ANCHOR * 2),
    fixture.replace("#!/bin/sh\n", f"#!/bin/sh\n# {module.MARKER_PREFIX}\n"),
):
    try:
        module.patch_init_second(bad)
    except module.Refusal:
        pass
    else:
        raise AssertionError("unsafe U0l fixture was accepted")

for snippet in module.REQUIRED_OPENRC_SNIPPETS:
    assert snippet
assert module.EXPECTED_ROOTFS_SHA256 == (
    "79c94efe41da14e72e82cfc66c8e6fac6f04482fa2ea2af024f6b1ebb67d3951"
)
assert module.EXPECTED_OPENRC_VERSION == "0.63.2-r0"

print("a33_u0l_openrc_cgroup_isolation_self_test=passed")
print("exact_u0k_anchor_patch=passed")
print("runtime_bind_mask_contract=passed")
print("persistent_rootfs_write_refusal=passed")
print("mask_before_switch_root_order=passed")
print("shell_syntax_validation=passed")
print("missing_duplicate_anchor_refusal=passed")
print("preexisting_marker_refusal=passed")
print("rootfs_and_openrc_identity_pinned=passed")
