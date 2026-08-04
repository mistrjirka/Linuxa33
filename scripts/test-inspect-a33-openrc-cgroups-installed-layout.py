#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path, PurePosixPath
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "inspect-a33-openrc-cgroups-installed-layout.py"
spec = importlib.util.spec_from_file_location("a33_openrc_installed_layout_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert PurePosixPath("/usr/libexec/rc") in module.base.SEARCH_ROOTS
assert PurePosixPath("/lib/rc") in module.base.SEARCH_ROOTS
assert PurePosixPath("/usr/libexec/rc/sh/openrc-run.sh") in module.base.PRESERVE_FILES
assert PurePosixPath("/usr/libexec/rc/sh/rc-cgroup.sh") in module.base.PRESERVE_FILES
assert PurePosixPath("/etc/init.d/cgroups") in module.base.PRESERVE_FILES
assert len(module.base.SEARCH_ROOTS) == len(set(module.base.SEARCH_ROOTS))
assert len(module.base.PRESERVE_FILES) == len(set(module.base.PRESERVE_FILES))

print("a33_openrc_installed_layout_self_test=passed")
print("openrc_libexec_search_root=passed")
print("legacy_openrc_search_root_preserved=passed")
print("installed_cgroup_implementation_preservation=passed")
print("duplicate_path_refusal_fixture=passed")
