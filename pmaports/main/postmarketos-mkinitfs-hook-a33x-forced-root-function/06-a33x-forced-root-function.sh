#!/bin/sh

# U0i: after U0h has created and verified the direct userdata block node,
# force the already-sourced initramfs root-discovery function to return it.
# This hook must be sourced by run_hooks() in PID 1's shell.

status=/run/a33x-userdata-root-node-status.txt
result=/run/a33x-forced-root-function-status.txt
root=/dev/block/sda36
expected_label=pmOS_root

ensure_kmsg() {
    if [ ! -c /dev/kmsg ]; then
        mkdir -p /dev
        mknod /dev/kmsg c 1 11 2>/dev/null || true
    fi
}

log_forced_root() {
    message="a33x-forced-root-v1: $*"
    ensure_kmsg
    if [ -w /dev/kmsg ]; then
        printf '<6>%s\n' "$message" > /dev/kmsg 2>/dev/null || true
    fi
    printf '%s\n' "$message"
}

: > "$result"
printf 'candidate=U0i-forced-root-function\n' >> "$result"
printf 'root=%s\n' "$root" >> "$result"

if [ ! -b "$root" ]; then
    printf 'result=failed\nreason=root-node-missing\n' >> "$result"
    log_forced_root "result=failed reason=root-node-missing root=$root"
    return 0 2>/dev/null || exit 0
fi

if [ ! -s "$status" ] || ! grep -Fqx 'result=passed' "$status" || \
   ! grep -Fqx 'reason=verified-userdata-root-node' "$status"; then
    printf 'result=failed\nreason=u0h-verification-not-passed\n' >> "$result"
    log_forced_root "result=failed reason=u0h-verification-not-passed"
    return 0 2>/dev/null || exit 0
fi

identity="$(blkid "$root" 2>/dev/null || true)"
printf 'blkid_output=%s\n' "${identity:-missing}" >> "$result"
case "$identity" in
    *'TYPE="ext4"'*) ;;
    *)
        printf 'result=failed\nreason=root-type-not-ext4\n' >> "$result"
        log_forced_root "result=failed reason=root-type-not-ext4"
        return 0 2>/dev/null || exit 0
        ;;
esac
case "$identity" in
    *'LABEL="pmOS_root"'*) ;;
    *)
        printf 'result=failed\nreason=root-label-not-pmOS_root\n' >> "$result"
        log_forced_root "result=failed reason=root-label-not-pmOS_root"
        return 0 2>/dev/null || exit 0
        ;;
esac

# Set both historical cache variables and replace the function itself. The
# builder proves that run_hooks sources this file in the same shell and that
# this hook executes before wait_root_partition.
DEVICE="$root"
PMOS_ROOT="$root"
find_root_partition() {
    printf '%s\n' /dev/block/sda36
}

selected="$(find_root_partition 2>/dev/null || true)"
if [ "$selected" != "$root" ]; then
    printf 'result=failed\nreason=function-override-test-failed\n' >> "$result"
    log_forced_root "result=failed reason=function-override-test-failed selected=${selected:-missing}"
    return 0 2>/dev/null || exit 0
fi

printf 'selected_root=%s\n' "$selected" >> "$result"
printf 'result=passed\nreason=forced-verified-userdata-root\n' >> "$result"
log_forced_root "result=passed reason=forced-verified-userdata-root root=$selected"
return 0 2>/dev/null || exit 0
