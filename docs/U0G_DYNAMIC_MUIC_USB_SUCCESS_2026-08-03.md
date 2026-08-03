# U0g dynamic MUIC USB success

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Candidate:** `U0g-muic-dynamic`  
**Recovery SHA256:** `e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81`  
**Runtime archive:** `u0g-result-20260803-155749.tar.gz`  
**Runtime archive SHA256:** `bb8ffc202234b8490ba6b3d30e78912b020db9b48c73e68a955bd9ac5e9e838e`

## Result classification

U0g is a **confirmed success for the Samsung A33 USB-C physical data path and Linux USB gadget enumeration**.

This closes the original MUIC-routing uncertainty. The stable identifier is the physical I2C controller `13860000.hsi2c`; its Linux bus number changes with adapter registration order and must not be hardcoded.

## Dynamic adapter selection

The persistent U0g result records:

```text
selected_controller=13860000.hsi2c
selected_bus=3
selected_entry=/sys/class/i2c-dev/i2c-3
selected_target=/sys/devices/platform/13860000.hsi2c/i2c-3/i2c-dev/i2c-3
selected_device=/dev/i2c-3
selected_device_number=89:3
selected_address=0x3e
selected_address_sysfs=/sys/bus/i2c/devices/3-003e
helper_rc=0
```

Therefore the same physical controller that appears as bus 2 in full TWRP appeared as bus 3 in the minimal postmarketOS recovery runtime.

## Exact MUIC register result

The helper transcript is complete:

```text
a33x-muic-switch-v2: initial device=/dev/i2c-3 bus=3 address=0x3e ctrl1=0x13 switch=0x00
a33x-muic-switch-v2: verify device=/dev/i2c-3 bus=3 reg=0x6d expected=0x13 actual=0x13
a33x-muic-switch-v2: verify device=/dev/i2c-3 bus=3 reg=0x70 expected=0x24 actual=0x24
a33x-muic-switch-v2: verify device=/dev/i2c-3 bus=3 reg=0x6d expected=0x17 actual=0x17
a33x-muic-switch-v2: success device=/dev/i2c-3 bus=3 ctrl1=0x17 switch=0x24
```

The sequence succeeded without rollback:

1. initial `CTRL1 (0x6d) = 0x13`;
2. initial manual switch `0x70 = 0x00`;
3. write and verify `0x70 = 0x24`;
4. write and verify `0x6d = 0x17`;
5. helper return code `0`;
6. no error or rollback marker.

## Host USB proof

The host kernel independently observed the Linux gadget:

```text
idVendor=04e8, idProduct=6860
Product: Samsung Galaxy A33 5G
Manufacturer: Samsung
SerialNumber: postmarketOS
cdc_ncm ... enp197s0f0u1
cdc_acm ... ttyACM0: USB ACM device
```

The first enumeration exposed CDC-NCM. After cable disconnect/reconnect, the host again enumerated CDC-NCM and also attached CDC-ACM. The user confirmed the NCM interface received `172.16.42.2/24` and that `172.16.42.1` responded to ping.

This proves:

- the Type-C manager accepted the UFP path;
- DWC3 completed real host reset/connect-done handling;
- the S2MU106 manual switch routed the physical USB data lines;
- the host received descriptors and created a network interface;
- cable reconnect works and is a useful recovery behavior.

## Serial and network service status

USB functions and phone-side services are separate concerns.

Observed:

- CDC-NCM transport works;
- CDC-ACM enumerates as `/dev/ttyACM0` on the host;
- no phone-side getty or shell was attached to the ACM endpoint;
- telnet accepted one connection and closed, then ports 22 and 23 were closed;
- raw serial writes produced no response.

Therefore USB is no longer the blocker. The next blocker is providing a persistent management service in the initramfs or continuing into the full root filesystem.

## Apparent panic matches are stale mixed-buffer data

The generic collector counted two `panic` strings in Samsung's mixed/wrapped 2 MiB `last_kmsg`:

1. an `exynos_ufs_probe` / `exynos_pm_qos` panic at timestamp `1.451564`;
2. a bootloader line reporting a retained previous panic reset reason.

These cannot describe the successful U0g boot:

- the U0g metadata report was generated at uptime `3.61` seconds;
- the helper had already completed successfully;
- the host later enumerated the postmarketOS gadget and communicated over NCM.

A kernel panic at `1.45` seconds in the same boot would make those later events impossible. The panic strings are therefore stale or mixed records in the Samsung wrapped buffer, not evidence that U0g panicked.

## Collector correction

The U0g wrapper now:

- creates `u0g-metadata-result.txt` instead of exposing only the historic U0f filename;
- creates a U0g-specific summary;
- reports selected controller, bus, device and register values;
- records host CDC-NCM/CDC-ACM enumeration;
- labels raw wrapped-buffer panic matches as unattributed rather than current-boot panics.

## Required next direction

Preserve U0g's working physical path unchanged. The next candidate should focus on management and normal boot, not another USB hardware workaround:

1. attach an explicit shell/getty to `/dev/ttyGS0`, or preferably start Dropbear/OpenSSH on the NCM interface;
2. continue from the debug initramfs into the actual postmarketOS root filesystem;
3. verify persistent SSH over `172.16.42.1`;
4. use the working USB network to bring up Wi-Fi;
5. introduce display/input modules incrementally before selecting a desktop environment.

The dynamic controller discovery must remain the production rule:

```text
find the unique i2c-dev adapter whose resolved physical path contains
13860000.hsi2c
```

Do not replace this with a fixed Linux bus number.
