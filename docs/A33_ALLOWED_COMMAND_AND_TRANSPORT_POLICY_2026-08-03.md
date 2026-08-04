# A33 allowed command and transport policy

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Scope:** every command that may run from final preflight through userdata deployment, U0g recovery flashing, first boot, SSH collection, and TWRP/Odin rescue.

## Why this audit was added

The first destructive attempt stopped before any write because the deployment
script inferred support for `adb exec-in` by searching `adb help`. The installed
client did not advertise that internal command in its help output.

AOSP source contains implementations of both `exec-in` and `exec-out`, but that
is not a sufficient compatibility contract for a packaged platform-tools build.
Command help text is also not a reliable feature probe. The chain therefore no
longer uses `exec-in` and does not infer capabilities from help output.

## Allowed ADB subcommands

The executable future scripts are statically scanned. Only these ADB
subcommands are permitted:

```text
shell
push
exec-out
reboot
```

Policy:

- `shell`: tested against exact known-good TWRP.
- `push`: tested first with a deterministic binary payload and then with the
  complete 802160640-byte rootfs image.
- `exec-out`: tested by reading the deterministic payload and then the complete
  staged image back to the host and comparing SHA256.
- `reboot recovery`: documented ADB interface and already proven repeatedly on
  this device; intentionally deferred until the explicit boot-observation step
  because testing it would reboot TWRP and discard the volatile staged image.
- `exec-in`: prohibited and absent from executable invocations.
- `adb help` feature detection: prohibited.

## Deployment transport

The approved host-to-phone transport is:

```text
adb push validated-root.img /tmp/a33x-userdata-pmos-root.img
phone-side stat + sha256sum
adb exec-out full staged-image readback + host sha256sum
TWRP dd from verified /tmp file to userdata
adb exec-out full written-prefix readback + host sha256sum
```

`/tmp` is a volatile TWRP tmpfs. Before the push, the script proves that it has
the complete image size plus a 256 MiB margin. No persistent phone partition is
written during staging or transport validation.

## Required host commands

The capability audit requires and records the resolved path for:

```text
bash adb sha256sum stat awk grep find sort date mkdir tee readlink realpath
tar ip ping timeout python3 ssh lsusb sudo mktemp cp rm cat seq sleep git
debugfs sed tr
```

Optional commands remain optional and guarded:

```text
journalctl shellcheck pv
```

The audit also tests:

- Python socket support;
- Python socket creation and TCP/22 connection probing;
- OpenSSH `StrictHostKeyChecking=accept-new` parsing.

## Required TWRP commands

The exact known-good TWRP must expose:

```text
sh awk grep sha256sum stat df rm readlink blockdev tail cat find dd sync mkdir umount mount wc ls uname dmesg getprop tr sed cp
```

The audit does more than `command -v`. It functionally tests the exact forms
used later, including:

TWRP on this device has no standalone, Toybox, or BusyBox `blkid`; the chain must not call it.

- `stat -c`;
- `df -k`;
- `find -printf`;
- `dd` with explicit block sizes;
- `blockdev --getsize64` and `--getro`;
- binary-clean 2048-byte ext superblock reads parsed by host Python for type, label, and UUID;
- read-only ext4 mount options and unmount;
- SHA256, text-processing, copying, sync, properties, dmesg and path
  canonicalization.

All writes made by this probe are confined to `/tmp`; metadata is mounted
read-only with `noload` when it is not already mounted.

## Required postmarketOS runtime commands

Before userdata is erased, the rootfs image is inspected offline and must
contain the required commands for boot and live validation:

```text
/bin/sh
/sbin/init
/sbin/rc-service
/usr/sbin/sshd
/usr/bin/nmcli
ip (accepted in a validated standard path)
awk (accepted in a validated standard path)
```

Commands used only for supplemental evidence are guarded by fallbacks or
`|| true` and cannot determine boot success by themselves.

## Audit chain

The non-destructive authorization sequence is now:

```text
audit-a33-command-capabilities.sh
  -> tests host, ADB, TWRP and rootfs command support

audit-a33-first-rootfs-chain-final.sh
  -> reruns image, backup, target and rescue checks

stage-a33-userdata-rootfs-in-twrp.sh
  -> pushes and fully reads back the complete rootfs in volatile /tmp

audit-a33-first-rootfs-transport-final.sh
  -> binds capability, chain and full-image transport results

audit-a33-first-rootfs-transport-bound-final.sh
  -> binds every report, script, rootfs image, manifest, private backup and
     rescue asset by SHA256
```

Only `execute-a33-first-rootfs-deployment.sh` is the approved destructive
entrypoint. It refuses if any audited script, report, image, backup or rescue
asset changes.

## Persistent-write policy

Before the explicit erase entrypoint, allowed phone effects are limited to:

```text
ADB reads
read-only block-device inspection
read-only metadata mount
volatile /tmp file creation and deletion
```

The first persistent write remains exactly:

```text
/dev/block/by-name/userdata
```

Later, and only after that write is fully verified, the recovery flasher writes
exactly:

```text
/dev/block/by-name/recovery
```

The chain does not write cache, super, Android boot, vendor_boot, vbmeta, dtbo,
or the GPT.