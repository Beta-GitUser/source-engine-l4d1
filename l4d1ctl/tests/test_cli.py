import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from l4d1ctl import cli, kv, mods, vpk


def run(argv):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def prepare(tmp: Path) -> Path:
    gamedir = tmp / "left4dead"
    (gamedir / "maps").mkdir(parents=True)
    (gamedir / "gameinfo.txt").write_text('"GameInfo" { }', encoding="utf-8")
    (gamedir / "pak01_dir.vpk").write_bytes(b"x" * 8)
    (gamedir / "maps" / "c1m1_hotel.bsp").write_bytes(b"b" * 8)
    return gamedir


class CliTests(unittest.TestCase):
    def test_init_creates_gameinfo(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = Path(tmp) / "left4dead"
            code, out, _ = run(["--gamedir", str(gamedir), "init", "--with-mods"])
            self.assertEqual(code, 0)
            self.assertIn("wrote", out)
            self.assertTrue((gamedir / "gameinfo.txt").is_file())
            self.assertTrue((gamedir / "mods").is_dir())

    def test_init_refuses_foreign_gameinfo(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = Path(tmp) / "left4dead"
            gamedir.mkdir(parents=True)
            (gamedir / "gameinfo.txt").write_text("handwritten", encoding="utf-8")
            code, _, err = run(["--gamedir", str(gamedir), "init"])
            self.assertEqual(code, 1)
            self.assertIn("--force", err)

    def test_import_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Left 4 Dead" / "left4dead"
            source.mkdir(parents=True)
            (source / "gameinfo.txt").write_text("x", encoding="utf-8")
            target = root / "out" / "left4dead"
            code, out, _ = run(["--gamedir", str(target), "import",
                                "--source", str(root / "Left 4 Dead"), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("copied", out)
            self.assertIn(str(source), out)
            self.assertFalse((target / "gameinfo.txt").exists())

    def test_import_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run(["--gamedir", str(Path(tmp) / "g"), "import", "--source", "nope"])
            self.assertEqual(code, 2)
            self.assertIn("does not exist", err)

    def test_fetch_dry_run_prints_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "left4dead"
            code, out, _ = run(["--gamedir", str(target), "fetch",
                                "--provider", "steamcmd", "--user", "me"])
            self.assertEqual(code, 0)
            self.assertIn("+app_update 500", out.replace("+app_update 500", "+app_update 500"))
            self.assertIn("steamcmd", out)

    def test_fetch_requires_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run(["--gamedir", str(Path(tmp) / "g"), "fetch"])
            self.assertEqual(code, 2)
            self.assertIn("--user", err)

    def test_identity_with_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = Path(tmp) / "left4dead"
            code, out, _ = run(["--gamedir", str(gamedir), "identity",
                                "--steamid", "STEAM_0:0:1", "--name", "Test Player"])
            self.assertEqual(code, 0)
            self.assertIn("Test Player", out)
            state = json.loads((gamedir / "l4d1_port" / "identity.json").read_text(encoding="utf-8"))
            self.assertEqual(state["steamid64"], 76561197960265730)
            cfg = (gamedir / "cfg" / "l4d1_identity.cfg").read_text(encoding="utf-8")
            self.assertIn('name "Test Player"', cfg)

    def test_identity_without_input_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run(["--gamedir", str(Path(tmp) / "g"), "identity"])
            self.assertEqual(code, 2)
            self.assertIn("could not resolve", err)

    def test_identity_avatar_without_key_still_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = Path(tmp) / "left4dead"
            code, _, err = run(["--gamedir", str(gamedir), "identity",
                                "--steamid", "76561198000000001", "--name", "P", "--avatar"])
            self.assertEqual(code, 1)
            self.assertIn("API key", err)
            self.assertTrue((gamedir / "l4d1_port" / "identity.json").is_file())

    def test_mod_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = prepare(Path(tmp))
            source = Path(tmp) / "mymod"
            (source / "materials").mkdir(parents=True)
            (source / "materials" / "a.vmt").write_text("x", encoding="utf-8")

            code, out, _ = run(["--gamedir", str(gamedir), "mod", "add", str(source),
                                "--order", "10"])
            self.assertEqual(code, 0, out)
            self.assertEqual(code, 0, out)
            self.assertIn("added mymod", out)
            self.assertTrue((gamedir / "mods" / "enabled" / "010_mymod").is_dir())

            code, out, _ = run(["--gamedir", str(gamedir), "mod", "list"])
            self.assertIn("mymod", out)

            code, out, _ = run(["--gamedir", str(gamedir), "mod", "enable", "mymod"])
            self.assertEqual(code, 0, out)
            self.assertTrue((gamedir / "mods" / "mymod" / "mod.vdf").is_file())

            code, _, _ = run(["--gamedir", str(gamedir), "mod", "disable", "mymod"])
            self.assertEqual(code, 0)
            self.assertEqual(list((gamedir / "mods" / "enabled").iterdir()), [])

            code, out, _ = run(["--gamedir", str(gamedir), "mod", "validate"])
            self.assertEqual(code, 0, out)

    def test_mod_sync_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = prepare(Path(tmp))
            mod_dir = gamedir / "mods" / "m"
            (mod_dir / "materials").mkdir(parents=True)
            (mod_dir / "materials" / "a.vmt").write_text("x", encoding="utf-8")
            root = kv.KVNode("mod")
            root.set_value("enabled", 1)
            kv.write_file(mod_dir / "mod.vdf", [root])
            code, out, _ = run(["--gamedir", str(gamedir), "mod", "sync", "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("+ 100_m", out)
            self.assertFalse((gamedir / "mods" / "enabled").exists())

    def test_status_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = prepare(Path(tmp))
            code, out, _ = run(["--gamedir", str(gamedir), "status", "--json"])
            self.assertEqual(code, 0, out)
            payload = json.loads(out)
            self.assertTrue(payload["content_ok"])
            self.assertEqual(payload["gamedir"], str(gamedir.resolve()))
            self.assertFalse(payload["gameinfo_generated"])

    def test_status_reports_missing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = Path(tmp) / "empty"
            gamedir.mkdir()
            code, out, _ = run(["--gamedir", str(gamedir), "status", "--json"])
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(out)["content_ok"])

    def test_doctor_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = prepare(Path(tmp))
            code, out, _ = run(["--gamedir", str(gamedir), "--steam-root", str(Path(tmp) / "nosteam"),
                                "doctor"])
            self.assertIn("[PASS]", out)
            self.assertIn(code, (0, 1, 2))

    def test_doctor_with_steam_account(self):
        from l4d1ctl.tests.test_steamlib import make_steam_root

        with tempfile.TemporaryDirectory() as tmp:
            steam_root = make_steam_root(Path(tmp) / "steam")
            gamedir = prepare(Path(tmp) / "game")
            code, out, _ = run(["--gamedir", str(gamedir), "--steam-root", str(steam_root),
                                "doctor"])
            self.assertIn("local account: Zoey Fan", out)
            self.assertIn("[U:1:", out)
            self.assertIn(code, (0, 1, 2))

    def test_launch_print_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = prepare(Path(tmp))
            code, out, _ = run(["--gamedir", str(gamedir), "launch", "--print",
                                "--map", "c1m1_hotel"])
            self.assertEqual(code, 0)
            self.assertIn("-game left4dead", out)
            self.assertIn("+map c1m1_hotel", out)

    def test_steam_libs_without_steam(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run(["--steam-root", str(Path(tmp) / "none"), "steam-libs"])
            self.assertEqual(code, 2)
            self.assertIn("no Steam install", err)

    def test_vpk_info_list_check_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "mod_dir.vpk"
            vpk.build_vpk_v1(pack, {"materials/a.vmt": b"abc", "scripts/b.txt": b"xy"},
                             split=True)

            code, out, _ = run(["vpk", "info", str(pack)])
            self.assertEqual(code, 0, out)
            self.assertIn("version  : 1", out)
            self.assertIn("entries  : 2", out)

            code, out, _ = run(["vpk", "list", str(root)])
            self.assertEqual(code, 0, out)
            self.assertIn("materials/a.vmt", out)

            code, out, _ = run(["vpk", "list", str(pack), "--filter", "scripts"])
            self.assertEqual(code, 0, out)
            self.assertIn("scripts/b.txt", out)
            self.assertNotIn("materials/a.vmt", out)

            code, out, _ = run(["vpk", "check", str(pack), "--verify-crc", "materials/a.vmt"])
            self.assertEqual(code, 0, out)
            self.assertIn("[PASS]", out)

            code, _, _ = run(["vpk", "check", str(pack), "--verify-crc", "missing.vmt"])
            self.assertEqual(code, 2)

            code, out, _ = run(["vpk", "extract", str(pack), "--output", str(root / "out")])
            self.assertEqual(code, 0, out)
            self.assertEqual((root / "out" / "materials/a.vmt").read_bytes(), b"abc")

            (root / "mod_000.vpk").unlink()
            code, out, _ = run(["vpk", "check", str(pack)])
            self.assertEqual(code, 2)
            self.assertIn("missing part", out)

    def test_vpk_missing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run(["vpk", "info", str(Path(tmp) / "nope_dir.vpk")])
            self.assertEqual(code, 2)
            self.assertIn("no such file", err)

    def test_mod_add_rejects_broken_vpk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "bad_dir.vpk"
            bad.write_bytes(b"\x00" * 8)
            gamedir = prepare(root / "game")
            code, _, err = run(["--gamedir", str(gamedir), "mod", "add", str(bad),
                                "--id", "bad"])
            self.assertEqual(code, 1)
            self.assertIn("readable VPK", err)

    def test_unpack(self):
        with tempfile.TemporaryDirectory() as tmp:
            import zipfile

            archive = Path(tmp) / "c.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("left4dead/gameinfo.txt", '"GameInfo" { }')
            gamedir = Path(tmp) / "left4dead"
            code, out, _ = run(["--gamedir", str(gamedir), "unpack", str(archive)])
            self.assertEqual(code, 0, out)
            self.assertTrue((gamedir / "gameinfo.txt").is_file())


if __name__ == "__main__":
    unittest.main()
