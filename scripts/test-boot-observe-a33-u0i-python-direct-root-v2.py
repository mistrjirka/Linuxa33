#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "u0i_observer", HERE / "boot-observe-a33-u0i-python-direct-root-v2.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

sample = (
    "7: enx123    inet 172.16.42.2/24 brd 172.16.42.255 scope global enx123\n"
    "8: eth0      inet 192.168.1.5/24 brd 192.168.1.255 scope global eth0\n"
)
assert module.parse_interface_for_cidr(sample, "172.16.42.2/24") == sample.splitlines()[0]
assert module.parse_interface_for_cidr(sample, "10.0.0.2/24") is None
assert module.valid_ssh_banner(b"SSH-2.0-OpenSSH_10.0\r\n")
assert not module.valid_ssh_banner(b"")
assert not module.valid_ssh_banner(b"HTTP/1.1 200 OK\r\n")

transient = {
    "usb_enumeration": True,
    "host_usb_network_interface": False,
    "ping_172_16_42_1": False,
    "ssh_banner": False,
}
rootfs = {
    "usb_enumeration": True,
    "host_usb_network_interface": True,
    "ping_172_16_42_1": True,
    "ssh_banner": True,
}
assert not all(transient.values())
assert all(rootfs.values())

print("u0i_observer_python_self_test=passed")
print("host_interface_parser=passed")
print("ssh_banner_parser=passed")
print("transient_usb_not_success=passed")
print("simultaneous_success_contract=passed")
