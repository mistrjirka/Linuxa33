#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import errno
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tarfile
import time

DEFAULT_HOST = "172.16.42.1"
DEFAULT_PORT = 22
CLIENT_BANNER = b"SSH-2.0-Linuxa33-live-diagnostic\r\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_host(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return subprocess.CompletedProcess(
            args=args,
            returncode=124,
            stdout=stdout,
            stderr=stderr + f"\ntimeout_seconds={timeout}\n",
        )


def decode_banner(payload: bytes) -> str:
    return payload.decode("ascii", errors="replace").strip()


def probe_once(
    host: str,
    port: int,
    *,
    connect_timeout: float,
    banner_timeout: float,
    send_client_banner: bool = True,
) -> dict[str, object]:
    started = time.monotonic()
    result: dict[str, object] = {
        "started": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "host": host,
        "port": port,
        "connect_timeout_seconds": connect_timeout,
        "banner_timeout_seconds": banner_timeout,
        "client_banner_sent": False,
        "banner_before_client": "",
        "banner_after_client": "",
    }
    connection: socket.socket | None = None
    try:
        connection = socket.create_connection((host, port), timeout=connect_timeout)
        result["connect_elapsed_seconds"] = round(time.monotonic() - started, 6)
        result["local_address"] = list(connection.getsockname())
        result["peer_address"] = list(connection.getpeername())
        connection.settimeout(banner_timeout)

        try:
            first = connection.recv(512)
        except socket.timeout:
            first = b""
            result["initial_recv"] = "timeout"
        except OSError as exc:
            first = b""
            result["initial_recv"] = f"error:{exc.errno}:{exc.strerror}"
        else:
            result["initial_recv"] = "data" if first else "eof"
        result["banner_before_client"] = decode_banner(first)

        second = b""
        if first.startswith(b"SSH-"):
            status = "ssh-banner"
        elif first:
            status = "connected-non-ssh-data"
        elif send_client_banner:
            try:
                connection.sendall(CLIENT_BANNER)
                result["client_banner_sent"] = True
                try:
                    second = connection.recv(512)
                except socket.timeout:
                    result["post_client_recv"] = "timeout"
                except OSError as exc:
                    result["post_client_recv"] = f"error:{exc.errno}:{exc.strerror}"
                else:
                    result["post_client_recv"] = "data" if second else "eof"
            except OSError as exc:
                result["client_banner_send_error"] = f"{exc.errno}:{exc.strerror}"
            result["banner_after_client"] = decode_banner(second)
            if second.startswith(b"SSH-"):
                status = "ssh-banner-after-client"
            elif second:
                status = "connected-non-ssh-data-after-client"
            else:
                status = "connected-no-banner"
        else:
            status = "connected-no-banner"
        result["status"] = status
    except ConnectionRefusedError as exc:
        result["status"] = "connection-refused"
        result["errno"] = exc.errno
        result["error"] = str(exc)
    except (TimeoutError, socket.timeout) as exc:
        result["status"] = "connect-timeout"
        result["errno"] = getattr(exc, "errno", None)
        result["error"] = str(exc)
    except OSError as exc:
        result["status"] = "connect-os-error"
        result["errno"] = exc.errno
        result["error"] = str(exc)
        if exc.errno == errno.ENETUNREACH:
            result["classification_hint"] = "no-route"
        elif exc.errno == errno.EHOSTUNREACH:
            result["classification_hint"] = "host-unreachable"
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
    result["total_elapsed_seconds"] = round(time.monotonic() - started, 6)
    return result


def classify(counts: Counter[str]) -> str:
    if counts["ssh-banner"] or counts["ssh-banner-after-client"]:
        return "ssh-listener-healthy"
    if counts["connected-no-banner"]:
        return "tcp-listener-accepts-but-no-ssh-banner"
    if counts["connection-refused"] and len(counts) == 1:
        return "port-closed-or-actively-rejected"
    if counts["connect-timeout"] and len(counts) == 1:
        return "tcp-syn-filtered-or-unanswered"
    if counts["connect-os-error"] and len(counts) == 1:
        return "host-network-error"
    return "mixed-tcp-behavior"


def capture_command(name: str, args: list[str], output: Path, timeout: float = 15.0) -> dict[str, object]:
    executable = shutil.which(args[0])
    if executable is None:
        output.write_text(f"command_missing={args[0]}\n", encoding="utf-8")
        return {"name": name, "available": False, "returncode": None}
    completed = run_host([executable, *args[1:]], timeout=timeout)
    output.write_text(
        completed.stdout + ("\n=== stderr ===\n" + completed.stderr if completed.stderr else ""),
        encoding="utf-8",
    )
    return {
        "name": name,
        "available": True,
        "returncode": completed.returncode,
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classify live TCP/SSH behavior of the booted Samsung A33 without phone writes"
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--connect-timeout", type=float, default=1.5)
    parser.add_argument("--banner-timeout", type=float, default=2.0)
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if not 1 <= args.attempts <= 300:
        raise SystemExit("--attempts must be between 1 and 300")
    if not 0 <= args.interval <= 60:
        raise SystemExit("--interval must be between 0 and 60 seconds")
    if not 0.05 <= args.connect_timeout <= 30:
        raise SystemExit("--connect-timeout must be between 0.05 and 30 seconds")
    if not 0.05 <= args.banner_timeout <= 30:
        raise SystemExit("--banner-timeout must be between 0.05 and 30 seconds")

    root = args.root.expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / "build/runtime-results" / f"a33-live-ssh-diagnosis-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    archive = out.with_suffix(".tar.gz")

    commands: list[dict[str, object]] = []
    commands.append(capture_command("ip-route", ["ip", "route", "get", args.host], out / "ip-route.txt"))
    commands.append(capture_command("ip-addresses", ["ip", "-br", "addr"], out / "ip-addresses.txt"))
    commands.append(capture_command("ip-neighbor", ["ip", "neigh", "show", args.host], out / "ip-neighbor.txt"))
    commands.append(capture_command("ping", ["ping", "-c", "3", "-W", "1", args.host], out / "ping.txt"))
    commands.append(capture_command("adb-devices", ["adb", "devices", "-l"], out / "adb-devices.txt"))
    commands.append(
        capture_command(
            "ssh-keyscan",
            ["ssh-keyscan", "-T", "5", "-p", str(args.port), args.host],
            out / "ssh-keyscan.txt",
            timeout=10,
        )
    )

    probes: list[dict[str, object]] = []
    jsonl = out / "tcp-probes.jsonl"
    with jsonl.open("w", encoding="utf-8") as stream:
        for attempt in range(1, args.attempts + 1):
            probe = probe_once(
                args.host,
                args.port,
                connect_timeout=args.connect_timeout,
                banner_timeout=args.banner_timeout,
            )
            probe["attempt"] = attempt
            probes.append(probe)
            stream.write(json.dumps(probe, sort_keys=True) + "\n")
            print(
                f"attempt={attempt} status={probe['status']} "
                f"elapsed={probe['total_elapsed_seconds']} "
                f"banner={probe.get('banner_before_client') or probe.get('banner_after_client') or ''}"
            )
            if attempt != args.attempts and args.interval:
                time.sleep(args.interval)

    counts = Counter(str(probe["status"]) for probe in probes)
    diagnosis = classify(counts)
    summary = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "diagnose-a33-live-ssh",
        "implementation_language": "python3",
        "host": args.host,
        "port": args.port,
        "attempts": args.attempts,
        "status_counts": dict(sorted(counts.items())),
        "diagnosis": diagnosis,
        "commands": commands,
        "tcp_probe_jsonl": str(jsonl),
        "phone_partition_writes": "no",
        "phone_reboot_performed": "no",
        "diagnostic_status": "passed",
    }
    summary_path = out / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)
    archive_sha = sha256_file(archive)
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{archive_sha}  {archive}\n", encoding="utf-8"
    )

    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"diagnostic_directory={out}")
    print(f"diagnostic_archive={archive}")
    print(f"diagnostic_archive_sha256={archive_sha}")
    print("phone_partition_writes=no")
    print("phone_reboot_performed=no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
