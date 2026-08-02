# Safety changes after the confirmed `phy_exynos_mipi` panic

Main branch now prevents accidental recreation of the failed 315-module recovery image.

## Enforced controls

- Safe dependency-closure generator with UFS/USB/SoC seeds.
- Hard blocklist for `phy_exynos_mipi` and early display/camera/media modules.
- Default maximum of 128 embedded kernel modules.
- Explicit unsafe opt-in required for the old complete TWRP load list.
- Completed-initramfs scanner.
- Recovery builder runs the scanner and fails closed.
- Device package installs a modprobe rule blocking the confirmed panic module.
- New workstation and next-boot documentation updated to use only the guarded workflow.

## Relevant files

```text
config/modules-initfs-safe-debug.seeds
config/modules-initfs-blocklist.glob
scripts/generate-modules-initfs.py
scripts/prepare-safe-module-packages.sh
scripts/verify-initramfs-safety.py
scripts/make-pmos-debug-recovery.sh
pmaports/device/downstream/device-samsung-a33x/90-samsung-a33x-unsafe-modules.conf
docs/SAFE_NEXT_BOOT.md
```

## Required next-workstation entry point

```bash
cd ~/Linuxa33
git pull --ff-only
bash scripts/prepare-safe-module-packages.sh
```

After rebuilding and exporting the debug initramfs:

```bash
python3 scripts/verify-initramfs-safety.py \
  --initramfs ~/a33-port/export-debug/initramfs
```

The recovery builder performs the same check again and refuses to continue if the safety checker or blocklist is missing.

These changes prevent the exact known panic path from being packaged again. They do not yet correct the missing `CONFIG_DEVTMPFS` kernel options or guarantee that the next minimal image boots.
