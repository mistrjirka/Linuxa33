# A33 device package safety notes

`modules-initfs` is intentionally generated locally and is not stored as a full TWRP module list.

Generate it only through:

```bash
bash ~/Linuxa33/scripts/prepare-safe-module-packages.sh
```

The previous experiment copied all 315 entries from `modules.load.recovery`. `udevd` then auto-loaded `phy_exynos_mipi` and caused a repeatable kernel panic in `exynos_mipi_phy_probe`.

This package also installs `90-samsung-a33x-unsafe-modules.conf`, which blocks the confirmed panic module and related early display modules. Remove that protection only for a controlled, separately logged module-order experiment.
