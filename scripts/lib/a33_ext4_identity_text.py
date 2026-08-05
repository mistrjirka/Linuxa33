from __future__ import annotations

import base64
import re
import uuid


class Ext4IdentityError(RuntimeError):
    pass


READ_SCRIPT = r'''set -u
target="$1"
payload=/tmp/a33x-ext4-superblock.$$
error=/tmp/a33x-ext4-superblock.$$.err
cleanup()
{
    rm -f "$payload" "$error" 2>/dev/null || true
}
trap cleanup EXIT

for command in dd stat base64 cat rm; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "missing_command=$command"
        exit 60
    }
done
[ -b "$target" ] || {
    echo "target_state=not-block path=$target"
    exit 61
}

: > "$error"
dd if="$target" of="$payload" bs=2048 count=1 2>"$error"
dd_rc=$?
bytes="$(stat -c '%s' "$payload" 2>/dev/null || true)"
echo "dd_rc=$dd_rc"
echo "payload_bytes=$bytes"
echo "error_b64_begin"
base64 "$error" 2>/dev/null || true
echo "error_b64_end"
echo "payload_b64_begin"
base64 "$payload" 2>/dev/null || true
echo "payload_b64_end"
echo "phone_partition_writes=no"
echo "temporary_files=/tmp-only"
'''


def _value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _section(text: str, name: str) -> str:
    begin = f"{name}_begin\n"
    end = f"{name}_end\n"
    if text.count(begin) != 1 or text.count(end) != 1:
        return ""
    return text.split(begin, 1)[1].split(end, 1)[0]


def _decode_section(text: str, name: str) -> bytes:
    encoded = "".join(_section(text, name).split())
    if not encoded:
        return b""
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise Ext4IdentityError(f"invalid base64 section {name}: {exc}") from exc


def parse_superblock_payload(payload: bytes) -> tuple[str, str]:
    if len(payload) != 2048:
        raise Ext4IdentityError(
            f"expected 2048 superblock bytes, received {len(payload)}"
        )
    superblock = payload[1024:2048]
    if superblock[56:58] != b"\x53\xef":
        raise Ext4IdentityError("ext4 superblock magic mismatch")
    label_raw = superblock[120:136].split(b"\0", 1)[0]
    if any(byte < 32 or byte > 126 for byte in label_raw):
        raise Ext4IdentityError("filesystem label contains unsupported bytes")
    return str(uuid.UUID(bytes=bytes(superblock[104:120]))), label_raw.decode("ascii")


def parse_read_output(text: str) -> tuple[str, str]:
    dd_rc = _value(text, "dd_rc")
    payload_bytes = _value(text, "payload_bytes")
    error = _decode_section(text, "error_b64").decode("utf-8", "replace").strip()
    if dd_rc != "0" or payload_bytes != "2048":
        raise Ext4IdentityError(
            "remote superblock read failed: "
            f"dd_rc={dd_rc!r} payload_bytes={payload_bytes!r} error={error!r}"
        )
    if text.count("phone_partition_writes=no") != 1:
        raise Ext4IdentityError("remote superblock read lacked no-write marker")
    return parse_superblock_payload(_decode_section(text, "payload_b64"))


def ext4_identity(common, adb: str, serial: str) -> tuple[str, str]:
    completed = common.run(
        [adb, "-s", serial, "shell", "sh", "-s", "--", common.USERDATA],
        input_data=READ_SCRIPT,
        check=False,
        timeout=30,
    )
    output = completed.stdout.replace("\r", "")
    stderr = completed.stderr.replace("\r", "")
    if completed.returncode != 0:
        raise Ext4IdentityError(
            "text-safe ext4 superblock read failed: "
            f"rc={completed.returncode} output={output!r} stderr={stderr!r}"
        )
    return parse_read_output(output)
