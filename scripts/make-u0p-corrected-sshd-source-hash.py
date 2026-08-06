#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
U0O_BUILDER_V2_PATH = HERE / "make-u0o-persistent-sshd-trace-v2.py"
U0O_AUDIT_V2_PATH = HERE / "audit-a33-u0o-candidate-v2.py"
EXPECTED_U0O_BUILDER_V2_BLOB = "88cd0b9b3446314c04ad0c4b20583c2e6facf449"
EXPECTED_U0O_AUDIT_V2_BLOB = "25a3ab194093b7b082477caba5c554481f37bf1a"
EXPECTED_U0O_MANIFEST_SHA256 = "486387c863f55c28dec19128eff2a46d377d86762ae543aa2f1978292845b728"
EXPECTED_U0O_PATCH_SHA256 = "f68c4dc7e605f8659553e7645db4f7e3cdfe47426bbf27906f740895671aea3a"
EXPECTED_U0O_AUDIT_SHA256 = "a78772e279b26abc639307b7094156ccfc0ed2df469b88f334502c14c9a723fc"
EXPECTED_U0O_INITRAMFS_SHA256 = "db1b76d1cb9da64272a7e42033bc72a8f9d7900e98fd3acea76c7edb1dd4d49e"
EXPECTED_U0O_CANDIDATE_SHA256 = "d98bb291f56fc8cb2f595c915d146c3b951333f04435dfb4e2839b95ddc5da0b"
EXPECTED_U0O_INIT2_SHA256 = "14930c5ab6cda0056b881cffd25c8272b0cf4f01704313384c133fac734c7e98"
STALE_U0N_INSTRUMENTED_SHA256 = "a6774be5b01375be9847ae7d548f47f3fa25b251a99144f4006ed6774d353ffc"
INIT_TARGET = "init_2nd.sh"
WATCHDOG_TARGET = "hooks/01-a33x-watchdog.sh"
TRACE_PATH = "/var/log/a33x-u0o-real-boot-sshd.log"
MODULES = 67


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


u0o_v2 = load("a33_u0p_parent_u0o_builder", U0O_BUILDER_V2_PATH)
u0o_audit_v2 = load("a33_u0p_parent_u0o_audit", U0O_AUDIT_V2_PATH)
u0o = u0o_v2.base
v2 = u0o.v2
HEREDOC = u0o.u0n.HEREDOC


class Refusal(RuntimeError):
    pass


def refuse(message: str) -> None:
    raise Refusal(message)


def git_blob(repo: Path, path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def embedded_sshd_bytes(init_text: str) -> bytes:
    opening = f"<<'{HEREDOC}'\n"
    if init_text.count(opening) != 1:
        refuse("instrumented sshd heredoc opening is absent or duplicated")
    start = init_text.index(opening) + len(opening)
    closing = f"\n{HEREDOC}\n"
    end = init_text.find(closing, start)
    if end < 0 or init_text.find(closing, end + 1) >= 0:
        refuse("instrumented sshd heredoc closing is absent or duplicated")
    # The newline immediately before the delimiter is part of the heredoc body.
    payload = init_text[start : end + 1].encode("utf-8")
    if not payload or not payload.endswith(b"\n"):
        refuse("instrumented sshd heredoc payload is empty or lacks final newline")
    return payload


def declared_instrumented_sha(init_text: str) -> str:
    matches = re.findall(r"(?m)^U0N_SSHD_INSTRUMENTED_SHA=([0-9a-f]{64})$", init_text)
    if len(matches) != 1:
        refuse(f"expected one declared instrumented sshd SHA, found {len(matches)}")
    return matches[0]


def patch_init_second(original: str) -> tuple[str, str]:
    if v2.sha_bytes(original.encode()) != EXPECTED_U0O_INIT2_SHA256:
        refuse("exact U0o init_2nd.sh hash mismatch")
    embedded = embedded_sshd_bytes(original)
    actual_embedded_sha = v2.sha_bytes(embedded)
    declared = declared_instrumented_sha(original)
    if declared != STALE_U0N_INSTRUMENTED_SHA256:
        refuse(
            "U0o no longer contains the expected stale U0n source hash: "
            f"actual={declared!r} expected={STALE_U0N_INSTRUMENTED_SHA256!r}"
        )
    if actual_embedded_sha == declared:
        refuse("U0o embedded source unexpectedly already matches its declaration")

    stale_line = f"U0N_SSHD_INSTRUMENTED_SHA={declared}"
    corrected_line = f"U0N_SSHD_INSTRUMENTED_SHA={actual_embedded_sha}"
    if original.count(stale_line) != 1:
        refuse("stale instrumented source hash line is absent or duplicated")
    old_label = "candidate=U0o-persistent-sshd-trace stage=trace-open"
    new_label = "candidate=U0p-corrected-sshd-source-hash stage=trace-open"
    if original.count(old_label) != 1:
        refuse("U0o persistent trace candidate label is absent or duplicated")

    patched = original.replace(stale_line, corrected_line, 1)
    patched = patched.replace(old_label, new_label, 1)
    if embedded_sshd_bytes(patched) != embedded:
        refuse("U0p changed the embedded instrumented sshd script bytes")
    if declared_instrumented_sha(patched) != actual_embedded_sha:
        refuse("U0p corrected source hash does not match the embedded heredoc bytes")
    if patched.count(new_label) != 1 or old_label in patched:
        refuse("U0p trace candidate label transformation failed")

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
            refuse(f"unsafe operation entered U0p: {token}")
    return patched, actual_embedded_sha


def assert_only_init_changed(before, after) -> None:
    if len(before.entries) != len(after.entries) or before.tail != after.tail:
        refuse("U0p changed CPIO entry count or trailer tail")
    changed: set[str] = set()
    for old, new in zip(before.entries, after.entries, strict=True):
        old_meta = (old.name, old.mode, old.nlink, old.ino, old.devmajor, old.devminor)
        new_meta = (new.name, new.mode, new.nlink, new.ino, new.devmajor, new.devminor)
        if old_meta != new_meta:
            refuse(f"U0p changed CPIO metadata for {old.name}")
        if v2.sha_bytes(old.data) != v2.sha_bytes(new.data):
            changed.add(old.normalized)
    if changed != {INIT_TARGET}:
        refuse(f"unexpected U0p initramfs payload delta: {sorted(changed)}")


def validate_parent(root: Path, repo: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    for path, expected in (
        (U0O_BUILDER_V2_PATH, EXPECTED_U0O_BUILDER_V2_BLOB),
        (U0O_AUDIT_V2_PATH, EXPECTED_U0O_AUDIT_V2_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            refuse(
                f"checked-in U0p dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0o-persistent-sshd-trace-manifest.txt"
    patch_path = root / "build/u0o-persistent-sshd-trace-patch.txt"
    audit_path = root / "build/a33-u0o-candidate-audit.txt"
    initramfs = root / "export-u0o-persistent-sshd-trace/initramfs"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0o-persistent-sshd-trace-recovery.img"
    for path in (manifest_path, patch_path, audit_path, initramfs, candidate):
        if not path.is_file():
            refuse(f"missing exact U0o parent evidence: {path}")
    expected_hashes = (
        (manifest_path, EXPECTED_U0O_MANIFEST_SHA256),
        (patch_path, EXPECTED_U0O_PATCH_SHA256),
        (audit_path, EXPECTED_U0O_AUDIT_SHA256),
        (initramfs, EXPECTED_U0O_INITRAMFS_SHA256),
        (candidate, EXPECTED_U0O_CANDIDATE_SHA256),
    )
    for path, expected in expected_hashes:
        actual = v2.sha_file(path)
        if actual != expected:
            refuse(
                f"exact U0o parent artifact changed: path={path} "
                f"actual={actual} expected={expected}"
            )
    manifest = v2.kv(manifest_path)
    v2.require(
        manifest,
        {
            "candidate": "U0o-persistent-sshd-trace",
            "u0o_initramfs_sha256": EXPECTED_U0O_INITRAMFS_SHA256,
            "recovery_sha256": EXPECTED_U0O_CANDIDATE_SHA256,
            "persistent_trace_path": TRACE_PATH,
            "u0n_watchdog_hook_preserved": "yes",
            "build_status": "passed",
        },
        "U0o parent manifest",
    )
    audit = v2.kv(audit_path)
    v2.require(
        audit,
        {
            "candidate_sha256": EXPECTED_U0O_CANDIDATE_SHA256,
            "u0n_watchdog_hook_byte_identical": "yes",
            "persistent_trace_scope_verified": "yes",
            "audit_status": "passed",
        },
        "U0o parent audit",
    )
    return manifest_path, initramfs, candidate, manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build U0p from exact U0o by correcting the stale declared SHA of "
            "the persistent-logging instrumented sshd heredoc"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    parent_manifest_path, parent_initramfs, _, _ = validate_parent(root, repo)
    try:
        before = v2.Archive.parse(gzip.decompress(parent_initramfs.read_bytes()))
    except (OSError, v2.CpioError) as exc:
        refuse(f"cannot parse exact U0o initramfs: {exc}")
    original_init = before.one(INIT_TARGET).data.decode("utf-8", errors="strict")
    patched_init, embedded_sha = patch_init_second(original_init)
    payload = before.replace(INIT_TARGET, patched_init.encode())
    after = v2.Archive.parse(payload)
    assert_only_init_changed(before, after)
    if before.one(WATCHDOG_TARGET).data != after.one(WATCHDOG_TARGET).data:
        refuse("U0p changed the proven U0m/U0n/U0o watchdog hook")
    if v2.count_modules(before) != MODULES or v2.count_modules(after) != MODULES:
        refuse("U0p module count changed or is not 67")

    output_initramfs = root / "export-u0p-corrected-sshd-source-hash/initramfs"
    inspect_dir = root / "build/u0p-corrected-sshd-source-hash-inspection"
    patch_report = root / "build/u0p-corrected-sshd-source-hash-patch.txt"
    recovery_output = root / "build/pmos-debug-recovery-u0p-corrected-sshd-source-hash"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0p-corrected-sshd-source-hash-recovery.img"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0p-corrected-sshd-source-hash-manifest.txt"
    for path in (output_initramfs, patch_report, candidate, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    inspect_dir.mkdir(parents=True, exist_ok=True)
    (inspect_dir / "u0o-init_2nd.sh").write_text(original_init, encoding="utf-8")
    syntax_path = inspect_dir / "u0p-init_2nd.sh"
    syntax_path.write_text(patched_init, encoding="utf-8")
    (inspect_dir / "embedded-instrumented-sshd.initd").write_bytes(
        embedded_sshd_bytes(patched_init)
    )
    subprocess.run(["sh", "-n", str(syntax_path)], check=True)
    subprocess.run(
        ["sh", "-n", str(inspect_dir / "embedded-instrumented-sshd.initd")],
        check=True,
    )

    output_initramfs.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))
    roundtrip = v2.Archive.parse(gzip.decompress(output_initramfs.read_bytes()))
    if roundtrip.one(INIT_TARGET).data != patched_init.encode() or roundtrip.tail != before.tail:
        refuse("written U0p initramfs did not round-trip")

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
        ("functional_base", "U0o-persistent-sshd-trace"),
        ("u0o_manifest", parent_manifest_path),
        ("u0o_manifest_sha256", v2.sha_file(parent_manifest_path)),
        ("u0o_initramfs", parent_initramfs),
        ("u0o_initramfs_sha256", v2.sha_file(parent_initramfs)),
        ("u0p_initramfs", output_initramfs),
        ("u0p_initramfs_sha256", v2.sha_file(output_initramfs)),
        ("cpio_entry_count", len(before.entries)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_payload_delta", INIT_TARGET),
        ("shell_delta", "correct-stale-instrumented-sshd-source-sha-and-candidate-label"),
        ("runtime_failure_fixed", "instrumented-source-hash-mismatch"),
        ("stale_declared_instrumented_sshd_sha256", STALE_U0N_INSTRUMENTED_SHA256),
        ("corrected_instrumented_sshd_sha256", embedded_sha),
        ("embedded_instrumented_sshd_bytes_preserved", "yes"),
        ("sshd_behavior_delta_from_u0o", "none"),
        ("persistent_trace_path", TRACE_PATH),
        ("persistent_trace_write_scope", "unchanged-from-u0o"),
        ("rootfs_persistent_delta", TRACE_PATH),
        ("original_init_2nd_sha256", v2.sha_bytes(before.one(INIT_TARGET).data)),
        ("patched_init_2nd_sha256", v2.sha_bytes(patched_init.encode())),
        ("u0o_watchdog_hook_preserved", "yes"),
        ("embedded_modules", MODULES),
        ("kernel_cmdline_delta", "none"),
        ("module_delta", "none"),
        ("kernel_delta", "none"),
        ("dtb_delta", "none"),
        ("recovery_dtbo_delta", "none"),
        ("phone_partition_writes", "no"),
    ]
    v2.write_report(
        patch_report,
        [("operation", "python-u0p-correct-stale-instrumented-sshd-source-sha")]
        + common
        + [("patch_status", "passed")],
    )

    recovery = v2.build_recovery(root, repo, output_initramfs, recovery_output)
    shutil.copy2(recovery, candidate)
    if candidate.stat().st_size != 100663296:
        refuse(f"unexpected U0p recovery size: {candidate.stat().st_size}")
    v2.write_report(
        manifest,
        [
            ("candidate", "U0p-corrected-sshd-source-hash"),
            ("functional_delta", "correct-runtime-hash-for-persistent-logging-sshd-heredoc"),
            *common,
            ("patch_report", patch_report),
            ("patch_report_sha256", v2.sha_file(patch_report)),
            ("recovery", candidate),
            ("recovery_size", candidate.stat().st_size),
            ("recovery_sha256", v2.sha_file(candidate)),
            ("preparation_status", "passed"),
            ("build_status", "passed"),
        ],
    )
    print(f"candidate={candidate}")
    print(f"candidate_sha256={v2.sha_file(candidate)}")
    print(f"manifest={manifest}")
    print(f"stale_declared_instrumented_sshd_sha256={STALE_U0N_INSTRUMENTED_SHA256}")
    print(f"corrected_instrumented_sshd_sha256={embedded_sha}")
    print("embedded_instrumented_sshd_bytes_preserved=yes")
    print(f"rootfs_persistent_delta={TRACE_PATH}")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        Refusal,
        u0o.Refusal,
        u0o.u0n.Refusal,
        u0o.u0n.u0m_core.Refusal,
        v2.Refusal,
        v2.CpioError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"REFUSING U0p: {exc}", file=sys.stderr)
        raise SystemExit(1)
