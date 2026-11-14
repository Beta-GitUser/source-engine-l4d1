"""Minimal VPK reader (versions 1 and 2).

Written so the launcher can answer three questions without the engine:

* does this mod's ``*_dir.vpk`` actually parse?
* are all of its ``_nnn.vpk`` parts present next to it?
* what is inside it (for `mod` debugging and for users who want to know what
  they are installing)?

Only reading is implemented; writing VPKs is the job of Valve's tooling.

Layout (both versions), little endian::

    uint32 signature  0x55AA1234
    uint32 version    1 | 2
    -- version 2 only --
    uint32 tree_size
    uint32 file_data_size
    uint32 archive_md5_size
    uint32 other_md5_size
    uint32 signature_size
    --------------------
    uint32 tree_size
    tree: extension\\0 { path\\0 { name\\0 crc u32, preload u16, archive u16,
                                  offset u32, length u32, 0xFFFF u16,
                                  preload[preload] } } \\0 \\0

``archive`` 0x7FFF means the file data lives in the dir VPK itself, otherwise it
is in ``<base>_<archive:03d>.vpk``.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

VPK_SIGNATURE = 0x55AA1234
DIR_DATA_ARCHIVE = 0x7FFF


class VPKError(RuntimeError):
    pass


@dataclass(frozen=True)
class VPKEntry:
    path: str          # "materials/models/x.vmt"
    crc32: int
    preload: bytes
    archive: int
    offset: int
    length: int

    @property
    def size(self) -> int:
        return len(self.preload) + self.length

    @property
    def stored_in_dir(self) -> bool:
        return self.archive == DIR_DATA_ARCHIVE


@dataclass
class VPKInfo:
    path: Path
    version: int
    tree_size: int
    entries: List[VPKEntry] = field(default_factory=list)

    @property
    def parts(self) -> List[Path]:
        """Expected ``_nnn.vpk`` part files for this dir VPK."""
        base = self.path.name
        if base.endswith("_dir.vpk"):
            base = base[: -len("_dir.vpk")]
        else:
            base = self.path.stem
        used = sorted({e.archive for e in self.entries if not e.stored_in_dir})
        return [self.path.parent / f"{base}_{index:03d}.vpk" for index in used]

    def missing_parts(self) -> List[Path]:
        return [part for part in self.parts if not part.is_file()]


def _read_cstring(data: bytes, pos: int) -> Tuple[str, int]:
    end = data.find(b"\x00", pos)
    if end < 0:
        raise VPKError("truncated string table")
    raw = data[pos:end]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return text, end + 1


class VPK:
    """An opened ``*_dir.vpk``; file data is read on demand."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.info = self._parse()

    # -- parsing -----------------------------------------------------------
    def _parse(self) -> VPKInfo:
        try:
            data = self.path.read_bytes()
        except OSError as exc:
            raise VPKError(f"cannot read {self.path}: {exc}") from exc

        if len(data) < 12:
            raise VPKError(f"{self.path.name}: too small to be a VPK")

        signature, version = struct.unpack_from("<II", data, 0)
        if signature != VPK_SIGNATURE:
            raise VPKError(f"{self.path.name}: bad signature 0x{signature:08x}")

        header_size = 12
        if version == 2:
            if len(data) < 28:
                raise VPKError(f"{self.path.name}: truncated version 2 header")
            header_size = 28
        elif version != 1:
            raise VPKError(f"{self.path.name}: unsupported VPK version {version}")

        (tree_size,) = struct.unpack_from("<I", data, 8)
        tree_start = header_size
        tree_end = tree_start + tree_size
        if tree_end > len(data):
            raise VPKError(f"{self.path.name}: tree size {tree_size} exceeds file")

        entries: List[VPKEntry] = []
        pos = tree_start
        while True:
            extension, pos = _read_cstring(data, pos)
            if extension == "":
                break
            while True:
                directory, pos = _read_cstring(data, pos)
                if directory == "":
                    break
                while True:
                    filename, pos = _read_cstring(data, pos)
                    if filename == "":
                        break
                    if pos + 18 > tree_end:
                        raise VPKError(f"{self.path.name}: truncated entry for {filename}")
                    crc, preload_size, archive, offset, length = struct.unpack_from(
                        "<IHHII", data, pos
                    )
                    pos += 16
                    (terminator,) = struct.unpack_from("<H", data, pos)
                    pos += 2
                    if terminator != 0xFFFF:
                        raise VPKError(f"{self.path.name}: bad entry terminator for {filename}")
                    preload = b""
                    if preload_size:
                        preload = data[pos:pos + preload_size]
                        pos += preload_size

                    # Valve stores root-level files under the path " " because
                    # an empty path string terminates the directory list.
                    if directory and directory != " ":
                        full = f"{directory}/{filename}.{extension}"
                    else:
                        full = f"{filename}.{extension}"
                    entries.append(VPKEntry(full.replace("\\", "/"), crc, preload,
                                            archive, offset, length))

        return VPKInfo(path=self.path, version=version, tree_size=tree_size, entries=entries)

    # -- access ------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.info.entries)

    def __iter__(self) -> Iterator[VPKEntry]:
        return iter(self.info.entries)

    def find(self, name: str) -> Optional[VPKEntry]:
        wanted = name.replace("\\", "/").lstrip("/").lower()
        for entry in self.info.entries:
            if entry.path.lower() == wanted:
                return entry
        return None

    def glob(self, suffix: str) -> List[VPKEntry]:
        suffix = suffix.lower().lstrip(".")
        return [e for e in self.info.entries if e.path.lower().endswith("." + suffix)]

    def _part_path(self, archive: int) -> Path:
        base = self.path.name
        if base.endswith("_dir.vpk"):
            base = base[: -len("_dir.vpk")]
        else:
            base = self.path.stem
        return self.path.parent / f"{base}_{archive:03d}.vpk"

    def read(self, name: str) -> bytes:
        entry = self.find(name)
        if entry is None:
            raise VPKError(f"{name} is not in {self.path.name}")
        if entry.stored_in_dir:
            data = self.path.read_bytes()
            return entry.preload + data[entry.offset:entry.offset + entry.length]

        part = self._part_path(entry.archive)
        if not part.is_file():
            raise VPKError(f"missing part {part.name} for {name}")
        with part.open("rb") as handle:
            handle.seek(entry.offset)
            return entry.preload + handle.read(entry.length)

    def verify(self) -> List[str]:
        """Structural checks; returns human-readable problems."""
        problems: List[str] = []
        for part in self.info.missing_parts():
            problems.append(f"missing part file: {part.name}")
        for entry in self.info.entries:
            if entry.length == 0 and entry.preload == b"":
                problems.append(f"zero-length entry: {entry.path}")
                break
        if not self.info.entries:
            problems.append("VPK contains no files")
        return problems

    def crc_matches(self, name: str) -> bool:
        entry = self.find(name)
        if entry is None:
            return False
        return zlib.crc32(self.read(name)) & 0xFFFFFFFF == entry.crc32

    def extract_all(self, destination: Path, *, overwrite: bool = False) -> int:
        destination = Path(destination)
        written = 0
        for entry in self.info.entries:
            target = destination / entry.path
            if target.exists() and not overwrite:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.read(entry.path))
            written += 1
        return written


def open_vpk(path: Path) -> VPK:
    return VPK(Path(path))


def find_dir_vpk(folder: Path) -> Optional[Path]:
    folder = Path(folder)
    if folder.is_file() and folder.name.endswith("_dir.vpk"):
        return folder
    candidates = sorted(p for p in folder.glob("*_dir.vpk") if p.is_file())
    return candidates[0] if candidates else None


def summarise(path: Path) -> Dict[str, object]:
    """One-line-dict summary used by `vpk info` and the mod validator."""
    vpk = VPK(Path(path))
    payload = sum(e.size for e in vpk.info.entries)
    return {
        "path": str(vpk.path),
        "version": vpk.info.version,
        "entries": len(vpk.info.entries),
        "payload_bytes": payload,
        "parts": [p.name for p in vpk.info.parts],
        "missing_parts": [p.name for p in vpk.info.missing_parts()],
        "problems": vpk.verify(),
    }


def build_vpk_v1(target: Path, files: Dict[str, bytes], *, split: bool = False,
                 version: int = 1) -> Path:
    """Write a minimal VPK, version 1 unless asked otherwise.

    Version 2 changes exactly two things: the header gains four uint32s (file data
    size, archive md5 size, other md5 size, signature size) so the tree starts at
    28 instead of 12, and ``tree_size`` stays at offset 8 in both. Reading the tree
    size from after the version-2 header instead truncated the index - which is
    what made packs look like they had no ``scripts/`` folder.

    With ``split=False`` all payload lives in the dir file (archive index
    0x7FFF). With ``split=True`` it goes into ``<base>_000.vpk`` and entries
    reference that part, which is how real multi-hundred-MB mods are shipped.

    Only used by the tests and fixtures; shipping mods is Valve tooling's job.
    """
    target = Path(target)

    by_ext: Dict[str, Dict[str, List[Tuple[str, bytes]]]] = {}
    for name, content in files.items():
        norm = name.replace("\\", "/").lstrip("/")
        stem, dot, extension = norm.rpartition(".")
        if not dot or not extension:
            # The empty extension string is the tree terminator, so a VPK
            # cannot represent a file without one.
            raise ValueError(f"VPK entries must have an extension: {name!r}")
        directory, _, filename = stem.rpartition("/")
        by_ext.setdefault(extension, {}).setdefault(directory, []).append((filename, content))

    # Layout: ext "" { path "" { name + entry, "" } "" } "". Build the tree with
    # placeholder offsets first, then patch them once its size is known.
    archive = 0 if split else DIR_DATA_ARCHIVE
    tree = bytearray()
    drafts: List[int] = []  # tree offset of each entry
    ordered: List[bytes] = []
    for extension, directories in sorted(by_ext.items()):
        tree += extension.encode() + b"\x00"
        for directory, entries in sorted(directories.items()):
            tree += (directory or " ").encode() + b"\x00"
            for filename, content in sorted(entries):
                tree += filename.encode() + b"\x00"
                drafts.append(len(tree))
                ordered.append(content)
                tree += struct.pack("<IHHII", zlib.crc32(content) & 0xFFFFFFFF, 0,
                                    archive, 0, len(content))
                tree += struct.pack("<H", 0xFFFF)
            tree += b"\x00"  # end of this directory's file list
        tree += b"\x00"      # end of this extension's path list
    tree += b"\x00"          # end of the extension list

    tree_size = len(tree)
    header_size = 28 if version == 2 else 12
    cursor = 0 if split else header_size + tree_size
    for position, content in zip(drafts, ordered):
        struct.pack_into("<I", tree, position + 8, cursor)
        cursor += len(content)

    if version == 2:
        header = struct.pack("<IIIIIII", VPK_SIGNATURE, 2, tree_size,
                             0 if split else sum(len(item) for item in ordered),
                             0, 0, 0)
    elif version == 1:
        header = struct.pack("<III", VPK_SIGNATURE, 1, tree_size)
    else:
        raise ValueError(f"no builder for VPK version {version}")
    dir_blob = header + bytes(tree)
    if split:
        dir_blob += b""  # payload lives in the part file
        base = target.name[: -len("_dir.vpk")] if target.name.endswith("_dir.vpk") else target.stem
        target.parent.mkdir(parents=True, exist_ok=True)
        (target.parent / f"{base}_000.vpk").write_bytes(b"".join(ordered))
    else:
        dir_blob += b"".join(ordered)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(dir_blob)
    return target


def build_vpk_v2(target: Path, files: Dict[str, bytes], *, split: bool = False) -> Path:
    """Write a minimal v2 VPK: the header layout the games here actually use."""
    return build_vpk_v1(target, files, split=split, version=2)
