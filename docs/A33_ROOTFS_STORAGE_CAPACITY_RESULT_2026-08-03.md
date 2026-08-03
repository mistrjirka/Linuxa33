# A33 rootfs storage-capacity result

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`samsung-a33x`)  
**Status:** current normal postmarketOS rootfs validated; no safe internal deployment target approved; removable storage required for the first real-rootfs boot.

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
userdata_early_boot_access=unproven

removable_device_count=0
```

No mount, format, block write, or persistent phone write occurred.

## Decision

The current rootfs cannot fit in the 600 MiB cache partition:

- its actual used data is already about 607 MiB;
- its verified ext4 minimum is about 802 MiB;
- a safe cache deployment would require additional working margin.

`userdata` is not approved because TWRP did not mount it and early Linux access through Android FBE is unproven.

The following remain prohibited:

- generic recovery ZIP installation to `system`;
- writes to `super` or a logical Android system device;
- writes to Android `boot` before rootfs handoff is proven through `recovery`;
- destructive repartitioning of `userdata`.

## Next milestone

Insert a removable microSD card of at least 4 GiB, preferably 8 GiB or larger, then rerun:

```bash
bash scripts/audit-a33-rootfs-storage-capacity-v3.sh
```

The audit must report a removable whole-device candidate and:

```text
decision=external-removable-rootfs-preferred
```

After exact removable-device identity, capacity, current partition table, and content-risk confirmation are captured, the next implementation is:

1. create an explicitly destructive, fail-closed microSD deployment tool;
2. back up the microSD partition table before modification;
3. write the validated `pmOS_root` image only to the confirmed removable target;
4. verify the resulting ext4 UUID, label, and sampled/full hash as applicable;
5. build U0h while preserving U0g exactly;
6. have U0h locate `LABEL=pmOS_root` or the exact UUID, mount it and `switch_root`;
7. boot U0h through the recovery partition;
8. validate `ping 172.16.42.1` and `ssh jirka@172.16.42.1`.

Wi-Fi and display work remain later stages after persistent rootfs boot and SSH over USB are proven.
