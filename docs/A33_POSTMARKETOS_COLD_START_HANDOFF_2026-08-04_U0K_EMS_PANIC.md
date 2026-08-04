# Samsung Galaxy A33 postmarketOS cold-start handoff

**Date:** 2026-08-04  
**Repository:** `mistrjirka/Linuxa33`  
**Branch:** `main`  
**Base repository head before this handoff:** `0dde09df794e15bd696211037b0c6ca8d6d3004c`  
**Current phase:** U0k successfully reached the installed rootfs and OpenRC, then repeatedly panicked in Samsung EMS during CPU-cgroup migration.

This document is for a fresh chat or terminal session. Do not rely on prior conversational context.

---

## 1. Overall goal

Boot postmarketOS/OpenRC reliably on a Samsung Galaxy A33 5G (`SM-A336B`, product `a33x`) using the proven TWRP-derived recovery layout, while preserving a fail-closed and reproducible workflow.

The immediate task is no longer root discovery or mounting. U0k proved that the installed ext4 rootfs can be found, mounted and entered. The current blocker is a repeatable kernel panic in Samsung's loadable `ems.ko` scheduler module when OpenRC writes a task into a CPU cgroup.

The preferred next solution order is:

1. Determine whether `ems.ko` is actually required by the safe boot module set.
2. If it is not required, prevent it from loading and preserve the exact existing kernel.
3. If only a small optional dependent closure requires it, remove that closure.
4. If EMS is required, prefer replacing or patching only `ems.ko` over rebuilding the whole kernel.
5. Build a new kernel only if a module-only solution is impossible and source/config/toolchain provenance has first been made reproducible.

Do not take a broad or lazy workaround. Every new candidate must have an exact declared delta and host tests before a phone experiment.

---

## 2. Non-negotiable safety rules

- Do not boot Android.
- Do not write `userdata` again unless evidence proves it necessary.
- Do not touch `cache`, `super`, `boot`, GPT or unrelated partitions.
- Recovery is the only disposable/test partition.
- Always retain exact hashes and readback reports.
- Exact known-good TWRP must remain restorable.
- Before any flash:
  - run the complete host test gate;
  - validate candidate ancestry and exact payload delta;
  - run a preflight-only flash validation;
  - verify only recovery will be written.
- After a failed Linux boot, restore exact TWRP through Download Mode and boot directly into TWRP. Do not allow Android to boot first.
- The user expects direct repository work, exact diagnosis and no generic workaround scripts.

---

## 3. Immutable device and partition facts

```text
Device model: SM-A336B
Product: a33x
ADB serial: RFCTA00V43L
Timezone: Europe/Prague
```

Exact known-good TWRP:

```text
Path: ~/a33-port/reference/twrp/recovery.img
Size: 100663296 bytes
SHA256: 414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e
```

Userdata/rootfs target:

```text
Physical node: /dev/block/sda36
By-name node: /dev/block/by-name/userdata
Bytes: 114240258048
512-byte sectors: 223125504
Filesystem label: pmOS_root
Filesystem UUID: 7b056328-bdfb-496b-ac38-2624c43c863a
```

Installed rootfs deployment image:

```text
Path: /home/jirka/a33-port/build/userdata-rootfs-images/20260803-193947/a33x-userdata-pmos-root.img
Size: 802160640 bytes
SHA256: 79c94efe41da14e72e82cfc66c8e6fac6f04482fa2ea2af024f6b1ebb67d3951
```

The rootfs remains approximately 765 MiB. U0k deliberately skipped first-boot partition and ext4 resize to isolate mounting and `switch_root`.

---

## 4. Current phone state

After the U0k panic loop, exact TWRP was restored and verified. The phone was then used to collect previous-boot evidence. The last confirmed recovery state is exact known-good TWRP.

Do not assume a test recovery is still installed. Verify TWRP before any new operation.

---

## 5. Evolution of the rootfs handoff

### U0g

- USB NCM enumerated.
- Host obtained `172.16.42.2/24` and could ping `172.16.42.1`.
- No SSH.
- PID 1 remained `/init_2nd.sh`.
- Root cause: kernel knew `sda36`, but initramfs lacked a usable `/dev/sda36` node.

### U0h

- Added creation and verification of `/dev/block/sda36`.
- Direct `blkid` found the rootfs.
- Generic root discovery still timed out.

U0h initramfs SHA256:

```text
e1ad2f2845549c6514ed7fb339c5a47327c4d0223a3d2eabb1cce363b7c163f7
```

### U0i v2

- Replaced only `find_root_partition()` and `wait_root_partition()` with a direct Python-built patch.
- Root was printed correctly, but the generated function failed the output-variable API `find_root_partition partition`.

Recovery:

```text
/home/jirka/a33-port/build/candidates/a33x-h1-usbpd-u0i-python-direct-root-v2-recovery.img
SHA256: 7d630ae2ebbd40e46a9e4d2ebd1cefefe710778a3be5a37a6d8d1573126fdabf
```

### U0j

- Fixed both root-finder APIs using BusyBox ash dynamic scoping.
- Exact TWRP runtime audit proved:
  - stdout API passed;
  - output-variable API passed;
  - `/dev/block/sda36` identity passed.
- Rootfs was clean under host e2fsprogs 1.47.4.
- U0j still never completed resize/mount.

Recovery:

```text
/home/jirka/a33-port/build/candidates/a33x-h1-usbpd-u0j-root-api-compatible-recovery.img
SHA256: 99f3e48be871865164edbc05ef04a0766139cb33c9bf7f09b40f40699d8a6e91
```

U0j initramfs:

```text
SHA256: aaf2f4bda5e253f5e42ec86ea699ac0385cafa0185d1f93850ef4f9e63ab3f2f
```

The exact embedded tools were verified as current rootfs binaries:

```text
blkid: util-linux 2.42.2
blkid SHA256: 1615aea0c782f2157b18ec6425fac1e3af30c715d723f51165cfa5465f8a9e5b

e2fsck: 1.47.4
e2fsck SHA256: 838ff23407700fbbd2005acbaabf453559bae2c8a3c7e2b79951c60447e05082

resize2fs: 1.47.4
resize2fs SHA256: 174279dd0af3539c3c963ad8db412a09fee5e4236a4bde3b07c3e945323f8c84
```

### U0k: direct-mount isolation

U0k reused the exact U0j initramfs and changed only `init_2nd.sh`.

It removed these second-stage calls:

```text
delete_old_install_partition
resize_root_partition
unlock_root_partition
resize_root_filesystem
wait_boot_partition
mount_boot_partition
```

It retained:

```text
wait_root_partition
mount_root_partition
resize_filesystem_after_mount /sysroot
exec switch_root /sysroot "$init"
```

For ext4, `resize_filesystem_after_mount` is a no-op, so U0k performed no filesystem resize.

U0k artifacts:

```text
Initramfs:
/home/jirka/a33-port/export-u0k-direct-mount-isolation/initramfs
SHA256: 06c0837933cde859ac04bc035e0830047606ac4694552ea17fc5581259cbaf01

Recovery:
/home/jirka/a33-port/build/candidates/a33x-h1-usbpd-u0k-direct-mount-isolation-recovery.img
Size: 100663296
SHA256: 7696262e0ee8d3c2a31e55045e59a2b36b8f7eefb0891d56a049a415a8be0b2f

Patch report:
/home/jirka/a33-port/build/u0k-direct-mount-isolation-patch.txt
SHA256: 409c5ba3c050a0c6e50fde273cc15deb81d87069e5545186f863d9c6f76119c2
```

Only `init_2nd.sh` changed. Kernel, DTB, recovery-DTBO, module set, command line, Samsung trailer and AVB layout were preserved.

U0k flash readback matched the candidate exactly, and only recovery was written.

---

## 6. U0k runtime result: major success followed by EMS panic

The observer timed out after 302 seconds, but the phone visibly repeated boot cycles. Later host kernel logs confirmed additional postmarketOS USB/NCM cycles after the observer ended.

The observer did not repeatedly request reboots. The repeated reboots came from kernel panics.

Collected previous-boot archive:

```text
/home/jirka/a33-port/build/runtime-results/u0h-root-node-result-20260804-173223.tar.gz
Final SHA256: 1ade13bfec231f44fe2cf63ed060261a16c08fa59ea161de7e3457a3259041d6
```

Collector summary included:

```text
recovery_status=verified-known-good-twrp
kernel_panic_count=15
openrc_log_count=3995
sshd_log_count=8
phone_partition_writes=no
collection_status=passed
```

The preserved log contains multiple complete U0k boot attempts. The consistent sequence is:

```text
~14.1 s: ext4 sda36 mounted successfully
~15.6 s: real-rootfs userspace/OpenRC active; sshd process appears
~17.7 s: kernel panic in Samsung EMS during CPU-cgroup migration
```

Therefore these stages are now proven:

```text
root-node creation              passed
root discovery                  passed
mount /dev/block/sda36          passed
switch_root into installed root passed in practical effect
OpenRC startup                  passed
sshd process launch             reached
stable SSH listener             not reached before panic
```

The rootfs handoff is no longer the current blocker.

---

## 7. Exact panic boundary

Representative stack:

```text
Internal error: BRK handler: f2005512
pc : freqboost_can_attach+0x19c/0x1a4 [ems]
...
ems_hook_cpu_cgroup_can_attach
cpu_cgroup_can_attach
cgroup_migrate_execute
cgroup_attach_task
cgroup_procs_write
...
Kernel panic - not syncing: BRK handler: Fatal exception
```

The current evidence indicates OpenRC writes a task into `cgroup.procs`; Samsung EMS handles the CPU-cgroup attach and traps inside `freqboost_can_attach()`.

Public S5E8825 EMS source shows unchecked group indexing around:

```c
dst_bg = css->id - 1;
if (dst_bg >= CGROUP_COUNT)
    dst_bg = CGROUP_COUNT - 1;

src_bg = cpuctl_task_group_idx(task);

bg->group[src_bg].tasks -= 1;
bg->group[dst_bg].tasks += 1;
```

There is no lower-bound check before indexing. A root or otherwise unexpected cgroup mapping can produce a negative index and trigger a bounds trap. This is a strong diagnosis, but the exact binary instruction and cgroup ID should still be verified before patching.

Do not yet claim that the only correct fix is a whole-kernel rebuild. The crashing code is in loadable module `[ems]`.

---

## 8. Critical correction: package release label versus actual module ABI

The repository currently packages modules under this directory/release label:

```text
5.10.66-Gabriel260BR-TWRP-ga0103aac9499
```

However, direct `modinfo` inspection of all 315 original modules proved that every module has this actual vermagic:

```text
5.10.66-android12-9-24537318-abA336BXXU2AVG2 SMP preempt mod_unload modversions aarch64
```

This was consistent across all 315 modules.

Exact EMS metadata:

```text
Path: /home/jirka/a33-port/unpacked/twrp-root/lib/modules/ems.ko
SHA256: b207e4443d9c30537f62821d81e73059c50f474a775b7e8283ef1f852fcd692c
Name: ems
Vermagic: 5.10.66-android12-9-24537318-abA336BXXU2AVG2 SMP preempt mod_unload modversions aarch64
Depends: cmupmucal,ect_parser
ELF: 64-bit LSB relocatable, ARM aarch64, not stripped
Build ID: b08df32a8b95cadddf1552ba90401a80dfd26c24
```

This means the repository currently conflates two concepts:

1. **Packaging/staging directory label**
   `5.10.66-Gabriel260BR-TWRP-ga0103aac9499`
2. **Actual module ABI/vermagic**
   `5.10.66-android12-9-24537318-abA336BXXU2AVG2 ...`

Do not replace one blindly with the other. First determine the actual embedded kernel UTS release from the kernel image or preserved boot log. Since `ems.ko` loaded and executed, the running kernel accepted its ABI. The package `_krel` may be only a local packaging label rather than the real UTS release.

Useful host checks:

```bash
strings -a ~/a33-port/unpacked/twrp/kernel |
  grep -E '5\.10\.66|Linux version' |
  sort -u

strings -a ~/Linuxa33/pmaports/device/downstream/linux-samsung-a33x/Image |
  grep -E '5\.10\.66|Linux version' |
  sort -u
```

Also search the collected `last_kmsg` for the exact `Linux version` line.

---

## 9. Current repository state and tooling

Last user-confirmed head before this handoff document:

```text
0dde09df794e15bd696211037b0c6ca8d6d3004c
```

The full host gate passed at that head:

```text
discoverable tests: 4 passed
legacy host tests: 15 passed
host_test_failures=0
host_test_status=passed
```

Important scripts:

```text
scripts/run-host-tests.py
scripts/audit-a33-reproducibility.py
scripts/inspect-a33-ems-module.py
scripts/make-u0k-direct-mount-isolation.py
scripts/flash-a33-u0k-direct-mount-isolation.py
scripts/boot-observe-a33-u0k-direct-mount-isolation.py
scripts/collect-a33-u0h-previous-boot.sh
scripts/restore-a33-twrp-odin.sh
scripts/prepare-safe-module-packages.sh
scripts/generate-modules-initfs.py
```

Reproducibility documentation:

```text
docs/REPRODUCIBILITY.md
```

### Known bugs in the newest audit/inspector

`audit-a33-reproducibility.py` currently compares modules against the packaging label instead of the actual module vermagic. It therefore reports all 315 modules as mismatches.

`inspect-a33-ems-module.py` currently has:

```python
KERNEL_RELEASE = "5.10.66-Gabriel260BR-TWRP-ga0103aac9499"
```

and refuses the real EMS vermagic:

```text
EMS INSPECTION FAILED: EMS vermagic mismatch:
'5.10.66-android12-9-24537318-abA336BXXU2AVG2 SMP preempt mod_unload modversions aarch64'
```

These are host-tool bugs. Do not interpret them as module corruption.

The AVB private key permission issue was real and has already been corrected locally:

```text
~/a33-port/build/keys mode 0700
~/a33-port/build/keys/a33x-recovery-test-rsa4096.pem mode 0600
```

---

## 10. Reproducibility status

### Exact binary deployment

Passed. The existing U0k candidate and exact TWRP can be validated and redeployed to another compatible A33 from preserved artifacts.

Important qualification: another phone must be verified for model, product, partition layout, bootloader state and firmware compatibility. The marketing name alone is insufficient.

### Binary recovery rebuild

Expected to pass after the module ABI audit bug is corrected. Current known preserved inputs include:

- exact TWRP;
- extracted kernel, DTB and recovery-DTBO;
- 315-module original tree;
- pinned AOSP mkbootimg and avb commits;
- local AVB key and fixed salt;
- U0k initramfs, patch report and manifests;
- rootfs deployment image and report.

### Kernel-source rebuild

Still missing. The TWRP project and Linuxa33 package use a prebuilt kernel `Image`. No exact source commit/config/toolchain record exists locally.

Do not claim a public S5E8825 tree is the exact source unless an unpatched build is proven compatible and source/config/toolchain are pinned.

---

## 11. Immediate next tasks — host only

### Task A: separate package label from actual module ABI

Refactor both scripts to use separate constants, for example:

```python
PACKAGE_KREL = "5.10.66-Gabriel260BR-TWRP-ga0103aac9499"
EXPECTED_MODULE_VERMAGIC_PREFIX = (
    "5.10.66-android12-9-24537318-abA336BXXU2AVG2"
)
```

Better: derive the actual module ABI from all 315 modules and require exactly one unique non-empty vermagic, while independently validating the staging directory/package label.

Required audit behavior:

- accept flat and nested module layouts;
- use `modinfo -F vermagic`, not an ad hoc byte search;
- require 315 modules;
- require one unique vermagic across all modules;
- record that exact vermagic;
- do not assume it equals the staging-directory name;
- compare the result against a pinned expected value after it has been deliberately recorded.

Required tests:

- flat layout;
- nested layout;
- one unique valid vermagic;
- one mismatching module rejected;
- `modinfo` failure rejected;
- package label and module ABI intentionally different and accepted.

### Task B: fix and run the EMS dependency inspector

The inspector must use the package label only to find staged files. It must use actual `modinfo` output for ABI validation.

Then rerun:

```bash
cd ~/Linuxa33
python3 scripts/run-host-tests.py
python3 scripts/audit-a33-reproducibility.py
python3 scripts/inspect-a33-ems-module.py
```

Important EMS inspector fields:

```text
ems_declared_dependencies=
ems_reverse_dependencies_all=
ems_is_explicit_seed=
seed_chain_count=
seed_chain=
ems_selected_by_modules_initfs=
selected_ems_dependent_count=
selected_ems_dependents=
ems_in_original_twrp_load_list=
u0k_ems_entry_count=
u0k_exact_ems_hash_entry_count=
ems_removal_classification=
```

### Task C: determine how EMS was loaded

Check all of these separately:

1. Was `ems` explicitly included in `modules-initfs`?
2. Was it pulled in transitively by a safe seed?
3. Is it present only in the installed rootfs module tree and autoloaded by udev/modalias after `switch_root`?
4. Is it named in `modules.load.recovery`?
5. Which selected modules depend on it?
6. Which device modalias or service caused the actual load?

Inspect rootfs configuration read-only if needed:

```text
/etc/modules
/etc/modules-load.d/*
/usr/lib/modules-load.d/*
/etc/modprobe.d/*
/usr/lib/modprobe.d/*
/lib/udev/rules.d/*
/usr/lib/udev/rules.d/*
```

Do not modify the phone while answering this.

---

## 12. Decision tree for the next candidate

### Case 1: EMS is not required by the safe initramfs/rootfs boot path

Build a minimal U0l candidate that keeps the exact U0k kernel and initramfs logic but prevents `ems.ko` from loading.

Possible mechanisms, in order of preference:

1. Remove EMS from generated `modules-initfs` if explicitly selected.
2. Remove the optional seed/dependent that pulls EMS in.
3. Add a rootfs `modprobe.blacklist=ems` or exact modprobe blacklist only if the actual load mechanism is module autoload and the kernel command line/config path is tested.
4. Remove only `ems.ko` from the packaged rootfs module tree as an isolation experiment, with exact manifest delta and dependency proof.

Do not assume blacklisting works until the load mechanism is proven.

### Case 2: selected optional modules depend on EMS

Remove the smallest optional dependent closure and test boot. Preserve USB-PD, UFS, rootfs and networking prerequisites.

### Case 3: EMS is required

Prefer a module-only correction:

- recover or establish compatible EMS source;
- build only `ems.ko` against the exact ABI/config/Module.symvers;
- preserve name, dependencies, vermagic and exported-symbol versions;
- validate all module metadata before replacement;
- recovery delta should be only `ems.ko` plus any unavoidable dependency metadata.

A binary patch of the existing module is conceivable but must not be guessed. It requires exact disassembly, instruction-level proof, patch-site uniqueness, control-flow validation and tests. Do not patch based only on the public source line number.

### Case 4: module-only correction is impossible

Only then establish a source-built kernel pipeline:

- pin repository and exact commit;
- pin full config;
- pin compiler/toolchain and hashes;
- rebuild matching modules;
- prove an unpatched source build boots equivalently with U0k;
- apply the minimal EMS bounds fix;
- record source lock in `~/a33-port/build/a33-kernel-source.lock`.

---

## 13. Candidate acceptance rules

Before any U0l or later flash:

```text
host_test_failures=0
host_test_status=passed
```

And require a candidate manifest proving:

- functional base is exact U0k;
- rootfs handoff logic unchanged;
- exact declared module/kernel delta only;
- kernel unchanged if using module-only experiment;
- DTB and recovery-DTBO unchanged;
- command line unchanged unless the experiment explicitly requires one parameter;
- recovery size exactly 100663296;
- AVB verification passed;
- only recovery will be written;
- TWRP restoration remains available.

The next observer should avoid creating ambiguity. Repeated active ping/socket polling was not proven to cause the panic, but it complicated interpretation. Prefer passive host USB/kernel-event logging plus sparse state checks, and preserve logs beyond five minutes if reboot loops continue.

---

## 14. User preferences for the continuation

- Do not ask the user to repeat facts already recorded here.
- Do not provide broad speculative advice instead of editing/testing the repository.
- Do not create another huge candidate-specific script when a shared implementation/profile can be extended.
- Add exact behavioral tests before asking for a phone experiment.
- Explain exact evidence and distinguish proof from inference.
- Preserve reproducibility for another same-model phone.
- Do not take shortcuts merely because a correct implementation is harder.

---

## 15. First commands for a fresh continuation

```bash
cd ~/Linuxa33
git pull --ff-only origin main
git rev-parse HEAD
git status --short
```

Then inspect the current buggy constants and module paths:

```bash
grep -Rns \
  -E 'KERNEL_RELEASE|EXPECTED_KERNEL_RELEASE|vermagic' \
  scripts/audit-a33-reproducibility.py \
  scripts/inspect-a33-ems-module.py \
  scripts/test-audit-a33-reproducibility.py \
  scripts/test-inspect-a33-ems-module.py

modinfo -F vermagic \
  ~/a33-port/unpacked/twrp-root/lib/modules/ems.ko

modinfo -F depends \
  ~/a33-port/unpacked/twrp-root/lib/modules/ems.ko
```

The first implementation task is to correct the host tools, extend their tests, run the complete host gate, and rerun the EMS dependency inspection. Do not perform a phone operation before that result.
