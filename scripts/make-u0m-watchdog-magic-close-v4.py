#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "make-u0m-watchdog-magic-close-v3.py"
EXPECTED_BASE_BLOB = "1e48bdd42905845046fc95e28e3cd597ae350df1"

spec = importlib.util.spec_from_file_location("a33_u0m_v4_builder_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0m v3 builder: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

# make-u0l exposes the U0j helper through u0k, not directly.
base.base.u0l.u0j = base.base.u0l.u0k.u0j


def main() -> int:
    repo = Path.home() / "Linuxa33"
    if base.base.u0l.u0j.git_blob(repo, BASE) != EXPECTED_BASE_BLOB:
        base.base.refuse("checked-in U0m v3 builder changed unexpectedly")
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
