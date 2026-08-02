# Continue the A33 port on another workstation

## 1. Restore the phone first

The last experimental image bootloops in recovery. Avoid repeatedly entering recovery.

Try to boot Android normally. With Android plus Magisk root available, restore exact official TWRP:

```bash
adb push reference/twrp/recovery.img /data/local/tmp/twrp-recovery.img
adb shell 'su -c "dd if=/data/local/tmp/twrp-recovery.img of=/dev/block/by-name/recovery bs=4M && sync"'
adb shell 'su -c "sha256sum /dev/block/by-name/recovery"'
```

Expected recovery SHA256:

```text
414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e
```

If Android is unavailable, use the already-proven Odin4/download-mode route to restore official TWRP or exact stock firmware.

## 2. Clone this repository

```bash
git clone https://github.com/mistrjirka/Linuxa33.git ~/Linuxa33
cd ~/Linuxa33
```

Use `docs/HANDOFF_2026-08-02.md` as the source of truth for the current state.

## 3. Recreate the working directory

The scripts default to `~/a33-port`:

```bash
mkdir -p ~/a33-port/{build,reference/twrp,unpacked}
```

Clone the required tools:

```bash
git clone https://android.googlesource.com/platform/system/tools/mkbootimg \
  ~/a33-port/aosp-mkbootimg

git clone https://android.googlesource.com/platform/external/avb \
  ~/a33-port/aosp-avb
```

Copy `scripts/make-pmos-debug-recovery.sh` from this repository into the working tree when needed.

## 4. Copy non-Git artifacts from the old workstation

Use SSH/rsync, removable storage or another trusted encrypted transfer method. Do not upload private keys or stock firmware publicly.

Minimum useful artifacts:

```text
~/a33-port/reference/twrp/recovery.img
~/a33-port/unpacked/twrp-root/
~/a33-port/unpacked/twrp/kernel
~/a33-port/unpacked/twrp/dtb
~/a33-port/unpacked/twrp/recovery_dtbo
~/a33-port/twrp-kernel.config
```

Optional artifacts that save rebuild time:

```text
~/a33-port/export/
~/a33-port/export-debug/
~/a33-port/build/recovery-before-pmos-test.img
~/.local/var/pmbootstrap/
```

Do not transfer `build/keys/` through an untrusted channel. It is acceptable to generate a new local test key for future experiments.

## 5. Verify copied artifacts

```bash
sha256sum ~/a33-port/reference/twrp/recovery.img
stat -c '%s bytes' ~/a33-port/reference/twrp/recovery.img
```

Expected:

```text
SHA256 414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e
Size   100663296 bytes
```

## 6. Restore local pmaports package definitions

Initialize pmbootstrap edge/OpenRC for `samsung-a33x`, then copy the repository templates into the configured pmaports checkout:

```bash
PMAPORTS="$(pmbootstrap config aports)"
mkdir -p "$PMAPORTS/device/downstream"
cp -a ~/Linuxa33/pmaports/device/downstream/device-samsung-a33x \
  "$PMAPORTS/device/downstream/"
cp -a ~/Linuxa33/pmaports/device/downstream/linux-samsung-a33x \
  "$PMAPORTS/device/downstream/"
```

Populate the intentionally omitted local kernel payloads:

```bash
KPKG="$PMAPORTS/device/downstream/linux-samsung-a33x"
cp ~/a33-port/unpacked/twrp/kernel "$KPKG/Image"
cp ~/a33-port/unpacked/twrp/dtb "$KPKG/samsung-a33x.dtb"
cp ~/a33-port/unpacked/twrp/recovery_dtbo "$KPKG/recovery_dtbo"
```

Recreate `modules.tar.gz` from the extracted TWRP modules and regenerate `modules-initfs` with `scripts/generate-modules-initfs.py`. Then run:

```bash
pmbootstrap checksum linux-samsung-a33x device-samsung-a33x
```

## 7. Do not immediately reflash the failed image

The last generated debug recovery image had:

```text
SHA256 7a9e680ad87121876a1beff396c4dd3cdc8b841fe4bf52e721140a59c8cd036f
Size   100663296 bytes
```

It passed structural validation but bootlooped. Preserve it for analysis, not for another identical flash.

The next task is to restore TWRP and collect pstore/last-kmsg logs, then distinguish bootloader rejection from kernel/userspace failure.
