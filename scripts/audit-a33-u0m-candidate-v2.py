#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "audit-a33-u0m-candidate.py"
BUILDER_V2 = HERE / "make-u0m-watchdog-magic-close-v2.py"
EXPECTED_U0M_BUILDER_BLOB = "19cb63ea55ecfb7a186016058b7303b4326c9030"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load("a33_u0m_audit_v2_base", BASE)
builder_v2 = load("a33_u0m_audit_v2_builder", BUILDER_V2)

# The exact-delta logic remains unchanged. The audited file identity is still
# the pinned base builder, while the transformation function uses the corrected
# v2 cardinality check for the two before/after kernel-log occurrences.
base.EXPECTED_U0M_BUILDER_BLOB = EXPECTED_U0M_BUILDER_BLOB
base.u0m.patch_watchdog_hook = builder_v2.patch_watchdog_hook


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
