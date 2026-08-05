#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "make-u0n-real-boot-sshd-trace.py"
EXPECTED_BASE_BLOB = "9b72b0ee3252f90d33f2cb6000210edfd35dd9cd"

spec = importlib.util.spec_from_file_location("a33_u0n_v2_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0n base builder: {BASE_PATH}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


def instrument_sshd_init(original: str) -> str:
    if base.v2.sha_bytes(original.encode()) != base.EXPECTED_SSHD_INIT_SHA256:
        base.refuse("installed sshd init script differs from the exact restored rootfs")
    if base.MARKER_PREFIX in original:
        base.refuse("U0n instrumentation already exists in installed sshd init script")
    if base.HEREDOC in original or base.SPLASH_HEREDOC in original:
        base.refuse("installed sshd init script collides with U0n heredoc delimiters")

    patched = original
    for required in ("update_command", "checkconfig", "start_pre"):
        patched = base._rename_required_function(patched, required)

    optional: dict[str, bool] = {}
    for name in ("start_post", "stop_pre", "stop_post"):
        patched, optional[name] = base._rename_optional_function(patched, name)

    if re.search(r"(?m)^start\(\)[ \t]*\{", patched):
        base.refuse("installed sshd init unexpectedly defines start(); refusing to alter start semantics")
    if re.search(r"(?m)^stop\(\)[ \t]*\{", patched):
        base.refuse("installed sshd init unexpectedly defines stop(); refusing to alter stop semantics")

    patched = patched.rstrip() + base.TRACE_FUNCTIONS
    patched += base._optional_wrapper(
        "start_post", optional["start_post"], snapshot_before=False
    )
    patched += base._optional_wrapper(
        "stop_pre", optional["stop_pre"], snapshot_before=True
    )
    patched += base._optional_wrapper(
        "stop_post", optional["stop_post"], snapshot_before=False
    )
    patched += (
        '\nu0n_kmsg 6 "event=script-loaded shell_pid=$$ ppid=$PPID '
        'service=${RC_SVCNAME:-unset} action=${RC_CMD:-unset} selected=${command:-unset}"\n'
    )

    required_counts = (
        ("u0n_original_update_command()", 1),
        ("u0n_original_checkconfig()", 1),
        ("u0n_original_start_pre()", 1),
        ("event=monitor-started", 1),
        ("event=monitor-complete", 1),
        ("schedule=0,1,2,5,10,20,30,60", 2),
        ("event=start-pre-enter", 1),
        ("event=checkconfig-exit", 1),
        ("event=update-command", 1),
        ("event=start_post-enter", 1),
        ("event=stop_pre-enter", 1),
        ("event=stop_post-enter", 1),
    )
    if "default_start" in patched[len(original):]:
        base.refuse("U0n instrumentation must not call default_start directly")
    if "default_stop" in patched[len(original):]:
        base.refuse("U0n instrumentation must not call default_stop directly")
    for token, expected in required_counts:
        actual = patched.count(token)
        if actual != expected:
            base.refuse(
                "instrumented sshd contract token count mismatch: "
                f"token={token!r} actual={actual} expected={expected}"
            )
    return patched + ("\n" if not patched.endswith("\n") else "")


base.instrument_sshd_init = instrument_sshd_init


def main() -> int:
    repo = Path.home() / "Linuxa33"
    actual = subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(BASE_PATH)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    if actual != EXPECTED_BASE_BLOB:
        base.refuse(
            "checked-in U0n base builder changed: "
            f"actual={actual!r} expected={EXPECTED_BASE_BLOB!r}"
        )
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        base.Refusal,
        base.u0m_core.Refusal,
        base.u0m_core.u0l.Refusal,
        base.v2.Refusal,
        base.v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        print(f"REFUSING U0n v2: {exc}", file=sys.stderr)
        raise SystemExit(1)
