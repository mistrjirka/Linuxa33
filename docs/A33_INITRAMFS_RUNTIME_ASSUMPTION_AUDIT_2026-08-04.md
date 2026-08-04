# A33 initramfs runtime-assumption audit

**Date:** 2026-08-04  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Current approved U0i entrypoint:** `scripts/make-u0i-python-direct-root-v2.py`

## Why the earlier U0i builders were rejected

The phone did not cause the repeated U0i host-side failures. The failures came
from validators that assumed one particular implementation of generated
postmarketOS shell code:

1. a direct `pmos_root=` parser inside `find_root_partition()`;
2. hooks sourced into PID 1 rather than executed in child shells;
3. assignment-based consumption of `find_root_partition()`;
4. a direct command substitution in `wait_root_partition()`;
5. `blkid` appearing directly inside `find_root_partition()` rather than behind
   another helper.

Each assumption correctly failed closed before any phone partition write, but
stacking more source-shape validators was the wrong design.

## Runtime facts actually proven

- U0h hook 05 runs before root waiting.
- Hook 05 creates `/dev/sda36` and `/dev/block/sda36` from the exact sysfs
  major/minor pair.
- It validates exactly `223125504` sectors.
- Direct `blkid /dev/block/sda36` reports ext4 with label `pmOS_root`.
- The generic postmarketOS root path still does not select the partition.
- The second stage reaches `wait_root_partition()` but never reaches
  `switch_root`, OpenRC or sshd.
- The U0h initramfs contains 67 validated modules and all retained U0g hooks and
  helpers.

## Approved U0i v2 design

U0i v2 no longer analyzes or depends on the old root-discovery call graph. It
uses Python to parse the actual gzip/newc archive and replaces exactly two shell
functions inside the existing `init_functions.sh` payload:

- `find_root_partition()` becomes an A33-specific implementation that requires
  `/dev/block/sda36`, re-runs direct `blkid`, requires ext4 and `pmOS_root`, and
  prints only the verified device path;
- `wait_root_partition()` becomes a direct loop over that new function.

This means the old implementation may use direct substitution, helper
functions, cached variables or another internal arrangement. None of those
shapes are trusted or required.

## Preservation contract

The Python builder requires all of the following before producing recovery:

- exact U0h initramfs hash matches its report;
- exact U0g helper and hooks 03/04 hashes match;
- exact U0h hook 05 hash matches;
- module count remains 67;
- only the `init_functions.sh` CPIO payload changes;
- CPIO entry order, names, modes, link counts, unrelated payloads and trailer
  tail remain unchanged;
- within `init_functions.sh`, all text outside the two named functions remains
  byte-identical;
- the patched shell file passes `sh -n`;
- the second-stage executable order remains hooks, root wait, resize, mount and
  `switch_root`;
- the recovery kernel command line remains unchanged;
- recovery is exactly 100663296 bytes and AVB/layout validation passes;
- no phone partition is written by the builder.

## Regression coverage

`scripts/test-u0i-python-direct-root-v2.py` tests three incompatible original
root-discovery shapes:

- direct `$(find_root_partition)` waiting;
- an indirect helper-based implementation;
- global cached state with no direct call between the functions.

For every shape, the test proves only the two function bodies change and only
`init_functions.sh` changes inside the synthetic CPIO archive.

## Disabled paths

Do not use these superseded builders:

- `scripts/make-u0i-explicit-userdata-root-recovery.sh`
- `scripts/make-u0i-forced-root-function-recovery.sh`
- `scripts/make-u0i-direct-root-function-recovery.sh`
- `scripts/make-u0i-direct-root-function-recovery-v2.sh`
- `scripts/make-u0i-python-direct-root.py`

They are retained only as explicit refusal stubs or in Git history.

## Current rule

Do not infer runtime behavior from source token placement or one expected call
shape. For boot-critical changes, validate the artifact and replace the smallest
complete runtime contract rather than attempting to adapt to undocumented
internal implementation details.
