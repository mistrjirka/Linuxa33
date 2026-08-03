# A33 U0g indirect second-stage handoff proof

**Date:** 2026-08-03  
**Device:** Samsung Galaxy A33 5G (`SM-A336B`, `a33x`)  
**Exact recovery SHA256:** `e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81`  
**Exact ramdisk SHA256:** `13ba030dc9593849622bfe85b318393c1f3397d0a95feebf7c734d97cf37732d`

## Why the previous verifier failed

The previous verifier searched `/init` for a direct executable statement containing `/init_2nd.sh`. The exact U0g `/init` does not execute the file directly. Instead it sources `/init_functions.sh` and calls `jump_init_2nd`.

The only literal `/init_2nd.sh` reference visible in `/init` itself after excluding comments was the final error message, so the verifier incorrectly reported:

```text
REFUSING: /init contains no executable invocation of /init_2nd.sh
```

This was a verifier defect. It was not a boot-layout failure and did not imply that cache or `pmOS_boot` was required.

## Exact proven control flow

The archived exact U0g initramfs contains:

```text
/init line 21:
. ./init_functions.sh

/init line 44:
jump_init_2nd

/init line 67:
extract_initramfs_extra /boot/initramfs-extra
```

`/init_functions.sh` defines:

```text
jump_init_2nd() {
    if ! [ -e /init_2nd.sh ]; then
        return
    fi

    echo "  ❬❬ PMOS STAGE 2 ❭❭"
    exec /init_2nd.sh
}
```

Therefore:

1. `/init_functions.sh` is sourced before the first handoff attempt;
2. `jump_init_2nd` checks whether embedded `/init_2nd.sh` exists;
3. the exact U0g ramdisk does contain executable `/init_2nd.sh`;
4. the function executes it at the first `jump_init_2nd` call;
5. that first call occurs before the optional `pmOS_boot`/`initramfs-extra` fallback.

The exact `/init_2nd.sh` then calls:

```text
wait_root_partition
resize_root_partition
resize_root_filesystem
mount_root_partition
exec switch_root /sysroot /sbin/init
```

`/init_functions.sh` discovers the root filesystem using the `pmOS_root` label. `init_functions_2nd.sh` checks the filesystem and uses `resize2fs` for ext4. For the direct physical Android `userdata` partition, partition-table resizing is skipped unless explicitly forced, while the ext4 filesystem itself is expanded to the available partition size.

## Approved first-test layout

```text
recovery -> exact U0g recovery
userdata -> ext4 filesystem labeled pmOS_root
cache    -> untouched
super    -> untouched
boot     -> untouched
GPT      -> untouched
```

## Verifier correction

Commit `ab3e7a0bf3fc2687b17670fd64aed39bbdbaabfc` updates `scripts/verify-a33-u0g-unified-root-handoff.sh` to verify the actual indirect flow across all four files:

- `/init`;
- `/init_functions.sh`;
- `/init_2nd.sh`;
- `/init_functions_2nd.sh`.

It now requires:

- the source of `init_functions.sh` before the handoff call;
- the first `jump_init_2nd` call before `extract_initramfs_extra`;
- an existence guard for `/init_2nd.sh`;
- `exec /init_2nd.sh` inside `jump_init_2nd`;
- executable mode on embedded `/init_2nd.sh`;
- `pmOS_root` discovery;
- root wait, filesystem check/resize, mount and `switch_root` support.

No phone partition write was performed while establishing this proof.
