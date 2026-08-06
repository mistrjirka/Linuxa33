#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

DEFAULT_SERIAL = "RFCTA00V43L"


class CollectError(RuntimeError):
    pass


def run(
    command: list[str],
    *,
    timeout: float,
    text: bool,
    check: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        stdout = completed.stdout if text else completed.stdout.decode(errors="replace")
        stderr = completed.stderr if text else completed.stderr.decode(errors="replace")
        raise CollectError(
            f"command failed rc={completed.returncode}: {command!r}\n"
            f"stdout={stdout[-2000:]}\nstderr={stderr[-2000:]}"
        )
    return completed


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize(payload: bytes) -> str:
    chars: list[str] = []
    for byte in payload:
        if byte in (9, 10, 13) or 32 <= byte < 127:
            chars.append(chr(byte))
        elif byte == 0:
            chars.append("\n")
        else:
            chars.append("�")
    return "".join(chars)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read only /proc/last_kmsg and pstore from the current TWRP boot"
    )
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    args = parser.parse_args()

    adb = shutil.which(args.adb) or args.adb
    root = args.root.expanduser().resolve()
    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"a33-last-kmsg-minimal-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)

    print("stage=probing-adb", flush=True)
    probe = run(
        [adb, "-s", args.serial, "shell", "printf", "a33_ready"],
        timeout=5,
        text=True,
    )
    if probe.stdout.replace("\r", "").strip() != "a33_ready":
        raise CollectError(f"unexpected adb probe output: {probe.stdout!r}")
    print("adb_probe=passed", flush=True)

    print("stage=reading-last-kmsg", flush=True)
    last = run(
        [adb, "-s", args.serial, "exec-out", "cat", "/proc/last_kmsg"],
        timeout=45,
        text=False,
    ).stdout
    if not last:
        raise CollectError("/proc/last_kmsg was empty")
    last_path = out / "last_kmsg.bin"
    last_path.write_bytes(last)
    sanitized = sanitize(last)
    (out / "last_kmsg.sanitized.txt").write_text(sanitized, encoding="utf-8")

    pattern = (
        "u0r|u0p|u0o|switch_root|switching root|openrc|sshd|watchdog|wdt|"
        "kernel panic|panic - not syncing|reboot|reset|call trace|bug:|oops|"
        "unable to handle|init_2nd|root-node|mount-root|cleanup-hooks"
    )
    focused = run(
        ["grep", "-aEin", pattern, str(out / "last_kmsg.sanitized.txt")],
        timeout=10,
        text=True,
        check=False,
    ).stdout
    (out / "last_kmsg.focused.txt").write_text(focused, encoding="utf-8")

    print("stage=reading-pstore-state", flush=True)
    listing = run(
        [
            adb,
            "-s",
            args.serial,
            "shell",
            "find /sys/fs/pstore -maxdepth 1 -type f -print 2>/dev/null",
        ],
        timeout=10,
        text=True,
        check=False,
    ).stdout.replace("\r", "")
    (out / "pstore-list.txt").write_text(listing, encoding="utf-8")
    pstore_dir = out / "pstore"
    pstore_dir.mkdir()
    pstore_count = 0
    for raw in listing.splitlines():
        remote = raw.strip()
        if not remote.startswith("/sys/fs/pstore/") or any(c.isspace() for c in remote):
            continue
        payload = run(
            [adb, "-s", args.serial, "exec-out", "cat", remote],
            timeout=20,
            text=False,
            check=False,
        ).stdout
        local = pstore_dir / Path(remote).name
        local.write_bytes(payload)
        pstore_count += 1

    summary = out / "summary.txt"
    summary.write_text(
        "".join(
            (
                f"created={datetime.now().astimezone().isoformat(timespec='microseconds')}\n",
                "operation=collect-a33-last-kmsg-minimal\n",
                f"adb_serial={args.serial}\n",
                f"last_kmsg_bytes={len(last)}\n",
                f"last_kmsg_sha256={sha256(last_path)}\n",
                f"focused_lines={len(focused.splitlines())}\n",
                f"pstore_file_count={pstore_count}\n",
                "phone_partition_writes=no\n",
                "collection_status=passed\n",
            )
        ),
        encoding="utf-8",
    )

    archive = out.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)
    archive_sha = sha256(archive)

    print(summary.read_text(encoding="utf-8"), end="")
    print(f"archive={archive}")
    print(f"archive_sha256={archive_sha}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("COLLECTION INTERRUPTED", file=sys.stderr)
        raise SystemExit(130)
    except (CollectError, OSError, subprocess.SubprocessError) as exc:
        print(f"COLLECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
