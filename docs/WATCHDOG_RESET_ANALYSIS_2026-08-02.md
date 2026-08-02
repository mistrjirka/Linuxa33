# A33 guarded recovery test: watchdog reset diagnosis

Date: 2026-08-02

## Result

The guarded 63-module postmarketOS recovery no longer reproduces the earlier
`phy_exynos_mipi` BRK panic. The module blocklist therefore removed the known
immediate panic path.

The remaining repeatable boot failure is expiration of the cluster-0 hardware
watchdog. The first shell-feeder experiment (`watchdog-v1`) did not feed the
correct device successfully.

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

On the following boot, Samsung LK reports:

```text
rst_stat:0x1000000 / CL0_WDTRESET
Watchdog or Warm Reset Detected.
```

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

The sysfs identity of the required device is:

```text
/sys/class/watchdog/watchdog0/device -> /sys/devices/platform/10060000.watchdog_cl0
```

TWRP starts its daemon from `init.recovery.s5e8825.rc` with the documented
intent to feed every 10 seconds and maintain a 20 second margin. The Android
binary is dynamically linked against `/system/bin/linker64`, so copying that
binary alone into an Alpine/postmarketOS initramfs is not an appropriate
solution.

## Watchdog-v1 result

Candidate:

```text
SHA256: a77a44be848b4a22dcdb56699e4de04746b0ac89d39d414f33c19720273fc782
compressed initramfs: 11358903 bytes (bootloader: 0x00ad52b7)
```

Using the exact ramdisk size to isolate this candidate from older boots in the
circular `last_kmsg` shows:

1. It reaches `init_2nd.sh`, discovers UFS, and attempts to create the USB
   gadget.
2. A scheduler warning occurs around 8.18 seconds in `update_load_avg`, but the
   kernel continues running; it is not the reset cause.
3. At about 48.9 seconds, `/dev/loop0` cannot be opened and the USB gadget path
   continues.
4. The final candidate messages are DWC3 runtime-suspend messages around 49.13
   seconds.
5. The immediately following boot reports `CL0_WDTRESET`.
6. The candidate segment contains no `a33x-watchdog` startup or ping messages.

There is also an older DWC3 `Internal error: Oops` and kernel panic elsewhere in
the same circular log. It belongs to a different historical boot and must not
be attributed to watchdog-v1.

## Why watchdog-v1 likely failed

Version 1 waited only for the Android/TWRP legacy alias `/dev/watchdog`.
postmarketOS uses mdev in this initramfs, and the legacy alias is not guaranteed
to be created. The canonical Linux watchdog node is `/dev/watchdog0`, which is
also the exact sysfs device mapped to `10060000.watchdog_cl0` in TWRP.

Because this vendor kernel lacks `CONFIG_DEVTMPFS`, device nodes must not be
assumed to appear automatically. Version 2 therefore resolves
`/sys/class/watchdog/watchdog0/dev`, creates `/dev/watchdog0` with the reported
major/minor when necessary, and uses `/dev/watchdog` only as a fallback. It also
creates `/dev/kmsg` when absent and records every watchdog ping.

Files:

```text
pmaports/main/postmarketos-mkinitfs-hook-a33x-watchdog/
scripts/verify-initramfs-watchdog-hook.sh
```

## Separate USB observation

USB is not yet usable as a debug transport:

1. DWC3 and the gadget are initialized.
2. `usb0` is created.
3. The host never enumerates the postmarketOS USB-network gadget.
4. DWC3 runtime PM suspends the controller and powers down its PHY.

This is an independent issue to address after watchdog survival. Do not weaken
the MIPI/camera blocklist to address it.

## Next controlled experiment

Build watchdog-v2 without changing the kernel command line or module set. The
success criteria are:

1. The final initramfs contains the executable v2 hook.
2. Offline verification proves that it resolves `/dev/watchdog0` from sysfs and
   can create the device node.
3. The image still passes the 63-module safety gate.
4. The phone remains alive for more than 90 seconds.
5. `last_kmsg` contains `a33x-watchdog-v2` startup and periodic ping messages.
6. The next boot does not report `CL0_WDTRESET`.

Only if the exact `/dev/watchdog0` feeder still cannot prevent the reset should
the debug-only kernel-command-line experiment be revisited:

```text
s3c2410_wdt.tmr_atboot=0 sec_watchdog.sec_pet=0
```

After watchdog survival is proven, debug DWC3 role/runtime-PM separately.
