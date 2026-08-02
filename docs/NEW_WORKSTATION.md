# Continue the A33 port on another workstation

## 1. Current phone state

Exact official TWRP has been restored successfully after the failed recovery
test. Verify it whenever recovery is changed:

```text
SHA256 414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e
Size   100663296 bytes
```

The failed postmarketOS image did boot Linux. It then panicked while `udevd`
loaded `phy_exynos_mipi.ko`. See:

- `docs/BOOTLOOP_ANALYSIS_2026-08-02.md`
- `docs/SAFE_NEXT_BOOT.md`

Do not recreate the old 315-module initramfs.

## 2. Clone this repository

```bash
git clone https://github.com/mistrjirka/Linuxa33.git ~/Linuxa33
cd ~/Linuxa33
git pull --ff-only
```

Use `docs/HANDOFF_2026-08-02.md` for full history and
`docs/SAFE_NEXT_BOOT.md` for the next experiment.

## 3. Recreate the working directory and tools

The scripts default to `~/a33-port`:

```bash
mkdir -p ~/a33-port/{build,reference/twrp,unpacked}
```

Clone the required Android image tools:

```bash
git clone https://android.googlesource.com/platform/system/tools/mkbootimg \
  ~/a33-port/aosp-mkbootimg

git clone https://android.googlesource.com/platform/external/avb \
  ~/a33-port/aosp-avb
```

Host packages needed by the guarded workflow include Python, kmod, gzip, cpio,
OpenSSL and the normal pmbootstrap dependencies.

## 4. Copy non-Git artifacts from the old workstation

Use SSH/rsync, removable storage or another trusted encrypted transfer method.
Do not upload private keys or stock firmware publicly.

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

Do not transfer or commit `build/keys/`.

Verify copied TWRP:

```bash
sha256sum ~/a33-port/reference/twrp/recovery.img
stat -c '%s bytes' ~/a33-port/reference/twrp/recovery.img
```

## 5. Initialize pmbootstrap

Use edge, `samsung-a33x`, console and OpenRC. Never select or flash an A53
image. After initialization:

```bash
pmbootstrap status
```

Expected essentials:

```text
Device: samsung-a33x
UI: console
systemd: no
```

## 6. Install the repository package templates

```bash
cd ~/Linuxa33

PMAPORTS="$(pmbootstrap config aports)"
mkdir -p "$PMAPORTS/device/downstream"

rm -rf \
  "$PMAPORTS/device/downstream/device-samsung-a33x" \
  "$PMAPORTS/device/downstream/linux-samsung-a33x"

cp -a pmaports/device/downstream/device-samsung-a33x \
  "$PMAPORTS/device/downstream/"

cp -a pmaports/device/downstream/linux-samsung-a33x \
  "$PMAPORTS/device/downstream/"
```

Populate the omitted local kernel payloads:

```bash
KPKG="$PMAPORTS/device/downstream/linux-samsung-a33x"

cp ~/a33-port/unpacked/twrp/kernel \
  "$KPKG/Image"
cp ~/a33-port/unpacked/twrp/dtb \
  "$KPKG/samsung-a33x.dtb"
cp ~/a33-port/unpacked/twrp/recovery_dtbo \
  "$KPKG/recovery_dtbo"
```

## 7. Generate modules using the guarded workflow

Run only:

```bash
cd ~/Linuxa33
bash scripts/prepare-safe-module-packages.sh
```

This computes a dependency closure from the safe seed profile, rejects the
known MIPI/display/camera stack and refuses unexpectedly large closures.

The old invocation that supplied TWRP's complete `modules.load.recovery` is now
rejected by default. Do not use `--unsafe-use-full-twrp-list` for the next boot.

Then:

```bash
pmbootstrap checksum \
  linux-samsung-a33x \
  device-samsung-a33x
```

## 8. Build and verify before constructing recovery

Follow `docs/SAFE_NEXT_BOOT.md`. In particular:

```bash
python3 ~/Linuxa33/scripts/verify-initramfs-safety.py \
  --initramfs ~/a33-port/export-debug/initramfs
```

The recovery builder now runs the same check automatically and fails closed
when:

- `phy_exynos_mipi` or another blocked module is present;
- more than 128 modules are embedded;
- the verifier or blocklist is missing.

Run the builder directly from the repository:

```bash
ROOT="$HOME/a33-port" \
LINUXA33_REPO="$HOME/Linuxa33" \
bash ~/Linuxa33/scripts/make-pmos-debug-recovery.sh
```

Do not use the old generated image with SHA256
`7a9e680ad87121876a1beff396c4dd3cdc8b841fe4bf52e721140a59c8cd036f`.

## 9. Remaining kernel defect

The prebuilt TWRP kernel lacks `CONFIG_DEVTMPFS` and
`CONFIG_DEVTMPFS_MOUNT`. Removing the crashing module should allow the next
stage of diagnosis, but a proper port still needs either:

1. a rebuilt A33/TWRP kernel with the required postmarketOS/OpenRC options; or
2. a hybrid TWRP first-stage ramdisk that preserves its static `/dev` and
   controlled module loading before launching postmarketOS userspace.
