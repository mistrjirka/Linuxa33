#!/usr/bin/env python3
from __future__ import annotations

import gzip
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from a33_cpio import Archive
from a33_shell import function_span, replace_function, root_consumption_mode, second_stage_calls


def entry(name: str, data: bytes, *, magic: bytes, inode: int, mode: int = 0o100755) -> bytes:
    encoded = name.encode() + b"\0"
    checksum = sum(data) & 0xffffffff if magic == b"070702" else 0
    fields = [inode, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(encoded), checksum]
    header = magic + b"".join(f"{value:08x}".encode() for value in fields)
    return header + encoded + b"\0" * ((-(110 + len(encoded))) % 4) + data + b"\0" * ((-len(data)) % 4)


def archive(magic: bytes, wait: str, prefix: str = "") -> bytes:
    functions = f'''find_root_partition() {{\n\techo old\n}}\nwait_root_partition() {{\n{wait}\n}}\n'''.encode()
    init2 = b'''# switch_root in a comment must be ignored\nrun_hooks /hooks\nwait_root_partition\nresize_root_partition\nresize_root_filesystem\nmount_root_partition\nexec switch_root /sysroot /sbin/init\n'''
    rows = [
        entry(prefix + "init_functions.sh", functions, magic=magic, inode=1),
        entry(prefix + "init_2nd.sh", init2, magic=magic, inode=2),
        entry(prefix + "unchanged", b"same", magic=magic, inode=3),
        entry("TRAILER!!!", b"", magic=magic, inode=0, mode=0),
    ]
    return b"".join(rows) + b"\0" * 12


def run_case(magic: bytes, wait: str, expected_mode: str, prefix: str = "") -> None:
    original = Archive.parse(archive(magic, wait, prefix))
    script = original.one("init_functions.sh").data.decode()
    original_wait = function_span(script, "wait_root_partition")[2]
    replacement = "find_root_partition() {\n\tprintf '%s\\n' /dev/block/sda36\n}\n"
    patched_script, _ = replace_function(script, "find_root_partition", replacement)
    assert function_span(patched_script, "wait_root_partition")[2] == original_wait
    assert root_consumption_mode(original_wait) == expected_mode
    patched_payload = original.replace("init_functions.sh", patched_script.encode())
    patched = Archive.parse(patched_payload)
    original.assert_only_payload_changed(patched, "init_functions.sh")
    assert patched.one("unchanged").data == b"same"
    assert patched.tail == b"\0" * 12
    assert [label for label, _, _ in second_stage_calls(original.one("init_2nd.sh").data.decode())] == [
        "run_hooks /hooks",
        "wait_root_partition",
        "resize_root_partition",
        "resize_root_filesystem",
        "mount_root_partition",
        "switch_root",
    ]
    assert gzip.decompress(gzip.compress(patched_payload, mtime=0)) == patched_payload


def main() -> None:
    run_case(b"070701", '\twhile [ -z "$(find_root_partition)" ]; do sleep 1; done', "empty-test")
    run_case(b"070702", '\troot="$(find_root_partition)"', "assignment:root", "./")
    print("u0i_python_self_test=passed")
    print("cpio_newc=passed")
    print("cpio_crc=passed")
    print("leading_dot_paths=passed")
    print("empty_test_root_consumption=passed")
    print("assignment_root_consumption=passed")
    print("only_target_payload_changed=passed")
    print("second_stage_executable_order=passed")


if __name__ == "__main__":
    main()
