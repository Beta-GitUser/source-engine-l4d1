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

# The folders the engine's search paths can reach from a game directory.
SEARCHED = ("scripts", "left4dead", "left4dead_dlc3", "hl2", "platform")
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
        # tree_size sits at offset 8 in both header layouts; version 2 only makes
        # the header longer (three sizes and a signature size before the tree), so
        # the tree begins at 28 rather than 12. Reading the size after those fields
        # instead reads the first bytes of the tree and truncates the index.
        (tree_size,) = struct.unpack_from("<I", blob, 8)
        pos = 28 if version == 2 else 12
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

        if not self.entries:
            raise ValueError(f"{self.path.name}: the index parsed to 0 entries, so "
                             "the VPK reader could not read this pack")

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
        self.stats = []          # (path as shown, version, entries, scripts, samples)
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
            self.loose.setdefault(logical.lower(), (logical, relative, candidate))

        for pack in self.packs:
            under_scripts = [name for name in pack.items() if name[0].lower()
                             .startswith("scripts/")]
            self.stats.append((pack.path.relative_to(self.root).as_posix(),
                               pack.version, len(pack.entries), len(under_scripts),
                               [name for name, _size in under_scripts[:200]]))

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
        for _key, (logical, relative, path) in sorted(self.loose.items()):
            if logical.lower().startswith(PREFIX):
                found.append((logical, "loose", f"a loose file at {relative}",
                              path.stat().st_size))
        for pack in self.packs:
            for name, size in pack.items():
                if name.lower().startswith(PREFIX):
                    found.append((name, "pack", pack.path.name, size))
        return found

    def is_loose(self, name):
        logical = self.logical(name).lower()
        stored = self.loose.get(logical)
        return stored is not None and stored[0] == self.logical(name)

    def path_of(self, name):
        stored = self.loose.get(self.logical(name).lower())
        return stored[2] if stored else None

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
            return (stored[2].read_bytes(), "loose", f"a loose file at {stored[1]}",
                    stored[1])
        for _key, (real, relative, path) in sorted(self.loose.items()):
            if real.lower() == key:
                return (path.read_bytes(), "loose-case",
                        f"a loose file at {relative}", relative)
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

def raw_mentions(content, needle=b"surfaceproperties", limit=5):
    """Occurrences of a name in a dir file's raw bytes, index or no index.

    The index is the part of a VPK that a reader can get wrong; the names it stores
    are just bytes in the same file. If the tree says nothing and the bytes say
    plenty, the reader is what needs fixing.
    """
    results = []
    for pack in content.packs:
        blob = pack.blob
        total = blob.count(needle)
        samples = []
        start = 0
        while len(samples) < limit:
            found = blob.find(needle, start)
            if found < 0:
                break
            left = found
            while left > 0 and 32 <= blob[left - 1] < 127:
                left -= 1
            right = found + len(needle)
            while right < len(blob) and 32 <= blob[right] < 127:
                right += 1
            samples.append(blob[left:right].decode("ascii", "replace"))
            start = found + len(needle)
        results.append((pack.path.name, total, samples, len(pack.entries)))
    return results


def show(content, manifest_hits, chosen, data, kind, where, root_name, blocks,
         missing_brace, entries, bogus, siblings, files, available):
    print(f"content root: {content.root}")
    print()
    for shown, version, total, under_scripts, samples in content.stats:
        print(f"pack:      {shown} — vpk v{version}, {total} entries, "
              f"{under_scripts} under scripts/")
    print(f"loose:     {len(content.loose)} files")
    print()
    print(f"manifest:  {MANIFEST}")
    if chosen is None:
        print("           not in any loose directory or *_dir.vpk under this root")
        for name, hit_kind, hit_where, size in manifest_hits:
            print(f"           near miss: {size} bytes as {name} in {hit_where}")
    else:
        described = "exact spelling" if kind in ("loose", "pack") else f"spelled {chosen}"
        print(f"           {where}, {len(data)} bytes, {described}")
        if kind in ("loose", "loose-case") and "/" in chosen:
            folder = chosen.split("/")[0]
            if folder.lower() not in SEARCHED:
                print(f"           note: '{folder}/' is not one of the folders the "
                      "engine searches (left4dead/, hl2/, platform/), so it cannot "
                      "see this file there")
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
            if content.packs:
                print()
                print("Raw byte search, which does not depend on the index:")
                for name, total, samples, entries in raw_mentions(content):
                    if total:
                        print(f"  {name}: {total} mention(s) of "
                              f"'surfaceproperties', e.g. {', '.join(samples[:3])}")
                        print(f"    -> the names are in the file but the index lists "
                              f"{entries} entries: the reader is wrong, not the content")
                    else:
                        print(f"  {name}: not a single mention, and the index lists "
                              f"{entries} entries")
            if content.stats:
                print()
                print("That is worth a second look rather than a shrug: the packs "
                      "above parsed, and none of them holds one.")
                for shown, _version, total, under_scripts, samples in content.stats:
                    if samples:
                        listing = ", ".join(samples[:10])
                        print(f"  {shown} has {under_scripts} scripts/ entries, e.g. "
                              f"{listing}")
                    else:
                        print(f"  {shown} has {total} entries and not one under "
                              "scripts/ — suspect the reader, not the content")


def build_content(target):
    """A Content for a root folder, or for the single pack the caller pointed at."""
    if target.is_file() and target.suffix.lower() == ".vpk":
        root = target.parent
        content = Content(root)
        content.packs = [pack for pack in content.packs
                         if pack.path.resolve() == target.resolve()]
        content.loose = {}
        return content
    return Content(target)


def collect(content):
    """Everything the report needs: where the manifest is, and what it names."""
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

    return {"available": available, "manifest_hits": manifest_hits, "chosen": chosen,
            "data": data, "kind": kind, "where": where, "root_name": root_name,
            "blocks": blocks, "missing_brace": missing_brace, "entries": entries,
            "bogus": bogus, "siblings": siblings, "files": files}


def main(argv):
    stage_dir = None
    force = False
    positional = []
    index = 1
    while index < len(argv):
        if argv[index] == "--stage":
            index += 1
            if index >= len(argv):
                print("--stage needs a game directory", file=sys.stderr)
                return 2
            stage_dir = Path(argv[index]).expanduser()
        elif argv[index] == "--force":
            force = True
        elif argv[index] in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        else:
            positional.append(argv[index])
        index += 1

    if len(positional) != 1:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        print("usage: surface-manifest.py <content root | *_dir.vpk> "
              "[--stage GAMEDIR] [--force]", file=sys.stderr)
        return 2

    target = Path(positional[0]).expanduser()
    if not target.exists():
        print(f"no such file or directory: {target}", file=sys.stderr)
        return 2

    if target.is_file() and target.suffix.lower() == ".vpk":
        if not target.stem.endswith("_dir"):
            print(f"{target.name} is one data part of a multi-part pack; the file that "
                  "names what is in it is "
                  f"{target.name.rsplit('_', 1)[0]}_dir.vpk, in the same folder.",
                  file=sys.stderr)
            print(f"pass that instead, or pass the folder: surface-manifest.py "
                  f"{target.parent}", file=sys.stderr)
            return 2
        # One pack: look inside it, and let anything outside it read as absent.
        root = target.parent
        content = Content(root)
        content.packs = [pack for pack in content.packs
                         if pack.path.resolve() == target.resolve()]
        content.loose = {}
    else:
        content = Content(target)

    state = collect(content)

    if stage_dir is not None:
        if state["chosen"] is None:
            print("nothing to stage: no surface property manifest was found",
                  file=sys.stderr)
            return 2
        sources = [(state["chosen"], state["data"])]
        for entry_name in state["entries"]:
            found = content.resolve(entry_name)
            if found is not None:
                sources.append((found[3], found[0]))
        for source_name, blob in sources:
            parts = source_name.replace("\\", "/").split("/")
            lowered = [part.lower() for part in parts]
            relative = ("/".join(parts[lowered.index("scripts") + 1:])
                        if "scripts" in lowered else parts[-1])
            destination = stage_dir / "scripts" / relative
            same_file = content.is_loose(source_name) and destination.exists() and \
                (content.path_of(source_name).resolve() == destination.resolve()
                 if content.path_of(source_name) else False)
            if same_file or (destination.exists() and not force):
                print(f"kept    {source_name} -> {destination}")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(blob)
            print(f"copied  {source_name} -> {destination}")
        print()
        print(f"The engine reaches it through the game directory now: "
              f"{stage_dir / 'scripts'}")
        print("Re-reading the content with that in place:")
        print()
        content = build_content(target)
        state = collect(content)

    show(content, state["manifest_hits"], state["chosen"], state["data"],
         state["kind"], state["where"], state["root_name"], state["blocks"],
         state["missing_brace"], state["entries"], state["bogus"],
         state["siblings"], state["files"], state["available"])

    print()
    problems = []
    hidden_in = None
    if state["chosen"] is None:
        problems.append("the manifest is not in this content")
        code = 2
    else:
        code = 0
        if not state["data"]:
            problems.append("the manifest is 0 bytes: the engine cannot read it")
            state["entries"], state["bogus"] = [], []
        if state["data"] and state["kind"] == "loose-case":
            problems.append(f"it is spelled '{state['chosen']}' as a loose file")
        if (state["data"] and state["kind"] in ("loose", "loose-case")
                and "/" in state["chosen"]):
            folder = state["chosen"].split("/")[0]
            if folder.lower() not in SEARCHED:
                hidden_in = folder
                problems.append(f"it sits in '{folder}/', which is not a folder the "
                                "engine searches, so it is invisible to the load")
        if state["missing_brace"]:
            problems.append('no { after the first name: the engine loads nothing')
        for name in state["bogus"]:
            problems.append(f"subkey '{name}' is not named 'file'")
        if not state["entries"] and not state["missing_brace"]:
            problems.append("no 'file' entries")
        for name in state["entries"]:
            if name not in state["files"]:
                problems.append(f"'{name}' is named by the manifest but is not in "
                                "the content")
            elif not state["files"][name][3]:
                problems.append(f"'{name}' defines no surfaces")
            for parent in state["files"][name][4]:
                problems.append(f"'{name}' bases a surface on '{parent}', which is "
                                "not defined")
        if problems:
            code = 1

    if not problems:
        total = sum(len(item[3]) for item in state["files"].values())
        print("verdict: the manifest is there and the chain behind it resolves "
              f"({total} surface{'' if total == 1 else 's'})")
    elif state["chosen"] is None:
        print("verdict: no manifest in this content")
    else:
        print("verdict: the manifest is there but does not work as intended")
        for problem in problems:
            print(f"  - {problem}")
        if hidden_in:
            print()
            print(f"The engine looks in the game directory, and '{hidden_in}/' is the "
                  "game's sibling rather than part of it. Either install the game's "
                  "own scripts/ folder, or copy what this needs into it:")
            print(f"  mkdir -p left4dead/scripts && cp {hidden_in}/scripts/"
                  "surfaceproperties*.txt left4dead/scripts/")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
