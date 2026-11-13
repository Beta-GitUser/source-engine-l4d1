#!/usr/bin/env python3
"""Is the surface property manifest there, and does the chain behind it work?

    python3 surface-manifest.py /storage/emulated/0/srceng
    python3 surface-manifest.py /storage/emulated/0/srceng/left4dead
    python3 surface-manifest.py /storage/emulated/0/srceng/left4dead/pak01_dir.vpk

Standalone: stdlib only, no l4d1ctl needed. Reads every ``*_dir.vpk`` under the
root plus any loose files, then answers the three questions separately.

1. Is ``scripts/surfaceproperties_manifest.txt`` anywhere - loose, or inside a
   pack - and is it spelled the way the engine asks for it? Pack lookups compare
   with ``V_stricmp``, so inside a VPK the spelling does not matter; a loose file
   is whatever the filesystem says it is.
2. What would the engine get out of it? ``KeyValues::LoadFromBuffer`` never fails:
   a file whose first token is not followed by ``{`` gets ``missing {`` and
   contributes nothing, a subkey not named ``file`` gets ``bogus file type`` and
   is skipped. So a manifest that exists and still loads nothing is a shape
   problem, and "Unable to load manifest file" can only mean unopenable or empty.
3. Does each file it names exist, parse, and do its ``base`` references land on
   surfaces that are actually defined.

Exit status: 0 the chain resolves, 1 the manifest is there but something behind it
is missing or broken, 2 there is no manifest in this content at all.
"""

import struct
import sys
from pathlib import Path

MANIFEST = "scripts/surfaceproperties_manifest.txt"
PREFIX = "scripts/surfaceproperties"

# What builds before the manifest hard-coded; this branch loads the first that reads.
FALLBACKS = (
    "scripts/surfaceproperties_left4dead.txt",
    "scripts/surfaceproperties.txt",
    "scripts/surfaceproperties_ep2.txt",
)

VPK_SIGNATURE = 0x55AA1234


# ---------------------------------------------------------------------------
# Reading packs
# ---------------------------------------------------------------------------

def _cstring(blob, at):
    stop = blob.index(b"\0", at)
    return blob[at:stop].decode("ascii", "replace"), stop + 1


class Pack:
    """The index of one ``*_dir.vpk``: enough to list names and read entries."""

    def __init__(self, path):
        self.path = Path(path)
        blob = self.path.read_bytes()
        if len(blob) < 12:
            raise ValueError(f"{self.path.name}: too short to be a VPK")
        magic, version = struct.unpack_from("<II", blob, 0)
        if magic != VPK_SIGNATURE:
            raise ValueError(f"{self.path.name}: not a VPK (0x{magic:08x})")
        pos = 8
        if version == 2:
            pos += 20                      # tree size, data size, 3 signature sizes
        tree_size = struct.unpack_from("<I", blob, pos)[0]
        pos += 4
        end = min(pos + tree_size, len(blob))

        self.version = version
        self.blob = blob
        self.entries = {}
        while pos < end:
            extension, pos = _cstring(blob, pos)
            if not extension:
                break
            while True:
                folder, pos = _cstring(blob, pos)
                if not folder:
                    break
                folder = "" if folder.strip() in ("", " ") else folder
                while True:
                    name, pos = _cstring(blob, pos)
                    if not name:
                        break
                    _crc, preload, archive, offset, length, _term = struct.unpack_from(
                        "<IHHIIH", blob, pos)
                    pos += 18
                    stored = f"{folder}/{name}.{extension}".lstrip("/")
                    self.entries[stored.lower()] = (
                        stored, preload, archive, offset, length,
                        blob[pos:pos + preload])
                    pos += preload

    def find(self, wanted):
        entry = self.entries.get(wanted.replace("\\", "/").lstrip("/").lower())
        return entry[0] if entry else None

    def read(self, wanted):
        entry = self.entries.get(wanted.replace("\\", "/").lstrip("/").lower())
        if entry is None:
            raise KeyError(wanted)
        _stored, _preload, archive, offset, length, prefix = entry
        if archive == 0x7FFF:
            return prefix + self.blob[offset:offset + length]
        part = self.path.parent / self.path.name.replace(
            "_dir.vpk", f"_{archive:03d}.vpk")
        if not part.is_file():
            raise FileNotFoundError(f"{part.name} is missing")
        with part.open("rb") as handle:
            handle.seek(offset)
            return prefix + handle.read(length)

    def items(self):
        for key in sorted(self.entries):
            stored, _preload, _archive, _offset, length, prefix = self.entries[key]
            yield stored, length + len(prefix)


class Content:
    """Loose files and packs under one root, searched the way the engine asks."""

    def __init__(self, root):
        self.root = Path(root).expanduser()
        self.packs = []
        self.loose = {}          # logical path, lowercased -> (real path, file)
        for candidate in sorted(self.root.rglob("*")):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() == ".vpk":
                if candidate.stem.endswith("_dir"):
                    try:
                        self.packs.append(Pack(candidate))
                    except (ValueError, OSError) as error:
                        print(f"warning: {error}", file=sys.stderr)
                continue
            relative = candidate.relative_to(self.root).as_posix()
            logical = self.logical(relative)
            self.loose.setdefault(logical.lower(), (logical, candidate))

    @staticmethod
    def logical(name):
        """The path as the engine sees it: drop any game directory in front."""
        parts = name.split("/")
        lowered = [part.lower() for part in parts]
        if "scripts" in lowered:
            return "/".join(parts[lowered.index("scripts"):])
        return name

    def survey(self):
        """Every surface properties file: (logical path as stored, kind, where, size)."""
        found = []
        for _key, (logical, path) in sorted(self.loose.items()):
            if logical.lower().startswith(PREFIX):
                found.append((logical, "loose", "a loose file", path.stat().st_size))
        for pack in self.packs:
            for name, size in pack.items():
                if name.lower().startswith(PREFIX):
                    found.append((name, "pack", pack.path.name, size))
        return found

    def resolve(self, name):
        """(bytes, kind, where, spelling) or None.

        ``kind`` separates the three places a name can come from, because only one
        of them is case-sensitive: a pack compares with V_stricmp, a loose file is
        whatever the filesystem says it is.
        """
        logical = self.logical(name)
        key = logical.lower()

        stored = self.loose.get(key)
        if stored is not None and stored[0] == logical:
            return stored[1].read_bytes(), "loose", "a loose file", stored[0]
        for _key, (real, path) in sorted(self.loose.items()):
            if real.lower() == key:
                return path.read_bytes(), "loose-case", "a loose file", real
        for pack in self.packs:
            hits = pack.find(logical)
            if hits is not None:
                return pack.read(logical), "pack", pack.path.name, hits
        return None


# ---------------------------------------------------------------------------
# KeyValues, read the way the engine reads it
# ---------------------------------------------------------------------------

def tokens(text):
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in " \t\r\n":
            index += 1
        elif text.startswith("//", index):
            stop = text.find("\n", index)
            index = length if stop < 0 else stop + 1
        elif char in "{}":
            yield (char, None)
            index += 1
        elif char == "[":
            stop = text.find("]", index)
            index = length if stop < 0 else stop + 1
        elif char == '"':
            index += 1
            collected = []
            while index < length and text[index] != '"':
                if text[index] == "\\" and index + 1 < length:
                    collected.append(text[index + 1])
                    index += 2
                    continue
                collected.append(text[index])
                index += 1
            index += 1
            yield ("str", "".join(collected))
        else:
            stop = index
            while stop < length and text[stop] not in ' \t\r\n"{}':
                stop += 1
            yield ("str", text[index:stop])
            index = stop


class Block:
    def __init__(self, name):
        self.name = name
        self.children = []          # [(name, value, [blocks])]


def parse_blocks(stream):
    """(blocks, missing brace) - a top-level name with no { after it is recorded."""
    blocks = []
    missing = False
    while stream:
        kind, value = stream.pop(0)
        if kind == "}":
            return blocks, missing
        if kind == "{":
            blocks.append(parse_child(stream, ""))
            continue
        name = value
        if stream and stream[0][0] == "{":
            stream.pop(0)
            blocks.append(parse_child(stream, name))
        else:
            blocks.append(Block(name))
            missing = True
    return blocks, missing


def parse_child(stream, name):
    block = Block(name)
    while stream:
        kind, value = stream.pop(0)
        if kind == "}":
            return block
        if kind == "{":
            block.children.append(("", "", parse_child(stream, "")))
            continue
        child_name = value
        if stream and stream[0][0] == "{":
            stream.pop(0)
            block.children.append((child_name, "", parse_child(stream, child_name)))
        elif stream and stream[0][0] == "str":
            block.children.append((child_name, stream.pop(0)[1], []))
        else:
            block.children.append((child_name, "", []))
    return block


def parse(text):
    """(root name, blocks, missing brace) - the shape LoadFromBuffer produces."""
    blocks, missing_brace = parse_blocks(list(tokens(text)))
    root_name = blocks[0].name if blocks else ""
    return root_name, blocks, missing_brace


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def show(content, manifest_hits, chosen, data, kind, where, root_name, blocks,
         missing_brace, entries, bogus, siblings, files, available):
    print(f"content root: {content.root}")
    print()
    print(f"manifest:  {MANIFEST}")
    if chosen is None:
        print("           not in any loose directory or *_dir.vpk under this root")
        for name, hit_kind, hit_where, size in manifest_hits:
            print(f"           near miss: {size} bytes as {name} in {hit_where}")
    else:
        described = "exact spelling" if kind in ("loose", "pack") else f"spelled {chosen}"
        print(f"           {where}, {len(data)} bytes, {described}")
        if kind == "loose-case":
            print("           a loose file keeps its own case; inside a pack it would "
                  "not matter")

    if chosen is not None:
        print()
        print(f"as loaded: {len(data)} bytes" if data else "as loaded: NOT readable")
        print(f"           root name '{root_name}' (the engine never compares it)")
        if missing_brace:
            print('           no { after it: the engine reports "missing {" and '
                  "loads no files")
        if siblings:
            print("           the manifest loop never visits later blocks: "
                  + ", ".join(siblings))
        if bogus:
            print("           subkeys not named 'file' (skipped with a warning): "
                  + ", ".join(bogus))

    if entries:
        print()
        print(f"it names {len(entries)} file(s):")
        for name in entries:
            found = files.get(name)
            if found is None:
                print(f"  [absent]  {name}")
                print("            the engine cannot read it; the surfaces it "
                      "describes are missing")
                continue
            surface_kind, surface_where, size, names, missing_bases = found
            status = "ok" if names else "BROKEN"
            print(f"  [{status:^6}]  {name}")
            print(f"            {surface_where}, {size} bytes")
            if names:
                shown = ", ".join(names[:12])
                more = "" if len(names) <= 12 else f" ... (+{len(names) - 12})"
                noun = "surface" if len(names) == 1 else "surfaces"
                print(f"            {len(names)} {noun}: {shown}{more}")
            if missing_bases:
                print("            bases on surfaces that are not defined: "
                      + ", ".join(missing_bases))

    if chosen is None:
        print()
        if available:
            print("surface files that are in the content:")
            for logical, _, file_where, size in available:
                print(f"  {size:>8}  {logical}   [{file_where}]")
            print()
            print("This branch falls back to the first it can read, so the game "
                  "keeps booting:")
            for name in FALLBACKS:
                present = any(item[0] == name for item in available)
                print(f"  {'yes' if present else 'no ':>3}  {name}")
        else:
            print("and no scripts/surfaceproperties* file is under this root at all, "
                  "loose or packed")


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        print("usage: surface-manifest.py <content root | *_dir.vpk>", file=sys.stderr)
        return 2

    target = Path(argv[1]).expanduser()
    if not target.exists():
        print(f"no such file or directory: {target}", file=sys.stderr)
        return 2

    if target.is_file() and target.suffix.lower() == ".vpk":
        # One pack: look inside it, and let missing neighbours resolve as absent.
        root = target.parent
        content = Content(root)
        content.packs = [pack for pack in content.packs if pack.path == target] or \
            [Pack(target)]
        content.loose = {}
    else:
        content = Content(target)

    available = content.survey()
    manifest_hits = [(logical, kind, where, size) for logical, kind, where, size
                     in available if logical == MANIFEST]

    resolved = content.resolve(MANIFEST)
    if resolved is None:
        chosen = None
        data, kind, where, spelling = b"", "", "", ""
        root_name, blocks, missing_brace, entries, bogus, siblings = (
            "", [], False, [], [], [])
    else:
        data, kind, where, spelling = resolved
        chosen = spelling
        root_name, blocks, missing_brace = parse(data.decode("utf-8", "replace"))
        entries, bogus = [], []
        for child in (blocks[0].children if blocks else []):
            if child[0].lower() == "file":
                entries.append(child[1])
            else:
                bogus.append(child[0])
        siblings = [block.name for block in blocks[1:]]

    files = {}
    defined = set()
    for name in entries:
        found = content.resolve(name)
        if found is None:
            continue
        surface_data, surface_kind, surface_where, _spelling = found
        _surface_root, surface_blocks, _missing = parse(
            surface_data.decode("utf-8", "replace"))
        names = [block.name for block in surface_blocks if block.name]
        bases = {}
        for block in surface_blocks:
            if not block.name:
                continue
            for child_name, child_value, _children in block.children:
                if child_name.lower() == "base":
                    bases.setdefault(block.name, child_value)
        files[name] = (surface_kind, surface_where, len(surface_data), names, bases)
        defined.update(name.lower() for name in names)

    for name, (kind_of, where_of, size, names, bases) in list(files.items()):
        missing = sorted({parent for parent in bases.values()
                          if parent.lower() not in defined})
        files[name] = (kind_of, where_of, size, names, missing)

    show(content, manifest_hits, chosen, data, kind, where, root_name, blocks,
         missing_brace, entries, bogus, siblings, files, available)

    print()
    problems = []
    if chosen is None:
        problems.append("the manifest is not in this content")
        code = 2
    else:
        code = 0
        if not data:
            problems.append("the manifest is 0 bytes: the engine cannot read it")
            entries, bogus = [], []
        if data and kind == "loose-case":
            problems.append(f"it is spelled '{chosen}' as a loose file")
        if missing_brace:
            problems.append('no { after the first name: the engine loads nothing')
        for name in bogus:
            problems.append(f"subkey '{name}' is not named 'file'")
        if not entries and not missing_brace:
            problems.append("no 'file' entries")
        for name in entries:
            if name not in files:
                problems.append(f"'{name}' is named by the manifest but is not in "
                                "the content")
            elif not files[name][3]:
                problems.append(f"'{name}' defines no surfaces")
            for parent in files[name][4]:
                problems.append(f"'{name}' bases a surface on '{parent}', which is "
                                "not defined")
        if problems:
            code = 1

    if not problems:
        total = sum(len(item[3]) for item in files.values())
        print("verdict: the manifest is there and the chain behind it resolves "
              f"({total} surface{'' if total == 1 else 's'})")
    elif chosen is None:
        print("verdict: no manifest in this content")
    else:
        print("verdict: the manifest is there but does not work as intended")
        for problem in problems:
            print(f"  - {problem}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
