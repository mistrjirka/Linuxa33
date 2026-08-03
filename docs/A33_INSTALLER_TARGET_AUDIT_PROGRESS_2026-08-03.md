# A33 installer target audit progress

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Phone mode during audit:** exact known-good TWRP  
**Installer SHA256:** `cab389de885fd1a599989d596e0c630fcdc02f098c0a6db1835b17d556837014`

## What completed successfully

The normal-rootfs Android recovery installer was regenerated and passed the host-side validation:

- exact U0g initramfs SHA256 retained: `13ba030dc9593849622bfe85b318393c1f3397d0a95feebf7c734d97cf37732d`;
- 67 guarded initramfs modules;
- exact patched Type-C manager, original PDIC and `i2c_dev` payloads;
- dynamic physical-controller discovery for `13860000.hsi2c`;
- exact U0g helper and hooks;
- OpenSSH installed;
- `sshd` enabled;
- NetworkManager and Wi-Fi management packages installed;
- NetworkManager enabled;
- no phone partition write during preparation.

The installer-target audit archive was also collected successfully and was read-only on persistent phone storage. It queried TWRP over ADB and did not execute the recovery installer.

## Important audit finding

The generated ZIP contains:

```text
INSTALL_PARTITION='system'
FLASH_KERNEL='true'
ISOREC='false'
```

Its bundled installer resolves `INSTALL_PARTITION`, then executes this destructive partitioning contract on the selected device:

```text
parted -s "$INSTALL_DEVICE" mktable msdos
parted -s "$INSTALL_DEVICE" mkpart primary ext2 2048s 256M
parted -s "$INSTALL_DEVICE" mkpart primary 256M 100%
parted -s "$INSTALL_DEVICE" set 1 boot on
```

It then formats both resulting partitions and, because `FLASH_KERNEL=true`, writes its `boot.img` to the resolved Android boot partition.

The A33 uses Android dynamic partitions:

- physical `super` is `/dev/block/sda30`, 11,114,905,600 bytes;
- `system`, `vendor`, `product`, `odm`, and related partitions are logical/device-mapper volumes inside `super`;
- `/dev/block/by-name/system` does not exist as a standalone physical partition;
- TWRP currently exposes several `dm-*` devices.

Therefore the generic recovery ZIP is **not approved for flashing yet**. We must execute only its bundled `findfs PARTLABEL=system` resolver in a read-only audit and map the returned block device to its `dm-*` name and `super` backing. If it resolves to the logical `system` volume, running `parted mktable msdos` on it is not accepted without a separately designed dynamic-partition installation strategy.

## Audit parser issue

The first summary printed an empty `twrp_recovery_sha256` due to malformed quoting in the summary-only `awk` command. The raw captured topology contains the correct hash:

```text
414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e
```

This did not affect collection or phone safety.

## Phone safety result

The completed preparation and first target audit performed:

- no `dd` to a phone block device;
- no filesystem formatting;
- no `parted` against the phone;
- no recovery ZIP sideload;
- no persistent phone-storage write;
- no writable mount of a phone partition.

The next resolver copies only the installer resolver payload to TWRP `/tmp`, executes the bundled `findfs` read-only, inventories `/sys/block/dm-*`, then removes the temporary files.

## Exact next step

Run:

```bash
bash scripts/audit-a33-installer-exact-resolved-target.sh
```

Do not sideload `pmos-samsung-a33x.zip` before that audit is reviewed.

## Likely direction after exact resolution

If `system` resolves to a logical device inside `super`, do not use the generic recovery installer. Design a controlled A33-specific installation method. Candidate strategies to evaluate include:

1. safely resizing/reallocating dynamic-partition space and creating a dedicated logical pmOS target;
2. using a separately verified non-Android physical partition only if one is large enough and intentionally expendable;
3. using userdata-backed image/container storage without reformatting all userdata;
4. initial external-storage boot for rootfs validation;
5. keeping the boot experiment on `recovery` while rootfs storage is proven.

The immediate goal remains normal postmarketOS rootfs boot with SSH over the already working U0g USB-NCM path, followed by Wi-Fi and incremental display bring-up.
