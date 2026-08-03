# A33 cache-boot plan correction: cache is not required

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`samsung-a33x`)  
**Status:** the earlier cache=`pmOS_boot` requirement was incorrect and is retired.

## What exposed the error

`prepare-a33-cache-boot-image.sh` correctly found that the generated
`pmOS_boot` filesystem has no `/initramfs-extra` file. Its actual boot-deploy
contents include `/initramfs` and `/samsung-a33x.dtb`.

This is expected for the current unified postmarketOS initramfs design because
the A33 device definition does not set:

```text
deviceinfo_create_initfs_extra="true"
```

That option defaults to false. The exact U0g recovery ramdisk embeds
`/init_2nd.sh` directly and can enter second-stage root discovery before the
optional initramfs-extra fallback.

## Correct first internal layout

```text
recovery partition -> exact proven U0g recovery candidate
userdata partition -> ext4 filesystem labeled pmOS_root
cache partition    -> unchanged Android cache
```

The first experiment remains one functional change: a valid `pmOS_root`
filesystem appears on `/dev/block/by-name/userdata`.

## Required proof

Run:

```bash
bash scripts/verify-a33-u0g-unified-root-handoff.sh
```

It verifies the exact U0g recovery and ramdisk hashes, extracts the real
ramdisk, and requires:

- embedded `/init_2nd.sh`;
- direct second-stage execution before optional `initramfs-extra` extraction;
- `wait_root_partition`;
- `mount_root_partition`;
- `pmOS_root` discovery;
- `switch_root`;
- no explicit `deviceinfo_create_initfs_extra=true`.

Only a report ending in `verification_status=passed` re-enables the gated
userdata deployment script.

## Safety state

The prior private userdata backup/preflight remains valid for the exact prepared
root image:

```text
deployment_sha256=79c94efe41da14e72e82cfc66c8e6fac6f04482fa2ea2af024f6b1ebb67d3951
userdata=/dev/block/sda36
userdata_bytes=114240258048
```

No phone partition was written during the failed cache preparation. Cache,
userdata, super, boot and recovery remain unchanged.

## Retired scripts

These scripts now refuse execution because their premise was wrong:

- `scripts/prepare-a33-cache-boot-image.sh`
- `scripts/complete-a33-internal-layout-preflight.sh`

The generic recovery ZIP remains prohibited. `system`, `super`, Android `boot`,
cache and the GPT are not modified in the first real-rootfs test.
