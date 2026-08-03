# Samsung A33 postmarketOS cold-start handoff: USB working, normal rootfs next

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`, Exynos 1280/s5e8825)  
**Repository:** `https://github.com/mistrjirka/Linuxa33`  
**Branch:** `main`  
**Minimum implementation commit for the next commands:** `e241a8bfe714bf45b2da6670244fa33da02647b5`  
**Current phone mode at handoff:** known-good TWRP, accessed through ADB.

This document is intended to be sufficient for a fresh chat with no earlier context.

---

## 1. Overall goal

The goal is not merely to enumerate a USB gadget. The goal is a usable Linux/postmarketOS phone:

1. boot the real postmarketOS root filesystem reliably;
2. provide persistent management access, initially SSH over USB networking;
3. bring up Wi-Fi as a second management path;
4. incrementally enable display, GPU, touchscreen and remaining hardware;
5. run a usable mobile or desktop environment.

The current immediate target is:

```text
verified U0g USB physical path
    -> generate a reproducible normal-rootfs installer on the host
    -> prove the exact safe phone installation target
    -> install rootfs without touching unrelated Android partitions
    -> boot through the U0g-compatible initramfs
    -> SSH to jirka@172.16.42.1
```

Do not skip installation-target verification.

---

## 2. Operating and safety rules

### Normal candidate testing

```text
Android -> adb reboot recovery -> TWRP
-> push recovery image
-> dd only /dev/block/by-name/recovery
-> verify the exact partition SHA256
-> adb reboot recovery into the candidate
```

### Recovery after a non-enumerating candidate

Use Odin only after the candidate has no working USB transport. Restore exact TWRP, boot directly into TWRP and collect `/proc/last_kmsg` before booting Android.

### Never do these casually

- Do not flash a homemade standalone `vbmeta`.
- Do not write `super`, `system`, `userdata`, `data`, or another Android partition until its exact role and installer behavior are proven.
- Do not sideload the generated postmarketOS ZIP merely because it exists.
- Do not hardcode a Linux I2C bus number for the MUIC path.
- Do not load the full TWRP module list.
- Do not enable the full display/MIPI dependency closure in one experiment.
- Do not interpret counts from Samsung's wrapped `last_kmsg` as automatically belonging to the most recent boot.
- Do not run `pmbootstrap pull` after the local pmaports overlay without re-running `scripts/setup-third-host-pmaports.sh`.

### Concurrency/blocking rule

Do not introduce unnecessary sleeps, global synchronization or blocking. The metadata `sync` operations in the U0f/U0g observer were deliberate durability barriers for cross-boot evidence and are temporary bring-up instrumentation, not the intended permanent fast path.

### TWRP ADB quirk

Do not use `adb wait-for-device` for this TWRP. Use:

```sh
until adb shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done
```

---

## 3. Exact rescue identity

Known-good TWRP:

```text
image: ~/a33-port/reference/twrp/recovery.img
size: 100663296
SHA256: 414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e
kernel: 5.10.66-Gabriel260BR-TWRP-ga0103aac9499
partition: /dev/block/by-name/recovery -> /dev/block/sda16
Odin tar: ~/a33-port/build/rescue/twrp-a33x-restore.img.tar
```

Odin:

```sh
sudo ~/a33-port/tools/odin4 -l
sudo ~/a33-port/tools/odin4 \
  -a ~/a33-port/build/rescue/twrp-a33x-restore.img.tar
```

Boot directly into TWRP after Odin:

1. hold Side + Volume Down until black;
2. immediately hold Side + Volume Up;
3. release Side at Samsung logo;
4. keep Volume Up held until TWRP appears.

---

## 4. What was proven before U0g

### U0d

U0d retained the original PDIC module and patched only the Type-C manager MUIC_NONE mask:

```text
manager_usb_event_send:
mov w9,#0x16 -> mov w9,#0x17
c9 02 80 52 -> e9 02 80 52
```

Hashes:

```text
original usb_typec_manager:
3a2d75c5e460d2aa0196ac363cddff1cf85d29507d572110008cfccc3e570ea7

patched usb_typec_manager:
de92f9dc0d29d671bd20f42ad01688e0584eb8e43f6826ff2643e0767c814641

original pdic_notifier_module:
5442a4cf5d4f12f394e5c3d4f5f01785929427fa71101731ea16bd00d0840161
```

U0d proved:

- real USB-PD attach;
- Type-C manager accepted UFP;
- delayed notifier replay;
- DWC3 runtime resume and gadget start;
- but no physical host enumeration.

### U0e and U0f

U0e/U0f added only `i2c_dev` and the MUIC register helper, but the helper never ran. The old hook assumed Linux bus 2 always represented physical controller `13860000.hsi2c`. In the minimal candidate runtime, bus 2 represented `13850000.hsi2c`, so the hook intentionally failed closed.

U0f metadata proved:

```text
i2c_dev_loaded=yes
runtime i2c-2 -> 13850000.hsi2c
helper_output_present=no
```

Therefore U0e/U0f did not disprove the register sequence; they never executed it.

---

## 5. Critical topology insight

Full TWRP showed two distinct S2MU106-related I2C groups:

```text
physical 13860000.hsi2c, TWRP bus 2:
  2-003d s2mu106mfd
  2-003e dummy / MUIC register bank

physical 138b0000.hsi2c, TWRP bus 6:
  6-003b s2mu106-fuelgauge
  6-003c s2mu106-usbpd
```

The earlier inference that the MUIC bank should be a sibling of fuel gauge/USB-PD on bus 6 was wrong. The correct stable identifier is the physical controller path `13860000.hsi2c`, not the Linux bus number and not the bus carrying fuel gauge/USB-PD.

This is why U0g discovers the adapter by resolved physical sysfs path.

---

## 6. U0g: confirmed USB-C success

Candidate:

```text
name: U0g-muic-dynamic
recovery:
~/a33-port/build/candidates/a33x-h1-usbpd-u0g-muic-dynamic-recovery.img
size: 100663296
SHA256: e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81
```

Runtime archive:

```text
u0g-result-20260803-155749.tar.gz
SHA256: bb8ffc202234b8490ba6b3d30e78912b020db9b48c73e68a955bd9ac5e9e838e
```

Persistent result:

```text
/a33x-bringup/u0g-muic-result.txt
SHA256: 2613bdaa7cf4bc110f361b752ac4cba3e7421056d6155ccbcc6528088644a25e
```

### Dynamic selection result

```text
selected_controller=13860000.hsi2c
selected_bus=3
selected_entry=/sys/class/i2c-dev/i2c-3
selected_target=/sys/devices/platform/13860000.hsi2c/i2c-3/i2c-dev/i2c-3
selected_device=/dev/i2c-3
selected_device_number=89:3
selected_address=0x3e
helper_rc=0
```

The same physical controller was bus 2 in TWRP and bus 3 in the minimal postmarketOS runtime. A fixed bus number is therefore invalid by construction.

### Exact register transcript

Initial values:

```text
CTRL1  0x6d = 0x13
SWITCH 0x70 = 0x00
```

Executed sequence:

```text
0x6d = 0x13
0x70 = 0x24
0x6d = 0x17
```

Verified final values:

```text
CTRL1  0x6d = 0x17
SWITCH 0x70 = 0x24
helper_rc=0
no rollback/error marker
```

### Independent host proof

The host enumerated:

```text
idVendor=04e8, idProduct=6860
Product: Samsung Galaxy A33 5G
Manufacturer: Samsung
SerialNumber: postmarketOS
cdc_ncm -> enp197s0f0u1
cdc_acm -> ttyACM0
```

Host NCM state:

```text
enp197s0f0u1 = 172.16.42.2/24
172.16.42.1 replies to ping
```

The cable reconnect test produced a second successful enumeration and added ACM.

### Service distinction

CDC-NCM and CDC-ACM functions enumerating does not mean services exist behind them:

- NCM transport worked;
- the initramfs telnet service accepted one connection and closed;
- ports 22 and 23 were then closed;
- ACM enumerated, but no getty/shell was attached to the phone-side endpoint;
- raw writes to `/dev/ttyACM0` returned no bytes.

USB hardware is solved. Management service and rootfs handoff are now the problem.

---

## 7. Wrapped `last_kmsg` rule

Samsung's `/proc/last_kmsg` is a mixed/wrapped 2 MiB buffer. Raw extraction was correct, but simple counts can include stale records.

The generic U0g collection counted two panic strings, including an apparent panic at about 1.45 seconds. That cannot belong to the successful U0g boot because:

- the U0g persistent report was written at uptime 3.61 seconds;
- the helper had already succeeded;
- the host later enumerated and communicated over NCM.

Treat metadata persistence and independent host events as authoritative for U0g. Label unmatched wrapped-buffer panic strings as unattributed, not current-boot panics.

---

## 8. Rootfs audit result

Audit archive:

```text
~/a33-port/build/a33-next-stage-audit-20260803-162217.tar.gz
size: 11272921
SHA256: 9c4cdb54deb621a7ad477a18438fbfc814ba099e26a5871a7ed51906da4002ea
```

Configuration:

```text
device=samsung-a33x
channel=edge
UI=console
init=OpenRC
user=jirka
hostname=roomster
extra_packages=none
```

Confirmed rootfs state:

```text
OpenSSH installed
sshd enabled in default runlevel
NetworkManager installed
networkmanager enabled in default runlevel
wpa_supplicant enabled in default runlevel
user jirka exists with /bin/ash
```

The rootfs is therefore service-ready for normal boot and SSH. It is not yet proven to be installed on a phone partition.

### Initramfs handoff insight

The audited initramfs already contains normal postmarketOS boot logic:

1. initialize udev;
2. set up USB network;
3. run U0g hooks;
4. enter `debug_shell` only when `pmos.debug-shell` is explicitly present;
5. find and mount `pmOS_root`;
6. `switch_root /sysroot /sbin/init`.

The normal device kernel cmdline does not contain `pmos.debug-shell`. Therefore a normal installed rootfs should continue to OpenRC rather than intentionally stopping in the debug shell.

---

## 9. Reproducibility issue found and fixed

The U0g packages were initially installed manually in the current rootfs but were not dependencies of `device-samsung-a33x`. A fresh `pmbootstrap install` could therefore silently omit the working USB fix.

Fixed in commit:

```text
e46efec63c801613157c9e415638772f0f1308ed
Make confirmed U0g USB path part of A33 device package
```

The device package now depends on:

```text
postmarketos-mkinitfs-hook-debug-shell
postmarketos-mkinitfs-hook-a33x-watchdog
postmarketos-mkinitfs-hook-a33x-usbpd
postmarketos-mkinitfs-hook-a33x-muic-switch-dynamic
postmarketos-mkinitfs-hook-a33x-muic-persist-dynamic
```

The persistence hook is intentionally retained for the next normal-rootfs handoff test. Remove it from the permanent path after reliable SSH/log collection is proven.

Additional commits:

```text
2b971aa6f16818756af40e968f66b5d9f890618e
Verify confirmed U0g aports in pmaports overlay

a049de534f602c74f1c0d1accadd47b297181c7d
Add fail-closed normal rootfs installer preparation

e241a8bfe714bf45b2da6670244fa33da02647b5
Add robust A33 installer target audit wrapper
```

---

## 10. Current blocker

The earlier installer-target audit failed with:

```text
REFUSING: generated recovery installer link is missing:
~/a33-port/export-debug/pmos-samsung-a33x.zip
```

The export tree contained a dangling symlink to a recovery installer that had not been generated in the current buildroot. This is a host-artifact issue, not a phone failure.

No installer-target audit archive was created, so an empty `TARGET_AUDIT` variable was expected.

The current phone partition topology and recovery ZIP behavior remain unverified together. Do not flash or sideload a rootfs yet.

---

## 11. Exact next commands

Keep the phone in TWRP. These operations build and inspect host artifacts; they do not write a phone partition.

### Step A: update and validate scripts

```sh
cd ~/Linuxa33
git pull --ff-only

git merge-base --is-ancestor \
  e241a8bfe714bf45b2da6670244fa33da02647b5 \
  HEAD || exit 1

bash -n scripts/prepare-a33-normal-rootfs-installer.sh
bash -n scripts/run-a33-installer-target-audit.sh
bash -n scripts/audit-a33-recovery-installer-target.sh
```

### Step B: generate a reproducible host-side installer

```sh
bash scripts/prepare-a33-normal-rootfs-installer.sh \
  2>&1 | tee "$HOME/a33-port/build/a33-normal-rootfs-installer-host-run.txt"
```

This script:

- does not write the phone;
- syncs only the tracked device/U0g package definitions;
- deliberately does not replace the patched proprietary Linux module payload;
- requires the guarded `i2c_dev` module profile;
- verifies exact Type-C, PDIC and `i2c_dev` hashes in `modules.tar.gz`;
- makes U0g dependencies part of the regenerated rootfs;
- runs `pmbootstrap install --android-recovery-zip`;
- exports to `~/a33-port/export-normal-rootfs`;
- re-extracts the regenerated initramfs;
- requires 67 modules and exact U0g helper/hook hashes;
- verifies SSH and NetworkManager remain enabled.

Required ending:

```text
preparation_status=passed
No phone partition was written.
```

### Step C: run the read-only installer/phone target audit

```sh
bash scripts/run-a33-installer-target-audit.sh \
  2>&1 | tee "$HOME/a33-port/build/a33-installer-target-audit-host-run.txt"
```

The wrapper uses:

```text
~/a33-port/export-normal-rootfs/pmos-samsung-a33x.zip
```

It then inspects the ZIP and TWRP partition topology read-only.

### Step D: locate the audit archive

```sh
TARGET_AUDIT="$(
  find "$HOME/a33-port/build" \
    -maxdepth 1 \
    -type f \
    -name 'a33-installer-target-audit-*.tar.gz' \
    -printf '%T@ %p\n' |
  sort -nr |
  head -n 1 |
  cut -d' ' -f2-
)"

test -n "$TARGET_AUDIT"
test -f "$TARGET_AUDIT"

echo "Upload this file:"
echo "$TARGET_AUDIT"
stat -Lc 'size=%s' "$TARGET_AUDIT"
sha256sum "$TARGET_AUDIT"

tar --wildcards -xOf "$TARGET_AUDIT" '*/summary.txt'
```

Upload the resulting `.tar.gz` only.

If Step B fails, upload:

```text
~/a33-port/build/a33-normal-rootfs-installer-host-run.txt
~/a33-port/build/a33-normal-rootfs-installer.txt
```

If Step C fails, upload:

```text
~/a33-port/build/a33-installer-target-audit-host-run.txt
```

---

## 12. Decision after the target audit

The next assistant must inspect:

1. the recovery ZIP `install_options`;
2. exact `pmos_install` partition-selection logic;
3. image sizes and labels `pmOS_boot` / `pmOS_root`;
4. TWRP `/dev/block/by-name` topology;
5. `super` and logical partitions;
6. whether the intended install mode is nested subpartitions inside a verified carrier partition;
7. whether the generated rootfs fits with safe margin.

Only after that evidence should installation commands be produced.

Expected next development stages:

### U0h / normal-rootfs handoff

- preserve U0g's Type-C patch, original PDIC and dynamic `13860000.hsi2c` discovery exactly;
- preserve the verified `0x17/0x24` MUIC state;
- boot the installed `pmOS_root` rather than remaining in a recovery-only debug environment;
- keep a deliberate rescue path;
- verify NCM and then SSH:

```sh
ping 172.16.42.1
ssh jirka@172.16.42.1
```

### Wi-Fi

Once SSH works:

```sh
nmcli device
nmcli radio wifi on
nmcli device wifi list
```

Do not commit SSIDs or passwords. Supply credentials privately at runtime.

### Display and desktop

Display is not the immediate next step. A prior indiscriminate TWRP module load caused a repeatable panic in `phy_exynos_mipi` / `exynos_mipi_phy_probe`.

Proceed incrementally:

1. identify exact panel/display controller modules and prerequisites;
2. add one controlled dependency layer at a time;
3. retain remote SSH so black-screen failures remain observable;
4. validate framebuffer/DRM before adding a desktop shell;
5. add touchscreen/input separately;
6. then select Phosh, Plasma Mobile or another environment.

Do not accept permanent headless operation as the final goal, but do not combine display bring-up with rootfs installation or Wi-Fi in one experiment.

---

## 13. Main lessons learned

1. **Stable physical identity beats dynamic enumeration.** Linux I2C bus numbers changed between TWRP and the minimal runtime; the physical controller path did not.
2. **Do not infer sibling register banks solely from chip-family naming.** Fuel gauge/USB-PD and MFD/MUIC appeared on different A33 controllers.
3. **Separate hardware functions from userspace services.** ACM enumeration did not imply a serial getty; NCM enumeration did not imply SSH.
4. **Persistent structured evidence is more trustworthy than mixed boot buffers.** Metadata resolved what `/dev/kmsg` and wrapped `last_kmsg` could not.
5. **Make successful experiments reproducible in packaging.** A manually installed hook is not a production result if a fresh install can omit it.
6. **Keep functional and observability deltas separate.** U0f added persistence without changing register behavior; U0g changed only adapter selection while retaining the sequence.
7. **Avoid lazy broad dependency loading.** The full vendor display/MIPI closure can trigger unrelated probes and panics; prerequisites must be studied and introduced deliberately.
8. **Host enumeration is independent proof.** The host's descriptors, NCM interface and successful ping corroborated the phone-side helper transcript.

---

## 14. Most relevant repository files

```text
docs/U0G_DYNAMIC_MUIC_USB_SUCCESS_2026-08-03.md
docs/A33_POSTMARKETOS_COLD_START_HANDOFF_2026-08-03_USB_WORKING_ROOTFS_NEXT.md

pmaports/device/downstream/device-samsung-a33x/APKBUILD
pmaports/device/downstream/device-samsung-a33x/deviceinfo
pmaports/device/downstream/device-samsung-a33x/kernel-cmdline.conf

pmaports/main/postmarketos-mkinitfs-hook-a33x-muic-switch-dynamic/
pmaports/main/postmarketos-mkinitfs-hook-a33x-muic-persist-dynamic/
pmaports/main/postmarketos-mkinitfs-hook-a33x-usbpd/
pmaports/main/postmarketos-mkinitfs-hook-a33x-watchdog/

scripts/prepare-u0g-muic-dynamic-initramfs.sh
scripts/make-u0g-muic-dynamic-recovery.sh
scripts/collect-a33-u0g-previous-boot.sh
scripts/collect-a33-next-stage-rootfs-audit.sh
scripts/prepare-a33-normal-rootfs-installer.sh
scripts/audit-a33-recovery-installer-target.sh
scripts/run-a33-installer-target-audit.sh
scripts/setup-third-host-pmaports.sh
```

---

## 15. Current one-sentence status

The Galaxy A33 now has a proven Linux USB-C data path through dynamic MUIC controller discovery and a verified register switch; the host rootfs is SSH/NetworkManager-ready, and the next task is to generate a reproducible U0g-preserving recovery installer and prove its exact safe installation target before performing any rootfs write.
