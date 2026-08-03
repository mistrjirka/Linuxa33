# A33 first real-rootfs full script-chain review

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Scope:** every script from non-destructive U0g handoff verification through userdata deployment, U0g recovery flash, first boot, SSH evidence, and no-USB TWRP recovery.

## Current approved first-test layout

```text
recovery -> exact proven U0g recovery image
userdata -> ext4 pmOS_root
cache    -> untouched
super    -> untouched
boot     -> untouched
GPT      -> untouched
```

The temporary cache/`pmOS_boot` plan is obsolete. The exact U0g ramdisk contains executable `/init_2nd.sh` and uses the unified second-stage root handoff. The cache scripts are now refusing stubs with no block-write primitives.

## Reviewed artifacts

### Existing validated inputs

- U0g recovery SHA256:
  `e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81`
- U0g ramdisk SHA256:
  `13ba030dc9593849622bfe85b318393c1f3397d0a95feebf7c734d97cf37732d`
- known-good TWRP SHA256:
  `414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e`
- Odin SHA256:
  `6754aa54f2abe6e99ece32414cd34c8b23b28dbddde537a33203036813637c3b`
- deployment image SHA256:
  `79c94efe41da14e72e82cfc66c8e6fac6f04482fa2ea2af024f6b1ebb67d3951`
- deployment image size:
  `802160640`
- root UUID:
  `7b056328-bdfb-496b-ac38-2624c43c863a`
- userdata mapping:
  `/dev/block/by-name/userdata -> /dev/block/sda36`
- userdata size:
  `114240258048`

### Private preflight

Private rescue directory:

```text
/home/jirka/a33-port/build/private-backups/a33-before-userdata-repurpose-20260803-194234
```

It is not a full Android userdata backup. It contains GPT edges, selected boot-chain partitions, metadata, userdata edge samples, and hashes. It must remain private.

## Defects found and fixed during review

1. **Verifier shell syntax failure**
   - The deviceinfo `sed` expression contained unsafe nested single quotes.
   - Replaced with Python parsing.

2. **False `/init_2nd.sh` failure**
   - The verifier required the literal word `exec` near the invocation.
   - Replaced with structured parsing of executable shell invocation forms, including direct, `sh`, `exec sh`, sourced, quoted, and same-line `then` forms.
   - The embedded script must also have executable mode.

3. **Incorrect cache requirement**
   - A split-initramfs assumption incorrectly required `/initramfs-extra` and `pmOS_boot` on cache.
   - Exact U0g is a unified initramfs. Cache stays untouched.
   - Obsolete cache scripts now always refuse.

4. **Hidden historical destructive implementation**
   - The deployment wrapper previously loaded an older script through `git show`.
   - The full implementation is now visible in the current tree and directly audited.

5. **Weak private-backup binding**
   - Deployment now verifies all `SHA256SUMS`, required rescue files and sizes, private manifest values, copied sanitized summary, exact TWRP recovery backup, image hash and size.

6. **Incomplete live target-use checks**
   - Deployment, recovery flash and preboot observation now check every mount source after canonical resolution, device-mapper users, swap users and block read-only status.
   - Final audit requires readable `/proc/swaps` and no userdata swap use.

7. **Recovery-flash gaps**
   - The flasher now rechecks the complete rootfs prefix hash immediately before touching recovery.
   - It verifies local and uploaded U0g hashes and writes only recovery.
   - It does not reboot automatically.

8. **Weak first-boot preconditions**
   - Observation validates report bindings, root UUID, exact recovery hash, userdata mapping, writable state, and no mount/swap/device-mapper use before reboot.
   - Observation timeout is constrained to 1–900 seconds.

9. **Live SSH false negatives**
   - Linux may expose userdata as `/dev/sda36` instead of TWRP's `/dev/block/sda36`.
   - The collector now validates kernel `MAJ:MIN`/`DEVNAME=sda36`, exact UUID, label, deployment marker, PID 1, sshd and USB address.
   - `findmnt`/`lsblk` fallbacks avoid requiring root-only `blkid` access.

10. **Failure collector ordering**
    - Read-only `e2fsck` previously could run before the collector released its own verification mount.
    - It now unmounts first and records mount/unmount status.

11. **Unverified no-USB recovery path**
    - The final pre-destructive audit now verifies exact Odin and exact TWRP rescue tar contents before userdata may be erased.

12. **Stale audit reports**
    - The final audit records hashes of every downstream script, its execution wrapper, itself, handoff report, root image/manifest, private backup manifests/checksums and rescue report.
    - The destructive execution wrapper refuses if any audited file changes.

## Remaining script chain

### Non-destructive authorization

```text
verify-a33-u0g-unified-root-handoff.sh
audit-a33-first-rootfs-chain.sh
verify-a33-twrp-rescue-assets.sh
audit-a33-first-rootfs-chain-final.sh
```

No phone partition writes occur in these steps.

### Destructive deployment

```text
execute-a33-first-rootfs-deployment.sh
  -> deploy-a33-rootfs-to-userdata.sh
```

The command requires the exact confirmation token. It writes only userdata and performs full-prefix SHA256 readback plus read-only mount verification.

### Recovery flash and first boot

```text
flash-a33-u0g-after-userdata-deploy.sh
boot-observe-a33-first-rootfs.sh
```

The flasher writes only recovery and leaves the phone in TWRP. The observer performs the explicit reboot and records USB, interface, ping and TCP/22 readiness.

### Success branch

```text
collect-a33-first-rootfs-live.sh
```

One SSH session collects system evidence while excluding user files, password hashes, SSH keys and network credentials.

### Failure branch

```text
verify-a33-twrp-rescue-assets.sh
restore-a33-twrp-odin.sh RESTORE-EXACT-TWRP
# manually boot directly into TWRP
collect-a33-first-rootfs-previous-boot.sh
```

The failure collector captures previous-boot evidence and validates the userdata rootfs read-only.

## Current authorization state

The destructive write is **not yet authorized**. The user must pull the reviewed tree and run the final non-destructive chain audit on the actual host and phone. Only a report containing all of the following permits the next stage:

```text
bash_syntax_all=passed
obsolete_cache_scripts=refusing-stubs
u0g_handoff_status=passed
private_backup_checksums=passed
userdata_unmounted=yes
userdata_device_mapper_users=none
proc_swaps_readable=yes
userdata_swap_users=none
rescue_assets_status=passed
final_phone_writes=no
final_audit_status=passed
```

Do not run the erase command until that report has been reviewed.
