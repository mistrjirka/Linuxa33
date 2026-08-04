#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from a33_cpio import Archive, CpioError
from a33_shell import ShellContractError, function_span, replace_function, root_consumption_mode, second_stage_calls

FORCED_ROOT = "/dev/block/sda36"
MODULES = 67
TARGET = "init_functions.sh"


class Refusal(RuntimeError):
    pass


def refuse(message: str) -> None:
    raise Refusal(message)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def kv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result.setdefault(key, value)
    return result


def require(values: dict[str, str], expected: dict[str, str], label: str) -> None:
    bad = [(key, values.get(key), wanted) for key, wanted in expected.items() if values.get(key) != wanted]
    if bad:
        refuse(label + " contract failed:\n" + "\n".join(f"{k}: actual={a!r} expected={w!r}" for k, a, w in bad))


def one_hash(archive: Archive, name: str, expected: str) -> None:
    entry = archive.one(name)
    actual = sha_bytes(entry.data)
    if actual != expected:
        refuse(f"{name} SHA256 mismatch: expected={expected} actual={actual}")


def count_modules(archive: Archive) -> int:
    pattern = re.compile(r"\.ko(?:\.(?:gz|xz|zst))?$")
    return sum(bool(pattern.search(entry.normalized)) for entry in archive.entries)


def write_report(path: Path, pairs: list[tuple[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{key}={value}" for key, value in pairs) + "\n", encoding="utf-8")
    for key, value in pairs:
        print(f"{key}={value}")


def build_recovery(root: Path, repo: Path, initramfs: Path, output: Path) -> Path:
    builder = repo / "scripts/make-pmos-debug-recovery.sh"
    if not builder.is_file():
        refuse(f"missing proven recovery builder: {builder}")
    with tempfile.TemporaryDirectory(prefix="a33-u0i-") as temp:
        stage = Path(temp)
        (stage / "export-debug").mkdir()
        shutil.copy2(initramfs, stage / "export-debug/initramfs")
        for name in ("reference", "aosp-mkbootimg", "aosp-avb", "build"):
            source = root / name
            if not source.exists():
                refuse(f"missing recovery input: {source}")
            os.symlink(source, stage / name, target_is_directory=True)
        env = os.environ.copy()
        env.update(ROOT=str(stage), OUT=str(output), EXTRA_KERNEL_CMDLINE="")
        subprocess.run(["bash", str(builder)], check=True, env=env)
    recovery = output / "recovery.img"
    if not recovery.is_file():
        refuse("recovery builder produced no image")
    return recovery


def main() -> int:
    parser = argparse.ArgumentParser(description="Build U0i by byte-preserving Python CPIO patching")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()
    u0h_image = root / "export-u0h-root-node/initramfs"
    u0h_report_path = root / "build/u0h-userdata-root-node.txt"
    u0g_report_path = root / "build/u0g-muic-dynamic.txt"
    output_image = root / "export-u0i-python-direct-root/initramfs"
    inspect = root / "build/u0i-python-direct-root-inspection"
    patch_report = root / "build/u0i-python-direct-root-patch.txt"
    recovery_output = root / "build/pmos-debug-recovery-u0i-python-direct-root"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0i-python-direct-root-recovery.img"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0i-python-direct-root-manifest.txt"

    for path in (u0h_image, u0h_report_path, u0g_report_path):
        if not path.is_file():
            refuse(f"missing input: {path}")
    u0h, u0g = kv(u0h_report_path), kv(u0g_report_path)
    require(u0h, {
        "preparation_status": "passed",
        "busybox_runtime_applets": "passed",
        "root_handoff_runtime_tools": "passed",
        "busybox_install_before_second_stage": "yes",
        "hook_before_root_discovery": "yes",
        "hook_order_validation": "passed",
        "embedded_modules": str(MODULES),
        "phone_partition_writes": "no",
    }, "U0h")
    if sha_file(u0h_image) != u0h.get("initramfs_sha256"):
        refuse("U0h initramfs differs from its audited report")

    compressed = u0h_image.read_bytes()
    try:
        base = Archive.parse(gzip.decompress(compressed))
    except (OSError, CpioError) as exc:
        refuse(f"cannot parse U0h initramfs: {exc}")
    functions_entry = base.one(TARGET)
    init2_entry = base.one("init_2nd.sh")
    functions = functions_entry.data.decode("utf-8", "strict")
    init2 = init2_entry.data.decode("utf-8", "strict")
    _, _, original_find = function_span(functions, "find_root_partition")
    _, _, original_wait = function_span(functions, "wait_root_partition")
    if "find_root_partition" not in original_wait:
        refuse("wait_root_partition does not call find_root_partition")
    mode = root_consumption_mode(original_wait)
    ordered = second_stage_calls(init2)

    replacement = f'''find_root_partition() {{\n\t# A33 U0i: U0h creates and verifies this node before root discovery.\n\ta33x_root={FORCED_ROOT}\n\t[ -b "$a33x_root" ] || return 0\n\ta33x_identity="$(blkid "$a33x_root" 2>/dev/null || true)"\n\tcase "$a33x_identity" in *'TYPE="ext4"'*) ;; *) unset a33x_root a33x_identity; return 0 ;; esac\n\tcase "$a33x_identity" in *'LABEL="pmOS_root"'*) ;; *) unset a33x_root a33x_identity; return 0 ;; esac\n\tprintf '<6>a33x-direct-root-v2: selected %s\\n' "$a33x_root" > /dev/kmsg 2>/dev/null || true\n\tprintf '%s\\n' "$a33x_root"\n\tunset a33x_root a33x_identity\n}}\n'''
    patched_functions, observed_find = replace_function(functions, "find_root_partition", replacement)
    if observed_find != original_find or function_span(patched_functions, "wait_root_partition")[2] != original_wait:
        refuse("function patch changed unexpected shell code")

    inspect.mkdir(parents=True, exist_ok=True)
    (inspect / "original-find_root_partition.sh").write_text(original_find, encoding="utf-8")
    (inspect / "patched-find_root_partition.sh").write_text(replacement, encoding="utf-8")
    (inspect / "original-wait_root_partition.sh").write_text(original_wait, encoding="utf-8")
    syntax_file = inspect / "patched-init_functions.sh"
    syntax_file.write_text(patched_functions, encoding="utf-8")
    subprocess.run(["sh", "-n", str(syntax_file)], check=True)
    (inspect / "second-stage-order.txt").write_text(
        "\n".join(f"{label} line={line} text={text}" for label, line, text in ordered) + "\n",
        encoding="utf-8",
    )

    patched_payload = base.replace(TARGET, patched_functions.encode())
    patched = Archive.parse(patched_payload)
    base.assert_only_payload_changed(patched, TARGET)
    if count_modules(base) != MODULES or count_modules(patched) != MODULES:
        refuse("module count changed or is not 67")
    one_hash(patched, "usr/libexec/a33x-muic-switch-dynamic", u0g.get("dynamic_helper_sha256", ""))
    one_hash(patched, "hooks/03-a33x-muic-switch-dynamic.sh", u0g.get("dynamic_hook03_sha256", ""))
    one_hash(patched, "hooks/04-a33x-muic-persist-dynamic.sh", u0g.get("dynamic_hook04_sha256", ""))
    one_hash(patched, "hooks/05-a33x-userdata-root-node.sh", u0h.get("root_node_hook_sha256", ""))

    output_image.parent.mkdir(parents=True, exist_ok=True)
    output_image.write_bytes(gzip.compress(patched_payload, compresslevel=9, mtime=0))
    roundtrip = Archive.parse(gzip.decompress(output_image.read_bytes()))
    if roundtrip.one(TARGET).data != patched_functions.encode() or roundtrip.tail != base.tail:
        refuse("written initramfs did not round-trip")

    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        refuse("cannot resolve repository commit")
    created = subprocess.run(["date", "-Ins"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    patch_pairs: list[tuple[str, object]] = [
        ("created", created),
        ("operation", "python-byte-preserving-newc-patch"),
        ("linuxa33_commit", commit),
        ("implementation_language", "python3"),
        ("u0h_initramfs_sha256", sha_bytes(compressed)),
        ("u0i_initramfs", output_image),
        ("u0i_initramfs_sha256", sha_file(output_image)),
        ("cpio_entry_count", len(base.entries)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_metadata_preserved_except_target_size_and_crc", "yes"),
        ("cpio_payload_delta", TARGET),
        ("original_init_functions_sha256", sha_bytes(functions_entry.data)),
        ("patched_init_functions_sha256", sha_bytes(patched_functions.encode())),
        ("original_find_root_sha256", sha_bytes(original_find.encode())),
        ("patched_find_root_sha256", sha_bytes(replacement.encode())),
        ("preserved_wait_root_sha256", sha_bytes(original_wait.encode())),
        ("wait_root_consumption_mode", mode),
        ("wait_root_consumes_find_root_stdout", "yes"),
        ("wait_root_function_preserved", "yes"),
        ("direct_root_identity_recheck", "yes"),
        ("forced_root", FORCED_ROOT),
        ("embedded_modules", MODULES),
        ("patch_status", "passed"),
        ("phone_partition_writes", "no"),
    ]
    patch_pairs += [("second_stage_call", f"{label} line={line} text={text}") for label, line, text in ordered]
    patch_pairs.append(("second_stage_order_validation", "passed"))
    write_report(patch_report, patch_pairs)

    recovery = build_recovery(root, repo, output_image, recovery_output)
    info = recovery_output / "final-boot-info.txt"
    if not info.is_file() or re.search(r"(?:^|\s)pmos_root=\S+", info.read_text(errors="replace")):
        refuse("recovery command line validation failed")
    if recovery.stat().st_size != 100663296:
        refuse(f"unexpected recovery size: {recovery.stat().st_size}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(recovery, candidate)
    manifest_pairs: list[tuple[str, object]] = [
        ("candidate", "U0i-python-direct-root"),
        ("created", subprocess.run(["date", "-Ins"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()),
        ("linuxa33_commit", commit),
        ("implementation_language", "python3"),
        ("functional_base", "U0h-userdata-root-node"),
        ("functional_delta", "replace-only-init_functions-newc-payload"),
        ("kernel_cmdline_delta", "none"),
        ("module_delta", "none"),
        ("forced_root", FORCED_ROOT),
        ("patch_report", patch_report),
        ("patch_report_sha256", sha_file(patch_report)),
        ("u0h_initramfs_sha256", sha_bytes(compressed)),
        ("u0i_initramfs", output_image),
        ("u0i_initramfs_sha256", sha_file(output_image)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_metadata_preserved_except_target_size_and_crc", "yes"),
        ("cpio_payload_delta", TARGET),
        ("embedded_modules", MODULES),
        ("wait_root_function_preserved", "yes"),
        ("direct_root_identity_recheck", "yes"),
        ("second_stage_order_validation", "passed"),
        ("preparation_status", "passed"),
        ("phone_partition_writes", "no"),
        ("recovery", candidate),
        ("recovery_size", candidate.stat().st_size),
        ("recovery_sha256", sha_file(candidate)),
        ("build_status", "passed"),
    ]
    write_report(manifest, manifest_pairs)
    print(f"\nCandidate: {candidate}\nManifest: {manifest}\nNo phone partition was written.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Refusal, CpioError, ShellContractError) as exc:
        print(f"REFUSING U0i: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        print(f"REFUSING U0i: command failed rc={exc.returncode}: {exc.cmd}", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
