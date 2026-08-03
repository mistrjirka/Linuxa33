# A33 normal-rootfs installer preparation progress

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Preceding confirmed result:** U0g fixed USB-C physical data routing and enumerated CDC-NCM/CDC-ACM.  
**Current phone mode:** known-good TWRP.  

## Purpose of this stage

The immediate target is no longer USB hardware bring-up. It is:

```text
preserve exact U0g initramfs path
    -> build a reproducible normal postmarketOS rootfs
    -> prove its recovery-installer target before any phone partition write
    -> boot the real rootfs
    -> reach sshd over 172.16.42.1
```

The broader goal remains a usable Linux phone with USB management, Wi-Fi, then incremental display/GPU/touchscreen and a desktop/mobile UI.

## First normal-rootfs preparation attempt

The first run of:

```sh
bash scripts/prepare-a33-normal-rootfs-installer.sh
```

successfully completed the host-side postmarketOS build and generated an Android recovery ZIP. It also confirmed that the fresh rootfs included all U0g packages:

- `postmarketos-mkinitfs-hook-a33x-watchdog`;
- `postmarketos-mkinitfs-hook-a33x-usbpd`;
- `postmarketos-mkinitfs-hook-a33x-muic-switch-dynamic`;
- `postmarketos-mkinitfs-hook-a33x-muic-persist-dynamic`;
- `postmarketos-mkinitfs-hook-debug-shell`.

The script then intentionally failed closed at:

```text
REFUSING: regenerated rootfs is missing package: openssh
```

This is not a phone failure and not an installer-generation failure. It proved that the earlier host rootfs audit had observed mutable chroot state: OpenSSH and NetworkManager had been installed in the old rootfs, but were not reproducible dependencies of a fresh `pmbootstrap install`.

The ZIP produced by that failed validation must not be flashed or sideloaded.

## Reproducibility correction

`device-samsung-a33x` now explicitly depends on:

- `openssh`;
- `networkmanager`;
- `networkmanager-cli`;
- `networkmanager-wifi`;
- `wpa_supplicant`.

It also packages deterministic OpenRC default-runlevel links for:

```text
/etc/init.d/sshd
/etc/init.d/networkmanager
```

This ensures that a fresh rootfs does not depend on whatever happened to be installed or enabled in an older pmbootstrap chroot.

The use of `openssh` is intentional: Alpine's `openssh` package pulls in the server, client, SFTP server and required key-generation pieces. `networkmanager-wifi` supplies the Wi-Fi plugin and requires an `nm-wifi-backend`; `wpa_supplicant` is explicitly selected as that backend. `networkmanager-cli` provides `nmcli`, needed for headless Wi-Fi bring-up after SSH works.

## Important insight

A host-side audit of an existing rootfs is not sufficient proof that a fresh installer reproduces that state. Every bring-up-critical package and service must be encoded in package dependencies and runlevel state, and then validated after a clean `pmbootstrap install`.

The same rule already applied to U0g: manually installed initramfs hooks were converted into device-package dependencies before generating a normal installer. It now also applies to SSH and network management.

## Required next action

Pull the commit containing the management dependency correction, then rerun the same preparation script. The script will rebuild the rootfs and recovery ZIP and must finish with:

```text
preparation_status=passed
No phone partition was written.
```

Only after that passes should the read-only installer-target audit run. Do not flash the generated ZIP before the audit proves the exact target partition and installer behavior.

## Safety state

- Phone remains in TWRP.
- No phone partition was written by `pmbootstrap install --android-recovery-zip`.
- Do not touch `super`, `system`, `userdata`, `data`, or other Android partitions until the target audit is reviewed.
- Preserve exact TWRP rescue and U0g dynamic discovery of physical controller `13860000.hsi2c`.
- Never replace the dynamic I2C selection with a fixed bus number.
