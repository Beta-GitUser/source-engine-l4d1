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
every ``*_dir.vpk`` under the game directory plus any loose files, and says where
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
    """path -> where it was found, across loose files and every *_dir.vpk."""
    found: Dict[str, str] = {}

    for candidate in sorted(directory.rglob("*")):
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() == ".vpk" and candidate.stem.endswith("_dir"):
            continue
        relative = candidate.relative_to(directory).as_posix().lower()
        found.setdefault(relative, "loose")
        # A source root (…/srceng) holds left4dead/materials/…, while a game
        # directory holds materials/… directly. Index both spellings so the caller
        # does not have to know which one it passed.
        _, separator, stripped = relative.partition("/")
        if separator:
            found.setdefault(stripped, "loose")

    for directory_vpk in sorted(directory.rglob("*_dir.vpk")):
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


def summarise(findings: Sequence[AssetFinding]) -> dict:
    missing = [f.path for f in findings if not f.found]
    return {
        "checked": len(findings),
        "missing": missing,
        "ok": not missing,
        "findings": [f.to_dict() for f in findings],
    }


__all__ = ["BOOT_ASSETS", "AssetFinding", "audit", "summarise"]
