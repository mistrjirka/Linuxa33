from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


class BusyBoxResolutionError(RuntimeError):
    pass


RUNTIME_DIR = "/tmp/a33-u0j-runtime"
RUNTIME_REMOTE_NAMES = {
    "busybox": f"{RUNTIME_DIR}/busybox",
    "busybox-extras": f"{RUNTIME_DIR}/busybox-extras",
    "find-root": f"{RUNTIME_DIR}/find_root_partition.sh",
    "runtime-test": f"{RUNTIME_DIR}/runtime-test.sh",
}


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_verified_busyboxes(
    *,
    archive: Any,
    root: Path,
    home: Path,
    report_values: dict[str, str],
    output_dir: Path,
) -> tuple[dict[str, Path], list[str]]:
    """Resolve the exact U0j BusyBox binaries without assuming CPIO path shape.

    Prefer direct non-empty CPIO payloads. If the newc representation uses a
    hard-link/path form the lightweight parser cannot reconstruct, fall back to
    the pmbootstrap rootfs copies only after matching the SHA256 values recorded
    by the U0h finalization report. That report proved the rootfs copies and
    embedded initramfs copies were byte-identical, and U0i/U0j changed only
    init_functions.sh.
    """

    specs = (
        ("busybox", "busybox_binary_sha256"),
        ("busybox-extras", "busybox_extras_binary_sha256"),
    )
    rootfs = home / ".local/var/pmbootstrap/chroot_rootfs_samsung-a33x"
    binaries: dict[str, Path] = {}
    evidence: list[str] = []

    for basename, report_key in specs:
        expected = report_values.get(report_key, "")
        if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
            raise BusyBoxResolutionError(f"invalid {report_key} in U0h report")

        candidates = [
            entry
            for entry in archive.entries
            if entry.normalized.rsplit("/", 1)[-1] == basename and entry.data
        ]
        selected_data: bytes | None = None
        selected_source = ""
        for entry in candidates:
            actual = hashlib.sha256(entry.data).hexdigest()
            evidence.append(
                f"busybox_candidate={basename} source=cpio:{entry.normalized} "
                f"size={len(entry.data)} sha256={actual}"
            )
            if actual == expected:
                selected_data = entry.data
                selected_source = f"cpio:{entry.normalized}"
                break

        destination = output_dir / basename
        if selected_data is not None:
            destination.write_bytes(selected_data)
        else:
            source = rootfs / "bin" / basename
            if not source.is_file():
                raise BusyBoxResolutionError(
                    f"no matching CPIO payload and missing verified fallback: {source}"
                )
            actual = _sha_file(source)
            evidence.append(
                f"busybox_candidate={basename} source=rootfs:{source} "
                f"size={source.stat().st_size} sha256={actual}"
            )
            if actual != expected:
                raise BusyBoxResolutionError(
                    f"rootfs fallback SHA256 mismatch for {basename}: "
                    f"expected={expected} actual={actual}"
                )
            destination.write_bytes(source.read_bytes())
            selected_source = f"rootfs-verified-by-u0h:{source}"

        destination.chmod(0o755)
        if _sha_file(destination) != expected:
            raise BusyBoxResolutionError(f"resolved {basename} differs from U0h hash")
        binaries[basename] = destination
        evidence.append(
            f"busybox_selected={basename} source={selected_source} "
            f"sha256={expected}"
        )

    return binaries, evidence


def build_runtime_upload_plan(
    *,
    binaries: dict[str, Path],
    find_root_script: Path,
    runtime_test_script: Path,
) -> tuple[tuple[Path, str], ...]:
    """Return an exact, fail-closed local-to-remote upload plan.

    The runtime test requires both resolved BusyBox binaries plus the exact U0j
    function and its test script. Reject extra/missing keys so a resolver naming
    mismatch cannot silently skip an upload again.
    """

    if set(binaries) != {"busybox", "busybox-extras"}:
        raise BusyBoxResolutionError(
            "runtime BusyBox keys differ from the required set: "
            f"actual={sorted(binaries)}"
        )
    local_paths = {
        "busybox": binaries["busybox"],
        "busybox-extras": binaries["busybox-extras"],
        "find-root": find_root_script,
        "runtime-test": runtime_test_script,
    }
    plan: list[tuple[Path, str]] = []
    for key in ("busybox", "busybox-extras", "find-root", "runtime-test"):
        local = local_paths[key]
        if not local.is_file() or local.stat().st_size <= 0:
            raise BusyBoxResolutionError(f"runtime upload input is missing or empty: {local}")
        plan.append((local, RUNTIME_REMOTE_NAMES[key]))
    return tuple(plan)
