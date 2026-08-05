#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import socket
import sys
import threading

HERE = Path(__file__).resolve().parent
MODULE = HERE / "diagnose-a33-live-ssh.py"
spec = importlib.util.spec_from_file_location("a33_live_ssh_diagnosis_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def listener(payload: bytes | None, wait_for_client: bool = False) -> tuple[int, threading.Thread]:
    ready = threading.Event()
    state: dict[str, object] = {}

    def run() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            state["port"] = server.getsockname()[1]
            ready.set()
            connection, _ = server.accept()
            with connection:
                if wait_for_client:
                    connection.settimeout(2)
                    try:
                        connection.recv(512)
                    except OSError:
                        pass
                if payload is not None:
                    connection.sendall(payload)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(2)
    return int(state["port"]), thread


port, thread = listener(b"SSH-2.0-test-server\r\n")
result = module.probe_once(
    "127.0.0.1",
    port,
    connect_timeout=1,
    banner_timeout=1,
)
thread.join(2)
assert result["status"] == "ssh-banner"
assert str(result["banner_before_client"]).startswith("SSH-")

port, thread = listener(b"SSH-2.0-after-client\r\n", wait_for_client=True)
result = module.probe_once(
    "127.0.0.1",
    port,
    connect_timeout=1,
    banner_timeout=0.1,
)
thread.join(2)
assert result["status"] == "ssh-banner-after-client"
assert result["client_banner_sent"] is True

port, thread = listener(None)
result = module.probe_once(
    "127.0.0.1",
    port,
    connect_timeout=1,
    banner_timeout=0.1,
)
thread.join(2)
assert result["status"] == "connected-no-banner"

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as temporary:
    temporary.bind(("127.0.0.1", 0))
    closed_port = temporary.getsockname()[1]
result = module.probe_once(
    "127.0.0.1",
    closed_port,
    connect_timeout=1,
    banner_timeout=0.1,
)
assert result["status"] == "connection-refused"

from collections import Counter
assert module.classify(Counter({"ssh-banner": 1})) == "ssh-listener-healthy"
assert module.classify(Counter({"connected-no-banner": 2})) == (
    "tcp-listener-accepts-but-no-ssh-banner"
)
assert module.classify(Counter({"connection-refused": 3})) == (
    "port-closed-or-actively-rejected"
)
assert module.classify(Counter({"connect-timeout": 3})) == (
    "tcp-syn-filtered-or-unanswered"
)

source = MODULE.read_text(encoding="utf-8")
for forbidden in (
    "adb reboot",
    "dd if=",
    "mkfs",
    "wipefs",
    "fastboot",
    "odin4",
):
    assert forbidden not in source
assert '"phone_partition_writes": "no"' in source
assert '"phone_reboot_performed": "no"' in source

print("a33_live_ssh_diagnosis_self_test=passed")
print("immediate_ssh_banner_classification=passed")
print("banner_after_client_classification=passed")
print("connected_no_banner_classification=passed")
print("connection_refused_classification=passed")
print("diagnosis_summary_contract=passed")
print("phone_write_and_reboot_absence=passed")
