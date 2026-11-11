"""Inventory mining for shipped Source engine binaries.

The retail ``client.dll`` / ``server.dll`` are the only authority on questions
the SDK cannot answer: which interface versions this build actually exports,
what the network properties are called, which convars and entity classnames
exist. This module reads those *declarations* out of the binary.

What it does not do is recover logic. Decompiled pseudocode is not a source of
truth for a reimplementation - see docs/L4D1/MINING.md for why, and for the line
between interoperability information and copying expression.

Pure standard library on purpose: the intended user runs it on the phone, in
Termux, where ``pip install pefile`` is an extra dependency to keep alive.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------
# PE reading
# --------------------------------------------------------------------------

_PE32_MAGIC = 0x10B
_PE32_PLUS_MAGIC = 0x20B

_MACHINES = {
    0x014C: "i386",
    0x8664: "x86-64",
    0x01C0: "arm",
    0x01C4: "armv7",
    0xAA64: "aarch64",
}

_EXPORT_DIRECTORY_INDEX = 0


class NotAPortableExecutable(ValueError):
    """The file is not a PE image (or is truncated/garbled)."""


@dataclass
class Section:
    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int

    @property
    def span(self) -> int:
        return max(self.virtual_size, self.raw_size)


@dataclass
class PortableExecutable:
    path: Path
    machine: int
    bits: int
    timestamp: int
    sections: List[Section]
    export_directory: Tuple[int, int]  # (rva, size), zero when absent
    data: bytes

    @property
    def machine_name(self) -> str:
        return _MACHINES.get(self.machine, f"0x{self.machine:04x}")

    @classmethod
    def read(cls, path: Path) -> "PortableExecutable":
        data = Path(path).read_bytes()
        if len(data) < 0x40 or data[:2] != b"MZ":
            raise NotAPortableExecutable(f"{path}: no MZ header")
        (lfanew,) = struct.unpack_from("<I", data, 0x3C)
        if lfanew + 24 > len(data) or data[lfanew:lfanew + 4] != b"PE\0\0":
            raise NotAPortableExecutable(f"{path}: no PE signature")

        coff = lfanew + 4
        machine, section_count, timestamp = struct.unpack_from("<HHI", data, coff)
        (optional_size,) = struct.unpack_from("<H", data, coff + 16)
        optional = coff + 20
        if optional + 2 > len(data):
            raise NotAPortableExecutable(f"{path}: truncated optional header")
        (magic,) = struct.unpack_from("<H", data, optional)
        if magic not in (_PE32_MAGIC, _PE32_PLUS_MAGIC):
            raise NotAPortableExecutable(f"{path}: unknown optional header magic 0x{magic:04x}")
        bits = 32 if magic == _PE32_MAGIC else 64

        # Data directories start after the fixed part of the optional header.
        directories = optional + (96 if bits == 32 else 112)
        export_rva = export_size = 0
        if directories + 8 <= len(data):
            export_rva, export_size = struct.unpack_from("<II", data, directories)

        sections: List[Section] = []
        table = optional + optional_size
        for index in range(section_count):
            offset = table + index * 40
            if offset + 40 > len(data):
                break
            raw_name = data[offset:offset + 8].rstrip(b"\0")
            virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from(
                "<IIII", data, offset + 8)
            sections.append(Section(
                name=raw_name.decode("latin-1", "replace"),
                virtual_address=virtual_address,
                virtual_size=virtual_size,
                raw_offset=raw_pointer,
                raw_size=raw_size,
            ))
        if not sections:
            raise NotAPortableExecutable(f"{path}: no sections")

        return cls(path=Path(path), machine=machine, bits=bits, timestamp=timestamp,
                   sections=sections,
                   export_directory=(export_rva, export_size), data=data)

    # -- address translation ------------------------------------------------

    def offset_of(self, rva: int) -> Optional[int]:
        for section in self.sections:
            if section.virtual_address <= rva < section.virtual_address + section.span:
                delta = rva - section.virtual_address
                if delta >= section.raw_size:
                    return None  # inside the virtual tail: not backed by the file
                return section.raw_offset + delta
        return None

    def read_rva(self, rva: int, size: int) -> Optional[bytes]:
        offset = self.offset_of(rva)
        if offset is None or offset + size > len(self.data):
            return None
        return self.data[offset:offset + size]

    def read_cstring_rva(self, rva: int, limit: int = 512) -> Optional[str]:
        offset = self.offset_of(rva)
        if offset is None:
            return None
        end = self.data.find(b"\0", offset, offset + limit)
        if end < 0:
            return None
        return self.data[offset:end].decode("latin-1", "replace")

    # -- exports ------------------------------------------------------------

    def exports(self) -> List[str]:
        """Names from the export directory, e.g. ``CreateInterface``."""
        rva, size = self.export_directory
        if not rva or size < 40:
            return []
        header = self.read_rva(rva, 40)
        if header is None:
            return []
        (number_of_names,) = struct.unpack_from("<I", header, 24)
        (address_of_names,) = struct.unpack_from("<I", header, 32)
        table = self.read_rva(address_of_names, number_of_names * 4)
        if table is None:
            return []
        names: List[str] = []
        for index in range(number_of_names):
            (name_rva,) = struct.unpack_from("<I", table, index * 4)
            name = self.read_cstring_rva(name_rva)
            if name:
                names.append(name)
        return names

    # -- strings ------------------------------------------------------------

    def strings(self, minimum: int = 4, sections: Optional[Iterable[str]] = None) -> Iterator[str]:
        """Printable runs from the section data.

        Both narrow and UTF-16LE runs, because MSVC emits wide literals for a
        good part of the Windows build and those carry the same declarations.
        """
        wanted = {s.lower() for s in sections} if sections else None
        for section in self.sections:
            if wanted is not None and section.name.lower() not in wanted:
                continue
            blob = self.data[section.raw_offset:section.raw_offset + section.raw_size]
            yield from _ascii_runs(blob, minimum)
            yield from _utf16_runs(blob, minimum)


def _ascii_runs(blob: bytes, minimum: int) -> Iterator[str]:
    run = bytearray()
    for byte in blob:
        if 0x20 <= byte < 0x7F:
            run.append(byte)
            continue
        if len(run) >= minimum:
            yield run.decode("ascii")
        run.clear()


def _utf16_runs(blob: bytes, minimum: int) -> Iterator[str]:
    # Cheap scan: every other byte must be NUL, the others printable ASCII.
    index = 0
    length = len(blob) - 1
    while index < length:
        if 0x20 <= blob[index] < 0x7F and blob[index + 1] == 0:
            start = index
            text = bytearray()
            while index < length and 0x20 <= blob[index] < 0x7F and blob[index + 1] == 0:
                text.append(blob[index])
                index += 2
            if len(text) >= minimum:
                yield text.decode("ascii")
            if index == start:
                index += 2
        else:
            index += 2 if blob[index] == 0 else 1


# --------------------------------------------------------------------------
# Known interface versions
# --------------------------------------------------------------------------
#
# Extracted from this tree's own headers by scripts/gen-sdk-interfaces.sh.
# Comparing the retail module against it separates three cases that look
# identical in a raw string dump:
#
#   * the module names an interface this SDK also declares, at the same
#     version  -> nothing to do;
#   * same family, different version -> the module will refuse to load unless
#     our headers request exactly the version the retail build asks for;
#   * no such family here -> an L4D-only interface, i.e. work this tree has
#     never had.

# BEGIN GENERATED SDK INTERFACES
SDK_INTERFACES = frozenset({
    "ACHIEVEMENTMGR_INTERFACE_VERSION001", "BotManager001", "COLORCORRECTION_VERSION_1",
    "ClientLeafSystem002", "ClientRenderTargets001", "ClientStats004",
    "ClientVirtualReality001", "EngineTraceClient003", "EngineTraceServer003",
    "EntityInfoManager001", "FriendsNET003", "FriendsUser001",
    "G15_INTERFACE_VERSION001", "GAMEEVENTSMANAGER001", "GAMEEVENTSMANAGER002",
    "GameClientExports001", "GameInfoManager001", "HLTVDirector001",
    "HLTVPANEL001", "HLTVServer001", "Hammer001",
    "HapticsInterface001", "IEffects001", "IEngineSoundClient003",
    "IEngineSoundServer003", "IEngineVoice001", "IExternalTest001",
    "IExternalTestInitialize001", "IExternalTestValidation001", "IMaterialProxyFactory001",
    "IQuickTime002", "ISERVERPLUGINCALLBACKS001", "ISERVERPLUGINCALLBACKS002",
    "ISERVERPLUGINCALLBACKS003", "ISERVERPLUGINHELPERS001", "ISQLWRAPPER001",
    "IVideoServices002", "InputSystemVersion001", "KeyValuesSystem002",
    "MDLCache004", "MapData001", "MatSystemSurface008",
    "MaterialSystemHardwareConfig012", "PlayerInfoManager002", "PluginHelpersCheck001",
    "QueuedLoaderVersion004", "SDLMgrInterface001", "STEAMAPPLIST_INTERFACE_VERSION001",
    "STEAMAPPS_INTERFACE_VERSION007", "STEAMAPPTICKET_INTERFACE_VERSION001", "STEAMHTMLSURFACE_INTERFACE_VERSION_003",
    "STEAMHTTP_INTERFACE_VERSION002", "STEAMINVENTORY_INTERFACE_V001", "STEAMMUSICREMOTE_INTERFACE_VERSION001",
    "STEAMMUSIC_INTERFACE_VERSION001", "STEAMREMOTESTORAGE_INTERFACE_VERSION013", "STEAMSCREENSHOTS_INTERFACE_VERSION002",
    "STEAMUGC_INTERFACE_VERSION007", "STEAMUNIFIEDMESSAGES_INTERFACE_VERSION001", "STEAMUSERSTATS_INTERFACE_VERSION011",
    "STEAMVIDEO_INTERFACE_V001", "SceneFileCache002", "ServerGameClients003",
    "ServerGameClients004", "ServerGameDLL008", "ServerGameDLL009",
    "ServerGameDLL010", "ServerGameEnts001", "ServerGameTags001",
    "ServerList001", "ServerUploadGameStats001", "ShaderApi030",
    "ShaderDevice001", "ShaderDeviceMgr001", "ShaderDynamic001",
    "ShaderShadow010", "SoundSystem001", "SourceVirtualReality001",
    "SpatialPartition001", "StaticPropMgrClient004", "StaticPropMgrServer002",
    "SteamAddOn007", "SteamClient017", "SteamController003",
    "SteamFriends015", "SteamGameCoordinator001", "SteamGameServer012",
    "SteamGameServerStats001", "SteamGameStats001", "SteamMatchMaking009",
    "SteamMatchMakingServers002", "SteamNetworking005", "SteamStreamLauncher001",
    "SteamUser018", "SteamUtils007", "UnitTestV001",
    "VAudio002", "VAvi001", "VBAllocTracker001",
    "VBaseFileSystem011", "VBik001", "VCENTERPRINT002",
    "VCLIENTENGINETOOLS001", "VCLIENTTOOLS001", "VClient017",
    "VClientEntityList003", "VClientPrediction001", "VCvarQuery001",
    "VDataCache003", "VDataModelVersion001", "VDebugOverlay003",
    "VDmSerializers001", "VDmeMakeFileUtils001", "VENGINETOOL003",
    "VENGINETOOLFRAMEWORK003", "VEngineClient014", "VEngineCvar004",
    "VEngineEffects001", "VEngineModel016", "VEngineRandom001",
    "VEngineRenderView014", "VEngineServer021", "VEngineServer022",
    "VEngineServer023", "VEngineShadowMgr002", "VFileSystem022",
    "VGUI_Input005", "VGUI_InputInternal001", "VGUI_Localize005",
    "VGUI_Panel009", "VGUI_Scheme010", "VGUI_Surface030",
    "VGUI_System010", "VGUI_ivgui008", "VMDLLIB001",
    "VMaterialSystem081", "VMaterialSystemStub001", "VModelInfoClient006",
    "VModelInfoServer004", "VP4001", "VParticleSystemQuery001",
    "VPhysics031", "VPhysicsCollision007", "VPhysicsDebugOverlay001",
    "VPhysicsSurfaceProps001", "VProcessUtils001", "VSERVERCHOREOTOOLS001",
    "VSERVERENGINETOOLS001", "VSERVERTOOLS003", "VShaderUtil001",
    "VSoundEmitter002", "VStudioDataCache005", "VStudioRender025",
    "VTOOLDICTIONARY002", "VTOOLFRAMEWORKVERSION002", "VoiceServer002",
    "XboxSystemInterface001", "_IMaterialProxy003", "_ITextureCompositor000",
    "launchable_dll_1", "shadercompiledll_0", "vraddll_1",
})
# END GENERATED SDK INTERFACES

_TRAILING_VERSION = re.compile(r"^(.*?)_?(?:V)?(\d{3})$")


def interface_family(name: str) -> Tuple[str, Optional[int]]:
    """Split an interface string into (family, version).

    ``MatSystemSurface006`` -> ("MatSystemSurface", 6).
    ``STEAMAPPS_INTERFACE_VERSION003`` -> ("STEAMAPPS_INTERFACE_VERSION", 3).
    """
    match = _TRAILING_VERSION.match(name)
    if not match:
        return name, None
    family = match.group(1).rstrip("_")
    return family, int(match.group(2))


def sdk_versions(names: Iterable[str] = SDK_INTERFACES) -> Dict[str, List[int]]:
    """Every version this tree declares, per family.

    Several is normal: the headers keep the older interfaces around for
    compatibility, so ServerGameDLL has 008, 009 and 010 at once. A module
    naming any of them is fine; only a version outside the set is a mismatch.
    """
    table: Dict[str, Set[int]] = {}
    for name in names:
        family, version = interface_family(name)
        if version is not None:
            table.setdefault(family, set()).add(version)
    return {family: sorted(versions) for family, versions in table.items()}


@dataclass
class InterfaceAlignment:
    """How a module's interface strings line up with this SDK."""

    known: List[str] = field(default_factory=list)          # a version this SDK declares
    mismatched: Dict[str, Tuple[str, List[int]]] = field(default_factory=dict)
    unknown: List[str] = field(default_factory=list)        # not in the SDK at all

    def to_dict(self) -> Dict[str, object]:
        return {
            "known": self.known,
            "mismatched": {family: {"module": pair[0], "sdk_versions": pair[1]}
                           for family, pair in self.mismatched.items()},
            "unknown": self.unknown,
        }


def align_with_sdk(names: Sequence[str], sdk: Iterable[str] = SDK_INTERFACES) -> InterfaceAlignment:
    table = sdk_versions(sdk)
    alignment = InterfaceAlignment()
    for name in names:
        family, version = interface_family(name)
        if family not in table:
            alignment.unknown.append(name)
        elif version in table[family]:
            alignment.known.append(name)
        else:
            alignment.mismatched[family] = (name, table[family])
    return alignment


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
#
# These are *filters*, not proofs. The point is to turn a 10MB binary into a
# few thousand candidate names a human can read in five minutes, organised by
# kind, with counts that say which ones matter. False positives are cheap;
# missing an entire category is not.

# Valve interface strings end in a three-digit version written 0xx (VClient017,
# MDLCache004) or in ``VersionNNN``. The version must not be the tail of a
# longer number: XM1014 ends in "014" and ABGR8888 in "888", and both are
# animation-activity and image-format enums, not interfaces.
_INTERFACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?<![0-9])(?:0\d{2}|Version\d{3})$")
_NETPROP_RE = re.compile(r"^m_[A-Za-z][A-Za-z0-9_]*$")
_DATATABLE_RE = re.compile(r"^DT_[A-Za-z][A-Za-z0-9_]*$")
# No whitespace: a sentence that happens to end in ".txt" is not an asset path.
# No dll/so/exe either - those are system libraries, not game content.
_ASSET_RE = re.compile(r"^[\w./\\-]{1,200}\.(vmt|vtf|mdl|vvd|vtx|phy|wav|mp3|pcf|vcd|"
                       r"txt|res|cfg|vdf|bsp|nav|ain|lod|ani|vfe|gma|vpk)$",
                       re.IGNORECASE)
_LOWER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,63}$")

# Convar families. Source convars are overwhelmingly prefix-organised, and the
# prefixes below cover the ones an L4D1 port has to honour. Unknown prefixes
# still show up in the identifier bucket, so nothing is lost.
CONVAR_PREFIXES: Tuple[str, ...] = (
    "cl_", "sv_", "mp_", "z_", "director_", "survivor_", "sb_", "hud_", "fps_",
    "mat_", "r_", "gl_", "joy_", "mm_", "lb_", "tv_", "spec_", "inf_", "versus_",
    "coop_", "scavenge_", "sb_", "in_", "m_", "vgui_", "snd_", "dsp_", "ui_",
    "demo_", "ai_", "anim_", "phys_", "vprof_", "host_", "net_", "rate",
)

# Entity classname families. L4D1 entity names are snake_case with known
# prefixes; special infected are bare words.
ENTITY_PREFIXES: Tuple[str, ...] = (
    "weapon_", "prop_", "info_", "func_", "logic_", "trigger_", "env_", "point_",
    "filter_", "math_", "phys_", "npc_", "item_", "ammo_", "prop_dynamic",
    "ai_", "path_", "script_", "commentary_", "player_", "infection_", "director_",
    "upgrade_", "ladder", "light_", "shadow_", "water", "sky_", "worldspawn",
)
ENTITY_WORDS: Tuple[str, ...] = (
    "infected", "tank", "witch", "smoker", "boomer", "hunter", "spitter", "jockey",
    "charger", "survivor", "player", "worldspawn", "team_manager",
    "vote_controller", "entityflame", "graphler", "ragdoll_prop", "physics_prop",
)


@dataclass
class Inventory:
    path: str
    machine: str
    bits: int
    size: int
    exports: List[str] = field(default_factory=list)
    interfaces: List[str] = field(default_factory=list)
    netprops: List[str] = field(default_factory=list)
    datatables: List[str] = field(default_factory=list)
    convars: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    assets: List[str] = field(default_factory=list)
    identifiers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    alignment: Optional["InterfaceAlignment"] = None

    def buckets(self) -> Dict[str, List[str]]:
        return {
            "exports": self.exports,
            "interfaces": self.interfaces,
            "netprops": self.netprops,
            "datatables": self.datatables,
            "convars": self.convars,
            "entities": self.entities,
            "assets": self.assets,
            "identifiers": self.identifiers,
        }

    def to_dict(self) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "path": self.path,
            "machine": self.machine,
            "bits": self.bits,
            "size": self.size,
            "counts": {name: len(items) for name, items in self.buckets().items()},
            "warnings": list(self.warnings),
        }
        payload.update(self.buckets())
        if self.alignment is not None:
            payload["sdk_alignment"] = self.alignment.to_dict()
        return payload


def _looks_like_entity(text: str) -> bool:
    """Entity classnames are lowercase snake_case.

    Capitalised matches ("Boomer", "Tank") are display names and localisation
    keys - real, but not classnames - so they stay in identifiers. Enum-ish
    ALL_CAPS tokens (ITEM_FLAG_*, AMMO_TYPE_*) are likewise not entities.
    """
    if not _LOWER_NAME_RE.match(text):
        return False
    if text in ENTITY_WORDS:
        return True
    if not text.startswith(ENTITY_PREFIXES):
        return False
    return "_" in text


def _looks_like_convar(text: str) -> bool:
    """Convar names are lowercase, single-token, and prefix-organised."""
    if not _LOWER_NAME_RE.match(text):
        return False
    if not text.startswith(CONVAR_PREFIXES):
        return False
    return len(text) >= 4 and not text.endswith("_")


def classify(pe: PortableExecutable, minimum: int = 4) -> Inventory:
    """Bucket the binary's strings into a readable inventory."""
    inventory = Inventory(path=str(pe.path), machine=pe.machine_name, bits=pe.bits,
                          size=len(pe.data))
    inventory.exports = sorted(set(pe.exports()))
    exported = set(inventory.exports)

    seen: Set[str] = set()
    candidates: Dict[str, Set[str]] = {name: set() for name in
                                       ("interfaces", "netprops", "datatables", "convars",
                                        "entities", "assets", "identifiers")}
    for text in pe.strings(minimum=minimum):
        if text in seen:
            continue
        seen.add(text)
        name = text.strip()
        if not name or name in exported:
            continue
        if _INTERFACE_RE.match(name):
            candidates["interfaces"].add(name)
        elif _NETPROP_RE.match(name):
            candidates["netprops"].add(name)
        elif _DATATABLE_RE.match(name):
            candidates["datatables"].add(name)
        elif _ASSET_RE.match(name):
            candidates["assets"].add(name)
        elif _looks_like_convar(name):
            candidates["convars"].add(name)
        elif _looks_like_entity(name):
            candidates["entities"].add(name)
        elif _IDENTIFIER_RE.match(name):
            candidates["identifiers"].add(name)

    inventory.interfaces = sorted(candidates["interfaces"])
    inventory.netprops = sorted(candidates["netprops"])
    inventory.datatables = sorted(candidates["datatables"])
    inventory.convars = sorted(candidates["convars"])
    inventory.entities = sorted(candidates["entities"])
    inventory.assets = sorted(candidates["assets"])
    inventory.identifiers = sorted(candidates["identifiers"])

    if inventory.interfaces:
        inventory.alignment = align_with_sdk(inventory.interfaces)
    inventory.warnings.extend(_sanity_warnings(pe, len(seen), inventory))
    return inventory


def _sanity_warnings(pe: PortableExecutable, string_count: int,
                     inventory: Inventory) -> List[str]:
    warnings: List[str] = []
    if pe.bits != 32:
        warnings.append(f"{pe.bits}-bit image: the retail L4D1 client/server are 32-bit, "
                        "so this is probably not the file you meant")
    if string_count < 500:
        warnings.append(f"only {string_count} strings from {len(pe.data) // 1024} KB - "
                        "the image looks packed, encrypted or truncated (SteamStub?)")
    if not inventory.exports:
        warnings.append("no export directory: if this is client.dll it should export "
                        "CreateInterface, so the file may be packed")
    if not inventory.interfaces:
        warnings.append("no interface versions found - either a stripped image or not "
                        "a Source module")
    return warnings


def mine(path: Path, minimum: int = 4) -> Inventory:
    return classify(PortableExecutable.read(path), minimum=minimum)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

_REPORT_ORDER = ("exports", "interfaces", "datatables", "entities", "netprops",
                 "convars", "assets")
_DESCRIPTIONS = {
    "exports": "exported symbols (CreateInterface and friends)",
    "interfaces": "interface versions the module exports or asks for",
    "datatables": "network data tables (DT_*)",
    "entities": "entity classnames",
    "netprops": "network properties (recv/send table entries)",
    "convars": "convar-like names, by family prefix",
    "assets": "asset paths (models, materials, sounds, scripts)",
    "identifiers": "other identifiers (commands, keys, misc strings)",
}


def report(inventory: Inventory, top: Optional[int] = 40) -> str:
    lines = [
        f"{Path(inventory.path).name}  {inventory.machine}  {inventory.bits}-bit  "
        f"{inventory.size / 1024 / 1024:.1f} MB",
        "",
    ]
    for warning in inventory.warnings:
        lines.append(f"  ! {warning}")
    if inventory.warnings:
        lines.append("")

    buckets = inventory.buckets()
    for name in _REPORT_ORDER:
        items = buckets.get(name) or []
        lines.append(f"{name} ({len(items)}) - {_DESCRIPTIONS[name]}")
        if not items:
            lines.append("    (none)")
        else:
            shown = items if top is None else items[:top]
            for item in shown:
                lines.append(f"    {item}")
            if top is not None and len(items) > top:
                lines.append(f"    ... {len(items) - top} more (--top 0 for all, --json for everything)")
        if name == "interfaces" and inventory.alignment is not None:
            lines.extend(_alignment_lines(inventory.alignment, top))
        lines.append("")

    other = len(inventory.identifiers)
    lines.append(f"identifiers: {other} others not shown (--json to dump, or --bucket identifiers)")
    return "\n".join(lines).rstrip() + "\n"


def _alignment_lines(alignment: InterfaceAlignment, top: Optional[int]) -> List[str]:
    """The part worth reading: what this module wants that we do not provide."""
    lines: List[str] = []
    if alignment.mismatched:
        lines.append("")
        lines.append("    version mismatches - these decide whether the module loads:")
        pairs = sorted(alignment.mismatched.items())
        for family, (module_name, versions) in (pairs if top is None else pairs[:top]):
            declared = ", ".join(f"{family}{version:03d}" for version in versions)
            lines.append(f"      {module_name}  (this SDK declares {declared})")
        if top is not None and len(pairs) > top:
            lines.append(f"      ... {len(pairs) - top} more")
    if alignment.unknown:
        lines.append("")
        lines.append(f"    not declared by this SDK ({len(alignment.unknown)}) "
                     f"- L4D-only, work this tree has never had:")
        unknown = alignment.unknown if top is None else alignment.unknown[:top]
        for name in unknown:
            lines.append(f"      {name}")
        if top is not None and len(alignment.unknown) > top:
            lines.append(f"      ... {len(alignment.unknown) - top} more")
    if alignment.known:
        lines.append("")
        lines.append(f"    {len(alignment.known)} match this SDK exactly")
    return lines


def dump_json(inventory: Inventory) -> str:
    return json.dumps(inventory.to_dict(), indent=2, sort_keys=False)


def merge(inventories: Sequence[Inventory]) -> Dict[str, List[str]]:
    """Union of several inventories - e.g. client.dll plus server.dll."""
    merged: Dict[str, Set[str]] = {}
    for inventory in inventories:
        for name, items in inventory.buckets().items():
            merged.setdefault(name, set()).update(items)
    return {name: sorted(items) for name, items in merged.items()}


@dataclass
class Comparison:
    """What one module declares that the other does not."""

    left: str
    right: str
    only_left: Dict[str, List[str]] = field(default_factory=dict)
    only_right: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "left": self.left,
            "right": self.right,
            "only_left": self.only_left,
            "only_right": self.only_right,
        }


def compare(left: Inventory, right: Inventory) -> Comparison:
    """Names present in one module's inventory but not the other's.

    Useful for the question this project actually has: what does the retail
    server declare that the client does not (and the reverse)? A name that only
    one side knows is a serialisation surface the two must agree on.
    """
    result = Comparison(left=left.path, right=right.path)
    left_buckets, right_buckets = left.buckets(), right.buckets()
    for name in left_buckets:
        left_items = set(left_buckets[name])
        right_items = set(right_buckets.get(name, ()))
        result.only_left[name] = sorted(left_items - right_items)
        result.only_right[name] = sorted(right_items - left_items)
    return result


def report_comparison(comparison: Comparison, top: Optional[int] = 20) -> str:
    left_name = Path(comparison.left).name
    right_name = Path(comparison.right).name
    lines: List[str] = []
    for name in _REPORT_ORDER:
        extra_left = comparison.only_left.get(name, [])
        extra_right = comparison.only_right.get(name, [])
        if not extra_left and not extra_right:
            continue
        lines.append(f"{name}: only in {left_name} ({len(extra_left)}), "
                     f"only in {right_name} ({len(extra_right)})")
        for label, items in ((f"  {left_name}", extra_left), (f"  {right_name}", extra_right)):
            shown = items if top is None else items[:top]
            for item in shown:
                lines.append(f"{label}: {item}")
            if top is not None and len(items) > top:
                lines.append(f"{label}: ... {len(items) - top} more")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n" if lines else "identical inventories\n"


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mine",
        description="Inventory a shipped Source module (client.dll / server.dll): "
                    "interfaces, netprops, convars, entity classnames, assets. "
                    "Reads declarations only - not logic.",
    )
    parser.add_argument("files", nargs="+", help="PE files to read")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--bucket", help="print one bucket only, one name per line")
    parser.add_argument("--top", type=int, default=40,
                        help="names per bucket in the report (0 = all)")
    parser.add_argument("--min-length", type=int, default=4,
                        help="shortest string to consider (default 4)")
    parser.add_argument("--compare", help="show which names the other module adds")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Standalone entry point: ``python3 mine.py client.dll``.

    Deliberately free of package imports, so this one file can be downloaded on
    its own onto a phone and used without cloning anything.
    """
    args = build_parser().parse_args(argv)
    top = None if args.top == 0 else args.top
    status = 0
    inventories: List[Inventory] = []

    for name in args.files:
        path = Path(name).expanduser()
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            status = 1
            continue
        try:
            inventories.append(mine_path(path, minimum=args.min_length))
        except (NotAPortableExecutable, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            status = 1

    if args.compare:
        if len(inventories) != 1:
            print("--compare takes exactly one file plus --compare <other>", file=sys.stderr)
            return 2
        try:
            other = mine_path(Path(args.compare).expanduser(), minimum=args.min_length)
        except (NotAPortableExecutable, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        comparison = compare(inventories[0], other)
        if args.json:
            print(json.dumps(comparison.to_dict(), indent=2))
        else:
            sys.stdout.write(report_comparison(comparison, top=top))
        return status

    if args.json:
        payload = [inventory.to_dict() for inventory in inventories]
        print(json.dumps(payload[0] if len(payload) == 1 else payload, indent=2))
        return status

    for inventory in inventories:
        if args.bucket:
            buckets = inventory.buckets()
            if args.bucket not in buckets:
                print(f"error: no bucket {args.bucket!r}; try one of "
                      f"{', '.join(sorted(buckets))}", file=sys.stderr)
                return 2
            items = buckets[args.bucket]
            shown = items if top is None else items[:top]
            for item in shown:
                print(item)
            if top is not None and len(items) > top:
                print(f"... {len(items) - top} more (--top 0 for all)", file=sys.stderr)
            continue
        sys.stdout.write(report(inventory, top=top))
    return status


def mine_path(path: Path, minimum: int = 4) -> Inventory:
    """Alias kept for readability at call sites: ``mine_path(p)``."""
    return mine(path, minimum=minimum)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
