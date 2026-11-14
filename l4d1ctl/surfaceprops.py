"""The surface property manifest: does it exist, and does the chain behind it work?

`PhysParseSurfaceData()` (``game/shared/physics_shared.cpp``) asks the file system
for ``scripts/surfaceproperties_manifest.txt`` through the ``GAME`` search path and
walks the subkeys of whatever loaded, treating every one named ``file`` as another
file to parse. Two facts about that reader decide what every log line means, and
the tests here pin both:

* **Parsing never fails.** ``KeyValues::LoadFromBuffer`` reports syntax problems
  (``missing {``, ``got EOF instead of keyname``) and returns true regardless. So
  ``Unable to load manifest file`` cannot mean "the file is malformed" - it means
  the file could not be opened, or opened with a length of zero
  (``KeyValues::LoadFromFile`` fails in exactly those two places).
* **The root name is not an identity.** The first token in the file becomes the
  name of the root KeyValues and is never compared with anything; the block's
  contents are what the loop sees. A manifest that opens and contributes nothing
  is a *shape* problem, reported as ``missing {`` or ``bogus file type``, never as
  a load failure.

So this module answers three questions separately:

1. is the file anywhere in the content - loose, or inside any ``*_dir.vpk`` - and
   under exactly the spelling the engine asks for;
2. what would the engine get out of it: which files it names, and would any of
   them be skipped;
3. does each named file parse, how many surfaces does it define, and does every
   ``base`` reference land on a surface that exists.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import contentaudit, vpk

MANIFEST = "scripts/surfaceproperties_manifest.txt"

# What builds before the manifest hard-coded, in the order this branch tries them.
FALLBACKS = (
    "scripts/surfaceproperties_left4dead.txt",
    "scripts/surfaceproperties.txt",
    "scripts/surfaceproperties_ep2.txt",
)


# ---------------------------------------------------------------------------
# KeyValues, read the way the engine reads it
# ---------------------------------------------------------------------------

@dataclass
class Node:
    name: str
    value: str = ""
    children: List["Node"] = field(default_factory=list)


@dataclass
class Document:
    """The top-level blocks of a KeyValues file, in file order.

    ``LoadFromBuffer`` puts the *first* block on the object it was given - its name
    becomes that object's name and its keys become its children - and every later
    block becomes a sibling (``SetNextKey``). Both readers in this area depend on
    that shape, in different ways:

    * the manifest loop asks for ``GetFirstSubKey()``, so it sees ``blocks[0]``'s
      children and nothing else - later blocks are parsed and then never visited;
    * ``ParseSurfaceData`` walks the sibling chain, so a surface file's *blocks*
      are the surfaces, the first one included.

    ``missing_brace`` records a top-level name that is not followed by ``{``: the
    engine reports ``missing {`` there and that block contributes nothing.
    """

    root_name: str
    blocks: List[Node] = field(default_factory=list)
    missing_brace: bool = False

    @property
    def children(self) -> List[Node]:
        """What ``GetFirstSubKey()`` returns: the first block's keys."""
        return self.blocks[0].children if self.blocks else []

    @property
    def sibling_names(self) -> List[str]:
        return [block.name for block in self.blocks[1:]]

    @property
    def named_blocks(self) -> List[Node]:
        return [block for block in self.blocks if block.name]


def _tokens(text: str):
    """Yield ('str', value), ('{', None) or ('}', None), dropping comments.

    Source KeyValues: ``//`` to end of line, double-quoted strings with ``\\``
    escapes, bare tokens, ``[conditionals]`` (skipped), and braces.
    """
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
                    escaped = text[index + 1]
                    collected.append({"n": "\n", "t": "\t"}.get(escaped, escaped))
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


def _parse_block(tokens: Sequence[tuple], index: int) -> tuple:
    """Parse up to the matching ``}``; return (nodes, index after it)."""
    nodes: List[Node] = []
    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "}":
            return nodes, index + 1
        if kind == "{":
            index += 1            # a brace with no name in front of it
            children, index = _parse_block(tokens, index)
            nodes.append(Node("", "", children))
            continue

        name = value
        index += 1
        if index < len(tokens) and tokens[index][0] == "{":
            children, index = _parse_block(tokens, index + 1)
            nodes.append(Node(name, "", children))
        elif index < len(tokens) and tokens[index][0] == "str":
            nodes.append(Node(name, tokens[index][1]))
            index += 1
        else:
            nodes.append(Node(name))
    return nodes, index


def parse_document(text: str) -> Document:
    """Parse a KeyValues file into the blocks the engine's readers would see."""
    tokens = list(_tokens(text))
    blocks: List[Node] = []
    missing_brace = False
    index = 0

    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "}":
            index += 1                      # stray close brace
            continue
        if kind == "{":
            children, index = _parse_block(tokens, index + 1)
            blocks.append(Node("", "", children))
            continue

        name = value
        index += 1
        if index < len(tokens) and tokens[index][0] == "{":
            children, index = _parse_block(tokens, index + 1)
        else:
            children = []
            missing_brace = True
        blocks.append(Node(name, "", children))

    root_name = blocks[0].name if blocks else ""
    return Document(root_name=root_name, blocks=blocks, missing_brace=missing_brace)


# ---------------------------------------------------------------------------
# Where the files are
# ---------------------------------------------------------------------------

def locate(root: Path, wanted: str) -> List[contentaudit.Hit]:
    return contentaudit.find(root, wanted)


def usable(hit: contentaudit.Hit) -> bool:
    """Would the engine find this under the name it asks for?

    Pack lookups compare with ``V_stricmp`` (``filesystem/packfile.cpp``), so a
    spelling difference inside a ``*_dir.vpk`` does not matter. A loose file is
    whatever the filesystem says it is, so only an exact spelling resolves - the
    case-folded second look this branch added to the manifest loader aside.
    """
    return hit.exact or hit.in_pack


def list_surface_files(root: Path) -> List[contentaudit.Hit]:
    """Every ``scripts/surfaceproperties*`` file in the content, loose or packed."""
    root = Path(root).expanduser()
    found: List[contentaudit.Hit] = []
    if not root.is_dir():
        return found

    def under_scripts(path: str) -> bool:
        # "scripts/surfaceproperties.txt" when the root *is* the game directory,
        # "left4dead/scripts/surfaceproperties.txt" when it is the folder that
        # holds it. Both are the same file to the engine, which searches the game
        # directory, so both count here.
        parts = path.lower().split("/")
        return (len(parts) >= 2 and parts[-2] == "scripts"
                and parts[-1].startswith("surfaceproperties"))

    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate.suffix.lower() == ".vpk":
            continue
        relative = candidate.relative_to(root).as_posix()
        if under_scripts(relative):
            found.append(contentaudit.Hit(path=relative, source="loose",
                                          exact=True,
                                          size=candidate.stat().st_size))
    for directory_vpk in sorted(root.rglob("*_dir.vpk")):
        try:
            archive = vpk.VPK(directory_vpk)
        except vpk.VPKError:
            continue
        for entry in archive:
            if under_scripts(entry.path):
                found.append(contentaudit.Hit(path=entry.path,
                                              source=directory_vpk.name,
                                              exact=True, size=entry.size,
                                              container=directory_vpk))
    return found


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

@dataclass
class SurfaceFile:
    name: str
    hit: Optional[contentaudit.Hit] = None
    surfaces: List[str] = field(default_factory=list)
    bases: Dict[str, str] = field(default_factory=dict)
    missing_bases: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def readable(self) -> bool:
        return self.hit is not None and not self.error


@dataclass
class PackOverview:
    """What one ``*_dir.vpk`` actually parsed into, so a nil result is explainable."""

    path: str
    version: Optional[int] = None
    entries: int = 0
    under_scripts: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "version": self.version, "entries": self.entries,
                "under_scripts": self.under_scripts, "error": self.error}


@dataclass
class Report:
    root: Path
    hits: List[contentaudit.Hit] = field(default_factory=list)
    chosen: Optional[contentaudit.Hit] = None
    data: bytes = b""
    document: Optional[Document] = None
    entries: List[str] = field(default_factory=list)
    bogus: List[str] = field(default_factory=list)
    files: List[SurfaceFile] = field(default_factory=list)
    fallbacks_present: List[contentaudit.Hit] = field(default_factory=list)
    packs: List[PackOverview] = field(default_factory=list)
    loose_files: int = 0
    mentions: List[tuple] = field(default_factory=list)
    available: List[contentaudit.Hit] = field(default_factory=list)
    error: str = ""

    # -- verdicts ---------------------------------------------------------
    @property
    def found(self) -> bool:
        return self.chosen is not None

    @property
    def loadable(self) -> bool:
        """Can the engine open and read it? The only two failure modes there are."""
        return self.found and not self.error and len(self.data) > 0

    @property
    def contributes(self) -> bool:
        """Would the manifest loop actually load any file?"""
        return bool(self.entries)

    @property
    def problems(self) -> List[str]:
        problems: List[str] = []
        if not self.found:
            problems.append("the manifest is not in the content")
            return problems
        if self.error:
            problems.append(f"the manifest could not be read: {self.error}")
            return problems
        if self.data == b"":
            problems.append("the manifest is 0 bytes: the engine cannot read it")
            return problems
        if not usable(self.chosen):
            problems.append(f"it is spelled '{self.chosen.path}': a loose file only "
                            "the case-folded second look this branch added can find")
        if outside := _outside_search_path(self.chosen):
            problems.append(f"it sits in '{outside}/', which is not a folder the engine "
                            "searches, so it is invisible to the load")
        if self.document is not None and self.document.missing_brace:
            problems.append('the file does not start with NAME { ... }: the engine '
                            'reports "missing {" and loads nothing')
        for name in self.bogus:
            problems.append(f"subkey '{name}' is not named 'file': the engine warns "
                            "'bogus file type' and skips it")
        if not self.entries and not (self.document and self.document.missing_brace):
            problems.append("no 'file' entries: the manifest names no surface data")
        for item in self.files:
            if item.hit is None:
                problems.append(f"'{item.name}' is named by the manifest but is not in "
                                "the content")
            elif item.error:
                problems.append(f"'{item.name}' could not be read: {item.error}")
            elif not item.surfaces:
                problems.append(f"'{item.name}' defines no surfaces")
            for parent in item.missing_bases:
                problems.append(f"'{item.name}' bases a surface on '{parent}', which "
                                "is not defined")
        return problems

    @property
    def ok(self) -> bool:
        return not self.problems

    def exit_code(self) -> int:
        if not self.found:
            return 2
        return 0 if self.ok else 1

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "found": self.found,
            "loadable": self.loadable,
            "ok": self.ok,
            "manifest": ({"path": self.chosen.path, "source": self.chosen.source,
                          "size": self.chosen.size, "exact": self.chosen.exact,
                          "in_pack": self.chosen.in_pack} if self.chosen else None),
            "root_name": self.document.root_name if self.document else "",
            "missing_brace": bool(self.document and self.document.missing_brace),
            "sibling_blocks": self.document.sibling_names if self.document else [],
            "entries": self.entries,
            "bogus_subkeys": self.bogus,
            "files": [{"name": item.name,
                       "found": item.hit is not None,
                       "source": item.hit.source if item.hit else None,
                       "size": item.hit.size if item.hit else 0,
                       "surfaces": item.surfaces,
                       "missing_bases": item.missing_bases,
                       "error": item.error} for item in self.files],
            "packs": [pack.to_dict() for pack in self.packs],
            "raw_mentions": [{"pack": name, "count": count, "samples": samples}
                             for name, count, samples in self.mentions],
            "loose_files": self.loose_files,
            "fallbacks_present": [hit.path for hit in self.fallbacks_present],
            "available": [{"path": hit.path, "source": hit.source, "size": hit.size}
                          for hit in self.available],
            "problems": self.problems,
        }


def _overview(root: Path) -> tuple:
    """Every pack under the root and how much of it parsed, plus the loose count."""
    packs: List[PackOverview] = []
    loose = 0
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() == ".vpk":
            if not candidate.stem.endswith("_dir"):
                continue
            overview = PackOverview(path=candidate.relative_to(root).as_posix())
            try:
                archive = vpk.VPK(candidate)
            except vpk.VPKError as error:
                overview.error = str(error)
            else:
                overview.version = archive.info.version
                overview.entries = len(archive)
                overview.under_scripts = sum(
                    1 for entry in archive if entry.path.lower().startswith("scripts/"))
            packs.append(overview)
        else:
            loose += 1
    return packs, loose


def raw_mentions(root: Path, packs: Sequence[PackOverview],
                 needle: bytes = b"surfaceproperties", limit: int = 3) -> List[tuple]:
    """Occurrences of a name in a dir file's raw bytes, index or no index.

    In a VPK the names *are* in the index, so if the reader lists none of them the
    question is whether it misread the tree or the pack has none. The bytes answer
    that without trusting the reader.
    """
    found: List[tuple] = []
    for pack in packs:
        if pack.error:
            continue
        try:
            blob = (root / pack.path).read_bytes()
        except OSError:
            continue
        samples: List[str] = []
        start = 0
        while len(samples) < limit:
            hit = blob.find(needle, start)
            if hit < 0:
                break
            left = hit
            while left > 0 and 32 <= blob[left - 1] < 127:
                left -= 1
            right = hit + len(needle)
            while right < len(blob) and 32 <= blob[right] < 127:
                right += 1
            samples.append(blob[left:right].decode("ascii", "replace"))
            start = hit + len(needle)
        found.append((pack.path, blob.count(needle), samples))
    return found


def audit(root: Path, *, max_surfaces: int = 0) -> Report:
    root = Path(root).expanduser()
    report = Report(root=root)
    report.packs, report.loose_files = _overview(root)
    report.available = list_surface_files(root)
    if not report.available:
        report.mentions = raw_mentions(root, report.packs)
    report.hits = locate(root, MANIFEST)

    # The engine asks for one exact name, so prefer a hit it would resolve.
    ordered = [hit for hit in report.hits if usable(hit)] + list(report.hits)
    if ordered:
        report.chosen = ordered[0]

    # A hit may carry its search-path folder ("left4dead/scripts/…"); the engine
    # asks for the tail, so the tail is what is matched.
    fallbacks = {name.lower() for name in FALLBACKS}
    report.fallbacks_present = [
        hit for hit in report.available
        if hit.path.lower() in fallbacks
        or any(hit.path.lower().endswith("/" + name) for name in fallbacks)]

    if report.chosen is not None:
        try:
            report.data = contentaudit.read_hit(root, report.chosen)
        except (OSError, vpk.VPKError) as error:
            report.error = str(error)
        else:
            report.document = parse_document(
                report.data.decode("utf-8", "replace"))
            for node in report.document.children:
                if node.name.lower() == "file":
                    report.entries.append(node.value)
                else:
                    report.bogus.append(node.name)

    for name in report.entries:
        item = SurfaceFile(name=name)
        hits = locate(root, name)
        ordered = [hit for hit in hits if usable(hit)] + list(hits)
        item.hit = ordered[0] if ordered else None
        if item.hit is not None:
            try:
                text = contentaudit.read_hit(root, item.hit).decode("utf-8", "replace")
            except (OSError, vpk.VPKError) as error:
                item.error = str(error)
            else:
                document = parse_document(text)
                item.surfaces = [block.name for block in document.named_blocks]
                for block in document.named_blocks:
                    for child in block.children:
                        if child.name.lower() == "base":
                            item.bases[block.name] = child.value
        report.files.append(item)

    # Resolve base references against every surface the whole chain defines.
    defined = {surface.lower() for item in report.files for surface in item.surfaces}
    for item in report.files:
        item.missing_bases = sorted({parent for parent in item.bases.values()
                                     if parent.lower() not in defined})

    if max_surfaces:
        for item in report.files:
            del item.surfaces[max_surfaces:]

    return report


# ---------------------------------------------------------------------------
# Making the engine able to see it
# ---------------------------------------------------------------------------

def _inside_scripts(name: str) -> str:
    """The part of a path below its scripts/ folder, so the copy keeps the layout."""
    parts = name.replace("\\", "/").split("/")
    lowered = [part.lower() for part in parts]
    if "scripts" in lowered:
        return "/".join(parts[lowered.index("scripts") + 1:])
    return parts[-1]


def stage(report: Report, game_dir: Path, *, force: bool = False) -> List[tuple]:
    """Copy the manifest and the files it names into ``<game_dir>/scripts/``.

    The engine reaches a loose file only through its search paths, and the game
    directory is the first of them. When the only copy of a surface property set
    lives in a sibling folder - another game's content, an unpacked archive - this
    puts it where the load will actually find it.
    """
    if report.chosen is None:
        raise ValueError("nothing to stage: no manifest was found")

    game_dir = Path(game_dir).expanduser()
    destination_root = game_dir / "scripts"
    actions: List[tuple] = []

    for hit in [report.chosen] + [item.hit for item in report.files if item.hit]:
        relative = _inside_scripts(hit.located if not hit.in_pack else hit.path)
        destination = destination_root / relative
        source = None if hit.in_pack else report.root / hit.located
        already_there = source is not None and destination.exists() \
            and source.resolve() == destination.resolve()
        if already_there or (destination.exists() and not force):
            actions.append((hit.located, destination, "kept"))
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(contentaudit.read_hit(report.root, hit))
        actions.append((hit.located, destination, "copied"))
    return actions


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _where(hit: contentaudit.Hit, root: Path) -> str:
    if not hit.in_pack:
        return "a loose file"   # callers that want the location use hit.located
    try:
        return f"in {hit.container.relative_to(root).as_posix()}"
    except (ValueError, AttributeError):
        return f"in {hit.source}"


SEARCHED_FOLDERS = ("scripts", "left4dead", "left4dead_dlc3", "hl2", "platform")


def _scripts_in(folder: Path):
    """(loose names, [(pack, names)]) for everything under a folder's scripts/."""
    loose: List[str] = []
    scripts = folder / "scripts"
    if scripts.is_dir():
        loose = sorted(entry.name for entry in scripts.rglob("*") if entry.is_file())

    packs = []
    for directory_vpk in sorted(folder.glob("*_dir.vpk")):
        try:
            archive = vpk.VPK(directory_vpk)
        except vpk.VPKError:
            continue
        names = sorted(entry.path for entry in archive
                       if entry.path.lower().startswith("scripts/"))
        if names:
            packs.append((directory_vpk.name, names))
    return loose, packs


def _surface_names(names: Sequence[str]) -> List[str]:
    return [name for name in names if name.lower().split("/")[-1].startswith("surfaceproperties")]


def search_paths(root: Path) -> List[tuple]:
    """What each folder the engine searches actually holds under scripts/.

    ``gameinfo.txt`` mounts ``left4dead_dlc3``, the game directory itself and
    ``hl2``, and the engine also searches ``platform``, in that order. A surface
    file that is not in one of them is invisible to the engine however present it
    is on disk - which is why "the content has it" and "the engine found it" are
    different questions.
    """
    root = Path(root)
    rows: List[tuple] = []
    for folder in ("left4dead_dlc3", "left4dead", "hl2", "platform"):
        candidate = root / folder
        if not candidate.is_dir() and root.name.lower() == folder:
            candidate = root
        if not candidate.is_dir():
            rows.append((folder + "/", "absent", ""))
            continue

        loose, packs = _scripts_in(candidate)
        loose_surfaces = _surface_names(loose)
        pack_surfaces = [(pack, names) for pack, names in
                         ((pack, _surface_names(names)) for pack, names in packs) if names]

        if loose_surfaces:
            described = (f"scripts/ has {len(loose)} files, incl. "
                         + ", ".join(loose_surfaces[:3]))
            where = ""
        elif pack_surfaces:
            described = "no loose surface file"
            pack, names = pack_surfaces[0]
            where = f"{pack}: {names[0]}"
        elif loose:
            described = (f"scripts/ has {len(loose)} files, none named "
                         "surfaceproperties*")
            where = ""
        elif packs:
            described = "scripts/ lives only in " + ", ".join(pack for pack, _ in packs[:2])
            where = ""
        else:
            described = "no scripts/ at all"
            where = ""
        rows.append((folder + "/", described, where))
    return rows


def _outside_search_path(hit: contentaudit.Hit) -> str:
    """The folder that hides this file from the engine, or "" if it is reachable.

    A loose file is only visible where the engine looks, and that is the game
    directory, the DLC folders next to it, hl2/ and platform/ - never an arbitrary
    folder that happens to be under the same parent.
    """
    if hit.in_pack:
        return ""
    parts = hit.located.split("/")
    if len(parts) < 2:
        return ""
    folder = parts[0]
    return "" if folder.lower() in SEARCHED_FOLDERS else folder


def _search_path_note(hit: contentaudit.Hit) -> str:
    folder = _outside_search_path(hit)
    if not folder:
        return ""
    return (f"note: '{folder}/' is not one of the folders the engine searches "
            "(left4dead/, hl2/, platform/), so it cannot see this file there")


def render(report: Report) -> str:
    lines: List[str] = []
    lines.append(f"content root: {report.root}")
    lines.append("")
    for pack in report.packs:
        if pack.error:
            lines.append(f"pack:      {pack.path} — unreadable: {pack.error}")
            continue
        note = "" if pack.entries else \
            "   (0 entries: the reader could not read this pack)"
        lines.append(f"pack:      {pack.path} — vpk v{pack.version}, "
                     f"{pack.entries} entries, {pack.under_scripts} under scripts/{note}")
    lines.append(f"loose:     {report.loose_files} files")
    lines.append("")

    if report.found:
        hit = report.chosen
        lines.append(f"manifest:  {MANIFEST}")
        where = _where(hit, report.root)
        if not hit.in_pack:
            where = f"{where} at {hit.located}"
        spelling = "" if hit.exact else f", spelled {hit.path}"
        lines.append(f"           {where}, {hit.size} bytes{spelling}")
        note = _search_path_note(hit)
        if note:
            lines.append(f"           {note}")
        for other in report.hits:
            if other is hit:
                continue
            spelled = "" if other.path == hit.path else f" as {other.path}"
            where = _where(other, report.root)
            if not other.in_pack:
                where = f"{where} at {other.located}"
            lines.append(f"           also: {where}{spelled}, {other.size} bytes")
    else:
        lines.append(f"manifest:  {MANIFEST}")
        lines.append("           not in any loose directory or *_dir.vpk under this root")
        for hit in report.hits:
            lines.append(f"           near miss: {_where(hit, report.root)} has {hit.path}")

    if report.error:
        lines.append(f"           read failed: {report.error}")

    paths = search_paths(report.root)
    if any(described != "absent" for _, described, _ in paths):
        lines.append("")
        lines.append("search paths, in the order gameinfo.txt mounts them:")
        for folder, described, where in paths:
            lines.append(f"  {folder:<17} {described}")
            if where:
                lines.append(f"  {'':<17} in {where}")

    if report.found and report.data:
        document = report.document or Document(root_name="", children=[])
        openable = "openable, non-empty" if report.loadable else "NOT readable"
        lines.append("")
        lines.append(f"as loaded: {openable}, {len(report.data)} bytes")
        lines.append(f"           root name '{document.root_name}' "
                     "(the engine never compares it)")
        if document.missing_brace:
            lines.append('           no { after it: the engine reports "missing {" '
                         "and loads no files")
        if document.sibling_names:
            lines.append("           the manifest loop never visits later blocks: "
                         + ", ".join(document.sibling_names))
        if report.bogus:
            lines.append("           subkeys not named 'file' (skipped with a warning): "
                         + ", ".join(report.bogus))

    if report.entries:
        lines.append("")
        lines.append(f"it names {len(report.entries)} file(s):")
        for item in report.files:
            if item.hit is None:
                lines.append(f"  [absent]  {item.name}")
                lines.append("            the engine cannot read it; the surfaces it "
                             "describes are missing")
                continue
            status = "ok" if item.readable and item.surfaces else "BROKEN"
            lines.append(f"  [{status:^6}]  {item.name}")
            lines.append(f"            {_where(item.hit, report.root)}, {item.hit.size} bytes")
            if item.error:
                lines.append(f"            read failed: {item.error}")
            elif item.surfaces:
                shown = ", ".join(item.surfaces[:12])
                more = "" if len(item.surfaces) <= 12 else \
                    f" ... (+{len(item.surfaces) - 12})"
                noun = "surface" if len(item.surfaces) == 1 else "surfaces"
                lines.append(f"            {len(item.surfaces)} {noun}: {shown}{more}")
            if item.missing_bases:
                lines.append("            bases on surfaces that are not defined: "
                             + ", ".join(item.missing_bases))

    if not report.found:
        lines.append("")
        if report.available:
            lines.append("surface files that are in the content:")
            for hit in report.available:
                lines.append(f"  {hit.size:>8}  {hit.path}   [{_where(hit, report.root)}]")
            lines.append("")
            lines.append("This branch falls back to the first of these it can read, "
                         "so the game keeps booting:")
            for name in FALLBACKS:
                # A hit may carry its search-path folder ("left4dead/scripts/…");
                # the engine asks for the tail, so the tail is what is compared.
                present = any(hit.path.lower() == name.lower()
                              or hit.path.lower().endswith("/" + name.lower())
                              for hit in report.available)
                lines.append(f"  {'yes' if present else 'no ':>3}  {name}")
        else:
            lines.append("and no scripts/surfaceproperties* file is under this root "
                         "at all, loose or packed")
            if report.mentions:
                lines.append("")
                lines.append("Raw byte search, which does not depend on the index:")
                for name, count, samples in report.mentions:
                    if count:
                        lines.append(f"  {name}: {count} mention(s) of "
                                     f"'surfaceproperties', e.g. {', '.join(samples)}")
                        lines.append("    -> the names are in the file but the index "
                                     "lists none: the reader is wrong, not the content")
                    else:
                        lines.append(f"  {name}: not a single mention")
            readable = [pack for pack in report.packs if not pack.error]
            if readable:
                lines.append("")
                lines.append("That is worth a second look rather than a shrug: the "
                             "packs above parsed, and none of them holds one.")
                for pack in readable:
                    if pack.under_scripts:
                        lines.append(f"  {pack.path} has {pack.under_scripts} scripts/ "
                                     "entries, so look at what they are called")
                    else:
                        lines.append(f"  {pack.path} has {pack.entries} entries and "
                                     "not one under scripts/ - suspect the reader, "
                                     "not the content")
    elif report.fallbacks_present and not report.contributes:
        lines.append("")
        lines.append("what this branch would fall back to, having loaded nothing:")
        for hit in report.fallbacks_present:
            lines.append(f"  {hit.size:>8}  {hit.path}   [{_where(hit, report.root)}]")

    lines.append("")
    if report.ok:
        total = sum(len(item.surfaces) for item in report.files)
        lines.append("verdict: the manifest is there and the chain behind it resolves "
                     f"({total} surface{'' if total == 1 else 's'})")
    elif not report.found:
        lines.append("verdict: no manifest in this content")
    else:
        lines.append("verdict: the manifest is there but does not work as intended")
        for problem in report.problems:
            lines.append(f"  - {problem}")
        folder = _outside_search_path(report.chosen)
        if folder:
            lines.append("")
            lines.append(f"The engine looks in the game directory ({folder} is the "
                         "game's sibling, not part of it). Either install the game's "
                         "own scripts/ folder, or copy what this needs into it:")
            lines.append(f"  mkdir -p left4dead/scripts && cp {folder}/scripts/"
                         "surfaceproperties*.txt left4dead/scripts/")
    return "\n".join(lines)
