"""Command line interface: ``l4d1ctl``.

Commands are intentionally small and composable so the Android launcher can
shell out to them (or reimplement the same calls in Java, using this as the
reference).

    l4d1ctl doctor
    l4d1ctl steam-libs
    l4d1ctl import --from-steam
    l4d1ctl fetch --provider steamcmd --user me --run
    l4d1ctl unpack Left4Dead.tar.gz
    l4d1ctl init
    l4d1ctl identity --avatar
    l4d1ctl mod list | enable <id> | disable <id> | order <id> <n> | add <path> | sync
    l4d1ctl mine client.dll       # what does the retail module declare?
    l4d1ctl check                 # will the launch work?
    l4d1ctl launch --map c1m1_hotel
    l4d1ctl launch --run --map c1m1_hotel   # run, capture, triage
    l4d1ctl selftest              # rehearse everything on a fake install
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

from . import (depot, doctor, gameinfo, gamemod, launch, mine, mods, preflight,
               selftest, steamid, steamlib, vpk)
from .paths import PortPaths, resolve_gamedir


def _paths(args: argparse.Namespace) -> PortPaths:
    return resolve_gamedir(getattr(args, "gamedir", None))


def _print_checks(report: depot.VerificationReport) -> None:
    for check in report.checks:
        print(f"  {check}")
    print(f"  -> {report.summary()}")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace) -> int:
    paths = _paths(args)
    results = doctor.run(paths, steam_root=args.steam_root)
    print(f"l4d1ctl doctor - game dir: {paths.gamedir}\n")
    for result in results:
        print(result.line())
    return doctor.exit_code(results)


def cmd_steam_libs(args: argparse.Namespace) -> int:
    root = steamlib.find_steam_root(args.steam_root)
    if root is None:
        print("no Steam install found; set L4D1_STEAM_ROOT to override", file=sys.stderr)
        return 2
    print(f"Steam root: {root}")
    for library in steamlib.library_folders(root):
        marker = " (root)" if library == root else ""
        print(f"  library: {library}{marker}")
    install = steamlib.discover_install(steam_root=str(root))
    if install:
        print(f"\nappid {install.appid}: {install.install_dir}")
        print(f"  complete: {install.looks_complete}  buildid: {install.buildid or 'n/a'}")
    else:
        print("\nappid 500 (Left 4 Dead) is not installed in any library")
    for user in steamlib.login_users(root):
        print(f"  account: {user.display_name} ({steamid.to_steam3(user.steamid64)})"
              f"{'  <- most recent' if user.most_recent else ''}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    paths = _paths(args)

    if args.from_steam:
        root = steamlib.find_steam_root(args.steam_root)
        if root is None:
            print("no Steam install found", file=sys.stderr)
            return 2
        install = steamlib.discover_install(steam_root=str(root))
        if install is None:
            print("Left 4 Dead is not installed in that Steam install", file=sys.stderr)
            return 2
        source = install.install_dir
    elif args.source:
        source = Path(args.source).expanduser()
        if not source.exists():
            print(f"source does not exist: {source}", file=sys.stderr)
            return 2
        # Accept either the app folder ("Left 4 Dead") or the content folder.
        source = depot.resolve_content_folder(source)
    else:
        print("pass --source <folder> or --from-steam", file=sys.stderr)
        return 2

    print(f"source: {source}")
    print(f"target: {paths.gamedir}")
    stats = depot.copy_game_content(
        source, paths.gamedir, link=args.hardlink, overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    print(f"  {stats.summary()}")
    if not args.dry_run:
        _print_checks(depot.verify_game_dir(paths.gamedir))
        if stats.copied + stats.linked == 0:
            print("\nnothing was imported. If you downloaded a single depot, it was "
                  "probably the binaries-only one (503): it holds bin/client.dll and "
                  "bin/server.dll, which mining wants but the game does not run on. "
                  "The game content lives in the content depots - add them, e.g. "
                  "--depot 501 --depot 502, and import again.",
                  file=sys.stderr)
            return 1
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    paths = _paths(args)
    target = paths.gamedir
    provider = args.provider

    if provider == "steamcmd":
        binary = args.tool or "steamcmd"
        command = depot.steamcmd_command(binary, target, args.user or "", appid=args.appid,
                                         validate=not args.no_validate, extra=args.extra,
                                         depots=args.depot)
    elif provider == "depotdownloader":
        binary = args.tool or "DepotDownloader"
        command = depot.depotdownloader_command(binary, target, args.user or "", appid=args.appid,
                                                validate=not args.no_validate, extra=args.extra,
                                                depots=args.depot, files=args.files_filter)
    else:
        print(f"unknown provider: {provider}", file=sys.stderr)
        return 2

    if not args.user:
        print("pass --user <steam account name>", file=sys.stderr)
        return 2

    print(f"provider: {command.provider}")
    for note in command.notes:
        print(f"  note: {note}")
    print(f"  {command.shell()}")

    if not args.run:
        print("\n(dry run: pass --run to execute; credentials are typed into the provider)")
        return 0
    if not args.yes:
        answer = input(f"Run the command above? It will write into {target} [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            return 1

    target.mkdir(parents=True, exist_ok=True)
    if args.files_filter and provider == "depotdownloader":
        filelist = target / "filelist.txt"
        filelist.write_text(depot.filelist_text(args.files_filter), encoding="utf-8")
        command.argv.extend(["-filelist", str(filelist)])
        print(f"  wrote {filelist} ({len(args.files_filter)} pattern(s))")
    code = depot.run_provider(command, execute=True)
    print(f"provider exited with {code}")
    if code == 0:
        _print_checks(depot.verify_game_dir(target))
    return code


def cmd_unpack(args: argparse.Namespace) -> int:
    paths = _paths(args)
    archive = Path(args.archive).expanduser()
    stats = depot.extract_archive(archive, paths.gamedir, dry_run=args.dry_run)
    print(f"  {stats.summary()}")
    if not args.dry_run:
        _print_checks(depot.verify_game_dir(paths.gamedir))
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser()
    if (source / "left4dead" / "gameinfo.txt").is_file():
        source = source / "left4dead"
    out = depot.make_export_archive(source, Path(args.output).expanduser(), dry_run=args.dry_run)
    print(f"wrote {out}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    paths = _paths(args)
    paths.gamedir.mkdir(parents=True, exist_ok=True)
    result = gameinfo.write(paths, force=args.force, dry_run=args.dry_run)
    if not result.written:
        if "error" in result.reason:
            print(result.reason["error"], file=sys.stderr)
            return 1
        print(f"gameinfo.txt unchanged ({result.path})")
    else:
        print(f"wrote {result.path}")

    if args.with_mods:
        (paths.mods_dir / "enabled").mkdir(parents=True, exist_ok=True)
        print(f"created {paths.mods_dir}")
    return 0


def cmd_identity(args: argparse.Namespace) -> int:
    paths = _paths(args)
    root = steamlib.find_steam_root(args.steam_root)
    identity = steamlib.resolve_identity(
        root,
        steamid64=steamid.try_parse(args.steamid),
        persona_name=args.name,
    )
    if identity is None:
        print("could not resolve an identity: no local Steam account and no --steamid/--name",
              file=sys.stderr)
        return 2

    # The identity itself is always written; an avatar failure must not throw
    # away a perfectly usable offline name and SteamID.
    avatar_error: Optional[str] = None
    if args.avatar:
        existing = paths.avatar_path(identity.steamid64)
        if existing.is_file():
            identity.avatar_path = str(existing)
        else:
            api_key = args.api_key or os.environ.get("STEAM_WEBAPI_KEY")
            try:
                avatar = steamlib.download_avatar(identity.steamid64, existing, api_key=api_key)
                identity.avatar_path = str(avatar)
                print(f"avatar: {avatar}")
            except steamlib.SteamWebApiError as exc:
                avatar_error = str(exc)
                print(f"avatar: {exc}", file=sys.stderr)

    paths.ensure_state()
    identity.write(paths.identity_json)
    launch.write_identity_cfg(paths, identity)
    print(f"{identity.display_name}  {identity.steamid64}  ({steamid.to_steam2(identity.steamid64)})")
    print(f"written: {paths.identity_json}")
    if avatar_error and not args.offline_ok:
        return 1
    return 0


def cmd_mod(args: argparse.Namespace) -> int:
    paths = _paths(args)
    manager = mods.ModManager(paths, link_mode=args.link_mode)
    action = args.action
    target = args.target

    if action == "list":
        known = manager.scan()
        if not known:
            print("no mods installed")
        for mod in known:
            state = "on " if mod.enabled else "off"
            print(f"[{state}] {mod.mod_id:<24} order={mod.order:<4} kind={mod.resolved_kind:<6} {mod.name}")
        return 0

    if action in ("enable", "disable", "order", "remove") and not target:
        print(f"`mod {action}` needs a mod id", file=sys.stderr)
        return 2
    if action == "add" and not target:
        print("`mod add` needs a path to a folder or *_dir.vpk", file=sys.stderr)
        return 2

    if action == "enable":
        manager.set_enabled(target, True)
        print(f"enabled {target}")
    elif action == "disable":
        manager.set_enabled(target, False)
        print(f"disabled {target}")
    elif action == "order":
        if args.value is None:
            print("order needs a numeric value", file=sys.stderr)
            return 2
        manager.set_order(target, int(args.value))
        print(f"{target} order={int(args.value)}")
    elif action == "add":
        mod = manager.add(Path(target).expanduser(), mod_id=args.mod_id, name=args.name,
                          order=args.order, enabled=not args.disabled)
        print(f"added {mod.mod_id} ({mod.resolved_kind})")
    elif action == "remove":
        manager.remove(target, delete_files=args.purge)
        print(f"removed {target}")
    elif action == "validate":
        issues = manager.validate()
        if not issues:
            print("no issues")
        for issue in issues:
            print(f"  {issue}")
        return 2 if any(i.level == "fail" for i in issues) else 0
    elif action == "sync":
        report = manager.sync(dry_run=args.dry_run)
        print(f"  {report.summary()}")
        for name in report.added:
            print(f"  + {name}")
        for name in report.removed:
            print(f"  - {name}")
        for issue in report.issues:
            print(f"  {issue}")
        return 2 if any(i.level == "fail" for i in report.issues) else 0
    else:  # pragma: no cover - argparse restricts choices
        print(f"unknown action {action}", file=sys.stderr)
        return 2

    if action in ("enable", "disable", "order", "add", "remove") and not args.no_sync:
        report = manager.sync()
        print(f"staged: {report.summary()}")
        for issue in report.issues:
            print(f"  {issue}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Report what a binary actually is: format, architecture, interfaces.

    Answers "what did the build machine just produce?" - run it on an artifact
    before installing anything on the device.
    """
    payload = []
    status = 0
    for name in args.files:
        path = Path(name).expanduser()
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            status = 1
            continue
        info = gamemod.inspect_binary(path)
        interfaces = preflight.interface_versions(path)
        payload.append({
            "path": str(path),
            "size": path.stat().st_size,
            "format": info.format,
            "bits": info.bits,
            "machine": info.machine,
            "kind": info.kind,
            "interfaces": interfaces,
        })
        if args.json:
            continue
        extra = ""
        if interfaces:
            extra = "  " + ", ".join(
                f"{key}{version}" for key, versions in sorted(interfaces.items())
                for version in versions
            )
        print(f"{path.name:<20} {info.summary}{extra}")
        if info.format == "unknown":
            print("  note: no ELF/PE/Mach-O header - a text file or a stub?")
    if args.json:
        print(json.dumps({"files": payload}, indent=2))
    return status


def cmd_module(args: argparse.Namespace) -> int:
    paths = _paths(args)

    if args.action == "inspect":  # pragma: no cover - kept for internal callers
        payload = []
        status = 0
        for name in args.files:
            path = Path(name).expanduser()
            if not path.is_file():
                print(f"no such file: {path}", file=sys.stderr)
                status = 1
                continue
            info = gamemod.inspect_binary(path)
            interfaces = preflight.interface_versions(path)
            entry = {
                "path": str(path),
                "size": path.stat().st_size,
                "format": info.format,
                "bits": info.bits,
                "machine": info.machine,
                "kind": info.kind,
                "interfaces": interfaces,
            }
            payload.append(entry)
            if not args.json:
                extra = ""
                if interfaces:
                    extra = "  " + ", ".join(
                        f"{key}{version}" for key, versions in sorted(interfaces.items())
                        for version in versions
                    )
                print(f"{path.name:<20} {info.summary}{extra}")
                if info.format == "unknown":
                    print("  note: no ELF/PE/Mach-O header - a text file or a stub?")
        if args.json:
            print(json.dumps({"files": payload}, indent=2))
        return status

    if args.action == "status":
        module_status = gamemod.status(paths)
        if args.json:
            print(json.dumps(module_status.to_dict(), indent=2))
            return 0 if module_status.ok else 1
        print(f"module dir: {module_status.bindir}")
        if not module_status.modules:
            print("  (nothing installed)")
        for name, module in sorted(module_status.modules.items()):
            link = f" -> {module.link_target}" if module.link_target else ""
            print(f"  {name:<8} {module.path.name:<18} {module.size / 1024:9.1f} KiB  "
                  f"{module.info.summary}{link}")
        for missing in module_status.missing:
            print(f"  missing: {missing}")
        for warning in module_status.warnings:
            print(f"  note: {warning}")
        return 0 if module_status.ok else 1

    if args.action == "install":
        args.source = args.source_opt or getattr(args, "source", None)
        if not args.source:
            print("install needs --from <dir with client/server modules>", file=sys.stderr)
            return 2
        source = Path(args.source).expanduser()
        if not source.is_dir():
            print(f"module source does not exist: {source}", file=sys.stderr)
            return 2

        if args.dry_run:
            print(f"source: {source}")
            for line in gamemod.describe_source(source):
                print(f"  {line}")
            print(f"target: {paths.bindir}")

        result = gamemod.install_modules(
            source, paths, link=args.link, force=args.force,
            install_all=args.all, dry_run=args.dry_run,
        )
        for name in result.installed:
            print(("would install " if args.dry_run else "installed ") + name)
        for name in result.unchanged:
            print(f"already in place {name}")
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(result.summary())
        return 0 if not any("will not start" in w for w in result.warnings) else 1

    if args.action == "remove":
        removed = gamemod.remove_modules(paths, dry_run=args.dry_run, names=args.names)
        for name in removed:
            print(("would remove " if args.dry_run else "removed ") + name)
        if not removed:
            print("nothing to remove")
        return 0

    print(f"unknown action {args.action}", file=sys.stderr)
    return 2


def cmd_vpk(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser()
    if not target.exists():
        print(f"no such file: {target}", file=sys.stderr)
        return 2

    if args.action in ("info", "list", "check"):
        dir_vpk = vpk.find_dir_vpk(target)
        if dir_vpk is None:
            print(f"no *_dir.vpk in {target}", file=sys.stderr)
            return 2
        try:
            archive = vpk.VPK(dir_vpk)
        except vpk.VPKError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if args.action == "info":
            payload = vpk.summarise(dir_vpk)
            print(f"path     : {payload['path']}")
            print(f"version  : {payload['version']}")
            print(f"entries  : {payload['entries']}")
            print(f"payload  : {payload['payload_bytes'] / (1024 * 1024):.2f} MiB")
            print(f"parts    : {', '.join(payload['parts']) or '(none, data in dir file)'}")
            for problem in payload["problems"]:
                print(f"problem  : {problem}")
            return 0 if not payload["problems"] else 1

        if args.action == "list":
            entries = sorted(archive, key=lambda e: e.path)
            shown = 0
            for entry in entries:
                if args.filter and args.filter.lower() not in entry.path.lower():
                    continue
                print(f"{entry.size:>10}  {entry.path}")
                shown += 1
                if args.limit and shown >= args.limit:
                    print("... (use --limit 0 for everything)")
                    break
            print(f"\n{shown} of {len(entries)} entries")
            return 0

        # check
        problems = archive.verify()
        for name in args.verify_crc[: args.max_crc]:
            if not archive.crc_matches(name):
                problems.append(f"crc mismatch: {name}")
        if problems:
            for problem in problems:
                print(f"[FAIL] {problem}")
            return 2
        print(f"[PASS] {dir_vpk.name}: {len(archive)} entries, parts present")
        return 0

    if args.action == "extract":
        if not args.output:
            print("extract needs --output <dir>", file=sys.stderr)
            return 2
        dir_vpk = vpk.find_dir_vpk(target)
        if dir_vpk is None:
            print(f"no *_dir.vpk in {target}", file=sys.stderr)
            return 2
        archive = vpk.VPK(dir_vpk)
        written = archive.extract_all(Path(args.output).expanduser(), overwrite=args.force)
        print(f"extracted {written} file(s) to {args.output}")
        return 0

    print(f"unknown action {args.action}", file=sys.stderr)
    return 2


def _log_path(paths: PortPaths) -> Path:
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return paths.logs_dir / f"launch-{stamp}.log"


def cmd_mine(args: argparse.Namespace) -> int:
    """Inventory a shipped module: interfaces, netprops, convars, entities.

    Thin wrapper: the implementation lives in ``mine.main`` so that
    ``python3 mine.py client.dll`` works as a single downloaded file, with no
    package and no clone.
    """
    argv: List[str] = list(args.files)
    if args.json:
        argv.append("--json")
    if args.bucket:
        argv.extend(["--bucket", args.bucket])
    if args.top != 40:
        argv.extend(["--top", str(args.top)])
    if args.min_length != 4:
        argv.extend(["--min-length", str(args.min_length)])
    if args.compare:
        argv.extend(["--compare", args.compare])
    return mine.main(argv)


def cmd_check(args: argparse.Namespace) -> int:
    """Answer "will this launch work?" without starting anything."""
    paths = _paths(args)
    plan = launch.build_launch_plan(
        paths,
        engine=args.engine,
        map_name=args.map,
        extra_args=tuple(args.extra),
        write_cfg=False,
        dry_run=True,
    )
    report = preflight.run(paths, engine=str(plan.argv[0]), argv=plan.argv)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render())
        print(f"  verdict: {report.verdict}")
        if report.blocking and not args.json:
            print("  fix the blocking items above, then re-run `l4d1ctl check`")
    return 0 if report.ok else 1


def cmd_selftest(args: argparse.Namespace) -> int:
    """Rehearse every pipeline on a synthetic install (no Steam, no content)."""
    keep = Path(args.keep).expanduser() if args.keep else None
    report = selftest.run(keep, stages=list(args.stage))
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render())
        if not report.ok:
            print("  re-run with L4D1_SELFTEST_TRACEBACK=1 for full tracebacks")
    return 0 if report.ok else 1


def cmd_launch(args: argparse.Namespace) -> int:
    paths = _paths(args)
    identity = steamlib.Identity.from_file(paths.identity_json)
    plan = launch.build_launch_plan(
        paths,
        engine=args.engine,
        identity=identity,
        map_name=args.map,
        extra_args=tuple(args.extra),
        fullscreen=not args.windowed,
        write_cfg=not args.print_only,
        dry_run=args.print_only,
    )
    for warning in plan.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if args.print_only:
        print(plan.command())
        if args.env:
            for key, value in plan.env.items():
                print(f"export {key}={value!r}")
        return 0

    if not args.no_check:
        report = preflight.run(paths, engine=str(plan.argv[0]), argv=plan.argv)
        print(report.render())
        if not report.ok and not args.force:
            print("error: refusing to launch (use --force to try anyway)", file=sys.stderr)
            return 1

    if not args.run:
        print(f"exec: {plan.command()}")
        try:
            os.execvpe(plan.argv[0], plan.argv, {**os.environ, **plan.env})
        except OSError as exc:
            print(f"failed to start engine: {exc}", file=sys.stderr)
            return 1

    return _run_captured(plan, paths, timeout=args.timeout)


def _run_captured(plan: launch.LaunchPlan, paths: PortPaths, *, timeout: Optional[float]) -> int:
    """Run the engine, capture its output, then explain any failure."""
    log_path = _log_path(paths)
    print(f"exec: {plan.command()}")
    print(f"log : {log_path}")
    started = time.time()
    try:
        completed = subprocess.run(
            plan.argv,
            env={**os.environ, **plan.env},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        output = completed.stdout or ""
        code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        code = 124
        print(f"error: engine still running after {timeout}s, stopped it", file=sys.stderr)
    except OSError as exc:
        print(f"failed to start engine: {exc}", file=sys.stderr)
        return 1

    log_path.write_text(output, encoding="utf-8", errors="replace")
    elapsed = time.time() - started

    # Show the tail only: startup spew is thousands of lines and the answer is
    # always near the end.
    tail = output.splitlines()[-40:]
    if tail:
        print("--- engine output (tail) ---")
        for line in tail:
            print(f"  {line}")
        print("--- end ---")

    triage = preflight.triage_log(output, exit_code=code, log_path=log_path)
    print(triage.render())
    print(f"  exited {code} after {elapsed:.1f}s")
    if triage.crashed:
        return 1
    return code


def cmd_status(args: argparse.Namespace) -> int:
    paths = _paths(args)
    report = depot.verify_game_dir(paths.gamedir)
    manager = mods.ModManager(paths)
    identity = steamlib.Identity.from_file(paths.identity_json)
    payload = {
        "gamedir": str(paths.gamedir),
        "content_ok": report.ok,
        "files": report.files,
        "bytes": report.bytes,
        "gameinfo": paths.gameinfo.is_file(),
        "gameinfo_generated": gameinfo.is_generated(paths.gameinfo),
        "mods": [
            {"id": m.mod_id, "name": m.name, "enabled": m.enabled, "order": m.order,
             "kind": m.resolved_kind}
            for m in manager.scan()
        ],
        "identity": None if identity is None else {
            "steamid64": identity.steamid64,
            "name": identity.display_name,
            "avatar": identity.avatar_path,
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"game dir      : {payload['gamedir']}")
        print(f"content       : {'ok' if payload['content_ok'] else 'incomplete'} "
              f"({payload['files']} files, {payload['bytes'] / (1024 * 1024):.1f} MiB)")
        print(f"gameinfo      : {'generated' if payload['gameinfo_generated'] else ('present' if payload['gameinfo'] else 'missing')}")
        print(f"identity      : {identity.display_name if identity else 'not set'}")
        mods_list = payload["mods"]
        print(f"mods          : {len(mods_list)} installed, {sum(1 for m in mods_list if m['enabled'])} enabled")
    return 0 if report.ok else 1


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="l4d1ctl",
        description="Left 4 Dead launcher tooling: content fetching, mods, identity, launch.",
    )
    parser.add_argument("--gamedir", help="game directory (default: $L4D1_GAMEDIR or <repo>/game/left4dead)")
    parser.add_argument("--steam-root", help="Steam installation to read (default: auto-detect)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check the environment and content")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("steam-libs", help="list Steam libraries, installs and local accounts")
    p.set_defaults(func=cmd_steam_libs)

    p = sub.add_parser("import", help="copy content from an existing install")
    p.add_argument("--source", help="folder with left4dead/ (or the left4dead folder itself)")
    p.add_argument("--from-steam", action="store_true", help="use the Steam install found on this machine")
    p.add_argument("--hardlink", action="store_true", help="hardlink instead of copy (same filesystem)")
    p.add_argument("--overwrite", action="store_true", help="replace existing files")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("fetch", help="drive steamcmd / DepotDownloader for appid 500")
    p.add_argument("--provider", default="steamcmd", choices=("steamcmd", "depotdownloader"))
    p.add_argument("--user", help="Steam account name (password is entered in the provider)")
    p.add_argument("--tool", help="path to steamcmd / DepotDownloader")
    p.add_argument("--appid", type=int, default=depot.APPID)
    p.add_argument("--no-validate", action="store_true")
    p.add_argument("--extra", nargs=argparse.REMAINDER, default=[], help="extra provider arguments")
    p.add_argument("--depot", type=int, action="append", default=[],
                   help="only this depot (repeatable; depotdownloader only). For the shipped "
                        "modules, 503 carries bin/client.dll and bin/server.dll")
    p.add_argument("--file", action="append", default=[], dest="files_filter",
                   help="only paths matching this regex (repeatable; depotdownloader only)")
    p.add_argument("--run", action="store_true", help="actually execute (otherwise dry run)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("unpack", help="extract a content archive made on another machine")
    p.add_argument("archive")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("pack", help="make a transfer archive from a local install")
    p.add_argument("--source", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("init", help="write the generated gameinfo.txt")
    p.add_argument("--force", action="store_true", help="replace a hand-written gameinfo.txt")
    p.add_argument("--with-mods", action="store_true", help="also create the mods/ folder")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("identity", help="resolve the offline player identity")
    p.add_argument("--steamid", help="override SteamID64 / STEAM_X:Y:Z / [U:1:n]")
    p.add_argument("--name", help="override the persona name")
    p.add_argument("--avatar", action="store_true", help="download the avatar (needs a Web API key)")
    p.add_argument("--api-key", help="Steam Web API key (or set STEAM_WEBAPI_KEY)")
    p.add_argument("--offline-ok", action="store_true", help="succeed even if the avatar fails")
    p.set_defaults(func=cmd_identity)

    p = sub.add_parser("mod", help="manage mods")
    p.add_argument("action", choices=("list", "enable", "disable", "order", "add", "remove",
                                      "validate", "sync"))
    p.add_argument("target", nargs="?",
                   help="mod id, or a folder / *_dir.vpk for `add`")
    p.add_argument("value", nargs="?", help="value for `order`")
    p.add_argument("--id", dest="mod_id", help="id to use for `add` (default: source name)")
    p.add_argument("--name", help="display name for `add`")
    p.add_argument("--link-mode", default="hardlink", choices=("hardlink", "copy", "symlink"))
    p.add_argument("--order", type=int, default=mods.DEFAULT_ORDER)
    p.add_argument("--disabled", action="store_true", help="add the mod without enabling it")
    p.add_argument("--purge", action="store_true", help="delete the mod's files on `remove`")
    p.add_argument("--no-sync", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_mod)

    p = sub.add_parser("module", help="install / inspect the game module (client + server)")
    p.add_argument("action", choices=("status", "install", "remove"))
    p.add_argument("--from", dest="source_opt", help="build output dir, e.g. game/hl2/bin")
    p.add_argument("--link", action="store_true", help="hardlink instead of copy")
    p.add_argument("--force", action="store_true", help="replace existing module files")
    p.add_argument("--all", action="store_true", help="also copy non-module binaries")
    p.add_argument("--names", nargs="*", help="module names to remove (default: all)")
    p.add_argument("--json", action="store_true", help="machine-readable `status`")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_module)

    p = sub.add_parser("vpk", help="inspect mod VPK packs")
    p.add_argument("action", choices=("info", "list", "check", "extract"))
    p.add_argument("path", help="*_dir.vpk or a folder containing one")
    p.add_argument("--filter", help="only show entries containing this substring")
    p.add_argument("--limit", type=int, default=200, help="0 = no limit")
    p.add_argument("--output", help="destination for `extract`")
    p.add_argument("--force", action="store_true", help="overwrite on `extract`")
    p.add_argument("--verify-crc", nargs="*", default=[], help="entries to CRC check")
    p.add_argument("--max-crc", type=int, default=20)
    p.set_defaults(func=cmd_vpk)

    p = sub.add_parser("inspect", help="identify ELF/PE files (format, arch, interfaces)")
    p.add_argument("files", nargs="+", help="binaries to inspect")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("mine", help="inventory a shipped client.dll/server.dll "
                                    "(interfaces, netprops, convars, entities)")
    p.add_argument("files", nargs="+", help="PE files to read")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--bucket", help="print one bucket only, one name per line")
    p.add_argument("--top", type=int, default=40,
                   help="names per bucket in the report (0 = all)")
    p.add_argument("--min-length", type=int, default=4,
                   help="shortest string to consider (default 4)")
    p.add_argument("--compare", help="show which names the other module adds")
    p.set_defaults(func=cmd_mine)

    p = sub.add_parser("check", help="will the launch work? (no launching, no Steam)")
    p.add_argument("--engine", default=launch.DEFAULT_ENGINE)
    p.add_argument("--map", help="also check the map argument, e.g. c1m1_hotel")
    p.add_argument("--extra", nargs=argparse.REMAINDER, default=[])
    p.add_argument("--json", action="store_true", help="machine-readable report (for the launcher UI)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("selftest", help="rehearse every pipeline on a synthetic install")
    p.add_argument("--keep", help="keep the synthetic install in this folder for inspection")
    p.add_argument("--stage", action="append", default=[],
                   help="only run this stage (repeatable)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("launch", help="build (or run) the engine command line")
    p.add_argument("--engine", default=launch.DEFAULT_ENGINE)
    p.add_argument("--map", help="map to load, e.g. c1m1_hotel")
    p.add_argument("--windowed", action="store_true")
    p.add_argument("--extra", nargs=argparse.REMAINDER, default=[])
    p.add_argument("--print", dest="print_only", action="store_true", help="print the command only")
    p.add_argument("--env", action="store_true", help="also print the environment")
    p.add_argument("--run", action="store_true",
                   help="run as a child process, capture the log, triage failures")
    p.add_argument("--timeout", type=float, default=None,
                   help="with --run: stop the engine after N seconds")
    p.add_argument("--no-check", action="store_true", help="skip the preflight checks")
    p.add_argument("--force", action="store_true", help="launch even if preflight says it will fail")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("status", help="machine-readable summary (used by the launcher UI)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    # Let `--gamedir` / `--steam-root` also be written after the subcommand.
    # SUPPRESS keeps the global value when the local one is absent.
    for child in sub.choices.values():
        child.add_argument("--gamedir", default=argparse.SUPPRESS,
                           help="game directory (same as the global option)")
        child.add_argument("--steam-root", default=argparse.SUPPRESS,
                           help="Steam installation to read (same as the global option)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:  # pragma: no cover
        parser.print_help()
        return 2
    try:
        return int(args.func(args) or 0)
    except (mods.ModError, steamlib.SteamLibraryError, steamlib.SteamWebApiError,
            FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
