#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "u0i_inspector", HERE / "inspect-u0i-root-handoff-structure.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

source = '''# ignored find_root_partition\n. /init_functions.sh\n. /init_functions_2nd.sh\nfind_root_partition() {\n\techo /dev/block/sda36\n}\nwait_root_partition() {\n\twhile [ -z "$(find_root_partition)" ]; do sleep 1; done\n}\nmount_root_partition() {\n\tpartition="$(find_root_partition)"\n\tmount "$partition" /sysroot\n}\nmount_boot_partition() {\n\tmount /dev/loop0 "$1"\n}\n'''
texts = {"init_2nd.sh": source}

find = module.function_spans(source, "find_root_partition")
wait = module.function_spans(source, "wait_root_partition")
assert len(find) == 1
assert len(wait) == 1
assert "/dev/block/sda36" in find[0][2]
assert "$(find_root_partition)" in wait[0][2]

sources = module.source_lines(texts)
assert len(sources) == 2
assert sources[0].endswith(". /init_functions.sh")
assert sources[1].endswith(". /init_functions_2nd.sh")

relevant = module.relevant_lines(texts)
assert any("mount_root_partition" in row for row in relevant)
assert any("/dev/loop0" in row for row in relevant)
assert not any("ignored find_root_partition" in row for row in relevant)

print("u0i_root_handoff_inspector_self_test=passed")
print("function_definition_parser=passed")
print("source_order_parser=passed")
print("comment_filter=passed")
print("loop0_reference_detection=passed")
