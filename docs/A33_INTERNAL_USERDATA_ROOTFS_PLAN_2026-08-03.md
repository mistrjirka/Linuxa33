# A33 internal-storage rootfs plan: repurpose userdata, preserve super

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Decision:** use internal UFS by repurposing only Android `userdata` for the first real postmarketOS rootfs boot.

## User intent

The user does not want to depend on a removable microSD card and accepts removing Android if necessary.

## Chosen layout

The first internal installation will preserve the physical partition table and all Android read-only/dynamic partitions. Only the contents of the standalone `userdata` partition will be replaced:

```text
/dev/block/by-name/userdata -> /dev/block/sda36
size = 114240258048 bytes
new content = ext4 filesystem labeled pmOS_root
```

Preserve during the first test:

- GPT and all partition boundaries;
- `super` and every logical Android partition inside it;
- Android `boot`;
- `vbmeta`, `dtbo`, and other boot-chain partitions;
- exact known-good TWRP rescue image and Odin recovery path.

The initial Linux boot continues through the `recovery` partition. Android `boot` is not replaced until Linux reaches the real rootfs and SSH over USB is proven.

## Why userdata is the least risky internal target

The A33 audit proves that `userdata` is a separate physical GPT partition, not a dynamic partition inside `super`. It has more than enough capacity for the validated 802160640-byte ext4 root image. Replacing its filesystem destroys Android apps, accounts, settings, media, and encryption state, but it does not require modifying `super` metadata or repartitioning the whole UFS device.

The generic postmarketOS recovery ZIP remains prohibited because it resolves `system` incorrectly and contains destructive partition-table logic plus `FLASH_KERNEL=true`.

## Experiment isolation

The first real-rootfs experiment should reuse the exact proven U0g recovery image:

```text
recovery image SHA256:
e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81
```

No new kernel, module, Type-C, MUIC, DWC3, gadget, or initramfs behavior is needed. The U0g initramfs already:

1. discovers a filesystem labeled `pmOS_root`;
2. runs ext4 checking and `resize2fs`;
3. mounts it at `/sysroot`;
4. verifies `/etc/os-release`;
5. calls `switch_root /sysroot /sbin/init`.

Therefore the single functional variable is the appearance of a valid root filesystem on `userdata`.

## Root image adjustment

The generated root image was originally paired with a separate Linux boot filesystem. For recovery-based boot, its `/etc/fstab` must not attempt to mount that absent boot filesystem. Create a dedicated userdata deployment copy with:

```text
UUID=<root UUID> / ext4 defaults 0 1
```

and no `/boot` line.

Keep its label `pmOS_root` and UUID unchanged so U0g can locate it by label and the rootfs can identify itself by UUID.

## Safety sequence

Before any write:

1. verify exact known-good TWRP;
2. verify `userdata -> /dev/block/sda36` and exact size;
3. require `/data` unmounted and ensure no device-mapper target uses `sda36`;
4. create a private host-side backup of GPT edges, boot-chain partitions, metadata, and userdata header/footer samples;
5. prepare and hash the dedicated userdata root image;
6. produce a sanitized preflight report.

The backup is private and must not be uploaded because Android metadata may contain encryption material.

## Destructive deployment sequence

After preflight passes:

1. stream the dedicated root image directly into `/dev/block/by-name/userdata`;
2. verify the exact written byte range by SHA256 before filesystem growth;
3. verify ext4 label and UUID;
4. mount it read-only in TWRP and verify `/sbin/init`, `/etc/os-release`, OpenSSH, NetworkManager, and the corrected fstab;
5. flash the exact known U0g recovery image to `recovery`;
6. verify recovery partition SHA256;
7. reboot recovery.

On boot, the unchanged U0g initramfs should expand ext4 to the full userdata partition and switch to the normal postmarketOS rootfs.

## Success criteria

Host:

```text
USB device 04e8:6860 enumerates
CDC-NCM interface returns
172.16.42.1 responds
ssh jirka@172.16.42.1 succeeds
```

Phone rootfs:

```text
PID 1 is OpenRC init from the real rootfs
/ is /dev/block/sda36 or /dev/block/by-name/userdata
filesystem label is pmOS_root
filesystem has expanded beyond the original 802160640 bytes
sshd is running
NetworkManager is running
```

## Later stages

After USB SSH is proven:

1. collect full normal-rootfs boot evidence;
2. bring up Wi-Fi;
3. decide whether to put the Linux boot image in Android `boot` and restore TWRP permanently to `recovery`;
4. optionally reclaim `super` only after the phone is independently recoverable and Linux is stable;
5. bring up display, GPU, touchscreen, and a mobile/desktop environment incrementally.

Do not reclaim `super` during the first internal-rootfs test.
