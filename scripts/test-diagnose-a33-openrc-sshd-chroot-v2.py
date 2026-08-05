#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
for name in (
    "diagnose-a33-openrc-sshd-chroot.py",
    "diagnose-a33-openrc-sshd-chroot-v2.py",
):
    path = HERE / name
    completed = subprocess.run(
        [sys.executable, str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 1
    assert "DISABLED" in completed.stderr
    assert "cleanup-a33-openrc-sshd-chroot.py" in completed.stderr

base_source = (HERE / "diagnose-a33-openrc-sshd-chroot.py").read_text(encoding="utf-8")
v2_source = (HERE / "diagnose-a33-openrc-sshd-chroot-v2.py").read_text(encoding="utf-8")
for source in (base_source, v2_source):
    assert "remount" in source
    assert "writable" in source
    assert "cleanup-a33-openrc-sshd-chroot.py" in source
    assert "REMOTE_SCRIPT" not in source
    assert "adb reboot" not in source

print("a33_openrc_sshd_chroot_diagnostic_disabled=passed")
print("unsafe_rw_dependency_startup_disclosed=passed")
print("cleanup_tool_redirect_present=passed")
print("unsafe_remote_script_removed=passed")
