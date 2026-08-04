from __future__ import annotations

from dataclasses import dataclass
import hashlib

HEADER = 110
MAGICS = {b"070701", b"070702"}


class CpioError(RuntimeError):
    pass


def _norm(name: str) -> str:
    return name[2:] if name.startswith("./") else name


@dataclass(frozen=True)
class Entry:
    name: str
    magic: bytes
    header: bytes
    name_area: bytes
    data: bytes
    raw: bytes
    mode: int
    nlink: int

    @property
    def normalized(self) -> str:
        return _norm(self.name)

    def replace_data(self, data: bytes) -> bytes:
        header = bytearray(self.header)
        header[54:62] = f"{len(data):08x}".encode()
        checksum = sum(data) & 0xffffffff if self.magic == b"070702" else 0
        header[102:110] = f"{checksum:08x}".encode()
        return bytes(header) + self.name_area + data + b"\0" * ((-len(data)) % 4)


@dataclass(frozen=True)
class Archive:
    entries: tuple[Entry, ...]
    tail: bytes

    @classmethod
    def parse(cls, payload: bytes) -> "Archive":
        entries: list[Entry] = []
        off = 0
        while True:
            start = off
            if off + HEADER > len(payload):
                raise CpioError(f"truncated header at {off}")
            header = payload[off:off + HEADER]
            magic = header[:6]
            if magic not in MAGICS:
                raise CpioError(f"unsupported magic {magic!r} at {off}")
            try:
                fields = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
            except ValueError as exc:
                raise CpioError(f"invalid header at {off}: {exc}") from exc
            mode, nlink, size, namesize, check = fields[1], fields[4], fields[6], fields[11], fields[12]
            off += HEADER
            if namesize < 1 or off + namesize > len(payload):
                raise CpioError(f"invalid filename at {off}")
            raw_name = payload[off:off + namesize]
            if not raw_name.endswith(b"\0"):
                raise CpioError(f"unterminated filename at {off}")
            name = raw_name[:-1].decode("utf-8", "surrogateescape")
            off += namesize
            off += (-off) % 4
            name_area = payload[start + HEADER:off]
            if off + size > len(payload):
                raise CpioError(f"truncated data for {name}")
            data = payload[off:off + size]
            off += size
            off += (-off) % 4
            if off > len(payload):
                raise CpioError(f"truncated padding for {name}")
            if magic == b"070702" and (sum(data) & 0xffffffff) != check:
                raise CpioError(f"CRC mismatch for {name}")
            entries.append(Entry(name, magic, header, name_area, data, payload[start:off], mode, nlink))
            if name == "TRAILER!!!":
                return cls(tuple(entries), payload[off:])

    def find(self, name: str) -> list[Entry]:
        return [entry for entry in self.entries if entry.normalized == name]

    def one(self, name: str) -> Entry:
        matches = self.find(name)
        if len(matches) != 1:
            raise CpioError(f"expected one {name!r}, found {len(matches)}")
        return matches[0]

    def replace(self, name: str, data: bytes) -> bytes:
        target = self.one(name)
        return b"".join(
            entry.replace_data(data) if entry is target else entry.raw
            for entry in self.entries
        ) + self.tail

    def assert_only_payload_changed(self, other: "Archive", name: str) -> None:
        if len(self.entries) != len(other.entries) or self.tail != other.tail:
            raise CpioError("entry count or trailer tail changed")
        changed: list[str] = []
        for before, after in zip(self.entries, other.entries, strict=True):
            if (before.name, before.mode, before.nlink) != (after.name, after.mode, after.nlink):
                raise CpioError(f"metadata changed for {before.name}")
            if hashlib.sha256(before.data).digest() != hashlib.sha256(after.data).digest():
                changed.append(before.normalized)
        if changed != [name]:
            raise CpioError(f"unexpected payload delta: {changed}")
