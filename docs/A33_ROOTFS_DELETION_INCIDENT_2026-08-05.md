# A33 userdata rootfs deletion incident — 2026-08-05

## Summary

The installed postmarketOS root filesystem on Samsung Galaxy A33 userdata was deleted by the experimental OpenRC SSH chroot diagnostic introduced in this repository.

The diagnostic was intended to mount userdata read-only and reproduce the exact `/etc/init.d/sshd start` path. OpenRC dependency startup remounted the chroot root filesystem read-write. The diagnostic's exit cleanup ignored unmount failures and then executed `rm -rf` on the chroot mountpoint unconditionally. Because the root filesystem remained mounted and busy, that command recursively deleted the mounted filesystem. The nested `/dev`, `/proc`, and `/run` mounts survived, which exactly matches the later filesystem layout.

This was a tooling defect, not a kernel, UFS, OpenSSH, or postmarketOS defect.

## Affected tool

Disabled permanently:

- `scripts/diagnose-a33-openrc-sshd-chroot.py`
- `scripts/diagnose-a33-openrc-sshd-chroot-v2.py`

The disabled entrypoints now refuse without running any phone-side payload.

## Evidence and causal sequence

### 1. Diagnostic began with a read-only mount

The diagnostic reported:

```text
readonly_root_mount=passed
```

### 2. OpenRC dependency startup remounted `/` read-write

The captured service output contains:

```text
* Checking local filesystems  ...pmOS_root: clean, 7174/97920 files, 148308/195840 blocks
* Remounting root filesystem read/write ... [ ok ]
* Remounting filesystems ... [ ok ]
* Mounting local filesystems ... [ ok ]
* Starting logbookd ... [ ok ]
* Starting sshd ... [ ok ]
```

This invalidated the diagnostic's `userdata_persistent_writes=no` claim.

### 3. The diagnostic cleanup ignored unmount failures

The original cleanup function used best-effort unmounts:

```sh
[ "$run_mounted" = no ] || umount "$root/run" 2>/dev/null || true
[ "$sys_mounted" = no ] || umount "$root/sys" 2>/dev/null || true
[ "$proc_mounted" = no ] || umount "$root/proc" 2>/dev/null || true
[ "$dev_mounted" = no ] || umount "$root/dev" 2>/dev/null || true
[ "$root_mounted" = no ] || umount "$root" 2>/dev/null || true
```

It then marked every mount as absent regardless of whether `umount` succeeded.

### 4. It recursively removed the mountpoint unconditionally

Immediately after the ignored unmount failures, the cleanup executed:

```sh
rm -rf "$root" "$work" 2>/dev/null || true
```

The diagnostic output showed the nested and root unmounts failed as busy. Therefore `rm -rf "$root"` operated on the still-mounted read-write userdata filesystem.

### 5. Follow-up cleanup proved the mount remained live and writable

At 2026-08-05 14:34 local time, the dedicated cleanup found:

```text
/dev/block/by-name/userdata /tmp/a33x-openrc-sshd-root ext4 rw,seclabel,noatime,norecovery 0 0
tmpfs /tmp/a33x-openrc-sshd-root/dev ...
proc /tmp/a33x-openrc-sshd-root/proc ...
tmpfs /tmp/a33x-openrc-sshd-root/run ...
```

It also found the chrooted process:

```text
/usr/bin/logbookd -p /run/logbookd.pid -r -g 8192 -d /var/log/logbookd.db
```

The cleanup terminated `logbookd`, unmounted the nested filesystems, remounted userdata read-only, and unmounted it successfully.

### 6. Read-only layout inspection confirmed deletion

The later layout inspection mounted the exact filesystem UUID and label and found only:

```text
dev/
proc/
run/
```

The following were absent:

```text
/bin
/sbin
/usr
/etc
```

No BusyBox binary existed anywhere in the mounted filesystem.

This survivor set is characteristic of recursive deletion while `/dev`, `/proc`, and `/run` were separate nested mounts.

## Impact

Deleted from the installed userdata rootfs:

- system binaries and libraries;
- `/etc` configuration;
- OpenRC services and runlevel links;
- provisioned SSH host keys;
- package database and userspace state;
- persistent logs and other rootfs files.

Unaffected:

- GPT;
- `boot`;
- `recovery`;
- `super`;
- `cache`;
- the host-side exact rootfs image and its manifests;
- the U0m recovery candidate.

The ext4 superblock, UUID, and label survived because the deletion removed directory contents rather than rewriting the filesystem image.

## Recovery source

Exact original rootfs image:

```text
/home/jirka/a33-port/build/userdata-rootfs-images/20260803-193947/a33x-userdata-pmos-root.img
```

Identity:

```text
size=802160640
sha256=79c94efe41da14e72e82cfc66c8e6fac6f04482fa2ea2af024f6b1ebb67d3951
uuid=7b056328-bdfb-496b-ac38-2624c43c863a
label=pmOS_root
```

Recovery tool:

```text
scripts/restore-a33-rootfs-after-unsafe-openrc-diagnostic.py
```

The tool requires the exact damage signature during preflight. Commit mode writes only the exact original image prefix to userdata, verifies the full written prefix SHA-256 on the phone, verifies ext4 identity, and validates the original critical file hashes read-only.

SSH host keys must be regenerated after restoration because the original deployment image intentionally did not contain them.

## Permanent safeguards

1. Never run a full OpenRC service inside a mounted persistent rootfs chroot when dependencies can start implicitly.
2. Never trust an initial read-only mount to remain read-only after starting an init system.
3. Never ignore an unmount failure before removing a mountpoint.
4. Never set an internal `mounted=no` flag unless the unmount succeeded or `/proc/mounts` proves the path is no longer mounted.
5. Never run `rm -rf` on or below a path that might still be a mountpoint.
6. Cleanup must identify and terminate processes rooted inside the chroot before unmounting.
7. Cleanup must fail closed if any nested or root mount remains.
8. Persistent write claims must be based on final observed mount state, not intended mount options.
9. Destructive or potentially destructive diagnostics must be split into preflight and explicit-confirmation commit modes.
10. Rootfs validation must compare against the exact deployment image and critical hashes after any persistent write experiment.
