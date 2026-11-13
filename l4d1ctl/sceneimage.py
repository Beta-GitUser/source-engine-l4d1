"""What is ``scenes/scenes.image`` really?

The engine refuses a scene image on a two-field check and says nothing else:

    CSceneFileCache: Bad scene image file scenes/scenes.image

which is enough to know that *something* about the header is wrong, and nothing
about what the file actually contains. This reads the file and says what it is:
the four-byte id, the version, how many scenes and strings it claims to hold,
whether those claims fit in the file, and what the first entries look like.

It reads from three kinds of place, because the file lives in different ones
depending on how the content was installed:

* a game directory - loose ``scenes/scenes.image`` first, then ``pak01_dir.vpk``;
* a ``*_dir.vpk`` directly;
* the extracted file itself.

Layout it expects (``public/scenefilecache/SceneImageFile.h``)::

    int nId                 MAKEID('V','S','I','F') -> bytes 46 49 53 56
    int nVersion            2 in this tree
    int nNumScenes
    int nNumStrings
    int nSceneEntryOffset   entries, sorted by crc of "scenes\\\\name.vcd"
    ...                     string table: nNumStrings u32 offsets from header
    entries: crcFilename, nDataOffset, nDataLength, nSceneSummaryOffset (v2)

Nothing here is fatal: it is a reader, and every uncertainty is reported as an
observation rather than a guess dressed up as a fact.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import vpk

SCENE_IMAGE_PATH = "scenes/scenes.image"
# MAKEID('V','S','I','F') is 0x56534946 as an integer, which lands in the file as
# the bytes 46 49 53 56 ("FISV") on a little-endian machine.
SCENE_IMAGE_ID = 0x56534946
SCENE_IMAGE_FILE_BYTES = b"FISV"
SCENE_IMAGE_VERSION = 2

HEADER = struct.Struct("<5i")
ENTRY = struct.Struct("<Iiii")
HEADER_SIZE = HEADER.size


class SceneImageError(RuntimeError):
    pass


@dataclass
class Source:
    """Where the bytes came from, in words that match what a user sees."""

    label: str
    data: bytes
    # filled in when the bytes came out of a pack
    archive: Optional[str] = None
    entry_size: Optional[int] = None
    stored_in_part: Optional[str] = None


@dataclass
class Report:
    source: Source
    size: int
    file_id: bytes = b""
    id_text: str = ""
    version: Optional[int] = None
    num_scenes: Optional[int] = None
    num_strings: Optional[int] = None
    entry_offset: Optional[int] = None
    entries: List[dict] = field(default_factory=list)
    strings: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    verdict: str = ""
    ok: bool = False
    head: bytes = b""

    def to_dict(self) -> dict:
        return {
            "source": self.source.label,
            "archive": self.source.archive,
            "size": self.size,
            "id": self.id_text,
            "id_bytes": self.file_id.hex(),
            "version": self.version,
            "expected": {"id": "VSIF", "version": SCENE_IMAGE_VERSION},
            "num_scenes": self.num_scenes,
            "num_strings": self.num_strings,
            "entry_offset": self.entry_offset,
            "entries": self.entries,
            "strings": self.strings,
            "problems": self.problems,
            "ok": self.ok,
            "verdict": self.verdict,
        }


def _read_from_vpk(archive: vpk.VPK) -> Source:
    entry = archive.find(SCENE_IMAGE_PATH)
    if entry is None:
        raise SceneImageError(f"{SCENE_IMAGE_PATH} is not in {archive.path.name}")
    data = archive.read(SCENE_IMAGE_PATH)
    part = None if entry.stored_in_dir else archive._part_path(entry.archive).name
    return Source(label=f"{archive.path.name} -> {SCENE_IMAGE_PATH}", data=data,
                  archive=archive.path.name, entry_size=entry.size, stored_in_part=part)


def locate(target: Path) -> Source:
    """Find the scene image bytes under a directory, a pack, or a file."""
    target = Path(target).expanduser()

    if target.is_file() and target.suffix.lower() == ".vpk":
        return _read_from_vpk(vpk.VPK(target))
    if target.is_file():
        return Source(label=str(target), data=target.read_bytes())
    if not target.is_dir():
        raise SceneImageError(f"no such file or directory: {target}")

    loose = target / SCENE_IMAGE_PATH
    if loose.is_file():
        return Source(label=str(loose), data=loose.read_bytes())

    candidates = sorted(p for p in target.glob("*_dir.vpk") if p.is_file())
    # pak01 first: it is the pack L4D1 keeps its content in.
    candidates.sort(key=lambda p: (not p.name.lower().startswith("pak01"), p.name.lower()))
    for candidate in candidates:
        archive = vpk.VPK(candidate)
        if archive.find(SCENE_IMAGE_PATH) is not None:
            return _read_from_vpk(archive)

    raise SceneImageError(
        f"no {SCENE_IMAGE_PATH} under {target} (looked for the loose file and in "
        f"{len(candidates)} *_dir.vpk)")
def _printable(raw: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in raw)


def _string_at(data: bytes, offset: int) -> str:
    if not 0 <= offset < len(data):
        return ""
    end = data.find(b"\0", offset)
    if end == -1:
        end = min(len(data), offset + 64)
    return data[offset:end].decode("ascii", "replace")


def parse(source: Source) -> Report:
    data = source.data
    report = Report(source=source, size=len(data), head=data[:64])

    if len(data) < HEADER_SIZE:
        report.problems.append(
            f"only {len(data)} bytes: too short to hold the 20-byte header")
        report.verdict = (f"{len(data)} bytes is too short to be a scene image: the header "
                          "alone is 20 bytes. The file is either truncated or not a scene "
                          "image at all, and nothing in it can be read.")
        return report

    file_id = data[:4]
    n_id, n_version, n_scenes, n_strings, n_entry_offset = HEADER.unpack_from(data, 0)

    report.file_id = file_id
    report.version = n_version
    report.num_scenes = n_scenes
    report.num_strings = n_strings
    report.entry_offset = n_entry_offset

    # MAKEID stores 'V','S','I','F' as a big-endian integer, which on disk is
    # "FISV". Accept the other order too, but say so - it means someone wrote the
    # id as an integer without pinning the byte order.
    if file_id == SCENE_IMAGE_FILE_BYTES:
        report.id_text = "VSIF"
    elif file_id == b"VSIF":
        report.id_text = "VSIF (byte order reversed: the file starts 'VSIF', Valve writes 'FISV')"
    else:
        report.id_text = _printable(file_id)

    if file_id not in (SCENE_IMAGE_FILE_BYTES, b"VSIF"):
        report.problems.append(f"id is {file_id.hex()} ('{_printable(file_id)}'), expected 'FISV'")
        if file_id == b"\0\0\0\0":
            report.verdict = ("The first four bytes are zero, which is what a failed or "
                              "stubbed read looks like, not a different format. Suspect the "
                              "pack read rather than the file.")
        elif data[:2] == b"\x1f\x8b":
            report.verdict = "It is gzip data, not a scene image."
        elif data[:4] in (b"LZMA", b"\x5d\x00\x00\x00") or data[1:5] == b"LZMA":
            report.verdict = "It is an LZMA stream, not a scene image (a compressed pack entry?)."
        else:
            report.verdict = ("Not a Source scene image: the id does not match. Whatever the "
                              "engine read is either a different file or not a file it can use.")
        report.ok = False
        return report

    if n_version != SCENE_IMAGE_VERSION:
        report.problems.append(
            f"version {n_version}, this build wants {SCENE_IMAGE_VERSION}")

    # Do the header's own claims fit in the bytes we have? This is how a truncated
    # or partially read file shows itself.
    if n_scenes <= 0:
        report.problems.append(f"nNumScenes is {n_scenes}")
    if n_strings < 0:
        report.problems.append(f"nNumStrings is {n_strings}")
    if n_entry_offset < HEADER_SIZE:
        report.problems.append(
            f"nSceneEntryOffset {n_entry_offset} points inside the header")
    elif n_scenes > 0 and n_entry_offset + n_scenes * ENTRY.size > len(data):
        needed = n_entry_offset + n_scenes * ENTRY.size
        report.problems.append(
            f"the entry table ends at {needed} but the file is {len(data)} bytes")

    # String table sits immediately after the header.
    if n_strings > 0 and HEADER_SIZE + 4 * n_strings <= len(data):
        for index in range(min(n_strings, 8)):
            offset = struct.unpack_from("<I", data, HEADER_SIZE + 4 * index)[0]
            report.strings.append(_string_at(data, offset))

    # Entries, as far as they are readable.
    if (n_scenes > 0 and HEADER_SIZE <= n_entry_offset
            and n_entry_offset + ENTRY.size <= len(data)):
        available = min(n_scenes, (len(data) - n_entry_offset) // ENTRY.size, 16)
        for index in range(available):
            crc, data_offset, data_length, summary_offset = ENTRY.unpack_from(
                data, n_entry_offset + index * ENTRY.size)
            entry = {
                "crc32": f"{crc:08x}",
                "data_offset": data_offset,
                "data_length": data_length,
                "summary_offset": summary_offset,
            }
            if data_length < 0 or data_offset < 0 or data_offset + data_length > len(data):
                entry["outside_file"] = True
                if len(report.problems) < 12:
                    report.problems.append(
                        f"entry {index} points at {data_offset}+{data_length}, "
                        f"which is outside {len(data)} bytes")
            report.entries.append(entry)

    report.ok = report.id_text.startswith("VSIF") and n_version == SCENE_IMAGE_VERSION \
        and not report.problems

    if report.ok:
        report.verdict = (
            "A scene image this build should accept (id VSIF, version "
            f"{n_version}, {n_scenes} scene{'' if n_scenes == 1 else 's'}, "
            f"{n_strings} string{'' if n_strings == 1 else 's'}, {len(data)} bytes). "
            "If the engine still rejects it, it is reading a different file than this "
            "one: check which pack is mounted first, or whether a loose copy shadows it.")
    elif n_version != SCENE_IMAGE_VERSION and report.id_text.startswith("VSIF"):
        report.verdict = (
            f"A scene image, but version {n_version} where this build requires "
            f"{SCENE_IMAGE_VERSION}. The stock engine makes that fatal; this port now "
            "warns and loads scenes individually. The version 1 layout is not described "
            "anywhere in this tree, so the numbers above are read as if it were v2 and "
            "are only trustworthy as far as they are self-consistent.")
    else:
        report.verdict = (
            "The header does not hold together: see the problems above. If the size and "
            "the entry table disagree, the file in the pack is truncated.")
    return report


def render(report: Report, *, dump: int = 64) -> str:
    lines: List[str] = []
    lines.append(f"source:  {report.source.label}")
    if report.source.archive:
        part = report.source.stored_in_part or "the dir vpk itself"
        lines.append(f"pack:    {report.source.archive} "
                     f"(entry {report.source.entry_size} bytes, data in {part})")
    lines.append(f"size:    {report.size} bytes")
    lines.append("")

    head = report.head[:dump]
    lines.append(f"first {len(head)} bytes:")
    for start in range(0, len(head), 16):
        chunk = head[start:start + 16]
        hexed = " ".join(f"{b:02x}" for b in chunk)
        lines.append(f"  {start:04x}  {hexed:<47}  {_printable(chunk)}")
    lines.append("")

    if report.version is not None:
        lines.append("header:")
        lines.append(f"  id                {report.id_text}")
        lines.append(f"  version           {report.version}"
                     f"   (this build wants {SCENE_IMAGE_VERSION})")
        lines.append(f"  nNumScenes        {report.num_scenes}")
        lines.append(f"  nNumStrings       {report.num_strings}")
        lines.append(f"  nSceneEntryOffset {report.entry_offset}")
        if report.strings:
            lines.append("  first strings:")
            for text in report.strings:
                lines.append(f"    {text!r}")
        if report.entries:
            lines.append("  first entries (crc32, offset, length, summary offset):")
            for index, entry in enumerate(report.entries[:8]):
                flag = "  OUTSIDE THE FILE" if entry.get("outside_file") else ""
                lines.append(f"    [{index}] {entry['crc32']}  {entry['data_offset']:>10}"
                             f"  {entry['data_length']:>9}  {entry['summary_offset']:>10}{flag}")
        lines.append("")

    if report.problems:
        lines.append("problems:")
        for problem in report.problems:
            lines.append(f"  - {problem}")
        lines.append("")

    lines.append(f"verdict: {report.verdict}")
    return "\n".join(lines)


__all__ = [
    "SCENE_IMAGE_PATH",
    "SCENE_IMAGE_VERSION",
    "Report",
    "SceneImageError",
    "Source",
    "locate",
    "parse",
    "render",
]
