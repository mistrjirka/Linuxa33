#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "run-host-tests.py"

spec = importlib.util.spec_from_file_location("a33_run_host_tests_selection", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

old_name = "test-a33-installed-ssh-keygen-tmpfs.py"
replacement_name = "test-a33-installed-ssh-keygen-tmpfs-v2.py"
assert module.SUPERSEDED_LEGACY_TESTS[old_name] == replacement_name

with tempfile.TemporaryDirectory() as temp:
    scripts = Path(temp)
    old = scripts / old_name
    replacement = scripts / replacement_name
    unrelated = scripts / "test-unrelated.py"
    for path in (old, replacement, unrelated):
        path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    selected, skipped = module.discover_legacy_tests(scripts)
    assert [path.name for path in selected] == [replacement_name, unrelated.name]
    assert skipped == [(old_name, replacement_name)]

    replacement.unlink()
    selected, skipped = module.discover_legacy_tests(scripts)
    assert [path.name for path in selected] == [old_name, unrelated.name]
    assert skipped == []

print("run_host_tests_superseded_selection_self_test=passed")
print("replacement_present_skips_obsolete_runtime_test=passed")
print("replacement_missing_preserves_base_test=passed")
