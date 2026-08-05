#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "make-u0o-persistent-sshd-trace.py"
EXPECTED_BASE_BLOB = "56bee8bbf637fea7d0a077e1be2aed460dc85b7e"

spec = importlib.util.spec_from_file_location("a33_u0o_v2_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0o base builder: {BASE_PATH}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


def patch_init_second(original: str) -> str:
    if base.v2.sha_bytes(original.encode()) != base.EXPECTED_U0N_INIT2_SHA256:
        base.refuse("exact U0n init_2nd.sh hash mismatch")
    if base.MARKER_PREFIX in original or base.TRACE_PATH in original:
        base.refuse("U0o persistent trace is already present")
    if original.count(base.ORIGINAL_KMSG) != 1:
        base.refuse("exact U0n kmsg helper is absent or duplicated")
    if original.count(base.SETUP_PREFIX) != 1:
        base.refuse("exact U0n setup prefix is absent or duplicated")
    if original.count(base.ORIGINAL_REFUSE_LINE) != 1:
        base.refuse("exact U0n refusal logging line is absent or duplicated")

    patched = original.replace(base.ORIGINAL_KMSG, base.PERSISTENT_KMSG)
    patched = patched.replace(base.SETUP_PREFIX, base.PERSISTENT_SETUP_PREFIX)
    patched = patched.replace(base.ORIGINAL_REFUSE_LINE, base.PERSISTENT_REFUSE_LINE)
    for anchor, addition in base.ADDITIONAL_TRACE_INSERTIONS:
        if patched.count(anchor) != 1:
            base.refuse(f"U0n persistent trace insertion anchor changed: {anchor[:80]!r}")
        patched = patched.replace(anchor, anchor + addition)

    required_counts = (
        (base.TRACE_PATH, 3),
        ("candidate=U0o-persistent-sshd-trace", 1),
        ("source=initramfs", 1),
        ("source=openrc", 1),
        ("stage=trace-open", 1),
        ('u0o_pre_trace 3 "error=$1"', 1),
        ("event=monitor-complete*", 1),
        ("event=monitor-complete schedule=0,1,2,5,10,20,30,60", 1),
        ("schedule=0,1,2,5,10,20,30,60", 2),
    )
    for token, expected in required_counts:
        actual = patched.count(token)
        if actual != expected:
            base.refuse(
                "U0o persistent trace token count mismatch: "
                f"token={token!r} actual={actual} expected={expected}"
            )

    allowed_sysroot_write = ': > "$U0O_TRACE"'
    forbidden = (
        'rm -rf "/sysroot"',
        "mount -o remount,rw /sysroot",
        "sed -i /sysroot",
        "> /sysroot/etc/",
        "dd if=",
        "mkfs",
        "wipefs",
    )
    for token in forbidden:
        if token in patched:
            base.refuse(f"unsafe persistent operation entered U0o: {token}")
    if patched.count(allowed_sysroot_write) != 1:
        base.refuse("U0o trace-file truncation is missing or duplicated")
    return patched


base.patch_init_second = patch_init_second


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
            f"checked-in U0o base changed: actual={actual!r} expected={EXPECTED_BASE_BLOB!r}"
        )
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        base.Refusal,
        base.u0n.Refusal,
        base.u0n.u0m_core.Refusal,
        base.v2.Refusal,
        base.v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"REFUSING U0o v2: {exc}", file=sys.stderr)
        raise SystemExit(1)
