# External USB research and U0g direction

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Scope:** evidence beyond the local Samsung A33 device tree and local U0f run.

## 1. U0f's concrete result

U0f did not execute the MUIC register helper. Its persistent result established:

```text
i2c_dev_loaded=yes
i2c2_adapter_target=../../devices/platform/13850000.hsi2c/i2c-2/i2c-dev/i2c-2
i2c2_character_device=no
address_2_003e_owned=no
helper_output_present=no
```

Hook 03 expected runtime bus 2 to resolve to `13860000.hsi2c`. It therefore exited at the adapter guard before creating an I2C character node and before invoking the helper.

The failure was caused by unstable runtime bus numbering, not evidence against the `0x6d/0x70` sequence.

## 2. Corrected A33 topology

The full TWRP sysfs topology established:

```text
13860000.hsi2c:
  2-003d  s2mu106mfd
  2-003e  dummy (MUIC bank reservation)

138b0000.hsi2c:
  6-003b  s2mu106-fuelgauge
  6-003c  s2mu106-usbpd
```

The earlier bus-6 MUIC inference was incorrect. The A33 exposes S2MU106 banks through two physical controllers. The MUIC operation belongs to physical controller `13860000.hsi2c`, address `0x3e`.

In TWRP that controller is bus 2. In the minimal U0f initramfs, bus 2 was instead `13850000.hsi2c`, proving that the integer bus number is not a stable identifier across boot environments.

U0g must discover the runtime adapter whose physical path contains `13860000.hsi2c` and derive its current bus number.

## 3. Same S2MU106 family on another Samsung device

A stock Galaxy A71 boot log with S2MU106 shows:

- MUIC USB detection calling `_s2mu106_muic_sel_path`;
- `manual_sw_ctrl` changing from `0x00` to `0x24` when USB is detected.

Source:

- https://gist.github.com/faizauthar12/1947b2420e0af0cb04414ad9a8f4278c

This independently supports the intended manual-switch value `0x24`. It does not justify assuming that all S2MU106 banks share one Linux bus number on the A33.

## 4. Other postmarketOS USB gadget failure classes

### Exact UDC selection

postmarketOS had USB networking failures when the initramfs assumed there was only one UDC or assumed the gadget interface would always be `usb0`. The project added explicit UDC selection and discovery of the actual gadget interface.

Sources:

- https://gitlab.com/postmarketOS/pmaports/-/merge_requests/4750
- https://gitlab.com/postmarketOS/pmaports/-/issues/2564

This becomes relevant only after U0g proves the physical MUIC operation completed.

### Cable present during boot

Other postmarketOS devices have failed to enumerate when booted with the USB cable already connected, despite the gadget existing on the device. Disconnecting/reconnecting generated the missing bus-powered/configuration transition.

Source:

- https://gitlab.com/postmarketOS/pmaports/-/issues/99

A later experiment should distinguish:

- boot with cable connected;
- boot disconnected, then connect after gadget start;
- explicit gadget pull-up/run-stop cycle after role and MUIC state are configured.

### Samsung Exynos gadget operation is feasible

postmarketOS Exynos work has successfully enumerated a Samsung gadget/ACM serial device. This rules out a generic claim that downstream Exynos cannot expose a Linux USB gadget.

Source:

- https://gitlab.com/postmarketOS/pmaports/-/merge_requests/2546

## 5. U0g decision

Change only adapter selection and device-path parameterization:

- preserve the U0d Type-C patch;
- preserve original PDIC;
- preserve `i2c_dev` as the sole module delta;
- preserve register sequence `0x6d=0x13`, `0x70=0x24`, `0x6d=0x17`;
- preserve readback and rollback;
- preserve metadata persistence;
- find exactly one `i2c-dev` adapter backed by physical controller `13860000.hsi2c`;
- derive its runtime bus number;
- guard `<runtime bus>-003e` ownership;
- invoke the helper on `/dev/i2c-<runtime bus>`.

### If U0g helper fails

Fix the exact persistent I2C error. Do not change DWC3 or gadget logic simultaneously.

### If U0g helper succeeds but USB still does not enumerate

The physical MUIC switch hypothesis is insufficient. The next experiment should focus on:

1. exact `/sys/class/udc` enumeration and configfs bind result;
2. selected UDC identity;
3. explicit disconnect/reconnect or DWC3 run-stop/pull-up cycle;
4. boot-with-cable versus connect-after-boot;
5. minimal ACM serial gadget versus USB networking.

## 6. Desktop/Wi-Fi fallback

A desktop environment does not by itself solve the current problem because display output is still black and the safe initramfs intentionally omits the known-crashing display/MIPI stack. Wi-Fi can be developed as a parallel headless transport, but it requires identifying the WLAN chipset, safe kernel modules, firmware, and a way to supply credentials without an existing transport.

Recommended priority:

1. one corrected dynamic-controller MUIC experiment;
2. if the switch succeeds, one UDC/pull-up/ACM isolation experiment;
3. in parallel, start a separate Wi-Fi bring-up track;
4. introduce display/input support incrementally toward a desktop environment.
