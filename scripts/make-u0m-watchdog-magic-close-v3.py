#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "make-u0m-watchdog-magic-close.py"
INSPECTOR = HERE / "inspect-a33-watchdog-kernel-contract.py"
EXPECTED_BASE_BLOB = "19cb63ea55ecfb7a186016058b7303b4326c9030"
EXPECTED_INSPECTOR_BLOB = "ea17562fba369bba3da81c291e22a15c663c929d"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load("a33_u0m_v3_base", BASE)
inspector = load("a33_u0m_v3_contract", INSPECTOR)

REPLACEMENT_FEEDER_BLOCK = f'''WATCHDOG_SHUTDOWN_REQUEST=/run/a33x-watchdog.shutdown-request
WATCHDOG_SHUTDOWN_STATUS=/run/a33x-watchdog.shutdown-status
rm -f "$WATCHDOG_SHUTDOWN_REQUEST" "$WATCHDOG_SHUTDOWN_STATUS"

watchdog_log_count()
{{
\t/bin/busybox dmesg 2>/dev/null |
\t\t/bin/busybox grep -F -c "$1" 2>/dev/null || true
}}

(
\tif ! exec 3>"$watchdog_device"; then
\t\tlog_a33x_watchdog "ERROR: failed to open $watchdog_device"
\t\texit 1
\tfi

\tlog_a33x_watchdog "opened $watchdog_device; feeding every second; logging every 8 pings"

\tping_count=0
\twhile true; do
\t\tif [ -f "$WATCHDOG_SHUTDOWN_REQUEST" ]; then
\t\t\tstop_before="$(watchdog_log_count '{base.STOP_LOG}')"
\t\t\tdid_not_stop_before="$(watchdog_log_count '{base.DID_NOT_STOP_LOG}')"
\t\t\tlog_a33x_watchdog "shutdown requested stop_before=${{stop_before:-missing}} did_not_stop_before=${{did_not_stop_before:-missing}} config_nowayout=disabled"

\t\t\tif ! printf 'V' >&3; then
\t\t\t\tprintf '%s\\n' "failed-magic-write" > "$WATCHDOG_SHUTDOWN_STATUS"
\t\t\t\trm -f "$WATCHDOG_SHUTDOWN_REQUEST"
\t\t\t\tlog_a33x_watchdog "ERROR: magic-close write failed"
\t\t\telse
\t\t\t\texec 3>&-
\t\t\t\tsleep 1
\t\t\t\tstop_after="$(watchdog_log_count '{base.STOP_LOG}')"
\t\t\t\tdid_not_stop_after="$(watchdog_log_count '{base.DID_NOT_STOP_LOG}')"
\t\t\t\tlog_a33x_watchdog "magic close observed stop_after=${{stop_after:-missing}} did_not_stop_after=${{did_not_stop_after:-missing}}"

\t\t\t\tverified=no
\t\t\t\tif [ -n "$stop_before" ] && [ -n "$stop_after" ] &&
\t\t\t\t   [ -n "$did_not_stop_before" ] && [ -n "$did_not_stop_after" ] &&
\t\t\t\t   [ "$stop_after" -gt "$stop_before" ] 2>/dev/null &&
\t\t\t\t   [ "$did_not_stop_after" -eq "$did_not_stop_before" ] 2>/dev/null; then
\t\t\t\t\tverified=yes
\t\t\t\tfi

\t\t\t\tif [ "$verified" = yes ]; then
\t\t\t\t\tprintf '%s\\n' "stopped" > "$WATCHDOG_SHUTDOWN_STATUS"
\t\t\t\t\tlog_a33x_watchdog "watchdog stopped for rootfs handoff; driver stop log verified"
\t\t\t\t\texit 0
\t\t\t\tfi

\t\t\t\tlog_a33x_watchdog "ERROR: watchdog stop was not proven; reopening and continuing to feed"
\t\t\t\tif ! exec 3>"$watchdog_device"; then
\t\t\t\t\tprintf '%s\\n' "failed-reopen" > "$WATCHDOG_SHUTDOWN_STATUS"
\t\t\t\t\texit 1
\t\t\t\tfi
\t\t\t\tprintf 'K' >&3 || true
\t\t\t\tprintf '%s\\n' "failed-unverified-stop" > "$WATCHDOG_SHUTDOWN_STATUS"
\t\t\t\trm -f "$WATCHDOG_SHUTDOWN_REQUEST"
\t\t\tfi
\t\tfi

\t\tif ! printf 'K' >&3; then
\t\t\tlog_a33x_watchdog "ERROR: watchdog write failed"
\t\t\texit 1
\t\tfi
\t\tping_count=$((ping_count + 1))
\t\tif [ $((ping_count % 8)) -eq 0 ]; then
\t\t\tlog_a33x_watchdog "ping=$ping_count device=$watchdog_device"
\t\tfi
\t\tsleep 1
\tdone
) &

a33x_watchdog_pid=$!
printf '%s\\n' "$a33x_watchdog_pid" > /run/a33x-watchdog.pid
log_a33x_watchdog "feeder pid=$a33x_watchdog_pid device=$watchdog_device"
'''


def patch_watchdog_hook(text: str) -> str:
    if text.count(base.ORIGINAL_FEEDER_BLOCK) != 1:
        base.refuse("U0l watchdog feeder block does not match exactly once")
    if "WATCHDOG_SHUTDOWN_REQUEST" in text or base.MARKER_PREFIX in text:
        base.refuse("watchdog handoff logic already exists in base hook")
    patched = text.replace(base.ORIGINAL_FEEDER_BLOCK, REPLACEMENT_FEEDER_BLOCK)
    required_counts = (
        ("watchdog_log_count()", 1),
        (base.STOP_LOG, 2),
        (base.DID_NOT_STOP_LOG, 2),
        ("config_nowayout=disabled", 1),
        ("printf 'V' >&3", 1),
        ("exec 3>&-", 1),
        ('printf \'%s\\n\' "stopped" > "$WATCHDOG_SHUTDOWN_STATUS"', 1),
        ("driver stop log verified", 1),
        ("failed-unverified-stop", 1),
    )
    forbidden = (
        "/sys/class/watchdog/watchdog0/state",
        "/sys/class/watchdog/watchdog0/nowayout",
        "/sys/module/s3c2410_wdt/parameters/nowayout",
        "read_watchdog_nowayout",
    )
    for token, expected in required_counts:
        actual = patched.count(token)
        if actual != expected:
            base.refuse(
                "patched watchdog hook contract is missing or duplicated: "
                f"token={token!r} actual={actual} expected={expected}"
            )
    for token in forbidden:
        if token in patched:
            base.refuse(f"runtime-only watchdog prerequisite remains: {token}")
    return patched


def rewrite_key_values(
    path: Path,
    *,
    remove: set[str],
    replace: dict[str, str],
    append: dict[str, str],
) -> None:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key in remove or key in append:
            continue
        if key in seen:
            base.refuse(f"duplicate key in generated report {path}: {key}")
        seen.add(key)
        rows.append((key, replace.get(key, value)))
    rows.extend(append.items())
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in rows), encoding="utf-8"
    )


def parsed_paths() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args, _ = parser.parse_known_args()
    return args.root.expanduser().resolve(), args.repo.expanduser().resolve()


def finalize_reports(
    root: Path,
    contract: inspector.WatchdogKernelContract,
    contract_report: Path,
) -> None:
    patch = root / "build/u0m-watchdog-magic-close-patch.txt"
    manifest = (
        root
        / "build/candidates/a33x-h1-usbpd-u0m-watchdog-magic-close-manifest.txt"
    )
    if not patch.is_file() or not manifest.is_file():
        base.refuse("U0m base builder did not produce patch report and manifest")

    remove = {
        "watchdog_nowayout_required",
        "watchdog_state_before_required",
        "watchdog_state_after_required",
    }
    common_append = {
        "watchdog_config_source": "/proc/config.gz",
        "watchdog_config_gz": str(contract.config_path),
        "watchdog_config_gz_sha256": contract.config_sha256,
        "watchdog_config_contract_report": str(contract_report),
        "watchdog_config_contract_report_sha256": base.v2.sha_file(contract_report),
        "watchdog_config_nowayout": contract.values["CONFIG_WATCHDOG_NOWAYOUT"],
        "watchdog_config_handle_boot_enabled": contract.values[
            "CONFIG_WATCHDOG_HANDLE_BOOT_ENABLED"
        ],
        "watchdog_config_open_timeout": contract.values[
            "CONFIG_WATCHDOG_OPEN_TIMEOUT"
        ],
        "watchdog_config_s3c2410_watchdog": contract.values[
            "CONFIG_S3C2410_WATCHDOG"
        ],
        "watchdog_runtime_parameter_required": "no",
        "watchdog_class_state_required": "no",
        "watchdog_stop_verification": (
            "driver-stop-log-increment-and-no-did-not-stop-increment"
        ),
        "watchdog_stop_log": base.STOP_LOG,
        "watchdog_did_not_stop_log": base.DID_NOT_STOP_LOG,
    }
    rewrite_key_values(
        patch,
        remove=remove,
        replace={
            "operation": "python-u0m-v3-host-config-pinned-watchdog-magic-close",
            "shell_delta": "driver-log-verified-watchdog-magic-close-before-switch-root",
        },
        append=common_append,
    )
    patch_sha = base.v2.sha_file(patch)
    rewrite_key_values(
        manifest,
        remove=remove,
        replace={
            "functional_delta": (
                "host-pinned-nowayout-disabled-and-driver-log-verified-"
                "magic-close-before-switch-root"
            ),
            "shell_delta": "driver-log-verified-watchdog-magic-close-before-switch-root",
            "patch_report_sha256": patch_sha,
        },
        append=common_append,
    )


def main() -> int:
    root, repo = parsed_paths()
    if base.u0l.u0j.git_blob(repo, BASE) != EXPECTED_BASE_BLOB:
        base.refuse("checked-in U0m base builder changed unexpectedly")
    if base.u0l.u0j.git_blob(repo, INSPECTOR) != EXPECTED_INSPECTOR_BLOB:
        base.refuse("watchdog kernel contract inspector changed unexpectedly")

    contract_path = root / "build/a33-twrp-runtime-config.gz"
    contract_report = root / "build/a33-watchdog-kernel-contract.txt"
    contract = inspector.inspect_config(contract_path)
    inspector.write_report(contract, contract_report)

    base.REPLACEMENT_FEEDER_BLOCK = REPLACEMENT_FEEDER_BLOCK
    base.patch_watchdog_hook = patch_watchdog_hook
    result = base.main()
    finalize_reports(root, contract, contract_report)
    print("u0m_v3_reports_finalized=yes")
    print(f"watchdog_config_contract_report={contract_report}")
    print("phone_partition_writes=no")
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        base.Refusal,
        base.u0l.Refusal,
        base.u0l.u0k.Refusal,
        base.u0l.u0k.u0j.Refusal,
        base.v2.Refusal,
        base.v2.CpioError,
        inspector.ContractError,
        OSError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        print(f"REFUSING U0m v3: {exc}", file=sys.stderr)
        raise SystemExit(1)
