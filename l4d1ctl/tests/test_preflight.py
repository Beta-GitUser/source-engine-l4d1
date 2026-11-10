import json
import os
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from l4d1ctl import cli, gameinfo, gamemod, preflight, steamlib
from l4d1ctl.paths import PortPaths


def run(argv):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def elf_with(strings=b"", *, machine=None, bits=None) -> bytes:
    ident = b"\x7fELF" + bytes([
        2 if (bits or preflight.host_bits()) == 64 else 1, 1, 1,
    ]) + b"\x00" * 9
    machine_id = machine if machine is not None else {
        "x86-64": 62, "AArch64": 183, "x86": 3, "ARM": 40,
    }.get(preflight.host_machine(), 62)
    return ident + struct.pack("<HH", 3, machine_id) + b"\x00" * 0x30 + strings


def make_install(tmp: Path, *, modules=True, gameinfo_text=None, engine=None) -> PortPaths:
    gamedir = tmp / "left4dead"
    gamedir.mkdir(parents=True, exist_ok=True)
    paths = PortPaths(gamedir)
    paths.ensure_state()
    paths.mods_dir.mkdir(parents=True, exist_ok=True)

    text = gameinfo_text if gameinfo_text is not None else gameinfo.render()
    paths.gameinfo.write_text(text, encoding="utf-8")

    from l4d1ctl import vpk

    vpk.build_vpk_v1(gamedir / "pak01_dir.vpk", {"scripts/test.txt": b"hi"})

    if modules:
        (paths.bindir).mkdir(parents=True, exist_ok=True)
        (paths.bindir / "client.so").write_bytes(elf_with(b"VClient017\x00"))
        (paths.bindir / "server.so").write_bytes(elf_with(b"ServerGameDLL010\x00"))
    return paths


def fake_engine(directory: Path) -> Path:
    engine = directory / "hl2_linux"
    engine.write_bytes(b"#!/bin/sh\nexit 0\n")
    engine.chmod(0o755)
    return engine


def codes(report):
    return {check.code for check in report.checks}


class SearchPathParsingTests(unittest.TestCase):
    def test_generated_gameinfo_is_parsed(self):
        entries = preflight.parse_search_paths(gameinfo.render())
        kinds = [entry.kind.lower() for entry in entries]
        self.assertIn("game", kinds)
        self.assertIn("platform", kinds)

    def test_quoted_valve_style_is_parsed(self):
        text = (
            '"GameInfo"\n{\n\tFileSystem\n\t{\n\t\t"SearchPaths"\n\t\t{\n'
            '\t\t\t"Game"\t\t"|gameinfo_path|."\n'
            '\t\t\t"GameBin"\t\t"|gameinfo_path|bin"\n'
            "\t\t}\n\t}\n}\n"
        )
        entries = preflight.parse_search_paths(text)
        self.assertEqual([entry.kind for entry in entries], ["Game", "GameBin"])
        self.assertEqual(entries[0].raw.strip('"'), "|gameinfo_path|.")

    def test_missing_block_returns_empty(self):
        self.assertEqual(preflight.parse_search_paths('"GameInfo" { }'), [])

    def test_entries_after_block_are_ignored(self):
        text = (
            '"GameInfo"\n{\n\tFileSystem\n\t{\n\t\tSearchPaths\n\t\t{\n'
            "\t\t\tGame\t|gameinfo_path|.\n\t\t}\n"
            "\t\tGame\t|gameinfo_path|wrong\n\t}\n}\n"
        )
        entries = preflight.parse_search_paths(text)
        self.assertEqual(len(entries), 1)

    def test_resolve_gameinfo_path_dot(self):
        entry = preflight.SearchPath("Game", "|gameinfo_path|.")
        self.assertEqual(entry.resolve(Path("/game/left4dead")), Path("/game/left4dead"))

    def test_resolve_subdirectory(self):
        entry = preflight.SearchPath("Game", "|gameinfo_path|mods/enabled")
        self.assertEqual(entry.resolve(Path("/g")), Path("/g/mods/enabled"))

    def test_resolve_wildcard_checks_parent(self):
        entry = preflight.SearchPath("Game", "|gameinfo_path|mods/enabled/*")
        self.assertEqual(entry.resolve(Path("/g")), Path("/g/mods"))

    def test_engine_paths_are_not_checked(self):
        entry = preflight.SearchPath("Platform", "|all_source_engine_paths|platform")
        self.assertIsNone(entry.resolve(Path("/g")))

    def test_gamebin_macro(self):
        entry = preflight.SearchPath("GameBin", "|gamebin|")
        self.assertEqual(entry.resolve(Path("/g")), Path("/g/bin"))


class HostAndEngineTests(unittest.TestCase):
    def test_host_facts_are_plausible(self):
        self.assertIn(preflight.host_bits(), (32, 64))
        self.assertTrue(preflight.host_machine())

    def test_resolve_engine_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Path(tmp) / "hl2_linux"
            engine.write_bytes(b"#!/bin/sh\n")
            self.assertEqual(preflight.resolve_engine(str(engine)), engine)

    def test_resolve_engine_missing_is_none(self):
        self.assertIsNone(preflight.resolve_engine("/nonexistent/hl2_linux"))

    def test_resolve_engine_from_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Path(tmp) / "fake_engine_binary"
            engine.write_bytes(b"#!/bin/sh\n")
            engine.chmod(0o755)
            old = os.environ.get("PATH", "")
            os.environ["PATH"] = tmp + os.pathsep + old
            try:
                self.assertEqual(preflight.resolve_engine("fake_engine_binary"), engine)
            finally:
                os.environ["PATH"] = old

    def test_interface_versions_reads_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.so"
            path.write_bytes(elf_with(b"VClient018\x00VClient019\x00"))
            found = preflight.interface_versions(path)
            self.assertEqual(found["VClient"], ["018", "019"])

    def test_interface_versions_without_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.so"
            path.write_bytes(elf_with(b"nothing here"))
            self.assertEqual(preflight.interface_versions(path), {})

    def test_interface_versions_missing_file(self):
        self.assertEqual(preflight.interface_versions(Path("/nope/m.so")), {})


class PreflightRunTests(unittest.TestCase):
    def test_clean_install_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            engine = fake_engine(Path(tmp))
            report = preflight.run(paths, engine=str(engine))
            self.assertTrue(report.ok, report.render())
            self.assertEqual(report.verdict, "ready")
            self.assertIn("module.client", codes(report))

    def test_missing_gamedir_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = preflight.run(PortPaths(Path(tmp) / "nope"))
            self.assertFalse(report.ok)
            self.assertIn("gamedir", codes(report))

    def test_missing_gameinfo_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            paths.gameinfo.unlink()
            report = preflight.run(paths)
            self.assertIn("gameinfo", codes(report))
            self.assertFalse(report.ok)

    def test_gameinfo_without_search_paths_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp), gameinfo_text='"GameInfo"\n{\n}\n')
            report = preflight.run(paths)
            self.assertIn("gameinfo.search", {c.code for c in report.blocking})

    def test_missing_module_blocks_with_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp), modules=False)
            report = preflight.run(paths)
            self.assertFalse(report.ok)
            self.assertIn("module.dir", codes(report))
            fixes = " ".join(c.fix for c in report.blocking)
            self.assertIn("module install", fixes)

    def test_engine_not_found_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            report = preflight.run(paths, engine="definitely-not-an-engine")
            self.assertIn("engine.binary", {c.code for c in report.blocking})

    def test_windows_dll_module_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            (paths.bindir / "client.so").write_bytes(b"MZ" + b"\x00" * 64)
            report = preflight.run(paths)
            self.assertIn("module.client", {c.code for c in report.blocking})
            self.assertTrue(any("Windows" in c.message for c in report.blocking))

    def test_bitness_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            other = 32 if preflight.host_bits() == 64 else 64
            (paths.bindir / "client.so").write_bytes(elf_with(b"VClient017", bits=other))
            report = preflight.run(paths)
            self.assertIn("module.client", {c.code for c in report.blocking})

    def test_interface_version_drift_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            engine = Path(tmp) / "engine_with_interface"
            engine.write_bytes(b"\x7fELF" + b"\x00" * 8 + b"VClient018\x00\x00" * 4)
            report = preflight.run(paths, engine=str(engine))
            self.assertIn("module.client.interface", {c.code for c in report.blocking})
            message = " ".join(c.message for c in report.blocking)
            self.assertIn("VClient018", message)
            self.assertIn("VClient017", message)

    def test_matching_interface_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            engine = Path(tmp) / "engine_with_interface"
            engine.write_bytes(b"\x7fELF" + b"\x00" * 8 + b"VClient017\x00\x00" * 4)
            report = preflight.run(paths, engine=str(engine))
            self.assertNotIn("module.client.interface", {c.code for c in report.blocking})

    def test_missing_exec_cfg_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            engine = Path(tmp) / "hl2_linux"
            engine.write_bytes(b"#!/bin/sh\n")
            report = preflight.run(paths, engine=str(engine),
                                   argv=[str(engine), "-game", "left4dead",
                                         "+exec", "missing.cfg"])
            self.assertIn("argv.exec", {c.code for c in report.blocking})

    def test_exec_cfg_present_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            (paths.cfg_dir / "l4d1_identity.cfg").write_text('name "x"\n')
            report = preflight.run(paths, argv=["engine", "+exec", "l4d1_identity.cfg"])
            self.assertIn("argv.exec", codes(report))
            self.assertTrue(any(c.code == "argv.exec" and c.level == preflight.OK
                                for c in report.checks))

    def test_steam_and_nosteam_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            report = preflight.run(paths, argv=["engine", "-steam", "-nosteam"])
            self.assertTrue(any("nosteam" in c.message for c in report.warnings))

    def test_broken_vpk_signature_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            (paths.gamedir / "pak01_dir.vpk").write_bytes(b"NOPE" + b"\x00" * 32)
            report = preflight.run(paths)
            self.assertIn("content.vpk", {c.code for c in report.blocking})

    def test_no_content_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            (paths.gamedir / "pak01_dir.vpk").unlink()
            report = preflight.run(paths)
            self.assertIn("content", {c.code for c in report.blocking})

    def test_readonly_gamedir_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            paths.gamedir.chmod(0o500)
            try:
                report = preflight.run(paths)
            finally:
                paths.gamedir.chmod(0o700)
            if os.geteuid() == 0:  # root ignores the permission bits
                self.skipTest("running as root")
            self.assertIn("gamedir.writable", {c.code for c in report.blocking})

    def test_report_json_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            report = preflight.run(paths, engine=str(fake_engine(Path(tmp))))
            payload = json.loads(json.dumps(report.to_dict()))
            self.assertEqual(payload["verdict"], report.verdict)
            self.assertEqual(len(payload["checks"]), len(report.checks))
            self.assertTrue(payload["ok"])

    def test_headline_mentions_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = preflight.run(PortPaths(Path(tmp) / "nope"))
            self.assertIn("would fail", report.headline())
            self.assertIn("preflight:", report.render())

    def test_game_name_mismatch_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            report = preflight.run(paths, argv=["engine", "-game", "otherdir"])
            self.assertIn("gamedir.name", codes(report))


class TriageTests(unittest.TestCase):
    def test_undefined_symbol_is_fatal(self):
        triage = preflight.triage_log("undefined symbol: _Z3foov\n")
        self.assertTrue(triage.crashed)
        self.assertIn("undefined symbol", {f.title for f in triage.findings})
        self.assertIn("waf", " ".join(f.fix for f in triage.findings))

    def test_client_load_failure_names_the_fix(self):
        triage = preflight.triage_log("Unable to load client DLL\n")
        self.assertTrue(triage.crashed)
        self.assertIn("module install", " ".join(f.fix for f in triage.findings))

    def test_unknown_entities_are_collected_and_deduped(self):
        log = ("Attempted to create unknown entity type weapon_ak47!\n"
               "Attempted to create unknown entity type weapon_ak47!\n"
               "Attempted to create unknown entity type info_survivor_position!\n")
        triage = preflight.triage_log(log)
        self.assertEqual(sorted(triage.unknown_entities),
                         ["info_survivor_position", "weapon_ak47"])
        self.assertFalse(triage.crashed)
        self.assertIn("unknown map entities", triage.render())

    def test_severity_ordering_is_fatal_first(self):
        log = ("Couldn't exec autoexec.cfg\n"
               "Attempted to create unknown entity type foo!\n"
               "Host_Error: Map load failed\n")
        triage = preflight.triage_log(log, exit_code=1)
        self.assertEqual(triage.findings[0].level, preflight.FATAL)
        self.assertEqual(triage.findings[-1].level, preflight.INFO)

    def test_clean_log(self):
        triage = preflight.triage_log("hello\nworld\n", exit_code=0)
        self.assertEqual(triage.verdict, "clean")
        self.assertFalse(triage.crashed)

    def test_unrecognised_nonzero_exit_is_reported(self):
        triage = preflight.triage_log("mystery output", exit_code=139)
        self.assertTrue(triage.crashed)
        self.assertIn("139", triage.findings[0].title)

    def test_crash_line_count(self):
        triage = preflight.triage_log("SIGSEGV\nSIGSEGV\nSIGSEGV\n")
        finding = triage.findings[0]
        self.assertEqual(finding.count, 3)
        self.assertIn("x3", finding.line())

    def test_json_round_trip(self):
        triage = preflight.triage_log("Host_Error: nope\n", exit_code=1,
                                      log_path=Path("/tmp/x.log"))
        payload = json.loads(json.dumps(triage.to_dict()))
        self.assertEqual(payload["verdict"], "failed")
        self.assertEqual(payload["findings"][0]["title"], "Host_Error")

    def test_find_latest_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            older = directory / "launch-a.log"
            newer = directory / "launch-b.log"
            older.write_text("a")
            newer.write_text("b")
            os.utime(older, (1000, 1000))
            os.utime(newer, (2000, 2000))
            self.assertEqual(preflight.find_latest_log(directory), newer)

    def test_find_latest_log_missing_dir(self):
        self.assertIsNone(preflight.find_latest_log(Path("/nope")))


class CheckCommandTests(unittest.TestCase):
    def test_check_json_reports_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_install(Path(tmp))
            code, out, _ = run(["--gamedir", str(Path(tmp) / "left4dead"), "check", "--json"])
            payload = json.loads(out)
            self.assertIn("verdict", payload)
            self.assertIn(code, (0, 1))

    def test_check_fails_on_broken_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = Path(tmp) / "left4dead"
            gamedir.mkdir(parents=True)
            code, out, _ = run(["--gamedir", str(gamedir), "check"])
            self.assertEqual(code, 1)
            self.assertIn("would fail", out)

    def test_check_passes_on_good_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            engine = Path(tmp) / "hl2_linux"
            engine.write_bytes(b"#!/bin/sh\n")
            engine.chmod(0o755)
            code, out, _ = run(["--gamedir", str(paths.gamedir), "check",
                                "--engine", str(engine)])
            self.assertEqual(code, 0, out)
            self.assertIn("verdict: ready", out)


    def test_gamedir_accepted_after_the_subcommand(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            engine = fake_engine(Path(tmp))
            code, out, _ = run(["check", "--gamedir", str(paths.gamedir),
                                "--engine", str(engine)])
            self.assertEqual(code, 0, out)

    def test_global_gamedir_still_wins_when_local_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            engine = fake_engine(Path(tmp))
            code, out, _ = run(["--gamedir", str(paths.gamedir), "check",
                                "--engine", str(engine)])
            self.assertEqual(code, 0, out)


class LaunchCaptureTests(unittest.TestCase):
    def test_run_captures_and_triages(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            identity = steamlib.Identity(steamid64=76561197960287930, persona_name="Tester")
            identity.write(paths.identity_json)

            engine = Path(tmp) / "hl2_linux"
            engine.write_text(
                "#!/bin/sh\n"
                "echo 'Attempted to create unknown entity type weapon_ak47!'\n"
                "exit 0\n",
                encoding="utf-8",
            )
            engine.chmod(0o755)

            code, out, _ = run(["--gamedir", str(paths.gamedir), "launch",
                                "--engine", str(engine), "--run", "--no-check"])
            self.assertEqual(code, 0, out)
            self.assertIn("triage:", out)
            self.assertIn("weapon_ak47", out)
            logs = list(paths.logs_dir.glob("launch-*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("weapon_ak47", logs[0].read_text())

    def test_run_reports_engine_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            engine = Path(tmp) / "hl2_linux"
            engine.write_text("#!/bin/sh\necho 'Host_Error: boom'\nexit 1\n",
                              encoding="utf-8")
            engine.chmod(0o755)
            code, out, _ = run(["--gamedir", str(paths.gamedir), "launch",
                                "--engine", str(engine), "--run", "--no-check"])
            self.assertEqual(code, 1)
            self.assertIn("Host_Error", out)

    def test_launch_refuses_when_preflight_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = Path(tmp) / "left4dead"
            gamedir.mkdir(parents=True)
            code, out, err = run(["--gamedir", str(gamedir), "launch",
                                  "--engine", "/nonexistent/engine"])
            self.assertEqual(code, 1)
            self.assertIn("refusing to launch", err)

    def test_launch_run_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_install(Path(tmp))
            engine = Path(tmp) / "hl2_linux"
            engine.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
            engine.chmod(0o755)
            code, out, err = run(["--gamedir", str(paths.gamedir), "launch",
                                  "--engine", str(engine), "--run", "--no-check",
                                  "--timeout", "0.2"])
            self.assertEqual(code, 1)
            self.assertIn("stopped it", err)


if __name__ == "__main__":
    unittest.main()
