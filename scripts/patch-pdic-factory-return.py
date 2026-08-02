#!/usr/bin/env python3
"""Patch the exact prebuilt PDIC helper to report USB factory mode.

This is a recovery-only diagnostic patch. It changes one AArch64 instruction
inside check_factory_mode_boot():

    mov w0, wzr   ->   mov w0, #1

The script parses the ELF rather than relying on a hard-coded file offset and
refuses any module whose symbol layout, instruction bytes, architecture, or
signature marker differs from the proven A33 TWRP module.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
import sys


ELF_MAGIC = b"\x7fELF"
EM_AARCH64 = 183
SHT_SYMTAB = 2

ORIGINAL_INSN = bytes.fromhex("e0031f2a")  # mov w0, wzr
PATCHED_INSN = bytes.fromhex("20008052")   # mov w0, #1
SYMBOL_NAME = "check_factory_mode_boot"
SYMBOL_RETURN_OFFSET = 0x28
EXPECTED_SYMBOL_SIZE = 0x3C
MODULE_SIGNATURE_MARKER = b"~Module signature appended~\n"


class PatchError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def c_string(table: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(table):
        raise PatchError(f"invalid string-table offset {offset}")
    end = table.find(b"\0", offset)
    if end < 0:
        raise PatchError("unterminated ELF string")
    return table[offset:end].decode("utf-8", errors="strict")


def parse_elf(data: bytes) -> tuple[list[dict[str, int | str]], dict[str, tuple[int, int, int]]]:
    if len(data) < 64 or data[:4] != ELF_MAGIC:
        raise PatchError("not an ELF file")
    if data[4] != 2:
        raise PatchError("expected ELF64")
    if data[5] != 1:
        raise PatchError("expected little-endian ELF")

    (
        _e_type,
        e_machine,
        _e_version,
        _e_entry,
        _e_phoff,
        e_shoff,
        _e_flags,
        _e_ehsize,
        _e_phentsize,
        _e_phnum,
        e_shentsize,
        e_shnum,
        e_shstrndx,
    ) = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)

    if e_machine != EM_AARCH64:
        raise PatchError(f"expected AArch64 ELF machine {EM_AARCH64}, found {e_machine}")
    if e_shentsize != 64:
        raise PatchError(f"unexpected section-header size {e_shentsize}")
    if e_shnum == 0 or e_shstrndx >= e_shnum:
        raise PatchError("invalid section-header table")
    if e_shoff + e_shnum * e_shentsize > len(data):
        raise PatchError("section-header table exceeds file")

    raw_sections: list[tuple[int, int, int, int, int, int, int, int, int, int]] = []
    for index in range(e_shnum):
        raw_sections.append(
            struct.unpack_from("<IIQQQQIIQQ", data, e_shoff + index * e_shentsize)
        )

    shstr = raw_sections[e_shstrndx]
    shstr_offset = shstr[4]
    shstr_size = shstr[5]
    if shstr_offset + shstr_size > len(data):
        raise PatchError("section-name string table exceeds file")
    shstr_data = data[shstr_offset : shstr_offset + shstr_size]

    sections: list[dict[str, int | str]] = []
    for index, raw in enumerate(raw_sections):
        name_offset, sh_type, flags, addr, offset, size, link, info, align, entsize = raw
        if offset + size > len(data) and sh_type != 8:  # SHT_NOBITS has no file data
            raise PatchError(f"section {index} exceeds file")
        sections.append(
            {
                "index": index,
                "name": c_string(shstr_data, name_offset),
                "type": sh_type,
                "flags": flags,
                "addr": addr,
                "offset": offset,
                "size": size,
                "link": link,
                "info": info,
                "align": align,
                "entsize": entsize,
            }
        )

    symbols: dict[str, tuple[int, int, int]] = {}
    for section in sections:
        if section["type"] != SHT_SYMTAB:
            continue
        entsize = int(section["entsize"])
        if entsize != 24:
            raise PatchError(f"unexpected symbol entry size {entsize}")
        link = int(section["link"])
        if link >= len(sections):
            raise PatchError("symbol table references invalid string table")
        strtab_section = sections[link]
        strtab_offset = int(strtab_section["offset"])
        strtab_size = int(strtab_section["size"])
        strtab = data[strtab_offset : strtab_offset + strtab_size]

        sym_offset = int(section["offset"])
        sym_size = int(section["size"])
        for entry_offset in range(sym_offset, sym_offset + sym_size, entsize):
            st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from(
                "<IBBHQQ", data, entry_offset
            )
            if st_name == 0:
                continue
            name = c_string(strtab, st_name)
            if name == SYMBOL_NAME:
                symbols[name] = (st_value, st_size, st_shndx)

    return sections, symbols


def locate_patch(data: bytes) -> tuple[int, int, int]:
    sections, symbols = parse_elf(data)
    if SYMBOL_NAME not in symbols:
        raise PatchError(f"missing symbol {SYMBOL_NAME}")

    symbol_value, symbol_size, symbol_section_index = symbols[SYMBOL_NAME]
    if symbol_size != EXPECTED_SYMBOL_SIZE:
        raise PatchError(
            f"unexpected {SYMBOL_NAME} size: expected 0x{EXPECTED_SYMBOL_SIZE:x}, "
            f"found 0x{symbol_size:x}"
        )
    if symbol_section_index >= len(sections):
        raise PatchError("symbol references invalid section")

    section = sections[symbol_section_index]
    if section["name"] != ".text":
        raise PatchError(f"{SYMBOL_NAME} is in {section['name']!r}, not .text")

    target_address = symbol_value + SYMBOL_RETURN_OFFSET
    section_address = int(section["addr"])
    section_size = int(section["size"])
    section_offset = int(section["offset"])

    relative = target_address - section_address
    if relative < 0 or relative + 4 > section_size:
        raise PatchError("target instruction falls outside .text")

    file_offset = section_offset + relative
    if file_offset + 4 > len(data):
        raise PatchError("target instruction falls outside file")

    return file_offset, symbol_value, symbol_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--verify-patched", action="store_true")
    args = parser.parse_args()

    if not args.module.is_file():
        parser.error(f"module does not exist: {args.module}")
    if args.verify_patched and args.output is not None:
        parser.error("--verify-patched cannot be combined with --output")

    data = args.module.read_bytes()
    if MODULE_SIGNATURE_MARKER in data[-4096:]:
        raise PatchError("module has an appended signature; refusing to modify it")

    file_offset, symbol_value, symbol_size = locate_patch(data)
    current = data[file_offset : file_offset + 4]

    if args.verify_patched:
        if current != PATCHED_INSN:
            raise PatchError(
                f"factory return is not patched at file offset 0x{file_offset:x}: "
                f"found {current.hex()}"
            )
        output_data = data
        status = "verified-patched"
        output_path = args.module
    else:
        if args.output is None:
            parser.error("--output is required unless --verify-patched is used")
        if current != ORIGINAL_INSN:
            raise PatchError(
                f"unexpected original instruction at file offset 0x{file_offset:x}: "
                f"expected {ORIGINAL_INSN.hex()}, found {current.hex()}"
            )
        patched = bytearray(data)
        patched[file_offset : file_offset + 4] = PATCHED_INSN
        output_data = bytes(patched)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(output_data)
        output_path = args.output
        status = "patched"

        verify_offset, verify_value, verify_size = locate_patch(output_data)
        if (verify_offset, verify_value, verify_size) != (
            file_offset,
            symbol_value,
            symbol_size,
        ):
            raise PatchError("ELF layout changed unexpectedly after patch")
        if output_data[file_offset : file_offset + 4] != PATCHED_INSN:
            raise PatchError("patched instruction verification failed")

    report_lines = [
        f"status={status}",
        f"module={args.module}",
        f"output={output_path}",
        f"symbol={SYMBOL_NAME}",
        f"symbol_value=0x{symbol_value:x}",
        f"symbol_size=0x{symbol_size:x}",
        f"instruction_offset=0x{SYMBOL_RETURN_OFFSET:x}",
        f"file_offset=0x{file_offset:x}",
        f"original_instruction={ORIGINAL_INSN.hex()}",
        f"patched_instruction={PATCHED_INSN.hex()}",
        f"input_sha256={sha256(data)}",
        f"output_sha256={sha256(output_data)}",
    ]

    text = "\n".join(report_lines) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PatchError as error:
        print(f"REFUSING PDIC FACTORY PATCH: {error}", file=sys.stderr)
        raise SystemExit(1)
