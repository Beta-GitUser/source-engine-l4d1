#!/usr/bin/env python3
"""What is this scenes/scenes.image?  Standalone: stdlib only, no l4d1ctl needed.

    python3 scene-image.py /storage/emulated/0/srceng/left4dead
    python3 scene-image.py /storage/emulated/0/srceng/left4dead/pak01_dir.vpk
    python3 scene-image.py ~/scenes.image

Reads the image out of a pack (or off disk), then prints the header the engine
refuses it on: the four-byte id, the version, the scene/string counts, whether
the entry table fits in the bytes that exist, and the first entries and strings.

This build wants id 'VSIF' - stored as the bytes 46 49 53 56 ('FISV') - and
version 2. Source: public/scenefilecache/SceneImageFile.h
"""

import struct
import sys
from pathlib import Path

WANT_ID = b"FISV"      # MAKEID('V','S','I','F') little endian
WANT_VERSION = 2
IMAGE = "scenes/scenes.image"


def read_vpk_entry(vpk_path, wanted=IMAGE):
    """Return (bytes, note) for `wanted` inside a VPK 1 or 2 dir file."""
    blob = vpk_path.read_bytes()
    magic, version = struct.unpack_from("<II", blob, 0)
    if magic != 0x55AA1234:
        raise SystemExit(f"{vpk_path.name}: not a VPK (signature 0x{magic:08x})")
    pos = 8
    if version == 2:
        pos += 20                      # tree size, file data, 3 md5/signature sizes
    tree_size = struct.unpack_from("<I", blob, pos)[0]
    pos += 4
    end = pos + tree_size

    def cstr(at):
        stop = blob.index(b"\0", at)
        return blob[at:stop].decode("ascii", "replace"), stop + 1

    while pos < end:
        ext, pos = cstr(pos)
        if not ext:
            break
        while True:
            folder, pos = cstr(pos)
            if not folder:
                break
            while True:
                name, pos = cstr(pos)
                if not name:
                    break
                crc, preload, archive, offset, length, _term = struct.unpack_from(
                    "<IHHIIH", blob, pos)
                pos += 18
                pre = blob[pos:pos + preload]
                pos += preload
                if f"{folder}/{name}.{ext}".lower() != wanted.lower():
                    continue
                if archive == 0x7FFF:
                    data = pre + blob[offset:offset + length]
                    return data, f"stored in {vpk_path.name}"
                part = vpk_path.parent / (
                    vpk_path.name.replace("_dir.vpk", f"_{archive:03d}.vpk"))
                if not part.is_file():
                    raise SystemExit(f"missing part {part.name} for {wanted}")
                with part.open("rb") as handle:
                    handle.seek(offset)
                    data = pre + handle.read(length)
                return data, f"{length} bytes in {part.name} at {offset}"
    raise SystemExit(f"{wanted} is not in {vpk_path.name} "
                     f"({version}, {tree_size}-byte tree)")


def locate(target):
    target = Path(target).expanduser()
    if target.is_file() and target.suffix.lower() == ".vpk":
        data, note = read_vpk_entry(target)
        return data, f"{target.name} -> {IMAGE} ({note})"
    if target.is_file():
        return target.read_bytes(), str(target)
    if not target.is_dir():
        raise SystemExit(f"no such file or directory: {target}")
    loose = target / IMAGE
    if loose.is_file():
        return loose.read_bytes(), str(loose)
    packs = sorted(target.glob("*_dir.vpk"),
                   key=lambda p: (not p.name.lower().startswith("pak01"), p.name.lower()))
    for pack in packs:
        try:
            data, note = read_vpk_entry(pack)
            return data, f"{pack.name} -> {IMAGE} ({note})"
        except SystemExit:
            continue
    raise SystemExit(f"no {IMAGE} under {target} (no loose file, none in "
                     f"{len(packs)} *_dir.vpk)")


def dump(image, source):
    print(f"source: {source}")
    print(f"size:   {len(image)} bytes")
    print()
    head = image[:64]
    print(f"first {len(head)} bytes:")
    for start in range(0, len(head), 16):
        chunk = head[start:start + 16]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {start:04x}  {' '.join(f'{b:02x}' for b in chunk):<47}  {text}")
    print()

    if len(image) < 20:
        print(f"verdict: {len(image)} bytes is too short to hold the 20-byte header.")
        return 1

    n_id, version, scenes, strings, entry_offset = struct.unpack_from("<5i", image, 0)
    ident = image[:4]
    label = "VSIF" if ident == WANT_ID else "'%s'%s" % (
        "".join(chr(b) if 32 <= b < 127 else "." for b in ident),
        " (byte order reversed; Valve writes FISV)" if ident == b"VSIF" else "")

    print("header:")
    print(f"  id                {label}   (want VSIF = bytes 46 49 53 56)")
    print(f"  version           {version}   (want {WANT_VERSION})")
    print(f"  nNumScenes        {scenes}")
    print(f"  nNumStrings       {strings}")
    print(f"  nSceneEntryOffset {entry_offset}")

    problems = []
    if ident not in (WANT_ID, b"VSIF"):
        problems.append("id does not match: not a Source scene image")
    if version != WANT_VERSION:
        problems.append(f"version {version}, want {WANT_VERSION}")
    if scenes <= 0:
        problems.append(f"nNumScenes is {scenes}")
    if entry_offset < 20:
        problems.append(f"nSceneEntryOffset {entry_offset} points inside the header")
    elif scenes > 0 and entry_offset + 16 * scenes > len(image):
        problems.append(f"entry table ends at {entry_offset + 16 * scenes}, "
                        f"file is {len(image)} - truncated")

    if strings > 0 and 20 + 4 * strings <= len(image):
        print("  first strings:")
        for index in range(min(strings, 8)):
            offset = struct.unpack_from("<I", image, 20 + 4 * index)[0]
            if 0 <= offset < len(image):
                stop = image.find(b"\0", offset)
                print(f"    {image[offset:stop if stop > 0 else offset + 64]!r}")

    if scenes > 0 and entry_offset + 16 <= len(image):
        print("  first entries (crc32, data offset, length, summary offset):")
        for index in range(min(scenes, (len(image) - entry_offset) // 16, 8)):
            crc, off, length, summary = struct.unpack_from("<Iiii", image,
                                                           entry_offset + 16 * index)
            outside = "" if 0 <= off and 0 <= length and off + length <= len(image) \
                else "   OUTSIDE THE FILE"
            print(f"    [{index}] {crc:08x}  {off:>10}  {length:>9}  {summary:>10}{outside}")
    print()

    for problem in problems:
        print(f"problem: {problem}")
    if not problems:
        print("verdict: this build should accept it. If the engine still rejects it, it "
              "is reading a different copy: a loose file shadowing the pack, or another "
              "pack mounted first.")
        return 0
    if ident in (WANT_ID, b"VSIF") and version != WANT_VERSION:
        print("verdict: a scene image, but of another version. The stock engine makes that "
              "fatal; this port warns and loads scenes individually.")
    elif ident not in (WANT_ID, b"VSIF"):
        print("verdict: not a scene image: zeros suggest a failed read, gzip/LZMA a "
              "compressed stream. Look at the pack, not the format.")
    else:
        print("verdict: the header does not hold together; the copy in the pack is likely "
              "truncated.")
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.strip().splitlines()[2].strip())
    sys.exit(dump(*locate(sys.argv[1])))
