# A33 rootfs storage target decision after exact installer audit

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Current phone mode:** exact known-good TWRP  
**Normal-rootfs installer SHA256:** `cab389de885fd1a599989d596e0c630fcdc02f098c0a6db1835b17d556837014`

## Conclusion

The stock postmarketOS Android recovery ZIP is **not approved and must not be sideloaded** on this A33.

Its options are:

```text
INSTALL_PARTITION='system'
FLASH_KERNEL='true'
```

The corrected v2 audit reproduced the installer resolution logic exactly:

```text
findfs PARTLABEL=system -> unable to resolve
fstab fallback source -> system
readlink -fn system -> /system
selected target -> /system
selected class -> non-block-path
```

The installer would therefore not obtain a valid installation block device. Its later static operations are destructive by design: create an MBR partition table, create boot/root subpartitions, format them, and write the generated boot image to Android `boot` when `FLASH_KERNEL=true`.

This ZIP must not be patched merely by substituting `/dev/block/mapper/system` or `/dev/block/dm-0`. TWRP proves that:

```text
/dev/block/mapper/system -> /dev/block/dm-0
dm name: system
dm size: 6,697,103,360 bytes
dm slave: sda30
/dev/block/by-name/super -> /dev/block/sda30
```

`system` is a logical partition inside `super`. Running the generic partition-table creation against either the logical mapping or `super` would be unsafe and would destroy Android dynamic-partition metadata/content.

## Proven storage topology relevant to Linux

```text
recovery: 100,663,296 bytes, standalone /dev/block/sda16
boot:      67,108,864 bytes, standalone /dev/block/sda14
cache:    629,145,600 bytes, standalone ext4 /dev/block/sda33
userdata: 114,240,258,048 bytes, F2FS /dev/block/sda36
super:     11,114,905,600 bytes, dynamic partition container /dev/block/sda30
```

The proven U0g Linux image can continue to boot from `recovery` while storage is brought up. Do not overwrite Android `boot` until a normal rootfs boot is proven and a deliberate production boot strategy is selected.

## Candidate rootfs strategies

### 1. Removable microSD — preferred first target

A removable microSD is the safest initial normal-rootfs target because it:

- avoids `super`, `system`, Android `boot`, and encrypted `userdata`;
- can provide multiple gigabytes of writable space;
- can be removed for repair or offline filesystem inspection;
- allows the U0g-compatible recovery image to remain the boot entry during bring-up.

It should be selected only after exact device/removable/size verification. Never assume a fixed `/dev/mmcblk*` name.

### 2. Cache partition — possible only if measured rootfs fits

`cache` is standalone and currently ext4, but it is only 600 MiB. Earlier package totals were already near this size. It is not approved unless the generated root filesystem's measured minimum ext4 size plus at least 128 MiB operating margin fits.

Even when it fits mathematically, using cache would be a constrained bring-up target, not a good long-term desktop rootfs.

### 3. Rootfs image file on userdata — not yet early-boot safe

`userdata` has ample capacity, but Android file-based encryption is the central problem. TWRP may decrypt and mount `/data`, but that does not prove the postmarketOS initramfs can obtain Android FBE keys early enough to read a rootfs image file.

Do not place the only bootable rootfs inside `/data` until early-boot FBE access is demonstrated independently.

### 4. Repartitioning userdata — rejected for initial bring-up

Shrinking/repartitioning userdata would be destructive, would risk user data, and adds unnecessary complexity before removable/cache options are exhausted.

### 5. Dynamic system/super — rejected

Do not use the stock installer on `/system`, `/dev/block/dm-0`, `/dev/block/mapper/system`, or `super`.

## Next read-only audit

Run:

```text
scripts/audit-a33-safe-rootfs-storage-options.sh
```

It compares:

- generated root image apparent, used, and minimum ext4 size;
- cache total/free capacity and required safety margin;
- userdata mount/encryption state without listing user files;
- removable block devices and capacity;
- exact known-good TWRP identity.

The audit performs no block writes, formatting, or mounts and does not inspect user directory contents, Wi-Fi credentials, or SSH keys.

## Decision after the audit

1. If a removable target of adequate size is present, build an A33-specific microSD rootfs installer/copy path and U0h recovery image that mounts it by filesystem UUID or label.
2. If no removable target exists but cache fits with margin, evaluate a minimal cache-rootfs experiment separately and document the limited headroom.
3. If neither is available, design an explicit FBE-capable userdata rootfs-image path or obtain removable storage. Do not fall back to `system/super`.

## Normal-rootfs target after storage is chosen

The first normal-rootfs boot must retain the proven U0g hardware path unchanged:

```text
patched usb_typec_manager SHA256:
de92f9dc0d29d671bd20f42ad01688e0584eb8e43f6826ff2643e0767c814641

original pdic_notifier_module SHA256:
5442a4cf5d4f12f394e5c3d4f5f01785929427fa71101731ea16bd00d0840161

physical MUIC controller:
13860000.hsi2c

runtime bus policy:
discover dynamically

verified MUIC final state:
CTRL1 0x6d = 0x17
manual switch 0x70 = 0x24
```

The first normal-rootfs success criterion remains:

```text
CDC-NCM enumerates
172.16.42.1 responds
sshd starts
ssh jirka@172.16.42.1 succeeds
```

Wi-Fi and display work follow only after this management path is reliable.
