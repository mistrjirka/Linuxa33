#!/usr/bin/env bash

A33_ADB_BIN="${A33_ADB_BIN:-${ADB:-adb}}"
A33_ADB_SERIAL="${A33_ADB_SERIAL:-}"

a33_die() {
    echo "REFUSING: $*" >&2
    return 1
}

a33_adb_selected() {
    "$A33_ADB_BIN" -s "$A33_ADB_SERIAL" "$@"
}

a33_init_recovery_adb() {
    local wait_seconds="${1:-30}"
    local deadline output serial state shell_state required

    [[ "$wait_seconds" =~ ^[0-9]+$ ]] && ((wait_seconds >= 1 && wait_seconds <= 300)) ||
        a33_die "invalid ADB wait timeout: $wait_seconds" || return 1

    for required in "$A33_ADB_BIN" awk grep sleep timeout tr; do
        command -v "$required" >/dev/null 2>&1 ||
            a33_die "required ADB-selection command is missing: $required" || return 1
    done

    deadline=$((SECONDS + wait_seconds))
    while :; do
        output="$("$A33_ADB_BIN" devices -l 2>&1)" ||
            a33_die "adb devices -l failed" || return 1

        # adb may print daemon startup diagnostics to stderr before the table.
        # Count only non-empty transport rows after the exact table header.
        mapfile -t a33_transport_lines < <(
            printf '%s\n' "$output" |
                tr -d '\r' |
                awk '
                    $0 == "List of devices attached" { in_table=1; next }
                    in_table && NF >= 2 { print }
                '
        )

        if ((${#a33_transport_lines[@]} > 1)); then
            printf '%s\n' "$output" >&2
            a33_die "multiple ADB transports are attached" || return 1
        fi

        if ((${#a33_transport_lines[@]} == 1)); then
            serial="$(awk '{print $1}' <<<"${a33_transport_lines[0]}")"
            state="$(awk '{print $2}' <<<"${a33_transport_lines[0]}")"

            case "$state" in
                recovery)
                    shell_state="$(
                        timeout 5 "$A33_ADB_BIN" -s "$serial" shell 'echo ADB_OK' \
                            2>/dev/null | tr -d '\r' || true
                    )"
                    if grep -Fqx ADB_OK <<<"$shell_state"; then
                        [[ "$("$A33_ADB_BIN" -s "$serial" get-state | tr -d '\r')" = recovery ]] ||
                            a33_die "selected transport stopped reporting recovery state" ||
                            return 1
                        [[ "$("$A33_ADB_BIN" -s "$serial" get-serialno | tr -d '\r')" = "$serial" ]] ||
                            a33_die "selected transport serial changed" || return 1
                        A33_ADB_SERIAL="$serial"
                        ADB=a33_adb_selected
                        export A33_ADB_BIN A33_ADB_SERIAL
                        return 0
                    fi
                    ;;
                offline)
                    ;;
                unauthorized)
                    printf '%s\n' "$output" >&2
                    a33_die "ADB transport is unauthorized" || return 1
                    ;;
                *)
                    printf '%s\n' "$output" >&2
                    a33_die "attached ADB transport is in state '$state', expected recovery" ||
                        return 1
                    ;;
            esac
        fi

        if ((SECONDS >= deadline)); then
            printf '%s\n' "$output" >&2
            a33_die "one responsive recovery transport did not appear within ${wait_seconds}s" ||
                return 1
        fi
        sleep 1
    done
}

a33_ext4_identity() {
    local target="$1"
    command -v python3 >/dev/null 2>&1 ||
        a33_die "python3 is required for ext4 identity parsing" || return 1

    [[ "$target" =~ ^/dev/block/(by-name/[A-Za-z0-9._-]+|sd[a-z][0-9]+)$ ]] ||
        a33_die "unsafe block-device path for ext4 identity: $target" || return 1

    "$ADB" exec-out sh -c "dd if='$target' bs=2048 count=1 2>/dev/null" |
        python3 -c '
import sys
import uuid
data = sys.stdin.buffer.read()
if len(data) != 2048:
    raise SystemExit(f"expected 2048 bytes, received {len(data)}")
sb = data[1024:2048]
if sb[56:58] != b"\x53\xef":
    raise SystemExit("ext superblock magic mismatch")
raw_uuid = bytes(sb[104:120])
raw_label = bytes(sb[120:136]).split(b"\x00", 1)[0]
if any(byte < 32 or byte > 126 for byte in raw_label):
    raise SystemExit("filesystem label contains unsupported bytes")
print("type=ext4")
print("label=" + raw_label.decode("ascii"))
print("uuid=" + str(uuid.UUID(bytes=raw_uuid)))
'
}

a33_tcp_port_open() {
    local host="$1" port="$2" timeout_seconds="${3:-1}"
    command -v python3 >/dev/null 2>&1 ||
        a33_die "python3 is required for TCP probing" || return 1
    python3 - "$host" "$port" "$timeout_seconds" <<'PY'
import socket
import sys
with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=float(sys.argv[3])):
    pass
PY
}
