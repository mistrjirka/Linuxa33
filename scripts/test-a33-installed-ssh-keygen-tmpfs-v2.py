#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "test-a33-installed-ssh-keygen-tmpfs.py"
EXPECTED_BASE_BLOB = "7f4b5df59f4ce1c42a2235c037ae667e554c3087"

spec = importlib.util.spec_from_file_location("a33_ssh_keygen_tmpfs_v2_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load SSH tmpfs diagnostic: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

OLD_REQUIRED_BLOCK = r'''for required in \
    /bin/sh \
    /usr/bin/ssh-keygen \
    /usr/sbin/sshd \
    /etc/ssh/sshd_config \
    /etc/passwd \
    /etc/group; do
    if [ ! -e "$root$required" ]; then
        echo "missing_rootfs_path=$required"
        exit 22
    fi
done
'''

NEW_REQUIRED_BLOCK = r'''rootfs_required_path()
{
    relative="$1"
    full="$root$relative"

    if [ -e "$full" ]; then
        echo "rootfs_required_path=present path=$relative resolution=direct"
        return 0
    fi

    # An absolute symlink inside the mounted rootfs is resolved by the host
    # shell against TWRP's /, so test -e "$root/bin/sh" can be false even
    # though /bin/sh -> /bin/busybox is valid after chroot. Resolve exactly
    # one rootfs-relative target here and preserve the original chroot test.
    if [ ! -L "$full" ]; then
        return 1
    fi

    target="$(readlink "$full" 2>/dev/null || true)"
    [ -n "$target" ] || return 1
    case "$target" in
        /*)
            target_relative="$target"
            ;;
        *)
            parent="${relative%/*}"
            [ "$parent" != "$relative" ] || parent=/
            target_relative="$parent/$target"
            ;;
    esac

    target_full="$root$target_relative"
    if [ -e "$target_full" ] || [ -L "$target_full" ]; then
        echo "rootfs_required_path=present path=$relative resolution=symlink target=$target target_relative=$target_relative"
        return 0
    fi

    echo "rootfs_required_symlink_broken path=$relative target=$target target_relative=$target_relative"
    return 1
}

for required in \
    /bin/sh \
    /usr/bin/ssh-keygen \
    /usr/sbin/sshd \
    /etc/ssh/sshd_config \
    /etc/passwd \
    /etc/group; do
    if ! rootfs_required_path "$required"; then
        echo "missing_rootfs_path=$required"
        exit 22
    fi
done
'''

if base.REMOTE_SCRIPT.count(OLD_REQUIRED_BLOCK) != 1:
    raise SystemExit("SSH tmpfs diagnostic required-path block changed")
base.REMOTE_SCRIPT = base.REMOTE_SCRIPT.replace(
    OLD_REQUIRED_BLOCK,
    NEW_REQUIRED_BLOCK,
)


def main() -> int:
    repo = Path.home() / "Linuxa33"
    completed = base.common.run(
        ["git", "-C", str(repo), "hash-object", str(BASE)],
        check=False,
    )
    actual = completed.stdout.strip()
    if actual != EXPECTED_BASE_BLOB:
        raise base.DiagnosticError(
            "checked-in SSH tmpfs diagnostic changed: "
            f"actual={actual} expected={EXPECTED_BASE_BLOB}"
        )
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
