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
from a33_shell import ShellContractError, function_span, replace_function, second_stage_calls

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


def mask_functions(text: str, names: tuple[str, ...]) -> str:
    lines = text.splitlines(keepends=True)
    spans = []
    for name in names:
        start, end, _ = function_span(text, name)
        spans.append((start, end, name))
    for start, end, name in sorted(spans, reverse=True):
        lines[start:end] = [f"@@A33_FUNCTION:{name}@@\n"]
    return "".join(lines)


def root_replacements() -> tuple[str, str]:
    find_replacement = f'''find_root_partition() {{
\t# A33 U0i v2: hook 05 creates and verifies this node before root wait.
\ta33x_root={FORCED_ROOT}
\t[ -b "$a33x_root" ] || return 0
\ta33x_identity="$(blkid "$a33x_root" 2>/dev/null || true)"
\tcase "$a33x_identity" in
\t\t*'TYPE="ext4"'*) ;;
\t\t*) unset a33x_root a33x_identity; return 0 ;;
\tesac
\tcase "$a33x_identity" in
\t\t*'LABEL="pmOS_root"'*) ;;
\t\t*) unset a33x_root a33x_identity; return 0 ;;
\tesac
\tprintf '<6>a33x-direct-root-v3: selected %s\\n' "$a33x_root" > /dev/kmsg 2>/dev/null || true
\tprintf '%s\\n' "$a33x_root"
\tunset a33x_root a33x_identity
}}
'''
    wait_replacement = '''wait_root_partition() {
\twhile [ -z "$(find_root_partition)" ]; do
\t\tshow_splash /splash-norootfs.ppm.gz 2>/dev/null || true
\t\techo "Could not validate the A33 userdata rootfs. Trying again in one second..."
\t\tsleep 1
\tdone
}
'''
    return find_replacement, wait_replacement


def patch_root_functions(functions: str) -> tuple[str, dict[str, str]]:
    original_find = function_span(functions, "find_root_partition")[2]
    original_wait = function_span(functions, "wait_root_partition")[2]
    baseline = mask_functions(functions, ("find_root_partition", "wait_root_partition"))
    find_replacement, wait_replacement = root_replacements()
    patched, observed_find = replace_function(functions, "find_root_partition", find_replacement)
    patched, observed_wait = replace_function(patched, "wait_root_partition", wait_replacement)
    if observed_find != original_find or observed_wait != original_wait:
        refuse("function replacement did not preserve the observed originals")
    if mask_functions(patched, ("find_root_partition", "wait_root_partition")) != baseline:
        refuse("shell text changed outside find_root_partition and wait_root_partition")
    if function_span(patched, "find_root_partition")[2] != find_replacement:
        refuse("patched find_root_partition did not round-trip")
    if function_span(patched, "wait_root_partition")[2] != wait_replacement:
        refuse("patched wait_root_partition did not round-trip")
    if len(re.findall(r"\$\(\s*find_root_partition\s*\)", wait_replacement)) != 1:
        refuse("patched wait_root_partition does not directly consume patched discovery")
    return patched, {
        "original_find": original_find,
        "original_wait": original_wait,
        "patched_find": find_replacement,
        "patched_wait": wait_replacement,
    }


def build_recovery(root: Path, repo: Path, initramfs: Path, output: Path) -> Path:
    builder = repo / "scripts/make-pmos-debug-recovery.sh"
    if not builder.is_file():
        refuse(f"missing proven recovery builder: {builder}")
    with tempfile.TemporaryDirectory(prefix="a33-u0i-v2-") as temp:
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
    parser = argparse.ArgumentParser(description="Build U0i v2 by replacing both root functions in one CPIO payload")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()

    u0h_image = root / "export-u0h-root-node/initramfs"
    u0h_report_path = root / "build/u0h-userdata-root-node.txt"
    u0g_report_path = root / "build/u0g-muic-dynamic.txt"
    output_image = root / "export-u0i-python-direct-root-v2/initramfs"
    inspect = root / "build/u0i-python-direct-root-v2-inspection"
    patch_report = root / "build/u0i-python-direct-root-v2-patch.txt"
    recovery_output = root / "build/pmos-debug-recovery-u0i-python-direct-root-v2"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0i-python-direct-root-v2-recovery.img"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0i-python-direct-root-v2-manifest.txt"

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
    ordered = second_stage_calls(init2)
    patched_functions, parts = patch_root_functions(functions)

    inspect.mkdir(parents=True, exist_ok=True)
    for name, text in parts.items():
        (inspect / f"{name}.sh").write_text(text, encoding="utf-8")
    (inspect / "original-init_functions.sh").write_text(functions, encoding="utf-8")
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
        ("operation", "python-byte-preserving-replace-two-root-functions"),
        ("linuxa33_commit", commit),
        ("implementation_language", "python3"),
        ("u0h_initramfs_sha256", sha_bytes(compressed)),
        ("u0i_initramfs", output_image),
        ("u0i_initramfs_sha256", sha_file(output_image)),
        ("cpio_entry_count", len(base.entries)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_metadata_preserved_except_target_size_and_crc", "yes"),
        ("cpio_payload_delta", TARGET),
        ("shell_delta", "find_root_partition,wait_root_partition"),
        ("shell_text_outside_two_functions_preserved", "yes"),
        ("original_init_functions_sha256", sha_bytes(functions_entry.data)),
        ("patched_init_functions_sha256", sha_bytes(patched_functions.encode())),
        ("original_find_root_sha256", sha_bytes(parts["original_find"].encode())),
        ("original_wait_root_sha256", sha_bytes(parts["original_wait"].encode())),
        ("patched_find_root_sha256", sha_bytes(parts["patched_find"].encode())),
        ("patched_wait_root_sha256", sha_bytes(parts["patched_wait"].encode())),
        ("patched_wait_directly_consumes_patched_find", "yes"),
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
        ("candidate", "U0i-python-direct-root-v2"),
        ("created", subprocess.run(["date", "-Ins"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()),
        ("linuxa33_commit", commit),
        ("implementation_language", "python3"),
        ("functional_base", "U0h-userdata-root-node"),
        ("functional_delta", "replace-find-and-wait-root-functions-only"),
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
        ("shell_delta", "find_root_partition,wait_root_partition"),
        ("shell_text_outside_two_functions_preserved", "yes"),
        ("embedded_modules", MODULES),
        ("patched_wait_directly_consumes_patched_find", "yes"),
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
    except (Refusal, CpioError, ShellContractError, UnicodeDecodeError) as exc:
        print(f"REFUSING U0i v2: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        print(f"REFUSING U0i v2: command failed rc={exc.returncode}: {exc.cmd}", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
