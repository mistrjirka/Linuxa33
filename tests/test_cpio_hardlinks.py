from __future__ import annotations

from pathlib import Path
import stat
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

from a33_cpio import Archive


def newc_entry(
    name: str,
    data: bytes = b"",
    *,
    ino: int,
    mode: int = stat.S_IFREG | 0o755,
    nlink: int = 1,
    devmajor: int = 0,
    devminor: int = 0,
) -> bytes:
    raw_name = name.encode() + b"\0"
    fields = (
        ino,
        mode,
        0,
        0,
        nlink,
        0,
        len(data),
        devmajor,
        devminor,
        0,
        0,
        len(raw_name),
        0,
    )
    header = b"070701" + b"".join(f"{value:08x}".encode() for value in fields)
    payload = header + raw_name
    payload += b"\0" * ((-len(payload)) % 4)
    payload += data
    payload += b"\0" * ((-len(payload)) % 4)
    return payload


def archive_bytes(*entries: bytes) -> bytes:
    trailer = newc_entry("TRAILER!!!", ino=0, mode=0, nlink=1)
    return b"".join((*entries, trailer))


class NewcHardLinkTests(unittest.TestCase):
    def test_data_can_follow_zero_sized_named_entry(self) -> None:
        archive = Archive.parse(
            archive_bytes(
                newc_entry(
                    "bin/busybox",
                    ino=41,
                    nlink=2,
                    devmajor=8,
                    devminor=1,
                ),
                newc_entry(
                    "bin/busybox-link",
                    b"busybox-payload",
                    ino=41,
                    nlink=2,
                    devmajor=8,
                    devminor=1,
                ),
            )
        )
        self.assertEqual(archive.one("bin/busybox").data, b"")
        self.assertEqual(archive.resolved_data("bin/busybox"), b"busybox-payload")

    def test_data_can_precede_zero_sized_named_entry(self) -> None:
        archive = Archive.parse(
            archive_bytes(
                newc_entry(
                    "bin/busybox-link",
                    b"busybox-payload",
                    ino=42,
                    nlink=2,
                    devmajor=8,
                    devminor=1,
                ),
                newc_entry(
                    "bin/busybox",
                    ino=42,
                    nlink=2,
                    devmajor=8,
                    devminor=1,
                ),
            )
        )
        self.assertEqual(archive.resolved_data("bin/busybox"), b"busybox-payload")

    def test_last_data_bearing_hardlink_wins(self) -> None:
        archive = Archive.parse(
            archive_bytes(
                newc_entry(
                    "bin/tool-a",
                    b"old",
                    ino=43,
                    nlink=3,
                    devmajor=8,
                    devminor=1,
                ),
                newc_entry(
                    "bin/tool-b",
                    ino=43,
                    nlink=3,
                    devmajor=8,
                    devminor=1,
                ),
                newc_entry(
                    "bin/tool-c",
                    b"new",
                    ino=43,
                    nlink=3,
                    devmajor=8,
                    devminor=1,
                ),
            )
        )
        self.assertEqual(archive.resolved_data("bin/tool-b"), b"new")

    def test_unlinked_zero_sized_file_remains_empty(self) -> None:
        archive = Archive.parse(
            archive_bytes(newc_entry("empty", ino=44, nlink=1))
        )
        self.assertEqual(archive.resolved_data("empty"), b"")


if __name__ == "__main__":
    unittest.main()
