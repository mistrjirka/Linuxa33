#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "make-u0r-early-metadata-trace.py"
EXPECTED_BASE_BLOB = "da593c22deae41e184e656dfb26ac61ccfbafe8c"


def load_base():
    spec = importlib.util.spec_from_file_location("a33_u0r_v3_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load U0r base builder: {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = load_base()


class U0rV3Error(RuntimeError):
    pass


def git_blob(path: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(HERE.parent), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.strip()


def replace_once(text: str, anchor: str, replacement: str, label: str) -> str:
    count = text.count(anchor)
    if count != 1:
        raise U0rV3Error(
            f"U0r structural anchor count mismatch: label={label} count={count}"
        )
    return text.replace(anchor, replacement, 1)


def patch_init_second_structural(original: str) -> str:
    """Insert U0r trace calls around unique executable boundaries.

    U0p inherits the U0l cgroup-isolation block between the U0k cleanup markers,
    so matching the old contiguous U0k cleanup block is invalid. This function
    preserves the exact pinned U0p shebang and patches each unique call/marker
    independently.
    """
    if base.v2.sha_bytes(original.encode()) != base.u0q.EXPECTED_U0P_INIT2_SHA256:
        raise U0rV3Error("exact U0p init_2nd.sh hash mismatch")
    if base.MARKER_PREFIX in original or base.TRACE_RELATIVE in original:
        raise U0rV3Error("U0r metadata trace is already present")

    newline = original.find("\n")
    if newline < 0:
        raise U0rV3Error("U0p init_2nd.sh has no complete first line")
    exact_shebang = original[: newline + 1]
    if not exact_shebang.startswith("#!"):
        raise U0rV3Error(f"U0p init_2nd.sh lacks a shebang: {exact_shebang!r}")

    inherited_sshd = base.u0q.u0p.embedded_sshd_bytes(original)
    patched = exact_shebang + base.TRACE_HELPER + "\n" + original[newline + 1 :]

    patched = replace_once(
        patched,
        "\nwait_root_partition\n",
        "\nu0r_trace append wait-root-begin \"\"\n"
        "wait_root_partition\n"
        "u0r_trace append wait-root-done \"\"\n",
        "wait-root-call",
    )

    mount_call = "mount_root_partition\n"
    patched = replace_once(
        patched,
        mount_call,
        'u0r_trace append mount-root-begin ""\n'
        + mount_call
        + 'u0r_trace append mount-root-success ""\n',
        "mount-root-call",
    )

    cleanup_call = "run_hooks /hooks-cleanup\n"
    patched = replace_once(
        patched,
        cleanup_call,
        'u0r_trace append cleanup-hooks-begin ""\n'
        + cleanup_call
        + 'u0r_trace append cleanup-hooks-done ""\n',
        "cleanup-hooks-call",
    )

    switch_marker = (
        "printf '<6>a33x-u0k-direct-mount: stage=switch-root-begin\\n' "
        "> /dev/kmsg 2>/dev/null || true\n"
    )
    patched = replace_once(
        patched,
        switch_marker,
        switch_marker + 'u0r_trace append switch-root-begin ""\n',
        "switch-root-stage-marker",
    )

    setup_anchor = "U0N_SSHD_SOURCE=/run/a33x-u0n-sshd.initd\n"
    patched = replace_once(
        patched,
        setup_anchor,
        'u0r_trace append u0p-setup-prefix-reached ""\n' + setup_anchor,
        "u0p-setup-prefix",
    )

    setup_success = (
        'u0o_pre_trace 6 "stage=setup-success original=$u0n_original_sha '
        'instrumented=$u0n_target_sha"\n'
    )
    patched = replace_once(
        patched,
        setup_success,
        setup_success + 'u0r_trace append u0p-setup-success ""\n',
        "u0p-setup-success",
    )

    switch_ready = 'u0o_pre_trace 6 "stage=switch-root-ready"\n'
    patched = replace_once(
        patched,
        switch_ready,
        switch_ready + 'u0r_trace append u0p-switch-root-ready ""\n',
        "u0p-switch-root-ready",
    )

    switch_exec = 'exec switch_root /sysroot "$init"'
    patched = replace_once(
        patched,
        switch_exec,
        'u0r_trace append exec-switch-root "init=$init"\n' + switch_exec,
        "switch-root-exec",
    )

    if not patched.startswith(exact_shebang):
        raise U0rV3Error("exact U0p shebang was not preserved")
    if base.u0q.u0p.embedded_sshd_bytes(patched) != inherited_sshd:
        raise U0rV3Error("U0r changed inherited OpenRC sshd instrumentation")

    required = (
        "u0r_trace reset init2-entry",
        "wait-root-begin",
        "wait-root-done",
        "mount-root-begin",
        "mount-root-success",
        "cleanup-hooks-begin",
        "cleanup-hooks-done",
        "switch-root-begin",
        "u0p-setup-prefix-reached",
        "u0p-setup-success",
        "u0p-switch-root-ready",
        "exec-switch-root",
    )
    for token in required:
        if token not in patched:
            raise U0rV3Error(f"U0r generated stage token missing: {token}")
    return patched


def main() -> int:
    actual = git_blob(BASE_PATH)
    if actual != EXPECTED_BASE_BLOB:
        raise U0rV3Error(
            f"base U0r builder changed: actual={actual} expected={EXPECTED_BASE_BLOB}"
        )
    original = base.patch_init_second
    try:
        base.patch_init_second = patch_init_second_structural
        return base.main()
    finally:
        base.patch_init_second = original


CANDIDATE = base.CANDIDATE
INIT_TARGET = base.INIT_TARGET
HOOK04_TARGET = base.HOOK04_TARGET
HOOK05_TARGET = base.HOOK05_TARGET
WATCHDOG_TARGET = base.WATCHDOG_TARGET
TRACE_RELATIVE = base.TRACE_RELATIVE
HOOK04_RELATIVE = base.HOOK04_RELATIVE
HOOK05_RELATIVE = base.HOOK05_RELATIVE
MARKER_PREFIX = base.MARKER_PREFIX
Refusal = base.Refusal


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0rV3Error,
        base.Refusal,
        base.u0q.Refusal,
        base.v2.Refusal,
        base.v2.CpioError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"U0r V3 BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
