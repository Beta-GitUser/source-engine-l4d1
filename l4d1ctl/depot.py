"""Fetching Left 4 Dead (appid 500) content from *your own* Steam copy.

Three legal routes are supported. All of them require that you own the game:

``local``
    Copy (or hardlink) the content out of a Steam installation that is already
    on this machine. Fastest, works offline, no credentials involved.

``steamcmd``
    Drive Valve's official ``steamcmd`` binary. Credentials and Steam Guard are
    typed into steamcmd itself; this tool never sees or stores them.

``depotdownloader``
    Drive ``DepotDownloader`` (SteamKit-based) the same way, for people who
    prefer it or need depot-level control.

``archive``
    Unpack a ``.zip``/``.tar`` that the user exported on another machine. This
    is the Android route: phones cannot run steamcmd, so content is fetched on a
    PC and moved over.

No provider implements the Steam CM protocol itself, and nothing here bypasses
ownership checks. Anonymous depot access is intentionally not offered because
appid 500 depots are not anonymously downloadable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

from . import gamemod
from .paths import APPID, GAME_DIR_NAME

# PC binaries are Windows/x86 leftovers. This port rebuilds game code, so they
# are always skipped to save ~200MB of dead weight on a phone.
EXCLUDED_TOP_LEVEL = ("bin",)
EXCLUDED_SUFFIXES = (".dll", ".exe", ".pdb", ".so", ".dylib", ".vdf.bak")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    level: str  # ok | warn | fail
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper():4}] {self.message}"


@dataclass
class VerificationReport:
    gamedir: Path
    checks: List[Check] = field(default_factory=list)
    files: int = 0
    bytes: int = 0

    @property
    def ok(self) -> bool:
        return not any(c.level == "fail" for c in self.checks)

    @property
    def failures(self) -> List[Check]:
        return [c for c in self.checks if c.level == "fail"]

    @property
    def warnings(self) -> List[Check]:
        return [c for c in self.checks if c.level == "warn"]

    def summary(self) -> str:
        mib = self.bytes / (1024 * 1024)
        return f"{self.files} files, {mib:.1f} MiB, {len(self.failures)} failures, {len(self.warnings)} warnings"


def _dir_size(root: Path) -> tuple[int, int]:
    files = 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                total += path.stat().st_size
                files += 1
            except OSError:
                continue
    return files, total


def verify_game_dir(gamedir: Path) -> VerificationReport:
    """Sanity-check a game directory before the engine is allowed to start."""
    gamedir = Path(gamedir)
    report = VerificationReport(gamedir=gamedir)

    if not gamedir.is_dir():
        report.checks.append(Check("fail", f"game directory does not exist: {gamedir}"))
        return report

    if (gamedir / "gameinfo.txt").is_file():
        report.checks.append(Check("ok", "gameinfo.txt present"))
    else:
        report.checks.append(Check("fail", "gameinfo.txt missing - run `l4d1ctl init`"))

    dir_vpks = sorted(p.name for p in gamedir.glob("*_dir.vpk"))
    if dir_vpks:
        report.checks.append(Check("ok", f"{len(dir_vpks)} VPK pack(s): {', '.join(dir_vpks[:4])}"
                                        + (" ..." if len(dir_vpks) > 4 else "")))
    else:
        report.checks.append(Check("warn", "no *_dir.vpk found: content may not have been copied"))

    maps_dir = gamedir / "maps"
    bsps = list(maps_dir.glob("*.bsp")) if maps_dir.is_dir() else []
    if bsps:
        report.checks.append(Check("ok", f"{len(bsps)} map(s) in maps/"))
    else:
        # L4D1 ships most maps inside the VPK; a missing maps/ folder is normal
        # for a pristine depot copy and only matters for mods.
        report.checks.append(Check("warn", "no loose .bsp maps in maps/ (normal for a VPK-only install)"))

    for required in ("scripts", "resource", "materials"):
        if (gamedir / required).is_dir():
            report.checks.append(Check("ok", f"{required}/ present"))
        else:
            report.checks.append(Check("warn", f"{required}/ missing (may live inside the VPK)"))

    # bin/ is two different things at once. A depot copy carries Valve's PC
    # binaries there (dead weight on a phone), while this port *installs* its
    # rebuilt module in the same folder. Deleting bin/ wholesale, as earlier
    # versions of this message suggested, removes the game module and every
    # launch fails with "Unable to load client DLL".
    bin_dir = gamedir / "bin"
    if bin_dir.is_dir():
        modules = gamemod.scan_modules(bin_dir)
        if modules:
            names = ", ".join(sorted(module.path.name for module in modules.values()))
            report.checks.append(Check("ok", f"game module in bin/: {names}"))
            missing = [name for name in gamemod.REQUIRED_MODULES if name not in modules]
            if missing:
                report.checks.append(Check("fail", f"bin/ is missing the {', '.join(missing)} module: "
                                                   "the engine cannot load the game"))
        pc = sorted(p.name for p in bin_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in (".dll", ".exe", ".pdb"))
        if pc:
            report.checks.append(Check("warn", f"PC binaries in bin/ ({', '.join(pc[:4])}"
                                              + (" ..." if len(pc) > 4 else "")
                                              + "): unused by this port, safe to delete individually"))

    report.files, report.bytes = _dir_size(gamedir)
    report.checks.append(Check("ok" if report.files else "fail", f"{report.files} files, {report.bytes / (1024 * 1024):.1f} MiB"))
    return report


# ---------------------------------------------------------------------------
# Local install -> game dir
# ---------------------------------------------------------------------------

@dataclass
class CopyStats:
    copied: int = 0
    linked: int = 0
    skipped: int = 0
    bytes: int = 0
    excluded: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.copied} copied, {self.linked} linked, {self.skipped} skipped, "
                f"{self.bytes / (1024 * 1024):.1f} MiB")


def _is_excluded(relative: Path) -> bool:
    if relative.parts and relative.parts[0].lower() in EXCLUDED_TOP_LEVEL:
        return True
    return relative.suffix.lower() in EXCLUDED_SUFFIXES


def copy_game_content(source: Path, destination: Path, *, link: bool = False,
                      overwrite: bool = False, dry_run: bool = False,
                      progress: Optional[Callable[[str], None]] = None) -> CopyStats:
    """Copy a Steam install's content folder into ``destination``.

    ``source`` is the ``Left 4 Dead/left4dead`` folder (or any folder with the
    same layout). With ``link=True`` hardlinks are attempted first, which makes
    a second copy essentially free on the same filesystem.
    """
    source = Path(source)
    destination = Path(destination)
    if not source.is_dir():
        raise FileNotFoundError(f"source content folder not found: {source}")

    stats = CopyStats()
    for root, dirnames, filenames in os.walk(source):
        root_path = Path(root)
        relative_root = root_path.relative_to(source)

        for filename in filenames:
            relative = relative_root / filename
            if _is_excluded(relative):
                stats.skipped += 1
                continue
            src = root_path / filename
            dst = destination / relative
            try:
                size = src.stat().st_size
            except OSError:
                stats.skipped += 1
                continue

            if dst.exists() and not overwrite:
                if dst.stat().st_size == size:
                    stats.skipped += 1
                    continue

            stats.bytes += size
            if dry_run:
                stats.copied += 1
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and overwrite:
                dst.unlink()
            done = False
            if link and not dst.exists():
                try:
                    os.link(src, dst)
                    stats.linked += 1
                    done = True
                except OSError:
                    done = False
            if not done:
                shutil.copy2(src, dst)
                stats.copied += 1
            if progress and (stats.copied + stats.linked) % 500 == 0:
                progress(f"... {stats.copied + stats.linked} files")

    return stats


# ---------------------------------------------------------------------------
# steamcmd / DepotDownloader
# ---------------------------------------------------------------------------

@dataclass
class ProviderCommand:
    provider: str
    argv: List[str]
    notes: List[str] = field(default_factory=list)

    def shell(self) -> str:
        import shlex

        return " ".join(shlex.quote(part) for part in self.argv)

    def __str__(self) -> str:
        return self.shell()


def steamcmd_command(steamcmd: str, destination: Path, username: str,
                     appid: int = APPID, *, validate: bool = True,
                     extra: Sequence[str] = ()) -> ProviderCommand:
    argv = [steamcmd, "+force_install_dir", str(destination),
            "+login", username, "+app_update", str(appid)]
    if validate:
        argv.append("validate")
    argv.extend(extra)
    argv.append("+quit")
    return ProviderCommand(
        "steamcmd",
        argv,
        [
            "steamcmd will ask for the password and Steam Guard code itself.",
            f"content lands in {destination} (force_install_dir), then copy it out.",
        ],
    )


def depotdownloader_command(tool: str, destination: Path, username: str,
                            appid: int = APPID, *, validate: bool = True,
                            extra: Sequence[str] = ()) -> ProviderCommand:
    argv = [tool, "-app", str(appid), "-dir", str(destination), "-username", username]
    if validate:
        argv.append("-validate")
    argv.extend(extra)
    return ProviderCommand(
        "depotdownloader",
        argv,
        ["DepotDownloader prompts for the password / Steam Guard in the terminal."],
    )


def run_provider(command: ProviderCommand, *, execute: bool = False,
                 stdin=None, stdout=None) -> int:
    """Run a provider command (or just report it). Returns the exit code."""
    if not execute:
        return 0
    return subprocess.call(command.argv, stdin=stdin, stdout=stdout)


# ---------------------------------------------------------------------------
# Archives (the phone route)
# ---------------------------------------------------------------------------

_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2")


def _strip_prefix(names: Iterable[str]) -> str:
    """If every member sits under a single folder, strip it while unpacking."""
    common = ""
    for name in names:
        parts = Path(name).parts
        if not parts:
            continue
        if not common:
            common = parts[0]
        elif parts[0] != common:
            return ""
    return common


def _safe_relative(name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"archive member escapes destination: {name!r}")
    return relative


def extract_archive(archive: Path, destination: Path, *, dry_run: bool = False,
                    strip_root: bool = True) -> CopyStats:
    """Extract a user-exported archive into ``destination``."""
    archive = Path(archive)
    destination = Path(destination)
    stats = CopyStats()
    if not archive.is_file():
        raise FileNotFoundError(f"archive not found: {archive}")

    lower = archive.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            prefix = _strip_prefix([m.filename for m in members]) if strip_root else ""
            for member in members:
                relative = _safe_relative(member.filename)
                if prefix and relative.parts and relative.parts[0] == prefix:
                    relative = Path(*relative.parts[1:])
                if not relative.parts or _is_excluded(relative):
                    stats.skipped += 1
                    continue
                stats.bytes += member.file_size
                if dry_run:
                    stats.copied += 1
                    continue
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                stats.copied += 1
        return stats

    if lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2")):
        with tarfile.open(archive) as tf:
            members = [m for m in tf.getmembers() if m.isfile()]
            prefix = _strip_prefix([m.name for m in members]) if strip_root else ""
            for member in members:
                relative = _safe_relative(member.name)
                if prefix and relative.parts and relative.parts[0] == prefix:
                    relative = Path(*relative.parts[1:])
                if not relative.parts or _is_excluded(relative):
                    stats.skipped += 1
                    continue
                stats.bytes += member.size
                if dry_run:
                    stats.copied += 1
                    continue
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(member)
                if src is None:
                    continue
                with src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                stats.copied += 1
        return stats

    raise ValueError(f"unsupported archive type: {archive.name} (expected one of {', '.join(_ARCHIVE_SUFFIXES)})")


def make_export_archive(source: Path, archive: Path, *, dry_run: bool = False) -> Path:
    """Create a transfer archive from a local install (PC side helper).

    The archive stores the content folder as its single top-level entry so that
    :func:`extract_archive` can strip it again on the phone.
    """
    source = Path(source)
    archive = Path(archive)
    if dry_run:
        return archive
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        raise FileNotFoundError(f"source content folder not found: {source}")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(source, arcname=GAME_DIR_NAME)
    return archive


__all__ = [
    "APPID",
    "Check",
    "CopyStats",
    "ProviderCommand",
    "VerificationReport",
    "copy_game_content",
    "depotdownloader_command",
    "extract_archive",
    "make_export_archive",
    "run_provider",
    "steamcmd_command",
    "verify_game_dir",
]
