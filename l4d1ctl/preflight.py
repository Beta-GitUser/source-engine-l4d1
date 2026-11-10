"""Predicting a launch, and explaining a crash.

Two questions, asked in this order:

``run()``
    *Will this launch fail, and why?* Checks are ordered the way the engine
    walks them — executable, gameinfo, search paths, game module, content —
    because the first thing that is wrong is the thing you will see, and
    everything after it is noise.

``triage_log()``
    *Why did it fail?* The engine reports most fatal conditions as one terse
    line buried in a few thousand lines of startup spew. The rules here turn
    that log back into a sentence and a fix.

Neither function launches anything, downloads anything, or needs Steam. Both
are cheap enough to run before every launch, including from the launcher UI
(``l4d1ctl check --json``).
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import gamemod, gameinfo
from .paths import PortPaths

OK = "ok"
WARN = "warn"
FAIL = "fail"

# If the engine can read the module but the module cannot reach the interface
# the engine asks for, LoadModule succeeds and CreateInterface returns NULL.
_INTERFACE_RE = re.compile(rb"(ServerGameDLL|VClient|VEngineServer|VEngineClient)(\d{3})")
_INTERFACE_SCAN_LIMIT = 96 << 20  # modules are ~10-30MB; don't read a 4GB core dump

_HOST_MACHINES = {
    "x86_64": "x86-64",
    "amd64": "x86-64",
    "aarch64": "AArch64",
    "arm64": "AArch64",
    "i386": "x86",
    "i486": "x86",
    "i586": "x86",
    "i686": "x86",
    "x86": "x86",
    "armv7l": "ARM",
    "armv8l": "ARM",
    "riscv64": "RISC-V",
    # Android ABI names, so CI can just pass $ARCH through.
    "arm64-v8a": "AArch64",
    "armeabi-v7a": "ARM",
    "armeabi-v7a-hard": "ARM",
    "armeabi": "ARM",
    "ppc64le": "PowerPC",
    "mips": "MIPS",
}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    code: str
    level: str
    message: str
    fix: str = ""

    def line(self) -> str:
        text = f"[{self.level.upper():4}] {self.code:<20} {self.message}"
        if self.fix and self.level != OK:
            text += f"\n         -> {self.fix}"
        return text

    def to_dict(self) -> dict:
        return {"code": self.code, "level": self.level, "message": self.message, "fix": self.fix}


@dataclass
class Report:
    gamedir: Path
    engine: str
    checks: List[Check] = field(default_factory=list)
    argv: List[str] = field(default_factory=list)
    gamedir_name: str = ""

    # -- building ----------------------------------------------------------
    def add(self, code: str, level: str, message: str, fix: str = "") -> Check:
        check = Check(code, level, message, fix)
        self.checks.append(check)
        return check

    # -- results -----------------------------------------------------------
    @property
    def blocking(self) -> List[Check]:
        return [c for c in self.checks if c.level == FAIL]

    @property
    def warnings(self) -> List[Check]:
        return [c for c in self.checks if c.level == WARN]

    @property
    def ok(self) -> bool:
        return not self.blocking

    @property
    def verdict(self) -> str:
        if self.blocking:
            return "will-fail"
        if self.warnings:
            return "ready-with-warnings"
        return "ready"

    def headline(self) -> str:
        if self.blocking:
            return (f"launch would fail ({len(self.blocking)} blocking issue"
                    f"{'' if len(self.blocking) == 1 else 's'})")
        if self.warnings:
            return f"launch looks fine ({len(self.warnings)} warning{'' if len(self.warnings) == 1 else 's'})"
        return "launch looks fine"

    def render(self) -> str:
        lines = [f"preflight: {self.headline()}"]
        for check in self.checks:
            lines.append("  " + check.line())
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "gamedir": str(self.gamedir),
            "engine": self.engine,
            "target_machine": host_machine(),
            "verdict": self.verdict,
            "ok": self.ok,
            "argv": list(self.argv),
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

# The other direction: an architecture name back to something the user can put
# in L4D1_TARGET_MACHINE.
_TARGET_ALIASES = {
    "x86-64": "x86_64",
    "AArch64": "aarch64",
    "ARM": "armeabi-v7a",
    "x86": "x86",
    "RISC-V": "riscv64",
    "PowerPC": "ppc64le",
    "MIPS": "mips",
}


def target_alias(machine: str) -> str:
    """A value for L4D1_TARGET_MACHINE that means ``machine``."""
    return _TARGET_ALIASES.get(machine, machine.lower().replace("-", "_"))


def host_machine() -> str:
    raw = (os.environ.get("L4D1_TARGET_MACHINE") or _platform_machine()).lower()
    return _HOST_MACHINES.get(raw, raw)


def _platform_machine() -> str:
    import platform

    return platform.machine()


def host_bits() -> int:
    return struct.calcsize("P") * 8


def resolve_engine(engine: str) -> Optional[Path]:
    """Find the engine binary the way the shell would."""
    candidate = Path(engine).expanduser()
    looks_like_path = os.sep in engine or (os.altsep and os.altsep in engine)
    if looks_like_path or candidate.is_absolute():
        return candidate if candidate.is_file() else None
    found = shutil.which(engine)
    if found:
        return Path(found)
    # The engine is often shipped next to the launcher rather than on PATH.
    local = Path.cwd() / engine
    if local.is_file():
        return local.resolve()
    return None


def interface_versions(path: Path, *, limit: int = _INTERFACE_SCAN_LIMIT) -> Dict[str, List[str]]:
    """Version strings of the game interfaces a binary mentions.

    ``{"VClient": ["017"], "ServerGameDLL": ["010"]}``. Only used to notice that
    a module was built against a different ``public/`` than the engine.
    """
    found: Dict[str, set] = {}
    try:
        size = Path(path).stat().st_size
        if size > limit:
            return {}
        data = Path(path).read_bytes()
    except OSError:
        return {}
    for match in _INTERFACE_RE.finditer(data):
        name = match.group(1).decode()
        found.setdefault(name, set()).add(match.group(2).decode())
    return {name: sorted(versions) for name, versions in found.items()}


# ---------------------------------------------------------------------------
# gameinfo search paths
# ---------------------------------------------------------------------------

_SEARCH_LINE = re.compile(
    r'^\s*"?(Game|GameBin|Platform|Mod|Write|DefaultWrite)"?\s+(\S.*?)\s*$',
    re.IGNORECASE,
)
_SEARCH_BLOCK_START = re.compile(r'^"?SearchPaths"?$', re.IGNORECASE)


@dataclass(frozen=True)
class SearchPath:
    kind: str
    raw: str

    def resolve(self, gamedir: Path) -> Optional[Path]:
        """Resolve the ``|macro|`` forms the engine understands.

        Returns None when the entry cannot be checked from here (it points into
        the engine installation, which is not our business).
        """
        value = self.raw.strip().strip('"')
        wildcard = value.endswith("*")
        if wildcard:
            value = value[:-1].rstrip("/\\")

        lowered = value.lower()
        if lowered.startswith("|gameinfo_path|"):
            remainder = value[len("|gameinfo_path|"):].strip("/\\")
            return _resolve_relative(gamedir, remainder, wildcard)
        if lowered.startswith("|all_source_engine_path|") or lowered.startswith("|all_source_engine_paths|"):
            return None
        if lowered.startswith("|gamebin|"):
            return gamedir / "bin"
        if value.startswith("|"):
            return None
        return _resolve_relative(gamedir, value, wildcard)


def _resolve_relative(gamedir: Path, remainder: str, wildcard: bool) -> Path:
    if remainder in ("", "."):
        return gamedir
    path = gamedir / remainder
    return path.parent if wildcard else path


def parse_search_paths(text: str) -> List[SearchPath]:
    """Pull the SearchPaths entries out of a gameinfo.txt.

    Tolerates both spellings found in the wild: the quoted Valve style
    (``"SearchPaths"``) and the bare one l4d1ctl generates, with or without the
    opening brace on the same line.
    """
    paths: List[SearchPath] = []
    depth = 0
    inside = False
    for raw_line in text.splitlines():
        line = raw_line.split("//")[0]
        stripped = line.strip()

        if not inside:
            if _SEARCH_BLOCK_START.match(stripped.replace("{", "").strip()):
                inside = True
                depth = stripped.count("{") - stripped.count("}")
            continue

        opens = line.count("{")
        closes = line.count("}")
        if opens or closes:
            depth += opens - closes
            if closes and depth <= 0:
                break
            continue
        if depth != 1:
            continue
        match = _SEARCH_LINE.match(line)
        if match:
            paths.append(SearchPath(match.group(1), match.group(2)))
    return paths


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

def _check_engine(report: Report, engine: str) -> Optional[Path]:
    resolved = resolve_engine(engine)
    if resolved is None:
        report.add(
            "engine.binary", FAIL, f"engine binary not found: {engine}",
            "pass --engine /path/to/hl2_linux, or put it next to the launcher "
            "(on Android: /data/local/tmp/srceng/hl2_linux)",
        )
        return None

    info = gamemod.inspect_binary(resolved)
    if info.format == "unknown":
        try:
            shebang = resolved.open("rb").read(2) == b"#!"
        except OSError:
            shebang = False
        described = "shell script" if shebang else info.summary
    else:
        described = info.summary
    report.add("engine.binary", OK, f"{resolved} ({described})")

    if not os.access(resolved, os.X_OK):
        report.add("engine.exec", FAIL, f"engine is not executable: {resolved}",
                   f"chmod +x {resolved}")

    if info.format in ("ELF", "Mach-O") and info.bits and info.bits != host_bits():
        report.add(
            "engine.arch", FAIL,
            f"engine is {info.bits}-bit, this machine is {host_bits()}-bit",
            "build the engine for the target that will run it",
        )
    elif info.format == "ELF" and info.machine:
        expected = host_machine()
        if expected in _HOST_MACHINES.values() and info.machine != expected:
            report.add(
                "engine.arch", FAIL,
                f"engine is {info.machine}, this machine is {expected}",
                f"cross-compiled for a device? declare it: "
                f"L4D1_TARGET_MACHINE={target_alias(info.machine)} l4d1ctl check "
                "(see docs/L4D1/BUILDING.md)",
            )
        else:
            report.add("engine.arch", OK, f"{info.machine} matches this machine")
    elif info.format == "PE":
        report.add("engine.arch", FAIL, "engine is a Windows executable",
                   "this port runs the Linux/Android engine binary")
    return resolved


def _check_gamedir(report: Report, paths: PortPaths) -> None:
    gamedir = paths.gamedir
    if not gamedir.is_dir():
        report.add("gamedir", FAIL, f"game directory does not exist: {gamedir}",
                   "run `l4d1ctl fetch` / `l4d1ctl import` first, or point --gamedir at it")
        return
    report.add("gamedir", OK, f"{gamedir}")

    # The engine resolves `-game <name>` relative to its own working dir; a name
    # that does not match the folder means it will look somewhere else entirely.
    if report.gamedir_name and report.gamedir_name != gamedir.name:
        report.add(
            "gamedir.name", WARN,
            f"-game {report.gamedir_name} but the folder is named {gamedir.name}",
            f"launch from {gamedir.parent} or rename the folder",
        )

    probe = gamedir / ".l4d1ctl_write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        report.add("gamedir.writable", OK, "writable")
    except OSError as exc:
        report.add(
            "gamedir.writable", FAIL, f"game directory is not writable ({exc.__class__.__name__})",
            "the engine writes cfg/, save/ and crash logs here; move the install off a "
            "read-only mount or fix permissions",
        )

    try:
        free = shutil.disk_usage(gamedir).free
    except OSError:
        return
    if free < 64 * 1024 * 1024:
        report.add("gamedir.space", WARN, f"only {free / (1024 * 1024):.0f} MiB free",
                   "maps and save games need room; the engine fails late when full")


def _check_gameinfo(report: Report, paths: PortPaths) -> None:
    gameinfo_path = paths.gameinfo
    if not gameinfo_path.is_file():
        report.add("gameinfo", FAIL, "gameinfo.txt is missing",
                   "run `l4d1ctl init` (or copy the one from your Steam install)")
        return

    try:
        text = gameinfo_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.add("gameinfo", FAIL, f"gameinfo.txt is unreadable: {exc}")
        return

    problems = gameinfo.check(paths)
    if problems:
        for problem in problems:
            report.add("gameinfo", FAIL, problem, "run `l4d1ctl init --force` to regenerate")
    else:
        report.add("gameinfo", OK, f"present ({'generated' if gameinfo.is_generated(gameinfo_path) else 'hand-written'})")

    search_paths = parse_search_paths(text)
    if not search_paths:
        report.add("gameinfo.search", FAIL, "gameinfo.txt has no SearchPaths entries",
                   "without a Game search path nothing is mounted and every map fails to load")
        return

    checked = 0
    for entry in search_paths:
        resolved = entry.resolve(paths.gamedir)
        if resolved is None:
            continue
        checked += 1
        code = f"search.{entry.kind.lower()}"
        if resolved.exists():
            report.add(code, OK, f"{entry.kind} {entry.raw} -> {resolved}")
            continue
        fix = ""
        if entry.kind.lower() == "game" and "*" in entry.raw:
            fix = "only needed once a mod is enabled"
            report.add(code, WARN, f"{entry.kind} {entry.raw} does not exist yet", fix)
        elif entry.kind.lower() == "game":
            report.add(code, FAIL, f"{entry.kind} {entry.raw} does not exist",
                       "the engine will find no content on this path")
        else:
            report.add(code, WARN, f"{entry.kind} {entry.raw} does not exist")

    if checked == 0:
        report.add("gameinfo.search", WARN, "no checkable search paths (all point into the engine)")


def _check_modules(report: Report, paths: PortPaths, engine: Optional[Path]) -> None:
    modules = gamemod.scan_modules(paths.bindir)
    if not paths.bindir.is_dir():
        report.add("module.dir", FAIL, f"no bin/ directory in {paths.gamedir}",
                   "run `l4d1ctl module install --from <build dir>`")
        return
    if not modules:
        report.add(
            "module.dir", FAIL, f"no game module in {paths.bindir}",
            "the engine prints 'Unable to load client DLL'; run "
            "`./waf configure -T release --build-games left4dead && ./waf build` "
            "then `l4d1ctl module install --from game/left4dead/bin`",
        )
        return

    engine_ifaces = interface_versions(engine) if engine else {}
    expected = host_machine()

    for name in gamemod.REQUIRED_MODULES:
        module = modules.get(name)
        if module is None:
            report.add(
                f"module.{name}", FAIL, f"{name} module missing from bin/",
                "the engine cannot start without it: build with "
                "`--build-games left4dead` and install again",
            )
            continue

        info = module.info
        code = f"module.{name}"
        if info.format == "PE":
            report.add(code, FAIL, f"{module.path.name} is a Windows DLL",
                       "PC binaries cannot be loaded by the Linux/Android engine; "
                       "rebuild the game code for the target")
            continue
        if info.format == "unknown":
            report.add(code, WARN, f"{module.path.name}: format not recognised ({info.summary})")
        elif info.bits and info.bits != host_bits():
            report.add(code, FAIL,
                       f"{module.path.name} is {info.bits}-bit, engine is {host_bits()}-bit",
                       "rebuild the module for the target")
        elif info.machine and expected in _HOST_MACHINES.values() and info.machine != expected:
            report.add(code, FAIL,
                       f"{module.path.name} is {info.machine}, engine is {expected}",
                       f"rebuild it for {expected}, or - if this really is a "
                       f"{info.machine} staging folder - declare the target: "
                       f"L4D1_TARGET_MACHINE={target_alias(info.machine)}")
        else:
            report.add(code, OK, f"{module.path.name} ({info.summary})")

        # Interface drift: rebuilt game code but an older/newer public/ header.
        wanted_key = "VClient" if name == "client" else "ServerGameDLL"
        provided = interface_versions(module.path).get(wanted_key, [])
        asked = engine_ifaces.get(wanted_key, [])
        if provided and asked and not set(provided) & set(asked):
            report.add(
                f"module.{name}.interface", FAIL,
                f"{wanted_key} mismatch: engine asks {', '.join(wanted_key + v for v in asked)}, "
                f"module provides {', '.join(wanted_key + v for v in provided)}",
                "rebuild the game module against this engine's public/ headers "
                "(a stale object file is the usual cause: `./waf clean`)",
            )
        elif provided:
            report.add(f"module.{name}.interface", OK, f"{wanted_key}{provided[0]}")


def _check_content(report: Report, paths: PortPaths) -> None:
    gamedir = paths.gamedir
    vpks = sorted(p.name for p in gamedir.glob("*_dir.vpk"))
    loose = [name for name in ("scripts", "materials", "models", "sound", "resource")
             if (gamedir / name).is_dir()]

    if vpks:
        report.add("content", OK, f"{len(vpks)} VPK pack(s): {', '.join(vpks[:4])}"
                                 + (" ..." if len(vpks) > 4 else ""))
        broken = []
        for name in vpks[:4]:
            header = (gamedir / name).read_bytes()[:12] if (gamedir / name).stat().st_size >= 12 else b""
            if len(header) < 12 or header[:4] != b"\x34\x12\xaa\x55":
                broken.append(name)
        if broken:
            report.add("content.vpk", FAIL, f"not a VPK (bad signature): {', '.join(broken)}",
                       "the copy was truncated or the file is a Steam download placeholder; "
                       "re-run `l4d1ctl fetch --run` / `import`")
    elif loose:
        report.add("content", WARN, f"no *_dir.vpk, loose folders present: {', '.join(loose)}",
                   "fine for a mod-only install")
    else:
        report.add("content", FAIL, "no game content (no *_dir.vpk, no content folders)",
                   "run `l4d1ctl fetch --run` or `l4d1ctl import`")

    maps = gamedir / "maps"
    bsp_files = sorted(p for p in maps.glob("*.bsp")) if maps.is_dir() else []
    if bsp_files and not vpks:
        size = bsp_files[0].stat().st_size
        if size < 1024 or bsp_files[0].read_bytes()[:4] != b"VBSP":
            report.add("content.bsp", FAIL, f"{bsp_files[0].name} is not a BSP",
                       "the map failed to download completely; re-fetch it")

    if not (gamedir / "resource").is_dir() and not vpks:
        report.add("content.resource", WARN, "no resource/ folder",
                   "the UI scheme and fonts live there; the menus will look broken")


def _check_argv(report: Report, paths: PortPaths, argv: Sequence[str]) -> None:
    if not argv:
        return
    lowered = [a.lower() for a in argv]
    if "-steam" in lowered and "-nosteam" in lowered:
        report.add("argv.steam", WARN, "both -steam and -nosteam were passed",
                   "the last one wins; the port should pass -nosteam only")

    if "+exec" in lowered:
        index = lowered.index("+exec")
        if index + 1 < len(argv):
            cfg = argv[index + 1]
            candidate = paths.cfg_dir / cfg
            if not candidate.is_file():
                report.add("argv.exec", FAIL, f"+exec {cfg} but {candidate} does not exist",
                           "the engine prints 'exec: couldn't exec' and starts with no config")
            else:
                report.add("argv.exec", OK, f"+exec {cfg}")

    for flag in ("-game", "-map"):
        if lowered.count(flag) > 1:
            report.add("argv.duplicate", WARN, f"{flag} appears more than once",
                       "only the first occurrence is used")


def _check_mods(report: Report, paths: PortPaths) -> None:
    if not paths.mods_dir.is_dir():
        return
    try:
        from . import mods as mods_module

        manager = mods_module.ModManager(paths)
        issues = manager.validate()
    except Exception as exc:  # noqa: BLE001 - a broken mod list must not block a launch
        report.add("mods", WARN, f"could not read the mod list: {exc}")
        return

    severe = [issue for issue in issues if getattr(issue, "level", "warn") == "fail"]
    if severe:
        for issue in severe:
            report.add("mods", FAIL, str(issue), "run `l4d1ctl mod sync`")
    elif issues:
        report.add("mods", WARN, f"{len(issues)} mod issue(s)", "run `l4d1ctl mod list --issues`")
    else:
        report.add("mods", OK, "staging is consistent")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(paths: PortPaths, *, engine: str = "hl2_linux",
        argv: Optional[Sequence[str]] = None) -> Report:
    """Run every pre-launch check, in the order the engine will hit them."""
    report = Report(gamedir=Path(paths.gamedir), engine=engine, argv=list(argv or []))
    report.gamedir_name = paths.gamedir.name if "-game" not in [a.lower() for a in (argv or [])] else ""

    # `-game <name>` may differ from the folder name; that is the engine's view.
    args = list(argv or [])
    lowered = [a.lower() for a in args]
    if "-game" in lowered:
        index = lowered.index("-game")
        if index + 1 < len(args):
            report.gamedir_name = args[index + 1]

    binary = _check_engine(report, engine)
    _check_gamedir(report, paths)
    _check_gameinfo(report, paths)
    _check_modules(report, paths, binary)
    _check_content(report, paths)
    _check_argv(report, paths, argv or [])
    _check_mods(report, paths)
    return report


# ---------------------------------------------------------------------------
# Log triage
# ---------------------------------------------------------------------------

FATAL = "fatal"
INFO = "info"


@dataclass(frozen=True)
class Finding:
    level: str
    title: str
    detail: str
    fix: str = ""
    count: int = 1
    sample: str = ""

    def line(self) -> str:
        text = f"[{self.level.upper():5}] {self.title}"
        if self.count > 1:
            text += f" (x{self.count})"
        text += f"\n        {self.detail}"
        if self.sample:
            text += f"\n        > {self.sample}"
        if self.fix:
            text += f"\n        -> {self.fix}"
        return text

    def to_dict(self) -> dict:
        return {"level": self.level, "title": self.title, "detail": self.detail,
                "fix": self.fix, "count": self.count, "sample": self.sample}


@dataclass
class Triage:
    findings: List[Finding] = field(default_factory=list)
    lines: int = 0
    unknown_entities: List[str] = field(default_factory=list)
    exit_code: Optional[int] = None
    log_path: Optional[Path] = None

    @property
    def crashed(self) -> bool:
        return any(f.level == FATAL for f in self.findings)

    @property
    def verdict(self) -> str:
        if self.crashed:
            return "failed"
        if self.exit_code not in (None, 0):
            return f"exited {self.exit_code}"
        return "clean"

    def headline(self) -> str:
        if self.crashed:
            fatal = [f for f in self.findings if f.level == FATAL]
            return f"launch failed: {fatal[0].title}"
        if self.exit_code not in (None, 0):
            return f"engine exited {self.exit_code} without a recognised error"
        return "engine exited cleanly"

    def render(self) -> str:
        lines = [f"triage: {self.headline()}"]
        for finding in self.findings:
            lines.append("  " + finding.line())
        has_entity_finding = any(f.title == "unknown map entities" for f in self.findings)
        if self.unknown_entities and not has_entity_finding:
            lines.append(
                f"  [INFO ] unknown map entities: {len(self.unknown_entities)} class(es): "
                + ", ".join(self.unknown_entities[:12])
                + (" ..." if len(self.unknown_entities) > 12 else "")
            )
            lines.append("        expected with the HL2 bootstrap; the map still loads and")
            lines.append("        this list is the checklist for L4D game code")
        if self.log_path:
            lines.append(f"  log: {self.log_path}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "log": str(self.log_path) if self.log_path else None,
            "unknown_entities": self.unknown_entities,
            "findings": [f.to_dict() for f in self.findings],
        }


# (regex, level, title, detail, fix)
_RULES: Tuple[Tuple[str, str, str, str, str], ...] = (
    (r"Unable to load client DLL|Could not load (?:the )?client(?: DLL| library)|dlopen.*client",
     FATAL, "client module failed to load",
     "the engine could not load bin/client.so.",
     "l4d1ctl check  # then: l4d1ctl module install --from game/left4dead/bin --force"),

    (r"Unable to load server DLL|Could not load (?:the )?server(?: DLL| library)|dlopen.*server",
     FATAL, "server module failed to load",
     "the engine could not load bin/server.so.",
     "l4d1ctl check  # then: l4d1ctl module install --from game/left4dead/bin --force"),

    (r"undefined symbol:?\s*(\S+)",
     FATAL, "undefined symbol",
     "the module was built against different engine headers than the engine binary.",
     "rebuild game code against this engine's public/ (`./waf clean && ./waf build`). "
     "Mixing a prebuilt module with a rebuilt engine is the usual cause"),

    (r"wrong ELF class|ELFCLASS|Exec format error|exec format error",
     FATAL, "architecture mismatch",
     "the binary and the module are for different CPU architectures.",
     "check `l4d1ctl check` -> module.arch; rebuild for the target"),

    (r"Host_Error:\s*(.+)",
     FATAL, "Host_Error",
     "the engine aborted with its own error message.",
     "the captured message names the subsystem; map/BSP problems are the usual "
     "cause with the HL2 bootstrap"),

    (r"signal 11|SIGSEGV|Segmentation fault|AddressSanitizer: SEGV",
     FATAL, "crash (SIGSEGV)",
     "the process died on an invalid memory access.",
     "run with `-condebug` and check l4d1_port/logs for the last subsystem "
     "printed; with the HL2 bootstrap this is usually a game-code assumption "
     "about L4D data (weapons/HUD)"),

    (r"Could not find gameinfo|gameinfo\.txt.*(?:not found|missing)|Unable to find gameinfo",
     FATAL, "gameinfo.txt not found",
     "the engine could not resolve -game <name>/gameinfo.txt.",
     "l4d1ctl init  # and launch from the parent of the game folder"),

    (r"Couldn't exec (\S+)",
     WARN, "config not executed",
     "a +exec config was missing or failed to parse.",
     "l4d1ctl launch --print  # the identity cfg is regenerated on launch"),

    (r"Attempted to create unknown entity type (\S+)",
     INFO, "unknown map entities",
     "map entities this game code does not implement.",
     "expected with the HL2 bootstrap - the map still loads"),

    (r"Cvar_Get: cvar already exists|cvar .* already exists",
     WARN, "duplicate cvar",
     "two parts of the game code registered the same console variable.",
     "harmless for a single launch; it means the module and the engine "
     "disagree about who owns the cvar"),

    (r"bink|Bink|\.bik",
     WARN, "Bink video unsupported",
     "the intro/splash video cannot play.",
     "expected: -novid skips it; Bink playback is still an open task"),

    (r"Unable to find (material|model|sound|texture)s? \"?([^\"\s]+)",
     INFO, "missing assets",
     "assets referenced by HL2 game code that L4D1 does not ship (weapons, HUD).",
     "expected with the HL2 bootstrap; they disappear as L4D game code replaces it"),

    (r"SteamAPI_Init|libsteam_api|Steam is not running",
     WARN, "Steam API unavailable",
     "the engine tried to reach Steam and failed.",
     "expected with -nosteam; the Steam replicator replaces this path"),

    (r"Could not find required OpenGL entry point|Unable to create (?:GL|openGL)|EGL_BAD|GL_INVALID",
     FATAL, "graphics initialisation failed",
     "the GL/EGL context could not be created.",
     "check the driver and -windowed; on Android this is usually a missing "
     "GLES3 context on the emulator"),

    (r"Memory is corrupt|corrupt memory|CrtIsValidHeapPointer",
     FATAL, "memory corruption",
     "the allocator detected damage.",
     "almost always an ABI mismatch between engine and game module; rebuild both "
     "from the same tree"),

    (r"Error parsing (?:VPK|.*\.vpk)|VPK.*(?:checksum|corrupt)|bad vpk",
     FATAL, "VPK read error",
     "a content pack is truncated or its index is damaged.",
     "re-verify the download (`l4d1ctl fetch --run` re-validates); "
     "`l4d1ctl vpk --verify-crc` shows which files are bad"),
)

_COMPILED_RULES = tuple((re.compile(pattern, re.IGNORECASE), level, title, detail, fix)
                        for pattern, level, title, detail, fix in _RULES)

_ENTITY_RE = re.compile(r"Attempted to create unknown entity type ([^\s!]+)")


def triage_log(text: str, *, exit_code: Optional[int] = None,
               log_path: Optional[Path] = None) -> Triage:
    """Turn engine output into findings, most severe first."""
    triage = Triage(lines=len(text.splitlines()), exit_code=exit_code, log_path=log_path)

    seen: Dict[str, Finding] = {}
    for index, line in enumerate(text.splitlines()):
        for pattern, level, title, detail, fix in _COMPILED_RULES:
            match = pattern.search(line)
            if not match:
                continue
            key = title
            sample = line.strip()[:160]
            existing = seen.get(key)
            if existing is None:
                seen[key] = Finding(level=level, title=title, detail=detail, fix=fix,
                                    count=1, sample=sample)
            else:
                seen[key] = Finding(level=existing.level, title=existing.title,
                                    detail=existing.detail, fix=existing.fix,
                                    count=existing.count + 1, sample=existing.sample)
            break  # one rule per line: the first match is the specific one

    for match in _ENTITY_RE.finditer(text):
        name = match.group(1)
        if name not in triage.unknown_entities:
            triage.unknown_entities.append(name)

    order = {FATAL: 0, WARN: 1, INFO: 2}
    triage.findings = sorted(seen.values(), key=lambda f: (order.get(f.level, 3), f.title))
    if not triage.findings and exit_code not in (None, 0):
        triage.findings.append(Finding(
            FATAL, f"engine exited {exit_code}",
            "no recognised error line; the log tail is below.",
            "look at the last 40 lines of the log for the last subsystem printed",
            sample=(text.strip().splitlines() or [""])[-1][:160],
        ))
    return triage


def find_latest_log(logs_dir: Path, *, pattern: str = "*.log") -> Optional[Path]:
    logs_dir = Path(logs_dir)
    if not logs_dir.is_dir():
        return None
    candidates = [p for p in logs_dir.glob(pattern) if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


__all__ = [
    "Check",
    "FAIL",
    "FATAL",
    "Finding",
    "INFO",
    "OK",
    "Report",
    "SearchPath",
    "Triage",
    "WARN",
    "find_latest_log",
    "host_bits",
    "host_machine",
    "interface_versions",
    "parse_search_paths",
    "resolve_engine",
    "run",
    "target_alias",
    "triage_log",
]
