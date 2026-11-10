import tempfile
import unittest
from pathlib import Path

from l4d1ctl import depot


def make_gamedir(root: Path, *, gameinfo=True, vpk=True, maps=True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if gameinfo:
        (root / "gameinfo.txt").write_text('"GameInfo" { }', encoding="utf-8")
    if vpk:
        (root / "pak01_dir.vpk").write_bytes(b"x" * 16)
        (root / "pak01_000.vpk").write_bytes(b"y" * 32)
    if maps:
        (root / "maps").mkdir(exist_ok=True)
        (root / "maps" / "c1m1_hotel.bsp").write_bytes(b"z" * 64)
    return root


class VerifyTests(unittest.TestCase):
    def test_missing_directory(self):
        report = depot.verify_game_dir(Path("/nonexistent/l4d1"))
        self.assertFalse(report.ok)
        self.assertTrue(any("does not exist" in c.message for c in report.failures))

    def test_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = depot.verify_game_dir(make_gamedir(Path(tmp) / "left4dead"))
            self.assertTrue(report.ok, [str(c) for c in report.checks])
            self.assertGreater(report.files, 0)
            self.assertGreater(report.bytes, 0)

    def test_missing_gameinfo_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = depot.verify_game_dir(make_gamedir(Path(tmp) / "left4dead", gameinfo=False))
            self.assertFalse(report.ok)
            self.assertTrue(any("gameinfo" in c.message for c in report.failures))

    def test_no_vpk_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = depot.verify_game_dir(make_gamedir(Path(tmp) / "left4dead", vpk=False))
            self.assertTrue(report.ok)
            self.assertTrue(any("VPK" in c.message for c in report.warnings))

    def test_pc_binaries_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = make_gamedir(Path(tmp) / "left4dead")
            (gamedir / "bin").mkdir()
            (gamedir / "bin" / "client.dll").write_bytes(b"mz")
            report = depot.verify_game_dir(gamedir)
            self.assertTrue(any("bin/" in c.message for c in report.warnings))


class CopyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source = make_gamedir(self.tmp / "Left 4 Dead" / "left4dead")
        (self.source / "bin").mkdir()
        (self.source / "bin" / "client.dll").write_bytes(b"mz" * 100)
        (self.source / "steam.inf").write_text("PatchVersion=1", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_copy_excludes_binaries(self):
        target = self.tmp / "out"
        stats = depot.copy_game_content(self.source, target)
        self.assertFalse((target / "bin").exists())
        self.assertTrue((target / "gameinfo.txt").is_file())
        self.assertTrue((target / "steam.inf").is_file())
        self.assertGreater(stats.copied, 0)
        self.assertGreater(stats.skipped, 0)  # the .dll was skipped

    def test_copy_is_incremental(self):
        target = self.tmp / "out"
        depot.copy_game_content(self.source, target)
        second = depot.copy_game_content(self.source, target)
        self.assertEqual(second.copied, 0)

    def test_copy_dry_run_writes_nothing(self):
        target = self.tmp / "out"
        stats = depot.copy_game_content(self.source, target, dry_run=True)
        self.assertGreater(stats.copied, 0)
        self.assertFalse((target / "gameinfo.txt").exists())

    def test_copy_overwrite(self):
        target = self.tmp / "out"
        depot.copy_game_content(self.source, target)
        (target / "gameinfo.txt").write_text("changed", encoding="utf-8")
        depot.copy_game_content(self.source, target, overwrite=True)
        self.assertIn("GameInfo", (target / "gameinfo.txt").read_text(encoding="utf-8"))

    def test_hardlink_when_possible(self):
        target = self.tmp / "out"
        stats = depot.copy_game_content(self.source, target, link=True)
        self.assertGreaterEqual(stats.linked + stats.copied, 1)

    def test_missing_source(self):
        with self.assertRaises(FileNotFoundError):
            depot.copy_game_content(self.tmp / "nope", self.tmp / "out")


class CommandTests(unittest.TestCase):
    def test_steamcmd_command_shape(self):
        command = depot.steamcmd_command("steamcmd", Path("/data/l4d1"), "me")
        self.assertEqual(
            command.argv,
            ["steamcmd", "+force_install_dir", "/data/l4d1", "+login", "me",
             "+app_update", "500", "validate", "+quit"],
        )
        self.assertIn("+app_update", command.shell())

    def test_steamcmd_without_validate(self):
        command = depot.steamcmd_command("steamcmd", Path("/x"), "me", validate=False)
        self.assertNotIn("validate", command.argv)
        self.assertEqual(command.argv[-1], "+quit")

    def test_depotdownloader_command_shape(self):
        command = depot.depotdownloader_command("DepotDownloader", Path("/x"), "me",
                                                extra=["-depot", "501"])
        self.assertEqual(command.argv[:6], ["DepotDownloader", "-app", "500", "-dir", "/x", "-username"])
        self.assertIn("-depot", command.argv)

    def test_run_provider_dry(self):
        command = depot.ProviderCommand("test", ["/bin/true"])
        self.assertEqual(depot.run_provider(command, execute=False), 0)

    def test_run_provider_executes(self):
        command = depot.ProviderCommand("test", ["/bin/true"])
        self.assertEqual(depot.run_provider(command, execute=True), 0)


class ArchiveTests(unittest.TestCase):
    def test_zip_roundtrip_strips_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "content.zip"
            import zipfile

            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("left4dead/gameinfo.txt", '"GameInfo" {}')
                zf.writestr("left4dead/maps/c1m1_hotel.bsp", "bsp")
                zf.writestr("left4dead/bin/client.dll", "mz")
            stats = depot.extract_archive(archive, root / "out")
            self.assertTrue((root / "out" / "gameinfo.txt").is_file())
            self.assertTrue((root / "out" / "maps" / "c1m1_hotel.bsp").is_file())
            self.assertFalse((root / "out" / "bin").exists())
            self.assertGreater(stats.skipped, 0)

    def test_tar_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_gamedir(root / "left4dead")
            archive = depot.make_export_archive(source, root / "export.tar.gz")
            stats = depot.extract_archive(archive, root / "out")
            self.assertTrue((root / "out" / "gameinfo.txt").is_file())
            self.assertGreater(stats.copied, 0)

    def test_zip_slip_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "evil.zip"
            import zipfile

            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../../etc/passwd", "x")
            with self.assertRaises(ValueError):
                depot.extract_archive(archive, root / "out")

    def test_unsupported_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.rar"
            path.write_bytes(b"nope")
            with self.assertRaises(ValueError):
                depot.extract_archive(path, Path(tmp) / "out")


if __name__ == "__main__":
    unittest.main()
