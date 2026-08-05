#!/usr/bin/env python3
from __future__ import annotations

import gzip
import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "inspect-a33-watchdog-kernel-contract.py"
spec = importlib.util.spec_from_file_location("a33_watchdog_contract_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

fixture_text = """CONFIG_WATCHDOG=y
CONFIG_WATCHDOG_CORE=y
# CONFIG_WATCHDOG_NOWAYOUT is not set
CONFIG_WATCHDOG_HANDLE_BOOT_ENABLED=y
CONFIG_WATCHDOG_OPEN_TIMEOUT=0
CONFIG_S3C2410_WATCHDOG=m
"""

assert module.config_value(fixture_text, "CONFIG_WATCHDOG") == "y"
assert (
    module.config_value(fixture_text, "CONFIG_WATCHDOG_NOWAYOUT")
    == "explicitly-not-set"
)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    config = root / "config.gz"
    payload = gzip.compress(fixture_text.encode(), mtime=0)
    config.write_bytes(payload)
    contract = module.inspect_config(
        config, expected_sha256=module.sha256_bytes(payload)
    )
    assert contract.values == module.EXPECTED_VALUES
    assert contract.config_sha256 == module.sha256_bytes(payload)

    bad = root / "bad.gz"
    bad.write_bytes(
        gzip.compress(
            fixture_text.replace(
                "# CONFIG_WATCHDOG_NOWAYOUT is not set",
                "CONFIG_WATCHDOG_NOWAYOUT=y",
            ).encode(),
            mtime=0,
        )
    )
    try:
        module.inspect_config(bad, expected_sha256=module.sha256_bytes(bad.read_bytes()))
    except module.ContractError:
        pass
    else:
        raise AssertionError("enabled WATCHDOG_NOWAYOUT was accepted")

    duplicate = root / "duplicate.gz"
    duplicate_payload = gzip.compress(
        (fixture_text + "CONFIG_WATCHDOG=y\n").encode(), mtime=0
    )
    duplicate.write_bytes(duplicate_payload)
    try:
        module.inspect_config(
            duplicate, expected_sha256=module.sha256_bytes(duplicate_payload)
        )
    except module.ContractError:
        pass
    else:
        raise AssertionError("duplicate kernel config value was accepted")

assert module.EXPECTED_CONFIG_SHA256 == (
    "7dd732d5b653571497e3e77d286705efc5b4247dcdc937afffc54827b4f3997c"
)
print("a33_watchdog_kernel_contract_self_test=passed")
print("explicit_nowayout_disabled_contract=passed")
print("watchdog_boot_handover_contract=passed")
print("enabled_nowayout_refusal=passed")
print("duplicate_config_value_refusal=passed")
print("runtime_config_identity_pinned=passed")
