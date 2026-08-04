#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
V2_PATH = HERE / "make-u0i-python-direct-root-v2.py"
EXPECTED_V2_GIT_BLOB = "be1529432f9f4bbb668bb8b520c089a6c4373b24"
spec = importlib.util.spec_from_file_location("a33_u0i_v2_builder", V2_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0i v2 builder: {V2_PATH}")
v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2)

FORCED_ROOT = "/dev/block/sda36"
MODULES = 67
TARGET = "init_functions.sh"


class Refusal(RuntimeError):
    pass


def refuse(message: str) -> None:
    raise Refusal(message)


def git_blob(repo: Path, path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", result):
        refuse(f"cannot resolve Git blob for {path}")
    return result


def shell_texts(archive: object) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in archive.entries:
        name = entry.normalized
        if name == "TRAILER!!!" or not entry.data:
            continue
        if not (
            name in {"init", "init_2nd.sh", "init_functions.sh", "init_functions_2nd.sh"}
            or name.endswith(".sh")
        ):
            continue
        try:
            result[name] = entry.data.decode("utf-8", "strict")
        except UnicodeDecodeError:
            continue
    return result


def classify_find_root_calls(texts: dict[str, str]) -> list[tuple[str, int, str, str]]:
    calls: list[tuple[str, int, str, str]] = []
    substitution = re.compile(r"\$\(\s*find_root_partition\s*\)")
    definition = re.compile(r"^[ \t]*find_root_partition[ \t]*\([ \t]*\)[ \t]*\{")
    for path in sorted(texts):
        for number, raw in enumerate(texts[path].splitlines(), 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or "find_root_partition" not in raw:
                continue
            if definition.match(raw):
                continue
            working = raw
            sub_count = len(substitution.findall(working))
            if sub_count:
                calls.extend((path, number, "stdout-command-substitution", raw) for _ in range(sub_count))
                working = substitution.sub("", working)
            if "find_root_partition" not in working:
                continue
            command = working.strip()
            if command == "find_root_partition":
                calls.append((path, number, "stdout-direct", raw))
            elif command == "find_root_partition partition":
                calls.append((path, number, "output-variable:partition", raw))
            else:
                refuse(f"unsupported find_root_partition invocation: {path}:{number}:{raw}")
    modes = {mode for _, _, mode, _ in calls}
    if "stdout-command-substitution" not in modes:
        refuse("no command-substitution caller of find_root_partition was found")
    if "output-variable:partition" not in modes:
        refuse("no output-variable caller 'find_root_partition partition' was found")
    return calls


def compatible_find_replacement() -> str:
    return f'''find_root_partition() {{
\t# A33 U0j: hook 05 creates and verifies this exact userdata node.
\t# Preserve both postmarketOS APIs used by this generated initramfs:
\t#   find_root_partition            -> print the device path
\t#   find_root_partition partition  -> assign the caller's local variable
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
\tcase "$#" in
\t\t0)
\t\t\tprintf '<6>a33x-direct-root-v4: mode=stdout selected=%s\\n' "$a33x_root" > /dev/kmsg 2>/dev/null || true
\t\t\tprintf '%s\\n' "$a33x_root"
\t\t\t;;
\t\t1)
\t\t\t[ "$1" = partition ] || {{ unset a33x_root a33x_identity; return 2; }}
\t\t\tpartition="$a33x_root"
\t\t\tprintf '<6>a33x-direct-root-v4: mode=output-variable selected=%s\\n' "$a33x_root" > /dev/kmsg 2>/dev/null || true
\t\t\t;;
\t\t*)
\t\t\tunset a33x_root a33x_identity
\t\t\treturn 2
\t\t\t;;
\tesac
\tunset a33x_root a33x_identity
}}
'''


def patch_find_only(functions: str) -> tuple[str, str, str, str]:
    original_find = v2.function_span(functions, "find_root_partition")[2]
    original_wait = v2.function_span(functions, "wait_root_partition")[2]
    baseline = v2.mask_functions(functions, ("find_root_partition",))
    replacement = compatible_find_replacement()
    patched, observed = v2.replace_function(functions, "find_root_partition", replacement)
    if observed != original_find:
        refuse("find_root_partition replacement did not preserve the observed original")
    if v2.mask_functions(patched, ("find_root_partition",)) != baseline:
        refuse("shell text changed outside find_root_partition")
    if v2.function_span(patched, "find_root_partition")[2] != replacement:
        refuse("patched find_root_partition did not round-trip")
    if v2.function_span(patched, "wait_root_partition")[2] != original_wait:
        refuse("wait_root_partition changed unexpectedly")
    required = (
        'case "$#" in',
        'partition="$a33x_root"',
        "mode=stdout",
        "mode=output-variable",
        FORCED_ROOT,
        'LABEL="pmOS_root"',
        'TYPE="ext4"',
    )
    for token in required:
        if token not in replacement:
            refuse(f"compatible find_root_partition lacks token: {token}")
    return patched, original_find, original_wait, replacement


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build U0j by fixing the dual find_root_partition API in U0i"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()

    if git_blob(repo, V2_PATH) != EXPECTED_V2_GIT_BLOB:
        refuse("U0i v2 builder changed unexpectedly; review U0j ancestry before rebuilding")

    u0i_manifest_path = root / "build/candidates/a33x-h1-usbpd-u0i-python-direct-root-v2-manifest.txt"
    u0i_image = root / "export-u0i-python-direct-root-v2/initramfs"
    u0h_report_path = root / "build/u0h-userdata-root-node.txt"
    u0g_report_path = root / "build/u0g-muic-dynamic.txt"
    output_image = root / "export-u0j-root-api-compatible/initramfs"
    inspect = root / "build/u0j-root-api-compatible-inspection"
    patch_report = root / "build/u0j-root-api-compatible-patch.txt"
    recovery_output = root / "build/pmos-debug-recovery-u0j-root-api-compatible"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0j-root-api-compatible-recovery.img"
    manifest = root / "build/candidates/a33x-h1-usbpd-u0j-root-api-compatible-manifest.txt"

    for path in (u0i_manifest_path, u0i_image, u0h_report_path, u0g_report_path):
        if not path.is_file():
            refuse(f"missing input: {path}")

    u0i = v2.kv(u0i_manifest_path)
    v2.require(
        u0i,
        {
            "candidate": "U0i-python-direct-root-v2",
            "implementation_language": "python3",
            "functional_base": "U0h-userdata-root-node",
            "functional_delta": "replace-find-and-wait-root-functions-only",
            "kernel_cmdline_delta": "none",
            "module_delta": "none",
            "forced_root": FORCED_ROOT,
            "cpio_payload_delta": TARGET,
            "shell_delta": "find_root_partition,wait_root_partition",
            "shell_text_outside_two_functions_preserved": "yes",
            "embedded_modules": str(MODULES),
            "patched_wait_directly_consumes_patched_find": "yes",
            "direct_root_identity_recheck": "yes",
            "second_stage_order_validation": "passed",
            "preparation_status": "passed",
            "phone_partition_writes": "no",
            "build_status": "passed",
        },
        "U0i manifest",
    )
    if v2.sha_file(u0i_image) != u0i.get("u0i_initramfs_sha256"):
        refuse("U0i initramfs differs from its manifest")

    u0h, u0g = v2.kv(u0h_report_path), v2.kv(u0g_report_path)
    v2.require(
        u0h,
        {
            "preparation_status": "passed",
            "embedded_modules": str(MODULES),
            "phone_partition_writes": "no",
        },
        "U0h report",
    )

    compressed = u0i_image.read_bytes()
    try:
        base = v2.Archive.parse(gzip.decompress(compressed))
    except (OSError, v2.CpioError) as exc:
        refuse(f"cannot parse U0i initramfs: {exc}")
    functions_entry = base.one(TARGET)
    init2_entry = base.one("init_2nd.sh")
    functions = functions_entry.data.decode("utf-8", "strict")
    init2 = init2_entry.data.decode("utf-8", "strict")
    ordered = v2.second_stage_calls(init2)
    calls = classify_find_root_calls(shell_texts(base))
    patched_functions, original_find, original_wait, replacement = patch_find_only(functions)

    inspect.mkdir(parents=True, exist_ok=True)
    (inspect / "original-find_root_partition.sh").write_text(original_find, encoding="utf-8")
    (inspect / "patched-find_root_partition.sh").write_text(replacement, encoding="utf-8")
    (inspect / "preserved-wait_root_partition.sh").write_text(original_wait, encoding="utf-8")
    syntax_file = inspect / "patched-init_functions.sh"
    syntax_file.write_text(patched_functions, encoding="utf-8")
    subprocess.run(["sh", "-n", str(syntax_file)], check=True)
    (inspect / "find-root-call-sites.txt").write_text(
        "\n".join(f"{mode} {path}:{line}:{raw}" for path, line, mode, raw in calls) + "\n",
        encoding="utf-8",
    )

    patched_payload = base.replace(TARGET, patched_functions.encode())
    patched = v2.Archive.parse(patched_payload)
    base.assert_only_payload_changed(patched, TARGET)
    if v2.count_modules(base) != MODULES or v2.count_modules(patched) != MODULES:
        refuse("module count changed or is not 67")
    v2.one_hash(patched, "usr/libexec/a33x-muic-switch-dynamic", u0g.get("dynamic_helper_sha256", ""))
    v2.one_hash(patched, "hooks/03-a33x-muic-switch-dynamic.sh", u0g.get("dynamic_hook03_sha256", ""))
    v2.one_hash(patched, "hooks/04-a33x-muic-persist-dynamic.sh", u0g.get("dynamic_hook04_sha256", ""))
    v2.one_hash(patched, "hooks/05-a33x-userdata-root-node.sh", u0h.get("root_node_hook_sha256", ""))

    output_image.parent.mkdir(parents=True, exist_ok=True)
    output_image.write_bytes(gzip.compress(patched_payload, compresslevel=9, mtime=0))
    roundtrip = v2.Archive.parse(gzip.decompress(output_image.read_bytes()))
    if roundtrip.one(TARGET).data != patched_functions.encode() or roundtrip.tail != base.tail:
        refuse("written U0j initramfs did not round-trip")

    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    created = subprocess.run(
        ["date", "-Ins"], check=True, text=True, stdout=subprocess.PIPE
    ).stdout.strip()
    stdout_calls = sum(mode.startswith("stdout-") for _, _, mode, _ in calls)
    variable_calls = sum(mode == "output-variable:partition" for _, _, mode, _ in calls)
    patch_pairs: list[tuple[str, object]] = [
        ("created", created),
        ("operation", "python-byte-preserving-fix-find-root-dual-api"),
        ("linuxa33_commit", commit),
        ("implementation_language", "python3"),
        ("functional_base", "U0i-python-direct-root-v2"),
        ("u0i_initramfs", u0i_image),
        ("u0i_initramfs_sha256", v2.sha_bytes(compressed)),
        ("u0j_initramfs", output_image),
        ("u0j_initramfs_sha256", v2.sha_file(output_image)),
        ("cpio_entry_count", len(base.entries)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_metadata_preserved_except_target_size_and_crc", "yes"),
        ("cpio_payload_delta", TARGET),
        ("shell_delta", "find_root_partition"),
        ("shell_text_outside_find_root_partition_preserved", "yes"),
        ("wait_root_function_preserved", "yes"),
        ("find_root_stdout_api", "passed"),
        ("find_root_output_variable_api", "partition"),
        ("find_root_stdout_call_count", stdout_calls),
        ("find_root_output_variable_call_count", variable_calls),
        ("original_init_functions_sha256", v2.sha_bytes(functions_entry.data)),
        ("patched_init_functions_sha256", v2.sha_bytes(patched_functions.encode())),
        ("original_find_root_sha256", v2.sha_bytes(original_find.encode())),
        ("patched_find_root_sha256", v2.sha_bytes(replacement.encode())),
        ("preserved_wait_root_sha256", v2.sha_bytes(original_wait.encode())),
        ("direct_root_identity_recheck", "yes"),
        ("forced_root", FORCED_ROOT),
        ("embedded_modules", MODULES),
        ("patch_status", "passed"),
        ("phone_partition_writes", "no"),
    ]
    patch_pairs += [("find_root_call_site", f"{mode} {path}:{line}:{raw}") for path, line, mode, raw in calls]
    patch_pairs += [("second_stage_call", f"{label} line={line} text={text}") for label, line, text in ordered]
    patch_pairs.append(("second_stage_order_validation", "passed"))
    v2.write_report(patch_report, patch_pairs)

    recovery = v2.build_recovery(root, repo, output_image, recovery_output)
    info = recovery_output / "final-boot-info.txt"
    if not info.is_file() or re.search(r"(?:^|\s)pmos_root=\S+", info.read_text(errors="replace")):
        refuse("recovery command line validation failed")
    if recovery.stat().st_size != 100663296:
        refuse(f"unexpected recovery size: {recovery.stat().st_size}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(recovery, candidate)
    manifest_pairs: list[tuple[str, object]] = [
        ("candidate", "U0j-root-api-compatible"),
        ("created", subprocess.run(["date", "-Ins"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()),
        ("linuxa33_commit", commit),
        ("implementation_language", "python3"),
        ("functional_base", "U0i-python-direct-root-v2"),
        ("functional_delta", "make-find-root-partition-support-stdout-and-output-variable"),
        ("kernel_cmdline_delta", "none"),
        ("module_delta", "none"),
        ("forced_root", FORCED_ROOT),
        ("patch_report", patch_report),
        ("patch_report_sha256", v2.sha_file(patch_report)),
        ("u0i_initramfs", u0i_image),
        ("u0i_initramfs_sha256", v2.sha_bytes(compressed)),
        ("u0j_initramfs", output_image),
        ("u0j_initramfs_sha256", v2.sha_file(output_image)),
        ("cpio_entry_order_preserved", "yes"),
        ("cpio_metadata_preserved_except_target_size_and_crc", "yes"),
        ("cpio_payload_delta", TARGET),
        ("shell_delta", "find_root_partition"),
        ("shell_text_outside_find_root_partition_preserved", "yes"),
        ("wait_root_function_preserved", "yes"),
        ("find_root_stdout_api", "passed"),
        ("find_root_output_variable_api", "partition"),
        ("embedded_modules", MODULES),
        ("direct_root_identity_recheck", "yes"),
        ("second_stage_order_validation", "passed"),
        ("preparation_status", "passed"),
        ("phone_partition_writes", "no"),
        ("recovery", candidate),
        ("recovery_size", candidate.stat().st_size),
        ("recovery_sha256", v2.sha_file(candidate)),
        ("build_status", "passed"),
    ]
    v2.write_report(manifest, manifest_pairs)
    print(f"\nCandidate: {candidate}\nManifest: {manifest}\nNo phone partition was written.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Refusal, v2.Refusal, v2.CpioError, v2.ShellContractError, UnicodeDecodeError) as exc:
        print(f"REFUSING U0j: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        print(f"REFUSING U0j: command failed rc={exc.returncode}: {exc.cmd}", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
