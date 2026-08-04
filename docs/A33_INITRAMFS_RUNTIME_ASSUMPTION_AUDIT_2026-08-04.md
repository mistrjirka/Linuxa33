# A33 initramfs runtime-assumption audit

**Date:** 2026-08-04  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Current approved U0i entrypoint:** `scripts/make-u0i-direct-root-function-recovery-v2.sh`

## Purpose

Several host-side validators failed closed even though the underlying artifact
was correct, or proposed a change that could not work with the exact generated
postmarketOS initramfs. This audit separates runtime facts from assumptions and
prevents the same classes of mistake from being reused.

## Confirmed runtime facts

1. BusyBox applet symlinks are created at runtime before second stage. They are
   not all stored as individual paths in the compressed initramfs.
2. Normal initramfs hooks execute in child shells in the exact generated image.
   A hook cannot redefine a function or set a shell variable in PID 1.
3. The exact generated `find_root_partition()` implementation did not satisfy
   the initially assumed direct `pmos_root=` parser contract used by the first
   U0i builder.
4. `wait_root_partition()` consumes `find_root_partition()` through command
   substitution. Valid implementations include both assignment and the
   postmarketOS-style empty test:

   ```sh
   while [ -z "$(find_root_partition)" ]; do
       sleep 1
   done
   ```

5. U0h creates `/dev/block/sda36`, validates the exact partition size, and
   directly identifies ext4 `LABEL="pmOS_root"`, but generic autodetection still
   returns no root device.
6. Absolute symlinks inside a mounted rootfs must be resolved relative to the
   mounted root, not relative to TWRP's `/`.

## Invalid approaches disabled

### Kernel-command-line assumption

`scripts/make-u0i-explicit-userdata-root-recovery.sh` is intentionally disabled.
It assumed a specific parser shape inside `find_root_partition()`. No recovery
image was produced by that failed attempt.

### Hook function override

`scripts/make-u0i-forced-root-function-recovery.sh` is intentionally disabled.
Hooks run in child shells, so a hook-defined `find_root_partition()` cannot
replace PID 1's function. The unused hook 06 implementation was removed.

## Approved direct patch

The v2 entrypoint creates a temporary checked copy of
`scripts/make-u0i-direct-root-function-recovery.sh` and corrects only its
host-side root-wait validator so it accepts either valid stdout-consumption
form. The core builder then operates only on a copied, exact U0h initramfs. It:

1. verifies the exact U0h report, U0g hashes, U0h hook and 67-module set;
2. extracts the actual `find_root_partition()` and `wait_root_partition()`
   definitions and preserves them in `build/u0i-direct-root-inspection/`;
3. proves exactly one command substitution consumes `find_root_partition()`;
4. records whether consumption is an assignment, empty test or another direct
   command-substitution form;
5. proves the second-stage order:
   hooks, root wait, partition resize, filesystem resize, mount, `switch_root`;
6. replaces only `find_root_partition()` so it revalidates
   `/dev/block/sda36` as ext4 `pmOS_root` and prints that path;
7. verifies that `wait_root_partition()` remains byte-identical;
8. syntax-checks the patched shell file;
9. repacks and re-extracts the initramfs;
10. compares every file, mode, symlink, special node and hard-link group,
    allowing only `init_functions.sh` to differ;
11. rechecks all retained U0g/U0h hashes and the 67-module set;
12. builds recovery with the kernel command line unchanged;
13. performs no phone partition write.

The corrected contract was tested with both assignment-based and empty-test
`wait_root_partition()` implementations.

## Similar issues found

### Root-relative symlink checks

The original `scripts/deploy-a33-rootfs-to-userdata.sh` still contains the old
`[ -e "$mountpoint$path" ]` verifier. It can false-fail for absolute rootfs
symlinks such as `/sbin/init` or OpenRC runlevel links because TWRP resolves the
link target against its own `/`. It is a historical destructive-path script and
must not be rerun for the current installation. The successful v2 postwrite
verifier and the U0h flash verifier use root-aware checks.

Historical `finalize-a33-userdata-postwrite-verification.sh` and older
first-rootfs failure collectors are likewise superseded. Use the latest
root-aware verifier/collector for new evidence.

### Token presence is not runtime proof

The old unified-root handoff audit proved that root-discovery and switch-root
code existed, but not that the required `/dev` node existed or that generic
`blkid` enumeration selected it. New reports distinguish code presence from
runtime prerequisites and observed success.

### Shell error propagation inside formatted output

Critical values must be computed in explicit checked assignments before being
printed. A failing command hidden inside `echo "key=$(command)"` may not stop the
script as expected. The direct U0i builder resolves and validates the exact Git
commit before creating any report.

### Repacking topology

Comparing only file hashes is insufficient when rebuilding a cpio archive. The
direct U0i builder also verifies file modes, symlinks, special nodes and
hard-link topology after repacking.

## Current rule

Do not infer behavior from a token, path name, package name or hook order alone.
For every boot-critical dependency, validate:

```text
artifact exists
    -> runtime provider exists
    -> execution scope is correct
    -> caller consumes the result
    -> downstream order is correct
    -> repacked artifact preserves all unrelated state
```

Only the v2 direct-function U0i entrypoint currently satisfies that contract
for the next root-handoff experiment.
