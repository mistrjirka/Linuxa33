# Recovery bootloop analysis — 2026-08-02

## Conclusion

The failed postmarketOS recovery image was accepted far enough to start the preserved TWRP kernel and execute the postmarketOS initramfs. The bootloader did **not** reject the locally generated recovery AVB key before kernel start.

The reset is caused by a repeatable kernel panic while `udevd` loads the Samsung MIPI PHY module:

```text
Unexpected kernel BRK exception at EL1
Internal error: BRK handler: f2005512 [#1] PREEMPT SMP
pc : exynos_mipi_phy_probe+0x4e0/0x4e8 [phy_exynos_mipi]
Kernel panic - not syncing: BRK handler: Fatal exception
```

The panic occurs while probing this DT node:

```text
exynos-mipi-phy-csi dphy_m4s0_dsim0@0x11860000
```

Immediately before every panic it prints four PHY entries:

```text
isolation: 0x714, power-gating: 0x0
isolation: 0x0, power-gating: 0x0
isolation: 0x0, power-gating: 0x0
isolation: 0x0, power-gating: 0x0
```

## Evidence from `last_kmsg`

The captured 2 MiB Samsung last-kmsg buffer contains six repetitions of the same failure. The panic occurs approximately 1.50–1.56 seconds after Linux starts, on varying CPUs and `udevd` PIDs, but always at the same probe offset:

```text
1.506262  CPU1  udevd PID214  exynos_mipi_phy_probe+0x4e0/0x4e8
1.513164  CPU0  udevd PID207  exynos_mipi_phy_probe+0x4e0/0x4e8
1.545890  CPU1  udevd PID211  exynos_mipi_phy_probe+0x4e0/0x4e8
1.502168  CPU3  udevd PID213  exynos_mipi_phy_probe+0x4e0/0x4e8
1.559321  CPU4  udevd PID228  exynos_mipi_phy_probe+0x4e0/0x4e8
1.515729  CPU6  udevd PID211  exynos_mipi_phy_probe+0x4e0/0x4e8
```

This rules out a random watchdog reset. The watchdog only restarts the phone after the kernel has already panicked.

The log also proves that:

- the TWRP kernel starts;
- the postmarketOS initramfs is unpacked;
- PID 1 starts enough userspace to launch `udevd`;
- many Samsung modules are loaded;
- UFS-related modules are present;
- USB-related modules start loading;
- no debug-shell network appears because the panic happens first.

## `DEVTMPFS` finding

The kernel still lacks `CONFIG_DEVTMPFS`. Each boot contains:

```text
request_module fs-devtmpfs succeeded, but still no fs?
```

This is a real compatibility defect and must be fixed for a proper port. It is **not the immediate cause of this reset**, because the initramfs continues, launches `udevd`, and then crashes later in `phy_exynos_mipi`.

## Most likely trigger

The failed device package placed all 315 entries from TWRP's `modules.load.recovery` into `modules-initfs`. postmarketOS then exposed all of them to normal initramfs `udev` modalias loading. The log shows several module probes running concurrently.

TWRP boots the same kernel and module binaries successfully, so the leading hypothesis is a module-loading/order/concurrency difference rather than a fundamentally invalid kernel, DTB, or module binary.

This remains an inference until tested. The immediate reproducible fact is that automatic loading of `phy_exynos_mipi.ko` from `udevd` panics in `exynos_mipi_phy_probe`.

## Relevant source behavior

In the matching Gabriel2392 kernel source, `exynos_mipi_phy_probe()`:

1. counts DT `reset` entries;
2. iterates over those entries;
3. tries to obtain one memory resource per PHY;
4. uses a named `lane` resource when available;
5. otherwise derives the lane address from `state->phys[i].regs + 0x100`.

The panic is at the very end of this probe (`+0x4e0` of a `0x4e8` function). Exact source-to-instruction mapping still requires the matching unstripped module or a rebuilt module with symbols. A null/missing resource reaching the default lane-address calculation is one plausible explanation, but it is not yet proven.

## Repository mitigation implemented

The repository now prevents accidental recreation of the failed image:

- `scripts/generate-modules-initfs.py` defaults to a dependency-closed safe profile;
- the complete TWRP load list requires an explicit unsafe override;
- MIPI/display/camera modules are hard-blocked;
- more than 128 initramfs modules are rejected by default;
- `scripts/verify-initramfs-safety.py` scans the completed initramfs;
- the recovery builder runs that scanner and fails closed;
- the device package installs a modprobe rule blocking `phy_exynos_mipi`;
- `docs/SAFE_NEXT_BOOT.md` contains the exact next-workstation procedure.

These controls prevent the exact known panic path from being packaged again. They do not guarantee that the smaller image boots.

## Correct next experiment

Do **not** rebuild or reflash the same 315-module image.

Build a new recovery debug initramfs containing only the dependency closure for:

- SoC basics needed by UFS/USB dependencies;
- UFS/block access;
- the Exynos USB DRD PHY/controller;
- configfs USB NCM/debug-shell.

Explicitly exclude all display, MIPI, camera and media modules, especially:

```text
phy-exynos-mipi.ko
phy-exynos-mipi-dsim.ko
exynos-drm.ko
mcd-panel*.ko
fimc-is.ko
is-cis-*.ko
camerapp.ko
```

Use `scripts/prepare-safe-module-packages.sh`; do not manually copy `modules.load.recovery`.

## Staged follow-up

1. Boot with `phy_exynos_mipi` and the whole camera/display stack excluded.
2. Confirm the debug shell becomes reachable.
3. Add module groups back one at a time.
4. Test `phy_exynos_mipi` last, first sequentially and then under udev.
5. Obtain/build the exact kernel module with symbols and map `+0x4e0` to the precise source instruction.
6. Build the kernel with `CONFIG_DEVTMPFS=y`, `CONFIG_DEVTMPFS_MOUNT=y`, and the remaining OpenRC/postmarketOS requirements.

## Artifacts

Original captured files on the old workstation:

```text
build/bootloop-logs-2026-08-02/last_kmsg.txt
build/bootloop-logs-2026-08-02/twrp-dmesg.txt
build/bootloop-logs-2026-08-02/pstore-list.txt
```

`/sys/fs/pstore` was empty. `last_kmsg.txt` contained the useful repeated panic records.

Do not commit raw logs until device identifiers have been removed.
