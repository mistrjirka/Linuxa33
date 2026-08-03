# U0f MUIC persistence runtime result

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Candidate:** `U0f-muic-persist`  
**Recovery SHA256:** `9273c6ed96f5ed6d51c28c8726769695ca1fe27a09d82109b407d150e38e50a2`  
**Runtime archive:** `u0f-result-20260803-145030.tar.gz`  
**Runtime archive SHA256:** `7f6c5154cd0bf46174ea453a27fc73f92d0742c78549406ede79423d9df34e34`

## Result classification

U0f is **negative for host USB enumeration**, but it conclusively explains why U0e/U0f did not test the intended MUIC register sequence: hook 03 aborted before creating the I2C character device or invoking the helper because its I2C topology assumption was wrong.

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

Hook 03 expected bus 2 to map to `13860000.hsi2c`. The actual bus-2 adapter was `13850000.hsi2c`. Hook 03 intentionally failed closed on this mismatch before its `mknod` and helper invocation stages. This explains all of the following together:

- `i2c_dev` loaded;
- `/sys/class/i2c-dev/i2c-2` appeared;
- `/dev/i2c-2` was not created;
- `/run/a33x-muic-switch-helper.log` did not exist;
- no helper success or error transcript was available.

## Stronger S2MU106 topology evidence

The recovered candidate kernel log identifies the active S2MU106 clients as:

```text
s2mu106-fuelgauge 6-003b
usbpd-s2mu106 6-003c
```

Therefore the S2MU106 device family is on I2C bus 6 in this runtime, not bus 2. The MUIC bank at address `0x3e` is expected to be a sibling on that same S2MU106 bus, pending direct TWRP sysfs confirmation.

The next candidate must not simply change the bus-2 expected controller from `13860000` to `13850000`. That would preserve the more fundamental wrong-bus assumption.

## USB and stability result

- no kernel panic occurred;
- UFP and delayed notifier activity remained present;
- the host saw no Linux USB gadget enumeration during the U0f observation window;
- later Samsung USB appearances belonged to Download Mode and restored TWRP;
- the wrapped Samsung `last_kmsg` includes mixed/stale DWC3 reset lines, while the host and persistent U0f result establish that U0f did not enumerate.

## Required next step

Run the read-only TWRP topology probe:

```sh
bash scripts/probe-a33-s2mu106-topology.sh
```

It must establish:

1. the controller behind I2C bus 6;
2. the exact clients at `6-003b`, `6-003c`, `6-003d`, and `6-003e`;
3. the client names and bound drivers;
4. whether `6-003e` is the S2MU106 MUIC client under the full TWRP stack.

Only then build U0g. U0g should be an actual functional correction, not another observability-only experiment:

- retain the safe U0d Type-C path;
- retain `i2c_dev` and metadata persistence;
- discover or verify the S2MU106 bus using the known USB-PD sibling at address `0x3c`;
- target the MUIC sibling at address `0x3e` on the same bus;
- create the correct `/dev/i2c-N` node;
- run and persist the exact register transcript;
- keep the full MUIC/CPIF/BTS soft-dependency closure absent.

## Broader bring-up direction

USB remains desirable, but it is not the only management path. After the corrected U0g attempt, work should branch in parallel:

1. USB-C debug transport and physical enumeration;
2. safe headless Wi-Fi plus SSH as an alternate management path;
3. incremental display/input bring-up toward a desktop environment.

A desktop environment cannot yet be treated as the immediate shortcut because the display/MIPI stack previously caused a confirmed kernel panic when loaded indiscriminately. Display modules must be introduced incrementally with the same fail-closed approach.