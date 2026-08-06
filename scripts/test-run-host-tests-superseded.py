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

expected = {
    "test-a33-installed-ssh-keygen-tmpfs.py": (
        "test-a33-installed-ssh-keygen-tmpfs-v2.py"
    ),
    "test-make-u0n-real-boot-sshd-trace.py": (
        "test-make-u0n-real-boot-sshd-trace-v2.py"
    ),
    "test-make-u0o-persistent-sshd-trace.py": (
        "test-make-u0o-persistent-sshd-trace-v2.py"
    ),
}
assert module.SUPERSEDED_LEGACY_TESTS == expected

with tempfile.TemporaryDirectory() as temp:
    scripts = Path(temp)
    unrelated = scripts / "test-unrelated.py"
    unrelated.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    for old_name, replacement_name in expected.items():
        (scripts / old_name).write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (scripts / replacement_name).write_text(
            "#!/usr/bin/env python3\n", encoding="utf-8"
        )

    selected, skipped = module.discover_legacy_tests(scripts)
    assert {path.name for path in selected} == {
        unrelated.name,
        *expected.values(),
    }
    assert skipped == sorted(expected.items())

    for restored_old, missing_replacement in expected.items():
        replacement_path = scripts / missing_replacement
        replacement_path.unlink()
        selected, skipped = module.discover_legacy_tests(scripts)
        selected_names = {path.name for path in selected}
        assert restored_old in selected_names
        assert missing_replacement not in selected_names
        assert (restored_old, missing_replacement) not in skipped
        for other_old, other_replacement in expected.items():
            if other_old == restored_old:
                continue
            assert other_old not in selected_names
            assert other_replacement in selected_names
            assert (other_old, other_replacement) in skipped
        replacement_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

print("run_host_tests_superseded_selection_self_test=passed")
print("all_corrected_replacements_skip_obsolete_tests=passed")
print("replacement_missing_preserves_each_base_test=passed")
