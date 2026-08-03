# U0e MUIC switch runtime result

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Candidate:** `U0e-muic-switch`  
**Candidate recovery SHA256:** `9aaf91f14758ab185283534072f82503d2f85be35d80ace9cd2dd67fc5bc09fa`  
**Runtime archive:** `u0e-result-20260803-134743.tar.gz`  
**Runtime archive SHA256:** `da81eb6b615acb91b99f79b7f0d7ab163d91a643bd56132247fd9e335fbd7c11`

## Result classification

U0e is **negative for host USB enumeration** but **inconclusive for the actual MUIC register sequence**.

Do not state that the helper failed merely because its userspace log prefix is absent from `/proc/last_kmsg`. The same recovered buffer also contains none of the known-running `a33x-watchdog-v2` or USB-PD loader userspace prefixes. Samsung's fixed 2 MiB `last_kmsg` is binary-corrupted, wrapped, and contains mixed boot data; userspace writes to `/dev/kmsg` were either not preserved or were overwritten.

## Build identity and isolated delta

U0e retained the proven U0d state and added only:

- `i2c_dev` as module 67;
- the static AArch64 `a33x-muic-switch` helper;
- hook `03-a33x-muic-switch.sh`;
- bus 2, address `0x3e`;
- sequence `0x6d=0x13`, `0x70=0x24`, `0x6d=0x17`;
- read-back verification and rollback on partial failure.

The Type-C module remained the exact U0d mask patch:

```text
usb_typec_manager SHA256:
de92f9dc0d29d671bd20f42ad01688e0584eb8e43f6826ff2643e0767c814641
```

The PDIC notifier remained the exact unpatched original:

```text
pdic_notifier_module SHA256:
5442a4cf5d4f12f394e5c3d4f5f01785929427fa71101731ea16bd00d0840161
```

## Reliable runtime evidence

### 1. U0e booted and remained stable

There is no `Kernel panic` or `panic - not syncing` in the recovered candidate segment. The watchdog feeder continued producing kernel keepalive side effects for tens of seconds. The black screen was expected.

### 2. The U0e hook reached `i2c_dev` activation

The recovered kernel log contains:

```text
[    3.327660] ... insmod ... i2c /dev entries driver
```

A second corrupted duplicate of the same kernel-originated module-init line also appears in the mixed Samsung buffer. `i2c_dev` was the U0e-only module and is explicitly activated by hook 03, not by the U0d path. Therefore hook 03 definitely executed at least through successful `i2c_dev` module loading.

This rules out:

- hook 03 missing from the initramfs;
- hook 03 never being invoked;
- failure before its `i2c_dev` activation step;
- `i2c_dev` module absence or basic `insmod` failure.

It does **not** prove that adapter validation, `/dev/i2c-2` creation, slave selection, register reads, writes, or read-back verification succeeded.

### 3. The U0d Type-C path still worked

The real PDIC UFP event reached the Type-C manager around 2.378 seconds. The event was not rejected by the U0d `muic_none` gate in the candidate segment. The delayed notifier replay later ran:

```text
reserve_state_check booting delay finished
reserve_state_check event=vbus(1) enable=1
```

### 4. DWC3 still reached gadget start

At approximately 13.55 seconds the recovered log shows the existing path reaching:

- peripheral attach handling;
- `13200000.usb` platform device;
- `Turn on gadget dwc3-gadget`;
- DWC3 runtime resume and core reset;
- `__dwc3_gadget_start`;
- `dwc3_gadget_run_stop : is_on = 1`;
- then `dwc3_gadget_vbus_draw: suspend`.

### 5. Physical enumeration still did not happen

There is no candidate-side:

```text
dwc3_gadget_reset_interrupt
dwc3_gadget_conndone_interrupt
```

The host log contains no Linux USB gadget enumeration during the U0e observation window. The later Samsung `04e8:685d` appearance belongs to Download Mode during TWRP restoration, not to U0e.

## Unreliable or unavailable evidence

The original collector's `relevant-last-kmsg.txt` was empty because normal `grep` treated the 2 MiB Samsung buffer as binary. Its original summary reported zero MUIC helper messages, but that cannot classify helper execution because all custom userspace `/dev/kmsg` prefixes were absent, including prefixes from hooks whose kernel side effects prove they ran.

The collector has been updated to:

- generate `last_kmsg.sanitized.txt`;
- use binary-safe matching;
- record `i2c /dev entries driver` counts;
- distinguish kernel side effects from absent userspace markers;
- classify userspace kmsg reliability explicitly.

## Current conclusion

The narrow physical-switch hypothesis is **not proven** and **not yet disproven**.

What U0e proved:

1. adding only `i2c_dev` is safe in this boot path;
2. hook 03 executes and reaches `i2c_dev` activation;
3. the system remains stable;
4. the existing Type-C/notifier/DWC3-start path remains intact;
5. no physical USB reset/connect-done occurred.

What remains unknown:

1. whether bus 2 mapped to `13860000.hsi2c` at hook runtime;
2. whether `/sys/class/i2c-dev/i2c-2/dev` appeared;
3. whether `/dev/i2c-2` was created;
4. whether `I2C_SLAVE 0x3e` succeeded;
5. initial values of registers `0x6d` and `0x70`;
6. whether each write/read-back succeeded;
7. whether the final state was `0x17/0x24`;
8. whether rollback was attempted.

## Required next experiment

Do not change the USB data path yet. U0f should change **observability only** and retain the exact U0e functional logic.

First perform read-only TWRP reconnaissance for a safe persistent result location, preferably the `metadata` filesystem:

```sh
adb shell '
set -x
ls -l /dev/block/by-name/metadata 2>&1 || true
blkid /dev/block/by-name/metadata 2>&1 || true
mount | grep -E "[ /]metadata[ /]" || true
ls -la /metadata 2>&1 || true
df -h /metadata 2>&1 || true
'
```

If `metadata` is a normal mounted filesystem with adequate free space, U0f should write the helper's exact output and a stage/result marker to a dedicated path such as:

```text
/metadata/a33x-bringup/u0f-muic-result.txt
```

The file must be synchronized before continuing. TWRP can then retrieve it after restoration. No production Android files should be modified or replaced.

If no safe filesystem is available, use a kernel-originated status encoding rather than relying on userspace `/dev/kmsg`.

No further MUIC, PHY, DWC3, or gadget behavior change should be made until the helper's exact result is recoverable.
