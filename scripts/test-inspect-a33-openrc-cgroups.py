#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "inspect-a33-openrc-cgroups.py"
spec = importlib.util.spec_from_file_location("a33_openrc_cgroup_inspector_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

entries = module.parse_debugfs_ls(
    "/2/040755/0/0/.//\n"
    "/2/040755/0/0/..//\n"
    "/12/040755/0/0/sh/1024/\n"
    "/13/100755/0/0/openrc-run.sh/128/\n"
    "/14/120777/0/0/link/10/\n"
)
assert [entry.name for entry in entries] == ["sh", "openrc-run.sh", "link"]
assert entries[0].is_directory
assert entries[1].is_regular
assert entries[2].is_symlink
assert stat.S_IMODE(entries[1].mode) == 0o755

regular = module.parse_debugfs_stat(
    PurePosixPath("/usr/lib/os-release"),
    "Inode: 10   Type: regular    Mode:  0644   Flags: 0x0\n"
    "User: 0   Group: 0   Project: 0   Size: 42\n",
)
assert regular.is_regular and regular.size == 42
symlink = module.parse_debugfs_stat(
    PurePosixPath("/etc/os-release"),
    "Inode: 11   Type: symlink    Mode:  0777   Flags: 0x0\n"
    "User: 0   Group: 0   Project: 0   Size: 21\n"
    'Fast link dest: "../usr/lib/os-release"\n',
)
assert symlink.is_symlink
assert symlink.symlink_target == "../usr/lib/os-release"
assert module.resolve_symlink_path(
    PurePosixPath("/etc/os-release"), symlink.symlink_target
) == PurePosixPath("/usr/lib/os-release")

installed = (
    "C:Q1fixture\nP:busybox\nV:1.37.0-r0\n\n"
    "C:Q1openrc\nP:openrc\nV:0.62.2-r0\n\n"
)
assert module.parse_apk_installed(installed, "openrc") == ["0.62.2-r0"]
assert module.parse_apk_installed(installed, "missing") == []

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    fake_bin = root / "bin"
    fake_bin.mkdir()
    log = root / "debugfs-argv.log"
    fake = fake_bin / "debugfs"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "log = pathlib.Path(os.environ['FAKE_DEBUGFS_LOG'])\n"
        "with log.open('a', encoding='utf-8') as stream:\n"
        "    stream.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:] == ['-V']:\n"
        "    print('debugfs 1.47.4', file=sys.stderr)\n"
        "    raise SystemExit(0)\n"
        "request = sys.argv[sys.argv.index('-R') + 1]\n"
        "print('debugfs 1.47.4', file=sys.stderr)\n"
        "listings = {\n"
        " '/lib/rc': '/2/040755/0/0/.//\\n/2/040755/0/0/..//\\n/12/040755/0/0/sh/1024/\\n/13/100755/0/0/nonmatch.sh/18/\\n',\n"
        " '/lib/rc/sh': '/12/040755/0/0/.//\\n/2/040755/0/0/..//\\n/14/100755/0/0/openrc-run.sh/64/\\n/15/100755/0/0/rc-cgroup.sh/64/\\n',\n"
        " '/etc/init.d': '/20/040755/0/0/.//\\n/2/040755/0/0/..//\\n/21/100755/0/0/cgroups/32/\\n',\n"
        " '/etc/conf.d': '/30/040755/0/0/.//\\n/2/040755/0/0/..//\\n/31/100644/0/0/example/32/\\n',\n"
        "}\n"
        "files = {\n"
        " '/usr/lib/os-release': b'PRETTY_NAME=\\\"postmarketOS fixture\\\"\\n',\n"
        " '/etc/rc.conf': b'rc_cgroup_mode=\\\"hybrid\\\"\\n',\n"
        " '/lib/apk/db/installed': b'P:busybox\\nV:1.0\\n\\nP:openrc\\nV:0.62.2-r0\\n\\n',\n"
        " '/lib/rc/sh/openrc-run.sh': b'cgroup_add_service \\\"$RC_SVCNAME\\\"\\n',\n"
        " '/lib/rc/sh/rc-cgroup.sh': b'echo $$ > \\\"$path/cgroup.procs\\\"\\n',\n"
        " '/lib/rc/nonmatch.sh': b'echo harmless\\n',\n"
        " '/etc/init.d/cgroups': b'description=\\\"cgroups\\\"\\n',\n"
        " '/etc/conf.d/example': b'rc_cgroup_cleanup=yes\\n',\n"
        "}\n"
        "symlinks = {'/etc/os-release': '../usr/lib/os-release'}\n"
        "if request == 'stats':\n"
        "    sys.stdout.write('Filesystem volume name: pmOS_root\\n')\n"
        "elif request.startswith('ls -p '):\n"
        "    path = request[6:]\n"
        "    if path in listings:\n"
        "        sys.stdout.write(listings[path])\n"
        "    else:\n"
        "        print('File not found by ext2_lookup', file=sys.stderr)\n"
        "elif request.startswith('stat '):\n"
        "    path = request[5:]\n"
        "    if path in symlinks:\n"
        "        target = symlinks[path]\n"
        "        sys.stdout.write(f'Inode: 11 Type: symlink Mode: 0777 Flags: 0x0\\nUser: 0 Group: 0 Project: 0 Size: {len(target)}\\nFast link dest: \\\"{target}\\\"\\n')\n"
        "    elif path in files:\n"
        "        sys.stdout.write(f'Inode: 12 Type: regular Mode: 0644 Flags: 0x0\\nUser: 0 Group: 0 Project: 0 Size: {len(files[path])}\\n')\n"
        "    else:\n"
        "        print('File not found by ext2_lookup', file=sys.stderr)\n"
        "elif request.startswith('cat '):\n"
        "    path = request[4:]\n"
        "    if path in symlinks:\n"
        "        print('cat: Attempt to read block from filesystem resulted in short read while reading ext2 file', file=sys.stderr)\n"
        "    elif path in files:\n"
        "        sys.stdout.buffer.write(files[path])\n"
        "    else:\n"
        "        print('File not found by ext2_lookup', file=sys.stderr)\n"
        "else:\n"
        "    print('unsupported fixture request: ' + request, file=sys.stderr)\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    image = root / module.DEFAULT_IMAGE_RELATIVE
    image.parent.mkdir(parents=True)
    image.write_bytes(b"read-only-ext4-fixture")
    original_hash = module.sha_file(image)
    output = root / "build/inspection"

    previous_log = os.environ.get("FAKE_DEBUGFS_LOG")
    os.environ["FAKE_DEBUGFS_LOG"] = str(log)
    try:
        result = module.inspect(root=root, image=image, output_dir=output, debugfs=fake)
    finally:
        if previous_log is None:
            os.environ.pop("FAKE_DEBUGFS_LOG", None)
        else:
            os.environ["FAKE_DEBUGFS_LOG"] = previous_log

    assert module.sha_file(image) == original_hash
    summary = result.report.read_text(encoding="utf-8")
    assert "image_unchanged=yes" in summary
    assert "debugfs_open_mode=read-only-no-w-flag" in summary
    assert "sudo_used=no" in summary
    assert "image_mounts=no" in summary
    assert "openrc_package_version=0.62.2-r0" in summary
    assert "symlink_resolution=/etc/os-release->/usr/lib/os-release" in summary
    assert "rc_conf_cgroup_match_count=1" in summary
    assert "cgroup_callsite_count=3" in summary
    assert "/lib/rc/sh/openrc-run.sh:1:cgroup_add_service" in summary
    assert "/lib/rc/sh/rc-cgroup.sh:1:echo $$" in summary
    assert "/etc/conf.d/example:1:rc_cgroup_cleanup=yes" in summary
    assert result.stable_report.is_file()
    assert result.archive.is_file()
    assert (
        result.output_dir / "extracted/etc/os-release"
    ).read_bytes() == b'PRETTY_NAME="postmarketOS fixture"\n'
    with tarfile.open(result.archive, "r:gz") as archive:
        names = archive.getnames()
    assert any(name.endswith("summary.txt") for name in names)
    assert any(name.endswith("extracted/etc/rc.conf") for name in names)

    commands = log.read_text(encoding="utf-8").splitlines()
    assert commands
    assert all("-w" not in command.split() for command in commands)
    assert all("mount" not in command.split() for command in commands)
    assert all("sudo" not in command.split() for command in commands)
    assert not any("cat /etc/os-release" in command for command in commands)
    assert any("cat /usr/lib/os-release" in command for command in commands)

try:
    module.parse_debugfs_ls("not-parseable")
except module.InspectionError:
    pass
else:
    raise AssertionError("malformed debugfs listing was accepted")

try:
    module.parse_debugfs_stat(PurePosixPath("/bad"), "not-parseable")
except module.InspectionError:
    pass
else:
    raise AssertionError("malformed debugfs stat was accepted")

print("a33_openrc_cgroup_inspector_self_test=passed")
print("debugfs_ls_parser=passed")
print("debugfs_stat_parser=passed")
print("fast_symlink_resolution=passed")
print("apk_openrc_version_parser=passed")
print("read_only_no_mount_contract=passed")
print("image_hash_invariance=passed")
print("cgroup_callsite_collection=passed")
print("evidence_archive_creation=passed")
print("malformed_listing_refusal=passed")
