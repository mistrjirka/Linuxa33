# A33 rootfs storage-capacity result

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`samsung-a33x`)  
**Status:** normal postmarketOS rootfs validated; cache rejected; active plan changed to internal `userdata` repurposing after explicit user approval to remove Android data.

## Proven rootfs image

The finalized standalone root filesystem is an ext4 image with:

```text
path=/home/jirka/a33-port/build/rootfs-images/20260803-181702/samsung-a33x-root.img
sha256=fe1aa1b4375cd5fc6ad50c3abcf179d72e72c49c74807fa9559a81d92ff4a526
uuid=7b056328-bdfb-496b-ac38-2624c43c863a
label=pmOS_root
apparent_bytes=802160640
used_bytes=607371264
minimum_bytes=802160640
```

The image contains `/sbin/init`, OpenSSH, enabled `sshd`, NetworkManager, enabled `networkmanager`, and the confirmed U0g MUIC/USB hooks.

## Phone storage result

The read-only v3 audit confirmed:

```text
cache=/dev/block/sda33
cache_bytes=629145600
cache_required_bytes=936378368
cache_result=too-small

userdata=/dev/block/sda36
userdata_bytes=114240258048
userdata_mounted_in_twrp=no

removable_device_count=0
```

No mount, format, block write, or persistent phone write occurred during that audit.

## Updated decision

The current rootfs cannot fit in the 600 MiB cache partition. The user explicitly does not want an SD-card dependency and accepts removing Android data.

The active internal-storage plan is therefore:

```text
preserve GPT and partition boundaries
preserve super and all logical Android partitions for now
preserve Android boot for the first test
repurpose only /dev/block/by-name/userdata as ext4 pmOS_root
boot the exact proven U0g recovery image
allow the unchanged pmOS initramfs to find, resize, mount, and switch_root
```

This destroys Android applications, accounts, settings, media, and encryption state stored in userdata. It does not yet reclaim `super` and does not replace Android `boot`.

The generic recovery ZIP remains prohibited because its `system` target resolution and partitioning logic are unsafe for this device's dynamic-partition layout.

## Current implementation

See:

```text
docs/A33_INTERNAL_USERDATA_ROOTFS_PLAN_2026-08-03.md
scripts/prepare-a33-userdata-rootfs-image.sh
scripts/backup-a33-before-userdata-repurpose.sh
scripts/deploy-a33-rootfs-to-userdata.sh
```

The first two scripts are non-destructive. The deployment script requires an exact destructive confirmation token and refuses unless the private backup preflight passed for the exact image and current userdata mapping.

## Next milestone

1. prepare the userdata-specific root image with the separate `/boot` mount removed;
2. create a private host-side rescue bundle and sanitized preflight archive;
3. write the validated root image to userdata;
4. verify complete readback hash and rootfs contents;
5. flash and boot the exact U0g recovery candidate;
6. validate `ping 172.16.42.1` and `ssh jirka@172.16.42.1`.

Wi-Fi and display work remain later stages after persistent rootfs boot and SSH over USB are proven.
