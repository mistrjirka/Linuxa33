#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "u0j_builder", HERE / "make-u0j-python-root-api-compatible.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

sample = '''find_root_partition() {
\techo old
}
wait_root_partition() {
\twhile [ -z "$(find_root_partition)" ]; do sleep 1; done
}
resize_root_partition() {
\tlocal partition
\tfind_root_partition partition
}
resize_root_filesystem() {
\tlocal partition
\tfind_root_partition partition
}
mount_root_partition() {
\tlocal partition
\tfind_root_partition partition
}
probe_root() {
\tfind_root_partition
}
'''

calls = module.classify_find_root_calls({"init_functions.sh": sample})
modes = [mode for _, _, mode, _ in calls]
assert modes.count("stdout-command-substitution") == 1
assert modes.count("stdout-direct") == 1
assert modes.count("output-variable:partition") == 3

patched, original_find, original_wait, replacement = module.patch_find_only(sample)
assert "partition=\"$a33x_root\"" in replacement
assert 'case "$#" in' in replacement
assert "mode=stdout" in replacement
assert "mode=output-variable" in replacement
assert module.v2.function_span(patched, "wait_root_partition")[2] == original_wait
assert module.v2.mask_functions(patched, ("find_root_partition",)) == module.v2.mask_functions(
    sample, ("find_root_partition",)
)

unsupported = sample.replace(
    "find_root_partition partition", "find_root_partition DEVICE", 1
)
try:
    module.classify_find_root_calls({"init_functions.sh": unsupported})
except module.Refusal:
    pass
else:
    raise AssertionError("unsupported output-variable API was accepted")

# Verify the exact assignment technique used by U0j with the host's POSIX shell.
# BusyBox ash and dash use dynamic scoping for local variables, so assigning
# 'partition' in the called function updates the caller's local variable.
fixture = '''find_fixture() {
\ta33x_root=/dev/block/sda36
\tcase "$#" in
\t\t0) printf '%s\\n' "$a33x_root" ;;
\t\t1) [ "$1" = partition ] || return 2; partition="$a33x_root" ;;
\t\t*) return 2 ;;
\tesac
\tunset a33x_root
}
consumer() {
\tlocal partition
\tfind_fixture partition
\t[ "$partition" = /dev/block/sda36 ]
}
consumer
[ "$(find_fixture)" = /dev/block/sda36 ]
'''
with tempfile.NamedTemporaryFile("w", delete=False) as stream:
    stream.write(fixture)
    fixture_path = stream.name
subprocess.run(["sh", fixture_path], check=True)
busybox = shutil.which("busybox")
if busybox:
    subprocess.run([busybox, "sh", fixture_path], check=True)

print("u0j_python_self_test=passed")
print("find_root_stdout_api=passed")
print("find_root_output_variable_api=passed")
print("caller_local_dynamic_assignment=passed")
print("unsupported_call_shape_refusal=passed")
print("wait_root_function_preserved=passed")
print("only_find_root_function_changed=passed")
