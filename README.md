# Linuxa33

Experimental postmarketOS port for the **Samsung Galaxy A33 5G (SM-A336B / `a33x`)**, Exynos 1280 (`s5e8825`).

## Current state — 2026-08-02

- Exact device firmware used: `A336BXXSDEYD2` (EUX/OXM).
- Bootloader is unlocked and Knox warranty bit is tripped.
- Official TWRP for `a33x` boots successfully from the recovery partition.
- A postmarketOS edge/OpenRC console rootfs and initramfs build successfully.
- The working TWRP kernel, DTB, recovery-DTBO and Samsung modules were packaged for postmarketOS.
- A recovery-header-v2 postmarketOS debug image was reconstructed from the exact TWRP layout and validated byte-for-byte where applicable.
- The debug recovery image was written to recovery successfully, but the phone currently **bootloops when entering recovery**.
- Android boot, `boot`, `vendor_boot`, `super` and `userdata` were not modified by that test.

The detailed cold-start continuation document is in [`docs/HANDOFF_2026-08-02.md`](docs/HANDOFF_2026-08-02.md).

## Important safety notes

- Never flash an A53 image to this phone. The archived A53 package was used only as a structural template because it shares `s5e8825`.
- Never flash a homemade or rollback-index-zero `vbmeta`; the device previously rejected one with `SW REV CHECK FAIL (VBMETA) DEVICE: 0xD BINARY: 0x0`.
- Do not commit stock firmware, recovery images, rootfs images, generated private keys, user data, serial numbers, IMEI values or passwords.
- Keep a verified exact TWRP recovery backup available before every recovery test.

## Repository scope

This repository is intended to hold:

- reproducible scripts;
- the A33-specific pmaports package definitions;
- configuration and checksums;
- investigation logs with personal identifiers removed;
- cold-start handoff documentation.

Large/proprietary artifacts stay outside Git:

- Samsung stock firmware;
- TWRP and generated recovery images;
- postmarketOS root/boot images;
- extracted proprietary modules and firmware;
- generated AVB private keys.

## Known verified hashes

```text
Official TWRP recovery.img
SHA256 414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e
Size   100663296 bytes

Generated postmarketOS debug recovery test image
SHA256 7a9e680ad87121876a1beff396c4dd3cdc8b841fe4bf52e721140a59c8cd036f
Size   100663296 bytes
```

The generated debug image is not stored in this repository.
