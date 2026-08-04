#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from a33_cpio import Archive, CpioError

FUNCTIONS = (
    "find_root_partition",
    "wait_root_partition",
    "resize_root_partition",
    "resize_root_filesystem",
    "mount_root_partition",
    "mount_boot_partition",
)
TOKENS = (
    "find_root_partition",
    "wait_root_partition",
    "resize_root_partition",
    "resize_root_filesystem",
    "mount_root_partition",
    "mount_boot_partition",
    "switch_root",
    "/dev/loop0",
    "pmOS_root",
    "/dev/block/sda36",
    "init_functions.sh",
    "init_functions_2nd.sh",
)


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
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values.setdefault(key, value)
    return values


def parse_image(path: Path) -> Archive:
    try:
        return Archive.parse(gzip.decompress(path.read_bytes()))
    except (OSError, CpioError) as exc:
        refuse(f"cannot parse {path}: {exc}")


def shell_texts(archive: Archive) -> dict[str, str]:
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


def function_spans(text: str, name: str) -> list[tuple[int, int, str]]:
    lines = text.splitlines(keepends=True)
    start_re = re.compile(
        rf"^[ \t]*{re.escape(name)}[ \t]*\([ \t]*\)[ \t]*\{{[ \t]*(?:#.*)?(?:\n)?$"
    )
    close_re = re.compile(r"^[ \t]*\}[ \t]*(?:#.*)?(?:\n)?$")
    spans: list[tuple[int, int, str]] = []
    starts = [i for i, line in enumerate(lines) if start_re.match(line)]
    for start in starts:
        for end in range(start + 1, len(lines)):
            if close_re.match(lines[end]):
                spans.append((start + 1, end + 1, "".join(lines[start : end + 1])))
                break
        else:
            refuse(f"unterminated {name}() beginning at line {start + 1}")
    return spans


def relevant_lines(texts: dict[str, str]) -> list[str]:
    rows: list[str] = []
    for path in sorted(texts):
        for number, raw in enumerate(texts[path].splitlines(), 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if any(token in raw for token in TOKENS):
                rows.append(f"{path}:{number}:{raw}")
    return rows


def source_lines(texts: dict[str, str]) -> list[str]:
    pattern = re.compile(r"^[ \t]*(?:\.|source)[ \t]+([^;&|]+)")
    rows: list[str] = []
    for path in sorted(texts):
        for number, raw in enumerate(texts[path].splitlines(), 1):
            if raw.lstrip().startswith("#"):
                continue
            match = pattern.search(raw)
            if match:
                rows.append(f"{path}:{number}:{raw}")
    return rows


def write_report(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(lines) + "\n"
    path.write_text(payload, encoding="utf-8")
    print(payload, end="")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect exact U0h/U0i root handoff definitions without modifying artifacts"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    args = parser.parse_args()
    root = args.root.resolve()

    u0h_path = root / "export-u0h-root-node/initramfs"
    u0i_path = root / "export-u0i-python-direct-root-v2/initramfs"
    u0h_report_path = root / "build/u0h-userdata-root-node.txt"
    u0i_manifest_path = (
        root / "build/candidates/a33x-h1-usbpd-u0i-python-direct-root-v2-manifest.txt"
    )
    report_path = root / "build/u0i-root-handoff-structure.txt"

    for path in (u0h_path, u0i_path, u0h_report_path, u0i_manifest_path):
        if not path.is_file():
            refuse(f"missing required input: {path}")

    u0h_report = kv(u0h_report_path)
    u0i_manifest = kv(u0i_manifest_path)
    if sha_file(u0h_path) != u0h_report.get("initramfs_sha256"):
        refuse("U0h initramfs differs from its report")
    if sha_file(u0i_path) != u0i_manifest.get("u0i_initramfs_sha256"):
        refuse("U0i initramfs differs from its manifest")

    u0h = parse_image(u0h_path)
    u0i = parse_image(u0i_path)
    u0h.assert_only_payload_changed(u0i, "init_functions.sh")
    base_texts = shell_texts(u0h)
    test_texts = shell_texts(u0i)

    lines: list[str] = [
        "operation=inspect-u0i-root-handoff-structure",
        "implementation_language=python3",
        f"u0h_initramfs={u0h_path}",
        f"u0h_initramfs_sha256={sha_file(u0h_path)}",
        f"u0i_initramfs={u0i_path}",
        f"u0i_initramfs_sha256={sha_file(u0i_path)}",
        "cpio_delta=init_functions.sh-only",
        "phone_partition_writes=no",
        "",
        "=== U0I SOURCE ORDER ===",
        *source_lines(test_texts),
        "",
        "=== U0I RELEVANT EXECUTABLE LINES ===",
        *relevant_lines(test_texts),
        "",
        "=== FUNCTION DEFINITIONS ===",
    ]

    definition_counts: dict[str, int] = {}
    for name in FUNCTIONS:
        count = 0
        for path in sorted(test_texts):
            spans = function_spans(test_texts[path], name)
            for start, end, body in spans:
                count += 1
                lines.extend(
                    [
                        f"--- function={name} file={path} lines={start}-{end} sha256={sha_bytes(body.encode())} ---",
                        body.rstrip("\n"),
                        f"--- end function={name} file={path} ---",
                    ]
                )
        definition_counts[name] = count
        lines.append(f"definition_count={name}:{count}")

    lines.extend(["", "=== U0H TO U0I FUNCTION CHANGES ==="])
    for name in ("find_root_partition", "wait_root_partition"):
        before = [
            (path, span[2])
            for path in sorted(base_texts)
            for span in function_spans(base_texts[path], name)
        ]
        after = [
            (path, span[2])
            for path in sorted(test_texts)
            for span in function_spans(test_texts[path], name)
        ]
        lines.append(f"function={name} u0h_definitions={len(before)} u0i_definitions={len(after)}")
        for path, body in before:
            lines.append(f"u0h_definition={name} file={path} sha256={sha_bytes(body.encode())}")
        for path, body in after:
            lines.append(f"u0i_definition={name} file={path} sha256={sha_bytes(body.encode())}")

    loop_rows = [row for row in relevant_lines(test_texts) if "/dev/loop0" in row]
    lines.extend(["", "=== LOOP0 REFERENCES ===", *loop_rows])
    lines.extend(
        [
            "",
            f"loop0_reference_count={len(loop_rows)}",
            "inspection_status=passed",
        ]
    )

    write_report(report_path, lines)
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Refusal, CpioError) as exc:
        print(f"REFUSING INSPECTOR: {exc}", file=sys.stderr)
        raise SystemExit(1)
