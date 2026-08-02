# A33 guarded recovery test: watchdog reset diagnosis

Date: 2026-08-02

## Result

The guarded 63-module postmarketOS recovery no longer reproduces the earlier
`phy_exynos_mipi` BRK panic. The module blocklist therefore removed the known
immediate panic path.

The new boot failure is a hardware watchdog reset, not a kernel panic.

## Evidence from `last_kmsg`

The Samsung bootloader starts the cluster-0 watchdog with a 60 second timeout
before entering the Linux kernel:

```text
kernel_watchdog_start: Start Watchdog 60 sec...
```

The postmarketOS initramfs then boots the preserved TWRP kernel and reaches:

- `/init` and `init_2nd.sh`
- UFS discovery and all expected `sda` partitions
- DWC3/USB gadget probing
- creation of `usb0`

The final Linux messages occur at about 43.22 seconds of kernel uptime. The
bootloader had already spent about 14.27 seconds before starting the kernel,
which places the silent reset at approximately the original 60 second watchdog
deadline.

On the following boot, Samsung LK reports:

```text
rst_stat:0x1000000 / CL0_WDTRESET
Watchdog or Warm Reset Detected.
```

There is no `Kernel panic`, fatal exception, or BRK trace in this test boot.

TWRP stays alive because its userspace starts `watchdogd`, which periodically
pets the Exynos cluster watchdog. The postmarketOS debug initramfs did not start
an equivalent early watchdog handler.

## Separate USB observation

USB is not yet usable as a debug transport:

1. `init_2nd.sh` initially reports that no UDC is available or it is busy.
2. DWC3 finishes probing shortly afterward.
3. At roughly 43.2 seconds, `usb0` is created and gadget pull-up starts.
4. Runtime PM immediately suspends DWC3 and turns the USB PHY LDO off.
5. The host never enumerates a postmarketOS USB network device before the
   watchdog reset.

This is likely the next independent issue after watchdog survival is fixed.
Do not weaken the MIPI/camera blocklist to address it.

## Next controlled experiment

First test a debug-only kernel-command-line override that disables the Samsung
watchdog-at-boot behavior while keeping the kernel, DTB, recovery-DTBO,
initramfs module set, trailer, and AVB construction unchanged:

```text
s3c2410_wdt.tmr_atboot=0 sec_watchdog.sec_pet=0
```

This is an experiment, not yet a proven permanent fix. The rebuilt image must
show these arguments in its final unpacked boot information before flashing.
The success criterion is surviving for more than 90 seconds without a
`CL0_WDTRESET`.

If the command-line override is ignored by this vendor kernel, add an early
initramfs watchdog petter using the actual TWRP watchdog device and interface.
Inspect TWRP's `/dev/watchdog*`, `/sys/class/watchdog`, `watchdogd` process, and
recovery init scripts before implementing that fallback.

After watchdog survival is proven, debug DWC3 role/runtime-PM separately.
