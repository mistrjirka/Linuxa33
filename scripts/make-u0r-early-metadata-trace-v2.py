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
    spec = importlib.util.spec_from_file_location("a33_u0r_v2_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load U0r base builder: {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = load_base()
ORIGINAL_PATCH_INIT_SECOND = base.patch_init_second


class U0rV2Error(RuntimeError):
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


def patch_init_second_preserve_shebang(original: str) -> str:
    """Run the reviewed U0r transformation while preserving the exact shebang.

    The exact U0p payload hash is still required. The original U0r builder
    incorrectly assumed that the first line was literally `#!/bin/sh`; the
    pinned payload uses another valid shell shebang. Normalize only for the
    reviewed transformation, then restore the exact original first line.
    """
    if base.v2.sha_bytes(original.encode()) != base.u0q.EXPECTED_U0P_INIT2_SHA256:
        raise U0rV2Error("exact U0p init_2nd.sh hash mismatch")

    newline = original.find("\n")
    if newline < 0:
        raise U0rV2Error("U0p init_2nd.sh has no complete first line")
    exact_shebang = original[: newline + 1]
    if not exact_shebang.startswith("#!"):
        raise U0rV2Error(f"U0p init_2nd.sh lacks a shebang: {exact_shebang!r}")

    normalized = "#!/bin/sh\n" + original[newline + 1 :]
    original_sha_bytes = base.v2.sha_bytes

    def pinned_sha_bytes(payload: bytes) -> str:
        if payload == normalized.encode():
            return base.u0q.EXPECTED_U0P_INIT2_SHA256
        return original_sha_bytes(payload)

    base.v2.sha_bytes = pinned_sha_bytes
    try:
        patched_normalized = ORIGINAL_PATCH_INIT_SECOND(normalized)
    finally:
        base.v2.sha_bytes = original_sha_bytes

    normalized_shebang = "#!/bin/sh\n"
    if not patched_normalized.startswith(normalized_shebang):
        raise U0rV2Error("reviewed U0r transformation did not preserve normalized shebang")
    patched = exact_shebang + patched_normalized[len(normalized_shebang) :]

    if not patched.startswith(exact_shebang):
        raise U0rV2Error("exact U0p shebang was not restored")
    if base.u0q.u0p.embedded_sshd_bytes(patched) != base.u0q.u0p.embedded_sshd_bytes(original):
        raise U0rV2Error("U0r v2 changed inherited OpenRC sshd instrumentation")
    if base.MARKER_PREFIX not in patched or base.TRACE_RELATIVE not in patched:
        raise U0rV2Error("U0r v2 trace payload is absent after shebang correction")
    return patched


def main() -> int:
    actual = git_blob(BASE_PATH)
    if actual != EXPECTED_BASE_BLOB:
        raise U0rV2Error(
            f"base U0r builder changed: actual={actual} expected={EXPECTED_BASE_BLOB}"
        )
    original = base.patch_init_second
    try:
        base.patch_init_second = patch_init_second_preserve_shebang
        return base.main()
    finally:
        base.patch_init_second = original


# Re-export the constants consumed by the guarded flash entrypoint.
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
        U0rV2Error,
        base.Refusal,
        base.u0q.Refusal,
        base.v2.Refusal,
        base.v2.CpioError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"U0r V2 BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
