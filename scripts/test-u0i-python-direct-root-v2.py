#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
from a33_cpio import Archive

spec = importlib.util.spec_from_file_location("u0i_v2", HERE / "make-u0i-python-direct-root-v2.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

CASES = {
    "direct-empty-test": '''find_root_partition() {\n\techo old\n}\nwait_root_partition() {\n\twhile [ -z "$(find_root_partition)" ]; do sleep 1; done\n}\nkeep() { echo same; }\n''',
    "indirect-helper": '''find_root_partition() {\n\tfind_partition_by_label pmOS_root\n}\nwait_root_partition() {\n\twait_for_partition_label pmOS_root\n}\nkeep() { echo same; }\n''',
    "global-state": '''find_root_partition() {\n\techo "$DEVICE"\n}\nwait_root_partition() {\n\twhile [ -z "$DEVICE" ]; do probe_root; done\n}\nkeep() { echo same; }\n''',
}


def entry(name: str, data: bytes, *, magic: bytes = b"070701", inode: int = 1, mode: int = 0o100755) -> bytes:
    encoded = name.encode() + b"\0"
    checksum = sum(data) & 0xFFFFFFFF if magic == b"070702" else 0
    fields = [inode, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(encoded), checksum]
    header = magic + b"".join(f"{value:08x}".encode() for value in fields)
    return header + encoded + b"\0" * ((-(110 + len(encoded))) % 4) + data + b"\0" * ((-len(data)) % 4)


for name, source in CASES.items():
    patched, parts = module.patch_root_functions(source)
    assert module.mask_functions(source, ("find_root_partition", "wait_root_partition")) == module.mask_functions(patched, ("find_root_partition", "wait_root_partition"))
    assert "/dev/block/sda36" in parts["patched_find"]
    assert 'LABEL="pmOS_root"' in parts["patched_find"]
    assert '$(find_root_partition)' in parts["patched_wait"]
    assert "keep() { echo same; }" in patched

    payload = b"".join([
        entry("init_functions.sh", source.encode(), inode=1),
        entry("unchanged", b"same", inode=2),
        entry("TRAILER!!!", b"", inode=0, mode=0),
    ]) + b"\0" * 12
    before = Archive.parse(payload)
    after_payload = before.replace("init_functions.sh", patched.encode())
    after = Archive.parse(after_payload)
    before.assert_only_payload_changed(after, "init_functions.sh")
    assert after.one("unchanged").data == b"same"
    assert after.tail == before.tail
    print(f"root_shape_{name}=passed")

print("u0i_python_v2_self_test=passed")
print("old_root_call_graph_not_required=yes")
print("only_two_shell_functions_changed=yes")
print("only_init_functions_cpio_payload_changed=yes")
