#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "diagnose-a33-openrc-sshd-chroot.py"
EXPECTED_BASE_BLOB = "d104487fbe97c1429d6df222b39fbf5a7e18a21c"

spec = importlib.util.spec_from_file_location("a33_openrc_sshd_chroot_v2_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load OpenRC SSH chroot diagnostic: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

OLD_REQUIRED_BLOCK = r'''for required in \
    /sbin/openrc-run \
    /etc/init.d/sshd \
    /usr/libexec/rc/sh/rc-cgroup.sh \
    /usr/sbin/sshd.pam \
    /etc/ssh/sshd_config; do
    [ -e "$root$required" ] || {
        echo "missing_rootfs_path=$required"
        exit 22
    }
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
    [ -L "$full" ] || return 1
    target="$(readlink "$full" 2>/dev/null || true)"
    [ -n "$target" ] || return 1
    case "$target" in
        /*) target_relative="$target" ;;
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
    /sbin/openrc-run \
    /etc/init.d/sshd \
    /usr/libexec/rc/sh/rc-cgroup.sh \
    /usr/sbin/sshd.pam \
    /etc/ssh/sshd_config; do
    if ! rootfs_required_path "$required"; then
        echo "missing_rootfs_path=$required"
        exit 22
    fi
done
'''

if base.REMOTE_SCRIPT.count(OLD_REQUIRED_BLOCK) != 1:
    raise SystemExit("OpenRC SSH required-path block changed")
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
            "checked-in OpenRC SSH chroot diagnostic changed: "
            f"actual={actual} expected={EXPECTED_BASE_BLOB}"
        )
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
