#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Iterable

KNOWN_TWRP_SHA256 = "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
KNOWN_TWRP_SIZE = 100663296
EXPECTED_KERNEL_RELEASE = "5.10.66-Gabriel260BR-TWRP-ga0103aac9499"
EXPECTED_ORIGINAL_MODULES = 315
EXPECTED_U0K_RECOVERY_SHA256 = "7696262e0ee8d3c2a31e55045e59a2b36b8f7eefb0891d56a049a415a8be0b2f"
SOURCE_LOCK_REQUIRED = (
    "source_repository",
    "source_commit",
    "source_tree_sha256",
    "kernel_config_sha256",
    "toolchain_identity",
    "toolchain_sha256",
    "unpatched_kernel_sha256",
    "patched_kernel_sha256",
)


class AuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def kv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="strict").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key.strip(), value.strip())
    return values


def tree_sha256(root: Path, paths: Iterable[Path] | None = None) -> str:
    digest = hashlib.sha256()
    selected = sorted(
        paths if paths is not None else (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in selected:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data_hash = sha_file(path).encode("ascii")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(data_hash)
    return digest.hexdigest()


def git_output(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AuditError(
            f"git command failed in {repo}: {args!r}: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AuditError(f"cannot load Python module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def record(checks: list[Check], name: str, status: str, detail: str) -> None:
    if status not in {"passed", "missing", "failed", "unlocked", "not-applicable"}:
        raise ValueError(f"invalid audit status: {status}")
    checks.append(Check(name, status, detail.replace("\n", " ")))


def verify_exact_file(
    checks: list[Check],
    *,
    name: str,
    path: Path,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> bool:
    if not path.is_file():
        record(checks, name, "missing", str(path))
        return False
    size = path.stat().st_size
    actual = sha_file(path)
    if expected_size is not None and size != expected_size:
        record(
            checks,
            name,
            "failed",
            f"path={path} size={size} expected_size={expected_size} sha256={actual}",
        )
        return False
    if expected_sha256 is not None and actual != expected_sha256:
        record(
            checks,
            name,
            "failed",
            f"path={path} sha256={actual} expected_sha256={expected_sha256}",
        )
        return False
    record(checks, name, "passed", f"path={path} size={size} sha256={actual}")
    return True


def audit_u0k_artifacts(checks: list[Check], root: Path, repo: Path) -> bool:
    wrapper = repo / "scripts/flash-a33-u0k-direct-mount-isolation.py"
    if not wrapper.is_file():
        record(checks, "u0k_local_validation", "missing", str(wrapper))
        return False
    try:
        module = load_module("a33_repro_u0k_flash", wrapper)
        local = module.validate_local(root, repo)
    except Exception as exc:  # fail closed while preserving the exact reason
        record(checks, "u0k_local_validation", "failed", f"{type(exc).__name__}: {exc}")
        return False

    candidate = Path(local["candidate"])
    actual = sha_file(candidate)
    if actual != EXPECTED_U0K_RECOVERY_SHA256:
        record(
            checks,
            "u0k_local_validation",
            "failed",
            f"candidate={candidate} sha256={actual} expected={EXPECTED_U0K_RECOVERY_SHA256}",
        )
        return False
    record(
        checks,
        "u0k_local_validation",
        "passed",
        f"candidate={candidate} sha256={actual} manifest={local['manifest_path']}",
    )
    return True


def unpack_twrp_components(
    checks: list[Check], root: Path, twrp: Path
) -> dict[str, Path] | None:
    unpacker = root / "aosp-mkbootimg/unpack_bootimg.py"
    if not unpacker.is_file():
        record(checks, "twrp_component_extraction", "missing", str(unpacker))
        return None
    temporary = tempfile.TemporaryDirectory(prefix="a33-repro-twrp-")
    output = Path(temporary.name)
    completed = subprocess.run(
        [sys.executable, str(unpacker), "--boot_img", str(twrp), "--out", str(output)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        temporary.cleanup()
        record(
            checks,
            "twrp_component_extraction",
            "failed",
            f"rc={completed.returncode} stderr={completed.stderr.strip()}",
        )
        return None
    components = {
        "kernel": output / "kernel",
        "dtb": output / "dtb",
        "recovery_dtbo": output / "recovery_dtbo",
    }
    missing = [name for name, path in components.items() if not path.is_file()]
    if missing:
        temporary.cleanup()
        record(checks, "twrp_component_extraction", "failed", f"missing={','.join(missing)}")
        return None
    components["__temporary__"] = temporary  # type: ignore[assignment]
    record(checks, "twrp_component_extraction", "passed", str(output))
    return components


def audit_prebuilt_kernel(
    checks: list[Check], root: Path, repo: Path, twrp: Path
) -> bool:
    package = repo / "pmaports/device/downstream/linux-samsung-a33x"
    components = unpack_twrp_components(checks, root, twrp)
    if components is None:
        return False
    temporary = components.pop("__temporary__")  # type: ignore[assignment]
    try:
        expected_paths = {
            "kernel": package / "Image",
            "dtb": package / "samsung-a33x.dtb",
            "recovery_dtbo": package / "recovery_dtbo",
        }
        good = True
        for name, package_path in expected_paths.items():
            extracted = components[name]
            if not package_path.is_file():
                record(checks, f"prebuilt_{name}", "missing", str(package_path))
                good = False
                continue
            extracted_sha = sha_file(extracted)
            packaged_sha = sha_file(package_path)
            if extracted_sha != packaged_sha:
                record(
                    checks,
                    f"prebuilt_{name}",
                    "failed",
                    f"twrp_sha256={extracted_sha} package_sha256={packaged_sha}",
                )
                good = False
            else:
                record(
                    checks,
                    f"prebuilt_{name}",
                    "passed",
                    f"path={package_path} sha256={packaged_sha}",
                )
        return good
    finally:
        temporary.cleanup()  # type: ignore[union-attr]


def module_vermagic_matches(path: Path) -> bool:
    """Validate the actual module ABI without assuming a directory layout."""

    needle = ("vermagic=" + EXPECTED_KERNEL_RELEASE).encode("ascii")
    with path.open("rb") as stream:
        remainder = b""
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                return needle in remainder
            data = remainder + chunk
            if needle in data:
                return True
            remainder = data[-len(needle) :]


def audit_modules(
    checks: list[Check], root: Path, reconstruction_values: dict[str, str] | None = None
) -> bool:
    module_root = root / "unpacked/twrp-root/lib/modules"
    if not module_root.is_dir():
        record(checks, "original_module_tree", "missing", str(module_root))
        return False

    nested = module_root / EXPECTED_KERNEL_RELEASE
    if nested.is_dir():
        release_root = nested
        layout = "nested-release-directory"
    else:
        release_root = module_root
        layout = "flat-release-root"

    modules = sorted(release_root.rglob("*.ko"))
    manifest_hash = tree_sha256(release_root, modules) if modules else "none"
    if len(modules) != EXPECTED_ORIGINAL_MODULES:
        record(
            checks,
            "original_module_tree",
            "failed",
            f"layout={layout} module_count={len(modules)} expected={EXPECTED_ORIGINAL_MODULES} "
            f"tree_sha256={manifest_hash}",
        )
        return False

    load_file = release_root / "modules.load.recovery"
    if not load_file.is_file():
        record(
            checks,
            "original_module_tree",
            "failed",
            f"layout={layout} missing={load_file} module_count={len(modules)}",
        )
        return False

    bad_vermagic = [path for path in modules if not module_vermagic_matches(path)]
    if bad_vermagic:
        sample = ",".join(path.relative_to(release_root).as_posix() for path in bad_vermagic[:5])
        record(
            checks,
            "original_module_tree",
            "failed",
            f"layout={layout} vermagic_mismatch_count={len(bad_vermagic)} sample={sample} "
            f"expected={EXPECTED_KERNEL_RELEASE}",
        )
        return False

    if reconstruction_values:
        recorded_source = reconstruction_values.get("module_source", "")
        recorded_count = reconstruction_values.get("module_files", "")
        if recorded_source and Path(recorded_source).resolve() != module_root.resolve():
            record(
                checks,
                "original_module_tree",
                "failed",
                f"manifest_module_source={recorded_source} actual={module_root}",
            )
            return False
        if recorded_count and recorded_count != str(EXPECTED_ORIGINAL_MODULES):
            record(
                checks,
                "original_module_tree",
                "failed",
                f"manifest_module_files={recorded_count} expected={EXPECTED_ORIGINAL_MODULES}",
            )
            return False

    record(
        checks,
        "original_module_tree",
        "passed",
        f"layout={layout} module_count={len(modules)} release={EXPECTED_KERNEL_RELEASE} "
        f"vermagic_all=passed tree_sha256={manifest_hash}",
    )
    return True


def audit_git_tool(
    checks: list[Check], *, name: str, path: Path, expected_commit: str | None
) -> bool:
    if not (path / ".git").is_dir():
        record(checks, name, "missing", str(path))
        return False
    try:
        commit = git_output(path, "rev-parse", "HEAD")
        dirty = bool(git_output(path, "status", "--porcelain"))
    except AuditError as exc:
        record(checks, name, "failed", str(exc))
        return False
    if expected_commit and commit != expected_commit:
        record(
            checks,
            name,
            "failed",
            f"commit={commit} expected={expected_commit} dirty={'yes' if dirty else 'no'}",
        )
        return False
    status = "unlocked" if expected_commit is None else "passed"
    record(
        checks,
        name,
        status,
        f"commit={commit} dirty={'yes' if dirty else 'no'} expected={expected_commit or 'not-recorded'}",
    )
    return expected_commit is not None and not dirty


def validate_source_lock(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, f"missing={path}"
    values = kv(path)
    missing = [key for key in SOURCE_LOCK_REQUIRED if not values.get(key)]
    bad_hashes = [
        key
        for key in SOURCE_LOCK_REQUIRED
        if key.endswith("sha256")
        and values.get(key)
        and not re.fullmatch(r"[0-9a-f]{64}", values[key])
    ]
    if missing or bad_hashes:
        return False, f"missing_fields={','.join(missing)} invalid_hashes={','.join(bad_hashes)}"
    commit = values["source_commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return False, f"invalid_source_commit={commit}"
    return True, f"source_repository={values['source_repository']} source_commit={commit}"


def write_report(path: Path, checks: list[Check], summary: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"check={check.name} status={check.status} detail={check.detail}" for check in checks]
    lines.extend(f"{key}={value}" for key, value in summary.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit of A33 binary and kernel-source reproducibility"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument(
        "--source-lock",
        type=Path,
        default=None,
        help="kernel source provenance lock; defaults to build/a33-kernel-source.lock",
    )
    parser.add_argument("--strict-source", action="store_true")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    source_lock = (
        args.source_lock.expanduser().resolve()
        if args.source_lock is not None
        else root / "build/a33-kernel-source.lock"
    )
    report = root / "build/a33-reproducibility-audit.txt"
    checks: list[Check] = []

    try:
        repo_commit = git_output(repo, "rev-parse", "HEAD")
        repo_dirty = bool(git_output(repo, "status", "--porcelain"))
        record(
            checks,
            "linuxa33_repository",
            "passed",
            f"commit={repo_commit} dirty={'yes' if repo_dirty else 'no'}",
        )
    except AuditError as exc:
        repo_commit = "unknown"
        record(checks, "linuxa33_repository", "failed", str(exc))

    twrp = root / "reference/twrp/recovery.img"
    twrp_ok = verify_exact_file(
        checks,
        name="known_good_twrp",
        path=twrp,
        expected_size=KNOWN_TWRP_SIZE,
        expected_sha256=KNOWN_TWRP_SHA256,
    )
    u0k_ok = audit_u0k_artifacts(checks, root, repo)
    prebuilt_ok = twrp_ok and audit_prebuilt_kernel(checks, root, repo, twrp)

    reconstruction = root / "build/third-host-reconstruction/manifest.txt"
    reconstruction_values = kv(reconstruction) if reconstruction.is_file() else {}
    if reconstruction.is_file():
        reconstruction_status = reconstruction_values.get("status", "")
        record(
            checks,
            "third_host_reconstruction_manifest",
            "passed" if reconstruction_status == "complete" else "failed",
            f"path={reconstruction} status={reconstruction_status or 'missing'} "
            f"sha256={sha_file(reconstruction)}",
        )
    else:
        record(checks, "third_host_reconstruction_manifest", "missing", str(reconstruction))

    modules_ok = audit_modules(checks, root, reconstruction_values)

    mkbootimg_ok = audit_git_tool(
        checks,
        name="aosp_mkbootimg",
        path=root / "aosp-mkbootimg",
        expected_commit=reconstruction_values.get("mkbootimg_commit"),
    )
    avb_ok = audit_git_tool(
        checks,
        name="aosp_avb",
        path=root / "aosp-avb",
        expected_commit=reconstruction_values.get("avb_commit"),
    )

    key = root / "build/keys/a33x-recovery-test-rsa4096.pem"
    key_ok = verify_exact_file(checks, name="local_avb_key", path=key)
    if key_ok:
        mode = stat.S_IMODE(key.stat().st_mode)
        if mode & 0o077:
            record(
                checks,
                "local_avb_key_permissions",
                "failed",
                f"mode={mode:04o} required=0600 fix=chmod-600:{key}",
            )
            key_ok = False
        else:
            record(
                checks,
                "local_avb_key_permissions",
                "passed",
                f"mode={mode:04o} sha256={sha_file(key)}",
            )

    source_ok, source_detail = validate_source_lock(source_lock)
    record(
        checks,
        "kernel_source_provenance",
        "passed" if source_ok else "missing",
        source_detail,
    )

    exact_deployment = twrp_ok and u0k_ok
    binary_rebuild = exact_deployment and prebuilt_ok and modules_ok and mkbootimg_ok and avb_ok and key_ok
    if source_ok:
        overall = "fully-source-and-binary-locked"
    elif binary_rebuild:
        overall = "binary-rebuild-reproducible-kernel-source-missing"
    elif exact_deployment:
        overall = "exact-deployment-reproducible-binary-rebuild-incomplete-source-missing"
    else:
        overall = "not-reproducible-missing-critical-artifacts"

    summary = {
        "audit_operation": "read-only-a33-reproducibility-audit",
        "linuxa33_commit": repo_commit,
        "phone_partition_writes": "no",
        "same_phone_exact_deployment": "passed" if exact_deployment else "failed",
        "binary_recovery_rebuild": "passed" if binary_rebuild else "incomplete",
        "kernel_source_rebuild": "passed" if source_ok else "missing",
        "kernel_source_lock": str(source_lock),
        "overall_status": overall,
        "report": str(report),
    }
    write_report(report, checks, summary)

    for check in checks:
        print(f"check={check.name} status={check.status} detail={check.detail}")
    for key_name, value in summary.items():
        print(f"{key_name}={value}")

    if not exact_deployment:
        return 1
    if args.strict_source and not source_ok:
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditError, OSError, UnicodeError, ValueError) as exc:
        print(f"REPRODUCIBILITY AUDIT FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
