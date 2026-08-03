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

Hook 03 expected bus 2 to resolve to `13860000.hsi2c`. It therefore exited at the adapter guard before creating `/dev/i2c-2` and before invoking the helper.

The U0f result also shows no kernel panic and no host enumeration. It does not test whether the `0x6d/0x70` register sequence works.

## 2. Same S2MU106 family on another Samsung device

A stock Galaxy A71 boot log with S2MU106 shows:

- fuel gauge at `<bus>-003b`;
- USB-PD at the same `<bus>-003c`;
- MUIC USB detection calling `_s2mu106_muic_sel_path`;
- `manual_sw_ctrl` changing from `0x00` to `0x24` when USB is detected.

Source:

- https://gist.github.com/faizauthar12/1947b2420e0af0cb04414ad9a8f4278c

This independently supports both:

1. the intended manual-switch value `0x24`;
2. treating the S2MU106 fuel-gauge, USB-PD, and MUIC banks as members of one I2C-bus topology rather than assuming bus 2 from a generic controller name.

## 3. A33 recovered-kernel evidence

The A33 U0f recovered log repeatedly identifies:

```text
s2mu106-fuelgauge 6-003b
usbpd-s2mu106 6-003c
```

This is strong evidence that the relevant S2MU106 topology is on bus 6. The expected unowned MUIC bank is therefore likely `6-003e`, but that final mapping must be confirmed from TWRP sysfs before any write.

The repository now includes a read-only topology probe:

```text
scripts/probe-a33-s2mu106-topology.sh
```

It enumerates every I2C adapter/client and fails closed unless the already-observed `6-003b` and `6-003c` clients exist.

## 4. Other postmarketOS USB gadget failure classes

### Exact UDC selection

postmarketOS had USB networking failures when the initramfs assumed there was only one UDC or assumed the gadget interface would always be `usb0`. The project added explicit UDC selection and discovery of the actual gadget interface.

Sources:

- https://gitlab.com/postmarketOS/pmaports/-/merge_requests/4750
- https://gitlab.com/postmarketOS/pmaports/-/issues/2564

This is not the current U0f root cause: U0f stopped before the MUIC helper. It becomes relevant after the corrected physical path succeeds.

### Cable present during boot

Other postmarketOS devices have failed to enumerate when booted with the USB cable already connected, despite the gadget existing on the device. Disconnecting/reconnecting generated the missing bus-powered/configuration transition.

Source:

- https://gitlab.com/postmarketOS/pmaports/-/issues/99

A later experiment should therefore distinguish:

- boot with cable connected;
- boot disconnected, then connect after gadget start;
- explicit gadget pull-up/run-stop cycle after role and MUIC state are configured.

### Samsung Exynos gadget operation is feasible

postmarketOS Exynos work has successfully enumerated a Samsung gadget/ACM serial device. This rules out a generic claim that downstream Exynos cannot expose a Linux USB gadget.

Source:

- https://gitlab.com/postmarketOS/pmaports/-/merge_requests/2546

## 5. Decision tree

### U0g

After the read-only topology probe, change only the incorrect I2C topology assumption:

- use the proven bus and adapter target;
- preserve the U0d Type-C patch;
- preserve original PDIC;
- preserve the exact helper sequence and rollback;
- preserve metadata persistence;
- use `/dev/i2c-<proven bus>` and `<proven bus>-003e` ownership guard.

### If U0g helper fails

Fix the exact persistent I2C error. Do not change DWC3 or gadget logic simultaneously.

### If U0g helper succeeds but USB still does not enumerate

The physical MUIC switch hypothesis is insufficient. The next experiment should focus on:

1. exact `/sys/class/udc` enumeration and configfs bind result;
2. selected UDC identity;
3. explicit disconnect/reconnect or DWC3 run-stop/pull-up cycle;
4. boot-with-cable versus connect-after-boot;
5. minimal ACM serial gadget versus USB networking, to separate basic enumeration from networking configuration.

## 6. Desktop/Wi-Fi fallback

A desktop environment does not by itself solve the current problem because display output is still black and the safe initramfs intentionally omits the known-crashing display/MIPI stack. Wi-Fi can be developed as a parallel headless transport, but it requires identifying the WLAN chipset, safe kernel modules, firmware, and a way to supply credentials without an existing transport.

Recommended priority:

1. one corrected bus-6 MUIC experiment;
2. if the switch succeeds, one UDC/pull-up/ACM isolation experiment;
3. in parallel, start a separate Wi-Fi bring-up track rather than waiting for a full desktop environment.