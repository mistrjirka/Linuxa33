# A33 installer exact-resolver v1 result: safe but inconclusive

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Installer SHA256:** `cab389de885fd1a599989d596e0c630fcdc02f098c0a6db1835b17d556837014`  
**Audit archive:** `a33-installer-resolved-target-audit-20260803-173018.tar.gz`  
**Audit archive SHA256:** `8392889b9f11ef8ca30203d40857d144164b2dbd298cc46d9bd91f7985f4305d`

## Classification

The first exact-resolver audit was **safe and read-only, but inconclusive about the installer’s effective target**.

It performed no persistent phone write, no block-device write and no mount operation. The only phone-side change was copying files into TWRP’s volatile `/tmp`, followed by their removal.

## Why the reported target was unresolved

The bundled postmarketOS `findfs` binary did not execute:

```text
findfs_partlabel_output=.../chroot/bin/findfs: can't execute: Permission denied
```

ADB copied the extracted installer payload without usable executable permission. The v1 script then incorrectly summarized an empty resolved path as `/` because `readlink -f ""` returns the current directory.

Therefore these v1 fields are not a storage conclusion:

```text
selected_target=unknown
selected_resolved=/
selected_class=unresolved
```

## Evidence that TWRP already maps the dynamic partitions

The same read-only capture proves:

```text
/dev/block/mapper/system -> /dev/block/dm-0
/dev/block/mapper/odm -> /dev/block/dm-1
/dev/block/mapper/product -> /dev/block/dm-2
/dev/block/mapper/vendor -> /dev/block/dm-3
/dev/block/mapper/vendor_dlkm -> /dev/block/dm-4
```

For `dm-0`:

```text
dm_name=system
dm_bytes=6697103360
dm_slaves=sda30
```

`/dev/block/sda30` is the physical `super` partition. Thus the Android `system` logical partition is definitely represented by a device-mapper block device backed by `super` in TWRP.

## Installer contract that remains under review

The generated ZIP contains:

```text
INSTALL_PARTITION='system'
FLASH_KERNEL='true'
```

Its generic installer will:

1. resolve `PARTLABEL=system` or use its fstab fallback;
2. create an MBR partition table on the resolved installation device;
3. create a 256 MiB `pmOS_boot` nested partition and a remaining `pmOS_root` nested partition;
4. format both filesystems;
5. write the generated boot image to `/dev/block/by-name/boot`.

This behavior must not be run until the effective target is reproduced exactly and the normal postmarketOS initramfs is proven able to recreate/access the required dynamic `system` mapping after reboot.

## Audit v2 correction

Commit `d9d5bed874696d1479f0ea82eb2eb3f171e8906f` updates:

```text
scripts/audit-a33-installer-exact-resolved-target.sh
```

The corrected audit:

- changes permissions only inside volatile TWRP `/tmp`;
- invokes the bundled `findfs` through its own musl loader;
- records the exact resolver output;
- mirrors the installer’s exact fstab fallback;
- distinguishes an unresolved target, a non-block fallback path and a dynamic logical `system` device;
- fixes empty-path handling;
- remains read-only with respect to all phone block devices.

## Next step

Rerun the corrected exact-target audit in known-good TWRP. Do not sideload the installer ZIP.
