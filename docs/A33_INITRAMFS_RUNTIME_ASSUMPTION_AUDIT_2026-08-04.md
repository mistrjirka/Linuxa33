# A33 initramfs runtime-assumption audit

**Date:** 2026-08-04  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Current approved U0i entrypoint:** `scripts/make-u0i-python-direct-root.py`

## Why the Bash U0i chain was replaced

The failed U0i host runs did not expose new phone failures. They exposed brittle
host-side validators that repeatedly assumed a particular generated shell
implementation:

- direct `pmos_root=` parsing inside one function;
- hooks sourced into PID 1 instead of child shells;
- assignment-based consumption instead of an empty-test loop;
- `blkid` appearing directly inside `find_root_partition()` instead of behind a
  helper.

The old direct builder also unpacked, rewrote and repacked the complete cpio
archive. That created unnecessary metadata, ordering and hard-link risks.

Both Bash U0i entrypoints are now fail-closed stubs. The explicit-root and
hook-function approaches remain disabled. No recovery image was produced by any
of those failed host runs.

## Confirmed runtime facts

1. BusyBox applet symlinks are created at runtime before second stage. They are
   not all stored as individual paths in the compressed initramfs.
2. Normal initramfs hooks execute in child shells in the exact generated image.
   A hook cannot redefine a function or set a shell variable in PID 1.
3. The exact generated `find_root_partition()` implementation need not contain
   `blkid`, `pmOS_root` or command-line parsing directly; helper calls are valid.
4. `wait_root_partition()` consumes `find_root_partition()` through command
   substitution. Valid implementations include assignment and the
   postmarketOS-style empty test:

   ```sh
   while [ -z "$(find_root_partition)" ]; do
       sleep 1
   done
   ```

5. U0h creates `/dev/block/sda36`, validates its exact size, and directly
   identifies ext4 `LABEL="pmOS_root"`, but generic autodetection still returns
   no root device.
6. Absolute symlinks inside a mounted rootfs must be resolved relative to the
   mounted root, not relative to TWRP's `/`.

## Python U0i implementation

The approved implementation consists of:

- `scripts/lib/a33_cpio.py` — parser/editor for `070701` newc and `070702` CRC
  archives;
- `scripts/lib/a33_shell.py` — structural extraction of the actual shell
  functions and executable second-stage calls;
- `scripts/make-u0i-python-direct-root.py` — fail-closed build driver;
- `scripts/test-u0i-python-tools.py` — synthetic regression tests.

The Python builder:

1. verifies the exact audited U0h initramfs and reports;
2. decompresses and parses the actual cpio bytes without extracting the whole
   tree to disk;
3. locates exactly one `init_functions.sh`, `init_2nd.sh`,
   `find_root_partition()` and `wait_root_partition()`;
4. accepts both assignment and empty-test stdout consumption without assuming
   the original discovery implementation;
5. validates executable second-stage ordering while ignoring comments;
6. replaces only the `find_root_partition()` payload so it revalidates
   `/dev/block/sda36` as ext4 `pmOS_root` and emits only that path;
7. preserves `wait_root_partition()` byte-for-byte;
8. updates only the target cpio entry's size and CRC fields;
9. preserves entry order, names, modes, link counts, all unrelated payloads and
   bytes after `TRAILER!!!`;
10. rechecks the 67 modules and all retained U0g/U0h hashes;
11. calls the already-proven recovery packer with an unchanged kernel command
    line;
12. performs no phone partition write.

The self-test covers:

- both newc and CRC cpio formats;
- names with and without a leading `./`;
- assignment and empty-test root consumers;
- comments containing misleading command names;
- exact single-payload delta and preserved trailer bytes.

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
code existed, but not that a usable `/dev` node existed or that generic
enumeration selected it. New checks validate callers, runtime providers and
observed output instead of requiring implementation tokens in one function.

### Shell scope

A file being called a hook does not prove it is sourced into the parent shell.
The exact `run_hooks()` implementation must be inspected before relying on
variable or function side effects. The Python U0i path no longer relies on hook
scope.

### Shell error propagation

Critical values must be computed in explicit checked operations before being
reported. Self-modifying Bash wrappers and exact multiline replacement blocks
are prohibited in the approved U0i path.

### Archive topology

Repacking an entire cpio tree and comparing only file hashes is insufficient.
The Python editor copies every raw entry unchanged except the selected payload,
then reparses and verifies the generated archive.

## Current rule

Do not infer behavior from a token, path name, package name or hook number.
For every boot-critical dependency, validate:

```text
artifact exists
    -> runtime provider exists
    -> execution scope is correct
    -> caller consumes the result
    -> downstream order is correct
    -> generated artifact preserves all unrelated bytes and metadata
```

Only `scripts/make-u0i-python-direct-root.py` is approved for the next U0i
host-side build.
