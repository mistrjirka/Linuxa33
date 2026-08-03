# A33 internal cache-boot and userdata-root plan

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`samsung-a33x`)  
**Status:** userdata root image and private preflight passed; destructive deployment is still blocked pending cache boot preparation and expanded preflight.

## Critical correction

A userdata-only `pmOS_root` deployment is insufficient for the proven U0g initramfs.

The postmarketOS initramfs sequence mounts a filesystem labeled `pmOS_boot`, extracts `/boot/initramfs-extra`, and only then waits for and mounts `pmOS_root` before `switch_root`.

The first internal-storage layout must therefore be:

```text
recovery partition -> exact proven U0g kernel/initramfs boot image
cache partition    -> pmOS_boot filesystem
userdata partition -> pmOS_root filesystem
```

## Proven capacities

```text
cache    = /dev/block/sda33 = 629145600 bytes
pmOS_boot image             = 510656512 bytes

userdata = /dev/block/sda36 = 114240258048 bytes
pmOS_root image             = 802160640 bytes
```

Both images fit their intended targets.

## Existing successful evidence

The prepared userdata root image passed:

- ext4 label `pmOS_root`;
- UUID `7b056328-bdfb-496b-ac38-2624c43c863a`;
- `/sbin/init` present;
- OpenSSH present and enabled;
- NetworkManager present and enabled;
- U0g helper/hooks present;
- root-only `/etc/fstab` with no active `/boot` entry;
- read-only `e2fsck`.

The initial private preflight passed for userdata and preserved:

- UFS GPT prefix/suffix;
- boot/recovery and boot-chain partitions;
- metadata and selected persistent partitions;
- first/last userdata samples;
- complete hashes.

It was not a full Android user-data backup.

## Required next actions

1. Validate and copy the extracted `pmOS_boot` filesystem with `scripts/prepare-a33-cache-boot-image.sh`.
2. Run `scripts/complete-a33-internal-layout-preflight.sh`.
3. That preflight must back up the entire 600 MiB Android cache partition privately and prove that both cache and userdata are unmounted and unused by device mapper.
4. Only after the sanitized expanded preflight is reviewed may a new combined deployment script write:
   - `pmOS_boot` to cache first;
   - verify its full hash, label, and `initramfs-extra`;
   - `pmOS_root` to userdata second;
   - verify its full hash, label, UUID, fstab, and required files.
5. Flash and boot the exact U0g recovery candidate; do not change U0g for this first rootfs test.
6. Validate USB network and SSH.
7. Grow the ext4 root filesystem to the full userdata partition only after first userspace boot is proven.

## Prohibited path

`scripts/deploy-a33-rootfs-to-userdata.sh` now refuses execution. Writing userdata alone would leave U0g unable to find `pmOS_boot` and it would not reach the rootfs handoff.

The generic postmarketOS recovery ZIP remains prohibited. `system`, `super`, Android `boot`, and the GPT are not touched in this stage.
