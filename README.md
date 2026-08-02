# Linuxa33

Experimental postmarketOS port for the **Samsung Galaxy A33 5G
(SM-A336B / `a33x`)**, Exynos 1280 (`s5e8825`).

## Current state — 2026-08-02

- Exact firmware used: `A336BXXSDEYD2` (EUX/OXM).
- Bootloader is unlocked and Knox warranty bit is tripped.
- Official A33 TWRP boots successfully and has been restored after the latest
  experiment.
- A postmarketOS edge/OpenRC console rootfs and initramfs build successfully.
- The working TWRP kernel, DTB, recovery-DTBO and Samsung modules are packaged
  for postmarketOS.
- The first postmarketOS recovery test was accepted by the bootloader and
  started Linux, but panicked around 1.5 seconds later in
  `exynos_mipi_phy_probe` when `udevd` auto-loaded `phy_exynos_mipi.ko`.
- The failed image included all 315 TWRP recovery modules. That configuration is
  now explicitly guarded against in this repository.
- Android boot, `boot`, `vendor_boot`, `super` and `userdata` were not modified
  by the recovery test.

Read in this order:

1. [`docs/HANDOFF_2026-08-02.md`](docs/HANDOFF_2026-08-02.md)
2. [`docs/BOOTLOOP_ANALYSIS_2026-08-02.md`](docs/BOOTLOOP_ANALYSIS_2026-08-02.md)
3. [`docs/SAFE_NEXT_BOOT.md`](docs/SAFE_NEXT_BOOT.md)
4. [`docs/NEW_WORKSTATION.md`](docs/NEW_WORKSTATION.md)

## Bootloop prevention now enforced

The next recovery image must use the guarded workflow:

```bash
bash scripts/prepare-safe-module-packages.sh

python3 scripts/verify-initramfs-safety.py \
  --initramfs ~/a33-port/export-debug/initramfs

ROOT="$HOME/a33-port" \
LINUXA33_REPO="$HOME/Linuxa33" \
bash scripts/make-pmos-debug-recovery.sh
```

The safety tooling:

- generates a dependency closure from a small UFS/USB/SoC seed profile;
- blocks the confirmed `phy_exynos_mipi` panic module and the early
  display/camera stack;
- refuses the old full `modules.load.recovery` workflow unless an explicitly
  unsafe flag is supplied;
- refuses more than 128 initramfs modules by default;
- scans the final initramfs before the recovery builder can proceed;
- installs a modprobe rule that blocks the confirmed panic module.

This prevents the exact known 315-module failure. It does not yet solve the
kernel's missing `CONFIG_DEVTMPFS` support.

## Important safety notes

- Never flash an A53 image to this phone. The archived A53 package was only a
  structural template because it shares `s5e8825`.
- Never flash a homemade or rollback-index-zero `vbmeta`; the device previously
  rejected one with
  `SW REV CHECK FAIL (VBMETA) DEVICE: 0xD BINARY: 0x0`.
- Do not repeat the old debug image with SHA256
  `7a9e680ad87121876a1beff396c4dd3cdc8b841fe4bf52e721140a59c8cd036f`.
- Do not commit stock firmware, recovery/rootfs images, generated private keys,
  raw logs with identifiers, serial numbers, IMEI values or passwords.
- Keep a verified exact TWRP recovery backup available before every test.

## Repository scope

This repository holds:

- reproducible image and safety scripts;
- A33-specific pmaports package definitions;
- safe module seed/blocklist configuration;
- investigation notes and cold-start handoffs;
- checksums and procedures.

Large or proprietary artifacts remain outside Git:

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

Failed 315-module postmarketOS debug recovery image
SHA256 7a9e680ad87121876a1beff396c4dd3cdc8b841fe4bf52e721140a59c8cd036f
Size   100663296 bytes
```

The generated images are not stored in this repository.
