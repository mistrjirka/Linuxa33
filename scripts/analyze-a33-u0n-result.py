#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

EXPECTED_CANDIDATE_SHA256 = "9196109cba6a6e13f314b2aba28de21580c8b434c74e075c451d84b48da1bc2d"


class AnalysisError(RuntimeError):
    pass


def safe_member_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or any(c in name for c in "\x00\r\n"):
        raise AnalysisError(f"unsafe archive member: {name!r}")
    return path


def read_member(archive: tarfile.TarFile, suffix: str) -> bytes:
    matches = []
    for member in archive.getmembers():
        path = safe_member_name(member.name)
        if member.isfile() and path.as_posix().endswith(suffix):
            matches.append(member)
    if len(matches) != 1:
        raise AnalysisError(f"expected one archive member ending in {suffix!r}, found {len(matches)}")
    stream = archive.extractfile(matches[0])
    if stream is None:
        raise AnalysisError(f"cannot read archive member: {matches[0].name}")
    with stream:
        return stream.read()


def parse_jsonl(data: bytes) -> list[dict[str, object]]:
    rows = []
    for number, raw in enumerate(data.decode("utf-8", errors="strict").splitlines(), 1):
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AnalysisError(f"invalid observation JSONL line {number}: {exc}") from exc
        if not isinstance(value, dict):
            raise AnalysisError(f"observation JSONL line {number} is not an object")
        rows.append(value)
    if not rows:
        raise AnalysisError("observation JSONL is empty")
    return rows


def analyze_archive(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise AnalysisError(f"archive is missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with tarfile.open(path, "r:*") as archive:
            summary = json.loads(read_member(archive, "/summary.json"))
            observation_summary = json.loads(
                read_member(archive, "/observation/summary.json")
            )
            rows = parse_jsonl(read_member(archive, "/observation/observation.jsonl"))
            last_kmsg = read_member(
                archive, "/last_kmsg.sanitized.txt"
            ).decode("utf-8", errors="replace")
    except (tarfile.TarError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"cannot inspect archive: {exc}") from exc

    if summary.get("candidate_sha256") != EXPECTED_CANDIDATE_SHA256:
        raise AnalysisError("archive references another candidate")
    if observation_summary.get("observation_status") != "passed-full-90-second-window":
        raise AnalysisError("observation did not complete the full 90-second window")

    marker_pattern = re.compile(r"a33x-u0[klmno]-|a33x-watchdog-v2", re.IGNORECASE)
    u0n_marker_count = len(re.findall(r"a33x-u0n-real-boot-sshd", last_kmsg, re.IGNORECASE))
    all_marker_count = len(marker_pattern.findall(last_kmsg))
    panic_uptimes = [
        float(value)
        for value in re.findall(r"\[(\d{4,}(?:\.\d+)?)\].{0,160}\brecovery\b", last_kmsg)
    ]
    long_running_recovery_uptime = max(panic_uptimes, default=0.0)
    hard_reset_hook = bool(
        re.search(r"hard.{0,20}reset.{0,20}hook|sec_hard_reset_hook", last_kmsg, re.IGNORECASE)
    )
    twrp_recovery_process = bool(
        re.search(r"\brecovery\b", last_kmsg, re.IGNORECASE)
        and re.search(r"Gabriel260BR-TWRP", last_kmsg, re.IGNORECASE)
    )

    usb_lines = {
        str(row.get("usb_line", ""))
        for row in rows
        if bool(row.get("usb_enumeration")) and row.get("usb_line")
    }
    interface_ever = any(bool(row.get("host_usb_network_interface")) for row in rows)
    ping_ever = any(bool(row.get("ping_172_16_42_1")) for row in rows)
    ssh_ever = any(bool(row.get("ssh_banner")) for row in rows)
    tcp_states: dict[str, int] = {}
    for row in rows:
        state = str(row.get("tcp22_state", "missing"))
        tcp_states[state] = tcp_states.get(state, 0) + 1

    preserved_kernel = "unknown"
    if u0n_marker_count == 0 and twrp_recovery_process and long_running_recovery_uptime > 3600:
        preserved_kernel = "long-running-twrp-not-u0n"

    reboot_transition = "not-verified"
    if len(usb_lines) > 1:
        reboot_transition = "usb-identity-changed"
    elif len(usb_lines) == 1:
        reboot_transition = "single-unchanged-usb-identity"

    diagnosis = "u0n-runtime-unproven"
    if preserved_kernel == "long-running-twrp-not-u0n":
        diagnosis = "last-kmsg-preserved-pre-u0n-twrp-hard-reset-not-u0n"

    return {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "analyze-u0n-real-boot-result-host-only",
        "archive": str(path.resolve()),
        "archive_sha256": digest,
        "candidate_sha256": EXPECTED_CANDIDATE_SHA256,
        "observation_seconds": observation_summary.get("observation_seconds"),
        "observation_rows": len(rows),
        "usb_identity_unique_count": len(usb_lines),
        "usb_identities": sorted(usb_lines),
        "reboot_transition_evidence": reboot_transition,
        "interface_ever": interface_ever,
        "ping_ever": ping_ever,
        "ssh_banner_ever": ssh_ever,
        "tcp22_state_counts": tcp_states,
        "last_kmsg_all_a33_markers": all_marker_count,
        "last_kmsg_u0n_markers": u0n_marker_count,
        "last_kmsg_hard_reset_hook": hard_reset_hook,
        "last_kmsg_twrp_recovery_process": twrp_recovery_process,
        "last_kmsg_max_recovery_uptime_seconds": long_running_recovery_uptime,
        "last_kmsg_kernel_classification": preserved_kernel,
        "u0n_execution_status": "unproven",
        "persistent_trace_required": True,
        "observer_must_verify_old_adb_disappears": True,
        "diagnosis": diagnosis,
        "phone_partition_writes": "no",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze collected U0n boot evidence without touching the phone")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze_archive(args.archive.expanduser().resolve())
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"report={output}")
    print(text, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AnalysisError, OSError, ValueError) as exc:
        print(f"U0n RESULT ANALYSIS FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
