#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "audit-a33-u0m-candidate.py"
EXPECTED_U0M_BUILDER_BLOB = "19cb63ea55ecfb7a186016058b7303b4326c9030"

spec = importlib.util.spec_from_file_location("a33_u0m_audit_v2_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0m audit: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

# The exact-delta logic remains unchanged. Only the pinned builder identity
# changes because U0m now verifies stop through the driver's kernel log rather
# than nonexistent watchdog class attributes.
base.EXPECTED_U0M_BUILDER_BLOB = EXPECTED_U0M_BUILDER_BLOB


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
