#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
import hashlib
from pathlib import Path
import sys

EXPECTED_CONFIG_SHA256 = (
    "7dd732d5b653571497e3e77d286705efc5b4247dcdc937afffc54827b4f3997c"
)
EXPECTED_VALUES = {
    "CONFIG_WATCHDOG": "y",
    "CONFIG_WATCHDOG_CORE": "y",
    "CONFIG_WATCHDOG_NOWAYOUT": "explicitly-not-set",
    "CONFIG_WATCHDOG_HANDLE_BOOT_ENABLED": "y",
    "CONFIG_WATCHDOG_OPEN_TIMEOUT": "0",
    "CONFIG_S3C2410_WATCHDOG": "m",
}


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class WatchdogKernelContract:
    config_path: Path
    config_sha256: str
    values: dict[str, str]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def config_value(text: str, key: str) -> str:
    enabled = f"{key}="
    disabled = f"# {key} is not set"
    matches: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(enabled):
            matches.append(line[len(enabled) :])
        elif line == disabled:
            matches.append("explicitly-not-set")
    if len(matches) != 1:
        raise ContractError(
            f"expected exactly one config value for {key}, found {matches!r}"
        )
    return matches[0]


def inspect_config(
    config_path: Path,
    *,
    expected_sha256: str = EXPECTED_CONFIG_SHA256,
) -> WatchdogKernelContract:
    config_path = config_path.expanduser().resolve()
    if not config_path.is_file():
        raise ContractError(f"missing TWRP runtime config: {config_path}")
    payload = config_path.read_bytes()
    actual_sha256 = sha256_bytes(payload)
    if actual_sha256 != expected_sha256:
        raise ContractError(
            "TWRP runtime config SHA256 mismatch: "
            f"actual={actual_sha256} expected={expected_sha256}"
        )
    try:
        text = gzip.decompress(payload).decode("utf-8", errors="strict")
    except (gzip.BadGzipFile, UnicodeDecodeError) as exc:
        raise ContractError(f"cannot decode TWRP runtime config: {exc}") from exc

    values = {key: config_value(text, key) for key in EXPECTED_VALUES}
    mismatches = [
        f"{key}: actual={values[key]!r} expected={expected!r}"
        for key, expected in EXPECTED_VALUES.items()
        if values[key] != expected
    ]
    if mismatches:
        raise ContractError(
            "watchdog kernel config contract failed:\n" + "\n".join(mismatches)
        )
    return WatchdogKernelContract(config_path, actual_sha256, values)


def write_report(contract: WatchdogKernelContract, report: Path) -> None:
    report = report.expanduser().resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, object]] = [
        ("operation", "inspect-a33-watchdog-kernel-contract"),
        ("implementation_language", "python3"),
        ("config_source", "/proc/config.gz"),
        ("config_path", contract.config_path),
        ("config_sha256", contract.config_sha256),
        *[(f"runtime_{key}", value) for key, value in contract.values.items()],
        ("watchdog_magic_close_supported_by_config", "yes"),
        ("runtime_module_parameter_required", "no"),
        ("watchdog_class_state_required", "no"),
        ("phone_partition_writes", "no"),
        ("inspection_status", "passed"),
    ]
    report.write_text(
        "".join(f"{key}={value}\n" for key, value in rows), encoding="utf-8"
    )
    for key, value in rows:
        print(f"{key}={value}")
    print(f"report={report}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate exact A33 TWRP watchdog kernel configuration"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    config = (
        args.config.expanduser().resolve()
        if args.config is not None
        else root / "build/a33-twrp-runtime-config.gz"
    )
    report = (
        args.report.expanduser().resolve()
        if args.report is not None
        else root / "build/a33-watchdog-kernel-contract.txt"
    )
    contract = inspect_config(config)
    write_report(contract, report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, ValueError) as exc:
        print(f"WATCHDOG KERNEL CONTRACT FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
