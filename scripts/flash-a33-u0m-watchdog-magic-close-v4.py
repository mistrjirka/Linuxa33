#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "flash-a33-u0m-watchdog-magic-close-v3.py"
AUDIT = HERE / "audit-a33-u0m-candidate-v4.py"
EXPECTED_BASE_BLOB = "c40067bcacf62523a624dc173e0a89b7fc217f3a"
EXPECTED_AUDIT_BLOB = "b58d76df2681df7a23e589eb50760d8f26e99d59"

spec = importlib.util.spec_from_file_location("a33_u0m_v4_flash_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0m v3 flash path: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

base.builder.base.u0l.u0j = base.builder.base.u0l.u0k.u0j
base.AUDIT = AUDIT
base.EXPECTED_AUDIT_BLOB = EXPECTED_AUDIT_BLOB


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
