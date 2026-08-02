# Cold-start handoff: Samsung Galaxy A33 5G postmarketOS recovery bring-up

**Date:** 2026-08-02, Europe/Prague  
**Repository:** `https://github.com/mistrjirka/Linuxa33`  
**Immediate task:** build and test the already-committed **watchdog-v2** initramfs hook. Do not reuse or reflash watchdog-v1.

## 1. How to continue this work

Treat this as a hardware bring-up and debugging task, not as a generic postmarketOS installation. Preserve one-variable-at-a-time experiments, verify every generated image before flashing, keep a known-good TWRP rescue path available, and write down every result with the exact image hash and exact evidence.

The user explicitly does not want shortcuts such as abandoning functionality merely because it is difficult. However, early bring-up must remain staged and safe: first achieve a stable kernel/initramfs and debug transport, then add display and camera modules incrementally.

At the start of the next session:

1. Read this handoff and `docs/WATCHDOG_RESET_ANALYSIS_2026-08-02.md`.
2. Run `git pull --ff-only` in `~/Linuxa33`; the user's local checkout probably still contains watchdog-v1.
3. Do not change the kernel command line, module set, USB configuration, display modules, or camera modules while testing watchdog-v2.
4. Do not flash until both the watchdog hook verifier and the 63-module safety verifier pass.

---

## 2. Target device and immutable facts

### Device

- Samsung Galaxy A33 5G
- Model: `SM-A336B`
- Android product/device: `a33x`
- SoC/platform: Exynos 1280 / `s5e8825`
- Firmware used for this work: `A336BXXSDEYD2`
- CSC: EUX/OXM
- Bootloader unlocked; Knox warranty bit is already set.

### Exact partition map

| Partition | Block device | Size |
|---|---:|---:|
| `boot` | `/dev/block/sda14` | 64 MiB |
| `vendor_boot` | `/dev/block/sda15` | 32 MiB |
| `recovery` | `/dev/block/sda16` | 96 MiB |
| `dtbo` | `/dev/block/sda12` | 8 MiB |
| `vbmeta` | `/dev/block/sda24` | 64 KiB |
| `super` | `/dev/block/sda30` | dynamic partitions |
| `userdata` | `/dev/block/sda36` | user data |

### Critical AVB safety rule

Never flash a homemade standalone `vbmeta` image. A previous rollback-index-zero vbmeta was rejected with:

```text
SW REV CHECK FAIL (VBMETA) DEVICE: 0xD BINARY: 0x0
```

The current recovery experiments work because the bootloader is unlocked and verification is disabled/orange, while the recovery image retains the exact known TWRP layout and receives a local AVB hash footer. This does **not** make a custom standalone vbmeta safe.

Never flash the archived Galaxy A53 image. The A53 package was only a template.

---

## 3. Known-good TWRP and rescue path

### Exact known-good TWRP

```text
Size:   100663296 bytes
SHA256: 414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e
Kernel: 5.10.66-Gabriel260BR-TWRP-ga0103aac9499
Device: a33x
Recovery block device: /dev/block/sda16
```

Image components:

```text
kernel:        31,461,888 bytes
DTB:              241,292 bytes
recovery-DTBO:     992,404 bytes
partition:     100,663,296 bytes
```

Known header/layout:

```text
boot image header: v2
page size: 4096
product: SRPUI23A002
cmdline: androidboot.selinux=permissive bootconfig buildtime_bootconfig=enable loop.max_part=7 buildvariant=eng
trailer: 16 bytes, ASCII SEANDROIDENFORCE
```

### Current phone state at handoff

The phone is currently booted into the restored known-good TWRP. Recovery was verified after restoration:

```text
414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e  /dev/block/by-name/recovery
```

`/sys/fs/pstore` is empty on this kernel; useful crash history is obtained through `/proc/last_kmsg`.

### Rescue files on the workstation

```text
~/a33-port/tools/odin4
~/a33-port/build/rescue/twrp-a33x-restore.img.tar
~/a33-port/reference/twrp/recovery.img
```

The restore tar was verified to contain the exact TWRP hash.

Restore from a fresh Download Mode session:

```sh
sudo ~/a33-port/tools/odin4 -l
sudo ~/a33-port/tools/odin4 \
  -a ~/a33-port/build/rescue/twrp-a33x-restore.img.tar
```

Do not interrupt Odin while it is uploading. Ctrl+C during one restore left the protocol session stale and required unplugging, force-resetting, entering a fresh Download Mode session, and retrying.

After Odin finishes, boot directly into TWRP rather than Android:

1. Hold Side + Volume Down until the display turns black.
2. Immediately switch to Side + Volume Up.
3. Release Side at the Samsung logo but keep Volume Up pressed until TWRP appears.

### ADB quirk in TWRP

On this device, `adb wait-for-device` hangs even though `adb shell` works because TWRP reports the recovery transport state rather than normal `device` state. Do not use it.

Use:

```sh
until adb shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
  sleep 1
done
```

---

## 4. Workstation and pmbootstrap state

### Important paths

```text
Repository:       ~/Linuxa33
Port workspace:   ~/a33-port
pmbootstrap:      ~/.local/bin/pmbootstrap
pmaports:         ~/.local/var/pmbootstrap/cache_git/pmaports
rootfs chroot:    ~/.local/var/pmbootstrap/chroot_rootfs_samsung-a33x
```

### pmbootstrap configuration

```text
Channel: edge
Device: samsung-a33x
Architecture: aarch64
UI: console
Init system: OpenRC
USB profile: developer
Timezone: Europe/Prague
Locale: cs_CZ
Hostname: Roomster
extra_space: 4096 MiB
```

The A33 kernel and device packages already built successfully. The split ext4 rootfs also installed successfully. The debug-shell hook is installed.

### Local proprietary/prebuilt artifacts

These are intentionally not in GitHub and exist under `~/a33-port`:

```text
reference/twrp/recovery.img
unpacked/twrp/kernel
unpacked/twrp/dtb
unpacked/twrp/recovery_dtbo
unpacked/twrp-root/lib/modules/
twrp-kernel.config
```

There are about 315 TWRP kernel modules in the complete extracted tree. The safe initramfs contains only a guarded dependency closure of 63 modules.

### Secret/private material

Do not commit:

```text
~/a33-port/build/keys/a33x-recovery-test-rsa4096.pem
```

It is only a local test key and can be regenerated. Raw logs also contain the phone's serial number and other device identifiers; keep them private or sanitize them before publishing.

---

## 5. Recovery image construction

Builder:

```text
~/Linuxa33/scripts/make-pmos-debug-recovery.sh
```

It:

1. verifies the initramfs module safety gate;
2. extracts the exact TWRP mkbootimg arguments;
3. proves an exact TWRP round-trip reconstruction;
4. replaces only the ramdisk;
5. preserves the TWRP kernel, DTB, recovery-DTBO, addresses, command line and 16-byte Samsung trailer;
6. adds and verifies a local AVB SHA256/RSA4096 hash footer;
7. produces an exact 96 MiB recovery image.

The builder currently checks the module safety gate but does not itself invoke the watchdog-hook verifier. Run both verifiers manually before building.

---

## 6. First confirmed failure: all TWRP modules in the initramfs

An older postmarketOS debug initramfs included all approximately 315 TWRP modules. `udevd` automatically loaded the MIPI PHY/display stack and the kernel panicked roughly 1.5 seconds after boot:

```text
Unexpected kernel BRK exception at EL1
pc : exynos_mipi_phy_probe+0x4e0/0x4e8 [phy_exynos_mipi]
Kernel panic - not syncing: BRK handler: Fatal exception
```

The triggering hardware node was approximately:

```text
dphy_m4s0_dsim0@0x11860000
```

This proved:

- the locally built recovery was accepted far enough to boot the kernel and initramfs;
- AVB was not the immediate failure;
- loading all TWRP modules blindly was unsafe;
- `phy_exynos_mipi` was a confirmed panic source.

### Mitigation now in the repository

The guarded module generator:

```text
scripts/generate-modules-initfs.py
scripts/prepare-safe-module-packages.sh
config/modules-initfs-safe-debug.seeds
config/modules-initfs-blocklist.glob
scripts/verify-initramfs-safety.py
```

It generated a 63-module dependency closure and excluded MIPI, display, panel and camera modules. The device package also installs a modprobe blacklist for these modules.

Do not bypass the blocklist, increase the 128-module limit, or copy `modules.load.recovery` wholesale.

### Camera/display implications

The full root filesystem package still contains the complete module tree, including camera-related modules. They are intentionally absent/blocked in early initramfs experiments. Display and cameras are later milestones after stable boot and debug access.

---

## 7. Guarded 63-module image result: watchdog reset

The first guarded recovery avoided the `phy_exynos_mipi` panic. It reached:

- `/init` and `init_2nd.sh`;
- UFS and expected `sda` partitions;
- DWC3/USB gadget probing;
- creation of `usb0`.

The bootloader starts cluster-0 watchdog before Linux:

```text
kernel_watchdog_start: Start Watchdog 60 sec...
```

The guarded boot stopped around the original 60-second deadline. The next boot recorded:

```text
rst_stat:0x1000000 / CL0_WDTRESET
Watchdog or Warm Reset Detected.
```

Therefore the next primary problem became early userspace watchdog feeding, not the original MIPI panic.

---

## 8. Exact TWRP watchdog behavior

Live inspection of working TWRP showed:

```text
/system/bin/watchdogd 10 20
```

Open file descriptor:

```text
fd 4 -> /dev/watchdog
```

Device nodes:

```text
/dev/watchdog   major 10, minor 130
/dev/watchdog0  major 242, minor 0
/dev/watchdog1  major 242, minor 1
```

Exact required sysfs device:

```text
/sys/class/watchdog/watchdog0/device
  -> /sys/devices/platform/10060000.watchdog_cl0
```

TWRP starts it from:

```text
init.recovery.s5e8825.rc
```

Relevant lines:

```text
start watchdogd
service watchdogd /system/bin/watchdogd 10 20
```

The Android binary is dynamically linked against `/system/bin/linker64`, so copying it alone into Alpine/postmarketOS is not appropriate.

TWRP command line includes:

```text
s3c2410_wdt.tmr_atboot=1
sec_watchdog.sec_pet=5
```

---

## 9. Watchdog-v1 experiment and evidence

### Candidate

```text
SHA256: a77a44be848b4a22dcdb56699e4de04746b0ac89d39d414f33c19720273fc782
Final size: 100663296 bytes
Compressed initramfs: 11358903 bytes
Bootloader ramdisk size marker: 0x00ad52b7
```

Local copy may still exist at:

```text
~/a33-port/build/candidates/a33x-watchdog-v1-recovery.img
```

Do not flash it again.

### What v1 attempted

The first mkinitfs hook waited for `/dev/watchdog`, opened it, and wrote every eight seconds.

### Exact result from the circular `last_kmsg`

Use the exact ramdisk-size marker `0x00ad52b7` to identify the v1 boot among historical boots in the circular log.

For that candidate:

1. It reached `init_2nd.sh` and UFS discovery.
2. There was a scheduler warning around 8.18 seconds in `update_load_avg`, but execution continued, so it was not the final reset cause.
3. DWC3 gadget activity continued around 49 seconds.
4. The immediately following boot recorded `CL0_WDTRESET`.
5. There were no `a33x-watchdog` startup/ping messages in the candidate segment.

The same circular log contains an older DWC3 `Internal error: Oops` and kernel panic around 1.45 seconds. Do not attribute that older historical boot to watchdog-v1.

Captured archive on the user's machine:

```text
~/a33-port/build/watchdog-v1-failure.tar.gz
```

TWRP was restored and verified after capture.

### Likely v1 defect

The vendor kernel lacks `CONFIG_DEVTMPFS` and `CONFIG_DEVTMPFS_MOUNT`. Device nodes cannot be assumed to appear automatically. The postmarketOS initramfs likely had the canonical `/dev/watchdog0` or sysfs registration but not Android's legacy `/dev/watchdog` alias. Version 1 therefore probably waited for the wrong node and never fed cluster 0.

This remains a strongly supported diagnosis, but watchdog-v2 is the experiment that must confirm it.

---

## 10. Current GitHub state: watchdog-v2 is committed but not yet tested

GitHub `main` now contains watchdog-v2. The latest relevant files are:

```text
pmaports/main/postmarketos-mkinitfs-hook-a33x-watchdog/APKBUILD
pmaports/main/postmarketos-mkinitfs-hook-a33x-watchdog/01-a33x-watchdog.sh
scripts/verify-initramfs-watchdog-hook.sh
docs/WATCHDOG_RESET_ANALYSIS_2026-08-02.md
```

The hook package is now:

```text
pkgver=2
```

### Watchdog-v2 behavior

Version 2:

1. waits for `/dev/watchdog0`;
2. reads major/minor from `/sys/class/watchdog/watchdog0/dev`;
3. creates `/dev/watchdog0` using `mknod` if necessary;
4. verifies it is a character device;
5. uses legacy `/dev/watchdog` only as a fallback;
6. creates `/dev/kmsg` as character device `1:11` if absent;
7. opens the watchdog and keeps the file descriptor alive;
8. writes every 8 seconds;
9. emits `a33x-watchdog-v2` startup and per-ping messages;
10. stores the feeder PID in `/run/a33x-watchdog.pid`.

No watchdog-v2 image has been built or flashed yet at the time of this handoff.

---

## 11. Immediate next commands: build watchdog-v2

The user's local `~/Linuxa33` and pmaports copy probably still contain v1. Start with:

```sh
cd ~/Linuxa33
git pull --ff-only

PMAPORTS="$(pmbootstrap config aports)"
HOOKPKG="postmarketos-mkinitfs-hook-a33x-watchdog"

rm -rf "$PMAPORTS/main/$HOOKPKG"
cp -a \
  "$HOME/Linuxa33/pmaports/main/$HOOKPKG" \
  "$PMAPORTS/main/"
```

Rebuild the package:

```sh
pmbootstrap checksum "$HOOKPKG"

pmbootstrap build \
  --arch aarch64 \
  --force \
  "$HOOKPKG"
```

Force replacement of the installed hook and rebuild initramfs:

```sh
pmbootstrap initfs hook_del a33x-watchdog
pmbootstrap initfs hook_add a33x-watchdog
pmbootstrap initfs hook_ls

rm -rf ~/a33-port/export-debug
pmbootstrap export ~/a33-port/export-debug
```

Required offline verification:

```sh
cd ~/Linuxa33

bash scripts/verify-initramfs-watchdog-hook.sh \
  ~/a33-port/export-debug/initramfs

python3 scripts/verify-initramfs-safety.py \
  --initramfs ~/a33-port/export-debug/initramfs
```

The watchdog verifier must explicitly report v2, `/dev/watchdog0`, sysfs resolution, node creation support, `/dev/kmsg`, and the 8-second interval. The safety verifier must still report 63 modules and no blocked modules.

Inspect the actual embedded hook:

```sh
gzip -dc ~/a33-port/export-debug/initramfs |
  cpio -itv 2>/dev/null |
  grep 'hooks/01-a33x-watchdog.sh'

gzip -dc ~/a33-port/export-debug/initramfs |
  cpio -i --to-stdout hooks/01-a33x-watchdog.sh 2>/dev/null |
  grep -E 'a33x-watchdog-v2|watchdog0/dev|mknod /dev/watchdog0'
```

Also record the exact compressed initramfs size and hexadecimal form before building, so the next boot can be isolated in the circular log:

```sh
INITRAMFS="$HOME/a33-port/export-debug/initramfs"
SIZE="$(stat -Lc %s "$INITRAMFS")"
printf 'initramfs decimal: %s\ninitramfs hex: 0x%08x\n' "$SIZE" "$SIZE" |
  tee ~/a33-port/build/watchdog-v2-initramfs-size.txt
```

---

## 12. Build and preserve watchdog-v2 recovery

Only after both verifiers pass:

```sh
cd ~/Linuxa33

ROOT="$HOME/a33-port" \
LINUXA33_REPO="$HOME/Linuxa33" \
bash scripts/make-pmos-debug-recovery.sh 2>&1 |
  tee ~/a33-port/build/pmos-watchdog-v2-recovery-build.log
```

Preserve it under a versioned name immediately because the builder overwrites its output directory:

```sh
mkdir -p ~/a33-port/build/candidates

cp --reflink=auto \
  ~/a33-port/build/pmos-debug-recovery/recovery.img \
  ~/a33-port/build/candidates/a33x-watchdog-v2-recovery.img

V2_IMG="$HOME/a33-port/build/candidates/a33x-watchdog-v2-recovery.img"
stat -Lc '%s bytes' "$V2_IMG"
sha256sum "$V2_IMG" |
  tee ~/a33-port/build/candidates/a33x-watchdog-v2-recovery.sha256
```

Required size:

```text
100663296 bytes
```

Inspect final validation and do not proceed on any failure:

```sh
grep -A15 '=== FINAL VALIDATION ===' \
  ~/a33-port/build/pmos-watchdog-v2-recovery-build.log
```

---

## 13. Controlled watchdog-v2 flash/test procedure

The phone should still be in known-good TWRP.

Confirm usable ADB and known TWRP before flashing:

```sh
until adb shell true >/dev/null 2>&1; do sleep 1; done

adb shell '
uname -r
getprop ro.product.device
sha256sum /dev/block/by-name/recovery
'
```

Expected current recovery hash:

```text
414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e
```

Push, hash, write only recovery, and read it back:

```sh
V2_IMG="$HOME/a33-port/build/candidates/a33x-watchdog-v2-recovery.img"
LOCAL_HASH="$(sha256sum "$V2_IMG" | awk '{print $1}')"

adb push "$V2_IMG" /tmp/a33x-watchdog-v2-recovery.img

REMOTE_HASH="$(
  adb shell 'sha256sum /tmp/a33x-watchdog-v2-recovery.img' |
  tr -d '\r' |
  awk '{print $1}'
)"

test "$LOCAL_HASH" = "$REMOTE_HASH"

adb shell '
set -e
dd if=/tmp/a33x-watchdog-v2-recovery.img \
   of=/dev/block/by-name/recovery \
   bs=4M
sync
'

FLASHED_HASH="$(
  adb shell 'sha256sum /dev/block/by-name/recovery' |
  tr -d '\r' |
  awk '{print $1}'
)"

test "$LOCAL_HASH" = "$FLASHED_HASH"
```

Start host USB logging before rebooting:

```sh
sudo dmesg -wH |
  tee ~/a33-port/build/watchdog-v2-host-dmesg.log
```

In the main terminal:

```sh
date -Ins |
  tee ~/a33-port/build/watchdog-v2-start-time.txt

adb reboot recovery
```

Observe for at least 100 seconds. A black screen is expected because display modules are intentionally blocked. Determine whether the Samsung logo repeats. Absence of USB is not proof of a crash because USB networking is independently broken.

### Watchdog-v2 success criteria

- no repeated Samsung logo/reset at approximately the 60-second deadline;
- phone remains powered for more than 100 seconds;
- later `last_kmsg` contains `a33x-watchdog-v2` startup and several ping messages;
- following boot does not report `CL0_WDTRESET`.

---

## 14. If watchdog-v2 still bootloops

Restore TWRP through a fresh Download Mode session, boot directly into TWRP, then capture before booting Android:

```sh
until adb shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
  sleep 1
done

LOGDIR="$HOME/a33-port/build/watchdog-v2-failure"
mkdir -p "$LOGDIR"

adb exec-out \
  'cat /proc/last_kmsg 2>/dev/null || true' \
  > "$LOGDIR/last_kmsg.txt"

adb exec-out dmesg \
  > "$LOGDIR/twrp-dmesg.txt"

adb shell '
echo "=== recovery hash ==="
sha256sum /dev/block/by-name/recovery

echo "=== pstore ==="
ls -la /sys/fs/pstore 2>/dev/null || true
' > "$LOGDIR/twrp-state.txt"

adb pull /sys/fs/pstore "$LOGDIR/pstore" 2>&1 |
  tee "$LOGDIR/pstore-pull.txt"
```

Use the recorded exact v2 ramdisk size to isolate the correct boot in circular `last_kmsg`.

Interpretation:

### A. No `a33x-watchdog-v2` messages

The hook likely did not execute, executed before `/sys` was available, or failed before it could create/write `/dev/kmsg`. Inspect postmarketOS mkinitfs hook execution order and the generated `/init`/`init_2nd.sh`; do not assume package presence means runtime execution.

### B. Startup/error messages but no pings

Use the exact error to fix node creation, open permissions, shell behavior, or process lifetime. Preserve the one-variable experiment.

### C. Repeated ping messages but still `CL0_WDTRESET`

Writing to `/dev/watchdog0` is not reproducing the vendor daemon's effective behavior. Investigate the exact driver interface and TWRP `watchdogd` behavior, including whether the legacy misc node performs different operations, whether an ioctl sets timeout, and whether the vendor daemon also handles a second watchdog. Do not blindly add unrelated modules.

### D. A new kernel panic occurs before the watchdog deadline

Treat it independently. Attribute it only to the exact v2 boot segment using the recorded ramdisk size. Do not confuse it with historical panics in the circular buffer.

### Next fallback experiment only after v2 is understood

A debug-only command-line experiment was proposed but has not been run:

```text
s3c2410_wdt.tmr_atboot=0 sec_watchdog.sec_pet=0
```

Use this only as a separate controlled experiment if the exact watchdog node feeder cannot work. Do not combine it with other changes.

---

## 15. USB is a separate unresolved problem

Even in boots that reach `init_2nd.sh`, the host does not enumerate a usable postmarketOS USB network device.

Observed kernel behavior includes:

- `usb0` creation;
- DWC3 gadget pull-up attempts;
- `current_dr_role = 255`;
- timeout waiting for USB SETUP phase;
- DWC3 runtime suspend/resume and USB PHY LDO changes.

Current deviceinfo requests:

```text
deviceinfo_usb_idVendor="0x04e8"
deviceinfo_usb_idProduct="0x6860"
deviceinfo_usb_network_function="ncm.usb0"
deviceinfo_usb_network_udc="13200000.dwc3"
```

Do not debug USB and watchdog in the same image. After watchdog survival is proven, investigate DWC3 role/runtime-PM and UDC timing separately.

An older historical boot in the circular log contains a DWC3 kernel panic near 1.45 seconds. It is not the watchdog-v1 result and must not be used as evidence for the current candidate without matching the exact ramdisk marker.

---

## 16. Kernel limitations that remain long-term defects

The prebuilt TWRP kernel configuration lacks at least:

```text
CONFIG_DEVTMPFS
CONFIG_DEVTMPFS_MOUNT
CONFIG_CGROUP_PIDS
CONFIG_IPC_NS
CONFIG_PID_NS
CONFIG_FHANDLE
```

OpenRC was deliberately selected instead of systemd because the vendor kernel lacks several requirements. The missing devtmpfs options directly complicate early device-node creation. A rebuilt/corrected kernel remains a likely long-term requirement even if the prebuilt kernel reaches a usable console.

Important enabled areas include UFS, ext4, loop, USB configfs and NCM/ECM/RNDIS/EEM support.

---

## 17. Evidence classification

### Proven

- Exact TWRP image, hash, kernel, DTB and recovery-DTBO.
- Custom recovery images reach and run the preserved TWRP kernel/initramfs.
- Loading the broad 315-module set triggers a repeatable `phy_exynos_mipi` BRK panic.
- The guarded 63-module set excludes that immediate panic path.
- Guarded boots reach `init_2nd.sh`, UFS and USB gadget code.
- The bootloader starts a 60-second CL0 watchdog.
- Guarded/watchdog-v1 boots are followed by `CL0_WDTRESET`.
- TWRP runs `/system/bin/watchdogd 10 20` with `/dev/watchdog` open.
- `/dev/watchdog0` maps to `10060000.watchdog_cl0`.
- Watchdog-v1's exact candidate segment has no `a33x-watchdog` messages and ends before a CL0 reset.
- TWRP has been restored and the exact recovery hash is currently present.

### Strongly supported but still to be experimentally confirmed

- Watchdog-v1 failed because it depended on legacy `/dev/watchdog`, which was not created in postmarketOS initramfs.
- Watchdog-v2's sysfs-based `/dev/watchdog0` creation and feeding will prevent CL0 expiry.
- USB failure is mainly DWC3 role/runtime-PM/UDC timing rather than missing NCM support.

### Not yet proven

- Whether watchdog-v2 runs at the necessary point in mkinitfs startup.
- Whether periodic writes to `/dev/watchdog0` exactly reproduce TWRP's daemon behavior.
- Whether the command-line watchdog-disable parameters are honored by this vendor kernel.
- Working display, camera, sound, modem, Wi-Fi, suspend or normal rootfs boot.

---

## 18. Files to read first

Repository:

```text
docs/HANDOFF_2026-08-02.md
docs/BOOTLOOP_ANALYSIS_2026-08-02.md
docs/SAFE_NEXT_BOOT.md
docs/WATCHDOG_RESET_ANALYSIS_2026-08-02.md
config/modules-initfs-blocklist.glob
config/modules-initfs-safe-debug.seeds
scripts/generate-modules-initfs.py
scripts/prepare-safe-module-packages.sh
scripts/verify-initramfs-safety.py
scripts/verify-initramfs-watchdog-hook.sh
scripts/make-pmos-debug-recovery.sh
pmaports/main/postmarketos-mkinitfs-hook-a33x-watchdog/
pmaports/device/downstream/device-samsung-a33x/
pmaports/device/downstream/linux-samsung-a33x/
```

Private local evidence:

```text
~/a33-port/build/watchdog-v1-failure.tar.gz
~/a33-port/build/safe-test-logs-2026-08-02/
~/a33-port/build/twrp-watchdog-inspection.txt
~/a33-port/build/twrp-watchdog-init-lines.txt
~/a33-port/build/twrp-watchdog-files.txt
```

---

## 19. Concise status for the next LLM

The project has progressed from an immediate `phy_exynos_mipi` panic to a guarded 63-module initramfs that reaches userspace and UFS. The remaining repeatable failure is CL0 watchdog expiry. A first feeder (`watchdog-v1`) failed, likely because it waited for the Android legacy `/dev/watchdog` alias. GitHub now contains `watchdog-v2`, which resolves the exact CL0 watchdog from sysfs, creates `/dev/watchdog0`, creates `/dev/kmsg`, and logs every ping. The phone is safely back in exact TWRP. The next action is to pull GitHub, rebuild/reinstall/export watchdog-v2, verify it offline, build a versioned recovery, flash only recovery with read-back verification, observe for 100 seconds, and capture `last_kmsg` if it still resets.
