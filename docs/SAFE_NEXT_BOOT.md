# Safe next recovery test after the 2026-08-02 bootloop

## Why the previous image rebooted

The recovery image was accepted by the bootloader and started Linux. The kernel
then panicked approximately 1.5 seconds into the postmarketOS initramfs while
`udevd` auto-loaded `phy-exynos-mipi.ko`:

```text
pc : exynos_mipi_phy_probe+0x4e0/0x4e8 [phy_exynos_mipi]
Kernel panic - not syncing: BRK handler: Fatal exception
```

The previous `modules-initfs` contained all 315 entries from TWRP's
`modules.load.recovery`. Do not recreate that list.

## Safety changes now enforced by this repository

1. `scripts/generate-modules-initfs.py` defaults to a small dependency-closed
   seed profile and rejects MIPI/display/camera modules.
2. Supplying the old `--load-list` argument now fails unless the explicit
   `--unsafe-use-full-twrp-list` flag is also supplied.
3. The generator refuses more than 128 modules by default.
4. `scripts/verify-initramfs-safety.py` scans the completed initramfs.
5. `scripts/make-pmos-debug-recovery.sh` runs that verifier and refuses to
   create a recovery image when the safety files are missing or a blocked
   module is present.
6. The device package installs a modprobe rule that blocks the confirmed
   panic module and related display modules.

These checks prevent the exact known failure. They do not prove the next image
will boot; the downstream kernel still lacks `CONFIG_DEVTMPFS`.

## 1. Restore the repository and pmaports packages

```bash
git clone https://github.com/mistrjirka/Linuxa33.git ~/Linuxa33
cd ~/Linuxa33
git pull --ff-only

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

Populate the intentionally omitted local kernel files:

```bash
KPKG="$PMAPORTS/device/downstream/linux-samsung-a33x"

cp ~/a33-port/unpacked/twrp/kernel \
  "$KPKG/Image"
cp ~/a33-port/unpacked/twrp/dtb \
  "$KPKG/samsung-a33x.dtb"
cp ~/a33-port/unpacked/twrp/recovery_dtbo \
  "$KPKG/recovery_dtbo"
```

## 2. Generate the safe module closure

```bash
cd ~/Linuxa33
bash scripts/prepare-safe-module-packages.sh
```

This creates:

```text
linux-samsung-a33x/modules.tar.gz
device-samsung-a33x/modules-initfs
~/a33-port/build/modules-initfs-safe.report.txt
```

Inspect the report and count:

```bash
DPKG="$PMAPORTS/device/downstream/device-samsung-a33x"

wc -l "$DPKG/modules-initfs"
cat ~/a33-port/build/modules-initfs-safe.report.txt

grep -Ei \
  'phy[-_]exynos[-_]mipi|exynos[-_]drm|mcd[-_]panel|fimc[-_]is' \
  "$DPKG/modules-initfs" &&
  {
    echo "Unsafe module found; stop"
    exit 1
  }
```

Do not increase the 128-module guard merely to make the build pass. Review
which seed introduced the large dependency chain.

## 3. Rebuild packages and initramfs

```bash
pmbootstrap checksum \
  linux-samsung-a33x \
  device-samsung-a33x

pmbootstrap shutdown

pmbootstrap build --arch aarch64 --force \
  linux-samsung-a33x

pmbootstrap build --arch aarch64 --force \
  device-samsung-a33x

pmbootstrap install \
  --split \
  --filesystem ext4 \
  --no-sparse

pmbootstrap initfs hook_add debug-shell

rm -rf ~/a33-port/export-debug
pmbootstrap export ~/a33-port/export-debug
```

## 4. Verify the finished initramfs before image construction

```bash
cd ~/Linuxa33

python3 scripts/verify-initramfs-safety.py \
  --initramfs ~/a33-port/export-debug/initramfs
```

Expected properties:

- fewer than or equal to 128 embedded modules;
- no `phy_exynos_mipi`;
- no Exynos DRM/panel/camera stack;
- exit code 0.

Also confirm the panic module is absent manually:

```bash
gzip -dc ~/a33-port/export-debug/initramfs |
  cpio -it 2>/dev/null |
  grep -Ei 'phy[-_]exynos[-_]mipi|exynos[-_]drm|mcd[-_]panel|fimc[-_]is' &&
  {
    echo "Unsafe module embedded; stop"
    exit 1
  }
```

## 5. Build the guarded recovery image

Run the builder from this repository instead of copying an old local version:

```bash
cd ~/Linuxa33

ROOT="$HOME/a33-port" \
LINUXA33_REPO="$HOME/Linuxa33" \
bash scripts/make-pmos-debug-recovery.sh |
  tee ~/a33-port/build/pmos-safe-debug-recovery-build.log
```

The first section must say:

```text
=== Fail-closed initramfs safety check ===
Safety check passed: no blocked MIPI/display/camera modules found
```

The final validation must include:

```text
Initramfs safety gate:  passed
```

The builder refuses to proceed when the verifier or blocklist is unavailable.
Do not bypass this check.

## 6. Before any flash

- Verify exact TWRP remains available:
  `414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e`.
- Back up and hash the current recovery partition.
- Flash only `recovery`.
- Keep Odin4 and the TWRP `.img.tar` ready.
- Do not modify `vbmeta`.
- Do not flash an A53 image.
- Do not write the root or boot images to the phone yet.

## 7. After the next failed or successful boot

Immediately restore TWRP and collect:

```bash
adb shell 'ls -la /sys/fs/pstore 2>/dev/null'
adb pull /sys/fs/pstore logs/pstore
adb exec-out 'cat /proc/last_kmsg 2>/dev/null || true' \
  > logs/last_kmsg.txt
```

A reachable `172.16.42.1` debug shell is the first success milestone. Do not
continue into the root filesystem until the initramfs remains stable.
