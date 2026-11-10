"""Game module (client/server DLL) handling.

The engine loads the *game* half of Source from the game directory:

    engine/cdll_engine_int.cpp:  g_pFileSystem->LoadModule( "client", "GAMEBIN", false )
    engine/sv_main.cpp:          the server module is loaded the same way

`GAMEBIN` resolves to ``<gamedir>/bin``, so ``left4dead/bin/client.*`` and
``left4dead/bin/server.*`` are what make ``-game left4dead`` boot. On Android
and Linux the files are shared objects, so both ``client.so`` and the
Android-style ``libclient.so`` are accepted.

This module is deliberately about *files*: installing a build (or another
game's build, for the HL2 bootstrap) into the L4D1 game directory, and telling
you what is there and whether it can possibly load on this machine. Compiling
game code is waf's job (``--build-games left4dead``).
"""

from __future__ import annotations

import os
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .paths import PortPaths

# Modules the engine asks GAMEBIN for. "gameui" is optional here: the engine
# loads it lazily and some builds link it into the client.
MODULE_NAMES = ("client", "server", "gameui")
REQUIRED_MODULES = ("client", "server")

_SO_SUFFIXES = (".so", ".dylib", ".dll", ".so.1")

# ELF e_machine -> name (only the ones this project can plausibly target).
_ELF_MACHINES = {
    3: "x86",
    8: "MIPS",
    20: "PowerPC",
    40: "ARM",
    62: "x86-64",
    183: "AArch64",
    243: "RISC-V",
}

_ELF_TYPES = {1: "relocatable", 2: "executable", 3: "shared object", 4: "core"}


class ModuleError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Binary inspection (no dependencies: we only need the header)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BinaryInfo:
    format: str            # "ELF", "PE", "Mach-O", "unknown"
    bits: int = 0          # 32 / 64 / 0
    machine: str = ""      # "AArch64", "x86-64", ...
    kind: str = ""         # "shared object", ...
    endian: str = ""

    @property
    def summary(self) -> str:
        if self.format == "unknown":
            return "unknown format"
        parts = [self.format]
        if self.bits:
            parts.append(f"{self.bits}-bit")
        if self.machine:
            parts.append(self.machine)
        if self.endian:
            parts.append(f"{self.endian}-endian")
        if self.kind:
            parts.append(self.kind)
        return " ".join(parts)


def inspect_binary(path: Path, *, read_size: int = 4096) -> BinaryInfo:
    """Identify a candidate module by its header only."""
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(read_size)
    except OSError:
        return BinaryInfo("unknown")

    if len(data) >= 20 and data[:4] == b"\x7fELF":
        ei_class, ei_data = data[4], data[5]
        bits = 64 if ei_class == 2 else (32 if ei_class == 1 else 0)
        endian = {1: "little", 2: "big"}.get(ei_data, "")
        prefix = "<" if ei_data == 1 else ">"
        e_type, e_machine = struct.unpack_from(prefix + "HH", data, 16)
        return BinaryInfo(
            "ELF",
            bits,
            _ELF_MACHINES.get(e_machine, f"machine#{e_machine}"),
            _ELF_TYPES.get(e_type, f"type#{e_type}"),
            endian,
        )

    if data[:2] == b"MZ":
        return BinaryInfo("PE", 0, "Windows", "executable")

    macho = {
        b"\xce\xfa\xed\xfe": (32, "big"),
        b"\xcf\xfa\xed\xfe": (64, "little"),
        b"\xfe\xed\xfa\xce": (32, "big"),
        b"\xfe\xed\xfa\xcf": (64, "little"),
    }
    if data[:4] in macho:
        bits, endian = macho[data[:4]]
        return BinaryInfo("Mach-O", bits, "Apple", "dylib", endian)

    return BinaryInfo("unknown")


# ---------------------------------------------------------------------------
# Module discovery
# ---------------------------------------------------------------------------

@dataclass
class ModuleFile:
    name: str              # client / server / gameui
    path: Path
    info: BinaryInfo
    size: int = 0
    link_target: Optional[str] = None

    @property
    def is_symlink(self) -> bool:
        return self.link_target is not None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "format": self.info.format,
            "bits": self.info.bits,
            "machine": self.info.machine,
            "kind": self.info.kind,
            "size": self.size,
            "symlink": self.is_symlink,
        }


def module_name_for(filename: str) -> Optional[str]:
    """Return the module name a filename provides, or None.

    Accepts ``client.so``, ``libclient.so``, ``client.dll``, ``client.dylib``.
    """
    lowered = filename.lower()
    stem = lowered
    for suffix in _SO_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    else:
        return None
    if stem.startswith("lib"):
        stem = stem[3:]
    return stem if stem in MODULE_NAMES else None


def scan_modules(directory: Path) -> Dict[str, ModuleFile]:
    """Map module name -> file inside ``directory`` (first match wins)."""
    directory = Path(directory)
    found: Dict[str, ModuleFile] = {}
    if not directory.is_dir():
        return found

    for entry in sorted(directory.iterdir()):
        if not entry.is_file():
            continue
        name = module_name_for(entry.name)
        if name is None or name in found:
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0
        found[name] = ModuleFile(
            name=name,
            path=entry,
            info=inspect_binary(entry),
            size=size,
            link_target=os.readlink(entry) if entry.is_symlink() else None,
        )
    return found


# ---------------------------------------------------------------------------
# Install / remove / status
# ---------------------------------------------------------------------------

@dataclass
class InstallResult:
    installed: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{len(self.installed)} installed, {len(self.unchanged)} unchanged, "
                f"{len(self.skipped)} skipped")


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _same_content(a: Path, b: Path, *, chunk: int = 1 << 20) -> bool:
    """True when two files have identical bytes (size check first)."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        with a.open("rb") as left, b.open("rb") as right:
            while True:
                block = left.read(chunk)
                if block != right.read(chunk):
                    return False
                if not block:
                    return True
    except OSError:
        return False


def install_modules(source_dir: Path, paths: PortPaths, *, link: bool = False,
                    force: bool = False, install_all: bool = False,
                    dry_run: bool = False) -> InstallResult:
    """Copy a game module build into ``<gamedir>/bin``.

    ``source_dir`` is a build output folder such as ``game/hl2/bin`` (desktop) or
    a packaged module folder (Android). Only files that look like game modules
    are copied unless ``install_all`` is set; a same-named file with different
    content is refused unless ``force`` is used.
    """
    source_dir = Path(source_dir)
    result = InstallResult()
    if not source_dir.is_dir():
        raise ModuleError(f"module source directory not found: {source_dir}")

    target_dir = paths.bindir
    candidates: List[tuple[str, Path]] = []
    for entry in sorted(source_dir.iterdir()):
        if not entry.is_file():
            continue
        name = module_name_for(entry.name)
        if name is None:
            if install_all:
                candidates.append((entry.stem, entry))
            else:
                result.skipped.append(entry.name)
            continue
        candidates.append((name, entry))

    if not candidates:
        raise ModuleError(
            f"no game modules in {source_dir} (looked for "
            + ", ".join(f"{n}.so / lib{n}.so" for n in MODULE_NAMES) + ")"
        )

    present = {name for name, _ in candidates}
    for required in REQUIRED_MODULES:
        if required not in present:
            result.warnings.append(
                f"no {required} module in {source_dir}: the engine will not start without it"
            )

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    for name, source in candidates:
        destination = target_dir / source.name
        try:
            if destination.exists() and (_same_file(source, destination)
                                         or _same_content(source, destination)):
                result.unchanged.append(destination.name)
                continue
            if destination.exists() and not force:
                result.skipped.append(destination.name)
                result.warnings.append(
                    f"{destination.name} already exists in {target_dir}; pass --force to replace"
                )
                continue
            if not dry_run:
                if destination.exists() or destination.is_symlink():
                    destination.unlink()
                if link:
                    try:
                        os.link(source, destination)
                    except OSError:
                        shutil.copy2(source, destination)
                else:
                    shutil.copy2(source, destination)
        except OSError as exc:
            raise ModuleError(f"cannot install {source.name}: {exc}") from exc
        result.installed.append(destination.name)

    return result


def remove_modules(paths: PortPaths, *, dry_run: bool = False,
                   names: Optional[Sequence[str]] = None) -> List[str]:
    target_dir = paths.bindir
    removed: List[str] = []
    if not target_dir.is_dir():
        return removed
    wanted = set(names or MODULE_NAMES)
    for entry in sorted(target_dir.iterdir()):
        name = module_name_for(entry.name)
        if name is None or name not in wanted:
            continue
        if not dry_run:
            entry.unlink()
        removed.append(entry.name)
    return removed


@dataclass
class ModuleStatus:
    bindir: Path
    modules: Dict[str, ModuleFile] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict:
        return {
            "bindir": str(self.bindir),
            "ok": self.ok,
            "modules": {name: mod.to_dict() for name, mod in self.modules.items()},
            "missing": list(self.missing),
            "warnings": list(self.warnings),
        }


def status(paths: PortPaths) -> ModuleStatus:
    """What game module is installed in the game directory?"""
    result = ModuleStatus(bindir=paths.bindir, modules=scan_modules(paths.bindir))
    result.missing = [name for name in REQUIRED_MODULES if name not in result.modules]

    for name, module in result.modules.items():
        if module.info.format == "unknown":
            result.warnings.append(f"{module.path.name}: not a recognisable binary")
        elif module.info.format == "ELF" and module.info.kind not in ("shared object", "shared", "dyn"):
            result.warnings.append(
                f"{module.path.name}: ELF is a {module.info.kind}, the engine needs a shared object"
            )

    if result.missing and not result.warnings:
        result.warnings.append(
            "no game module installed: run `l4d1ctl module install --from <build>/bin` "
            "or build with `./waf configure --build-games left4dead build`"
        )
    return result


def describe_source(source_dir: Path) -> List[str]:
    """Human-readable line per module in a build output folder (for dry runs)."""
    lines: List[str] = []
    for name, module in sorted(scan_modules(Path(source_dir)).items()):
        lines.append(f"{name:<8} {module.path.name:<16} {module.info.summary}")
    return lines


def supported_architectures() -> Iterable[str]:
    return sorted({value for value in _ELF_MACHINES.values()})


__all__ = [
    "BinaryInfo",
    "InstallResult",
    "MODULE_NAMES",
    "ModuleError",
    "ModuleFile",
    "ModuleStatus",
    "REQUIRED_MODULES",
    "describe_source",
    "inspect_binary",
    "install_modules",
    "module_name_for",
    "remove_modules",
    "scan_modules",
    "status",
]
