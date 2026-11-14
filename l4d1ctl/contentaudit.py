"""Does the content actually contain what the engine asks for at boot?

The engine logs a handful of missing-asset lines on every start:

    ##### CTexture::LoadTextureBitsFromFile couldn't find materials/dev/identitylightwarp.vtf
    ##### CTexture::LoadTextureBitsFromFile couldn't find materials/debug/debugluxelsnoalpha.vtf
    ##### CTexture::LoadTextureBitsFromFile couldn't find materials/engine/normalizedrandomdirections2d.vtf
    Can't find background image 'materials/console/background01_widescreen.vtf'

Two different things can cause that, and they need different fixes:

  * the file is in the content, but the engine never looks there (mounting,
    search path, wrong game directory);
  * the file is genuinely not in the content that was downloaded.

Guessing between them wastes an evening. This asks the question directly: it reads
every mountable pack (``*_dir.vpk`` or a self-contained ``pak01.vpk``) plus any loose files, and says where
each expected asset was found - or that it is nowhere.

None of these four are fatal. They are engine fallbacks and a console backdrop; the
engine warns and carries on, exactly as it does in nillerusr's tree
(source-engine issue #292 shows the same four lines). They are worth checking
anyway, because a *missing* file and an *unmounted* file look identical in the log.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import vpk

# (path, what it is, what its absence costs)
BOOT_ASSETS = (
    ("materials/dev/identitylightwarp.vtf",
     "default light warp, used by materials that do not name one",
     "some materials lose their lighting gradient; not fatal"),
    ("materials/debug/debugluxelsnoalpha.vtf",
     "luxel debug texture, used by mat_luxels",
     "debug views only; not fatal"),
    ("materials/engine/normalizedrandomdirections2d.vtf",
     "random lookups for detail sprites and particles",
     "detail/particle variation flattens; not fatal"),
    ("materials/console/background01_widescreen.vtf",
     "console and loading backdrop",
     "black console background; cosmetic"),
)


# The top-level trees the engine asks for by name. If one of these is absent from
# both the loose directories and every pack, the log lines that follow it are
# content, not code - and no amount of engine patching will produce the files.
CONTENT_TREES = (
    ("resource/", "UI layouts and localisation: GameMenu.res, valve_english.txt, *UIPanel.res"),
    ("shaders/", "precompiled shader blobs; without them every material fails to build"),
    ("materials/", "textures and material definitions"),
    ("models/", "model geometry and animations"),
    ("sound/", "sound files and the sound cache"),
    ("scripts/", "surface properties, vehicle/talker scripts, plugin lists"),
    ("maps/", "the maps themselves"),
    ("cfg/", "config files the engine execs at boot"),
)


@dataclass
class TreeFinding:
    """One expected top-level tree, and everywhere it exists under the root."""

    tree: str
    description: str
    loose: List[str] = field(default_factory=list)      # directories, relative
    packs: Dict[str, int] = field(default_factory=dict)  # pack name -> entries

    @property
    def found(self) -> bool:
        return bool(self.loose or self.packs)

    def where(self) -> str:
        parts = []
        if self.loose:
            parts.append("loose: " + ", ".join(self.loose))
        for pack, count in sorted(self.packs.items()):
            parts.append(f"{pack}: {count} entries")
        return "; ".join(parts) if parts else "absent"


def trees(root: Path) -> List[TreeFinding]:
    """Where each expected top-level tree lives: a loose folder, a pack, or nowhere.

    The engine mounts the game directory and, through gameinfo.txt, its siblings -
    so a tree is lost either by not being in the content at all or by being in a
    folder none of those search paths covers.
    """
    root = Path(root).expanduser()
    findings = [TreeFinding(tree=name, description=description)
                for name, description in CONTENT_TREES]
    by_name = {finding.tree: finding for finding in findings}
    if not root.is_dir():
        return findings

    candidates = [root] + [entry for entry in sorted(root.iterdir())
                           if entry.is_dir() and not entry.name.startswith(".")]
    for base in candidates:
        for entry in sorted(base.iterdir()) if base.is_dir() else []:
            if not entry.is_dir():
                continue
            finding = by_name.get(entry.name.lower() + "/")
            if finding is None:
                continue
            relative = entry.relative_to(root).as_posix() + "/"
            if relative not in finding.loose:
                finding.loose.append(relative)

    for directory_vpk in vpk.packs_under(root):
        try:
            archive = vpk.VPK(directory_vpk)
        except vpk.VPKError:
            continue
        counts: Dict[str, int] = {}
        for entry in archive:
            head = entry.path.split("/")[0].lower() + "/"
            if head in by_name:
                counts[head] = counts.get(head, 0) + 1
        for head, count in counts.items():
            by_name[head].packs[directory_vpk.name] = count
    return findings


@dataclass
class AssetFinding:
    path: str
    description: str
    cost: str
    found_in: Optional[str] = None      # "loose" or the vpk file name
    searched: List[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.found_in is not None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "found": self.found,
            "found_in": self.found_in,
            "searched": self.searched,
            "cost_if_missing": self.cost,
        }


def _index(directory: Path) -> Dict[str, str]:
    """path -> where it was found, across loose files and every mountable pack."""
    found: Dict[str, str] = {}

    for candidate in sorted(directory.rglob("*")):
        if not candidate.is_file():
            continue
        if vpk.is_pack(candidate):
            continue
        relative = candidate.relative_to(directory).as_posix().lower()
        found.setdefault(relative, "loose")
        # A source root (…/srceng) holds left4dead/materials/…, while a game
        # directory holds materials/… directly. Index both spellings so the caller
        # does not have to know which one it passed.
        _, separator, stripped = relative.partition("/")
        if separator:
            found.setdefault(stripped, "loose")

    for directory_vpk in vpk.packs_under(directory):
        try:
            archive = vpk.VPK(directory_vpk)
        except vpk.VPKError:
            continue
        for entry in archive:
            found.setdefault(entry.path.lower(), directory_vpk.name)

    return found


def audit(root: Path, assets: Sequence[tuple] = BOOT_ASSETS) -> List[AssetFinding]:
    root = Path(root).expanduser()
    index = _index(root) if root.is_dir() else {}
    searched = sorted({where for where in index.values()})

    findings: List[AssetFinding] = []
    for path, description, cost in assets:
        where = index.get(path.lower())
        findings.append(AssetFinding(path=path, description=description, cost=cost,
                                     found_in=where, searched=searched))
    return findings


@dataclass
class Hit:
    """One place a wanted path exists, with the spelling it has there."""

    path: str
    source: str          # "loose" or the archive file name
    exact: bool          # same case as the caller asked for
    size: int = 0
    container: Optional[Path] = None    # the pack it came from, if any
    located: str = ""        # where it really is, relative to the root

    def __post_init__(self) -> None:
        if not self.located:
            self.located = self.path

    @property
    def in_pack(self) -> bool:
        return self.container is not None

    def to_dict(self) -> dict:
        return {"path": self.path, "source": self.source, "exact": self.exact,
                "size": self.size, "in_pack": self.in_pack,
                "located": self.located}


def find(root: Path, wanted: str) -> List[Hit]:
    """Where does `wanted` exist under `root`, and is it spelled the same way?

    The engine's lookups are case-sensitive once the content sits on a
    case-sensitive filesystem and inside a VPK, while Valve's data was authored on
    Windows where case did not matter. So "the file is there" and "the engine will
    find the file" are different questions, and this answers both.
    """
    root = Path(root).expanduser()
    normalized = wanted.replace("\\", "/").lstrip("/")
    hits: List[Hit] = []
    if not root.is_dir():
        return hits

    def consider(name: str, source: str, size: int = 0,
                 container: Optional[Path] = None, located: str = "") -> None:
        if name.lower() == normalized.lower():
            hits.append(Hit(path=name, source=source, exact=name == normalized,
                            size=size, container=container, located=located))

    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        if vpk.is_pack(candidate):
            continue
        relative = candidate.relative_to(root).as_posix()
        size = candidate.stat().st_size
        consider(relative, "loose", size, located=relative)
        _, separator, stripped = relative.partition("/")
        if separator:
            # The engine sees paths relative to a game directory, so scripts/x.txt
            # inside left4dead/ is the same file as left4dead/scripts/x.txt here.
            consider(stripped, "loose", size, located=relative)

    for directory_vpk in vpk.packs_under(root):
        try:
            archive = vpk.VPK(directory_vpk)
        except vpk.VPKError:
            continue
        for entry in archive:
            consider(entry.path, directory_vpk.name, entry.size, directory_vpk,
                     located=f"{directory_vpk.name}:{entry.path}")

    return hits


def read_hit(root: Path, hit: "Hit") -> bytes:
    """The bytes behind a Hit, so the file can be looked at, not just located."""
    root = Path(root).expanduser()
    if hit.source == "loose":
        # The recorded path may be relative to the root or to a game directory
        # under it (left4dead/scripts/... versus scripts/...), so try both.
        trimmed = hit.located.split("/", 1)[-1]
        candidates = [root / hit.located, root / trimmed]
        for child in sorted(path for path in root.iterdir() if path.is_dir()):
            candidates.extend([child / hit.path, child / trimmed])
        for candidate in candidates:
            if candidate.is_file():
                return candidate.read_bytes()
        raise FileNotFoundError(hit.path)

    archive = vpk.VPK(hit.container or (root / hit.source))
    return archive.read(hit.path)


def summarise(findings: Sequence[AssetFinding]) -> dict:
    missing = [f.path for f in findings if not f.found]
    return {
        "checked": len(findings),
        "missing": missing,
        "ok": not missing,
        "findings": [f.to_dict() for f in findings],
    }


__all__ = ["BOOT_ASSETS", "AssetFinding", "Hit", "audit", "find", "read_hit", "summarise"]
