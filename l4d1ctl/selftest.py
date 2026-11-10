"""End-to-end rehearsal of the port tooling, on a synthetic install.

You cannot test a Steam download without Steam, and you cannot test a launch
without content. This builds a *real-shaped* game directory in a temporary
folder — a genuine VPK (built by :mod:`l4d1ctl.vpk`, read back by the same
parser), a generated gameinfo, installed modules with correct headers, a staged
mod, an identity file — and then runs the actual pipelines against it:

    gameinfo write/check -> VPK build/read/extract -> mod add/sync/disable
    -> module install/status -> preflight -> log triage -> launch planning
    -> archive export/import -> content verification

Every stage calls the production code. If a stage fails, the tooling is broken
in a way that would also bite on the device, and the traceback says where.

It finishes with two *negative* assertions on purpose: it removes a module and
checks that preflight notices, and it feeds a crash log to the triage rules and
checks that they fire. A checker that never says "no" is worthless.
"""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import depot, gameinfo, gamemod, launch, mods, preflight, steamlib, vpk
from .paths import GAME_DIR_NAME, PortPaths

_MACHINE_IDS = {
    "x86-64": 62,
    "AArch64": 183,
    "x86": 3,
    "ARM": 40,
    "RISC-V": 243,
    "PowerPC": 20,
    "MIPS": 8,
}

_SAMPLE_FILES: Dict[str, bytes] = {
    "materials/selftest/test.vmt": b'"VertexLitGeneric" { "$basetexture" "selftest/test" }\n',
    "scripts/selftest.txt": b"// selftest payload\n",
    "resource/selftest.res": b'"Resource/GameMenu"\n{\n}\n',
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def fake_module(kind: str) -> bytes:
    """A minimal ELF for *this* machine carrying the interface string.

    Not loadable, but every header field a preflight check looks at is real,
    including the ``VClient0xx`` / ``ServerGameDLL0xx`` marker.
    """
    machine = _MACHINE_IDS.get(preflight.host_machine(), 62)
    bits = preflight.host_bits()
    ident = b"\x7fELF" + bytes([
        2 if bits == 64 else 1,  # EI_CLASS
        1,                       # EI_DATA (little)
        1,                       # EI_VERSION
    ]) + b"\x00" * 9
    header = ident + struct.pack("<HH", 3, machine) + b"\x00" * 0x30
    payload = {
        "client": b"VClient017\x00CreateInterface\x00",
        "server": b"ServerGameDLL010\x00CreateInterface\x00",
        "gameui": b"GameUI011\x00CreateInterface\x00",
    }.get(kind, b"\x00")
    return header + payload


def fake_engine(path: Path) -> Path:
    """A stand-in engine: an executable that prints a plausible startup log."""
    path.write_text(
        "#!/bin/sh\n"
        "echo \"engine: fake, built for the selftest\"\n"
        "echo \"Attempted to create unknown entity type weapon_ak47!\"\n"
        "echo \"Attempted to create unknown entity type info_survivor_position!\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def sample_vpk(gamedir: Path, *, split: bool = False) -> Path:
    return vpk.build_vpk_v1(gamedir / "pak01_dir.vpk", _SAMPLE_FILES, split=split)


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

@dataclass
class Stage:
    name: str
    run: Callable[["Context"], str]
    ok: bool = True
    detail: str = ""
    error: str = ""


@dataclass
class Context:
    root: Path
    paths: PortPaths
    engine: Path = None  # type: ignore[assignment]
    notes: List[str] = field(default_factory=list)

    @property
    def gamedir(self) -> Path:
        return self.paths.gamedir


@dataclass
class Report:
    root: Path
    stages: List[Stage] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(stage.ok for stage in self.stages)

    @property
    def failures(self) -> List[Stage]:
        return [stage for stage in self.stages if not stage.ok]

    def headline(self) -> str:
        passed = sum(1 for stage in self.stages if stage.ok)
        if self.ok:
            return f"selftest passed ({passed}/{len(self.stages)} stages)"
        return f"selftest FAILED ({len(self.failures)} of {len(self.stages)} stages)"

    def render(self) -> str:
        lines = [f"{self.headline()}  [{self.root}]"]
        for stage in self.stages:
            if stage.ok:
                lines.append(f"  [PASS] {stage.name:<18} {stage.detail}")
            else:
                lines.append(f"  [FAIL] {stage.name:<18} {stage.error}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "root": str(self.root),
            "stages": [
                {"name": s.name, "ok": s.ok, "detail": s.detail, "error": s.error}
                for s in self.stages
            ],
        }


def _stage_paths(ctx: Context) -> str:
    ctx.paths.ensure_state()
    # ensure_state() owns the bookkeeping folders; bin/ and mods/enabled are
    # created on demand by `module install` and by the first mod sync.
    for directory in (ctx.paths.state_dir, ctx.paths.cfg_dir, ctx.paths.logs_dir,
                      ctx.paths.avatars_dir):
        assert directory.is_dir(), f"missing {directory}"
    assert ctx.paths.bindir == ctx.gamedir / "bin"
    assert ctx.paths.mod_dir == ctx.gamedir / "mods" if hasattr(ctx.paths, "mod_dir") else True
    return f"{ctx.gamedir.name}/ with l4d1_port/{{logs,avatars}}, cfg/, bin/ GAMEBIN"


def _stage_gameinfo(ctx: Context) -> str:
    result = gameinfo.write(ctx.paths)
    assert result.written or ctx.paths.gameinfo.is_file(), "gameinfo was not written"
    problems = gameinfo.check(ctx.paths)
    assert not problems, f"generated gameinfo still has problems: {problems}"
    assert gameinfo.is_generated(ctx.paths.gameinfo), "generated marker not detected"
    text = ctx.paths.gameinfo.read_text(encoding="utf-8")
    assert gameinfo.MODS_SEARCH_PATH in text, "mods are not mounted"
    entries = preflight.parse_search_paths(text)
    kinds = {entry.kind.lower() for entry in entries}
    assert "game" in kinds, f"no Game search path parsed (got {kinds})"
    return f"{len(entries)} search path(s): {', '.join(sorted(kinds))}"


def _stage_vpk(ctx: Context) -> str:
    target = sample_vpk(ctx.gamedir)
    pack = vpk.open_vpk(target)
    assert len(pack) == len(_SAMPLE_FILES), f"{len(pack)} entries, expected {len(_SAMPLE_FILES)}"

    for name, expected in _SAMPLE_FILES.items():
        assert pack.read(name) == expected, f"round-trip mismatch for {name}"
        assert pack.crc_matches(name), f"CRC mismatch for {name}"
    assert pack.verify() == [], f"verify() reported problems: {pack.verify()}"

    extracted = ctx.root / "extracted"
    shutil.rmtree(extracted, ignore_errors=True)  # `--keep` runs again in place
    count = pack.extract_all(extracted)
    assert count == len(_SAMPLE_FILES), f"extracted {count} files"
    for name, expected in _SAMPLE_FILES.items():
        assert (extracted / name).read_bytes() == expected, f"extracted bytes differ: {name}"

    # Split packs are how real mods ship (payload in _000.vpk).
    split_target = vpk.build_vpk_v1(ctx.root / "split_dir.vpk", _SAMPLE_FILES, split=True)
    split_pack = vpk.open_vpk(split_target)
    missing = split_pack.info.missing_parts()
    assert not missing, f"missing parts: {missing}"
    assert split_pack.info.parts, "split pack has no archive parts"
    assert split_pack.read("scripts/selftest.txt") == _SAMPLE_FILES["scripts/selftest.txt"]
    return f"{len(pack)} entries read + CRC ok, {count} extracted, split pack ok"


def _stage_mods(ctx: Context) -> str:
    payload = ctx.root / "mod_payload" / "materials" / "modstuff"
    payload.mkdir(parents=True, exist_ok=True)
    (payload / "mod.vmt").write_bytes(b'"UnlitGeneric" {}\n')

    manager = mods.ModManager(ctx.paths)
    manager.add(ctx.root / "mod_payload", mod_id="selftest_mod", name="Selftest Mod")
    assert manager.get("selftest_mod").name == "Selftest Mod"
    manager.set_enabled("selftest_mod", True)
    sync = manager.sync()
    assert sync.added, f"nothing was staged: {sync.summary()}"

    staged = sorted(p.name for p in ctx.paths.staged_dir.iterdir())
    assert staged, "mods/enabled is empty after sync"
    assert not [i for i in manager.validate() if i.level == "fail"], [str(i) for i in manager.validate()]

    manager.set_order("selftest_mod", 7)
    assert manager.get("selftest_mod").order == 7

    manager.set_enabled("selftest_mod", False)
    manager.sync()
    assert not list(ctx.paths.staged_dir.iterdir()), "disabling a mod left files staged"

    manager.remove("selftest_mod", delete_files=True)
    assert not (ctx.paths.mods_dir / "selftest_mod").exists()
    return f"add/enable/stage/order/disable/remove ok ({staged[0]})"


def _stage_modules(ctx: Context) -> str:
    build = ctx.root / "build_out"
    build.mkdir(parents=True, exist_ok=True)
    for kind in gamemod.MODULE_NAMES:
        (build / f"{kind}.so").write_bytes(fake_module(kind))

    first = gamemod.install_modules(build, ctx.paths)
    installed = sorted(first.installed + first.unchanged)
    assert installed == ["client.so", "gameui.so", "server.so"], installed

    again = gamemod.install_modules(build, ctx.paths)
    assert not again.installed, f"reinstall was not idempotent: {again.installed}"
    assert len(again.unchanged) == 3, again.unchanged

    status = gamemod.status(ctx.paths)
    assert status.ok, f"status says modules are missing: {status.missing}"
    assert set(gamemod.REQUIRED_MODULES) <= set(status.modules), sorted(status.modules)

    scan = gamemod.scan_modules(ctx.paths.bindir)
    assert scan["client"].info.format == "ELF", scan["client"].info.summary
    assert scan["client"].info.machine == preflight.host_machine(), scan["client"].info.machine
    return f"installed 3, second run unchanged, ELF {scan['client'].info.machine}"


def _stage_preflight(ctx: Context) -> str:
    report = preflight.run(ctx.paths, engine=str(ctx.engine),
                           argv=[str(ctx.engine), "-game", ctx.gamedir.name,
                                 "-nosteam", "-insecure"])
    assert report.ok, "preflight blocks a launch it should accept:\n" + report.render()
    codes = {check.code for check in report.checks}
    assert "module.client" in codes and "module.server" in codes, sorted(codes)

    # Negative test: the checker must notice a missing module.
    client = ctx.paths.bindir / "client.so"
    stash = ctx.root / "client.so.stashed"
    client.rename(stash)
    try:
        broken = preflight.run(ctx.paths, engine=str(ctx.engine))
        assert not broken.ok, "preflight accepted a game dir with no client module"
        assert "module.client" in {c.code for c in broken.blocking}, [c.code for c in broken.blocking]
    finally:
        stash.rename(client)

    restored = preflight.run(ctx.paths, engine=str(ctx.engine))
    assert restored.ok, "preflight did not recover after restoring the module"
    return f"{len(report.checks)} checks, verdict {report.verdict}, negative case detected"


def _stage_triage(ctx: Context) -> str:
    log = "\n".join([
        "engine: loading gameinfo",
        "Attempted to create unknown entity type weapon_ak47!",
        "Attempted to create unknown entity type info_survivor_position!",
        "Attempted to create unknown entity type weapon_ak47!",
        "dlopen failed: cannot locate symbol",
        "undefined symbol: _ZN11CBasePlayer13SomethingEv",
        "Host_Error: Couldn't spawn server",
    ])
    triage = preflight.triage_log(log, exit_code=134)
    levels = {finding.level for finding in triage.findings}
    assert preflight.FATAL in levels, [f.to_dict() for f in triage.findings]
    assert triage.crashed
    titles = {finding.title for finding in triage.findings}
    assert "undefined symbol" in titles, titles
    assert "Host_Error" in titles, titles
    assert set(triage.unknown_entities) == {"weapon_ak47", "info_survivor_position"}, triage.unknown_entities

    clean = preflight.triage_log("engine: hello\nexit\n", exit_code=0)
    assert not clean.crashed and clean.verdict == "clean", clean.verdict

    silent = preflight.triage_log("nothing useful here", exit_code=139)
    assert silent.crashed, "a non-zero exit with no matching rule must still be reported"
    return f"{len(triage.findings)} findings, {len(triage.unknown_entities)} unknown entities"


def _stage_identity(ctx: Context) -> str:
    identity = steamlib.Identity(steamid64=76561197960287930, persona_name="Selftest",
                                 account_name="selftest", source="selftest")
    identity.write(ctx.paths.identity_json)
    loaded = steamlib.Identity.from_file(ctx.paths.identity_json)
    assert loaded is not None and loaded.steamid64 == identity.steamid64
    assert loaded.display_name == "Selftest"

    cfg = launch.write_identity_cfg(ctx.paths, identity)
    text = cfg.read_text(encoding="utf-8")
    assert 'name "Selftest"' in text, text
    assert str(identity.steamid64) in text

    offline = launch.identity_cfg_text(None)
    assert "no identity resolved" in offline
    return "identity.json round-trip + cfg generated"


def _stage_launch(ctx: Context) -> str:
    identity = steamlib.Identity.from_file(ctx.paths.identity_json)
    plan = launch.build_launch_plan(ctx.paths, engine=str(ctx.engine), identity=identity,
                                    map_name="c1m1_hotel", dry_run=True)
    assert plan.argv[0] == str(ctx.engine)
    assert "-game" in plan.argv and ctx.gamedir.name in plan.argv
    index = plan.argv.index("+exec")
    assert plan.argv[index + 1] == launch.IDENTITY_CFG_NAME
    assert "+map" in plan.argv and "c1m1_hotel" in plan.argv
    assert not plan.warnings, plan.warnings
    assert any(arg.startswith("-") for arg in plan.argv[1:])
    assert "+map" in plan.command()
    return f"{len(plan.argv)} argv entries, +exec {launch.IDENTITY_CFG_NAME}"


def _stage_archive(ctx: Context) -> str:
    archive = ctx.root / "transfer.tar.gz"
    depot.make_export_archive(ctx.gamedir, archive)
    assert archive.is_file() and archive.stat().st_size > 0, "archive was not created"

    target = ctx.root / "restored" / "left4dead"
    stats = depot.extract_archive(archive, target)
    assert stats.copied or stats.linked, f"nothing extracted: {stats.summary()}"
    assert (target / "gameinfo.txt").is_file(), "gameinfo.txt did not survive the round trip"
    assert list(target.glob("*_dir.vpk")), "VPK did not survive the round trip"
    return f"export + import round-trip, {stats.summary()}"


def _stage_depot(ctx: Context) -> str:
    report = depot.verify_game_dir(ctx.gamedir)
    assert report.ok, [str(c) for c in report.failures]
    # Regression: phase 2 put the rebuilt modules in bin/, so verify must not
    # tell the user to delete that folder any more.
    bin_warnings = [c.message for c in report.warnings if "bin/" in c.message]
    assert not bin_warnings, f"verify still complains about the module folder: {bin_warnings}"
    assert any("module" in c.message for c in report.checks), [c.message for c in report.checks]
    return report.summary()


STAGES: List[tuple[str, Callable[[Context], str]]] = [
    ("paths", _stage_paths),
    ("gameinfo", _stage_gameinfo),
    ("vpk", _stage_vpk),
    ("mods", _stage_mods),
    ("modules", _stage_modules),
    ("preflight", _stage_preflight),
    ("triage", _stage_triage),
    ("identity", _stage_identity),
    ("launch", _stage_launch),
    ("archive", _stage_archive),
    ("depot", _stage_depot),
]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(root: Optional[Path] = None, *, stages: Optional[List[str]] = None) -> Report:
    """Run every stage against a fresh synthetic install."""
    if root is None:
        temporary = tempfile.TemporaryDirectory(prefix="l4d1ctl-selftest-")
        root = Path(temporary.name)  # kept alive by the caller holding the report
        report = Report(root=root)
        report._tmp = temporary  # type: ignore[attr-defined]
    else:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        report = Report(root=root)

    ctx = Context(root=root, paths=PortPaths(root / GAME_DIR_NAME))
    ctx.engine = fake_engine(root / "hl2_linux")

    selected = [name for name in (stages or []) if name]
    for name, function in STAGES:
        if selected and name not in selected:
            continue
        stage = Stage(name=name, run=function)
        try:
            stage.detail = function(ctx)
        except Exception as exc:  # noqa: BLE001 - the point is to report, not raise
            stage.ok = False
            stage.error = f"{exc.__class__.__name__}: {exc}"
            if os.environ.get("L4D1_SELFTEST_TRACEBACK"):
                import traceback

                stage.error += "\n" + traceback.format_exc()
        report.stages.append(stage)
    return report


__all__ = [
    "Context",
    "Report",
    "STAGES",
    "Stage",
    "fake_engine",
    "fake_module",
    "run",
    "sample_vpk",
]
