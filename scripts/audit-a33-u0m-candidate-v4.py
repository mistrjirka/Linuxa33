#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "audit-a33-u0m-candidate-v3.py"
EXPECTED_BASE_BLOB = "d4b5b3d1ef271b4d02d1ca77592a1c1d8e3bf356"

spec = importlib.util.spec_from_file_location("a33_u0m_v4_audit_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0m v3 audit: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

base.builder.base.u0l.u0j = base.builder.base.u0l.u0k.u0j


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
