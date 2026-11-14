#!/usr/bin/env python3
"""List what a ``*_dir.vpk`` really says is inside it.

    python3 vpk-list.py left4dead/pak01_dir.vpk
    python3 vpk-list.py left4dead/pak01_dir.vpk --folders
    python3 vpk-list.py left4dead/pak01_dir.vpk --filter surfaceproperties
    python3 vpk-list.py left4dead/pak01_dir.vpk --strings 60
    python3 vpk-list.py left4dead/pak01_dir.vpk --all --write entries.txt

Standalone: stdlib only, no l4d1ctl needed. Only the ``_dir.vpk`` has an index -
the ``pak01_000.vpk`` files beside it are raw data with no header at all - so this
takes the dir file and says nothing about parts except whether their data is there.

Nothing here is inferred from the game. The three views exist because a name can be
missing for three different reasons and they are worth telling apart:

``--folders``   how the tree groups paths: a folder named ``scripts`` with entries
                under it, or no such folder at all.
``--filter``    names as the tree spells them, with the size each entry claims.
``--strings``   every printable string in the tree region, offsets and all. This
                does not trust the structural parse: if the parse is walking the
                wrong bytes, the names are still visible here as they are stored.
"""

import json
import struct
import sys
from collections import Counter
from pathlib import Path

VPK_SIGNATURE = 0x55AA1234


class PackError(Exception):
    """The file cannot be read as a VPK index; the message says what was seen."""


def _cstring(blob, at):
    stop = blob.index(b"\0", at)
    return blob[at:stop].decode("ascii", "replace"), stop + 1


def parse(path):
    blob = path.read_bytes()
    if len(blob) < 12:
        raise PackError(f"{path.name}: {len(blob)} bytes, too short to be a VPK")
    signature, version = struct.unpack_from("<II", blob, 0)
    if signature != VPK_SIGNATURE:
        raise PackError(f"{path.name}: signature 0x{signature:08x}, not a VPK "
                        f"(0x{VPK_SIGNATURE:08x})")
    if version == 2:
        header = 28
    elif version == 1:
        header = 12
    else:
        raise PackError(f"{path.name}: VPK version {version}, this reader knows 1 and 2")
    (tree_size,) = struct.unpack_from("<I", blob, 8)
    tree_start = header
    tree_end = tree_start + tree_size
    if tree_end > len(blob):
        raise PackError(f"{path.name}: tree claims {tree_size} bytes but only "
                        f"{len(blob) - tree_start} exist - truncated index")

    entries = []
    pos = tree_start
    while pos < tree_end:
        extension, pos = _cstring(blob, pos)
        if extension == "":
            break
        while pos < tree_end:
            folder, pos = _cstring(blob, pos)
            if folder == "":
                break
            while pos < tree_end:
                name, pos = _cstring(blob, pos)
                if name == "":
                    break
                if pos + 18 > tree_end:
                    raise PackError(f"{path.name}: entry for {name} runs past the "
                                    f"tree end at {pos}")
                crc, preload, archive, offset, length, terminator = struct.unpack_from(
                    "<IHHIIH", blob, pos)
                pos += 18
                pos += preload                      # preload bytes live in the tree
                stored = "" if folder.strip() in ("", " ") else folder
                entries.append({
                    "path": f"{stored}/{name}.{extension}" if stored
                            else f"{name}.{extension}",
                    "folder": stored,
                    "name": name,
                    "extension": extension,
                    "crc": crc,
                    "preload": preload,
                    "archive": archive,
                    "offset": offset,
                    "length": length,
                    "terminator_ok": terminator == 0xFFFF,
                })
    return {"path": path, "blob": blob, "version": version, "header": header,
            "tree_size": tree_size, "tree_start": tree_start, "tree_end": tree_end,
            "entries": entries}


def strings_in_tree(pack, limit):
    """Printable NUL-separated strings in the tree region, with their offsets."""
    start, end = pack["tree_start"], pack["tree_end"]
    window = pack["blob"][start:end]
    found = []
    pos = 0
    while pos < len(window) and len(found) < limit:
        stop = window.find(b"\0", pos)
        if stop < 0:
            break
        text = window[pos:stop]
        if len(text) >= 2 and all(32 <= byte < 127 for byte in text):
            found.append((start + pos, text.decode("ascii")))
        pos = stop + 1
    return found


def data_present(pack):
    """How many entries point at bytes that exist: (ok, missing_examples)."""
    root = pack["path"].parent
    part_sizes = {}
    ok = 0
    bad = []
    for entry in pack["entries"]:
        if entry["archive"] == 0x7FFF:
            # data stored in the dir file itself: the offset is absolute in it
            if entry["offset"] + entry["length"] <= len(pack["blob"]):
                ok += 1
            else:
                bad.append((entry["path"], "past the end of the dir file"))
            continue
        part = root / pack["path"].name.replace("_dir.vpk",
                                                f"_{entry['archive']:03d}.vpk")
        if part not in part_sizes:
            part_sizes[part] = part.stat().st_size if part.is_file() else -1
        size = part_sizes[part]
        if size < 0:
            bad.append((entry["path"], f"{part.name} is missing"))
        elif entry["offset"] + entry["length"] > size:
            bad.append((entry["path"], f"past the end of {part.name}"))
        else:
            ok += 1
    return ok, bad


def summarise(pack, strings=0):
    entries = pack["entries"]
    folders = Counter(entry["folder"] or "(root)" for entry in entries)
    extensions = Counter(entry["extension"] for entry in entries)
    present, bad = data_present(pack)

    print(f"file:       {pack['path']}")
    print(f"version:    {pack['version']}   header {pack['header']} bytes")
    print(f"tree:       {pack['tree_size']} bytes at {pack['tree_start']}, "
          f"ends {pack['tree_end']}")
    print(f"entries:    {len(entries)}")
    print(f"folders:    {len(folders)}")
    print(f"extensions: {len(extensions)}")
    print(f"data:       {present}/{len(entries)} entries point at bytes that exist")
    for path, why in bad[:5]:
        print(f"            {path}: {why}")

    if folders:
        print()
        print("folders, most entries first:")
        for folder, count in folders.most_common(20):
            print(f"  {count:>7}  {folder}")
        if len(folders) > 20:
            print(f"  ... {len(folders) - 20} more (use --folders)")

    if extensions:
        print()
        print("extensions: " + ", ".join(f"{ext or '(none)'} {count}"
                                         for ext, count in extensions.most_common(12)))

    if strings:
        print()
        print(f"first {strings} strings in the tree, as stored:")
        for offset, text in strings_in_tree(pack, strings):
            print(f"  {offset:>9}  {text}")


def scan(root, filter_=None):
    """Every *_dir.vpk under a folder, one line each: what it holds, where scripts go."""
    root = Path(root).expanduser()
    packs = sorted(root.rglob("*_dir.vpk"))
    if not packs:
        print(f"no *_dir.vpk under {root}")
        return 2
    print(f"{len(packs)} pack(s) under {root}:")
    print()
    any_scripts = False
    for path in packs:
        shown = path.relative_to(root).as_posix()
        try:
            pack = parse(path)
        except (PackError, OSError) as error:
            print(f"  {shown}: {error}")
            continue
        folders = Counter(entry["folder"] or "(root)" for entry in pack["entries"])
        scripts = sum(count for folder, count in folders.items()
                      if folder == "scripts" or folder.startswith("scripts/"))
        any_scripts = any_scripts or scripts > 0
        note = f"{scripts} under scripts/" if scripts else "no scripts/ folder"
        print(f"  {shown}: vpk v{pack['version']}, {len(pack['entries'])} entries, "
              f"{note}")
        top = ", ".join(folder for folder, _count in folders.most_common(6))
        print(f"    top folders: {top}")
        if filter_ is not None:
            hits = [entry for entry in pack["entries"]
                    if filter_ in entry["path"].lower()]
            for entry in hits[:20]:
                print(f"    {entry['length']:>10}  {entry['path']}")
            if len(hits) > 20:
                print(f"    ... {len(hits) - 20} more in this pack")
    print()
    if not any_scripts:
        print("No pack under this root has a scripts/ folder, so whatever the engine "
              "loads for scripts comes from somewhere else - a loose folder, or a "
              "game directory that is not here.")
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        print("usage: vpk-list.py <*_dir.vpk> [--filter S] [--folders] [--strings N]",
              file=sys.stderr)
        return 2

    target = Path(argv[1]).expanduser()
    if target.is_dir():
        # --filter may come after the folder
        wanted = None
        if "--filter" in argv:
            wanted = argv[argv.index("--filter") + 1].lower() \
                if argv.index("--filter") + 1 < len(argv) else ""
        return scan(target, wanted)
    if argv[1] == "--scan" and len(argv) > 2:
        return scan(Path(argv[2]), None)
    if not target.is_file():
        print(f"no such file: {target}", file=sys.stderr)
        return 2
    if target.suffix.lower() != ".vpk":
        print(f"{target.name} is not a .vpk", file=sys.stderr)
        return 2
    if not target.stem.endswith("_dir"):  # a data part has no index at all
        print(f"{target.name} is one data part of a multi-part pack; its index is "
              f"{target.name.rsplit('_', 1)[0]}_dir.vpk next to it", file=sys.stderr)
        return 2

    filter_ = None
    limit = 40
    show_all = False
    strings = 0
    folders_only = False
    as_json = False
    write_to = None

    index = 2
    while index < len(argv):
        arg = argv[index]
        if arg == "--filter":
            index += 1
            filter_ = argv[index].lower()
        elif arg == "--limit":
            index += 1
            limit = int(argv[index])
        elif arg == "--strings":
            index += 1
            strings = int(argv[index]) if index < len(argv) else 40
        elif arg == "--all":
            show_all = True
        elif arg == "--folders":
            folders_only = True
        elif arg == "--json":
            as_json = True
        elif arg == "--write":
            index += 1
            write_to = Path(argv[index]).expanduser()
        else:
            print(f"unknown option {arg}", file=sys.stderr)
            return 2
        index += 1

    try:
        pack = parse(target)
    except (PackError, OSError) as error:
        print(error, file=sys.stderr)
        return 2
    entries = pack["entries"]

    if write_to is not None:
        lines = [f"{entry['length']:>10}\t{entry['path']}" for entry in entries]
        write_to.write_text("\n".join(lines) + "\n")
        print(f"wrote {len(lines)} entries to {write_to}")

    if as_json:
        print(json.dumps({"path": str(target), "version": pack["version"],
                          "entries": len(entries),
                          "folders": dict(Counter(e["folder"] or "(root)"
                                                  for e in entries)),
                          "extensions": dict(Counter(e["extension"] for e in entries)),
                          "files": [{"path": e["path"], "size": e["length"],
                                     "archive": e["archive"], "offset": e["offset"]}
                                    for e in entries]}, indent=2))
        return 0

    if folders_only:
        counts = Counter(entry["folder"] or "(root)" for entry in entries)
        print(f"{len(counts)} folders in {target.name}:")
        for folder, count in sorted(counts.items()):
            print(f"  {count:>7}  {folder}")
        return 0

    if filter_ is not None:
        hits = [entry for entry in entries if filter_ in entry["path"].lower()]
        print(f"{len(hits)} of {len(entries)} entries match '{filter_}':")
        shown = hits if show_all else hits[:limit]
        for entry in shown:
            print(f"  {entry['length']:>10}  {entry['path']}")
        if len(hits) > len(shown):
            print(f"  ... {len(hits) - len(shown)} more (--all, or --limit N)")
        if not hits:
            print("  (nothing by that name in the index; try --strings to see the "
                  "names as stored)")
        return 0 if hits else 1

    summarise(pack, strings=strings)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
