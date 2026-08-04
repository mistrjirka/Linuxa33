# A33 first-rootfs no-SSH root cause

**Date:** 2026-08-04  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Failed candidate:** exact U0g recovery, SHA256 `e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81`  
**Installed rootfs:** direct ext4 `pmOS_root` on Android `userdata` (`sda36`)

## Result

SSH did not fail inside OpenRC. The boot never reached OpenRC or `sshd`.

The U0g initramfs remained in `/init_2nd.sh`, failed to discover the installed
`pmOS_root` filesystem, entered its failure/log-disk path, and then restored the
initramfs USB gadget. That explains the observed combination:

```text
USB enumeration: yes
172.16.42.2/24 host interface: yes
172.16.42.1 ping: yes
TCP/22: no
```

The USB network belonged to the initramfs, not the normal rootfs.

## Evidence from the captured failed boot

The previous-boot archive contains these current-boot events:

1. The kernel enumerated the complete UFS partition table at about 1.58 seconds,
   including `sda26` and `sda36`.
2. PID 1 was `/init_2nd.sh` at about 1.39 seconds.
3. The U0g metadata observer persisted its report at uptime 3.60 seconds.
4. That report states:

   ```text
   metadata_device=/dev/block/sda26
   metadata_resolved=/dev/block/sda26
   metadata_node_created=yes
   ```

   Therefore the minimal initramfs had sysfs block information but did not
   automatically expose the metadata partition through a usable `/dev` node;
   the U0g observer had to create `sda26` itself.
5. No corresponding U0g code created `/dev/sda36`.
6. At about 45.99 seconds, the initramfs failed to mount `/dev/loop0` in its
   fallback/log path.
7. At about 46.19 seconds, PID 1 was still `/init_2nd.sh` and restarted the USB
   gadget.
8. There was no current-boot kernel panic and no evidence that `switch_root`,
   OpenRC, or `sshd` ran.

After exact TWRP restoration, TWRP could resolve and mount the rootfs as
`/dev/block/sda36`, confirming that the written filesystem itself remained
present and valid. The first collector's extra read-only mount failed only
because TWRP had already mounted the same filesystem at `/data` and `/sdcard`.
That collector issue is separate from the candidate boot failure.

## Why root discovery failed

The embedded postmarketOS second-stage flow discovers `pmOS_root` through
`blkid`. `blkid` requires a usable block-device node. U0g proved the physical
UFS partition existed in sysfs, but did not ensure that `sda36` existed in
`/dev` before root discovery.

The previous static handoff verifier established that the required functions
and tokens existed in the initramfs. It did not prove the runtime prerequisite:
a device node through which `blkid` could inspect the direct physical userdata
filesystem.

## U0h: isolated correction

U0h keeps the exact U0g functional base and adds one hook:

```text
hooks/05-a33x-userdata-root-node.sh
```

The hook:

1. waits for `/sys/class/block/sda36/dev` and `size`;
2. requires the already-proven exact size of `223125504` sectors;
3. creates only:
   - `/dev/sda36`;
   - `/dev/block/sda36`;
   - `/dev/block/by-name/userdata -> ../sda36`;
4. runs `blkid` and requires `TYPE="ext4"` and `LABEL="pmOS_root"`;
5. persists an exact result to metadata at
   `/a33x-bringup/u0h-root-node-result.txt`;
6. performs no write to userdata, recovery, boot, cache, super, or the GPT.

The experimental delta is therefore:

```text
kernel: unchanged
kernel command line: unchanged
module set: unchanged at 67
U0g MUIC helper/register sequence: unchanged
new behavior: create and verify the already-existing sda36 block node
```

## Fail-closed build and test chain

```text
prepare-u0h-userdata-root-node-initramfs.sh
make-u0h-userdata-root-node-recovery.sh
flash-a33-u0h-userdata-root-node.sh
boot-observe-a33-u0h-userdata-root-node.sh
```

The preparation script reconstructs the exact U0g base, embeds only hook 05,
checks all retained U0g hashes, requires the same 67 modules, verifies the
runtime root-handoff tools, and rejects destructive commands in the hook.

The recovery builder uses an isolated temporary root view so it does not alter
the known U0g export. The flash and observation wrappers validate the generated
manifest and temporarily adapt the already-reviewed generic scripts without
changing their tracked bytes.

The hook is experimental and is not yet a permanent dependency of the device
package. Promote it only after U0h proves a real `switch_root` and SSH service.
