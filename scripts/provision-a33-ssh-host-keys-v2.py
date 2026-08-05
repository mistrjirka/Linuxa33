#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "provision-a33-ssh-host-keys.py"
EXPECTED_BASE_BLOB = "535bfd2bb920e6ee1c6d82e756e327bb0b7f58a5"

spec = importlib.util.spec_from_file_location("a33_ssh_host_key_provision_v2_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load SSH host-key provisioner: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

OLD = 'rmdir "$staging"\n'
NEW = 'rm -rf "$staging"\n'
if base.REMOTE_SCRIPT.count(OLD) != 1:
    raise SystemExit("SSH host-key staging cleanup anchor changed")
base.REMOTE_SCRIPT = base.REMOTE_SCRIPT.replace(OLD, NEW)


def main() -> int:
    repo = Path.home() / "Linuxa33"
    completed = base.common.run(
        ["git", "-C", str(repo), "hash-object", str(BASE)],
        check=False,
    )
    actual = completed.stdout.strip()
    if actual != EXPECTED_BASE_BLOB:
        raise base.ProvisionError(
            "checked-in SSH host-key provisioner changed: "
            f"actual={actual} expected={EXPECTED_BASE_BLOB}"
        )
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
