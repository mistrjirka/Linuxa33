#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
U0K_BUILDER = HERE / "make-u0k-direct-mount-isolation.py"
U0K_FLASH = HERE / "flash-a33-u0k-direct-mount-isolation.py"
OPENRC_INSPECTOR = HERE / "inspect-a33-openrc-cgroups.py"
EXPECTED_U0K_BUILDER_BLOB = "98a144efb6213d277be596b4a5c6c4cbbfec1e57"
EXPECTED_U0K_FLASH_BLOB = "404308fa0e439ea00224ef6f58647fc3cca63778"
EXPECTED_OPENRC_INSPECTOR_BLOB = "88ad686c732398c6ca8474ce7802fdd772a0f05b"
EXPECTED_ROOTFS_SHA256 = "79c94efe41da14e72e82cfc66c8e6fac6f04482fa2ea2af024f6b1ebb67d3951"
EXPECTED_OPENRC_VERSION = "0.63.2-r0"
ROOTFS_RELATIVE = Path(
    "build/userdata-rootfs-images/20260803-193947/a33x-userdata-pmos-root.img"
)
OPENRC_CGROUP_PATH = PurePosixPath("/usr/libexec/rc/sh/rc-cgroup.sh")
APK_INSTALLED_PATH = PurePosixPath("/lib/apk/db/installed")
TARGET = "init_2nd.sh"
MODULES = 67
MARKER_PREFIX = "a33x-u0l-openrc-cgroup-isolation"
MARKERS = ("mask-begin", "mask-success")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0k = load("a33_u0l_u0k_builder", U0K_BUILDER)
u0k_flash = load("a33_u0l_u0k_flash", U0K_FLASH)
openrc_inspector = load("a33_u0l_openrc_inspector", OPENRC_INSPECTOR)
v2 = u0k.v2

ANCHOR = (
    "printf '<6>a33x-u0k-direct-mount: stage=cleanup-hooks-done\\n' "
    "> /dev/kmsg 2>/dev/null || true\n"
)
MASK_BLOCK = r'''OPENRC_CGROUP_SH=/sysroot/usr/libexec/rc/sh/rc-cgroup.sh
printf '<6>a33x-u0l-openrc-cgroup-isolation: stage=mask-begin\n' > /dev/kmsg 2>/dev/null || true
if [ ! -f "$OPENRC_CGROUP_SH" ]; then
	printf '<3>a33x-u0l-openrc-cgroup-isolation: error=missing-target\n' > /dev/kmsg 2>/dev/null || true
	echo "U0l refusal: missing $OPENRC_CGROUP_SH"
	while true; do sleep 3600; done
fi
if ! mount -o bind /dev/null "$OPENRC_CGROUP_SH"; then
	printf '<3>a33x-u0l-openrc-cgroup-isolation: error=bind-mask-failed\n' > /dev/kmsg 2>/dev/null || true
	echo "U0l refusal: cannot bind-mask $OPENRC_CGROUP_SH"
	while true; do sleep 3600; done
fi
if ! grep -q " $OPENRC_CGROUP_SH " /proc/self/mountinfo; then
	printf '<3>a33x-u0l-openrc-cgroup-isolation: error=bind-mask-unverified\n' > /dev/kmsg 2>/dev/null || true
	echo "U0l refusal: bind-mask is absent from mountinfo"
	while true; do sleep 3600; done
fi
printf '<6>a33x-u0l-openrc-cgroup-isolation: stage=mask-success\n' > /dev/kmsg 2>/dev/null || true
'''

REQUIRED_OPENRC_SNIPPETS = (
    "cgroup_add_service()",
    'rc_cgroup_path="${cgroup_path}/openrc.${RC_SVCNAME}"',
    '[ ! -d "${rc_cgroup_path}" ] && mkdir "${rc_cgroup_path}"',
    '[ -f "${rc_cgroup_path}"/cgroup.procs ] &&',
    'printf 0 > "${rc_cgroup_path}"/cgroup.procs',
)


class Refusal(RuntimeError):
    pass


def refuse(message: str) -> None:
    raise Refusal(message)


def patch_init_second(text: str) -> str:
    if text.count(ANCHOR) != 1:
        refuse("U0k post-cleanup insertion anchor does not occur exactly once")
    if MARKER_PREFIX in text:
        refuse("U0l marker already exists in base init_2nd.sh")
    patched = text.replace(ANCHOR, ANCHOR + MASK_BLOCK)
    if patched.count('mount -o bind /dev/null "$OPENRC_CGROUP_SH"') != 1:
        refuse("OpenRC cgroup bind-mask command is missing or duplicated")
    if patched.count("/sysroot/usr/libexec/rc/sh/rc-cgroup.sh") != 1:
        refuse("OpenRC cgroup target is missing or duplicated")
    for marker in MARKERS:
        token = f"{MARKER_PREFIX}: stage={marker}"
        if patched.count(token) != 1:
            refuse(f"U0l marker is missing or duplicated: {token}")
    order = (
        patched.index("a33x-u0k-direct-mount: stage=skip-legacy-boot-mount"),
        patched.index("a33x-u0k-direct-mount: stage=cleanup-hooks-begin"),
        patched.index("a33x-u0k-direct-mount: stage=cleanup-hooks-done"),
        patched.index(f"{MARKER_PREFIX}: stage=mask-begin"),
        patched.index('mount -o bind /dev/null "$OPENRC_CGROUP_SH"'),
        patched.index(f"{MARKER_PREFIX}: stage=mask-success"),
        patched.index("a33x-u0k-direct-mount: stage=switch-root-begin"),
        patched.index('exec switch_root /sysroot "$init"'),
    )
    if tuple(sorted(order)) != order:
        refuse("U0l bind-mask is not ordered after cleanup and before switch_root")
    forbidden = (
        'rm "$OPENRC_CGROUP_SH"',
        'cp /dev/null "$OPENRC_CGROUP_SH"',
        '> "$OPENRC_CGROUP_SH"',
        "sed -i",
    )
    for token in forbidden:
        if token in patched:
            refuse(f"persistent rootfs mutation entered U0l patch: {token}")
    return patched


def inspect_rootfs(root: Path, debugfs: Path) -> tuple[str, str]:
    image = root / ROOTFS_RELATIVE
    if not image.is_file():
        refuse(f"missing exact rootfs image: {image}")
    actual_image_sha = v2.sha_file(image)
    if actual_image_sha != EXPECTED_ROOTFS_SHA256:
        refuse(
            f"rootfs image SHA256 mismatch: actual={actual_image_sha} "
            f"expected={EXPECTED_ROOTFS_SHA256}"
        )
    reader = openrc_inspector.DebugfsReader(debugfs, image)
    reader.probe()
    metadata = reader.stat_path(OPENRC_CGROUP_PATH, allow_missing=False)
    if metadata is None or not metadata.is_regular:
        refuse(f"OpenRC cgroup implementation is not a regular file: {OPENRC_CGROUP_PATH}")
    data = reader.read_file(OPENRC_CGROUP_PATH, allow_missing=False)
    if data is None:
        refuse(f"cannot read OpenRC cgroup implementation: {OPENRC_CGROUP_PATH}")
    text = data.decode("utf-8", errors="strict")
    for snippet in REQUIRED_OPENRC_SNIPPETS:
        if text.count(snippet) != 1:
            refuse(f"OpenRC cgroup implementation contract changed: {snippet!r}")
    installed = reader.read_file(APK_INSTALLED_PATH, allow_missing=False)
    if installed is None:
        refuse("cannot read Alpine package database")
    versions = openrc_inspector.parse_apk_installed(
        installed.decode("utf-8", errors="replace"), "openrc"
    )
    if versions != [EXPECTED_OPENRC_VERSION]:
        refuse(f"unexpected OpenRC package versions: {versions}")
    return v2.sha_bytes(data), versions[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build U0l from exact U0k by bind-masking OpenRC rc-cgroup.sh "
            "at runtime after cleanup and before switch_root"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--debugfs", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    debugfs = (
        args.debugfs.expanduser().resolve()
        if args.debugfs is not None
        else Path(shutil.which("debugfs") or "")
    )
    if not debugfs.is_file():
        refuse("debugfs is unavailable; install e2fsprogs")

    for path, expected in (
        (U0K_BUILDER, EXPECTED_U0K_BUILDER_BLOB),
        (U0K_FLASH, EXPECTED_U0K_FLASH_BLOB),
        (OPENRC_INSPECTOR, EXPECTED_OPENRC_INSPECTOR_BLOB),
    ):
        if u0k.u0j.git_blob(repo, path) != expected:
            refuse(f"checked-in dependency changed unexpectedly: {path.name}")

    local = u0k_flash.validate_local(root, repo)
    u0k_manifest_path = Path(local["manifest_path"])
    u0k_manifest = v2.kv(u0k_manifest_path)
    u0k_initramfs = Path(u0k_manifest.get("u0k_initramfs", ""))
    if not u0k_initramfs.is_file():
        refuse(f"missing U0k initramfs: {u0k_initramfs}")
    if v2.sha_file(u0k_initramfs) != u0k_manifest.get("u0k_initramfs_sha256"):
        refuse("U0k initramfs differs from validated manifest")

    openrc_cgroup_sha, openrc_version = inspect_rootfs(root, debugfs)
    try:
        base = v2.Archive.parse(gzip.decompress(u0k_initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        refuse(f"cannot parse U0k initramfs: {exc}")
    original_entry = base.one(TARGET)
    original = original_entry.data.decode("utf-8", errors="strict")
    patched = patch_init_second(original)

    output_image = root / "export-u0l-openrc-cgroup-isolation/initramfs"
    inspect_dir = root / "build/u0l-openrc-cgroup-isolation-inspection"
    patch_report = root / "build/u0l-openrc-cgroup-isolation-patch.txt"
    recovery_output = root / "build/pmos-debug-recovery-u0l-openrc-cgroup-isolation"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0l-openrc-cgroup-isolation-recovery.img"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0l-openrc-cgroup-isolation-manifest.txt"

    for path in (output_image, patch_report, candidate, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    inspect_dir.mkdir(parents=True, exist_ok=True)
    (inspect_dir / "original-init_2nd.sh").write_text(original, encoding="utf-8")
    syntax_file = inspect_dir / "patched-init_2nd.sh"
    syntax_file.write_text(patched, encoding="utf-8")
    subprocess.run(["sh", "-n", str(syntax_file)], check=True)

    patched_payload = base.replace(TARGET, patched.encode())
    after = v2.Archive.parse(patched_payload)
    base.assert_only_payload_changed(after, TARGET)
    if v2.count_modules(base) != MODULES or v2.count_modules(after) != MODULES:
        refuse("module count changed or is not 67")
    output_image.write_bytes(gzip.compress(patched_payload, compresslevel=9, mtime=0))
    roundtrip = v2.Archive.parse(gzip.decompress(output_image.read_bytes()))
    if roundtrip.one(TARGET).data != patched.encode() or roundtrip.tail != base.tail:
        refuse("written U0l initramfs did not round-trip")

    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        refuse("cannot resolve repository commit")
    created = subprocess.run(
        ["date", "-Ins"], text=True, stdout=subprocess.PIPE, check=True
    ).stdout.strip()

    common: list[tuple[str, object]] = [
        ("created", created),
        ("linuxa33_commit", commit),
        ("implementation_language", "python3"),
        ("functional_base", "U0k-direct-mount-isolation"),
        ("u0k_manifest", u0k_manifest_path),
        ("u0k_manifest_sha256", v2.sha_file(u0k_manifest_path)),
        ("u0k_initramfs", u0k_initramfs),
        ("u0k_initramfs_sha256", v2.sha_file(u0k_initramfs)),
        ("u0l_initramfs", output_image),
        ("u0l_initramfs_sha256", v2.sha_file(output_image)),
        ("cpio_entry_count", len(base.entries)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_payload_delta", TARGET),
        ("shell_delta", "runtime-bind-mask-openrc-rc-cgroup-after-cleanup-before-switch-root"),
        ("rootfs_persistent_delta", "none"),
        ("runtime_mount_delta", "bind-/dev/null-over-/usr/libexec/rc/sh/rc-cgroup.sh"),
        ("openrc_cgroup_target", OPENRC_CGROUP_PATH.as_posix()),
        ("openrc_cgroup_target_sha256", openrc_cgroup_sha),
        ("openrc_package_version", openrc_version),
        ("rootfs_image", root / ROOTFS_RELATIVE),
        ("rootfs_image_sha256", EXPECTED_ROOTFS_SHA256),
        ("original_init_2nd_sha256", v2.sha_bytes(original_entry.data)),
        ("patched_init_2nd_sha256", v2.sha_bytes(patched.encode())),
        ("stage_markers", ",".join(MARKERS)),
        ("embedded_modules", MODULES),
        ("kernel_cmdline_delta", "none"),
        ("module_delta", "none"),
        ("kernel_delta", "none"),
        ("dtb_delta", "none"),
        ("recovery_dtbo_delta", "none"),
        ("userdata_write", "none"),
        ("phone_partition_writes", "no"),
    ]
    v2.write_report(
        patch_report,
        [("operation", "python-u0l-openrc-cgroup-runtime-bind-mask")]
        + common
        + [("patch_status", "passed")],
    )

    recovery = v2.build_recovery(root, repo, output_image, recovery_output)
    info = recovery_output / "final-boot-info.txt"
    if not info.is_file() or re.search(
        r"(?:^|\s)pmos_root=\S+", info.read_text(errors="replace")
    ):
        refuse("recovery command line validation failed")
    if recovery.stat().st_size != 100663296:
        refuse(f"unexpected recovery size: {recovery.stat().st_size}")
    shutil.copy2(recovery, candidate)

    v2.write_report(
        manifest,
        [
            ("candidate", "U0l-openrc-cgroup-isolation"),
            (
                "functional_delta",
                "bind-mask-openrc-service-cgroup-helper-without-persistent-rootfs-write",
            ),
            ("patch_report", patch_report),
            ("patch_report_sha256", v2.sha_file(patch_report)),
        ]
        + common
        + [
            ("preparation_status", "passed"),
            ("recovery", candidate),
            ("recovery_size", candidate.stat().st_size),
            ("recovery_sha256", v2.sha_file(candidate)),
            ("build_status", "passed"),
        ],
    )
    print(f"Candidate: {candidate}")
    print(f"Manifest: {manifest}")
    print("No phone partition was written.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        Refusal,
        u0k.Refusal,
        u0k.u0j.Refusal,
        v2.Refusal,
        v2.CpioError,
        v2.ShellContractError,
        openrc_inspector.InspectionError,
        UnicodeDecodeError,
    ) as exc:
        print(f"REFUSING U0l: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        print(f"REFUSING U0l: command failed rc={exc.returncode}: {exc.cmd}", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
