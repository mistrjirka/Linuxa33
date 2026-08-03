# U0f MUIC persistence runtime result

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Candidate:** `U0f-muic-persist`  
**Recovery SHA256:** `9273c6ed96f5ed6d51c28c8726769695ca1fe27a09d82109b407d150e38e50a2`  
**Runtime archive:** `u0f-result-20260803-145030.tar.gz`  
**Runtime archive SHA256:** `7f6c5154cd0bf46174ea453a27fc73f92d0742c78549406ede79423d9df34e34`

## Result classification

U0f is **negative for host USB enumeration**, but it conclusively explains why U0e/U0f did not test the intended MUIC register sequence: hook 03 aborted before creating the I2C character device or invoking the helper because it treated a runtime I2C bus number as stable.

This is not evidence that the register sequence itself is ineffective. The helper did not run.

## Persistent evidence

The U0f metadata result was recovered successfully from:

```text
/a33x-bringup/u0f-muic-result.txt
```

It reports:

```text
i2c_dev_loaded=yes
i2c2_adapter_target=../../devices/platform/13850000.hsi2c/i2c-2/i2c-dev/i2c-2
i2c2_device_number=89:2
i2c2_character_device=no
address_2_003e_owned=no
helper_output_present=no
helper_success_marker=no
```

Hook 03 expected runtime bus 2 to map to physical controller `13860000.hsi2c`. In the minimal initramfs, runtime bus 2 instead mapped to `13850000.hsi2c`. Hook 03 intentionally failed closed on this mismatch before its `mknod` and helper invocation stages.

This explains all of the following together:

- `i2c_dev` loaded;
- `/sys/class/i2c-dev/i2c-2` appeared;
- `/dev/i2c-2` was not created;
- `/run/a33x-muic-switch-helper.log` did not exist;
- no helper success or error transcript was available.

## Corrected topology from full TWRP sysfs

The read-only TWRP topology probe established:

```text
2-003d -> /sys/devices/platform/13860000.hsi2c/i2c-2/2-003d
name    -> s2mu106mfd

2-003e -> /sys/devices/platform/13860000.hsi2c/i2c-2/2-003e
name    -> dummy

6-003b -> /sys/devices/platform/138b0000.hsi2c/i2c-6/6-003b
name    -> s2mu106-fuelgauge

6-003c -> /sys/devices/platform/138b0000.hsi2c/i2c-6/6-003c
name    -> s2mu106-usbpd
```

The earlier inference that the MUIC bank should be `6-003e` was wrong. The A33 exposes different S2MU106 banks through two physical I2C controllers:

- MFD/MUIC bank: `13860000.hsi2c`, TWRP bus 2, addresses `0x3d/0x3e`;
- fuel-gauge/USB-PD bank: `138b0000.hsi2c`, TWRP bus 6, addresses `0x3b/0x3c`.

The correct invariant is therefore the **physical controller `13860000.hsi2c`**, not the integer bus number 2. Bus numbering changes when the minimal initramfs registers a different subset/order of adapters.

## USB and stability result

- no kernel panic occurred;
- UFP and delayed notifier activity remained present;
- the host saw no Linux USB gadget enumeration during the U0f observation window;
- later Samsung USB appearances belonged to Download Mode and restored TWRP;
- the wrapped Samsung `last_kmsg` includes mixed/stale DWC3 reset lines, while the host and persistent U0f result establish that U0f did not enumerate.

## Required U0g correction

U0g must change only I2C adapter selection:

1. load `i2c_dev` as before;
2. enumerate `/sys/class/i2c-dev/i2c-*` after registration;
3. select exactly one adapter whose resolved physical path contains `13860000.hsi2c`;
4. derive the runtime bus number from that selected adapter;
5. create `/dev/i2c-<runtime bus>` from its sysfs major/minor;
6. refuse if `<runtime bus>-003e` is owned;
7. run the unchanged `0x6d/0x70` sequence against the dynamically selected device;
8. persist the exact transcript to metadata.

The U0d Type-C patch, original PDIC module, module set, register values, readback, rollback, and metadata channel remain unchanged.

## Broader bring-up direction

After U0g:

1. if the helper fails, fix the exact persistent I2C error;
2. if the helper succeeds but USB still fails, isolate UDC selection, pull-up/run-stop, cable reconnect behavior, and a minimal ACM serial gadget;
3. continue a parallel headless Wi-Fi/SSH track;
4. introduce display/input modules incrementally toward a desktop environment rather than loading the prior crashing display/MIPI closure indiscriminately.
