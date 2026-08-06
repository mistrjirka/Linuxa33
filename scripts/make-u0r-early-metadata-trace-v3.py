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
            f"U0r executable anchor mismatch: label={label} count={count}"
        )
    return text.replace(anchor, replacement, 1)


def patch_hook04_minimal(original: str) -> str:
    """Change only the active U0g output assignment and candidate label."""
    old_path = "relative_file=u0g-muic-result.txt"
    new_path = "relative_file=u0r-hook04-muic-result.txt"
    old_label = '    echo "candidate=U0g-muic-dynamic"'
    new_label = (
        '    echo "candidate=U0r-early-metadata-trace"\n'
        '    echo "experiment_role=hook04-after-dynamic-muic"'
    )
    patched = replace_once(original, old_path, new_path, "hook04-output-assignment")
    patched = replace_once(patched, old_label, new_label, "hook04-candidate-label")
    if patched.count(new_path) != 1 or old_path in patched:
        raise U0rV3Error("U0r hook04 active output assignment was not replaced")
    return patched


def patch_hook05_minimal(original: str) -> str:
    """Change only the active U0h output assignment and candidate label."""
    old_path = "metadata_relative=a33x-bringup/u0h-root-node-result.txt"
    new_path = "metadata_relative=a33x-bringup/u0r-hook05-root-node-result.txt"
    old_label = "record candidate U0h-userdata-root-node"
    new_label = (
        "record candidate U0r-early-metadata-trace\n"
        "record experiment_role hook05-root-node"
    )
    patched = replace_once(original, old_path, new_path, "hook05-output-assignment")
    patched = replace_once(patched, old_label, new_label, "hook05-candidate-label")
    if patched.count(new_path) != 1 or old_path in patched:
        raise U0rV3Error("U0r hook05 active output assignment was not replaced")
    return patched


def patch_init_second_structural(original: str) -> str:
    """Insert U0r trace calls around unique executable boot boundaries."""
    if base.v2.sha_bytes(original.encode()) != base.u0q.EXPECTED_U0P_INIT2_SHA256:
        raise U0rV3Error("exact U0p init_2nd.sh hash mismatch")

    newline = original.find("\n")
    if newline < 0 or not original.startswith("#!"):
        raise U0rV3Error("U0p init_2nd.sh lacks a complete shebang")
    exact_shebang = original[: newline + 1]
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
    patched = replace_once(
        patched,
        "mount_root_partition\n",
        'u0r_trace append mount-root-begin ""\n'
        'mount_root_partition\n'
        'u0r_trace append mount-root-success ""\n',
        "mount-root-call",
    )
    patched = replace_once(
        patched,
        "run_hooks /hooks-cleanup\n",
        'u0r_trace append cleanup-hooks-begin ""\n'
        'run_hooks /hooks-cleanup\n'
        'u0r_trace append cleanup-hooks-done ""\n',
        "cleanup-hooks-call",
    )
    patched = replace_once(
        patched,
        "printf '<6>a33x-u0k-direct-mount: stage=switch-root-begin\\n' > /dev/kmsg 2>/dev/null || true\n",
        "printf '<6>a33x-u0k-direct-mount: stage=switch-root-begin\\n' > /dev/kmsg 2>/dev/null || true\n"
        'u0r_trace append switch-root-begin ""\n',
        "switch-root-stage-marker",
    )
    patched = replace_once(
        patched,
        "U0N_SSHD_SOURCE=/run/a33x-u0n-sshd.initd\n",
        'u0r_trace append u0p-setup-prefix-reached ""\n'
        "U0N_SSHD_SOURCE=/run/a33x-u0n-sshd.initd\n",
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

    if base.u0q.u0p.embedded_sshd_bytes(patched) != inherited_sshd:
        raise U0rV3Error("U0r changed inherited OpenRC sshd instrumentation")
    return patched


def main() -> int:
    if git_blob(BASE_PATH) != EXPECTED_BASE_BLOB:
        raise U0rV3Error("base U0r builder changed")

    old_init = base.patch_init_second
    old_hook04 = base.patch_hook04
    old_hook05 = base.patch_hook05
    try:
        base.patch_init_second = patch_init_second_structural
        base.patch_hook04 = patch_hook04_minimal
        base.patch_hook05 = patch_hook05_minimal
        return base.main()
    finally:
        base.patch_init_second = old_init
        base.patch_hook04 = old_hook04
        base.patch_hook05 = old_hook05


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
