# Samsung A33 candidate flashing and runtime-log workflow

This document defines the default workflow for every recovery/initramfs experiment on the Samsung Galaxy A33 5G (`SM-A336B`, `a33x`). It exists to prevent confusion between Android, TWRP, a Linux candidate, and Samsung Download Mode.

## 1. Mode map

| Current phone mode | Expected display | Host transport | What works |
|---|---|---|---|
| Android | Android UI | ADB | `adb reboot recovery`, `adb reboot download` |
| TWRP | TWRP UI | ADB | push candidate, `dd` recovery partition, verify hashes, collect logs |
| Linux candidate with working USB | usually black during bring-up | USB gadget/network, possibly telnet | live debugging and log collection |
| Linux candidate without USB | black | none | wait for experiment, then enter Download Mode physically |
| Samsung Download Mode | blue Download screen | Odin protocol | flash a recovery tar; ADB does not work |

## 2. Default candidate test path: TWRP and ADB

Do not use Odin as the normal candidate flasher. Odin is the rescue path.

From Android:

```sh
adb reboot recovery
```

Wait for TWRP with a real shell probe. Do not use `adb wait-for-device`, because this TWRP may expose a recovery transport state while `adb shell` already works.

```sh
until adb shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done
```

Verify the candidate locally, push it to TWRP, verify the uploaded copy, write only the recovery partition, synchronize, and verify the actual partition before rebooting:

```sh
CANDIDATE=/path/to/recovery.img
EXPECTED_SHA256=<exact-candidate-sha256>

[ "$(stat -Lc '%s' "$CANDIDATE")" = 100663296 ]
[ "$(sha256sum "$CANDIDATE" | awk '{print $1}')" = "$EXPECTED_SHA256" ]

adb push "$CANDIDATE" /tmp/candidate-recovery.img
adb shell 'stat -c "uploaded_size=%s" /tmp/candidate-recovery.img; sha256sum /tmp/candidate-recovery.img'

adb shell '
set -e
dd if=/tmp/candidate-recovery.img of=/dev/block/by-name/recovery bs=4M
sync
'

adb shell 'sha256sum /dev/block/by-name/recovery'
```

Only when the partition hash matches the candidate exactly:

```sh
adb reboot recovery
```

This boots the newly written Linux recovery directly. A black screen is expected during early bring-up.

## 3. Candidate observation window

Keep USB connected. Unless the experiment defines a different interval, allow at least 90 seconds before declaring a no-USB result. This gives the watchdog feeder, delayed notifier replay, USB role switch, and helper hooks time to run.

Host-side evidence can be captured concurrently:

```sh
sudo journalctl -kf -o short-monotonic | tee ~/a33-port/build/candidate-host-kernel-live.txt
```

```sh
while true; do
    printf '\n=== %s ===\n' "$(date -Ins)"
    lsusb
    ip -br addr
    sleep 1
done | tee ~/a33-port/build/candidate-host-usb-live.txt
```

If USB networking appears, inspect `172.16.42.1` and the initramfs debug shell. If there is no USB transport, do not keep rebooting the candidate: restore TWRP and collect the previous boot log.

## 4. Emergency path after a no-USB candidate

When the candidate has no ADB or USB gadget, software reboot commands are unavailable. Download Mode must be entered physically.

1. Disconnect USB.
2. Hold Side + Volume Down until the phone force-resets and the display goes black.
3. Release those buttons immediately.
4. Hold Volume Up + Volume Down.
5. While holding both volume buttons, connect the USB cable directly to the computer.
6. At the Download Mode warning screen, release the buttons and press Volume Up to continue.

Verify Odin sees the device:

```sh
sudo ~/a33-port/tools/odin4 -l
```

Restore the exact known-good TWRP tar:

```sh
sudo ~/a33-port/tools/odin4 \
    -a ~/a33-port/build/rescue/twrp-a33x-restore.img.tar
```

Do not interrupt Odin or disconnect the cable during transfer.

The rescue tar must contain exactly:

```text
recovery.img size:   100663296 bytes
recovery.img SHA256: 414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e
```

## 5. Boot TWRP immediately after Odin

After Odin completes, boot TWRP before Android. Android or another candidate boot may overwrite or replace the previous-boot evidence needed from `/proc/last_kmsg`.

1. Hold Side + Volume Down until the screen turns black.
2. Immediately switch to Side + Volume Up.
3. Release Side at the Samsung logo.
4. Continue holding Volume Up until TWRP starts.

Then wait for TWRP ADB with the shell probe shown above.

## 6. First action in TWRP: collect previous-boot logs

Before rebooting anywhere else, run:

```sh
cd ~/Linuxa33
bash scripts/collect-a33-previous-boot.sh u0e
```

The script saves:

- `/proc/last_kmsg` from the failed Linux candidate;
- current TWRP `dmesg`, properties, command line, and pstore state;
- the restored recovery-partition hash;
- focused USB, Type-C, MUIC, I2C, DWC3, gadget, watchdog, and reset lines;
- relevant build manifests and host-side logs when present;
- a compressed result archive under `~/a33-port/build/runtime-results/`.

Do not boot Android until this archive exists and `/proc/last_kmsg` is non-empty.

## 7. Current U0e experiment logic

U0d proved the following path works:

- `s2mu106_usbpd` is explicitly loaded without the unsafe full MUIC soft-dependency closure;
- a real UFP attach reaches the Type-C manager;
- the exact `usb_typec_manager.ko` mask patch allows `muic_none` only for the real UFP event;
- delayed notifier replay reaches `exynos_set_peripheral`, DWC3 runtime resume, gadget start, and run/stop.

U0d still did not produce a physical host reset/connect-done event.

U0e retains U0d and adds only:

- `i2c_dev` as the 67th initramfs module;
- a static AArch64 helper;
- a guarded initramfs hook that accesses I2C bus 2, address `0x3e` only when `2-003e` is unowned;
- the Samsung-observed MUIC USB data-switch sequence:
  - register `0x6d` = `0x13`;
  - register `0x70` = `0x24`;
  - register `0x6d` = `0x17`;
- read-back verification and best-effort rollback after partial failure.

The first U0e boot showed a black screen and no visible host USB enumeration. That observation alone does not determine whether the helper succeeded. `/proc/last_kmsg` must distinguish among:

1. helper could not create or open `/dev/i2c-2`;
2. adapter identity did not match `13860000.hsi2c`;
3. address `2-003e` was unexpectedly owned;
4. SMBus read/write or read-back verification failed;
5. the MUIC sequence completed successfully but DWC3 still received no physical reset/connect-done event.

No further design change should be made until the U0e previous-boot log is collected.

## 8. Non-negotiable rules

- Default testing uses TWRP + ADB + `dd`; Odin is rescue only.
- Never write boot, vendor_boot, vbmeta, dtbo, super, or userdata as part of a recovery experiment.
- Never flash a standalone homemade vbmeta image.
- Verify candidate hash locally, uploaded hash, and recovery-partition hash.
- Preserve one variable per candidate and record exact hashes.
- After a no-USB candidate, collect `/proc/last_kmsg` immediately after TWRP restoration.
- Do not label a runtime result complete until safety gates and runtime evidence have actually passed.
