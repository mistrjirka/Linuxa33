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

## Exact TWRP watchdog interface

Live TWRP inspection established the known-working userspace behavior:

```text
/system/bin/watchdogd 10 20
```

The process keeps file descriptor 4 open on `/dev/watchdog`. Available nodes
are:

```text
/dev/watchdog
/dev/watchdog0 -> platform 10060000.watchdog_cl0
/dev/watchdog1 -> virtual watchdog device
```

TWRP starts it from `init.recovery.s5e8825.rc` with the documented intent to
feed every 10 seconds and maintain a 20 second margin. The Android binary is
dynamically linked against `/system/bin/linker64`, so copying that binary alone
into an Alpine/postmarketOS initramfs is not an appropriate solution.

A dedicated noarch postmarketOS mkinitfs hook has therefore been added at:

```text
pmaports/main/postmarketos-mkinitfs-hook-a33x-watchdog/
```

Its early shell feeder opens `/dev/watchdog` once, keeps the file descriptor
open, and writes every 8 seconds. This mirrors the proven TWRP interface without
adding Android linker or Bionic dependencies. The hook logs startup and failure
messages to `/dev/kmsg` and stores its PID in `/run/a33x-watchdog.pid`.

The test recovery must be rejected before assembly unless
`hooks/01-a33x-watchdog.sh` is present and passes
`scripts/verify-initramfs-watchdog-hook.sh`.

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

Build a recovery with the early A33 watchdog hook and leave the existing kernel
command line unchanged. This avoids combining two variables in one experiment.

The success criteria are:

1. `hooks/01-a33x-watchdog.sh` is present in the final initramfs.
2. The image still passes the 63-module safety gate.
3. The phone remains alive for more than 90 seconds.
4. The next boot does not report `CL0_WDTRESET`.
5. `last_kmsg` contains `a33x-watchdog: started early feeder` and periodic
   hardware watchdog keepalive messages.

Only if the feeder cannot open or ping `/dev/watchdog` should the debug-only
kernel-command-line experiment be revisited:

```text
s3c2410_wdt.tmr_atboot=0 sec_watchdog.sec_pet=0
```

After watchdog survival is proven, debug DWC3 role/runtime-PM separately.
