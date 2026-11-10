"""Mod manager.

Design constraints that shaped this:

* The engine mounts a gameinfo search path that contains ``*`` by expanding the
  wildcard and mounting every directory and every ``*_dir.vpk`` it finds
  (``public/filesystem_init.cpp``, ``FileSystem_AddLoadedSearchPath``). It also
  drops the ``_nnn.vpk`` parts when the matching ``_dir.vpk`` exists, and sorts
  entries alphabetically.
* Therefore: ``mods/enabled/*`` is a generated staging directory. Load order is
  the alphabetical name of each entry, which we control with an ``NNN_`` prefix
  derived from the mod's ``order``.

So enabling a mod never edits ``gameinfo.txt``; it only (re)builds
``mods/enabled``. Deletes are tracked in a stamp file so we never remove
anything a user put there manually.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import kv, vpk
from .paths import PortPaths

MANIFEST_NAME = "mod.vdf"
MANIFEST_ROOT = "mod"
DEFAULT_ORDER = 100
STAGING_DIR_NAME = "enabled"


class ModError(RuntimeError):
    pass


@dataclass
class Mod:
    mod_id: str
    path: Path
    name: str
    kind: str = "auto"       # auto | folder | vpk
    enabled: bool = False
    order: int = DEFAULT_ORDER
    notes: str = ""
    manifest_path: Optional[Path] = None

    @property
    def resolved_kind(self) -> str:
        if self.kind in ("folder", "vpk"):
            return self.kind
        return "vpk" if self.vpk_dir_file() is not None else "folder"

    def vpk_dir_file(self) -> Optional[Path]:
        candidates = sorted(p for p in self.path.glob("*_dir.vpk") if p.is_file())
        if candidates:
            return candidates[0]
        return None

    def vpk_parts(self) -> List[Path]:
        dir_vpk = self.vpk_dir_file()
        if dir_vpk is None:
            return []
        base = dir_vpk.name[: -len("_dir.vpk")]
        return sorted(p for p in self.path.glob(f"{base}_[0-9][0-9][0-9].vpk") if p.is_file())

    def content_files(self) -> List[Path]:
        """All files a folder mod contributes (manifest excluded)."""
        out: List[Path] = []
        for root, _dirnames, filenames in os.walk(self.path):
            root_path = Path(root)
            for filename in filenames:
                path = root_path / filename
                if path.name.lower() == MANIFEST_NAME:
                    continue
                if path.suffix.lower() == ".vpk":
                    # VPKs inside a folder mod are mounted by the game itself
                    # only if they land at the search path root; skip them to
                    # avoid surprise double mounts.
                    continue
                out.append(path)
        return out


def _read_manifest(path: Path) -> kv.KVNode:
    nodes = kv.load_file(path)
    root = kv.find(nodes, MANIFEST_ROOT)
    if root is None:
        if len(nodes) == 1 and nodes[0].children:
            return nodes[0]
        raise ModError(f"{path}: not a mod manifest")
    return root


def _write_manifest(mod: Mod) -> None:
    manifest = mod.manifest_path or (mod.path / MANIFEST_NAME)
    root = kv.KVNode(MANIFEST_ROOT)
    root.set_value("name", mod.name)
    root.set_value("kind", mod.kind)
    root.set_value("order", mod.order)
    root.set_value("enabled", 1 if mod.enabled else 0)
    if mod.notes:
        root.set_value("notes", mod.notes)
    kv.write_file(manifest, [root], header="l4d1ctl mod manifest (safe to edit)")
    mod.manifest_path = manifest


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    level: str  # warn | fail
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper():4}] {self.message}"


@dataclass
class SyncReport:
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)

    def summary(self) -> str:
        return f"{len(self.added)} staged, {len(self.removed)} removed, {len(self.unchanged)} unchanged"


class ModManager:
    def __init__(self, paths: PortPaths, *, link_mode: str = "hardlink") -> None:
        self.paths = paths
        self.link_mode = link_mode  # hardlink | copy | symlink

    # -- discovery ---------------------------------------------------------
    @property
    def mods_dir(self) -> Path:
        return self.paths.mods_dir

    @property
    def staged_dir(self) -> Path:
        return self.paths.staged_dir

    def scan(self) -> List[Mod]:
        mods: List[Mod] = []
        if not self.mods_dir.is_dir():
            return mods
        for entry in sorted(self.mods_dir.iterdir()):
            if not entry.is_dir() or entry.name == STAGING_DIR_NAME:
                continue
            manifest_path = entry / MANIFEST_NAME
            mod = Mod(mod_id=entry.name, path=entry, name=entry.name)
            if manifest_path.is_file():
                try:
                    root = _read_manifest(manifest_path)
                except (OSError, kv.KVParseError, ModError):
                    mod.notes = "manifest unreadable, using defaults"
                else:
                    mod.manifest_path = manifest_path
                    mod.name = root.get("name", entry.name) or entry.name
                    mod.kind = (root.get("kind", "auto") or "auto").lower()
                    mod.order = root.get_int("order", DEFAULT_ORDER)
                    mod.enabled = root.get_bool("enabled", False)
                    mod.notes = root.get("notes", mod.notes)
            else:
                mod.notes = mod.notes or "no mod.vdf (treated as a folder mod)"
            mods.append(mod)
        return mods

    def get(self, mod_id: str) -> Mod:
        for mod in self.scan():
            if mod.mod_id == mod_id:
                return mod
        raise ModError(f"no such mod: {mod_id}")

    # -- mutation ----------------------------------------------------------
    def add(self, source: Path, *, mod_id: Optional[str] = None,
            name: Optional[str] = None, order: int = DEFAULT_ORDER,
            move: bool = False, enabled: bool = True) -> Mod:
        source = Path(source)
        if not source.exists():
            raise FileNotFoundError(f"mod source not found: {source}")

        mod_id = mod_id or source.stem if source.is_file() else (mod_id or source.name)
        target = self.mods_dir / mod_id
        if target.exists():
            raise ModError(f"mod already exists: {mod_id}")

        self.mods_dir.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            target.mkdir(parents=True)
            extension = source.suffix.lower()
            if extension != ".vpk":
                raise ModError("single-file mods must be .vpk (or pass a folder)")
            shutil.copy2(source, target / source.name)
            base = source.name[: -len("_dir.vpk")] if source.name.endswith("_dir.vpk") else None
            if base:
                for part in source.parent.glob(f"{base}_[0-9][0-9][0-9].vpk"):
                    shutil.copy2(part, target / part.name)
            kind = "vpk"
        else:
            if move:
                shutil.move(str(source), str(target))
            else:
                shutil.copytree(source, target)
            kind = "auto"

        mod = Mod(mod_id=mod_id, path=target, name=name or mod_id, kind=kind,
                  enabled=enabled, order=order)
        if mod.resolved_kind == "vpk":
            dir_vpk = mod.vpk_dir_file()
            if dir_vpk is not None:
                try:
                    parsed = vpk.VPK(dir_vpk)
                except vpk.VPKError as exc:
                    raise ModError(f"{dir_vpk.name} is not a readable VPK: {exc}") from exc
                missing = parsed.info.missing_parts()
                if missing:
                    raise ModError(
                        f"{dir_vpk.name} is missing part file(s): "
                        + ", ".join(p.name for p in missing)
                        + " (copy the whole mod folder, not just the _dir.vpk)"
                    )
        _write_manifest(mod)
        return mod

    def set_enabled(self, mod_id: str, enabled: bool) -> Mod:
        mod = self.get(mod_id)
        mod.enabled = enabled
        _write_manifest(mod)
        return mod

    def set_order(self, mod_id: str, order: int) -> Mod:
        mod = self.get(mod_id)
        mod.order = int(order)
        _write_manifest(mod)
        return mod

    def remove(self, mod_id: str, *, delete_files: bool = False) -> None:
        mod = self.get(mod_id)
        if delete_files:
            shutil.rmtree(mod.path)
        else:
            mod.enabled = False
            _write_manifest(mod)

    # -- validation --------------------------------------------------------
    def validate(self) -> List[Issue]:
        issues: List[Issue] = []
        mods = self.scan()
        by_vpk_base: Dict[str, str] = {}
        by_order: Dict[int, List[str]] = {}

        for mod in mods:
            if mod.manifest_path is None:
                issues.append(Issue("warn", f"{mod.mod_id}: no mod.vdf, defaults assumed"))
            if mod.kind not in ("auto", "folder", "vpk"):
                issues.append(Issue("warn", f"{mod.mod_id}: unknown kind {mod.kind!r}, using auto"))

            kind = mod.resolved_kind
            if kind == "vpk":
                dir_vpk = mod.vpk_dir_file()
                if dir_vpk is None:
                    issues.append(Issue("fail", f"{mod.mod_id}: kind is vpk but no *_dir.vpk found"))
                else:
                    base = dir_vpk.name
                    if base in by_vpk_base:
                        issues.append(Issue("fail", f"{mod.mod_id}: VPK name {base} collides with {by_vpk_base[base]}"))
                    else:
                        by_vpk_base[base] = mod.mod_id
                    try:
                        parsed = vpk.VPK(dir_vpk)
                    except vpk.VPKError as exc:
                        issues.append(Issue("fail", f"{mod.mod_id}: VPK is unreadable: {exc}"))
                    else:
                        for problem in parsed.verify():
                            issues.append(Issue("fail", f"{mod.mod_id}: {dir_vpk.name}: {problem}"))
            else:
                if not mod.content_files():
                    issues.append(Issue("warn", f"{mod.mod_id}: folder mod has no content"))

            by_order.setdefault(mod.order, []).append(mod.mod_id)

        for order, ids in sorted(by_order.items()):
            if len(ids) > 1:
                issues.append(Issue("warn", f"order {order} shared by {', '.join(ids)} (alphabetical within the group)"))

        if self.staged_dir.is_dir():
            for entry in self.staged_dir.iterdir():
                stamp = self._read_stamp()
                if entry.name not in stamp:
                    issues.append(Issue("warn", f"unmanaged entry in mods/enabled: {entry.name} (left untouched)"))
        return issues

    # -- staging -----------------------------------------------------------
    def _read_stamp(self) -> Dict[str, str]:
        try:
            data = json.loads(self.paths.mods_stamp.read_text(encoding="utf-8"))
            entries = data.get("entries", {})
            return entries if isinstance(entries, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_stamp(self, entries: Dict[str, str]) -> None:
        self.paths.mods_stamp.parent.mkdir(parents=True, exist_ok=True)
        self.paths.mods_stamp.write_text(
            json.dumps({"entries": entries}, indent=2) + "\n", encoding="utf-8"
        )

    def _place(self, source: Path, destination: Path) -> str:
        """Link or copy one file. Returns the strategy that was used."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        if self.link_mode == "symlink":
            destination.symlink_to(source.resolve())
            return "symlink"
        if self.link_mode == "hardlink":
            try:
                os.link(source, destination)
                return "hardlink"
            except OSError:
                pass
        shutil.copy2(source, destination)
        return "copy"

    def sync(self, *, dry_run: bool = False) -> SyncReport:
        """Rebuild ``mods/enabled`` from the manifests. Idempotent."""
        report = SyncReport()
        enabled = [m for m in self.scan() if m.enabled]
        enabled.sort(key=lambda m: (m.order, m.mod_id))

        # staged name -> ("file", source path) | ("tree", Mod)
        planned: Dict[str, tuple] = {}
        for mod in enabled:
            prefix = f"{mod.order:03d}_{mod.mod_id}"
            if mod.resolved_kind == "vpk":
                dir_vpk = mod.vpk_dir_file()
                if dir_vpk is None:
                    report.issues.append(Issue("fail", f"{mod.mod_id}: enabled vpk mod has no *_dir.vpk"))
                    continue
                base = dir_vpk.name[: -len("_dir.vpk")]
                planned[f"{prefix}_dir.vpk"] = ("file", dir_vpk)
                for part in mod.vpk_parts():
                    planned[f"{prefix}{part.name[len(base):]}"] = ("file", part)
            else:
                if not mod.content_files():
                    report.issues.append(Issue("warn", f"{mod.mod_id}: folder mod has no content"))
                planned[prefix] = ("tree", mod)

        previous = self._read_stamp()

        # Remove staged entries we created that are no longer wanted.
        for name in sorted(previous):
            if name in planned:
                continue
            path = self.staged_dir / name
            if path.is_dir() and not path.is_symlink():
                if not dry_run:
                    shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                if not dry_run:
                    path.unlink()
            report.removed.append(name)

        if not dry_run and planned:
            self.staged_dir.mkdir(parents=True, exist_ok=True)

        stamps: Dict[str, str] = {}
        for name, (kind, payload) in planned.items():
            if kind == "file":
                signature = _signature(payload)
                destination = self.staged_dir / name
                if previous.get(name) == signature and destination.exists():
                    stamps[name] = signature
                    report.unchanged.append(name)
                    continue
                if not dry_run:
                    self._place(payload, destination)
                stamps[name] = signature
                report.added.append(name)
            else:
                mod: Mod = payload
                files = mod.content_files()
                signature = _tree_signature(files)
                destination = self.staged_dir / name
                if previous.get(name) == signature and destination.is_dir():
                    stamps[name] = signature
                    report.unchanged.append(name)
                    continue
                if not dry_run:
                    if destination.exists():
                        shutil.rmtree(destination)
                    for source in files:
                        relative = source.relative_to(mod.path)
                        self._place(source, destination / relative)
                stamps[name] = signature
                report.added.append(name)

        if not dry_run:
            # Keep stamps for entries we could not plan (e.g. a vpk mod whose
            # files vanished) so the leftover staging can be cleaned up later.
            for name, signature in previous.items():
                if name not in stamps and (self.staged_dir / name).exists():
                    stamps[name] = signature
            self._write_stamp(stamps)
        return report

def _signature(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_size}:{int(stat.st_mtime)}"


def _tree_signature(files: Iterable[Path]) -> str:
    parts = sorted(f"{path.name}:{_signature(path)}" for path in files)
    return f"{len(parts)}:" + "/".join(parts)


__all__ = [
    "Issue",
    "Mod",
    "ModError",
    "ModManager",
    "SyncReport",
]
